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
        enabled = body.get("audioEnabled")

        if not isinstance(enabled, bool):
            return {"statusCode": 400, "headers": CORS,
                    "body": json.dumps({"error": "audioEnabled must be true or false"})}

        item = cameras_table.get_item(Key={"cameraId": camera_id}).get("Item") if camera_id else None
        refusal = tenancy_refusal(event, item, "control") if item else None
        if refusal == "view-only":
            return view_only_response()
        if not item or refusal:                                  # someone else's: as if unknown (D4)
            return {"statusCode": 400, "headers": CORS,
                    "body": json.dumps({"error": f"unknown camera '{camera_id}'"})}
        if r := unregistered_response(camera_id, item):
            return r

        # Same reasoning as set_camera_mode's ONVIF check: the API is reachable
        # independently of the page, so refuse here rather than relying on the UI hiding
        # the control. Enabling audio on a camera with no microphone would build a
        # pipeline whose audio pad never delivers, and kvssink collects across pads --
        # so the *video* would stall too. That is a much worse failure than a 400.
        if enabled and not item.get("audioCapable"):
            return {"statusCode": 400, "headers": CORS, "body": json.dumps(
                {"error": f"'{camera_id}' has no usable audio source"})}

        cameras_table.update_item(
            Key={"cameraId": camera_id},
            UpdateExpression="SET audioEnabled = :a",
            ExpressionAttributeValues={":a": enabled},
        )
        # The producer reads this once at startup (adapter/bin/camera-audio.py), so the
        # change lands on the next Start -- deliberately, not as a limitation. KVS refuses
        # a stream whose fragments switch between video-only and audio+video
        # ("Track changes aren't supported"), so applying this to a live producer would
        # break GetClip across the boundary. appliesOn tells the client what to say.
        return {"statusCode": 200, "headers": CORS, "body": json.dumps(
            {"cameraId": camera_id, "audioEnabled": enabled, "appliesOn": "next start"})}
    except Exception as e:
        return {"statusCode": 500, "headers": CORS, "body": json.dumps({"error": str(e)})}
