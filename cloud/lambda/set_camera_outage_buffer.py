import boto3, os, json

REGION = os.environ["AWS_REGION"]
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

# How long to keep recording locally AFTER the connection drops. 0 = off (the default).
# The ~2 min pre-roll is separate and always included, so the shortest setting still
# yields ~2.5 min of footage -- see OUTAGE.md 2.2, and note the UI must say so or the
# numbers read as nonsense.
#
# This list is duplicated in adapter/outage_buffer.py (VALID_LIMITS) and in both GUIs.
# Deliberately: the Lambda cannot import adapter code, and a value this API accepts but
# the supervisor rejects would be silently ignored on the device.
VALID_LIMITS = {0, 30, 120, 300, 600, 1800, 3600, 18000, 43200, 86400}


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
        camera_id = body.get("cameraId")
        secs = body.get("outageBufferSec")

        if not isinstance(secs, int) or isinstance(secs, bool) or secs not in VALID_LIMITS:
            return {"statusCode": 400, "headers": CORS, "body": json.dumps(
                {"error": f"outageBufferSec must be one of {sorted(VALID_LIMITS)}"})}

        item = cameras_table.get_item(Key={"cameraId": camera_id}).get("Item") if camera_id else None
        refusal = tenancy_refusal(event, item, "control") if item else None
        if refusal == "view-only":
            return view_only_response()
        if not item or refusal:                                  # someone else's: as if unknown (D4)
            return {"statusCode": 400, "headers": CORS,
                    "body": json.dumps({"error": f"unknown camera '{camera_id}'"})}
        if r := unregistered_response(camera_id, item):
            return r

        cameras_table.update_item(
            Key={"cameraId": camera_id},
            UpdateExpression="SET outageBufferSec = :s",
            ExpressionAttributeValues={":s": secs},
        )
        # Unlike the audio flag, this one applies without restarting anything: the
        # supervisor re-reads the registry every 60s and arms or disarms MediaMTX
        # recording in place. Buffering only actually runs while the camera's producer is
        # running, though -- with no producer there is no cloud stream to protect.
        return {"statusCode": 200, "headers": CORS, "body": json.dumps(
            {"cameraId": camera_id, "outageBufferSec": secs, "appliesOn": "within 60s"})}
    except Exception as e:
        return {"statusCode": 500, "headers": CORS, "body": json.dumps({"error": str(e)})}
