"""The admin GUI's codec, stream and frame-rate routes (PYTEST.md, Phase 3).

Each is "done" only when the wire shows it: MediaMTX receives the new codec, the other stream, or
frames at the new rate -- never the camera's read-back alone (guide §21). The fake camera's
on_set and FakeMediaMTX's on_write play the camera and MediaMTX reacting, as they do on the LAN.
"""
import subprocess

import pytest

import sync_mediamtx_paths
from fakes import FakeMediaMTX, ModuleShim, encoder_options, path_state
from gui_world import CAM03, admin, arn, media2, systemctl_writes

MAIN = CAM03["rtspUrl"]
SUB = CAM03["liveRtspUrl"]


def _cam03_mtx(**kw):
    return FakeMediaMTX(conf={"cam03": {"source": MAIN, "rtspTransport": "tcp"},
                              "cam03_live": {"source": SUB, "rtspTransport": "tcp"}},
                        state={"cam03": path_state("H265", 2560, 1440, ready_time="t0"),
                               "cam03_live": path_state("H264", 800, 448)},
                        flowing={"cam03", "cam03_live"}, **kw)


# --- GET codec ----------------------------------------------------------------------------------

def test_codec_of_the_usb_camera_is_shown_not_offered(gui):
    g = gui(mtx=FakeMediaMTX(state={"cam01": path_state("H264")}))
    status, body = g.get("/api/cameras/cam-01/codec")
    assert status == 200 and body["switchable"] is False and body["configured"] == "h264"
    assert body["options"]["h264"] == {"available": True, "encodedBy": "pi-hw"}
    assert "software encoding" in body["options"]["h265"]["reason"]


def test_codec_of_an_onvif_camera(gui):
    g = gui(mtx=_cam03_mtx(), camera=media2("192.0.2.13"))
    status, body = g.get("/api/cameras/cam-03/codec")
    assert status == 200
    assert (body["chosen"], body["configured"], body["switchable"], body["encoderToken"]) == \
        ("h265", "h265", True, "E0")
    assert body["wire"] == {"codec": "h265", "width": 2560, "height": 1440}
    assert g.table.writes == []                # the registry already agrees with the wire


def test_ff69_the_wire_corrects_a_stale_registry_once(gui):
    g = gui(rows=[{**CAM03, "videoCodec": "h264"}], mtx=_cam03_mtx(), camera=media2("192.0.2.13", main="H264"))
    body = g.get("/api/cameras/cam-03/codec")[1]
    assert (body["chosen"], body["configured"], body["registry"]) == ("h265", "h264", {"recorded": True})
    row = g.table.row("cam-03")
    assert (row["videoCodec"], row["videoEncoderToken"], row["videoCodecProbe"]) == ("h265", "E0", "media2")
    g.get("/api/cameras/cam-03/codec")
    assert len(g.table.writes) == 1


def test_codec_of_an_unreachable_camera(gui):
    g = gui(mtx=_cam03_mtx())
    status, body = g.get("/api/cameras/cam-03/codec")
    assert status == 502 and body["wire"]["codec"] == "h265"


def test_codec_with_the_registry_unreachable(gui, monkeypatch):
    g = gui()

    def unreachable(*a, **k):
        raise OSError("timeout")
    monkeypatch.setattr(admin, "get_session", unreachable)
    assert g.get("/api/cameras/cam-03/codec")[0] == 503


# --- POST codec -------------------------------------------------------------------------------

def _switching_camera(g_holder, codec_after=True):
    """A camera that, once told, sends the new codec at the configured size."""
    def on_set(cfg):
        if codec_after:
            s = admin.onvif_media2.summarize(cfg)
            g_holder["g"].mtx.state["cam03"] = path_state(s["encoding"], s["width"], s["height"], "t1")
    return media2("192.0.2.13", main="H265", main_size=(1920, 1080), on_set=on_set)


def test_codec_switch_is_proved_on_the_wire(gui):
    holder = {}
    g = holder["g"] = gui(mtx=_cam03_mtx(), camera=_switching_camera(holder), active=("cam-03",))
    status, body = g.post("/api/cameras/cam-03/codec", {"videoCodec": "h264"})
    assert status == 200, body
    assert (body["unchanged"], body["wire"]["codec"], body["settings"]["encoding"]) == (False, "h264", "H264")
    # The picture size comes from the wire, not the camera's stale configuration (1920x1080).
    assert (body["wire"]["width"], body["wire"]["height"]) == (2560, 1440)
    assert body["producer"] == "active" and body["registry"] == {"recorded": True}
    assert g.table.row("cam-03")["videoCodec"] == "h264"
    # The running producer is not stopped: it restarts itself on the new codec (#44).
    assert systemctl_writes(g.system) == []


def test_a_codec_the_wire_already_carries_leaves_the_camera_alone(gui):
    g = gui(mtx=_cam03_mtx(), camera=media2("192.0.2.13"))
    body = g.post("/api/cameras/cam-03/codec", {"videoCodec": "h265"})[1]
    assert body["unchanged"] is True and g.camera.sets == []


def test_a_switch_the_wire_never_shows_is_a_504(gui):
    holder = {}
    g = holder["g"] = gui(mtx=_cam03_mtx(), camera=_switching_camera(holder, codec_after=False))
    start = g.clock.now
    status, body = g.post("/api/cameras/cam-03/codec", {"videoCodec": "h264"})
    assert status == 504 and "receives H265" in body["error"]
    assert g.clock.now - start >= admin.CODEC_VERIFY_SEC
    assert g.table.row("cam-03")["videoCodec"] == "h265"     # not recorded


def test_an_encoder_without_the_codec_is_a_409(gui):
    g = gui(mtx=_cam03_mtx(), camera=media2("192.0.2.13", main="H264",
                                            options={"E0": encoder_options("H264"),
                                                     "E1": encoder_options("H264")}))
    assert g.post("/api/cameras/cam-03/codec", {"videoCodec": "h265"})[0] == 409


@pytest.mark.parametrize("camera_id, codec, status", [
    ("cam-01", "h265", 400),             # the Pi has no H.265 encoder
    ("cam-03", "hevc", 400), ("cam-03", None, 400),
    ("cam-05", "h264", 409), ("cam-09", "h264", 404),
])
def test_codec_switch_refuses(gui, camera_id, codec, status):
    g = gui(mtx=_cam03_mtx(), camera=media2("192.0.2.13"))
    assert g.post(f"/api/cameras/{camera_id}/codec", {"videoCodec": codec})[0] == status
    assert g.camera.sets == [] and g.table.writes == []


def test_the_usb_cameras_own_codec_is_unchanged(gui):
    assert gui().post("/api/cameras/cam-01/codec", {"videoCodec": "h264"}) == \
        (200, {"cameraId": "cam-01", "videoCodec": "h264", "unchanged": True})


def test_codec_switch_with_an_unreachable_camera(gui):
    assert gui(mtx=_cam03_mtx()).post("/api/cameras/cam-03/codec", {"videoCodec": "h264"})[0] == 502


def test_on_wire(gui):
    g = gui(mtx=_cam03_mtx())
    assert g.get("/api/cameras/cam-03/on-wire") == (200, {"cameraId": "cam-03", "onWire": "h265", "flowing": True})
    assert g.get("/api/cameras/cam3/on-wire")[0] == 400


# --- GET stream -------------------------------------------------------------------------------

def test_streams_of_a_camera_recording_main(gui):
    g = gui(mtx=_cam03_mtx(), camera=media2("192.0.2.13", main_fps="25"))
    body = g.get("/api/cameras/cam-03/stream")[1]
    assert (body["current"], [s["name"] for s in body["streams"]]) == ("main", ["main", "sub"])
    main, sub = body["streams"]
    assert main["fps"] is None and sub["fps"] == 15          # only 10 and 15 are ever shown (D5)
    assert body["live"] == {"stream": "sub", "path": "cam03_live",
                            "wire": {"codec": "h264", "width": 800, "height": 448}}


def test_the_usb_camera_has_one_fixed_stream(gui):
    assert gui().get("/api/cameras/cam-01/stream")[1]["switchable"] is False


# --- POST stream: switching main <-> sub ---------------------------------------------------------

def _reconnects(g_holder, codec="H264", size=(800, 448)):
    """MediaMTX reconnecting to cam03's new source: a new readyTime and the new stream's track."""
    def on_write(method, path, body):
        if path == "cam03" and method == "PATCH":
            # The registry was written first: a MediaMTX restart rebuilds the path from it.
            assert g_holder["g"].table.row("cam-03")["rtspUrl"] == body["source"]
            g_holder["g"].mtx.state["cam03"] = path_state(codec, *size, ready_time="t1")
    return on_write


def test_switch_to_sub_moves_recording_and_live_view_follows(gui):
    holder = {}
    g = holder["g"] = gui(mtx=_cam03_mtx(on_write=_reconnects(holder)), camera=media2("192.0.2.13"))
    status, body = g.post("/api/cameras/cam-03/stream", {"stream": "sub"})
    assert status == 200, body
    assert (body["stream"], body["wire"]) == ("sub", {"codec": "h264", "width": 800, "height": 448})
    row = g.table.row("cam-03")
    assert row["rtspUrl"] == SUB                                     # credentials carried over
    assert (row["videoCodec"], row["videoEncoderToken"]) == ("h264", "E1")   # from the wire
    # Recording the sub stream: live view is the recorded stream again.
    assert body["liveView"] == {"live": "recorded stream", "path": "cam03"}
    assert "liveRtspUrl" not in row
    assert ("DELETE", "/v3/config/paths/delete/cam03_live", None) in g.mtx.writes()
    assert ["sudo", "systemctl", "stop", "kvs-live@cam03.service"] in g.system.calls
    assert "/sub" in sync_mediamtx_paths.CACHE.read_text()


def test_switch_to_main_sets_live_view_up_on_sub(gui):
    holder = {}
    on_sub = {**CAM03, "rtspUrl": SUB}
    on_sub.pop("liveRtspUrl")
    mtx = FakeMediaMTX(conf={"cam03": {"source": SUB, "rtspTransport": "tcp"}},
                       state={"cam03": path_state("H264", 800, 448, ready_time="t0")}, flowing={"cam03"},
                       on_write=_reconnects(holder, "H265", (2560, 1440)))
    g = holder["g"] = gui(rows=[on_sub], mtx=mtx, camera=media2("192.0.2.13"))
    g.table.session.stub("kinesisvideo").add_response(
        "describe_stream", {"StreamInfo": {"StreamARN": arn("cam-03-live")}}, {"StreamName": "cam-03-live"})
    body = g.post("/api/cameras/cam-03/stream", {"stream": "main"})[1]
    assert body["liveView"]["live"] == "sub"
    assert g.table.row("cam-03")["liveRtspUrl"] == SUB
    assert g.mtx.conf["cam03_live"]["source"] == SUB


def test_a_failed_mediamtx_update_rolls_the_registry_back(gui):
    mtx = _cam03_mtx()
    del mtx.conf["cam03"]                      # the patch 404s
    g = gui(mtx=mtx, camera=media2("192.0.2.13"))
    assert g.post("/api/cameras/cam-03/stream", {"stream": "sub"})[0] == 502
    assert g.table.row("cam-03")["rtspUrl"] == MAIN


def test_a_switch_without_video_is_a_504(gui):
    g = gui(mtx=_cam03_mtx(), camera=media2("192.0.2.13"))      # MediaMTX never reconnects
    status, body = g.post("/api/cameras/cam-03/stream", {"stream": "sub"})
    assert status == 504 and "no video" in body["error"]


@pytest.mark.parametrize("body, status", [
    ({}, 400), ({"fps": 20}, 400), ({"fps": True}, 400), ({"stream": "third"}, 400),
])
def test_stream_switch_refuses(gui, body, status):
    g = gui(mtx=_cam03_mtx(), camera=media2("192.0.2.13"))
    assert g.post("/api/cameras/cam-03/stream", body)[0] == status
    assert g.mtx.writes() == [] and g.table.writes == []


@pytest.mark.parametrize("camera_id, status", [("cam-01", 400), ("cam-05", 409), ("cam-09", 404)])
def test_stream_switch_needs_a_registered_onvif_camera(gui, camera_id, status):
    assert gui().post(f"/api/cameras/{camera_id}/stream", {"stream": "sub"})[0] == status


def test_a_registered_stream_the_camera_no_longer_has(gui):
    g = gui(rows=[{**CAM03, "rtspUrl": "rtsp://admin:pw3@192.0.2.13:554/gone"}], camera=media2("192.0.2.13"))
    assert g.post("/api/cameras/cam-03/stream", {"stream": "sub"})[0] == 502


# --- POST stream: frame rate ----------------------------------------------------------------------

def _ffprobe_at(fps, seconds=4):
    """What _measure_fps's ffprobe prints: one pts per video packet, after a few N/A."""
    lines = ["N/A", "N/A"] + [f"{i / fps:.6f}," for i in range(int(fps * seconds) + 1)]
    return "\n".join(lines) + "\n"


def test_frame_rate_is_set_and_counted_on_the_wire(gui):
    g = gui(mtx=_cam03_mtx(), camera=media2("192.0.2.13"), run={("ffprobe",): _ffprobe_at(10)})
    status, body = g.post("/api/cameras/cam-03/stream", {"fps": 10})
    assert status == 200 and "warning" not in body
    # 30 frames at 15 fps is 2 s between keyframes; at 10 fps that is 20 frames.
    assert body["fps"] == {"asked": 10, "measured": 10.0, "configured": 10.0, "govLength": 20}
    probe = next(c for c in g.system.calls if c[0] == "ffprobe")
    assert probe[-1] == "rtsp://127.0.0.1:8554/cam03"         # MediaMTX's copy, not the camera


def test_a_camera_sending_fewer_frames_is_reported_not_rolled_back(gui):
    g = gui(mtx=_cam03_mtx(), camera=media2("192.0.2.13"), run={("ffprobe",): _ffprobe_at(7)})
    body = g.post("/api/cameras/cam-03/stream", {"fps": 10})[1]
    assert body["fps"]["measured"] == 7.0 and "receives 7.0 fps" in body["warning"]
    assert len(g.camera.sets) == 1


def test_a_rate_the_encoder_does_not_offer(gui):
    cam = media2("192.0.2.13", options={"E0": encoder_options("H265", frame_rates=(5.0, 15.0)),
                                        "E1": encoder_options("H264")})
    assert gui(mtx=_cam03_mtx(), camera=cam).post("/api/cameras/cam-03/stream", {"fps": 10})[0] == 502


@pytest.mark.parametrize("output, fps", [
    (_ffprobe_at(15), 15.0),
    ("0.2,\n0.0,\n0.1,\n0.3,\n", 10.0),          # out of order
    ("N/A\n0.0,\n0.1,\n", None),                 # fewer than three packets
    ("1.0\n1.0\n1.0\n", None),                   # no time passed
    ("", None),
])
def test_measure_fps(fake_run, output, fps):
    fake_run(admin, {("ffprobe",): output})
    assert admin._measure_fps("cam03") == fps


def test_measure_fps_timeout(monkeypatch):
    def hangs(*a, **k):
        raise subprocess.TimeoutExpired("ffprobe", 14)
    monkeypatch.setattr(admin, "subprocess", ModuleShim(subprocess, run=hangs))
    assert admin._measure_fps("cam03") is None


@pytest.mark.parametrize("measured, fps, ok", [(19.8, 20, True), (8.6, 10, True), (8.4, 10, False),
                                               (None, 10, False)])
def test_fps_tolerance(measured, fps, ok):
    # cam-03 sends 19.8 fps at a 20 fps limit.
    assert admin._fps_ok(measured, fps) is ok
