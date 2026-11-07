import boto3, os, json
from botocore.exceptions import ClientError
from datetime import datetime, timedelta, timezone

REGION = os.environ["AWS_REGION"]
DEFAULT_STREAM = os.environ.get("STREAM_NAME", "cam-01")

CORS = {"Access-Control-Allow-Origin": "*"}
cameras_table = boto3.resource("dynamodb", region_name=REGION).Table("cameras")

# --- tenancy (DEALER-PANEL-PLAN.md D4, Phase 3): these three are the same in every camera and clip
# Lambda, kept identical by tests/cloud/test_lambda_twins.py -------------------------------------

def tenant_access(event, row):
    """What the caller's ID token allows on this camera row: "control", "view" or None (D4).
    vsz_adapters gives full access by the Thing that serves the camera ("*": every camera, a system
    administrator's); vsz_cameras gives view only, as cameraId@thing. A row without adapterId
    belongs to no customer yet, so only an administrator reaches it."""
    claims = (((event or {}).get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    full = {a for a in str(claims.get("vsz_adapters") or "").split(",") if a}
    if "*" in full:
        return "control"
    adapter = (row or {}).get("adapterId")
    if not adapter:
        return None
    if adapter in full:
        return "control"
    shared = {c for c in str(claims.get("vsz_cameras") or "").split(",") if c}
    return "view" if f"{row.get('cameraId')}@{adapter}" in shared else None


def tenancy_refusal(event, row, need):
    """None when the caller may do what needs `need` ("view" or "control") on this camera; else
    "hidden" -- answer exactly as for an unknown camera, so other tenants' IDs aren't confirmed --
    or "view-only". Each refusal is logged. TENANCY=report, the rollout's first step, logs what
    enforcing would refuse and refuses nothing; unset, or anything else, means enforce."""
    access = tenant_access(event, row)
    if access == "control" or (access == "view" and need == "view"):
        return None
    report = os.environ.get("TENANCY") == "report"
    claims = (((event or {}).get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    print(json.dumps({"tenancy": "would-refuse" if report else "refused",
                      "verdict": "view-only" if access == "view" else "hidden", "need": need,
                      "camera": (row or {}).get("cameraId"), "adapterId": (row or {}).get("adapterId"),
                      "role": claims.get("vsz_role"), "sub": claims.get("sub"),
                      "path": (event or {}).get("path")}))
    if report:
        return None
    return "view-only" if access == "view" else "hidden"


def view_only_response():
    """403 for a camera shared with the caller to watch, when they try to change it (D18)."""
    return {"statusCode": 403, "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"error": "this camera is shared with you to view, not to change"})}


def lambda_handler(event, context):
    kv = boto3.client("kinesisvideo", region_name=REGION)
    try:
        params = event.get("queryStringParameters") or {}
        stream = params.get("stream", DEFAULT_STREAM)
        # IAM's stream-ARN resource is now a wildcard (cam-*) rather than an enumerated
        # list, so this registry lookup is the only thing standing between an arbitrary
        # caller-supplied "stream" value and a live KVS API call -- not just a nicety.
        item = cameras_table.get_item(Key={"cameraId": stream}).get("Item")
        if not item or tenancy_refusal(event, item, "view"):     # someone else's: as if unknown (D4)
            return {"statusCode": 400, "headers": CORS,
                     "body": json.dumps({"error": f"unknown stream '{stream}'"})}
        if item.get("unregisteredAt"):
            return {"statusCode": 409, "headers": CORS, "body": json.dumps(
                {"error": f"'{stream}' is unregistered: re-register it in the admin GUI first"})}
        # view=live: the cloud page's Live player, independent of Start (VMS-PORT-PLAN.md D3).
        # The Pi pushes the camera's sub stream -- or, with none, its recorded stream -- to its
        # own KVS stream "<camera>-live" while someone watches (agent.py set_live). The name is
        # derived here, never taken from the caller. Anything else (and a page from before Live
        # existed, which sends no view) gets the recorded stream, as it always did.
        live = params.get("view") == "live"
        if live:
            stream = f"{stream}-live"
        # since=<seconds>: the page pressed Start (or Live) that long ago and wants only footage
        # from after it. A plain LIVE session right after a quick Stop -> Start is built from the
        # previous run's last fragment, which KVS still counts as live: the player plays 4 s of
        # old video and then waits for the new run (FoundAndFixed.md #66). LIVE_REPLAY from that
        # moment answers "no fragments" (-> 503, the page keeps polling) until the new run's
        # first fragment arrives, then plays on as live. Server timestamps, so no clock skew:
        # the page measures the age on its own clock and this subtracts it from AWS's.
        since = params.get("since")
        try:
            since = min(max(float(since), 1.0), 600.0) if since is not None else None
        except ValueError:
            return {"statusCode": 400, "headers": CORS,
                    "body": json.dumps({"error": "since must be a number of seconds"})}

        ep = kv.get_data_endpoint(
            StreamName=stream, APIName="GET_HLS_STREAMING_SESSION_URL"
        )["DataEndpoint"]

        kvam = boto3.client("kinesis-video-archived-media",
                            endpoint_url=ep, region_name=REGION)
        # KVS allows 300-43200s. 300 (the minimum, and the old value here) meant the
        # session URL died five minutes into every viewing session -- the client then
        # sat retrying a permanently-dead URL and the browser showed a spinner forever.
        # An hour keeps refreshes rare without leaving a usable URL lying around for
        # half a day; the client refreshes at 80% of this (see scheduleSessionRefresh).
        session_ttl = 3600
        mode = {"PlaybackMode": "LIVE"}
        if since is not None:
            mode = {"PlaybackMode": "LIVE_REPLAY", "HLSFragmentSelector": {
                "FragmentSelectorType": "SERVER_TIMESTAMP",
                "TimestampRange": {"StartTimestamp":
                                   datetime.now(timezone.utc) - timedelta(seconds=since)}}}
        url = kvam.get_hls_streaming_session_url(
            StreamName=stream,
            **mode,
            Expires=session_ttl,
            # fMP4 is KVS's default already; pinned because H.265 cameras depend on it --
            # HLS carries HEVC in fragmented MP4 (the verified path, `hvc1`), and a later
            # switch to MPEG_TS for some other reason must not silently break them
            # (measurements/codec-phase0.md §3).
            ContainerFormat="FRAGMENTED_MP4",
        )["HLSStreamingSessionURL"]

        return {
            "statusCode": 200,
            "headers": CORS,
            "body": json.dumps({"url": url, "expires_in": session_ttl, "stream": stream,
                                "view": "live" if live else "recorded"}),
        }
    except ClientError as e:
        # Matched on the error *code*, not on a client's generated exception class.
        # This used to be `except kv.exceptions.ResourceNotFoundException`, which never
        # fired for the common case: "no fragments" is raised by the **kvam** client
        # (kinesis-video-archived-media), and boto3 generates a separate exception class
        # per client, so kvam's ResourceNotFoundException is not kv's. The friendly 503
        # below was dead code and users saw the raw AWS text via the generic handler.
        if e.response.get("Error", {}).get("Code") == "ResourceNotFoundException" and live:
            return {"statusCode": 503, "headers": CORS, "body": json.dumps({"error":
                "live view not arriving yet -- the Pi starts it on request; "
                "if it doesn't come, the Pi may be offline from AWS."})}
        if e.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return {
                "statusCode": 503,
                "headers": CORS,
                "body": json.dumps({"error":
                    "stream is not live -- no footage is reaching the cloud. "
                    "Press Start; if it stays down, check the camera is online."}),
            }
        return {
            "statusCode": 500,
            "headers": CORS,
            "body": json.dumps({"error": str(e)}),
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "headers": CORS,
            "body": json.dumps({"error": str(e)}),
        }
