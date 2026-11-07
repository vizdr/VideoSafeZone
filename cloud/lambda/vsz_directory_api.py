"""vsz-directory-api: /directory/*, the directory for the web panel (DEALER-PANEL-PLAN.md Phase 4, D8).

Outside the VPC: it reaches Cognito and Lambda, never the database. For each call the caller is
the Cognito authorizer's verified token -- its sub, nothing else: vsz-directory looks the person up
and decides what they may do (D6), so a role in a stale token counts for nothing. A route becomes
one of vsz-directory's operations; invitations and account changes also go to Cognito.

  GET   /directory/me | overview | dealers | customers[?dealerId=] | adapters | users | pricing
  POST  /directory/dealers                    {name, email?, phone?}
  PATCH /directory/dealers/<id>               {name?, email?, phone?, status?}
  POST  /directory/users                      {email, name?, orgId, role} -- an invitation
  POST  /directory/users/<id>/disable | enable | reset-password | resend-invite
  PUT   /directory/pricing                    {prices: [...], settings: {...}}
  The dealer portal (Phase 5):
  POST  /directory/customers                  {name, reference?, email?, phone?, dealerId?,
                                               site: {name, timezone}, holder?: {email, name?}}
  GET   /directory/customers/<id> · PATCH     details · {name?, reference?, email?, phone?, status?}
  GET   /directory/customers/<id>/adapters    each adapter's plan, cameras and plan vs. actual; the total
  GET   /directory/customers/<id>/users       the customer's people
  PUT   /directory/adapters/<id>/plan         {lines, retentionDays, interval, basedOn} -- a new version
  POST  /directory/adapters/<id>/plan/cancel  {basedOn}
  GET   /directory/adapters/<id>/plans        the plan's history at its current customer
  GET   /directory/dealer · PATCH             the dealer's own details
  Adapters (Phase 6, D15):
  POST  /directory/adapters                   {count, label?, dealerId?} -- new adapters, their Cloud IDs once
  POST  /directory/adapters/activate          {cloudId, accountId? | customerId?, siteId? | newSite?, name?}
  POST  /directory/adapters/<id>/allocate     {dealerId | null}
  POST  /directory/adapters/<id>/new-code | release | remove | revoke | sync

An invitation: vsz-directory adds the person, invited, with their membership; Cognito creates the
user and emails a temporary password (AdminCreateUser); the new sub goes back to the directory. If
Cognito refuses, the directory's row is taken back out. A disable is the directory's first --
the sign-in trigger refuses a disabled person at their next token, whatever Cognito says -- then
Cognito's, with every session signed out.

The camera registry (DynamoDB `cameras`) is read here, since vsz-directory, inside the VPC,
can't reach it: the customer list's camera counts and each adapter's cameras (D5: name, codec,
picture size, recording mode, when the last thumbnail was taken -- never the picture, never a
credential: the scan reads only the fields below). A registry that can't be read leaves those out
and says so; the rest of the answer stands.

After an adapter's state changes, this function publishes its retained config message --
adapter/<thing>/config, {"activated": true|false} -- which the Pi reads at every connect (D15);
and when an adapter leaves its customer (release, remove, revoke), its camera rows lose adapterId
and get releasedFrom, so the next customer starts with none and never sees the last one's
recordings. Either can fail after the directory changed: the answer then says so (configUnsent,
registryError), and `sync` does both again. An adapter's AWS identity is the owner's script's,
cloud/adapter-identity.sh -- this function has no certificate permissions (Phase 6 amendment).

Every answer carries CORS headers, errors included (FoundAndFixed.md #8).
"""
import json
import os
import re
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

REGION = os.environ["AWS_REGION"]
POOL_ID = os.environ.get("POOL_ID", "")
DIRECTORY = os.environ.get("DIRECTORY_FUNCTION", "vsz-directory")
CAMERAS_TABLE = os.environ.get("CAMERAS_TABLE", "cameras")
THUMB_BUCKET = os.environ.get("THUMB_BUCKET", "")       # the evidence bucket: thumbs/<cameraId>.jpg
# The only registry fields read -- no credential is among them (no rtspUrl, onvifPassword, ...).
REGISTRY_FIELDS = "cameraId, adapterId, unregisteredAt, displayName, videoCodec, videoCodecDefault, recordSize, recordingMode"
CORS = {"Access-Control-Allow-Origin": "*"}
ID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
ACTIONS = {"disable": "disable", "enable": "enable", "reset-password": "reset", "resend-invite": "resend"}

# (method, path under /directory/, what to do)
ROUTES = [
    ("GET", "me", "me"),
    ("GET", "overview", "overview"),
    ("GET", "dealers", "list_dealers"),
    ("POST", "dealers", "create_dealer"),
    ("PATCH", f"dealers/(?P<id>{ID})", "update_dealer"),
    ("GET", "customers", "customers"),
    ("POST", "customers", "create_customer"),
    ("GET", f"customers/(?P<id>{ID})", "get_customer"),
    ("PATCH", f"customers/(?P<id>{ID})", "update_customer"),
    ("GET", f"customers/(?P<id>{ID})/adapters", "customer_adapters"),
    ("GET", f"customers/(?P<id>{ID})/users", "customer_users"),
    ("PUT", f"adapters/(?P<adapterId>{ID})/plan", "put_plan"),
    ("POST", f"adapters/(?P<adapterId>{ID})/plan/cancel", "cancel_plan"),
    ("GET", f"adapters/(?P<adapterId>{ID})/plans", "plan_history"),
    ("GET", "dealer", "get_dealer"),
    ("PATCH", "dealer", "update_own_dealer"),
    ("POST", "adapters", "generate"),
    ("POST", "adapters/activate", "activate"),
    ("POST", f"adapters/(?P<id>{ID})/allocate", "allocate_adapter"),
    ("POST", f"adapters/(?P<id>{ID})/new-code", "new_code"),
    ("POST", f"adapters/(?P<id>{ID})/release", "release"),
    ("POST", f"adapters/(?P<id>{ID})/remove", "remove"),
    ("POST", f"adapters/(?P<id>{ID})/revoke", "revoke"),
    ("POST", f"adapters/(?P<id>{ID})/sync", "sync"),
    ("GET", "adapters", "list_adapters"),
    ("GET", "users", "list_users"),
    ("POST", "users", "invite"),
    ("POST", f"users/(?P<userId>{ID})/(?P<action>{'|'.join(ACTIONS)})", "account"),
    ("GET", "pricing", "get_pricing"),
    ("PUT", "pricing", "put_pricing"),
]
CREATED = {"create_dealer", "invite", "create_customer", "generate"}


class Refused(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def answer(status, body):
    return {"statusCode": status, "headers": CORS, "body": json.dumps(body)}


def call_directory(op, sub, args=None):
    """vsz-directory's result for an operation asked as the signed-in person; Refused otherwise."""
    out = boto3.client("lambda", region_name=REGION).invoke(
        FunctionName=DIRECTORY,
        Payload=json.dumps({"op": op, "actor": {"kind": "user", "sub": sub}, "args": args or {}}).encode())
    payload = json.loads(out["Payload"].read() or b"{}")
    if out.get("FunctionError"):
        raise Refused(502, "the directory failed")
    if not payload.get("ok"):
        raise Refused(int(payload.get("status") or 500), payload.get("error") or "the directory refused")
    return payload["result"]


def error_code(exc):
    return exc.response.get("Error", {}).get("Code", "?") if isinstance(exc, ClientError) else type(exc).__name__


def invite(sub, args):
    made = call_directory("invite_user", sub, args)
    email = str(args.get("email") or "").strip()
    try:
        user = boto3.client("cognito-idp", region_name=REGION).admin_create_user(
            UserPoolId=POOL_ID, Username=email, DesiredDeliveryMediums=["EMAIL"],
            UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}])
    except Exception as exc:
        call_directory("cancel_invite", sub, {"userId": made["userId"]})
        if error_code(exc) == "UsernameExistsException":
            raise Refused(409, "can't add this address")            # never says whose it is
        raise Refused(502, f"Cognito didn't create the account ({error_code(exc)}); nothing was kept")
    new_sub = next((a["Value"] for a in user["User"]["Attributes"] if a["Name"] == "sub"), None)
    if new_sub:
        try:
            call_directory("link_sub", sub, {"userId": made["userId"], "sub": new_sub})
        except Refused:
            pass                 # the sign-in trigger links the sub by the verified email at first sign-in
    return {"userId": made["userId"], "invited": email}


def account(sub, user_id, action):
    done = call_directory("user_action", sub, {"userId": user_id, "action": ACTIONS[action]})
    username = done.get("sub") or done["email"]
    cognito = boto3.client("cognito-idp", region_name=REGION)
    try:
        if action == "disable":
            cognito.admin_disable_user(UserPoolId=POOL_ID, Username=username)
            cognito.admin_user_global_sign_out(UserPoolId=POOL_ID, Username=username)
        elif action == "enable":
            cognito.admin_enable_user(UserPoolId=POOL_ID, Username=username)
        elif action == "reset-password":
            cognito.admin_reset_user_password(UserPoolId=POOL_ID, Username=username)
        else:
            cognito.admin_create_user(UserPoolId=POOL_ID, Username=done["email"], MessageAction="RESEND",
                                      DesiredDeliveryMediums=["EMAIL"])
    except Exception as exc:
        note = " -- the directory has it disabled, so the sign-in is refused at the next token" \
            if action == "disable" else ""
        raise Refused(502, f"Cognito refused ({error_code(exc)}){note}")
    return {"userId": done["userId"], "status": done["status"]}


# --- the camera registry, for the portal's counts and inventory (D5) ---------------------------------

def registry_cameras():
    """Every registered camera's portal fields (REGISTRY_FIELDS), unregistered ones left out."""
    table = boto3.resource("dynamodb", region_name=REGION).Table(CAMERAS_TABLE)
    rows, kwargs = [], {"ProjectionExpression": REGISTRY_FIELDS}
    while True:
        page = table.scan(**kwargs)
        rows += page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            return [r for r in rows if not r.get("unregisteredAt")]
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def size_class(size):
    """A picture size's camera class (§7.1): up to 2.1 Mpx 2 MP, up to 4.2 Mpx 4 MP, else 8 MP."""
    m = re.fullmatch(r"(\d+)x(\d+)", str(size or ""))
    if not m:
        return None
    pixels = int(m.group(1)) * int(m.group(2))
    return "2mp" if pixels <= 2_100_000 else "4mp" if pixels <= 4_200_000 else "8mp"


def recording_class(mode):
    """Started and stopped by hand, a camera records all the time it runs; the detection modes
    record on events. A hint for plan vs. actual, not a rule (§7.1)."""
    return "continuous" if (mode or "manual") == "manual" else "motion"


def thumbnail_times(camera_ids):
    """{cameraId: when its last thumbnail was taken} -- the time only, never the picture (D5)."""
    if not THUMB_BUCKET:
        return {}
    s3 = boto3.client("s3", region_name=REGION)
    out = {}
    for cid in camera_ids:
        try:
            head = s3.head_object(Bucket=THUMB_BUCKET, Key=f"thumbs/{cid}.jpg")
        except Exception:  # noqa: BLE001 -- no thumbnail yet, or not readable: no time
            continue
        out[cid] = head["LastModified"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


def inventory(things):
    """{thing: [camera]} for these adapters' cameras, sorted by camera ID."""
    rows = sorted((r for r in registry_cameras() if r.get("adapterId") in set(things)), key=lambda r: r["cameraId"])
    taken = thumbnail_times([r["cameraId"] for r in rows])
    out = {t: [] for t in things}
    for r in rows:
        out[r["adapterId"]].append({
            "id": r["cameraId"], "name": r.get("displayName"),
            "codec": r.get("videoCodec") or r.get("videoCodecDefault") or "h264",
            "size": r.get("recordSize"), "class": size_class(r.get("recordSize")),
            "recordingMode": r.get("recordingMode") or "manual",
            "recording": recording_class(r.get("recordingMode")), "thumbnailAt": taken.get(r["cameraId"])})
    return out


def actual(cameras):
    """What is on the adapter, counted as a plan counts: by class/recording; a camera whose picture
    size isn't known is counted apart."""
    by_class = {}
    for c in cameras:
        if c["class"]:
            key = f"{c['class']}/{c['recording']}"
            by_class[key] = by_class.get(key, 0) + 1
    return {"total": len(cameras), "byClass": by_class, "unclassified": sum(1 for c in cameras if not c["class"])}


def registry_trouble(exc):
    return f"the camera registry couldn't be read ({error_code(exc)}): camera counts are left out"


def customers(sub, args):
    """The customer list, each with the cameras registered on its adapters."""
    rows = call_directory("list_customers", sub, args)
    try:
        counts = {}
        for r in registry_cameras():
            counts[r.get("adapterId")] = counts.get(r.get("adapterId"), 0) + 1
    except Exception as exc:  # noqa: BLE001 -- the list stands without the counts
        print(json.dumps({"registry": registry_trouble(exc)}))
        counts = None
    for c in rows:
        c["registered"] = None if counts is None else sum(counts.get(t, 0) for t in c.get("things", []))
    return rows


def customer_adapters(sub, args):
    """The customer's adapters, each with its plan (the directory's), its cameras and plan vs. actual."""
    out = call_directory("customer_adapters", sub, args)
    try:
        cams = inventory([a["thingName"] for a in out["adapters"]])
    except Exception as exc:  # noqa: BLE001
        out["registryError"] = registry_trouble(exc)
        cams = None
    for a in out["adapters"]:
        a["cameras"] = None if cams is None else cams[a["thingName"]]
        a["actual"] = None if cams is None else actual(a["cameras"])
    return out


def create_customer(sub, body):
    """A customer and its first site; then, if one is given, its holder's invitation. A refused
    invitation leaves the customer created and says why, so the holder can be invited again."""
    holder = body.get("holder")
    made = call_directory("create_customer", sub, {k: v for k, v in body.items() if k != "holder"})
    out = {"id": made["id"], "siteId": made["siteId"]}
    if isinstance(holder, dict) and str(holder.get("email") or "").strip():
        try:
            out["holder"] = invite(sub, {"email": holder["email"], "name": holder.get("name"),
                                         "orgId": made["id"], "role": "customer"})
        except Refused as r:
            out["holderError"] = r.message
    return out


# --- adapters: the retained config and the registry's view (Phase 6, D15) -----------------------------

def publish_config(thing, activated):
    """The adapter's retained config message: what the Pi reads whenever it connects."""
    boto3.client("iot-data", region_name=REGION).publish(
        topic=f"adapter/{thing}/config", qos=1, retain=True,
        payload=json.dumps({"activated": activated,
                            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}).encode())


def release_cameras(thing):
    """The adapter's camera rows leave its customer: adapterId removed, releasedFrom set (D15) -- so
    no customer's token reaches them, and the Pi's rematch never adopts them again (D12). Field by
    field and conditional, as the registry's contract asks. Returns how many."""
    table = boto3.resource("dynamodb", region_name=REGION).Table(CAMERAS_TABLE)
    released, kwargs = 0, {"ProjectionExpression": "cameraId, adapterId"}
    while True:
        page = table.scan(**kwargs)
        for row in page.get("Items", []):
            if row.get("adapterId") == thing:
                try:
                    table.update_item(Key={"cameraId": row["cameraId"]},
                                      UpdateExpression="SET releasedFrom = :t REMOVE adapterId",
                                      ConditionExpression="adapterId = :t", ExpressionAttributeValues={":t": thing})
                    released += 1
                except ClientError as exc:
                    if error_code(exc) != "ConditionalCheckFailedException":   # moved meanwhile: not ours
                        raise
        if "LastEvaluatedKey" not in page:
            return released
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _follow_up(out, thing, activated, release):
    """After the directory changed: the registry (when the adapter left a customer), then the config.
    A failure is reported, never undone: the directory's state is the one that holds."""
    if release:
        try:
            out["camerasReleased"] = release_cameras(thing)
        except Exception as exc:  # noqa: BLE001
            out["registryError"] = f"its cameras are still in the registry under {thing} ({error_code(exc)}): sync it"
    try:
        publish_config(thing, activated)
    except Exception as exc:  # noqa: BLE001
        out["configUnsent"] = f"the adapter wasn't told ({error_code(exc)}): sync it"
    return out


def generate(sub, args):
    made = call_directory("generate_adapters", sub, args)
    out = {"adapters": made,
           "identityCommand": "bash cloud/adapter-identity.sh create " + " ".join(m["thingName"] for m in made)}
    unsent = []
    for m in made:
        try:
            publish_config(m["thingName"], False)
        except Exception:  # noqa: BLE001
            unsent.append(m["thingName"])
    if unsent:
        out["configUnsent"] = f"not told yet that they aren't activated: {', '.join(unsent)} (sync them)"
    return out


def activate(sub, args):
    """The activation, then (a backstop) any camera rows still under this adapter from before are
    taken out of view, then the Pi is told -- before that, its admin GUI registers no camera."""
    done = call_directory("activate_adapter", sub, args)
    return _follow_up(dict(done), done["thingName"], True, release=True)


def release(sub, args):
    done = call_directory("release_adapter", sub, args)
    return _follow_up(dict(done), done["thingName"], False, release=True)


def remove(sub, args):
    done = call_directory("remove_adapter", sub, args)
    return _follow_up(dict(done), done["thingName"], False, release=True)


def revoke(sub, args):
    done = call_directory("revoke_adapter", sub, args)
    out = _follow_up(dict(done), done["thingName"], False, release=done["wasActivated"])
    if done.get("identity"):
        out["identityCommand"] = f"bash cloud/adapter-identity.sh revoke {done['thingName']}"
    return out


def sync(sub, args):
    """Both follow-ups again, from the directory's state: the config, and for an adapter that isn't
    activated, the registry's view of its cameras."""
    state = call_directory("adapter_state", sub, args)
    return _follow_up({"thingName": state["thingName"], "activated": state["activated"]},
                      state["thingName"], state["activated"], release=not state["activated"])


SPECIAL = {"customers": customers, "customer_adapters": customer_adapters, "create_customer": create_customer,
           "generate": generate, "activate": activate, "release": release, "remove": remove, "revoke": revoke,
           "sync": sync}


def lambda_handler(event, context):
    try:
        event = event or {}
        claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            return answer(401, {"error": "no signed-in user"})
        method = event.get("httpMethod") or "GET"
        path = ((event.get("pathParameters") or {}).get("proxy") or "").strip("/")
        try:
            body = json.loads(event.get("body") or "{}")
        except ValueError:
            return answer(400, {"error": "the body isn't JSON"})
        if not isinstance(body, dict):
            return answer(400, {"error": "the body must be a JSON object"})
        query = event.get("queryStringParameters") or {}
        allowed = []
        for route_method, pattern, what in ROUTES:
            m = re.fullmatch(pattern, path)
            if not m:
                continue
            if route_method != method:
                allowed.append(route_method)
                continue
            params = m.groupdict()
            args = {**{k: v for k, v in query.items() if k == "dealerId"}, **params} if method == "GET" \
                else {**body, **params}
            if what == "activate":            # the rate limit's address is API Gateway's, never the body's
                args["sourceIp"] = ((event.get("requestContext") or {}).get("identity") or {}).get("sourceIp")
            if what == "invite":
                result = invite(sub, body)
            elif what == "account":
                result = account(sub, params["userId"], params["action"])
            elif what in SPECIAL:
                result = SPECIAL[what](sub, args)
            else:
                result = call_directory(what, sub, args)
            return answer(201 if what in CREATED else 200, result)
        if allowed:
            return answer(405, {"error": f"{method} isn't allowed here: {', '.join(sorted(set(allowed)))}"})
        return answer(404, {"error": f"no route /directory/{path}"})
    except Refused as r:
        return answer(r.status, {"error": r.message})
    except Exception as exc:
        return answer(500, {"error": f"{type(exc).__name__}: {exc}"})
