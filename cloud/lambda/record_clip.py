import boto3, os, json
from botocore.exceptions import ClientError
from datetime import datetime, timedelta, timezone

REGION = os.environ["AWS_REGION"]
S3B = os.environ["BUCKET"]
DEFAULT_STREAM = os.environ.get("STREAM_NAME", "cam-01")

CORS = {"Access-Control-Allow-Origin": "*"}
cameras_table = boto3.resource("dynamodb", region_name=REGION).Table("cameras")


def unregistered_response(camera_id, item):
    """409 for a camera unregistered in an admin GUI, else None. The registry is shared with VMS:
    list-cameras no longer shows such a camera, but an open page or a direct API call can still
    name it (VMS-PORT-PLAN.md D4). Same check in every Lambda that acts on a camera."""
    if item and item.get("unregisteredAt"):
        return {"statusCode": 409, "headers": CORS, "body": json.dumps(
            {"error": f"'{camera_id}' is unregistered: re-register it in the admin GUI first"})}
    return None

# Small safety padding on both ends -- the browser's Start/Stop button presses are wall
# clock on the *viewer's* machine, not the producer's; a couple of seconds of margin
# absorbs minor clock skew and network round-trip between click and this Lambda running,
# same rationale as the fixed +/-window in clip_to_s3.py, just applied to a caller-given
# range instead of one fixed to a single event timestamp.
PAD = timedelta(seconds=2)


def utc(ts):
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def codec_windows(start, end, camera):
    """[start, end] split at the camera's last video-codec switch -- one window per codec.

    KVS refuses a clip whose fragments change codec ("The codec private data is not
    consistent between all fragments", measurements/codec-phase0.md §4), so a recording
    that spans an H.264/H.265 switch would otherwise be lost whole. The producer stamps
    videoCodecActiveSince when it restarts on the new codec (adapter/bin/stream-codec.py);
    nothing of the old codec is newer than that. Same function in clip_to_s3.py.
    """
    since = (camera or {}).get("videoCodecActiveSince")
    if since:
        since = utc(datetime.fromisoformat(since))
        if start < since < end:
            return [(start, since), (since, end)]
    return [(start, end)]


def mp4_video_codec(data):
    """'h264' / 'h265' from the clip's own MP4 sample description, or None.

    Read from the payload, not the registry: the two halves of a clip split at a codec
    switch have different codecs. Only `moov` is searched -- the sample-entry fourccs
    (avc1/avc3, hvc1/hev1) could occur by chance in `mdat`, and `ftyp`'s brand list may
    itself say "avc1". Same function in clip_to_s3.py.
    """
    i, n = 0, len(data)
    while i + 8 <= n:
        size, box = int.from_bytes(data[i:i + 4], "big"), data[i + 4:i + 8]
        hdr = 8
        if size == 1:                        # 64-bit largesize
            size, hdr = int.from_bytes(data[i + 8:i + 16], "big"), 16
        elif size == 0:                      # box runs to end of file
            size = n - i
        if size < hdr:
            return None                      # malformed; don't guess
        if box == b"moov":
            moov = data[i + hdr:i + size]
            if b"hvc1" in moov or b"hev1" in moov:
                return "h265"
            if b"avc1" in moov or b"avc3" in moov:
                return "h264"
            return None
        i += size
    return None


def _child(data, name):
    """The payload of the first box called `name` at this level of an MP4, or None."""
    i, n = 0, len(data or b"")
    while i + 8 <= n:
        size, box = int.from_bytes(data[i:i + 4], "big"), data[i + 4:i + 8]
        hdr = 8
        if size == 1:                        # 64-bit largesize
            size, hdr = int.from_bytes(data[i + 8:i + 16], "big"), 16
        elif size == 0:                      # box runs to end of file
            size = n - i
        if size < hdr:
            return None                      # malformed; don't guess
        if box == name:
            return data[i + hdr:i + size]
        i += size
    return None


def mp4_duration_sec(data):
    """Whole seconds the clip actually holds, from its moov/mvhd, or None when unreadable.

    GetClip returns whatever the archive has inside the window, which can be far less than was
    asked: a cam-02 H.265 detection clip listed as 45 s held 9 s (FoundAndFixed.md #54). So the
    duration shown in the clip list comes from the file, not from the request. GetClip's MP4 is
    not fragmented: mvhd carries the real duration. Same function in record_clip.py and
    clip_to_s3.py.
    """
    mvhd = _child(_child(data, b"moov"), b"mvhd")
    if not mvhd or len(mvhd) < 32:
        return None
    if mvhd[0] == 1:                         # version 1: 64-bit times and duration
        scale, dur = int.from_bytes(mvhd[20:24], "big"), int.from_bytes(mvhd[24:32], "big")
    else:
        scale, dur = int.from_bytes(mvhd[12:16], "big"), int.from_bytes(mvhd[16:20], "big")
    return round(dur / scale) if scale else None


def no_fragments(e):
    # Matched on the error *code*: GetClip's ResourceNotFoundException is raised by the
    # kinesis-video-archived-media client, and boto3 generates a separate exception class
    # per client -- `except kv.exceptions.ResourceNotFoundException` never fired here, so
    # the friendly 503 below was dead code (FoundAndFixed.md #45; get_hls_url.py had the
    # same fault).
    return isinstance(e, ClientError) and e.response.get("Error", {}).get("Code") == "ResourceNotFoundException"


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
        stream = body.get("stream", DEFAULT_STREAM)
        camera = cameras_table.get_item(Key={"cameraId": stream}).get("Item")
        if not camera:
            return {"statusCode": 400, "headers": CORS,
                     "body": json.dumps({"error": f"unknown stream '{stream}'"})}
        if r := unregistered_response(stream, camera):
            return r
        start = utc(datetime.fromisoformat(body["startTs"])) - PAD
        end = utc(datetime.fromisoformat(body["endTs"])) + PAD
        if end <= start:
            return {"statusCode": 400, "headers": CORS,
                     "body": json.dumps({"error": "endTs must be after startTs"})}

        kv = boto3.client("kinesisvideo", region_name=REGION)
        ep = kv.get_data_endpoint(StreamName=stream, APIName="GET_CLIP")["DataEndpoint"]
        kvam = boto3.client("kinesis-video-archived-media", endpoint_url=ep, region_name=REGION)
        s3 = boto3.client("s3", region_name=REGION, endpoint_url=f"https://s3.{REGION}.amazonaws.com")
        clips = boto3.resource("dynamodb", region_name=REGION).Table("clips")

        windows = codec_windows(start, end, camera)
        split = len(windows) > 1
        keys, missing = [], None
        for s, e in windows:
            try:
                clip = kvam.get_clip(
                    StreamName=stream,
                    ClipFragmentSelector={
                        "FragmentSelectorType": "PRODUCER_TIMESTAMP",
                        "TimestampRange": {"StartTimestamp": s, "EndTimestamp": e},
                    },
                )["Payload"].read()
            except ClientError as err:
                # One side of a split may legitimately be empty -- the ~10 s the producer
                # takes to restart on the new codec -- so only "nothing at all" is an error.
                if split and no_fragments(err):
                    missing = err
                    continue
                raise
            key = f"clips/{stream}/{s:%Y/%m/%d}/{s:%H%M%S}-manual.mp4"
            s3.put_object(Bucket=S3B, Key=key, Body=clip, ContentType="video/mp4")
            clips.put_item(Item={
                "cameraId": stream, "startTs": s.isoformat(),
                "s3Key": key,
                "labels": ["manual-recording"] + (["codec-switch"] if split else []),
                "durationSec": mp4_duration_sec(clip) or round((e - s).total_seconds()),
                # shown after the duration in the clip list; absent only if unreadable
                **({"videoCodec": c} if (c := mp4_video_codec(clip)) else {}),
            })
            keys.append(key)
        if not keys:
            raise missing
        return {"statusCode": 200, "headers": CORS,
                "body": json.dumps({"key": keys[0], "keys": keys})}
    except Exception as e:
        if no_fragments(e):
            return {"statusCode": 503, "headers": CORS, "body": json.dumps(
                {"error": "no footage found for that time range -- was the stream live throughout?"})}
        return {"statusCode": 500, "headers": CORS, "body": json.dumps({"error": str(e)})}
