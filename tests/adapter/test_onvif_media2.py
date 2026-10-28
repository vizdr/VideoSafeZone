"""onvif_media2: the pure parts of switching a camera's codec, stream and frame rate (PYTEST.md,
step 5).

plan_config and fps_config were written as pure functions "so the choices can be checked without a
camera" -- these tests are that check. The encoder configuration is an lxml element shaped like a
Media2 GetVideoEncoderConfigurations reply; the options dict is encoder_options()'s shape.
"""
from types import SimpleNamespace

import pytest
from lxml import etree

import onvif_media2 as m2

NS = m2.NS


def _cfg(encoding="H264", width=1920, height=1080, fps="15", bitrate="4096", gov="30",
         profile="Main"):
    attrs = f'token="VideoEncoderToken_1" GovLength="{gov}"' if gov else 'token="VideoEncoderToken_1"'
    if profile:
        attrs += f' Profile="{profile}"'
    rate = f"<tt:FrameRateLimit>{fps}</tt:FrameRateLimit>" if fps is not None else ""
    return etree.fromstring(
        f'<tr2:Configurations xmlns:tr2="{NS["tr2"]}" xmlns:tt="{NS["tt"]}" {attrs}>'
        "<tt:Name>VideoEncoder_1</tt:Name><tt:UseCount>1</tt:UseCount>"
        f"<tt:Encoding>{encoding}</tt:Encoding>"
        f"<tt:Resolution><tt:Width>{width}</tt:Width><tt:Height>{height}</tt:Height></tt:Resolution>"
        f'<tt:RateControl ConstantBitRate="false">{rate}'
        f"<tt:BitrateLimit>{bitrate}</tt:BitrateLimit></tt:RateControl>"
        "<tt:Quality>3</tt:Quality></tr2:Configurations>")


def _res(w, h, fr=(1, 25), br=(256, 8192)):
    return {"width": w, "height": h, "frameRate": list(fr) if fr else None,
            "bitrate": list(br) if br else None}


OPTS = {
    "H264": {"profiles": ["Baseline", "Main", "High"], "govLength": [1, 400],
             "frameRates": [1.0, 5.0, 10.0, 12.0, 15.0, 20.0, 25.0],
             "resolutions": [_res(1920, 1080), _res(1280, 720), _res(640, 360)],
             "bitrate": [32.0, 16384.0]},
    "H265": {"profiles": ["Main", "Main10"], "govLength": [1, 400],
             "frameRates": [1.0, 5.0, 10.0, 12.0, 15.0],
             "resolutions": [_res(1280, 720, fr=(1, 15), br=(256, 2048)), _res(640, 360, fr=(1, 15))],
             "bitrate": [32.0, 8192.0]},
}


def _read(cfg):
    s = m2.summarize(cfg)
    return s["encoding"], s["profile"], s["width"], s["height"], s["frameRate"], s["bitrateKbps"], \
        s["govLength"]


def test_summarize():
    assert m2.summarize(_cfg()) == {"encoding": "H264", "profile": "Main", "width": 1920,
                                    "height": 1080, "frameRate": 15.0, "bitrateKbps": 4096.0,
                                    "govLength": 30}


def test_plan_h265_moves_every_field_into_the_target_range():
    new = m2.plan_config(_cfg(), OPTS, "H265")
    # 1080p isn't offered for H.265: the largest size not above it; bitrate into 720p's range.
    assert _read(new) == ("H265", "Main", 1280, 720, 15.0, 2048.0, 30)


def test_plan_does_not_touch_its_input():
    cfg = _cfg()
    before = etree.tostring(cfg)
    m2.plan_config(cfg, OPTS, "H265")
    assert etree.tostring(cfg) == before


def test_plan_h264_prefers_the_high_profile():
    # Baseline played in ffmpeg and stayed black in browsers (FoundAndFixed.md #13, guide §16.1).
    new = m2.plan_config(_cfg(encoding="H265", width=1280, height=720, profile="Main10"), OPTS, "H264")
    assert new.get("Profile") == "High"


def test_plan_keeps_a_profile_the_target_allows():
    new = m2.plan_config(_cfg(profile="Main"), OPTS, "H265")
    assert new.get("Profile") == "Main"


def test_plan_drops_the_profile_when_the_target_lists_none():
    opts = {"H265": {**OPTS["H265"], "profiles": []}}
    assert "Profile" not in m2.plan_config(_cfg(), opts, "H265").attrib


def test_plan_prefers_the_resolution_mediamtx_receives():
    # The camera's own configuration can be stale (cam-03): what arrives wins when allowed.
    new = m2.plan_config(_cfg(width=1920, height=1080), OPTS, "H264", prefer_resolution=(1280, 720))
    assert _read(new)[2:4] == (1280, 720)


def test_plan_ignores_a_preferred_resolution_the_target_lacks():
    new = m2.plan_config(_cfg(width=1280, height=720), OPTS, "H265", prefer_resolution=(1920, 1080))
    assert _read(new)[2:4] == (1280, 720)


def test_plan_takes_the_smallest_size_when_nothing_fits_below():
    opts = {"H265": {**OPTS["H265"], "resolutions": [_res(2560, 1440), _res(3840, 2160)]}}
    new = m2.plan_config(_cfg(width=1280, height=720), opts, "H265")
    assert _read(new)[2:4] == (2560, 1440)


def test_plan_frame_rate_steps_down_to_an_offered_one():
    new = m2.plan_config(_cfg(width=1280, height=720, fps="20"), OPTS, "H265")
    assert _read(new)[4] == 15.0


def test_plan_clamps_gov_length():
    opts = {"H264": {**OPTS["H264"], "govLength": [10, 60]}}
    assert m2.plan_config(_cfg(gov="200"), opts, "H264").get("GovLength") == "60"
    assert m2.plan_config(_cfg(gov="2"), opts, "H264").get("GovLength") == "10"


@pytest.mark.parametrize("opts, resolution, expected", [
    ({"frameRates": [1.0, 10.0, 15.0, 25.0]}, None, [10, 15]),
    ({"frameRates": [5.0, 10.0, 20.0]}, None, [10]),
    ({}, None, [10, 15]),                                         # no list: both offered
    ({"frameRates": [10.0, 15.0], "resolutions": [_res(1920, 1080, fr=(1, 12))]}, (1920, 1080), [10]),
    ({"frameRates": [10.0, 15.0], "resolutions": [_res(1920, 1080, fr=(1, 12))]}, (1280, 720), [10, 15]),
])
def test_fps_choices(opts, resolution, expected):
    assert m2.fps_choices(opts, resolution) == expected


def test_fps_config_keeps_the_keyframe_interval_in_seconds():
    # 100 frames at 20 fps is 5 s; at 10 fps it must become 50 frames, not stay 100 (10 s).
    new = m2.fps_config(_cfg(fps="20", gov="100"), {}, 10)
    assert new.findtext("tt:RateControl/tt:FrameRateLimit", namespaces=NS) == "10"
    assert new.get("GovLength") == "50"


def test_fps_config_clamps_gov_length():
    new = m2.fps_config(_cfg(fps="25", gov="25"), {"govLength": [5, 400]}, 1)
    assert new.get("GovLength") == "5"


def test_fps_config_without_a_frame_rate_limit():
    with pytest.raises(m2.OnvifError):
        m2.fps_config(_cfg(fps=None), {}, 10)


# --- stream URIs ----------------------------------------------------------------------------------

@pytest.mark.parametrize("uri, expected", [
    ("rtsp://admin:secret@192.0.2.10:554/Streaming/101", "rtsp://192.0.2.10:554/Streaming/101"),
    ("rtsp://192.0.2.11/live?channel=1&username=admin&password=5f4dcc3b",
     "rtsp://192.0.2.11/live?channel=1&username=admin&password=<redacted>"),
    ("rtsp://192.0.2.11/live?pwd=abc&ch=1", "rtsp://192.0.2.11/live?pwd=<redacted>&ch=1"),
    (None, ""),
])
def test_redact_uri(uri, expected):
    assert m2.redact_uri(uri) == expected


@pytest.mark.parametrize("a, b, same", [
    ("rtsp://admin:pw@192.0.2.10:554/Streaming/101", "rtsp://192.0.2.10/Streaming/101/", True),
    ("rtsp://h/live?channel=1&username=a&password=b", "rtsp://h2/live?channel=1", True),
    ("rtsp://h/live?a=1&b=2", "rtsp://h/live?b=2&a=1", True),
    ("rtsp://h/Streaming/101", "rtsp://h/Streaming/102", False),
    ("rtsp://h/live?channel=1", "rtsp://h/live?channel=2", False),
])
def test_same_stream(a, b, same):
    assert m2.same_stream(a, b) is same


@pytest.mark.parametrize("uri, registered, expected", [
    ("rtsp://192.0.2.10/Streaming/102", "rtsp://admin:pw@192.0.2.10/Streaming/101",
     "rtsp://admin:pw@192.0.2.10/Streaming/102"),
    ("rtsp://192.0.2.11:554/live?channel=2", "rtsp://192.0.2.11:554/live?channel=1&username=admin&password=abc",
     "rtsp://192.0.2.11:554/live?channel=2&username=admin&password=abc"),
    ("rtsp://other:x@192.0.2.10/s2", "rtsp://admin:pw@192.0.2.10/s1", "rtsp://other:x@192.0.2.10/s2"),
    ("rtsp://192.0.2.10/s2", "rtsp://192.0.2.10/s1", "rtsp://192.0.2.10/s2"),
])
def test_with_credentials_carries_the_registered_login(uri, registered, expected):
    assert m2.with_credentials(uri, registered) == expected


# --- choosing a profile, listing streams (fake client) ---------------------------------------------

class FakeCamera:
    def __init__(self, profiles, configs=None, options=None):
        self._profiles, self._configs, self._options = profiles, configs or {}, options or {}

    def profiles(self):
        return self._profiles

    def encoder_config(self, token):
        return self._configs[token]

    def encoder_options(self, token):
        return self._options.get(token, OPTS)


def _profile(token, encoder, path):
    return {"token": token, "name": token, "encoderToken": encoder,
            "streamUri": f"rtsp://192.0.2.10:554{path}"}


def test_stream_profile_finds_the_registered_stream():
    cam = FakeCamera([_profile("P0", "E0", "/main"), _profile("P1", "E1", "/sub")])
    assert m2.stream_profile(cam, "rtsp://admin:pw@192.0.2.10/sub")["encoderToken"] == "E1"


def test_stream_profile_accepts_profiles_sharing_one_encoder():
    cam = FakeCamera([_profile("PTZ", "E0", "/main"), _profile("P0", "E0", "/main")])
    assert m2.stream_profile(cam, "rtsp://192.0.2.10/main")["encoderToken"] == "E0"


def test_stream_profile_never_guesses_between_encoders():
    cam = FakeCamera([_profile("A", "E0", "/main"), _profile("B", "E1", "/main")])
    with pytest.raises(m2.OnvifError, match="not guessing"):
        m2.stream_profile(cam, "rtsp://192.0.2.10/main")


def test_stream_profile_none_matches():
    cam = FakeCamera([_profile("P0", "E0", "/main")])
    with pytest.raises(m2.OnvifError, match="re-register"):
        m2.stream_profile(cam, "rtsp://192.0.2.10/other")


def test_streams_biggest_picture_is_main_whatever_the_vendor_order():
    cam = FakeCamera(
        [_profile("PROFILE_000", "E0", "/sub"), _profile("PROFILE_001", "E1", "/main"),
         _profile("PROFILE_002", "E2", "/third")],
        configs={"E0": _cfg(width=640, height=360), "E1": _cfg(width=1920, height=1080),
                 "E2": _cfg(width=320, height=180)})
    out = m2.streams(cam)
    assert [(s["name"], s["token"], s["width"]) for s in out] == [
        ("main", "PROFILE_001", 1920), ("sub", "PROFILE_000", 640), ("sub2", "PROFILE_002", 320)]
    assert out[0]["fpsChoices"] == [10, 15]


def test_for_camera_builds_a_client_from_the_registry_row():
    client = m2.for_camera({"onvifHost": "192.0.2.10", "onvifUser": "admin",
                            "onvifPassword": "pw"})
    assert (client.host, client.port) == ("192.0.2.10", 80)
    assert client.device_url == "http://192.0.2.10:80/onvif/device_service"
