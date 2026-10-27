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


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
        camera_id = body.get("cameraId")
        enabled = body.get("audioEnabled")

        if not isinstance(enabled, bool):
            return {"statusCode": 400, "headers": CORS,
                    "body": json.dumps({"error": "audioEnabled must be true or false"})}

        item = cameras_table.get_item(Key={"cameraId": camera_id}).get("Item") if camera_id else None
        if not item:
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
