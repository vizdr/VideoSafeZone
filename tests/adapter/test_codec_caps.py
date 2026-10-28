"""codec_caps: which codecs a camera can deliver, the default, and why one can't be chosen
(PYTEST.md, step 5). MediaMTX, detect-hw.sh and the camera are faked."""
import pytest

import codec_caps as cc
import mediamtx_api
import onvif_media2

CAMERA = {"h264": "camera", "h265": "camera"}


@pytest.mark.parametrize("caps, current, expected", [
    (CAMERA, "h265", "h265"),                                 # what the camera sends now
    (CAMERA, None, "h264"),                                   # tie -> H.264, every browser plays it
    ({"h264": "none", "h265": "camera"}, None, "h265"),
    ({"h264": "pi-hw", "h265": "none"}, "h265", "h264"),      # current not hardware: ignored
    ({"h264": "none", "h265": "none"}, "h264", None),
    ({}, None, None),
])
def test_default_codec_is_hardware_first(caps, current, expected):
    assert cc.default_codec(caps, current) == expected


@pytest.mark.parametrize("item, codec, phrase", [
    ({"videoCodecCaps": CAMERA}, "h265", None),
    ({}, "h265", "not known yet"),
    ({"mode": "transcode", "videoCodecCaps": {"h264": "pi-hw", "h265": "none"}}, "h265",
     "software encoding"),
    ({"videoCodecCaps": {"h264": "camera", "h265": "none"}, "videoCodecProbe": "fallback: timeout",
      "onvifHost": "192.0.2.10"}, "h265", "could not be asked"),
    ({"videoCodecCaps": {"h264": "camera", "h265": "none"}}, "h265", "no ONVIF access"),
    ({"videoCodecCaps": {"h264": "camera", "h265": "none"}, "onvifHost": "192.0.2.10"}, "h265",
     "does not offer H.265"),
])
def test_unavailable_reason(item, codec, phrase):
    reason = cc.unavailable_reason(item, codec)
    assert reason is None if phrase is None else phrase in reason


@pytest.fixture
def on_wire(monkeypatch):
    """Sets the codec MediaMTX reports arriving on any path (None: nothing arriving)."""
    def answer(codec):
        monkeypatch.setattr(mediamtx_api, "path_video_codec", lambda path: codec)
    return answer


def test_fallback_records_only_what_arrives(on_wire):
    on_wire("h265")
    out = cc.fallback({"cameraId": "cam-03"}, TimeoutError("camera asleep"))
    assert out == {"videoCodecCaps": {"h264": "none", "h265": "camera"}, "videoCodecDefault": "h265",
                   "videoEncoderToken": None, "videoCodecProbe": "fallback: camera asleep"}


def test_fallback_with_nothing_arriving_assumes_h264(on_wire):
    on_wire(None)
    assert cc.fallback({"cameraId": "cam-03"}, OSError("x"))["videoCodecDefault"] == "h264"


def test_probe_transcode_camera_asks_the_pi_not_the_registry(on_wire, monkeypatch):
    on_wire("h264")
    monkeypatch.setattr(cc, "pi_hw_encoders", lambda: {"h264"})
    assert cc.probe({"cameraId": "cam-01", "mode": "transcode"}) == {
        "videoCodecCaps": {"h264": "pi-hw", "h265": "none"}, "videoCodecDefault": "h264",
        "videoEncoderToken": None, "videoCodecProbe": "local"}


def test_probe_passthrough_without_onvif_knows_only_the_wire(on_wire):
    on_wire("h265")
    out = cc.probe({"cameraId": "cam-05", "mode": "passthrough"})
    assert out["videoCodecCaps"] == {"h264": "none", "h265": "camera"}
    assert out["videoCodecProbe"] == "stream only (no ONVIF)"


class _ClaimsH264Camera:
    """cam-03: offers both codecs, says H264, sends H.265."""

    def profiles(self):
        return [{"token": "P0", "name": "main", "encoderToken": "E0",
                 "streamUri": "rtsp://192.0.2.13:554/main"}]

    def encoder_options(self, token):
        return {"H264": {}, "H265": {}}

    def encoder_config(self, token):
        from lxml import etree
        ns = onvif_media2.NS
        return etree.fromstring(
            f'<tr2:Configurations xmlns:tr2="{ns["tr2"]}" xmlns:tt="{ns["tt"]}">'
            "<tt:Encoding>H264</tt:Encoding></tr2:Configurations>")


@pytest.mark.parametrize("wire, expected", [("h265", "h265"), (None, "h264")])
def test_ff69_default_follows_the_wire_not_the_cameras_claim(on_wire, monkeypatch, wire, expected):
    on_wire(wire)
    monkeypatch.setattr(onvif_media2, "for_camera", lambda item: _ClaimsH264Camera())
    out = cc.probe({"cameraId": "cam-03", "mode": "passthrough", "onvifHost": "192.0.2.13",
                    "rtspUrl": "rtsp://admin:pw@192.0.2.13/main"})
    assert out["videoCodecDefault"] == expected
    assert out["videoCodecCaps"] == CAMERA
    assert out["videoEncoderToken"] == "E0"


# --- Phase 2: refreshing the registry rows --------------------------------------------------------

from fakes import FakeTable   # noqa: E402

KNOWN = {"videoCodecCaps": CAMERA, "videoCodecDefault": "h264", "videoEncoderToken": "E0",
         "videoCodecProbe": "media2"}


def test_refresh_writes_only_what_changed(monkeypatch):
    table = FakeTable([{"cameraId": "cam-02", **KNOWN}, {"cameraId": "cam-03"}])
    monkeypatch.setattr(cc, "probe", lambda item: dict(KNOWN))
    done = cc.refresh(table, log=lambda msg: None)
    assert done == {"cam-02": "ok", "cam-03": "ok, written"}
    assert [w[1]["Key"] for w in table.writes] == [{"cameraId": "cam-03"}]
    assert table.row("cam-03")["videoCodecCaps"] == CAMERA


def test_an_unreachable_camera_keeps_the_caps_it_has(monkeypatch):
    # cam-02 drops off the network for minutes on its own; overwriting a good answer with a
    # fallback on every blip would make the GUI flap.
    table = FakeTable([{"cameraId": "cam-02", **KNOWN}])

    def unreachable(item):
        raise onvif_media2.OnvifError("192.0.2.12 unreachable (ConnectTimeout)")
    monkeypatch.setattr(cc, "probe", unreachable)
    fields, status = cc.refresh_item(table, table.row("cam-02"))
    assert fields == KNOWN and status.startswith("kept existing")
    assert table.writes == []


def test_an_unreachable_new_camera_gets_the_fallback(monkeypatch, on_wire):
    on_wire(None)
    table = FakeTable([{"cameraId": "cam-04"}])

    def unreachable(item):
        raise OSError("no route to host")
    monkeypatch.setattr(cc, "probe", unreachable)
    fields, status = cc.refresh_item(table, table.row("cam-04"))
    assert status.startswith("fallback") and fields["videoCodecCaps"] == {"h264": "camera",
                                                                          "h265": "none"}
    assert table.row("cam-04")["videoCodecProbe"].startswith("fallback")


def test_one_bad_row_does_not_stop_the_others(monkeypatch):
    table = FakeTable([{"cameraId": "cam-02"}, {"cameraId": "cam-03"}])

    def probe(item):
        if item["cameraId"] == "cam-02":
            raise KeyError("rtspUrl")
        return dict(KNOWN)
    monkeypatch.setattr(cc, "probe", probe)
    done = cc.refresh(table, log=lambda msg: None)
    assert done["cam-02"].startswith("error") and done["cam-03"] == "ok, written"


def test_refresh_only_the_cameras_asked_for(monkeypatch):
    table = FakeTable([{"cameraId": "cam-02"}, {"cameraId": "cam-03"}])
    monkeypatch.setattr(cc, "probe", lambda item: dict(KNOWN))
    assert list(cc.refresh(table, {"cam-03"}, log=lambda msg: None)) == ["cam-03"]


def test_dry_run_writes_nothing(monkeypatch):
    table = FakeTable([{"cameraId": "cam-03"}])
    monkeypatch.setattr(cc, "probe", lambda item: dict(KNOWN))
    cc.refresh(table, write=False, log=lambda msg: None)
    assert table.writes == []
