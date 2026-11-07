import boto3, os, json

REGION = os.environ["AWS_REGION"]
BUCKET = os.environ["BUCKET"]

CORS = {"Access-Control-Allow-Origin": "*"}
# The camera row, for the tenant check: whose camera the clip is (DEALER-PANEL-PLAN.md D4).
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
    try:
        body = json.loads(event.get("body") or "{}")
        key = body.get("s3Key")
        start_ts = body.get("startTs")
        if not key or not key.startswith("clips/") or not start_ts:
            return {"statusCode": 400, "headers": CORS,
                     "body": json.dumps({"error": "s3Key and startTs are required"})}

        # cameraId isn't in the request -- it's recoverable from the key itself, since
        # every writer (clip_to_s3.py, record_clip.py) always builds keys as
        # clips/<stream>/... . Avoids requiring the client to separately track/send it.
        parts = key.split("/")
        if len(parts) < 2:
            return {"statusCode": 400, "headers": CORS,
                     "body": json.dumps({"error": "malformed s3Key"})}
        camera_id = parts[1]
        row = cameras_table.get_item(Key={"cameraId": camera_id}).get("Item")
        refusal = tenancy_refusal(event, row or {"cameraId": camera_id}, "control")   # whose clip (D4)
        if refusal == "hidden":
            return {"statusCode": 404, "headers": CORS, "body": json.dumps({"error": "no such clip"})}
        if refusal:
            return view_only_response()

        s3 = boto3.client("s3", region_name=REGION, endpoint_url=f"https://s3.{REGION}.amazonaws.com")
        # Deleting an archived (Deep Archive/Glacier) object needs no prior restore --
        # unlike reading or reclassifying it, DeleteObject always works directly.
        s3.delete_object(Bucket=BUCKET, Key=key)

        boto3.resource("dynamodb", region_name=REGION).Table("clips").delete_item(
            Key={"cameraId": camera_id, "startTs": start_ts})

        return {"statusCode": 200, "headers": CORS, "body": json.dumps({"status": "deleted"})}
    except Exception as e:
        return {"statusCode": 500, "headers": CORS, "body": json.dumps({"error": str(e)})}
