import boto3, os, json, re

REGION = os.environ["AWS_REGION"]
CORS = {"Access-Control-Allow-Origin": "*"}

# POST /cameras/settings: the per-camera settings the web panel added (WEB-PANEL-PLAN.md D7, §5.1)
# -- tags, the camera schedule, and the recorded-quality choice below the admin GUI's ceiling.
# Body {cameraId, tags?, schedule?, recordQuality?}. Each field is validated and written on its
# own -- SET, or REMOVE when emptied -- because the registry is shared with VMS and updated field
# by field, never overwritten whole. VMS reads none of these fields.
cameras_table = boto3.resource("dynamodb", region_name=REGION).Table("cameras")

VALID_MODES = {"manual", "motion", "cellMotion", "human"}    # set_camera_mode.py
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")      # client-app/src/cameras/schedule.ts
TAG = re.compile(r"^[A-Za-z0-9 _-]{1,24}$")
MAX_TAGS = 10
MAX_ENTRIES = 50
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# An IANA zone name, by shape: the Pi checks it against its zone database when it runs the
# schedule (Phase 5). Lambda's own zone data isn't relied on.
TZ = re.compile(r"^(UTC|[A-Za-z]+(/[A-Za-z0-9_+-]+){1,2})$")


def unregistered_response(camera_id, item):
    """409 for a camera unregistered in an admin GUI, else None. The registry is shared with VMS:
    list-cameras no longer shows such a camera, but an open page or a direct API call can still
    name it (VMS-PORT-PLAN.md D4). Same check in every Lambda that acts on a camera."""
    if item and item.get("unregisteredAt"):
        return {"statusCode": 409, "headers": CORS, "body": json.dumps(
            {"error": f"'{camera_id}' is unregistered: re-register it in the admin GUI first"})}
    return None


def respond(status, body):
    return {"statusCode": status, "headers": CORS, "body": json.dumps(body)}


# Each check returns None when the value may be written, else (status, message).

def check_tags(v, item):
    if v is None:
        return None
    if not isinstance(v, list) or len(v) > MAX_TAGS:
        return 400, f"tags must be a list of at most {MAX_TAGS}"
    if not all(isinstance(t, str) and TAG.match(t) and t == t.strip() for t in v):
        return 400, "each tag is 1-24 letters, digits, spaces, - or _, with no space at either end"
    if len({t.lower() for t in v}) != len(v):
        return 400, "tags must not repeat"
    return None


def check_mode(mode, item):
    # The same rules as set_camera_mode.py: a detection mode needs ONVIF, and one the camera's
    # event list doesn't offer would never record anything (FoundAndFixed.md #65).
    if mode not in VALID_MODES:
        return 400, f"the mode must be one of {sorted(VALID_MODES)}"
    if mode != "manual" and not item.get("onvifHost"):
        return 400, f"'{item['cameraId']}' has no ONVIF host, so it supports only 'manual'"
    supported = item.get("detectionModes")
    if mode != "manual" and supported is not None and mode not in supported:
        return 409, f"'{item['cameraId']}' doesn't publish the events for '{mode}'"
    return None


def check_schedule(v, item):
    if v is None:
        return None
    if not isinstance(v, dict) or set(v) != {"tz", "entries"}:
        return 400, "schedule must be {tz, entries}"
    if not isinstance(v["tz"], str) or not TZ.match(v["tz"]):
        return 400, "schedule.tz must be a time zone name such as Europe/Berlin"
    entries = v["entries"]
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        return 400, f"schedule.entries must be a list of at most {MAX_ENTRIES}"
    taken = set()
    for e in entries:
        if not isinstance(e, dict) or set(e) != {"days", "at", "action"}:
            return 400, "each schedule entry is {days, at, action}"
        days, at, action = e["days"], e["at"], e["action"]
        if (not isinstance(days, list) or not days or len(set(days)) != len(days)
                or not all(d in DAYS for d in days)):
            return 400, f"days must be some of {list(DAYS)}, each once"
        if not isinstance(at, str) or not TIME.match(at):
            return 400, "at must be a 24-hour time, HH:MM"
        if action in ("start", "stop"):
            kind = "recording"
        elif isinstance(action, str) and action.startswith("mode:"):
            kind = "mode"
            if (err := check_mode(action[len("mode:"):], item)) is not None:
                return err
        else:
            return 400, 'action must be "start", "stop" or "mode:<recordingMode>"'
        # Start and Stop at the same moment would race on the Pi; so would two mode changes.
        for d in days:
            if (d, at, kind) in taken:
                return 400, f"two entries change the {kind} at {d} {at}"
            taken.add((d, at, kind))
    return None


def check_quality(v, item):
    # The admin GUI's choice is the ceiling; the cloud may only go lower, and the one lower step
    # is the sub stream, offered when the camera records main and has one (D5). null, or the
    # ceiling itself, means no choice.
    if v is None:
        return None
    if not isinstance(v, dict) or set(v) != {"stream"}:
        return 400, 'recordQuality must be {"stream": ...} or null'
    ceiling = item.get("recordStream")
    if v["stream"] == ceiling:
        return None
    if v["stream"] == "sub" and ceiling == "main" and item.get("liveRtspUrl"):
        return None
    return 400, (f"'{v['stream']}' isn't offered for '{item['cameraId']}': the most it records is "
                 f"{ceiling or 'not known yet'} (set in the admin GUI), and only the sub stream is below main")


FIELDS = {"tags": check_tags, "schedule": check_schedule, "recordQuality": check_quality}


def empty(name, value, item):
    """A value that clears the field rather than storing it."""
    if value is None:
        return True
    if name == "tags":
        return value == []
    if name == "schedule":
        return value["entries"] == []
    return value["stream"] == item.get("recordStream")       # recordQuality at the ceiling


def lambda_handler(event, context):
    # Every path returns through respond(), with CORS (FoundAndFixed.md #8).
    try:
        try:
            body = json.loads(event.get("body") or "{}")
        except ValueError:
            return respond(400, {"error": "the body must be JSON"})
        if not isinstance(body, dict):
            return respond(400, {"error": "the body must be an object"})
        camera_id = body.pop("cameraId", None)
        if not body:
            return respond(400, {"error": f"give cameraId and at least one of {sorted(FIELDS)}"})
        unknown = sorted(set(body) - set(FIELDS))
        if unknown:
            return respond(400, {"error": f"unknown setting(s): {', '.join(unknown)}"})

        item = cameras_table.get_item(Key={"cameraId": camera_id}).get("Item") if isinstance(camera_id, str) else None
        if not item:
            return respond(400, {"error": f"unknown camera '{camera_id}'"})
        if r := unregistered_response(camera_id, item):
            return r
        for name, value in body.items():
            if (err := FIELDS[name](value, item)) is not None:
                return respond(err[0], {"error": err[1]})

        sets = {k: v for k, v in body.items() if not empty(k, v, item)}
        removes = [k for k, v in body.items() if empty(k, v, item)]
        parts = []
        if sets:
            parts.append("SET " + ", ".join(f"#{k} = :{k}" for k in sets))
        if removes:
            parts.append("REMOVE " + ", ".join(f"#{k}" for k in removes))
        kwargs = {"ExpressionAttributeNames": {f"#{k}": k for k in body}}
        if sets:
            kwargs["ExpressionAttributeValues"] = {f":{k}": v for k, v in sets.items()}
        cameras_table.update_item(Key={"cameraId": camera_id}, UpdateExpression=" ".join(parts), **kwargs)
        return respond(200, {"cameraId": camera_id, "saved": {k: sets.get(k) for k in body}})
    except Exception as e:
        return respond(500, {"error": str(e)})
