"""The Pi's half of the registry route (adapter/registry_client.py, DEALER-PANEL-PLAN.md Phase 10.2b): each
call signed for execute-api with the device credentials, to /registry/<this Thing>/...; the route's
refusals passed back as they came; and the check's verdicts. The cloud's half is
tests/cloud/test_vsz_registry.py; IAM's -- another Thing's path refused -- is tried on the Pi by `check`.
"""
import io
import json
import urllib.error

import boto3
import pytest

import registry_client as rc

SESSION = boto3.Session(aws_access_key_id="ASIAEXAMPLEKEY", aws_secret_access_key="secret",
                        aws_session_token="device-token", region_name="eu-central-1")


class _Answer(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def sent(monkeypatch):
    """The requests urlopen got; answers from `replies` (an exception, or a JSON body for 200)."""
    requests, replies = [], []

    def urlopen(request):
        requests.append(request)
        reply = replies.pop(0) if replies else {"ok": True}
        if isinstance(reply, Exception):
            raise reply
        return _Answer(json.dumps(reply).encode())
    monkeypatch.setattr(rc, "_open", urlopen)
    return requests, replies


def test_a_call_is_signed_for_execute_api_with_the_device_credentials(sent):
    requests, _ = sent
    assert rc.call("POST", "cameras/cam-02", {"set": {"displayName": "Gate"}}, session=SESSION) == (200, {"ok": True})
    [req] = requests
    assert req.full_url == "https://registry.invalid/prod/registry/test-thing/cameras/cam-02"
    assert (req.get_method(), json.loads(req.data)) == ("POST", {"set": {"displayName": "Gate"}})
    headers = {k.lower(): v for k, v in req.header_items()}
    assert headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=ASIAEXAMPLEKEY/")
    assert "/eu-central-1/execute-api/aws4_request" in headers["authorization"]
    assert headers["x-amz-security-token"] == "device-token" and headers["content-type"] == "application/json"


def test_a_get_has_no_body_and_another_things_path_is_only_for_the_check(sent):
    requests, _ = sent
    rc.call("GET", "ping", thing="adapter-99", session=SESSION)
    assert requests[0].full_url.endswith("/registry/adapter-99/ping") and requests[0].data is None


def test_a_refusal_comes_back_as_it_came(sent):
    _, replies = sent
    replies.append(urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(b'{"error": "no camera cam-03 on test-thing"}')))
    replies.append(urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b"<html>denied</html>")))
    assert rc.call("POST", "cameras/cam-03", {"set": {"a": 1}}, session=SESSION) == \
        (404, {"error": "no camera cam-03 on test-thing"})
    assert rc.call("GET", "ping", session=SESSION) == (403, {"message": "<html>denied</html>"})


def test_the_route_is_needed_only_when_called(monkeypatch):
    import config
    monkeypatch.delenv("REGISTRY_URL", raising=False)
    monkeypatch.setattr(config, "_FILE", {k: v for k, v in config._FILE.items() if k != "REGISTRY_URL"})
    with pytest.raises(config.ConfigError):
        rc.call("GET", "ping", session=SESSION)


def _answers(monkeypatch, other=403, write=200):
    calls = []

    def call(method, path, body=None, thing=None, session=None):
        calls.append((method, path, body, thing))
        if path == "ping":
            return (200, {"thing": "test-thing"}) if thing is None else (other, {"message": "Forbidden"})
        return write, {"updated": path.split("/")[-1]}
    monkeypatch.setattr(rc, "call", call)
    return calls


def test_the_check_passes_when_the_route_answers_iam_refuses_another_thing_and_a_write_goes_through(monkeypatch, capsys):
    calls = _answers(monkeypatch)
    assert rc.check("cam-02", session=SESSION) == 0
    out = capsys.readouterr().out
    assert out.count("PASS") == 3 and "FAIL" not in out
    assert calls[1][3] == "adapter-99"
    assert calls[2][:2] == ("POST", "cameras/cam-02") and list(calls[2][2]["set"]) == ["registryCheckedAt"]


def test_the_check_fails_when_another_things_path_is_let_through(monkeypatch, capsys):
    _answers(monkeypatch, other=200, write=404)
    assert rc.check("cam-02", session=SESSION) == 2
    assert capsys.readouterr().out.count("FAIL") == 2


def test_the_check_writes_to_the_first_camera_this_pi_acts_on(registry, monkeypatch):
    table = registry([
        {"cameraId": "cam-03", "mode": "passthrough", "adapterId": "adapter-07"},          # another's
        {"cameraId": "cam-01", "mode": "transcode", "unregisteredAt": "2026-11-01T00:00:00Z"},
        {"cameraId": "cam-04", "mode": "passthrough", "adapterId": "test-thing"},
        {"cameraId": "cam01", "videoCodecActive": "h264"},                                   # not a camera
    ])
    calls = _answers(monkeypatch)
    assert rc.check(session=table.session) == 0
    assert calls[2][1] == "cameras/cam-04"


# --- the typed calls, through the real vsz-registry on the test's table (tests/fakes.py) ---------------

from decimal import Decimal  # noqa: E402

from fakes import FakeTable  # noqa: E402


def test_update_writes_this_pis_row_and_names_each_refusal(registry):
    table = registry([{"cameraId": "cam-02", "mode": "passthrough", "adapterId": "test-thing", "onvifHost": "192.0.2.12"},
                      {"cameraId": "cam-03", "mode": "passthrough", "adapterId": "adapter-07"}])
    rc.update("cam-02", set={"recordFps": Decimal("10"), "ratio": Decimal("1.5")}, expect={"onvifHost": "192.0.2.12"})
    assert (table.row("cam-02")["recordFps"], table.row("cam-02")["ratio"]) == (10, Decimal("1.5"))
    with pytest.raises(rc.ConditionFailed):
        rc.update("cam-02", set={"onvifHost": "192.0.2.77"}, expect={"onvifHost": "192.0.2.99"})
    with pytest.raises(rc.NotMine):
        rc.update("cam-03", set={"displayName": "Mine"})
    with pytest.raises(rc.RegistryError) as refused:
        rc.update("cam-02", set={"adapterId": "adapter-07"})
    assert refused.value.status == 403 and table.row("cam-02")["adapterId"] == "test-thing"
    assert [c[1] for c in table.route.calls] == ["cameras/cam-02", "cameras/cam-02", "cameras/cam-03", "cameras/cam-02"]


def test_claim_adopt_and_the_scans_mark(registry):
    table = registry([{"cameraId": "cam-04", "mode": "passthrough"},
                      {"cameraId": "cam-05", "mode": "passthrough", "adapterId": "adapter-07", "onvifEndpointRef": "urn:x"}])
    camera_id, token = rc.claim()
    assert camera_id == "cam-06" and table.row("cam-06")["registering"] == token
    with pytest.raises(rc.ConditionFailed):
        rc.claim("cam-05")
    assert rc.adopt("cam-04") is True and table.row("cam-04")["adapterId"] == "test-thing"
    assert rc.adopt("cam-05") is False
    assert rc.elsewhere(["urn:x", "urn:y"]) == {"urn:x"} and rc.elsewhere([]) == set()


def test_a_clip_row_through_the_route(registry):
    table = registry([{"cameraId": "cam-02", "mode": "passthrough", "adapterId": "test-thing"}])
    table.session.tables["clips"] = clips = FakeTable([], range_key="startTs")
    item = {"cameraId": "cam-02", "startTs": "2026-11-15T09:00:00+00:00", "s3Key": "clips/cam-02/test-thing/2026/11/15/090000-outage.mp4",
            "labels": ["outage"], "durationSec": 60}
    rc.add_clip(item)
    assert clips.row("cam-02", item["startTs"])["durationSec"] == 60
    with pytest.raises(rc.ConditionFailed):
        rc.add_clip(item)


def test_a_route_that_cant_be_reached_is_a_registry_error_without_a_status(monkeypatch):
    def offline(request):
        raise urllib.error.URLError("Temporary failure in name resolution")
    monkeypatch.setattr(rc, "_open", offline)
    with pytest.raises(rc.RegistryError) as e:
        rc.update("cam-02", set={"a": 1}, session=SESSION)
    assert e.value.status is None and "can't be reached" in e.value.message


def test_only_what_json_and_dynamodb_share_goes_to_the_route(sent):
    with pytest.raises(TypeError):
        rc.call("POST", "cameras/cam-02", {"set": {"tags": {"a", "b"}}}, session=SESSION)
