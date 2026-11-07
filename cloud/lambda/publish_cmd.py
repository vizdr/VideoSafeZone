import boto3, os, json

REGION = os.environ["AWS_REGION"]
THING  = os.environ.get("THING_NAME", "adapter-01")
CMD_T  = f"adapter/{THING}/cmd"
ALLOWED_IR_MODES = {"AUTO", "ON", "OFF"}

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
    # See get_hls_url.py for why every path returns through here rather than letting
    # an exception propagate -- API Gateway's own error response has no CORS headers.
    try:
        body = json.loads(event.get("body") or "{}")
        action = body.get("action")
        camera = body.get("camera", "cam-01")
        # live / live-stop: watch without recording (agent.py set_live). The page re-sends `live`
        # every 2 min while the player is visible; the Pi stops the live push by itself when that
        # stops (its deadline), so a lost live-stop costs minutes, never an unattended stream.
        if action not in ("start", "stop", "ir", "live", "live-stop"):
            return {
                "statusCode": 400,
                "headers": CORS,
                "body": json.dumps({"error": "action must be 'start', 'stop', 'ir', 'live' or 'live-stop'"}),
            }
        cam_item = cameras_table.get_item(Key={"cameraId": camera}).get("Item")
        need = "view" if action in ("live", "live-stop") else "control"
        refusal = tenancy_refusal(event, cam_item, need) if cam_item else None
        if refusal == "view-only":
            return view_only_response()
        if not cam_item or refusal:                              # someone else's: as if unknown (D4)
            return {
                "statusCode": 400,
                "headers": CORS,
                "body": json.dumps({"error": f"unknown camera '{camera}'"}),
            }
        if action not in ("stop", "live-stop") and (r := unregistered_response(camera, cam_item)):
            return r

        payload = {"action": action, "camera": camera}
        if action == "ir":
            if not cam_item.get("hasIrControl"):
                return {
                    "statusCode": 400,
                    "headers": CORS,
                    "body": json.dumps({"error": f"camera '{camera}' has no IR control"}),
                }
            mode = body.get("mode")
            if mode not in ALLOWED_IR_MODES:
                return {
                    "statusCode": 400,
                    "headers": CORS,
                    "body": json.dumps({"error": "mode must be 'AUTO', 'ON', or 'OFF'"}),
                }
            payload["mode"] = mode

        # One shared command topic for the whole adapter (not one topic per camera) --
        # the camera field inside the payload is what agent.py uses to route to the
        # right systemd unit (kvs-cam01.service vs kvs-cam02.service) or ONVIF client.
        # The IoT policy's existing adapter/adapter-01/* wildcard already covers this
        # without changes.
        iot_data = boto3.client("iot-data", region_name=REGION)
        iot_data.publish(topic=CMD_T, qos=1, payload=json.dumps(payload))

        return {
            "statusCode": 200,
            "headers": CORS,
            "body": json.dumps({"published": action, "camera": camera, **({"mode": payload["mode"]} if action == "ir" else {})}),
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "headers": CORS,
            "body": json.dumps({"error": str(e)}),
        }
