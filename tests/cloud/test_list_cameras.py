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
        "detectionTests": None, "webUrl": None, "liveView": "sub", "liveVideoCodec": None,
        # WEB-PANEL-PLAN.md §5.1, all absent from this row; no ceiling recorded yet: no choices.
        "tags": [], "schedule": None, "recordStream": None, "recordSize": None, "recordFps": None,
        "recordQuality": None, "recordQualityActive": None, "recordQualityOptions": [],
        "thumbnailUrl": None, "thumbnailAt": None,      # no bucket configured in this test: none
        "access": "control",            # DEALER-PANEL-PLAN.md D4; TENANCY=report here, so as before
        "adapterId": None}              # the row predates it: an administrator's alone (D4)
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


def test_the_web_panels_camera_settings_are_returned(aws):
    # WEB-PANEL-PLAN.md §5.1: tags, schedule, the recorded-quality ceiling and choice.
    schedule = {"tz": "Europe/Berlin", "entries": [{"days": ["mon"], "at": "18:00", "action": "start"}]}
    rows = [{**ROWS[0], "tags": ["outdoor"], "schedule": schedule, "recordStream": "main",
             "recordSize": "2560x1440", "recordFps": Decimal("15"), "recordQuality": {"stream": "sub"}},
            ROWS[1]]
    cams = {c["id"]: c for c in _list(aws, rows)}
    c2 = cams["cam-02"]
    assert (c2["tags"], c2["schedule"], c2["recordStream"], c2["recordSize"], c2["recordFps"]) == \
        (["outdoor"], schedule, "main", "2560x1440", 15)
    assert c2["recordQuality"] == {"stream": "sub"}
    assert c2["recordQualityOptions"] == ["main", "sub"]
    c1 = cams["cam-01"]
    assert (c1["tags"], c1["schedule"], c1["recordQualityOptions"]) == ([], None, [])


def test_quality_options_follow_the_ceiling():
    opts = list_cameras.record_quality_options
    assert opts({"onvifHost": "h", "recordStream": "main", "liveRtspUrl": "rtsp://x/sub"}) == ["main", "sub"]
    assert opts({"onvifHost": "h", "recordStream": "main"}) == ["main"]          # no sub stream
    assert opts({"onvifHost": "h", "recordStream": "sub"}) == ["sub"]            # nothing below sub
    assert opts({"onvifHost": "h"}) == []                                        # ceiling not known yet
    assert opts({"mode": "transcode", "recordStream": "main"}) == []             # USB: fixed on the Pi



def test_thumbnails_are_linked_when_the_pi_has_uploaded_one(aws, monkeypatch):
    # WEB-PANEL-PLAN.md Phase 5.3: a HEAD per camera; a missing key (403 without ListBucket) is no thumbnail.
    from datetime import datetime, timezone
    monkeypatch.setattr(list_cameras, "BUCKET", "evidence-bucket")
    fake = aws(list_cameras, ROWS)
    s3 = fake.stub("s3")
    s3.add_client_error("head_object", "403", "Forbidden", expected_params={"Bucket": "evidence-bucket", "Key": "thumbs/cam-01.jpg"})
    s3.add_response("head_object", {"LastModified": datetime(2026, 11, 1, 10, 20, tzinfo=timezone.utc)},
                    {"Bucket": "evidence-bucket", "Key": "thumbs/cam-02.jpg"})
    code, out = invoke(list_cameras)
    cams = {c["id"]: c for c in out["cameras"]}
    assert (cams["cam-01"]["thumbnailUrl"], cams["cam-01"]["thumbnailAt"]) == (None, None)
    url = cams["cam-02"]["thumbnailUrl"]
    assert url.startswith("https://s3.eu-central-1.amazonaws.com/evidence-bucket/thumbs/cam-02.jpg?")
    assert "X-Amz-Expires=3600" in url and cams["cam-02"]["thumbnailAt"] == "2026-11-01T10:20:00Z"
