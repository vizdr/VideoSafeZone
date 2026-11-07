"""The admin GUI with several adapters (DEALER-PANEL-PLAN.md Phase 7.2, D11, D12).

D12: the registry is one table for every adapter, and this Pi acts only on its own rows -- another
adapter's camera (adapterId) and one released from this adapter (releasedFrom, D15) are left out
of the list, refused by every route that changes a camera, and unknown to the name route; Stop
stays possible, since a producer may still run here.

D11: a new camera's ID is allocated -- one past the highest in the registry -- and claimed before
anything is made, so two registrations can't both have it: the one that comes second finds the ID
taken before it has touched a path, a unit or a stream, and an allocated ID is followed by the next.
"""
import pytest

import camera_registration
from fakes import FakeMediaMTX, FakeTable, path_state
from gui_world import ROWS, admin, arn, media2, systemctl_writes

FOREIGN = {"cameraId": "cam-07", "mode": "passthrough", "onvifHost": "192.0.2.17", "onvifPort": 80,
           "onvifUser": "admin", "onvifPassword": "theirs", "rtspUrl": "rtsp://192.0.2.17:554/main",
           "kvsStreamArn": arn("cam-07"), "adapterId": "adapter-99", "displayName": "Gate"}
RELEASED = {"cameraId": "cam-08", "mode": "passthrough", "onvifHost": "192.0.2.18", "onvifPort": 80,
            "onvifUser": "admin", "onvifPassword": "old", "rtspUrl": "rtsp://192.0.2.18:554/main",
            "kvsStreamArn": arn("cam-08"), "releasedFrom": "test-thing"}
NEW = {"host": "192.0.2.14", "port": 80, "user": "admin", "password": "pw4",
       "streamUri": "rtsp://192.0.2.14:554/main", "hasIrControl": False,
       "endpointRef": "urn:uuid:00000000-0000-0000-0000-0000000000c4"}


def _detects_nothing(host, port, user, password):
    raise RuntimeError("no event service")


# --- D12: another adapter's rows ---------------------------------------------------------------------

def test_the_list_shows_only_this_adapters_cameras(gui):
    g = gui(rows=ROWS + [FOREIGN, RELEASED])
    status, body = g.get("/api/cameras")
    assert status == 200
    assert [c["cameraId"] for c in body["cameras"]] == ["cam-01", "cam-02", "cam-03", "cam-05"]


@pytest.mark.parametrize("route, body", [
    ("start", None), ("unregister", None), ("mode", {"recordingMode": "manual"}),
    ("audio", {"audioEnabled": True}), ("outage-buffer", {"outageBufferSec": 0}), ("ir", {"mode": "AUTO"}),
    ("codec", {"videoCodec": "h264"}), ("stream", {"stream": "main"}), ("detection/test", {"mode": "motion"}),
])
@pytest.mark.parametrize("row", [FOREIGN, RELEASED], ids=["another adapter's", "released"])
def test_a_camera_that_isnt_this_adapters_is_refused(gui, route, body, row):
    g = gui(rows=ROWS + [row])
    status, out = g.post(f"/api/cameras/{row['cameraId']}/{route}", body)
    assert status == 409 and "isn't this adapter's camera" in out["error"], (status, out)
    assert systemctl_writes(g.system) == [] and g.mtx.writes() == []
    assert g.table.row(row["cameraId"]) == row                      # untouched


def test_a_producer_still_running_here_can_be_stopped(gui):
    g = gui(rows=ROWS + [RELEASED], producers=("cam-01", "cam-08"))
    status, _ = g.post("/api/cameras/cam-08/stop")
    assert status == 200
    assert systemctl_writes(g.system) == [["sudo", "systemctl", "stop", "kvs-cam@cam08.service"]]


def test_another_adapters_camera_is_unknown_to_the_name_route_and_its_name_is_free_here(gui):
    g = gui(rows=ROWS + [FOREIGN])
    assert g.post("/api/cameras/cam-07/name", {"displayName": "Mine now"})[0] == 404
    status, out = g.post("/api/cameras/cam-03/name", {"displayName": "Gate"})   # another customer's name
    assert status == 200 and out["displayName"] == "Gate"


# --- D11: allocated IDs, claimed first ---------------------------------------------------------------

def _registering_gui(gui, path, rows=ROWS):
    mtx = FakeMediaMTX(state={path: path_state("H264", 1920, 1080)}, flowing={path})
    return gui(rows=rows, mtx=mtx, camera=media2("192.0.2.14"), detection=_detects_nothing)


def _expect_streams(g, cid):
    kv = g.table.session.stub("kinesisvideo")
    kv.add_response("create_stream", {"StreamARN": arn(cid)}, {"StreamName": cid, "DataRetentionInHours": 24,
                                                               "MediaType": "video/h264"})
    kv.add_client_error("describe_stream", "ResourceNotFoundException", http_status_code=404,
                        expected_params={"StreamName": f"{cid}-live"})
    kv.add_response("create_stream", {"StreamARN": arn(f"{cid}-live")},
                    {"StreamName": f"{cid}-live", "DataRetentionInHours": 1, "MediaType": "video/h264"})


def test_a_new_camera_gets_the_next_free_id_past_every_adapters(gui):
    g = _registering_gui(gui, "cam08", rows=ROWS + [FOREIGN])         # cam-07 is another adapter's
    _expect_streams(g, "cam-08")
    status, body = g.post("/api/cameras", NEW)
    assert status == 201 and body["cameraId"] == "cam-08", body
    assert systemctl_writes(g.system) == [["sudo", admin.PROVISION_SH, "cam-08", "cam08"]]
    row = g.table.row("cam-08")
    assert row["adapterId"] == "test-thing" and row["mode"] == "passthrough"
    assert not {"registering", "unregisteredAt", "abandonedAt"} & set(row)       # the claim is gone


def test_an_id_claimed_meanwhile_is_followed_by_the_next(gui, monkeypatch):
    g = _registering_gui(gui, "cam07")
    _expect_streams(g, "cam-07")
    put = g.table.put_item

    def someone_else_first(Item, **kw):          # another registration claims cam-06 just before this one
        if Item["cameraId"] == "cam-06" and g.table.row("cam-06") is None:
            g.table.items.append({"cameraId": "cam-06", "adapterId": "adapter-99", "unregisteredAt": "x",
                                  "registering": "theirs"})
        return put(Item, **kw)
    monkeypatch.setattr(g.table, "put_item", someone_else_first)
    status, body = g.post("/api/cameras", NEW)
    assert status == 201 and body["cameraId"] == "cam-07", body
    assert g.table.row("cam-06")["registering"] == "theirs"                     # theirs, untouched


def test_a_typed_id_that_is_taken_meanwhile_touches_nothing(gui, monkeypatch):
    g = _registering_gui(gui, "cam04")
    put = g.table.put_item

    def someone_else_first(Item, **kw):
        if g.table.row("cam-04") is None:
            g.table.items.append({"cameraId": "cam-04", "adapterId": "adapter-99", "unregisteredAt": "x",
                                  "registering": "theirs"})
        return put(Item, **kw)
    monkeypatch.setattr(g.table, "put_item", someone_else_first)
    status, out = g.post("/api/cameras", {**NEW, "cameraId": "cam-04"})
    assert status == 409 and "cam-04 is taken" in out["error"]
    assert g.mtx.writes() == [] and systemctl_writes(g.system) == []          # no path, no unit
    assert g.table.row("cam-04")["registering"] == "theirs"


def test_ids_run_from_cam_01_to_cam_9999():
    assert camera_registration.next_camera_id(FakeTable([])) == "cam-01"
    rows = [{"cameraId": f"cam-{n:02d}"} for n in (1, 2, 99)] + [{"cameraId": "cam-03-live"}, {"cameraId": "other"}]
    assert camera_registration.next_camera_id(FakeTable(rows, page_size=2)) == "cam-100"   # every page counts
    with pytest.raises(ValueError, match="cam-9999"):
        camera_registration.next_camera_id(FakeTable([{"cameraId": "cam-9999"}]))


def test_no_id_left_or_a_too_long_one_is_refused(gui):
    g = gui(rows=ROWS + [{"cameraId": "cam-9999", "mode": "passthrough", "adapterId": "adapter-99"}])
    status, out = g.post("/api/cameras", NEW)
    assert status == 409 and "no camera ID left" in out["error"]
    assert g.post("/api/cameras", {**NEW, "cameraId": "cam-10000"})[0] == 400
    assert g.mtx.writes() == [] and systemctl_writes(g.system) == []


def test_a_claim_is_skipped_by_every_reader_until_the_camera_is_ready():
    import camera_control
    table = FakeTable([])
    cid, token = camera_registration.claim_camera_id(table, "cam-01", allocated=True)
    claim = table.row(cid)
    assert claim["registering"] == token and claim["adapterId"] == "test-thing"
    assert camera_control.is_mine(claim) and not camera_control.acts_on(claim)  # unregistered: readers skip it
    camera_registration.abandon_claim(table, cid, "not this one")              # only its own claim
    assert "registering" in table.row(cid)
    camera_registration.abandon_claim(table, cid, token)
    assert table.row(cid)["abandonedAt"] and "registering" not in table.row(cid)
