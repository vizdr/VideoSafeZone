"""onvif_discovery: addresses, audio figures, RTSP logins and SDP (PYTEST.md, step 5).

The one RTSP request (_describe) is faked; everything around it is the real code.
"""
import base64
import hashlib
from types import SimpleNamespace as NS

import pytest

import onvif_discovery as d


@pytest.mark.parametrize("xaddr, expected", [
    ("http://192.168.178.67:8080/onvif/device_service", ("192.168.178.67", 8080)),
    ("http://192.168.178.67/onvif/device_service", ("192.168.178.67", 80)),
    ("http://cam.local:2020/onvif/device_service", ("cam.local", 2020)),
])
def test_xaddr_host_port(xaddr, expected):
    assert d.xaddr_host_port(xaddr) == expected


@pytest.mark.parametrize("value, expected", [
    (8000, 8.0), (64000, 64.0),     # cam-02: Hz and bit/s
    (8, 8), (64, 64),               # cam-03: the spec's kHz and kbit/s
    (None, None),
])
def test_ff74_per_kilo(value, expected):
    assert d._per_kilo(value) == expected


def test_ff74_audio_from_a_profile_in_base_units():
    profile = NS(AudioEncoderConfiguration=NS(Encoding="G711", SampleRate=8000, Bitrate=64000))
    assert d._audio(profile) == {"encoding": "G711", "sampleRateKHz": 8.0, "bitrateKbps": 64.0,
                                 "supported": True}


def test_audio_unsupported_encoding_is_shown_but_flagged():
    profile = NS(AudioEncoderConfiguration=NS(Encoding="G726", SampleRate=8, Bitrate=32))
    assert d._audio(profile)["supported"] is False


@pytest.mark.parametrize("profile", [NS(), NS(AudioEncoderConfiguration=None),
                                     NS(AudioEncoderConfiguration=NS(Encoding=None))])
def test_no_audio(profile):
    assert d._audio(profile) is None


# --- RTSP authentication ------------------------------------------------------------------------

def test_basic_auth():
    header = d._rtsp_auth('Basic realm="cam"', "admin", "secret", "rtsp://cam/stream")
    assert header == "Basic " + base64.b64encode(b"admin:secret").decode()


def test_digest_auth_follows_rfc2617_without_qop():
    uri = "rtsp://192.0.2.10:554/Streaming/Channels/101"
    header = d._rtsp_auth('Digest realm="IP Camera", nonce="abc123", stale="FALSE"',
                          "admin", "secret", uri)
    md5 = lambda s: hashlib.md5(s.encode()).hexdigest()   # noqa: E731
    expected = md5(f"{md5('admin:IP Camera:secret')}:abc123:{md5('DESCRIBE:' + uri)}")
    assert header == (f'Digest username="admin", realm="IP Camera", nonce="abc123", '
                      f'uri="{uri}", response="{expected}"')


@pytest.mark.parametrize("uri, has", [
    ("rtsp://admin:pw@192.0.2.10/stream", True),
    ("rtsp://admin@192.0.2.10/stream", True),
    ("rtsp://192.0.2.10/stream?username=admin&password=abc", True),
    ("rtsp://192.0.2.10/stream?pwd=abc", True),
    ("rtsp://192.0.2.10/stream?channel=1&subtype=0", False),
    ("rtsp://192.0.2.10/stream", False),
])
def test_has_login(uri, has):
    assert d._has_login(uri) is has


@pytest.fixture
def describe(monkeypatch):
    """Fakes the one RTSP DESCRIBE: answer(user) -> SDP text, or raises. Records each user asked."""
    calls = []

    def install(answer):
        def fake(uri, user, password, timeout=3):
            calls.append((uri, user, password))
            return answer(user)
        monkeypatch.setattr(d, "_describe", fake)
        return calls
    return install


def _unauthorized(user):
    raise d.RtspUnauthorized("401")


def test_rtsp_login_keeps_a_uri_that_already_has_one(describe):
    calls = describe(_unauthorized)
    uri = "rtsp://admin:pw@192.0.2.10/stream"
    assert d.rtsp_login(uri, "admin", "pw") == uri
    assert calls == []                       # not even asked


def test_rtsp_login_keeps_a_stream_that_plays_without_login(describe):
    describe(lambda user: "v=0\r\n")
    assert d.rtsp_login("rtsp://192.0.2.10/stream", "admin", "pw") == "rtsp://192.0.2.10/stream"


def test_rtsp_login_adds_the_onvif_login_after_a_401(describe):
    calls = describe(lambda user: _unauthorized(user) if user is None else "v=0\r\n")
    assert d.rtsp_login("rtsp://192.0.2.10:554/stream?ch=1", "admin", "p@ss word", ) == \
        "rtsp://admin:p%40ss%20word@192.0.2.10:554/stream?ch=1"
    assert [c[1] for c in calls] == [None, "admin"]


def test_ff67_empty_password_uses_the_placeholder(describe):
    # MediaMTX refuses a URL with a user and an empty password.
    calls = describe(lambda user: _unauthorized(user) if user is None else "v=0\r\n")
    assert d.rtsp_login("rtsp://192.0.2.10/stream", "admin", "") == \
        f"rtsp://admin:{d.RTSP_PLACEHOLDER}@192.0.2.10/stream"
    assert calls[-1][2] == d.RTSP_PLACEHOLDER


def test_ff67_refused_empty_password_says_what_to_do(describe):
    describe(_unauthorized)
    with pytest.raises(d.RtspUnauthorized, match="set a password on the camera"):
        d.rtsp_login("rtsp://192.0.2.10/stream", "admin", "")


def test_rtsp_login_leaves_an_unreachable_camera_alone(describe):
    def unreachable(user):
        raise OSError("no route to host")
    describe(unreachable)
    assert d.rtsp_login("rtsp://192.0.2.10/stream", "admin", "pw") == "rtsp://192.0.2.10/stream"


# --- SDP ------------------------------------------------------------------------------------------

SDP_VIDEO_AND_PCMA = """v=0\r
o=- 0 0 IN IP4 192.0.2.10\r
s=Media Presentation\r
m=video 0 RTP/AVP 96\r
a=rtpmap:96 H264/90000\r
m=audio 0 RTP/AVP 8\r
a=rtpmap:8 PCMA/8000\r
"""


@pytest.mark.parametrize("sdp, expected", [
    (SDP_VIDEO_AND_PCMA, ("G711", 8.0, True)),
    # static payload type 0 with no rtpmap line (RFC 3551)
    ("v=0\r\nm=audio 0 RTP/AVP 0\r\n", ("G711", 8.0, True)),
    ("v=0\r\nm=audio 0 RTP/AVP 97\r\na=rtpmap:97 MPEG4-GENERIC/16000/1\r\n", ("AAC", 16.0, False)),
    ("v=0\r\nm=audio 0 RTP/AVP 98\r\na=rtpmap:98 G726-32/8000\r\n", ("G726", 8.0, False)),
])
def test_ff67_stream_audio_from_sdp(describe, sdp, expected):
    # cam-04's microphone is only in its SDP, not its ONVIF profile.
    describe(lambda user: sdp)
    audio = d.stream_audio("rtsp://192.0.2.10/stream", "admin", "pw")
    assert (audio["encoding"], audio["sampleRateKHz"], audio["supported"]) == expected
    assert audio["fromStream"] is True


def test_stream_audio_none_without_an_audio_track(describe):
    describe(lambda user: "v=0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H265/90000\r\n")
    assert d.stream_audio("rtsp://192.0.2.10/stream", "admin", "pw") is None
