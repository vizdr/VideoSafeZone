"""The admin GUI's everyday routes: the camera list, scan, status, Start/Stop, IR, display names,
and the three settings it shares with the cloud Lambdas (PYTEST.md, Phase 3)."""
import json
from types import SimpleNamespace

import pytest

import camera_control
import onvif_discovery
import outage_buffer
from fakes import FakeMediaMTX, invoke, path_state
from gui_world import CAM02, CAM03, PRODUCERS, ROWS, admin, systemctl_writes
from loaders import load_lambda


def test_the_page_is_served(gui):
    resp = gui().client.get("/")
    assert resp.status_code == 200 and b"<html" in resp.data.lower()


# --- the camera list -----------------------------------------------------------------------------

def test_camera_list_never_sends_a_credential(gui):
    g = gui(mtx=FakeMediaMTX(state={"cam03": path_state("H265")}, flowing={"cam03"}))
    resp = g.client.get("/api/cameras")
    body = resp.get_data(as_text=True)
    for secret in ("pw2", "pw3", "5f4dcc3b"):
        assert secret not in body, secret
    cams = {c["cameraId"]: c for c in resp.get_json()["cameras"]}
    assert not {"onvifPassword", "rtspUrl", "liveRtspUrl"} & set(cams["cam-03"])
    assert cams["cam-03"]["streamUri"] == "rtsp://192.0.2.13:554/main"
    assert cams["cam-02"]["streamUri"].endswith("password=<redacted>")


def test_camera_list_adds_what_the_page_needs(gui):
    g = gui(mtx=FakeMediaMTX(state={"cam03": path_state("H265"), "cam02": path_state("H264")},
                             flowing={"cam03"}))
    cams = {c["cameraId"]: c for c in g.get("/api/cameras")[1]["cameras"]}
    assert [c for c in cams] == ["cam-01", "cam-02", "cam-03", "cam-05"]      # sorted
    cam03 = cams["cam-03"]
    assert (cam03["mediamtxPath"], cam03["previewPath"]) == ("cam03", "cam03_live")   # live view on sub
    assert (cam03["onWireCodec"], cam03["onWireFlowing"]) == ("h265", True)
    assert (cams["cam-02"]["onWireCodec"], cams["cam-02"]["onWireFlowing"]) == ("h264", False)  # #50
    assert cams["cam-02"]["previewPath"] == "cam02"
    assert all(c["hasProducer"] for c in cams.values())
    assert "software encoding" in cams["cam-01"]["videoCodecUnavailable"]["h265"]
    # Unregistered cameras stay in the list, marked, so the page can offer Re-register.
    assert cams["cam-05"]["unregisteredAt"]


def test_a_camera_without_a_producer_here_says_so(gui):
    g = gui(producers=("cam-01", "cam-02"))
    cams = {c["cameraId"]: c for c in g.get("/api/cameras")[1]["cameras"]}
    assert cams["cam-03"]["hasProducer"] is False


# --- scan ---------------------------------------------------------------------------------------

class _Device:
    def __init__(self, host, epr):
        self.host, self.epr = host, epr

    def getXAddrs(self):  # noqa: N802 -- wsdiscovery's names
        return [f"http://{self.host}/onvif/device_service"]

    def getScopes(self):  # noqa: N802
        return ["onvif://www.onvif.org/name/IPC"]

    def getEPR(self):  # noqa: N802
        return self.epr


@pytest.fixture
def lan(monkeypatch):
    asked = []
    monkeypatch.setattr(onvif_discovery, "scan", lambda timeout=4: [
        _Device("192.0.2.13", "urn:uuid:c3"), _Device("192.0.2.14", "urn:uuid:c4")])

    def enrich(xaddr, user, password):
        asked.append((xaddr, user, password))
        if "192.0.2.14" in xaddr:
            raise OSError("401 Unauthorized")
        return {"manufacturer": "Hik"}
    monkeypatch.setattr(onvif_discovery, "enrich_sync", enrich)
    return asked


def test_scan_without_a_user_only_lists(gui, lan):
    status, body = gui().post("/api/scan", {})
    assert status == 200 and [d["epr"] for d in body["devices"]] == ["urn:uuid:c3", "urn:uuid:c4"]
    assert lan == [] and "details" not in body["devices"][0]


def test_ff67_scan_logs_in_with_an_empty_password(gui, lan):
    status, body = gui().post("/api/scan", {"user": "admin"})
    assert lan[0] == ("http://192.0.2.13/onvif/device_service", "admin", "")
    assert body["devices"][0]["details"] == {"manufacturer": "Hik"}
    assert "401" in body["devices"][1]["enrichError"]      # one camera refusing doesn't stop the scan


# --- status (polled) ----------------------------------------------------------------------------

def _cache(ids):
    outage_buffer.REGISTRY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    outage_buffer.REGISTRY_CACHE.write_text(json.dumps({i: 0 for i in ids}))


def _aws_unreachable(monkeypatch):
    def unreachable(*a, **k):
        raise OSError("Temporary failure in name resolution")
    monkeypatch.setattr(admin, "get_session", unreachable)


def test_ff56_status_answers_from_the_local_cache_with_aws_down(gui, monkeypatch):
    # A DynamoDB lookup per poll hung ~36 s each while DNS failed, and froze the page (#56).
    g = gui(active=("cam-03",))
    _cache(["cam-01", "cam-02", "cam-03"])
    _aws_unreachable(monkeypatch)
    assert g.get("/api/cameras/cam-03/status") == (200, {"cameraId": "cam-03", "state": "active"})
    assert g.get("/api/cameras/cam-09/status")[0] == 404


def test_status_without_a_cache_asks_the_registry(gui):
    g = gui()
    assert g.get("/api/cameras/cam-02/status") == (200, {"cameraId": "cam-02", "state": "inactive"})
    assert g.get("/api/cameras/cam-09/status")[0] == 404


@pytest.mark.parametrize("camera_id", ["cam-3", "cam03", "../x", "cam-003"])
def test_status_checks_the_id(gui, camera_id):
    assert gui().get(f"/api/cameras/{camera_id}/status")[0] in (400, 404)


def test_ff37_status_of_a_camera_without_a_producer(gui):
    g = gui(producers=("cam-01", "cam-02"))
    _cache(["cam-03"])
    assert g.get("/api/cameras/cam-03/status")[1]["state"] == "no-producer"


# --- Start / Stop -------------------------------------------------------------------------------

@pytest.mark.parametrize("camera_id, unit", [
    ("cam-01", "kvs-cam01.service"), ("cam-02", "kvs-cam02.service"),
    ("cam-03", "kvs-cam@cam03.service"),                       # #7, #37
])
@pytest.mark.parametrize("action", ["start", "stop"])
def test_start_stop_reach_the_right_unit(gui, camera_id, unit, action):
    g = gui()
    assert g.post(f"/api/cameras/{camera_id}/{action}") == (200, {"cameraId": camera_id, "action": action})
    assert systemctl_writes(g.system) == [["sudo", "systemctl", action, unit]]


def test_no_unit_is_ever_enabled_by_start(gui):
    # A producer enabled would push to KVS after every reboot, unattended (#81).
    g = gui()
    g.post("/api/cameras/cam-03/start")
    assert not any("enable" in c for c in g.system.calls)


def test_an_unregistered_camera_cannot_start_but_can_stop(gui):
    g = gui()
    assert g.post("/api/cameras/cam-05/start")[0] == 409
    assert systemctl_writes(g.system) == []
    assert g.post("/api/cameras/cam-05/stop")[0] == 200


def test_start_without_a_producer_here_is_refused(gui):
    g = gui(producers=("cam-01", "cam-02"))
    status, body = g.post("/api/cameras/cam-03/start")
    assert status == 409 and "Re-register" in body["error"]
    assert systemctl_writes(g.system) == []


@pytest.mark.parametrize("url, status", [("/api/cameras/cam-03/reboot", 400),
                                         ("/api/cameras/cam-09/start", 404)])
def test_start_stop_refuses(gui, url, status):
    g = gui()
    assert g.post(url)[0] == status
    assert systemctl_writes(g.system) == []


# --- IR -----------------------------------------------------------------------------------------

@pytest.fixture
def ir(monkeypatch):
    sent = []

    def set_ir_mode(item, mode):
        if mode not in camera_control.ALLOWED_IR_MODES:    # the real check, before any camera call
            raise ValueError(f"invalid IR mode '{mode}'")
        sent.append((item["cameraId"], mode))
    monkeypatch.setattr(camera_control, "set_ir_mode", set_ir_mode)
    return sent


def test_ir_reaches_the_camera(gui, ir):
    assert gui().post("/api/cameras/cam-02/ir", {"mode": "AUTO"}) == (200, {"cameraId": "cam-02", "irMode": "AUTO"})
    assert ir == [("cam-02", "AUTO")]


@pytest.mark.parametrize("camera_id, status", [("cam-03", 400), ("cam-05", 409), ("cam-09", 404)])
def test_ir_refuses(gui, ir, camera_id, status):
    assert gui().post(f"/api/cameras/{camera_id}/ir", {"mode": "ON"})[0] == status
    assert ir == []


def test_an_unknown_ir_mode_never_reaches_the_camera(gui, ir):
    status, _ = gui().post("/api/cameras/cam-02/ir", {"mode": "night"})
    assert status >= 400 and ir == []


@pytest.mark.xfail(reason="FoundAndFixed.md #85: an unknown IR mode answers 500, not 400 as the "
                          "cloud's publish-cmd does")
def test_ff85_an_unknown_ir_mode_is_a_400(gui, ir):
    assert gui().post("/api/cameras/cam-02/ir", {"mode": "night"})[0] == 400


# --- display names ------------------------------------------------------------------------------

NAMES = {"cam-01": None, "cam-03": "Hall", "cam-05": "Garage"}


@pytest.mark.parametrize("raw, name", [
    ("Kitchen", "Kitchen"), ("  Front   door ", "Front door"), ("Küche-1", "Küche-1"),
    ("hall", None),            # cam-03's own name, any case: allowed for cam-03 only (below)
    ("", None), (None, None),
])
def test_display_name_rules_accept(raw, name):
    assert admin._display_name(raw, "cam-03", NAMES) == (name if raw != "hall" else "hall", None)


@pytest.mark.parametrize("raw, phrase", [
    ("garage", "already the name of cam-05"),     # unregistered cameras' names count: they come back
    ("HALL", "already the name of cam-03"),
    ("cam-07", "camera ID"), ("CAM7", "camera ID"),
    ("<b>x</b>", "characters"), ("a" * 33, "characters"), ("$(reboot)", "characters"),
    ("___", "letter or digit"), (5, "must be text"),
])
def test_display_name_rules_refuse(raw, phrase):
    name, err = admin._display_name(raw, "cam-01", NAMES)
    assert name is None and phrase in err


def test_set_and_clear_a_name_writing_only_that_field(gui):
    g = gui()
    assert g.post("/api/cameras/cam-01/name", {"displayName": "Desk"}) == \
        (200, {"cameraId": "cam-01", "displayName": "Desk"})
    assert g.table.row("cam-01")["displayName"] == "Desk"
    assert g.post("/api/cameras/cam-03/name", {"displayName": ""})[0] == 200
    row = g.table.row("cam-03")
    assert "displayName" not in row and row["recordingMode"] == "manual" and row["updatedAt"]


def test_an_unchanged_name_writes_nothing(gui):
    g = gui()
    assert g.post("/api/cameras/cam-03/name", {"displayName": "Hall"})[1]["unchanged"] is True
    assert g.table.writes == []


@pytest.mark.parametrize("url, body, status", [
    ("/api/cameras/cam-09/name", {"displayName": "X"}, 404),
    ("/api/cameras/cam9/name", {"displayName": "X"}, 400),
    ("/api/cameras/cam-01/name", {"displayName": "Garage"}, 400),
])
def test_name_refuses(gui, url, body, status):
    g = gui()
    assert g.post(url, body)[0] == status
    assert g.table.writes == []


# --- settings shared with the cloud Lambdas ----------------------------------------------------
#
# The GUI routes mirror set-camera-mode, set-camera-audio and set-camera-outage-buffer on purpose:
# both write the one registry row, so either page sees the other's change. Each case below goes to
# both; they must agree on whether it is accepted, and on what the row then holds. (Status codes
# differ by design: the GUI answers 404 for an unknown camera, the Lambdas 400.)

LAMBDA_ROWS = ROWS + [{"cameraId": "cam-04", "mode": "passthrough", "onvifHost": "192.0.2.14"}]

SETTINGS = [
    # (GUI route, Lambda, body without cameraId)
    ("mode", "set_camera_mode", {"recordingMode": "motion"}),
    ("mode", "set_camera_mode", {"recordingMode": "manual"}),
    ("mode", "set_camera_mode", {"recordingMode": "human"}),          # cam-03: not in its list (#65)
    ("mode", "set_camera_mode", {"recordingMode": "person"}),
    ("audio", "set_camera_audio", {"audioEnabled": True}),
    ("audio", "set_camera_audio", {"audioEnabled": "yes"}),
    ("outage-buffer", "set_camera_outage_buffer", {"outageBufferSec": 600}),
    ("outage-buffer", "set_camera_outage_buffer", {"outageBufferSec": 45}),
    ("outage-buffer", "set_camera_outage_buffer", {"outageBufferSec": False}),
]


@pytest.mark.parametrize("route, lambda_name, body", SETTINGS)
@pytest.mark.parametrize("camera_id", ["cam-01", "cam-02", "cam-03", "cam-04", "cam-05", "cam-09"])
def test_gui_and_lambda_agree(gui, aws, route, lambda_name, body, camera_id):
    g = gui(rows=LAMBDA_ROWS)
    gui_status, _ = g.post(f"/api/cameras/{camera_id}/{route}", body)
    lam = load_lambda(lambda_name)
    fake = aws(lam, LAMBDA_ROWS)
    lam_status, _ = invoke(lam, {"cameraId": camera_id, **body})
    assert (gui_status < 300) == (lam_status < 300), (gui_status, lam_status)
    if gui_status == 409 or lam_status == 409:
        assert gui_status == lam_status == 409             # unregistered, or a mode it doesn't publish
    assert g.table.row(camera_id) == fake.tables["cameras"].row(camera_id)


def test_the_gui_settings_route_needs_a_known_camera(gui):
    assert gui().post("/api/cameras/cam-09/mode", {"recordingMode": "manual"})[0] == 404
