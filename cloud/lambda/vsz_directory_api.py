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

An invitation: vsz-directory adds the person, invited, with their membership; Cognito creates the
user and emails a temporary password (AdminCreateUser); the new sub goes back to the directory. If
Cognito refuses, the directory's row is taken back out. A disable is the directory's first --
the sign-in trigger refuses a disabled person at their next token, whatever Cognito says -- then
Cognito's, with every session signed out.

Every answer carries CORS headers, errors included (FoundAndFixed.md #8).
"""
import json
import os
import re

import boto3
from botocore.exceptions import ClientError

REGION = os.environ["AWS_REGION"]
POOL_ID = os.environ.get("POOL_ID", "")
DIRECTORY = os.environ.get("DIRECTORY_FUNCTION", "vsz-directory")
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
    ("GET", "customers", "list_customers"),
    ("GET", "adapters", "list_adapters"),
    ("GET", "users", "list_users"),
    ("POST", "users", "invite"),
    ("POST", f"users/(?P<userId>{ID})/(?P<action>{'|'.join(ACTIONS)})", "account"),
    ("GET", "pricing", "get_pricing"),
    ("PUT", "pricing", "put_pricing"),
]
CREATED = {"create_dealer", "invite"}


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
            if what == "invite":
                result = invite(sub, body)
            elif what == "account":
                result = account(sub, params["userId"], params["action"])
            elif method == "GET":
                result = call_directory(what, sub, {**{k: v for k, v in query.items() if k == "dealerId"}})
            else:
                result = call_directory(what, sub, {**body, **params})
            return answer(201 if what in CREATED else 200, result)
        if allowed:
            return answer(405, {"error": f"{method} isn't allowed here: {', '.join(sorted(set(allowed)))}"})
        return answer(404, {"error": f"no route /directory/{path}"})
    except Refused as r:
        return answer(r.status, {"error": r.message})
    except Exception as exc:
        return answer(500, {"error": f"{type(exc).__name__}: {exc}"})
