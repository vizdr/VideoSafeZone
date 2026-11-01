import boto3, os, json, re
from datetime import datetime, timezone

REGION = os.environ["AWS_REGION"]
CORS = {"Access-Control-Allow-Origin": "*"}

# GET /me/settings and PUT /me/settings: the signed-in user's own settings for the web panel
# (WEB-PANEL-PLAN.md D4, §5.2) -- so far the Live layout and its large-cell cameras, which follow
# the account from browser to browser. One item per user in the table `user-settings`.
#
# Whose settings: always the `sub` of the Cognito token the API Gateway authorizer checked, never
# anything in the request, so one user can neither read nor write another's.
settings_table = boto3.resource("dynamodb", region_name=REGION).Table("user-settings")

LAYOUTS = {"2+6", "1+12", "16"}            # client-app/src/layouts/grid.ts
CAMERA_ID = re.compile(r"^[A-Za-z0-9-]{1,32}$")
MAX_LARGE_CELLS = 2                         # the most any layout has
MAX_BODY_BYTES = 4096


def _layout(v):
    return None if v in LAYOUTS else f"liveLayout must be one of {sorted(LAYOUTS)}"


def _large_cells(v):
    # Camera IDs, or "" for "the next camera in list order". Whether a camera still exists is
    # the page's business: it has the camera list, and a pick for a camera that is gone is simply
    # dropped there, so this route needs no access to the registry.
    if (not isinstance(v, list) or len(v) > MAX_LARGE_CELLS
            or not all(isinstance(x, str) and (x == "" or CAMERA_ID.match(x)) for x in v)):
        return f"largeCells must be a list of at most {MAX_LARGE_CELLS} camera IDs"
    return None


FIELDS = {"liveLayout": _layout, "largeCells": _large_cells}


def respond(status, body):
    return {"statusCode": status, "headers": CORS, "body": json.dumps(body)}


def user_id(event):
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    sub = claims.get("sub")
    return sub if isinstance(sub, str) and sub else None


def lambda_handler(event, context):
    # Every path returns through respond(), with CORS: API Gateway's own error response has none,
    # and the browser then can't read the answer at all (FoundAndFixed.md #8).
    try:
        uid = user_id(event)
        if not uid:     # the authorizer always supplies one; a test-invoke or a direct call doesn't
            return respond(401, {"error": "no signed-in user"})

        method = event.get("httpMethod")
        if method == "GET":
            item = settings_table.get_item(Key={"userId": uid}).get("Item") or {}
            return respond(200, {"settings": {k: item[k] for k in FIELDS if k in item}})

        if method == "PUT":
            raw = event.get("body") or ""
            if len(raw.encode()) > MAX_BODY_BYTES:
                return respond(413, {"error": f"settings are limited to {MAX_BODY_BYTES} bytes"})
            try:
                body = json.loads(raw or "{}")
            except ValueError:
                return respond(400, {"error": "the body must be JSON"})
            if not isinstance(body, dict) or not body:
                return respond(400, {"error": f"give at least one of {sorted(FIELDS)}"})
            unknown = sorted(set(body) - set(FIELDS))
            if unknown:
                return respond(400, {"error": f"unknown setting(s): {', '.join(unknown)}"})
            for name, value in body.items():
                if (err := FIELDS[name](value)) is not None:
                    return respond(400, {"error": err})

            # Field by field, so a setting this request doesn't name is left as it was -- a page
            # that knows only some settings never erases the others.
            names = {f"#{k}": k for k in body}
            values = {f":{k}": v for k, v in body.items()}
            values[":t"] = datetime.now(timezone.utc).isoformat()
            settings_table.update_item(
                Key={"userId": uid},
                UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in body) + ", updatedAt = :t",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            return respond(200, {"settings": body})

        return respond(405, {"error": "use GET or PUT"})
    except Exception as e:
        return respond(500, {"error": str(e)})
