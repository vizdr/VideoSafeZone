"""The fakes behave like what they stand in for, where the tests rely on it (PYTEST.md, Phase 2).

A fake that pages, orders or checks conditions wrongly would make the tests built on it pass for
the wrong reason, so these pin the DynamoDB and MediaMTX behaviour the fakes model.
"""
import pytest
import requests
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from fakes import FakeBoto3, FakeMediaMTX, FakeTable


def test_scan_pages_until_last_evaluated_key_is_gone():
    t = FakeTable([{"cameraId": f"cam-{n}"} for n in range(5)], page_size=2)
    seen, kw = [], {}
    while True:
        page = t.scan(**kw)
        seen += [i["cameraId"] for i in page["Items"]]
        if "LastEvaluatedKey" not in page:
            break
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    assert seen == [f"cam-{n}" for n in range(5)]
    assert t.scan()["LastEvaluatedKey"] == {"cameraId": "cam-1"}


def test_query_orders_by_the_range_key_and_continues_after_a_key():
    t = FakeTable([{"cameraId": "c", "startTs": ts} for ts in ("2", "1", "3")] + [{"cameraId": "d", "startTs": "9"}],
                  range_key="startTs")
    q = dict(KeyConditionExpression=Key("cameraId").eq("c"))
    assert [i["startTs"] for i in t.query(**q)["Items"]] == ["1", "2", "3"]
    assert [i["startTs"] for i in t.query(**q, ScanIndexForward=False)["Items"]] == ["3", "2", "1"]
    after = t.query(**q, ScanIndexForward=False, ExclusiveStartKey={"cameraId": "c", "startTs": "3"}, Limit=1)
    assert [i["startTs"] for i in after["Items"]] == ["2"] and "LastEvaluatedKey" in after
    assert t.query(**q, ProjectionExpression="startTs")["Items"][0] == {"startTs": "1"}


def test_update_sets_fields_and_keeps_the_rest():
    t = FakeTable([{"cameraId": "cam-02", "mode": "passthrough"}])
    t.update_item(Key={"cameraId": "cam-02"}, UpdateExpression="SET #h = :h, a = :a",
                  ExpressionAttributeNames={"#h": "onvifHost"},
                  ExpressionAttributeValues={":h": "192.0.2.1", ":a": True})
    assert t.row("cam-02") == {"cameraId": "cam-02", "mode": "passthrough", "onvifHost": "192.0.2.1", "a": True}


def test_update_creates_a_missing_item_as_dynamodb_does():
    t = FakeTable()
    t.update_item(Key={"cameraId": "cam-09"}, UpdateExpression="SET x = :x",
                  ExpressionAttributeValues={":x": 1})
    assert t.row("cam-09") == {"cameraId": "cam-09", "x": 1}


@pytest.mark.parametrize("condition, holds", [
    ("onvifHost = :expect", True),
    ("onvifHost = :other", False),
    ("attribute_not_exists(onvifEndpointRef)", True),
    ("attribute_exists(onvifEndpointRef)", False),
    ("onvifHost = :expect AND attribute_not_exists(onvifEndpointRef)", True),
    ("onvifHost = :other AND attribute_not_exists(onvifEndpointRef)", False),
])
def test_conditions(condition, holds):
    t = FakeTable([{"cameraId": "cam-02", "onvifHost": "192.0.2.12"}])
    call = dict(Key={"cameraId": "cam-02"}, UpdateExpression="SET y = :y", ConditionExpression=condition,
                ExpressionAttributeValues={":y": 1, ":expect": "192.0.2.12", ":other": "192.0.2.99"})
    if holds:
        t.update_item(**call)
        assert t.row("cam-02")["y"] == 1
    else:
        with pytest.raises(ClientError) as err:
            t.update_item(**call)
        assert err.value.response["Error"]["Code"] == "ConditionalCheckFailedException"
        assert "y" not in t.row("cam-02")


def test_what_the_fake_table_does_not_model_fails_loudly():
    t = FakeTable([{"cameraId": "cam-02"}])
    with pytest.raises(NotImplementedError):
        t.update_item(Key={"cameraId": "cam-02"}, UpdateExpression="ADD n :one",
                      ExpressionAttributeValues={":one": 1})
    with pytest.raises(NotImplementedError):
        t.scan(FilterExpression="x")


@pytest.mark.parametrize("expression, row", [
    ("SET a = :a REMOVE b, c", {"cameraId": "k", "a": 2, "d": 4}),
    ("REMOVE b", {"cameraId": "k", "a": 1, "c": 3, "d": 4}),
    ("SET a = :a REMOVE nothere", {"cameraId": "k", "a": 2, "b": 2, "c": 3, "d": 4}),  # a no-op
])
def test_set_and_remove(expression, row):
    t = FakeTable([{"cameraId": "k", "a": 1, "b": 2, "c": 3, "d": 4}])
    t.update_item(Key={"cameraId": "k"}, UpdateExpression=expression, ExpressionAttributeValues={":a": 2})
    assert t.row("k") == row


def test_scan_projection():
    t = FakeTable([{"cameraId": "k", "displayName": "Hall", "onvifPassword": "x"}])
    assert t.scan(ProjectionExpression="cameraId, displayName")["Items"] == [
        {"cameraId": "k", "displayName": "Hall"}]


def test_mediamtx_flowing_paths_grow_and_others_stall():
    from fakes import path_state
    m = FakeMediaMTX(state={"a": path_state(), "b": path_state()}, flowing={"a"})
    first, second = (
        {i["name"]: i["bytesReceived"] for i in m.get("http://127.0.0.1:9997/v3/paths/list").json()["items"]}
        for _ in range(2))
    assert second["a"] > first["a"] and second["b"] == first["b"]


def test_mediamtx_on_write_sees_each_write():
    seen = []
    m = FakeMediaMTX(on_write=lambda *w: seen.append(w))
    m.post("http://127.0.0.1:9997/v3/config/paths/add/x", json={"source": "s"})
    m.patch("http://127.0.0.1:9997/v3/config/paths/patch/x", json={"source": "t"})
    m.post("http://127.0.0.1:9997/v3/config/paths/add/x", json={})          # refused: not a write
    assert seen == [("POST", "x", {"source": "s"}), ("PATCH", "x", {"source": "t"})]


def test_fake_media2_applies_a_new_configuration():
    from fakes import FakeMedia2, encoder_options, encoder_xml
    import onvif_media2
    cam = FakeMedia2("192.0.2.13", [("P0", "E0", "/main", encoder_xml())], {"E0": encoder_options("H264", "H265")})
    cfg = onvif_media2.plan_config(cam.encoder_config("E0"), cam.encoder_options("E0"), "H265")
    cam.set_encoder_config(cfg)
    assert onvif_media2.summarize(cam.encoder_config("E0"))["encoding"] == "H265"
    assert onvif_media2.stream_profile(cam, "rtsp://admin:pw@192.0.2.13/main")["encoderToken"] == "E0"


def test_mediamtx_routes():
    m = FakeMediaMTX(conf={"cam02": {"source": "s"}})
    api = "http://127.0.0.1:9997"
    assert m.post(f"{api}/v3/config/paths/delete/cam02").status_code == 404     # as MediaMTX: #36
    assert m.patch(f"{api}/v3/config/paths/patch/cam09", json={}).status_code == 404
    assert m.post(f"{api}/v3/config/paths/add/cam02", json={}).status_code == 400
    assert m.delete(f"{api}/v3/config/paths/delete/cam02").status_code == 200
    assert m.get(f"{api}/v3/config/paths/get/cam02").status_code == 404


def test_mediamtx_snapshots_advance_then_repeat():
    m = FakeMediaMTX(snapshots=[[{"name": "a"}], [{"name": "b"}]])
    names = [m.get("http://127.0.0.1:9997/v3/paths/list").json()["items"][0]["name"] for _ in range(3)]
    assert names == ["a", "b", "b"]


def test_mediamtx_down():
    with pytest.raises(requests.ConnectionError):
        FakeMediaMTX(down=True).get("http://127.0.0.1:9997/v3/paths/list")


def test_an_unused_queued_answer_fails_verify():
    fake = FakeBoto3()
    fake.stub("s3").add_response("delete_object", {}, {"Bucket": "b", "Key": "k"})
    with pytest.raises(AssertionError):
        fake.verify()


def test_a_client_rebuilt_with_the_lambdas_arguments_keeps_the_queue():
    fake = FakeBoto3()
    fake.stub("s3").add_response("delete_object", {}, {"Bucket": "b", "Key": "k"})
    s3 = fake.client("s3", region_name="eu-central-1", endpoint_url="https://s3.eu-central-1.amazonaws.com")
    s3.delete_object(Bucket="b", Key="k")
    fake.verify()
    assert s3.meta.endpoint_url == "https://s3.eu-central-1.amazonaws.com"
