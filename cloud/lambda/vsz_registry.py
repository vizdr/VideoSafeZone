"""vsz-registry: the camera registry's writes from the adapters (DEALER-PANEL-PLAN.md D13 as amended
2026-11-15, Phase 10.2b).

An adapter writes the `cameras` table through here instead of directly, so that once the device role
is narrowed (10.2f) no adapter can change another adapter's rows: IAM can't limit a table write to the
rows of one Thing, but it can limit which path of an API a device may call. The route is
/registry/{proxy+} on kvs-demo-api with IAM authorization. A Pi signs each call with its own device
credentials, and the device role allows `execute-api:Invoke` only on
`.../registry/${credentials-iot:ThingName}/*` -- so the Thing in the path is the caller's, IAM has
checked it, and this function writes only that Thing's rows. A call from anything but the device role
is refused, in case the route were ever left open by mistake.

  GET  /registry/<thing>/ping                        who is asking: a check, writes nothing
  POST /registry/<thing>/cameras/claim               {cameraId?} -- a camera ID claimed for a registration:
                                                     the one given, or the next free one past every
                                                     adapter's (D11) -> {cameraId, token}
  POST /registry/<thing>/cameras/<cameraId>          {set?, remove?, expect?, exists?, missing?} -- fields
                                                     of one of this Thing's rows, under conditions
  POST /registry/<thing>/cameras/<cameraId>/adopt    stamp an unstamped row with this Thing -- the
                                                     adopting adapter only (D12)
  POST /registry/<thing>/clips                       {item} -- a clip row of one of this Thing's cameras
  POST /registry/<thing>/devices                     {endpointRefs} -- which belong to another adapter's
                                                     cameras (the scan's mark, D16)
  GET  /registry/<thing>/requests/<requestId>        a camera request of the page's Add cameras for this
                                                     Thing, password included (D16; Phase 10.2f)

A row is this Thing's when its adapterId says so; a row without one is the adopting adapter's
(ADOPTING_THING, adapter-01), unless it was released (camera_control.is_mine on the Pi). Never through
here: a row's key, or whose it is (adapterId, releasedFrom -- the directory API's, D15); a claim token
(`registering`) is set only by /claim. Completing a registration and abandoning a claim are field
updates that expect the token. Values are written as given -- the data contract's field-by-field rule
is the caller's -- and nothing here logs a value: rows, and requests, carry camera passwords.
"""
import json
import os
import re
import secrets
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

REGION = os.environ["AWS_REGION"]
CAMERAS_TABLE = os.environ.get("CAMERAS_TABLE", "cameras")
REQUESTS_TABLE = os.environ.get("REQUESTS_TABLE", "camera-requests")
# "keep": rows hold the camera secrets as well as SSM, while VMS's card still reads them there; "drop"
# once it doesn't (DEALER-PANEL-PLAN.md Phase 10.2f, set by provision.sh narrow). Anything else refuses.
ROW_SECRETS = os.environ.get("ROW_SECRETS", "keep")
SECRET_FIELDS = ("onvifPassword", "rtspUrl", "liveRtspUrl")      # camera_secrets.FIELDS on the Pi
CLIPS_TABLE = os.environ.get("CLIPS_TABLE", "clips")
ADOPTING_THING = os.environ.get("ADOPTING_THING", "")       # adopts unstamped rows (D12): adapter-01
DEVICE_ROLE = os.environ.get("DEVICE_ROLE", "KVSAdapterRole")
CORS = {"Access-Control-Allow-Origin": "*"}
THING = re.compile(r"^adapter-[0-9]{2,6}$")
CAMERA = re.compile(r"^cam-[0-9]{2,4}$")
REQUEST = re.compile(r"^[0-9a-f-]{8,64}$")    # adapter-cameras' request IDs (agent.REQUEST_ID_RE)
FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
OWNERSHIP = {"cameraId", "adapterId", "releasedFrom"}       # the key, and whose the row is
NEVER_SET = OWNERSHIP | {"registering"}                     # a claim is made by /claim alone
MAX_FIELDS = 100
CLIP_REQUIRED = {"cameraId", "startTs", "s3Key", "labels", "durationSec"}
CLIP_OPTIONAL = {"videoCodec"}


class Refused(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def registry_answer(status, body):
    return {"statusCode": status, "headers": CORS, "body": json.dumps(body)}


def table(name):
    return boto3.resource("dynamodb", region_name=REGION).Table(name)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def failed_condition(exc):
    return isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def from_a_device(event):
    """Whether the call was signed by the adapters' role, as IAM authorization reports it."""
    arn = ((event.get("requestContext") or {}).get("identity") or {}).get("userArn") or ""
    return re.fullmatch(rf"arn:aws:sts::[0-9]{{12}}:assumed-role/{re.escape(DEVICE_ROLE)}/[^/]+", arn) is not None


def is_mine(row, thing):
    owner = row.get("adapterId")
    if owner:
        return owner == thing
    return thing == ADOPTING_THING and not row.get("releasedFrom")


def _fields(given, what, kind):
    if not isinstance(given, kind):
        raise Refused(400, f"{what} must be {'an object' if kind is dict else 'a list'}")
    for name in given:
        if not isinstance(name, str) or not FIELD.match(name):
            raise Refused(400, f"{what}: a field name is a letter, then letters, digits or _")
    return given


class Expression:
    """Names and values for one DynamoDB call, every field through a placeholder (reserved words)."""

    def __init__(self):
        self.names, self.values = {}, {}

    def name(self, field):
        key = f"#f{len(self.names)}"
        self.names[key] = field
        return key

    def value(self, value):
        key = f":v{len(self.values)}"
        self.values[key] = value
        return key


# --- the routes ---------------------------------------------------------------------------------------

def who_asks(thing, camera, body):
    return 200, {"thing": thing, "adopting": thing == ADOPTING_THING}


def update(thing, camera, body):
    """Fields of one of the Thing's rows: SET and REMOVE, under equality and presence conditions. For
    the adopting adapter a row without an owner counts too, tried as a second write: one condition
    with an OR would read the same, but every write here keeps to conditions the tests can check."""
    sets = _fields(body.get("set") or {}, "set", dict)
    removes = _fields(body.get("remove") or [], "remove", list)
    expect = _fields(body.get("expect") or {}, "expect", dict)
    exists = _fields(body.get("exists") or [], "exists", list)
    missing = _fields(body.get("missing") or [], "missing", list)
    if not sets and not removes:
        raise Refused(400, "give set or remove")
    if len(sets) + len(removes) > MAX_FIELDS:
        raise Refused(400, f"at most {MAX_FIELDS} fields at once")
    if set(sets) & set(removes):
        raise Refused(400, "a field is either set or removed")
    if set(sets) & NEVER_SET or set(removes) & OWNERSHIP:
        raise Refused(403, f"never from an adapter: {', '.join(sorted((set(sets) & NEVER_SET) | (set(removes) & OWNERSHIP)))}")
    if any(v is None for v in expect.values()):
        raise Refused(400, "expect a value; for an absent field use missing")
    if ROW_SECRETS not in ("keep", "drop"):
        raise Refused(500, "ROW_SECRETS must be keep or drop")
    if ROW_SECRETS == "drop":
        # The rows hold no camera secrets any more and never get them back: the Pi keeps them in SSM
        # (registry_client.update writes them there first), so here a secret field is left out -- and
        # every write takes away whatever secrets a row still holds (camera_secrets.py strip relies on that).
        if {*expect, *exists, *missing} & {*SECRET_FIELDS}:
            raise Refused(400, "no condition on a camera secret: the rows hold none")
        sets = {k: v for k, v in sets.items() if k not in SECRET_FIELDS}
        removes = [*(k for k in removes if k not in SECRET_FIELDS), *SECRET_FIELDS]

    def write(owned):
        e = Expression()
        parts = []
        if sets:
            parts.append("SET " + ", ".join(f"{e.name(k)} = {e.value(v)}" for k, v in sets.items()))
        if removes:
            parts.append("REMOVE " + ", ".join(e.name(k) for k in removes))
        terms = (owned(e) + [f"{e.name(k)} = {e.value(v)}" for k, v in expect.items()]
                 + [f"attribute_exists({e.name(k)})" for k in exists]
                 + [f"attribute_not_exists({e.name(k)})" for k in missing])
        extra = {"ExpressionAttributeValues": e.values} if e.values else {}     # boto3 refuses an empty one
        table(CAMERAS_TABLE).update_item(Key={"cameraId": camera}, UpdateExpression=" ".join(parts),
                                         ConditionExpression=" AND ".join(terms), ExpressionAttributeNames=e.names,
                                         **extra)

    attempts = [lambda e: [f"{e.name('adapterId')} = {e.value(thing)}"]]
    if thing == ADOPTING_THING:            # an unstamped row -- and never a new one (attribute_exists)
        attempts.append(lambda e: [f"attribute_exists({e.name('cameraId')})", f"attribute_not_exists({e.name('adapterId')})",
                                   f"attribute_not_exists({e.name('releasedFrom')})"])
    for owned in attempts:
        try:
            write(owned)
            return 200, {"updated": camera}
        except ClientError as exc:
            if not failed_condition(exc):
                raise
    row = table(CAMERAS_TABLE).get_item(Key={"cameraId": camera}).get("Item")
    if not row or not is_mine(row, thing):
        raise Refused(404, f"no camera {camera} on {thing}")      # another adapter's: says nothing more
    raise Refused(409, "the row doesn't meet the condition: it changed meanwhile")


def next_free(t):
    """One past the highest camera ID of every adapter's rows (D11); unregistered and abandoned ones
    count, since their streams and recordings keep their IDs."""
    taken, kw = [0], {"ProjectionExpression": "cameraId"}
    while True:
        page = t.scan(**kw)
        taken += [int(m.group(1)) for i in page.get("Items", []) if (m := re.fullmatch(r"cam-([0-9]{2,4})", i["cameraId"]))]
        if "LastEvaluatedKey" not in page:
            break
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    n = max(taken) + 1
    if n > 9999:
        raise Refused(409, "no camera ID left (cam-9999 is the last)")
    return f"cam-{n:02d}"


def claim(thing, camera, body):
    """A camera ID for a registration (D11): a row of its own, marked unregistered so that every reader
    skips it until the registration writes the camera over it (expecting the token). A given ID taken
    is refused; an allocated one taken meanwhile is followed by the next free one."""
    wanted = body.get("cameraId")
    if wanted is not None and (not isinstance(wanted, str) or not CAMERA.match(wanted)):
        raise Refused(400, "cameraId: cam-01 ... cam-9999")
    t, token = table(CAMERAS_TABLE), secrets.token_hex(8)
    camera_id = wanted or next_free(t)
    for _ in range(5):
        try:
            t.put_item(Item={"cameraId": camera_id, "adapterId": thing, "unregisteredAt": now(), "registering": token},
                       ConditionExpression="attribute_not_exists(cameraId)")
            return 201, {"cameraId": camera_id, "token": token}
        except ClientError as exc:
            if not failed_condition(exc):
                raise
        if wanted:
            raise Refused(409, f"{camera_id} is taken -- leave the camera ID out and the next free one is used")
        camera_id = next_free(t)
    raise Refused(409, "couldn't claim a camera ID: registrations keep taking them -- try again")


def adopt(thing, camera, body):
    """Stamp a row without an owner with the adopting adapter's Thing (D12). Never a released row, and
    never a row that isn't a camera -- one with no mode (FoundAndFixed.md #93)."""
    if thing != ADOPTING_THING:
        raise Refused(403, f"{thing} adopts no rows: only the adopting adapter does (D12)")
    try:
        table(CAMERAS_TABLE).update_item(
            Key={"cameraId": camera}, UpdateExpression="SET adapterId = :t",
            ConditionExpression="attribute_not_exists(adapterId) AND attribute_not_exists(releasedFrom) AND attribute_exists(#m)",
            ExpressionAttributeNames={"#m": "mode"}, ExpressionAttributeValues={":t": thing})
    except ClientError as exc:
        if not failed_condition(exc):
            raise
        raise Refused(409, "stamped meanwhile, released, or not a camera -- left as it is")
    return 200, {"adopted": camera}


def clip(thing, camera, body):
    """A clip row (the outage uploader's backfill) for one of the Thing's cameras; never over another."""
    item = body.get("item")
    if not isinstance(item, dict) or not CLIP_REQUIRED <= set(item) or set(item) - CLIP_REQUIRED - CLIP_OPTIONAL:
        raise Refused(400, f"item: {', '.join(sorted(CLIP_REQUIRED))}, and videoCodec if known")
    cid = item["cameraId"]
    if not isinstance(cid, str) or not CAMERA.match(cid) or not isinstance(item["startTs"], str):
        raise Refused(400, "item: a camera ID and a start time")
    # Its own camera's key, under its own Thing: clips/<cameraId>/<thing>/... (Phase 10.2e) -- the
    # camera where every clip reader takes it from.
    parts = item["s3Key"].split("/") if isinstance(item["s3Key"], str) else []
    if len(parts) < 4 or parts[0] != "clips" or parts[1] != cid or parts[2] != thing:
        raise Refused(400, f"item: the clip's key is under clips/{cid}/{thing}/")
    row = table(CAMERAS_TABLE).get_item(Key={"cameraId": cid}).get("Item")
    if not row or not is_mine(row, thing):
        raise Refused(404, f"no camera {cid} on {thing}")
    try:
        table(CLIPS_TABLE).put_item(Item=item, ConditionExpression="attribute_not_exists(startTs)")
    except ClientError as exc:
        if not failed_condition(exc):
            raise
        raise Refused(409, "a clip with that start time is there already -- not overwritten")
    return 201, {"clip": {"cameraId": cid, "startTs": item["startTs"]}}


def devices(thing, camera, body):
    """Which of these WS-Discovery identities are cameras of another adapter -- the scan marks them and
    names no camera (D16) -- since a Pi will read only its own rows (10.2c)."""
    refs = body.get("endpointRefs")
    if not isinstance(refs, list) or len(refs) > 200 or not all(isinstance(r, str) for r in refs):
        raise Refused(400, "endpointRefs: a list of at most 200 identities")
    wanted, rows = set(refs), []
    kw = {"ProjectionExpression": "cameraId, adapterId, releasedFrom, onvifEndpointRef"}
    t = table(CAMERAS_TABLE)
    while True:
        page = t.scan(**kw)
        rows += page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            break
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return 200, {"elsewhere": sorted({r["onvifEndpointRef"] for r in rows
                                      if r.get("onvifEndpointRef") in wanted and not is_mine(r, thing)})}


REQUEST_FIELDS = ("requestId", "thing", "xaddr", "endpointRef", "user", "password", "displayName")


def camera_request(thing, request_id, body):
    """A camera request of the web page's Add cameras (D16), for the agent of the adapter it is for:
    the password it carries reaches that adapter only, and the device role needs no read of the table
    (DEALER-PANEL-PLAN.md Phase 10.2f). Another adapter's request is no request."""
    if not REQUEST.match(request_id or ""):
        raise Refused(400, "a request ID")
    item = table(REQUESTS_TABLE).get_item(Key={"requestId": request_id}).get("Item")
    if not item or item.get("thing") != thing:
        raise Refused(404, "no such request for this adapter")
    return 200, {"request": {k: item[k] for k in REQUEST_FIELDS if k in item}}


ROUTES = [
    ("GET", r"(?P<thing>[^/]+)/ping", who_asks),
    ("POST", r"(?P<thing>[^/]+)/cameras/claim", claim),
    ("POST", r"(?P<thing>[^/]+)/cameras/(?P<camera>cam-[0-9]{2,4})/adopt", adopt),
    ("POST", r"(?P<thing>[^/]+)/cameras/(?P<camera>cam-[0-9]{2,4})", update),
    ("POST", r"(?P<thing>[^/]+)/clips", clip),
    ("POST", r"(?P<thing>[^/]+)/devices", devices),
    ("GET", r"(?P<thing>[^/]+)/requests/(?P<request>[^/]+)", camera_request),
]


def lambda_handler(event, context):
    try:
        event = event or {}
        if not from_a_device(event):
            return registry_answer(403, {"error": "the registry route answers adapters only"})
        method = event.get("httpMethod") or "GET"
        path = ((event.get("pathParameters") or {}).get("proxy") or "").strip("/")
        try:
            body = json.loads(event.get("body") or "{}", parse_float=Decimal)
        except ValueError:
            return registry_answer(400, {"error": "the body isn't JSON"})
        if not isinstance(body, dict):
            return registry_answer(400, {"error": "the body must be a JSON object"})
        allowed = []
        for route_method, pattern, fn in ROUTES:
            m = re.fullmatch(pattern, path)
            if not m:
                continue
            if route_method != method:
                allowed.append(route_method)
                continue
            if not THING.match(m["thing"]):
                return registry_answer(404, {"error": f"no adapter {m['thing']}"})
            status, out = fn(m["thing"], m.groupdict().get("camera") or m.groupdict().get("request"), body)
            return registry_answer(status, out)
        if allowed:
            return registry_answer(405, {"error": f"{method} isn't allowed here: {', '.join(sorted(set(allowed)))}"})
        return registry_answer(404, {"error": f"no route /registry/{path}"})
    except Refused as r:
        return registry_answer(r.status, {"error": r.message})
    except Exception as exc:  # noqa: BLE001 -- the error's type and message only, never a value written
        return registry_answer(500, {"error": f"{type(exc).__name__}: {exc}"})
