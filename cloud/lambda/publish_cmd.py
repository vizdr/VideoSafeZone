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
        if not cam_item:
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
