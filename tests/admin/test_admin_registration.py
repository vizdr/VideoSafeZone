"""Registering, re-registering and unregistering a camera in the admin GUI (PYTEST.md, Phase 3).

These touch everything at once -- MediaMTX, systemd, KVS and the registry -- in an order that
matters, with rollbacks when a step fails. Each test checks what reached each of them.
"""
import pytest

import onvif_discovery
import sync_mediamtx_paths
from fakes import FakeMediaMTX, path_state
from gui_world import CAM02, CAM03, CAM05, ROWS, Unreachable, admin, arn, media2, systemctl_writes

PROVISION = admin.PROVISION_SH
NEW = {"cameraId": "cam-04", "host": "192.0.2.14", "port": 80, "user": "admin", "password": "pw4",
       "streamUri": "rtsp://192.0.2.14:554/main", "hasIrControl": False,
       "endpointRef": "urn:uuid:00000000-0000-0000-0000-0000000000c4", "audioCapable": True,
       "displayName": "Garden"}
MOTION = {"motion": {"topic": "tns1:VideoSource/MotionAlarm", "field": "State", "match": "known"}}


def _detects_motion(host, port, user, password):
    return MOTION, set(t["topic"] for t in MOTION.values()), f"http://{host}/"


def _expect_kvs(g, main_type="video/h265", live=True, main_exists=False):
    kv = g.table.session.stub("kinesisvideo")
    if main_exists:
        kv.add_client_error("create_stream", "ResourceInUseException", http_status_code=400)
        kv.add_response("describe_stream", {"StreamInfo": {"StreamARN": arn("cam-04")}},
                        {"StreamName": "cam-04"})
    else:
        kv.add_response("create_stream", {"StreamARN": arn("cam-04")},
                        {"StreamName": "cam-04", "DataRetentionInHours": 24, "MediaType": main_type})
    if live:
        kv.add_client_error("describe_stream", "ResourceNotFoundException", http_status_code=404,
                            expected_params={"StreamName": "cam-04-live"})
        kv.add_response("create_stream", {"StreamARN": arn("cam-04-live")},
                        {"StreamName": "cam-04-live", "DataRetentionInHours": 1, "MediaType": "video/h264"})


def _new_camera_gui(gui, *, wire="H265", flowing=True, camera=None, **kw):
    mtx = FakeMediaMTX(state={"cam04": path_state(wire, 2560, 1440)} if wire else {},
                       flowing={"cam04"} if flowing else ())
    return gui(mtx=mtx, camera=camera if camera is not None else media2("192.0.2.14"),
               detection=kw.pop("detection", _detects_motion), **kw)


# --- a new camera -------------------------------------------------------------------------------

def test_register_a_new_camera_end_to_end(gui):
    g = _new_camera_gui(gui)
    _expect_kvs(g)
    status, body = g.post("/api/cameras", NEW)
    assert status == 201, body
    assert body == {"cameraId": "cam-04", "kvsStreamArn": arn("cam-04"), "updated": False, "fps": None,
                    "detectionModes": ["motion"],
                    "liveView": {"live": "sub", "path": "cam04_live", "kvsStream": "cam-04-live",
                                 "kvsStreamArn": arn("cam-04-live")}}
    # MediaMTX: the recorded path, then live view on the sub stream, both over TCP.
    assert g.mtx.writes() == [
        ("POST", "/v3/config/paths/add/cam04", {"source": NEW["streamUri"], "rtspTransport": "tcp"}),
        ("POST", "/v3/config/paths/add/cam04_live",
         {"source": "rtsp://192.0.2.14:554/sub", "rtspTransport": "tcp"})]
    # systemd: provisioned and started, never enabled (#81).
    assert systemctl_writes(g.system) == [["sudo", PROVISION, "cam-04", "cam04"]]
    row = g.table.row("cam-04")
    assert {k: row[k] for k in ("mode", "onvifHost", "onvifPort", "onvifUser", "onvifPassword",
                                "rtspUrl", "onvifEndpointRef", "hasIrControl", "kvsStreamArn",
                                "audioCapable", "displayName", "liveRtspUrl")} == {
        "mode": "passthrough", "onvifHost": "192.0.2.14", "onvifPort": 80, "onvifUser": "admin",
        "onvifPassword": "pw4", "rtspUrl": NEW["streamUri"], "onvifEndpointRef": NEW["endpointRef"],
        "hasIrControl": False, "kvsStreamArn": arn("cam-04"), "audioCapable": True,
        "displayName": "Garden", "liveRtspUrl": "rtsp://192.0.2.14:554/sub"}
    assert (row["videoCodecCaps"], row["videoCodecDefault"], row["videoEncoderToken"]) == \
        ({"h264": "camera", "h265": "camera"}, "h265", "E0")
    assert (row["detectionModes"], row["webUrl"]) == (["motion"], "http://192.0.2.14/")
    assert row["createdAt"].endswith("Z")
    assert "audioEnabled" not in row             # recording audio stays the user's choice (#60)
    assert "cam-04" in sync_mediamtx_paths.CACHE.read_text()      # an offline restart keeps it


def test_ff69_the_default_codec_comes_from_the_wire(gui):
    # cam-03 claims H264 and sends H.265: the KVS stream's MediaType must follow the wire.
    g = _new_camera_gui(gui, wire="H265", camera=media2("192.0.2.14", main="H264"))
    _expect_kvs(g, main_type="video/h265")
    assert g.post("/api/cameras", NEW)[0] == 201
    assert g.table.row("cam-04")["videoCodecDefault"] == "h265"


def test_without_video_the_probe_waits_then_takes_the_claim(gui):
    g = _new_camera_gui(gui, wire=None, camera=media2("192.0.2.14", main="H264"))
    _expect_kvs(g, main_type="video/h264")
    start = g.clock.now
    assert g.post("/api/cameras", NEW)[0] == 201
    assert g.clock.now - start >= admin.WIRE_WAIT_SEC          # waited, in fake time


def test_an_unreachable_camera_is_registered_conservatively(gui):
    # H.265 not offered, H.264 assumed; live view not set up, and the response says why.
    g = _new_camera_gui(gui, wire=None, camera=Unreachable(), detection=None)
    _expect_kvs(g, main_type="video/h264", live=False)
    status, body = g.post("/api/cameras", NEW)
    assert status == 201
    assert g.table.row("cam-04")["videoCodecProbe"].startswith("fallback")
    assert "live view not set up" in body["liveView"]["error"] and body["detectionModes"] is None


def test_a_kvs_stream_that_already_exists_is_reused(gui):
    g = _new_camera_gui(gui)
    _expect_kvs(g, main_exists=True)
    assert g.post("/api/cameras", NEW)[1]["kvsStreamArn"] == arn("cam-04")


def test_ff67_rtsp_login_goes_into_the_pulled_uri(gui):
    def needs_login(user):
        if user is None:
            raise onvif_discovery.RtspUnauthorized("401")
        return "v=0\r\n"
    g = _new_camera_gui(gui, rtsp=needs_login)
    _expect_kvs(g)
    assert g.post("/api/cameras", NEW)[0] == 201
    assert g.table.row("cam-04")["rtspUrl"] == "rtsp://admin:pw4@192.0.2.14:554/main"
    assert g.mtx.conf["cam04"]["source"] == "rtsp://admin:pw4@192.0.2.14:554/main"


def test_a_refused_rtsp_login_creates_nothing(gui):
    def refuses(user):
        raise onvif_discovery.RtspUnauthorized("401")
    g = _new_camera_gui(gui, rtsp=refuses)
    status, body = g.post("/api/cameras", NEW)
    assert status == 400 and "refuses user 'admin'" in body["error"]
    assert (g.mtx.writes(), systemctl_writes(g.system), g.table.writes) == ([], [], [])


def test_ff36_failed_provisioning_rolls_the_path_back_with_delete(gui):
    g = _new_camera_gui(gui, run={("sudo", PROVISION): ("", 1, "invalid camera id")})
    status, body = g.post("/api/cameras", NEW)
    assert status == 500 and "invalid camera id" in body["error"]
    assert [w[:2] for w in g.mtx.writes()] == [("POST", "/v3/config/paths/add/cam04"),
                                                ("DELETE", "/v3/config/paths/delete/cam04")]
    assert "cam04" not in g.mtx.conf and g.table.row("cam-04") is None


def test_an_existing_mediamtx_path_stops_registration_before_systemd(gui):
    g = _new_camera_gui(gui)
    g.mtx.conf["cam04"] = {"source": "rtsp://192.0.2.99/other"}
    assert g.post("/api/cameras", NEW)[0] == 502
    assert systemctl_writes(g.system) == [] and g.table.row("cam-04") is None


def test_the_registration_frame_rate_is_set_on_the_camera(gui):
    g = _new_camera_gui(gui)
    _expect_kvs(g)
    body = g.post("/api/cameras", {**NEW, "fps": 10})[1]
    # 30 frames at 15 fps is 2 s between keyframes; at 10 fps that is 20 frames.
    assert body["fps"] == {"asked": 10, "configured": 10.0, "govLength": 20}


@pytest.mark.parametrize("change, phrase", [
    ({"cameraId": "cam-4"}, "cameraId must look like"),
    ({"cameraId": "camera-04"}, "cameraId must look like"),
    ({"host": ""}, "required"),
    ({"streamUri": None}, "required"),
    ({"user": ""}, "required"),
    ({"password": 1234}, "must be strings"),
    ({"audioCapable": "yes"}, "audioCapable"),
    ({"fps": 25}, "fps must be one of"),
    ({"displayName": "Hall"}, "already the name of cam-03"),
    ({"displayName": "cam-07"}, "camera ID"),
])
def test_registration_refuses_before_changing_anything(gui, change, phrase):
    g = _new_camera_gui(gui)
    status, body = g.post("/api/cameras", {**NEW, **change})
    assert status == 400 and phrase in body["error"]
    assert (g.mtx.writes(), systemctl_writes(g.system), g.table.writes) == ([], [], [])


def test_an_empty_password_is_allowed(gui):
    g = _new_camera_gui(gui)
    _expect_kvs(g)
    assert g.post("/api/cameras", {**NEW, "password": ""})[0] == 201
    assert g.table.row("cam-04")["onvifPassword"] == ""


# --- re-registration ---------------------------------------------------------------------------

def _known_gui(gui, host="192.0.2.13", **kw):
    """cam-03 as it runs: both MediaMTX paths configured, video flowing, the live KVS stream there."""
    mtx = FakeMediaMTX(conf={"cam03": {"source": CAM03["rtspUrl"], "rtspTransport": "tcp"},
                             "cam03_live": {"source": CAM03["liveRtspUrl"], "rtspTransport": "tcp"}},
                       state={"cam03": path_state("H265", 2560, 1440)}, flowing={"cam03"})
    g = gui(mtx=mtx, camera=media2(host), **kw)
    g.table.session.stub("kinesisvideo").add_response(
        "describe_stream", {"StreamInfo": {"StreamARN": arn("cam-03-live")}}, {"StreamName": "cam-03-live"})
    return g


REREG = {"cameraId": "cam-03", "host": "192.0.2.13", "user": "admin", "password": "pw3",
         "streamUri": "rtsp://192.0.2.13:554/main"}


def test_a_rescan_that_changes_nothing_costs_nothing(gui):
    # Patching `source` reconnects every reader -- a running producer and a recording included.
    g = _known_gui(gui)
    status, body = g.post("/api/cameras", REREG)
    assert status == 200, body
    assert (body["updated"], body["reactivated"], body["producer"]) == (True, False, "unchanged")
    assert g.mtx.writes() == [] and systemctl_writes(g.system) == []


def test_reregistration_updates_field_by_field(gui):
    # The registry is shared with VMS: what the form doesn't carry stays as it is.
    g = _known_gui(gui)
    g.post("/api/cameras", REREG)
    row = g.table.row("cam-03")
    for kept in ("recordingMode", "outageBufferSec", "kvsStreamArn", "displayName", "detectionModes"):
        assert row[kept] == CAM03[kept], kept
    assert row["rtspUrl"] == CAM03["rtspUrl"]               # the camera's URI, credentials carried over
    assert "audioCapable" not in row                        # not asked at this scan: left alone
    assert row["updatedAt"].endswith("Z")


def test_a_camera_that_moved_is_followed(gui):
    g = _known_gui(gui, host="192.0.2.23")
    g.post("/api/cameras", {**REREG, "host": "192.0.2.23", "streamUri": "rtsp://192.0.2.23:554/main"})
    assert g.mtx.writes() == [
        ("PATCH", "/v3/config/paths/patch/cam03",
         {"source": "rtsp://admin:pw3@192.0.2.23:554/main", "rtspTransport": "tcp"}),
        ("PATCH", "/v3/config/paths/patch/cam03_live",
         {"source": "rtsp://admin:pw3@192.0.2.23:554/sub", "rtspTransport": "tcp"})]
    row = g.table.row("cam-03")
    assert (row["onvifHost"], row["liveRtspUrl"]) == ("192.0.2.23", "rtsp://admin:pw3@192.0.2.23:554/sub")


def test_query_credentials_are_kept_on_reregistration(gui):
    # cam-02's URI carries ?username=..&password=..; the camera reports it without them.
    g = gui(mtx=FakeMediaMTX(conf={"cam02": {"source": CAM02["rtspUrl"], "rtspTransport": "tcp"}}),
            camera=media2("192.0.2.12", sub=False))
    body = {"cameraId": "cam-02", "host": "192.0.2.12", "user": "admin", "password": "pw2",
            "streamUri": "rtsp://192.0.2.12:554/Streaming/Channels/101"}
    assert g.post("/api/cameras", body)[0] == 200
    assert g.table.row("cam-02")["rtspUrl"] == CAM02["rtspUrl"]
    assert g.mtx.writes()[0][0] == "DELETE"     # only live view's removal (no sub stream): no patch of cam02


def test_reregistering_an_unregistered_camera_brings_it_back(gui):
    g = gui(mtx=FakeMediaMTX(state={"cam05": path_state("H264")}, flowing={"cam05"}),
            camera=media2("192.0.2.15", main="H264", sub=False))
    status, body = g.post("/api/cameras", {"cameraId": "cam-05", "host": "192.0.2.15", "user": "admin",
                                           "password": "pw5", "streamUri": "rtsp://192.0.2.15:554/main"})
    assert status == 200 and body["reactivated"] is True
    row = g.table.row("cam-05")
    assert "unregisteredAt" not in row and row["displayName"] == "Garage"
    assert g.mtx.writes()[0] == ("POST", "/v3/config/paths/add/cam05",
                                 {"source": CAM05["rtspUrl"], "rtspTransport": "tcp"})
    # Its producer stays stopped until someone presses Start (#81).
    assert not any(c[:3] == ["sudo", "systemctl", "start"] for c in g.system.calls)


def test_a_camera_from_the_other_card_gets_a_producer_not_started(gui):
    cam06 = {**CAM05, "cameraId": "cam-06", "onvifHost": "192.0.2.16", "displayName": "Shed"}
    cam06.pop("unregisteredAt")
    g = gui(rows=ROWS + [cam06], mtx=FakeMediaMTX(conf={"cam06": {"source": cam06["rtspUrl"], "rtspTransport": "tcp"}}),
            camera=media2("192.0.2.16", sub=False))
    body = g.post("/api/cameras", {"cameraId": "cam-06", "host": "192.0.2.16", "user": "admin",
                                   "password": "pw5", "streamUri": "rtsp://192.0.2.16:554/main"})[1]
    assert body["producer"] == "provisioned (not started)"
    assert ["sudo", PROVISION, "cam-06", "cam06", "no-start"] in g.system.calls


def test_cam02_is_never_reprovisioned(gui):
    # It predates the kvs-cam@ template; provisioning it would start a second producer on its stream.
    g = gui(producers=("cam-01", "cam-03"), camera=media2("192.0.2.12", sub=False),
            mtx=FakeMediaMTX(conf={"cam02": {"source": CAM02["rtspUrl"], "rtspTransport": "tcp"}}))
    assert g.post("/api/cameras", {"cameraId": "cam-02", "host": "192.0.2.12", "user": "admin",
                                   "password": "pw2", "streamUri": CAM02["rtspUrl"]})[0] == 200
    assert not any(PROVISION in c for c in g.system.calls)


def test_the_usb_camera_cannot_be_reregistered(gui):
    g = gui()
    assert g.post("/api/cameras", {"cameraId": "cam-01", "host": "192.0.2.11", "user": "a",
                                   "streamUri": "rtsp://x/"})[0] == 409
    assert g.table.writes == []


def test_reregistration_can_clear_the_name_and_record_audio(gui):
    g = _known_gui(gui)
    g.post("/api/cameras", {**REREG, "displayName": "", "audioCapable": False})
    row = g.table.row("cam-03")
    assert "displayName" not in row and row["audioCapable"] is False


def test_mediamtx_down_leaves_the_row_untouched(gui):
    g = gui(mtx=FakeMediaMTX(down=True), camera=media2("192.0.2.13"))
    assert g.post("/api/cameras", REREG)[0] == 502
    assert g.table.writes == []


# --- unregister --------------------------------------------------------------------------------

def _unregister_gui(gui, **kw):
    mtx = FakeMediaMTX(conf={"cam03": {"source": CAM03["rtspUrl"]}, "cam03_live": {"source": CAM03["liveRtspUrl"]},
                             "cam02": {"source": CAM02["rtspUrl"]}}, **kw)
    return gui(mtx=mtx)


def test_unregister_keeps_the_row_and_stops_everything(gui):
    g = _unregister_gui(gui)
    status, body = g.post("/api/cameras/cam-03/unregister")
    assert status == 200 and body["unregisteredAt"].endswith("Z") and "warning" not in body
    row = g.table.row("cam-03")
    assert row["unregisteredAt"] == body["unregisteredAt"]
    assert {k: row[k] for k in CAM03} == CAM03      # everything else kept, for Re-register
    # Stopped, kept from starting at boot (#80: the sudo rule allows disable), live view stopped.
    assert systemctl_writes(g.system) == [
        ["sudo", "systemctl", "stop", "kvs-cam@cam03.service"],
        ["sudo", "systemctl", "disable", "kvs-cam@cam03.service"],
        ["sudo", "systemctl", "stop", "kvs-live@cam03.service"]]
    assert g.mtx.writes() == [("DELETE", "/v3/config/paths/delete/cam03", None),
                              ("DELETE", "/v3/config/paths/delete/cam03_live", None)]
    assert "cam02" in g.mtx.conf
    assert "cam-03" not in sync_mediamtx_paths.CACHE.read_text()


def test_unregister_twice_changes_nothing_more(gui):
    g = _unregister_gui(gui)
    assert g.post("/api/cameras/cam-05/unregister") == (200, {"cameraId": "cam-05", "unregisteredAt": CAM05["unregisteredAt"]})
    assert (g.table.writes, systemctl_writes(g.system), g.mtx.writes()) == ([], [], [])


@pytest.mark.parametrize("camera_id, status", [("cam-01", 409), ("cam-09", 404)])
def test_unregister_refuses(gui, camera_id, status):
    g = _unregister_gui(gui)
    assert g.post(f"/api/cameras/{camera_id}/unregister")[0] == status
    assert (g.table.writes, systemctl_writes(g.system), g.mtx.writes()) == ([], [], [])


def test_unregister_with_mediamtx_down_still_marks_and_stops(gui):
    g = _unregister_gui(gui, down=True)
    status, body = g.post("/api/cameras/cam-03/unregister")
    assert status == 200 and "next MediaMTX restart" in body["warning"]
    assert g.table.row("cam-03")["unregisteredAt"] and len(systemctl_writes(g.system)) == 3
