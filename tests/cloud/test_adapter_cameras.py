"""adapter-cameras: adding and removing cameras from the web page (DEALER-PANEL-PLAN.md Phase 7.3, D16).

What is pinned: only a caller whose token names the adapter (vsz_adapters, or "*") reaches it --
anyone else is told it doesn't exist; each route sends the agent the right command on the adapter's
own topic, with the request's ID only -- the password is stored with the request and travels
nowhere, and no answer carries it back; a request that couldn't be sent is taken back out.
"""
import json

import pytest
from botocore.stub import ANY

from fakes import FakeTable, invoke
from loaders import load_lambda

lam = load_lambda("adapter_cameras")

HOLDER = {"sub": "11111111-1111-4111-8111-111111111111", "vsz_role": "customer", "vsz_adapters": "adapter-01,adapter-02"}
ADMIN = {"sub": "22222222-2222-4222-8222-222222222222", "vsz_role": "system_admin", "vsz_adapters": "*"}
DEALER = {"sub": "33333333-3333-4333-8333-333333333333", "vsz_role": "dealer"}
VIEWER = {"sub": "44444444-4444-4444-8444-444444444444", "vsz_role": "customer", "vsz_cameras": "cam-03@adapter-01"}
REQ = "55555555-5555-4555-8555-555555555555"
ADD = {"endpointRef": "urn:uuid:00000000-0000-0000-0000-0000000000d0", "xaddr": "http://192.0.2.20/onvif/device_service",
       "user": "admin", "password": "s3cret-pw", "displayName": " Yard "}


@pytest.fixture
def world(aws, monkeypatch):
    def install(cameras=(), discovery=(), requests=()):
        fake = aws(lam, cameras=cameras)
        fake.tables["adapter-discovery"] = FakeTable(discovery, hash_key="thing")
        fake.tables["camera-requests"] = FakeTable(requests, hash_key="requestId")
        monkeypatch.setattr(lam.uuid, "uuid4", lambda: REQ)
        return fake
    return install


def route(method, path, body=None, claims=HOLDER):
    original = lam.lambda_handler

    def with_path(event, context):
        event["pathParameters"] = {"proxy": path}
        return original(event, context)
    lam.lambda_handler = with_path
    try:
        return invoke(lam, body=body, method=method, **({"claims": claims} if claims else {}))
    finally:
        lam.lambda_handler = original


def expect_command(fake, thing, command):
    fake.stub("iot-data").add_response("publish", {}, {"topic": f"adapter/{thing}/cmd", "qos": 1,
                                                       "payload": json.dumps(command).encode()})


# --- who may -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("claims", [DEALER, VIEWER, {**HOLDER, "vsz_adapters": "adapter-010"}],
                         ids=["a dealer", "a user with a shared camera", "a holder of another adapter"])
def test_anyone_but_the_adapters_holder_is_told_it_doesnt_exist(world, claims):
    world()                                                      # nothing queued: nothing may be sent
    for method, path in [("POST", "adapter-01/discover"), ("GET", "adapter-01/discovery"), ("POST", "adapter-01/cameras")]:
        assert route(method, path, ADD, claims=claims) == (404, {"error": "no such adapter"})


def test_without_a_token_nothing_happens(world):
    world()
    assert route("POST", "adapter-01/discover", claims=None) == (401, {"error": "no signed-in user"})


@pytest.mark.parametrize("method, path, status", [
    ("GET", "adapter-01/discover", 405), ("POST", "adapter-1/discover", 404), ("POST", "cam-01/discover", 404),
    ("GET", f"adapter-01/cameras/requests/not-a-uuid", 404), ("POST", "adapter-01/cameras/cam-1/remove", 404),
])
def test_unknown_routes(world, method, path, status):
    world()
    assert route(method, path)[0] == status


# --- discovery -------------------------------------------------------------------------------------------

def test_a_scan_is_asked_of_the_adapter_and_its_result_read_back(world):
    fake = world(discovery=[{"thing": "adapter-02", "requestId": REQ, "at": "2026-11-07T21:00:00Z", "ttl": 9_999_999_999,
                             "devices": [{"endpointRef": "urn:x", "port": __import__("decimal").Decimal(80), "state": None}]}])
    expect_command(fake, "adapter-02", {"action": "discover", "requestId": REQ})
    assert route("POST", "adapter-02/discover") == (202, {"requestId": REQ})
    assert route("GET", "adapter-02/discovery") == (200, {"requestId": REQ, "at": "2026-11-07T21:00:00Z",
                                                          "devices": [{"endpointRef": "urn:x", "port": 80, "state": None}]})


def test_an_administrator_may_ask_any_adapter(world):
    fake = world()
    expect_command(fake, "adapter-07", {"action": "discover", "requestId": REQ})
    assert route("POST", "adapter-07/discover", claims=ADMIN)[0] == 202


def test_no_scan_or_an_expired_one_is_none(world):
    world(discovery=[{"thing": "adapter-02", "requestId": REQ, "ttl": 1, "devices": []}])
    assert route("GET", "adapter-01/discovery") == (404, {"error": "no scan yet: ask for one"})
    assert route("GET", "adapter-02/discovery")[0] == 404


# --- adding a camera ----------------------------------------------------------------------------------

def test_a_camera_request_keeps_the_password_in_its_table_and_sends_only_its_id(world):
    fake = world()
    expect_command(fake, "adapter-01", {"action": "register", "requestId": REQ})
    assert route("POST", "adapter-01/cameras", ADD) == (202, {"requestId": REQ})
    item = fake.tables["camera-requests"].row(REQ)
    assert {k: item[k] for k in ("thing", "xaddr", "user", "password", "endpointRef", "displayName", "status", "askedBy")} == {
        "thing": "adapter-01", "xaddr": ADD["xaddr"], "user": "admin", "password": "s3cret-pw",
        "endpointRef": ADD["endpointRef"], "displayName": "Yard", "status": "sent", "askedBy": HOLDER["sub"]}
    assert item["ttl"] - item["createdAt"] == 900


def test_a_request_that_couldnt_be_sent_is_taken_back(world):
    fake = world()
    fake.stub("iot-data").add_client_error("publish", "InternalFailureException")
    assert route("POST", "adapter-01/cameras", ADD) == (502, {"error": "the adapter couldn't be asked: nothing was kept"})
    assert fake.tables["camera-requests"].items == []


@pytest.mark.parametrize("change", [
    {"xaddr": "ftp://192.0.2.20/x"}, {"xaddr": None}, {"user": ""}, {"user": "u" * 65}, {"password": 5},
    {"displayName": "Yard; drop"}, {"endpointRef": "u" * 101},
])
def test_what_a_camera_request_may_hold(world, change):
    world()
    status, out = route("POST", "adapter-01/cameras", {**ADD, **change})
    assert status == 400, out


def test_a_requests_progress_shows_no_login(world):
    world(requests=[{"requestId": REQ, "thing": "adapter-01", "status": "done", "cameraId": "cam-10", "at": "x",
                     "user": "admin", "password": "left-over", "xaddr": "http://192.0.2.20/"}])
    status, out = route("GET", f"adapter-01/cameras/requests/{REQ}")
    assert (status, out) == (200, {"requestId": REQ, "status": "done", "cameraId": "cam-10", "at": "x"})
    assert route("GET", f"adapter-02/cameras/requests/{REQ}")[0] == 404              # another adapter's request


# --- removing a camera ---------------------------------------------------------------------------------

def test_removing_asks_the_cameras_own_adapter_to_unregister_it(world):
    fake = world(cameras=[{"cameraId": "cam-03", "adapterId": "adapter-01"}, {"cameraId": "cam-07", "adapterId": "adapter-09"},
                          {"cameraId": "cam-05", "adapterId": "adapter-01", "unregisteredAt": "x"}])
    expect_command(fake, "adapter-01", {"action": "unregister", "camera": "cam-03"})
    assert route("POST", "adapter-01/cameras/cam-03/remove") == (202, {"cameraId": "cam-03"})
    assert route("POST", "adapter-01/cameras/cam-07/remove")[0] == 404              # not on this adapter
    assert route("POST", "adapter-01/cameras/cam-05/remove") == (200, {"cameraId": "cam-05", "removed": True, "already": True})


def test_no_answer_or_error_carries_the_request(world, monkeypatch):
    world()
    monkeypatch.setattr(lam, "add_camera", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(ADD["password"])))
    monkeypatch.setattr(lam, "ROUTES", [(m, p, lam.add_camera if h.__name__ == "add_camera" else h) for m, p, h in lam.ROUTES])
    assert route("POST", "adapter-01/cameras", ADD) == (500, {"error": "RuntimeError"})
