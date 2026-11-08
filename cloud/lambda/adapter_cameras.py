"""adapter-cameras: adding and removing cameras from the web page, through the adapter (DEALER-PANEL-PLAN.md D16).

The browser can't reach the customer's LAN, and WS-Discovery works only on it, so the page asks the
adapter over the Pi's own outbound MQTT session -- the zero-inbound-ports rule holds. On kvs-demo-api,
/adapters/{proxy+}, behind the Cognito authorizer (vsz-users):

  POST /adapters/<thing>/discover                       {} -> {requestId}: the agent scans its LAN
  GET  /adapters/<thing>/discovery                      the last scan: {requestId, at, devices[], error?}
  POST /adapters/<thing>/cameras                        {endpointRef, xaddr, user, password, displayName?}
                                                        -> {requestId}: the agent registers the camera
  GET  /adapters/<thing>/cameras/requests/<requestId>   {status: sent | registering | done | failed, cameraId?, error?}
  POST /adapters/<thing>/cameras/<cameraId>/remove      the agent unregisters it (the soft mark)
  POST /adapters/<thing>/cameras/<cameraId>/reregister  the agent brings it back from its row (USB too)

Only for an adapter in the caller's vsz_adapters -- the accounts they hold or are Super Admin of,
or "*" for an administrator (D4, D6); dealers don't add cameras from here (D5). Anyone else gets
"no such adapter", as for one that doesn't exist.

The password: stored with the request in camera-requests (expiring after 15 minutes) and sent
nowhere -- the MQTT command carries only the request's ID; the agent reads it there, and its first
progress message replaces the item (an IoT rule writes the whole item), so the password leaves the
table as soon as the adapter has it. It appears in no log and in no answer. The scan's results and
a request's progress come back through IoT rules into adapter-discovery and camera-requests, with
the Thing name from the topic: the Thing policy keeps each Pi to its own topics.

Every answer carries CORS headers, errors included (FoundAndFixed.md #8).
"""
import json
import os
import re
import time
import uuid
from decimal import Decimal

import boto3

REGION = os.environ["AWS_REGION"]
DISCOVERY_TABLE = os.environ.get("DISCOVERY_TABLE", "adapter-discovery")
REQUESTS_TABLE = os.environ.get("REQUESTS_TABLE", "camera-requests")
CAMERAS_TABLE = os.environ.get("CAMERAS_TABLE", "cameras")
REQUEST_TTL_SEC = 900
CORS = {"Access-Control-Allow-Origin": "*"}
THING = r"adapter-\d{2,6}"
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
XADDR_RE = re.compile(r"^https?://[A-Za-z0-9.\-\[\]:]{1,100}(/[\w./%-]*)?$")
DISPLAY_NAME_RE = re.compile(r"^[\w .-]{1,32}$")          # as the admin GUI's (camera_registration)
# What a request's status shows the page: never who logged in where, or with what.
STATUS_FIELDS = ("requestId", "status", "at", "cameraId", "error", "reactivated")


class Refused(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def answer(status, body):
    return {"statusCode": status, "headers": CORS, "body": json.dumps(body)}


def from_dynamodb(value):
    """An item as JSON carries it: DynamoDB returns every number as a Decimal."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: from_dynamodb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_dynamodb(v) for v in value]
    return value


def controls(claims, thing):
    """Whether the caller may add and remove the adapter's cameras: it is one of their accounts'
    adapters (vsz_adapters, full access), or they are an administrator ("*")."""
    adapters = str(claims.get("vsz_adapters") or "")
    return adapters == "*" or thing in adapters.split(",")


def table(name):
    return boto3.resource("dynamodb", region_name=REGION).Table(name)


def send(thing, command):
    boto3.client("iot-data", region_name=REGION).publish(
        topic=f"adapter/{thing}/cmd", qos=1, payload=json.dumps(command).encode())


def discover(thing, body, claims):
    request_id = str(uuid.uuid4())
    send(thing, {"action": "discover", "requestId": request_id})
    return 202, {"requestId": request_id}


def discovery(thing, body, claims):
    item = table(DISCOVERY_TABLE).get_item(Key={"thing": thing}).get("Item")
    if not item or int(item.get("ttl") or 0) < time.time():
        raise Refused(404, "no scan yet: ask for one")
    return 200, from_dynamodb({k: item[k] for k in ("requestId", "at", "devices", "error") if k in item})


def _text(body, key, limit, required=True):
    value = body.get(key)
    if value is None or value == "":
        if required:
            raise Refused(400, f"give {key}")
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise Refused(400, f"{key}: text, at most {limit} characters")
    return value


def add_camera(thing, body, claims):
    xaddr = _text(body, "xaddr", 200)
    if not XADDR_RE.match(xaddr):
        raise Refused(400, "xaddr: the device's ONVIF address, as the scan found it")
    request = {"xaddr": xaddr, "user": _text(body, "user", 64),
               "password": _text(body, "password", 128, required=False) or ""}
    ref = _text(body, "endpointRef", 100, required=False)
    if ref:
        request["endpointRef"] = ref
    name = _text(body, "displayName", 32, required=False)
    if name:
        if not DISPLAY_NAME_RE.match(name.strip()):
            raise Refused(400, "displayName: letters, digits, spaces, . _ - (up to 32)")
        request["displayName"] = name.strip()
    request_id = str(uuid.uuid4())
    now = int(time.time())
    requests = table(REQUESTS_TABLE)
    requests.put_item(Item={"requestId": request_id, "thing": thing, "status": "sent", "createdAt": now,
                            "ttl": now + REQUEST_TTL_SEC, "askedBy": claims.get("sub"), **request})
    try:
        send(thing, {"action": "register", "requestId": request_id})
    except Exception:
        requests.delete_item(Key={"requestId": request_id})     # not sent: the password goes too
        raise Refused(502, "the adapter couldn't be asked: nothing was kept")
    return 202, {"requestId": request_id}


def request_status(thing, body, claims, request_id):
    item = table(REQUESTS_TABLE).get_item(Key={"requestId": request_id}).get("Item")
    if not item or item.get("thing") != thing:
        raise Refused(404, "no such request")
    return 200, from_dynamodb({k: item[k] for k in STATUS_FIELDS if k in item})


def remove_camera(thing, body, claims, camera_id):
    row = table(CAMERAS_TABLE).get_item(Key={"cameraId": camera_id}).get("Item")
    if not row or row.get("adapterId") != thing:
        raise Refused(404, f"no camera {camera_id} on {thing}")
    if row.get("unregisteredAt"):
        return 200, {"cameraId": camera_id, "removed": True, "already": True}
    send(thing, {"action": "unregister", "camera": camera_id})
    return 202, {"cameraId": camera_id}


def reregister_camera(thing, body, claims, camera_id):
    row = table(CAMERAS_TABLE).get_item(Key={"cameraId": camera_id}).get("Item")
    if not row or row.get("adapterId") != thing:
        raise Refused(404, f"no camera {camera_id} on {thing}")
    if not row.get("unregisteredAt"):
        return 200, {"cameraId": camera_id, "registered": True, "already": True}
    send(thing, {"action": "reregister", "camera": camera_id})
    return 202, {"cameraId": camera_id}


ROUTES = [
    ("POST", rf"(?P<thing>{THING})/discover", discover),
    ("GET", rf"(?P<thing>{THING})/discovery", discovery),
    ("POST", rf"(?P<thing>{THING})/cameras", add_camera),
    ("GET", rf"(?P<thing>{THING})/cameras/requests/(?P<request_id>{UUID})", request_status),
    ("POST", rf"(?P<thing>{THING})/cameras/(?P<camera_id>cam-\d{{2,4}})/remove", remove_camera),
    ("POST", rf"(?P<thing>{THING})/cameras/(?P<camera_id>cam-\d{{2,4}})/reregister", reregister_camera),
]


def lambda_handler(event, context):
    try:
        event = event or {}
        claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
        if not claims.get("sub"):
            return answer(401, {"error": "no signed-in user"})
        method = event.get("httpMethod") or "GET"
        path = ((event.get("pathParameters") or {}).get("proxy") or "").strip("/")
        try:
            body = json.loads(event.get("body") or "{}")
        except ValueError:
            return answer(400, {"error": "the body isn't JSON"})
        if not isinstance(body, dict):
            return answer(400, {"error": "the body must be a JSON object"})
        allowed = []
        for route_method, pattern, handler in ROUTES:
            m = re.fullmatch(pattern, path)
            if not m:
                continue
            if route_method != method:
                allowed.append(route_method)
                continue
            params = m.groupdict()
            thing = params.pop("thing")
            if not controls(claims, thing):
                raise Refused(404, "no such adapter")
            status, out = handler(thing, body, claims, **params)
            return answer(status, out)
        if allowed:
            return answer(405, {"error": f"{method} isn't allowed here: {', '.join(sorted(set(allowed)))}"})
        return answer(404, {"error": f"no route /adapters/{path}"})
    except Refused as r:
        return answer(r.status, {"error": r.message})
    except Exception as exc:  # noqa: BLE001 -- the type only: a message could carry the request
        return answer(500, {"error": f"{type(exc).__name__}"})
