import boto3, os, json

REGION = os.environ["AWS_REGION"]
BUCKET = os.environ["BUCKET"]

CORS = {"Access-Control-Allow-Origin": "*"}
# The camera row, for the tenant check: whose camera the clip is (DEALER-PANEL-PLAN.md D4).
cameras_table = boto3.resource("dynamodb", region_name=REGION).Table("cameras")
VALID_TIERS = {"STANDARD", "STANDARD_IA", "DEEP_ARCHIVE"}

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
        target = body.get("tier")
        if not key or not key.startswith("clips/") or target not in VALID_TIERS:
            return {"statusCode": 400, "headers": CORS,
                     "body": json.dumps({"error": "s3Key and a valid tier are required"})}

        # Whose clip it is: the camera from the key, clips/<cameraId>/..., and its row (D4). A clip
        # of a camera the caller may not see is answered as if there were no such clip.
        camera_id = key.split("/")[1] if key.count("/") >= 2 else ""
        row = cameras_table.get_item(Key={"cameraId": camera_id}).get("Item") if camera_id else None
        refusal = tenancy_refusal(event, row or {"cameraId": camera_id}, "control")
        if refusal == "hidden":
            return {"statusCode": 404, "headers": CORS, "body": json.dumps({"error": "no such clip"})}
        if refusal:
            return view_only_response()

        s3 = boto3.client("s3", region_name=REGION, endpoint_url=f"https://s3.{REGION}.amazonaws.com")
        head = s3.head_object(Bucket=BUCKET, Key=key)
        current = head.get("StorageClass", "STANDARD")
        restore = head.get("Restore")

        if current == target:
            return {"statusCode": 200, "headers": CORS, "body": json.dumps({"status": "unchanged"})}

        # Moving a clip OUT of Deep Archive/Glacier needs it already restored (temporarily
        # readable) first -- CopyObject can't read the bytes of a still-archived object.
        # Moving further IN (Standard -> IA -> Deep Archive) has no such restriction; the
        # object's bytes are always readable by S3 itself for that direction.
        if current in ("DEEP_ARCHIVE", "GLACIER"):
            if restore is None or 'ongoing-request="true"' in restore:
                return {"statusCode": 409, "headers": CORS, "body": json.dumps({
                    "error": "clip is archived and not yet restored -- press Play first, "
                             "wait for the restore to finish, then try changing the tier again"})}

        s3.copy_object(
            Bucket=BUCKET, Key=key,
            CopySource={"Bucket": BUCKET, "Key": key},
            StorageClass=target,
            MetadataDirective="COPY",
        )
        return {"statusCode": 200, "headers": CORS, "body": json.dumps({"status": "changed", "tier": target})}
    except Exception as e:
        return {"statusCode": 500, "headers": CORS, "body": json.dumps({"error": str(e)})}
