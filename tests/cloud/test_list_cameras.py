"""list-cameras: what the cloud page learns about each camera (PYTEST.md, Phase 2).

Part of the data contract with VMS: it never returns `rtspUrl` or `liveRtspUrl`, which carry
camera logins (CLAUDE.md, "The camera registry").
"""
from decimal import Decimal

from fakes import invoke
from loaders import load_lambda

list_cameras = load_lambda("list_cameras")

SECRET = "s3cret-pw-7781"
ROWS = [
    {"cameraId": "cam-02", "mode": "passthrough", "onvifHost": "192.0.2.12",
     "onvifUser": "admin", "onvifPassword": SECRET,
     "rtspUrl": f"rtsp://admin:{SECRET}@192.0.2.12:554/main",
     "liveRtspUrl": f"rtsp://admin:{SECRET}@192.0.2.12:554/sub",
     "hasIrControl": True, "audioCapable": True, "audioEnabled": False,
     "outageBufferSec": Decimal("300"), "recordingMode": "human",
     "videoCodecCaps": {"h264": "camera", "h265": "camera"}, "videoCodec": "h265",
     "detectionModes": ["motion", "human"],
     "detectionTests": {"human": {"events": Decimal("3"), "at": "2026-10-25T12:00:00Z"}}},
    {"cameraId": "cam-01", "mode": "transcode",
     "videoCodecCaps": {"h264": "pi-hw", "h265": "none"}, "videoCodecDefault": "h264"},
    {"cameraId": "cam-05", "mode": "passthrough", "unregisteredAt": "2026-10-25T12:00:00Z"},
]


def _list(aws, rows=ROWS, page_size=None):
    aws(list_cameras, rows, page_size=page_size)
    code, out = invoke(list_cameras)
    assert code == 200, out
    return out["cameras"]


def test_never_returns_a_credential(aws, monkeypatch):
    aws(list_cameras, ROWS)
    resp = list_cameras.lambda_handler({}, None)
    assert SECRET not in resp["body"]
    for cam in _list(aws):
        assert not {"rtspUrl", "liveRtspUrl", "onvifPassword", "onvifUser"} & set(cam)


def test_unregistered_cameras_are_left_out(aws):
    assert [c["id"] for c in _list(aws)] == ["cam-01", "cam-02"]


def test_ff82_registry_numbers_become_json(aws):
    # DynamoDB returns every number as a Decimal; one nested Decimal made the whole list a 500.
    cam = {c["id"]: c for c in _list(aws)}["cam-02"]
    assert cam["outageBufferSec"] == 300
    assert cam["detectionTests"]["human"]["events"] == 3


def test_every_page_of_the_scan_is_read(aws):
    # A single Scan stops at 1 MB; a camera on the second page must still be listed.
    rows = [{"cameraId": f"cam-{n:02d}", "mode": "passthrough"} for n in range(1, 8)]
    assert [c["id"] for c in _list(aws, rows, page_size=2)] == [r["cameraId"] for r in rows]


def test_fields_the_page_uses(aws):
    cams = {c["id"]: c for c in _list(aws)}
    assert cams["cam-02"] | {"detectionTests": None} == {
        "id": "cam-02", "mode": "passthrough", "hasIrControl": True, "recordingMode": "human",
        "supportsDetection": True, "audioCapable": True, "audioEnabled": False,
        "outageBufferSec": 300, "videoCodec": "h265", "videoEncode": "camera",
        "videoCodecActive": None, "displayName": None, "detectionModes": ["motion", "human"],
        "detectionTests": None, "webUrl": None, "liveView": "sub", "liveVideoCodec": None}
    assert cams["cam-01"]["recordingMode"] == "manual"          # the default
    assert cams["cam-01"]["supportsDetection"] is False         # no ONVIF: manual only
    assert (cams["cam-01"]["videoCodec"], cams["cam-01"]["videoEncode"]) == ("h264", "pi-hw")
    assert (cams["cam-01"]["liveView"], cams["cam-01"]["liveVideoCodec"]) == ("recorded", "h264")


def test_a_registry_failure_is_a_500_with_cors(aws, monkeypatch):
    fake = aws(list_cameras, ROWS)

    def broken(**kw):
        raise RuntimeError("ProvisionedThroughputExceededException")
    monkeypatch.setattr(fake.tables["cameras"], "scan", broken)
    assert invoke(list_cameras)[0] == 500
