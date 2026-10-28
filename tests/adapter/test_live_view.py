"""live_view: live view on the sub stream while the main stream is recorded (PYTEST.md, Phase 3).

Only a camera recording its main stream, and having a sub stream, gets a separate live view: a
registry field (liveRtspUrl), a MediaMTX path (cam03_live) and a KVS stream (cam-03-live).
Setting it up writes the registry first, then MediaMTX; an unchanged camera costs one KVS
describe and one MediaMTX read, and no write anywhere.
"""
import boto3
import pytest
from botocore.stub import Stubber

import live_view
import mediamtx_api
from fakes import FakeMediaMTX, FakeTable, REGION

MAIN = "rtsp://admin:pw@192.0.2.13:554/main"
SUB_AS_REPORTED = "rtsp://192.0.2.13:554/sub"
SUB = "rtsp://admin:pw@192.0.2.13:554/sub"
ARN = "arn:aws:kinesisvideo:eu-central-1:000000000000:stream/cam-03-live/1"


def _streams(*names):
    paths = {"main": "/main", "sub": "/sub", "sub2": "/third"}
    return [{"name": n, "streamUri": f"rtsp://192.0.2.13:554{paths[n]}"} for n in names]


@pytest.mark.parametrize("streams, recorded, uri", [
    (_streams("main", "sub"), MAIN, SUB),                 # credentials carried over to the sub stream
    (_streams("main", "sub"), SUB, None),                 # recording the sub stream: no separate view
    (_streams("main"), MAIN, None),                       # no sub stream
    (_streams("main", "sub"), "rtsp://admin:pw@192.0.2.13:554/gone", None),
    (_streams("main", "sub", "sub2"), MAIN, SUB),         # "sub" by name, not a third stream
])
def test_live_uri(streams, recorded, uri):
    assert live_view.live_uri(streams, recorded) == uri


@pytest.fixture
def world(monkeypatch, fake_run):
    def install(row, conf=None, kvs_exists=True):
        table = FakeTable([row])
        mtx = FakeMediaMTX(conf=conf or {})
        monkeypatch.setattr(mediamtx_api, "requests", mtx)
        kv = boto3.client("kinesisvideo", region_name=REGION)
        stub = Stubber(kv)
        if kvs_exists:
            stub.add_response("describe_stream", {"StreamInfo": {"StreamARN": ARN}}, {"StreamName": "cam-03-live"})
        else:
            stub.add_client_error("describe_stream", "ResourceNotFoundException", http_status_code=404)
            stub.add_response("create_stream", {"StreamARN": ARN},
                              {"StreamName": "cam-03-live", "DataRetentionInHours": 1, "MediaType": "video/h264"})
        stub.activate()
        system = fake_run(live_view)
        return table, mtx, kv, stub, system
    return install


CAM03 = {"cameraId": "cam-03", "rtspUrl": MAIN}


def test_set_up_from_nothing(world):
    table, mtx, kv, stub, _ = world(CAM03, kvs_exists=False)
    out = live_view.apply("cam-03", CAM03, SUB, table, kv)
    assert out == {"live": "sub", "path": "cam03_live", "kvsStream": "cam-03-live", "kvsStreamArn": ARN}
    assert table.row("cam-03")["liveRtspUrl"] == SUB
    assert mtx.writes() == [("POST", "/v3/config/paths/add/cam03_live", {"source": SUB, "rtspTransport": "tcp"})]
    stub.assert_no_pending_responses()


def test_an_unchanged_camera_writes_nothing(world):
    row = {**CAM03, "liveRtspUrl": SUB}
    table, mtx, kv, stub, _ = world(row, conf={"cam03_live": {"source": SUB, "rtspTransport": "tcp"}})
    live_view.apply("cam-03", row, SUB, table, kv)
    assert table.writes == [] and mtx.writes() == []
    stub.assert_no_pending_responses()


def test_a_moved_sub_stream_patches_only_the_live_path(world):
    row = {**CAM03, "liveRtspUrl": "rtsp://admin:pw@192.0.2.99:554/sub"}
    table, mtx, kv, _, _ = world(row, conf={"cam03_live": {"source": row["liveRtspUrl"]}})
    live_view.apply("cam-03", row, SUB, table, kv)
    assert table.row("cam-03")["liveRtspUrl"] == SUB
    assert mtx.writes() == [("PATCH", "/v3/config/paths/patch/cam03_live", {"source": SUB, "rtspTransport": "tcp"})]


def test_removing_stops_the_live_producer_and_keeps_the_kvs_stream(world):
    row = {**CAM03, "liveRtspUrl": SUB}
    table, mtx, kv, stub, system = world(row, conf={"cam03_live": {"source": SUB}})
    stub.deactivate()                       # no KVS call at all: the stream stays, idle, for later
    out = live_view.apply("cam-03", row, None, table, kv)
    assert out == {"live": "recorded stream", "path": "cam03"}
    assert system.calls == [["sudo", "systemctl", "stop", "kvs-live@cam03.service"]]
    assert mtx.writes() == [("DELETE", "/v3/config/paths/delete/cam03_live", None)]
    assert "liveRtspUrl" not in table.row("cam-03")


def test_removing_when_there_is_none_writes_no_registry(world):
    table, mtx, kv, stub, _ = world(CAM03)
    stub.deactivate()
    live_view.apply("cam-03", CAM03, None, table, kv)
    assert table.writes == []
