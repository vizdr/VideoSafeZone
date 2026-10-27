import boto3, os, json
from decimal import Decimal

REGION = os.environ["AWS_REGION"]
CORS = {"Access-Control-Allow-Origin": "*"}

def video_codec(item):
    """The codec a camera is set to deliver: the admin's choice (videoCodec, written by the
    local admin GUI when it switches the camera), else the hardware-first default the
    adapter recorded (videoCodecDefault, adapter/codec_caps.py). Every camera predating
    codec selection streams H.264, hence the last resort."""
    return item.get("videoCodec") or item.get("videoCodecDefault") or "h264"


def plain(v):
    """A registry value as JSON can carry it: DynamoDB returns every number as a Decimal, which
    json.dumps refuses -- one Decimal anywhere in the response turns the whole camera list into
    a 500 (detectionTests' `events` counts are the first nested numbers this route returns)."""
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, dict):
        return {k: plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    return v


def lambda_handler(event, context):
    try:
        table = boto3.resource("dynamodb", region_name=REGION).Table("cameras")
        # Scan, not Query -- this table has no sort key and is expected to stay small
        # (a handful of cameras on one adapter), so a full scan is the right tool here.
        # Unregistered cameras are left out entirely, which is all the page needs to hide them
        # (VMS-PORT-PLAN.md D4). Their clips stay in S3 and the clips table, reachable again
        # once the camera is re-registered. VMS's page reads its own copy (pir-list-cameras),
        # so this filter can't affect it. Scanned page by page: the table is small, but a
        # single Scan stops at 1 MB.
        items, kw = [], {}
        while True:
            page = table.scan(**kw)
            items += page["Items"]
            if "LastEvaluatedKey" not in page:
                break
            kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        items = [i for i in items if not i.get("unregisteredAt")]
        cameras = sorted(
            (
                {
                    "id": i["cameraId"],
                    "mode": i.get("mode"),
                    "hasIrControl": bool(i.get("hasIrControl", False)),
                    # How clips get created for this camera. "manual" (the default, and
                    # what every camera had before Phase 1) means only the Record button
                    # makes clips; the detection modes are consumed by the event watcher.
                    "recordingMode": i.get("recordingMode", "manual"),
                    # Detection needs an ONVIF event subscription, so it is only possible
                    # for cameras we have an ONVIF host for. cam-01 is a USB webcam with
                    # no ONVIF at all and can therefore only ever be "manual" -- the GUIs
                    # gate the selector on this, the same way they gate IR control.
                    "supportsDetection": bool(i.get("onvifHost")),
                    # Whether this camera records audio alongside video. Two separate
                    # facts, both needed by the GUIs: audioCapable is a property of the
                    # hardware (written at registration), audioEnabled is the user's
                    # choice. Default is video-only -- audio is opt-in per camera, which
                    # is both the safe default for a recording system and the one the
                    # law tends to assume (see Camera-Features.md on audio).
                    "audioCapable": bool(i.get("audioCapable", False)),
                    "audioEnabled": bool(i.get("audioEnabled", False)),
                    # Seconds of local recording kept after the connection to AWS drops;
                    # 0 = off, the default. Separate from the fixed ~2 min pre-roll that
                    # is always included (OUTAGE.md 2.2). Every camera can do this -- it
                    # is MediaMTX-side, not camera-side -- so unlike audio there is no
                    # capability flag to gate on, only the USB buffer being present,
                    # which only the adapter can see.
                    "outageBufferSec": int(i.get("outageBufferSec", 0) or 0),
                    # Video codec, for display only: it is chosen in the local admin GUI,
                    # which switches the camera's own encoder -- there is deliberately no
                    # cloud route to change it. videoEncode says where the encoding happens
                    # ("camera" or "pi-hw", the adapter's hardware encoder); the client
                    # also warns a viewer whose browser can't decode H.265.
                    "videoCodec": video_codec(i),
                    "videoEncode": (i.get("videoCodecCaps") or {}).get(video_codec(i)),
                    # What the cloud stream last carried (set by the producer at start);
                    # differs from videoCodec only until the producer next starts.
                    "videoCodecActive": i.get("videoCodecActive"),
                    # Everything below is additive (the registry contract: add, never redefine)
                    # and never carries a credential -- no rtspUrl, no liveRtspUrl.
                    # A name to show beside the ID; set in the admin GUI only.
                    "displayName": i.get("displayName"),
                    # The recording triggers the camera's own ONVIF event list offers, the last
                    # "Test triggers" result per mode, and its settings page (LAN only), so the
                    # page can grey out modes it doesn't publish and say where to switch one on.
                    "detectionModes": i.get("detectionModes"),
                    "detectionTests": i.get("detectionTests"),
                    "webUrl": i.get("webUrl"),
                    # What the Live player shows: the camera's sub stream while its main stream
                    # is recorded, otherwise the recorded stream itself; and the live push's
                    # codec as the Pi last saw it (stream-codec.py --live), for the page's
                    # H.265 warning on the Live player.
                    "liveView": "sub" if i.get("liveRtspUrl") else "recorded",
                    "liveVideoCodec": i.get("liveVideoCodecActive")
                                      or (None if i.get("liveRtspUrl") else video_codec(i)),
                }
                for i in items
            ),
            key=lambda c: c["id"],
        )
        return {"statusCode": 200, "headers": CORS, "body": json.dumps({"cameras": plain(cameras)})}
    except Exception as e:
        return {"statusCode": 500, "headers": CORS, "body": json.dumps({"error": str(e)})}
