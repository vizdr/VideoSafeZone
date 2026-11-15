"""What the device role lets the Pi do, tried on the Pi (adapter/device_access.py, DEALER-PANEL-PLAN.md Phase 10.2f).

Every probe changes nothing whichever way it comes out. Before the narrowing the fake world answers
everything, which is the wide role: the "refused" lines fail, as they do on the Pi today.
"""
import pytest
from botocore.exceptions import ClientError

import device_access
from fakes import FakeTable

ROWS = [{"cameraId": "cam-02", "adapterId": "test-thing", "mode": "passthrough", "onvifPassword": "pw",
         "rtspUrl": "rtsp://u:pw@h/s"},
        {"cameraId": "cam-01", "adapterId": "test-thing", "mode": "transcode"}]


def denied(op):
    def call(*a, **k):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}}, op)
    return call


def world(registry, monkeypatch, narrowed):
    import config
    monkeypatch.setattr(config, "ADOPT_UNSTAMPED_ROWS", False)
    table = registry(ROWS)
    table.session.tables["clips"] = clips = FakeTable([], range_key="startTs")
    table.session.tables["camera-requests"] = requests = FakeTable([], hash_key="requestId")
    kv = table.session.stub("kinesisvideo")
    for cid in ("cam-01", "cam-02"):
        kv.add_response("describe_stream", {"StreamInfo": {"StreamName": cid}}, {"StreamName": cid})
    table.session.stub("s3").add_response(
        "put_object", {}, {"Bucket": "test-evidence-bucket", "Key": "thumbs/test-thing/.device-access-check",
                           "Body": b"", "CacheControl": "no-cache"})
    real_query = table.query                       # own-rows (10.2c): LeadingKeys, in either role

    def query(**kw):
        if kw["KeyConditionExpression"].get_expression()["values"][1] != config.THING_NAME:
            return denied("Query")()
        return real_query(**kw)
    monkeypatch.setattr(table, "query", query)
    if narrowed:
        monkeypatch.setattr(table, "scan", denied("Scan"))
        monkeypatch.setattr(table, "get_item", denied("GetItem"))
        monkeypatch.setattr(clips, "put_item", denied("PutItem"))
        monkeypatch.setattr(requests, "get_item", denied("GetItem"))
    import camera_secrets
    camera_secrets.mirror(now=1000)
    return table


def test_the_narrowed_role_passes(registry, monkeypatch, capsys):
    world(registry, monkeypatch, narrowed=True)
    assert device_access.check() == 0
    out = capsys.readouterr().out
    assert "PASS  the whole camera table (Scan): refused" in out
    assert "PASS  its stream cam-02 (DescribeStream, by its tag): allowed" in out
    assert "SKIP  the index agrees with the table" in out and "pw" not in out


def test_the_wide_role_fails_where_it_still_allows_too_much(registry, monkeypatch, capsys):
    world(registry, monkeypatch, narrowed=False)
    assert device_access.check() == 4
    out = capsys.readouterr().out
    assert "FAIL  the whole camera table (Scan): allowed" in out
    assert "FAIL  a clip row written directly (PutItem, a condition that never holds): allowed" in out
    assert out.count("FAIL") == 4


@pytest.mark.parametrize("code, got", [("AccessDeniedException", "refused"), ("AccessDenied", "refused"),
                                       ("ConditionalCheckFailedException", "allowed"),
                                       ("ResourceNotFoundException", "allowed"), ("ThrottlingException", "ThrottlingException")])
def test_what_a_probe_proves(code, got):
    def call():
        raise ClientError({"Error": {"Code": code, "Message": "x"}}, "Op")
    assert device_access.probe(call) == got
    assert device_access.probe(lambda: None) == "allowed"
