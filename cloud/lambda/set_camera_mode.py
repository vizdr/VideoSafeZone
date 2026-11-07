import boto3, os, json

REGION = os.environ["AWS_REGION"]
CORS = {"Access-Control-Allow-Origin": "*"}

# Phase 1 of detection-driven recording (Camera-Features.md §9). This only writes the
# registry -- nothing consumes the value yet, by design: shipping the control surface
# before the event logic keeps the change independently testable and carries no
# regression risk to the working control plane.
VALID_MODES = {"manual", "motion", "cellMotion", "human"}

cameras_table = boto3.resource("dynamodb", region_name=REGION).Table("cameras")


def unregistered_response(camera_id, item):
    """409 for a camera unregistered in an admin GUI, else None. The registry is shared with VMS:
    list-cameras no longer shows such a camera, but an open page or a direct API call can still
    name it (VMS-PORT-PLAN.md D4). Same check in every Lambda that acts on a camera."""
    if item and item.get("unregisteredAt"):
        return {"statusCode": 409, "headers": CORS, "body": json.dumps(
            {"error": f"'{camera_id}' is unregistered: re-register it in the admin GUI first"})}
    return None


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
        mode = body.get("recordingMode")

        if mode not in VALID_MODES:
            return {"statusCode": 400, "headers": CORS, "body": json.dumps(
                {"error": f"recordingMode must be one of {sorted(VALID_MODES)}"})}

        item = cameras_table.get_item(Key={"cameraId": camera_id}).get("Item") if camera_id else None
        refusal = tenancy_refusal(event, item, "control") if item else None
        if refusal == "view-only":
            return view_only_response()
        if not item or refusal:                                  # someone else's: as if unknown (D4)
            return {"statusCode": 400, "headers": CORS,
                    "body": json.dumps({"error": f"unknown camera '{camera_id}'"})}
        if r := unregistered_response(camera_id, item):
            return r

        # A detection mode requires an ONVIF event subscription, which requires an ONVIF
        # host. Rejecting here rather than only hiding the option in the UI: the API is
        # reachable independently of the page, and a camera silently set to a mode it
        # cannot honour would look like a broken watcher later.
        if mode != "manual" and not item.get("onvifHost"):
            return {"statusCode": 400, "headers": CORS, "body": json.dumps(
                {"error": f"'{camera_id}' has no ONVIF host, so it supports only 'manual'"})}

        # detectionModes: what the camera's own ONVIF event list supports, recorded by the admin
        # GUI. A mode outside it would subscribe to events the camera never sends, and nothing
        # would ever be recorded (FoundAndFixed.md #65). Absent = not known yet: allowed.
        supported = item.get("detectionModes")
        if mode != "manual" and supported is not None and mode not in supported:
            return {"statusCode": 409, "headers": CORS, "body": json.dumps(
                {"error": f"'{camera_id}' doesn't publish the events for '{mode}' "
                          f"(it supports: {', '.join(supported) or 'no detection mode'})"})}

        cameras_table.update_item(
            Key={"cameraId": camera_id},
            UpdateExpression="SET recordingMode = :m",
            ExpressionAttributeValues={":m": mode},
        )
        return {"statusCode": 200, "headers": CORS,
                "body": json.dumps({"cameraId": camera_id, "recordingMode": mode})}
    except Exception as e:
        return {"statusCode": 500, "headers": CORS, "body": json.dumps({"error": str(e)})}
