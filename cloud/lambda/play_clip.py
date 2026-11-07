import boto3, os, json

REGION = os.environ["AWS_REGION"]
BUCKET = os.environ["BUCKET"]

CORS = {"Access-Control-Allow-Origin": "*"}
# The camera row, for the tenant check: whose camera the clip is (DEALER-PANEL-PLAN.md D4).
cameras_table = boto3.resource("dynamodb", region_name=REGION).Table("cameras")

# Deep Archive retrieval tiers: "Standard" ~12h, "Bulk" ~48h. "Expedited" does not exist
# for Deep Archive (only for regular Glacier) -- there is no fast path here, by design;
# that's the whole reason it's ~1/6th the storage cost of Standard.
RESTORE_ETA_HOURS = 12

# A presigned URL has to outlive the *viewing session*, not the download. A <video> tag
# re-uses the same signed URL for every range request, so once it expires the player 403s
# mid-playback -- on a pause, a seek, or simply a slow link. A flat 300s was fine when
# every clip was a 45s evidence clip (~5 MB) and wrong as soon as outage-buffer clips
# arrived: those run to 2 hours and ~1.1 GB (OUTAGE.md 4.6).
#
# Scale with object size, which is the only proxy for duration available here -- the
# DynamoDB durationSec is not in this request. ~100 KB/s is a deliberately pessimistic
# effective rate, so the URL comfortably outlasts watching the clip end to end.
PRESIGN_MIN_SEC = 900        # 15 min floor, even for a tiny clip
PRESIGN_MAX_SEC = 21600      # 6 h ceiling -- see the credential caveat below
PRESIGN_BYTES_PER_SEC = 100 * 1024


def presign_seconds(content_length: int) -> int:
    # NOTE: the URL is signed with this Lambda's *temporary* role credentials, so it dies
    # when those do, regardless of ExpiresIn. The 6h ceiling keeps the promise the URL
    # makes roughly honest rather than advertising a lifetime S3 will not grant.
    return max(PRESIGN_MIN_SEC,
               min(PRESIGN_MAX_SEC,
                   PRESIGN_MIN_SEC + int((content_length or 0) / PRESIGN_BYTES_PER_SEC)))


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
    try:
        body = json.loads(event.get("body") or "{}")
        key = body.get("s3Key")
        if not key or not key.startswith("clips/"):
            return {"statusCode": 400, "headers": CORS,
                     "body": json.dumps({"error": "s3Key required"})}

        # Whose clip it is: the camera from the key, clips/<cameraId>/..., and its row (D4). A clip
        # of a camera the caller may not see is answered as if there were no such clip.
        camera_id = key.split("/")[1] if key.count("/") >= 2 else ""
        row = cameras_table.get_item(Key={"cameraId": camera_id}).get("Item") if camera_id else None
        refusal = tenancy_refusal(event, row or {"cameraId": camera_id}, "view")
        if refusal == "hidden":
            return {"statusCode": 404, "headers": CORS, "body": json.dumps({"error": "no such clip"})}
        if refusal:
            return view_only_response()

        # explicit regional endpoint -- without it, generate_presigned_url can build a
        # global (s3.amazonaws.com) hostname while signing for eu-central-1, and S3
        # rejects the mismatch between the endpoint hit and the signing region with a
        # bare 400 (no useful error body). Only matters outside us-east-1.
        s3 = boto3.client("s3", region_name=REGION, endpoint_url=f"https://s3.{REGION}.amazonaws.com")
        head = s3.head_object(Bucket=BUCKET, Key=key)
        # S3 omits StorageClass entirely for the Standard tier -- absence means Standard,
        # not an error.
        storage_class = head.get("StorageClass", "STANDARD")
        restore = head.get("Restore")  # e.g. 'ongoing-request="true"'
        expires = presign_seconds(head.get("ContentLength", 0))

        if storage_class in ("STANDARD", "STANDARD_IA"):
            url = s3.generate_presigned_url(
                "get_object", Params={"Bucket": BUCKET, "Key": key}, ExpiresIn=expires)
            return {"statusCode": 200, "headers": CORS,
                     "body": json.dumps({"status": "ready", "url": url, "expires_in": expires})}

        # DEEP_ARCHIVE or GLACIER from here on -- not directly readable.
        if restore is None:
            s3.restore_object(Bucket=BUCKET, Key=key, RestoreRequest={
                "Days": 1, "GlacierJobParameters": {"Tier": "Standard"}})
            return {"statusCode": 202, "headers": CORS, "body": json.dumps({
                "status": "restore_requested", "eta_hours": RESTORE_ETA_HOURS})}

        if 'ongoing-request="true"' in restore:
            return {"statusCode": 202, "headers": CORS,
                     "body": json.dumps({"status": "restoring", "eta_hours": RESTORE_ETA_HOURS})}

        # ongoing-request="false" -- restore finished, object is temporarily readable
        # again (still reported as DEEP_ARCHIVE/GLACIER storage class, but a GET works
        # until the restored copy's expiry-date).
        url = s3.generate_presigned_url(
            "get_object", Params={"Bucket": BUCKET, "Key": key}, ExpiresIn=expires)
        return {"statusCode": 200, "headers": CORS,
                 "body": json.dumps({"status": "ready", "url": url, "expires_in": expires})}
    except Exception as e:
        return {"statusCode": 500, "headers": CORS, "body": json.dumps({"error": str(e)})}
