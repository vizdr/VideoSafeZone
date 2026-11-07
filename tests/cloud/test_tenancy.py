"""The tenant check in the twelve camera and clip Lambdas (DEALER-PANEL-PLAN.md D4, Phase 3.2).

Every route against every kind of caller, with TENANCY=enforce:
  - a camera the caller may not see gets exactly the answer an unknown camera gets, so the API
    doesn't confirm other tenants' camera IDs;
  - a user with a camera shared may watch it (list, live, recordings, clips) but not change it:
    403 "view only" for Start/Stop, IR, every setting, recording, deleting or re-tiering a clip;
  - holder, Super Admin (vsz_adapters) and a system administrator ("*") pass;
  - dealers, owners, an old pool's token and no token at all reach nothing;
  - a row without adapterId (not stamped yet) is an administrator's alone.
An allowed call goes on past the check and fails at its next AWS call (nothing is queued), which
is all these tests need to know: it wasn't refused. TENANCY=report refuses nothing but logs.
"""
import json

import pytest

from fakes import invoke
from loaders import load_lambda

L = {name: load_lambda(name) for name in (
    "list_cameras", "get_hls_url", "list_clips", "play_clip", "delete_clip", "set_clip_tier",
    "record_clip", "publish_cmd", "set_camera_audio", "set_camera_mode", "set_camera_outage_buffer",
    "set_camera_settings")}

CAMERAS = [
    {"cameraId": "cam-03", "mode": "passthrough", "onvifHost": "192.0.2.13", "adapterId": "adapter-01",
     "audioCapable": True, "detectionModes": ["motion"]},
    {"cameraId": "cam-04", "mode": "passthrough", "onvifHost": "192.0.2.14", "adapterId": "adapter-01"},
    {"cameraId": "cam-05", "mode": "passthrough", "onvifHost": "192.0.2.15"},       # not stamped yet
    {"cameraId": "cam-07", "mode": "passthrough", "onvifHost": "192.0.2.17", "adapterId": "adapter-0002"},
]
ORG = "6f1c2a0e-0000-4000-8000-000000000001"
CALLERS = {
    "holder": {"sub": "s-holder", "vsz_role": "customer", "vsz_adapters": "adapter-01"},
    "super admin": {"sub": "s-super", "vsz_role": "customer", "vsz_adapters": "adapter-01"},
    "user (cam-03 shared)": {"sub": "s-user", "vsz_role": "customer", "vsz_cameras": "cam-03@adapter-01"},
    "user (cam-04 shared)": {"sub": "s-other", "vsz_role": "customer", "vsz_cameras": "cam-04@adapter-01"},
    "another customer": {"sub": "s-cust2", "vsz_role": "customer", "vsz_adapters": "adapter-0002"},
    "dealer": {"sub": "s-dealer", "vsz_role": "dealer", "vsz_org": ORG},
    "owner": {"sub": "s-owner", "vsz_role": "system_owner", "vsz_org": ORG},
    "administrator": {"sub": "s-admin", "vsz_role": "system_admin", "vsz_org": ORG, "vsz_adapters": "*"},
    "old pool's token": {"sub": "s-old", "cognito:username": "demo-viewer"},
    "no token": None,
}
FULL = {"holder", "super admin", "administrator"}
VIEWER = "user (cam-03 shared)"
VIEW_ONLY = (403, {"error": "this camera is shared with you to view, not to change"})
KEY = "clips/cam-03/2026-11-01T10-00-00Z.mp4"
TS = "2026-11-01T10:00:00+00:00"

# route: (lambda, need, call(camera) -> invoke kwargs, the answer for an unknown camera)
ROUTES = {
    "GET /hls": ("get_hls_url", "view", lambda c: {"query": {"stream": c}},
                 lambda c: (400, {"error": f"unknown stream '{c}'"})),
    "GET /clips": ("list_clips", "view", lambda c: {"query": {"camera": c}},
                   lambda c: (400, {"error": f"unknown camera '{c}'"})),
    "POST /clips/play": ("play_clip", "view", lambda c: {"body": {"s3Key": f"clips/{c}/x.mp4"}},
                         lambda c: (404, {"error": "no such clip"})),
    "POST /cmd live": ("publish_cmd", "view", lambda c: {"body": {"action": "live", "camera": c}},
                       lambda c: (400, {"error": f"unknown camera '{c}'"})),
    "POST /cmd live-stop": ("publish_cmd", "view", lambda c: {"body": {"action": "live-stop", "camera": c}},
                            lambda c: (400, {"error": f"unknown camera '{c}'"})),
    "POST /cmd start": ("publish_cmd", "control", lambda c: {"body": {"action": "start", "camera": c}},
                        lambda c: (400, {"error": f"unknown camera '{c}'"})),
    "POST /cmd ir": ("publish_cmd", "control", lambda c: {"body": {"action": "ir", "camera": c, "mode": "AUTO"}},
                     lambda c: (400, {"error": f"unknown camera '{c}'"})),
    "POST /clips/record": ("record_clip", "control",
                           lambda c: {"body": {"stream": c, "startTs": TS, "endTs": "2026-11-01T10:00:30+00:00"}},
                           lambda c: (400, {"error": f"unknown stream '{c}'"})),
    "POST /clips/delete": ("delete_clip", "control", lambda c: {"body": {"s3Key": f"clips/{c}/x.mp4", "startTs": TS}},
                           lambda c: (404, {"error": "no such clip"})),
    "POST /clips/tier": ("set_clip_tier", "control", lambda c: {"body": {"s3Key": f"clips/{c}/x.mp4", "tier": "STANDARD_IA"}},
                         lambda c: (404, {"error": "no such clip"})),
    "POST /cameras/audio": ("set_camera_audio", "control", lambda c: {"body": {"cameraId": c, "audioEnabled": True}},
                            lambda c: (400, {"error": f"unknown camera '{c}'"})),
    "POST /cameras/mode": ("set_camera_mode", "control", lambda c: {"body": {"cameraId": c, "recordingMode": "manual"}},
                           lambda c: (400, {"error": f"unknown camera '{c}'"})),
    "POST /cameras/outage-buffer": ("set_camera_outage_buffer", "control",
                                    lambda c: {"body": {"cameraId": c, "outageBufferSec": 0}},
                                    lambda c: (400, {"error": f"unknown camera '{c}'"})),
    "POST /cameras/settings": ("set_camera_settings", "control", lambda c: {"body": {"cameraId": c, "tags": ["hall"]}},
                               lambda c: (400, {"error": f"unknown camera '{c}'"})),
}


@pytest.fixture
def enforce(monkeypatch):
    monkeypatch.setenv("TENANCY", "enforce")


def call(aws, route, camera, who):
    name, need, kwargs, unknown = ROUTES[route]
    aws(L[name], CAMERAS)
    return invoke(L[name], claims=CALLERS[who], **kwargs(camera)), unknown(camera), need


def expected(who, need, camera):
    if who == "administrator":
        return "allowed"
    if camera == "cam-03" and who in FULL:
        return "allowed"
    if camera == "cam-03" and who == VIEWER:
        return "allowed" if need == "view" else "view-only"
    return "hidden"


@pytest.mark.parametrize("who", list(CALLERS))
@pytest.mark.parametrize("route", list(ROUTES))
def test_every_route_against_every_caller(aws, enforce, route, who):
    result, unknown, need = call(aws, route, "cam-03", who)
    want = expected(who, need, "cam-03")
    if want == "hidden":
        assert result == unknown, f"{who} on {route}: {result}"
    elif want == "view-only":
        assert result == VIEW_ONLY, f"{who} on {route}: {result}"
    else:
        assert result not in (unknown, VIEW_ONLY), f"{who} on {route} was refused: {result}"


@pytest.mark.parametrize("route", list(ROUTES))
@pytest.mark.parametrize("who", ["holder", VIEWER, "another customer"])
def test_a_camera_not_yet_stamped_is_the_administrators_alone(aws, enforce, route, who):
    result, unknown, _ = call(aws, route, "cam-05", who)
    assert result == unknown
    result, unknown, _ = call(aws, route, "cam-05", "administrator")
    assert result not in (unknown, VIEW_ONLY)


def test_the_camera_list_holds_only_what_the_caller_reaches(aws, enforce):
    aws(L["list_cameras"], CAMERAS)

    def listed(who):
        status, body = invoke(L["list_cameras"], claims=CALLERS[who])
        assert status == 200
        return {c["id"]: c["access"] for c in body["cameras"]}
    assert listed("holder") == listed("super admin") == {"cam-03": "control", "cam-04": "control"}
    assert listed(VIEWER) == {"cam-03": "view"}
    assert listed("another customer") == {"cam-07": "control"}
    assert listed("administrator") == {c["cameraId"]: "control" for c in CAMERAS}
    for nobody in ("dealer", "owner", "old pool's token", "no token"):
        assert listed(nobody) == {}


def test_each_refusal_is_logged(aws, enforce, capsys):
    call(aws, "POST /cmd start", "cam-03", VIEWER)
    call(aws, "GET /hls", "cam-07", "holder")
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith('{"tenancy"')]
    assert [(l["tenancy"], l["verdict"], l["need"], l["camera"], l["sub"]) for l in lines] == [
        ("refused", "view-only", "control", "cam-03", "s-user"),
        ("refused", "hidden", "view", "cam-07", "s-holder")]


def test_report_mode_refuses_nothing_but_says_what_it_would(aws, monkeypatch, capsys):
    monkeypatch.setenv("TENANCY", "report")
    result, unknown, _ = call(aws, "POST /cameras/settings", "cam-07", "old pool's token")
    assert result not in (unknown, VIEW_ONLY)
    line = json.loads(next(l for l in capsys.readouterr().out.splitlines() if l.startswith('{"tenancy"')))
    assert (line["tenancy"], line["verdict"], line["role"]) == ("would-refuse", "hidden", None)


def test_unset_means_enforce(aws, monkeypatch):
    monkeypatch.delenv("TENANCY", raising=False)
    result, unknown, _ = call(aws, "GET /hls", "cam-03", "no token")
    assert result == unknown
