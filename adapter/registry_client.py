"""The registry route, from the Pi (DEALER-PANEL-PLAN.md D13 as amended 2026-11-15, Phase 10.2b).

This adapter's writes to the camera registry go to /registry/<this Thing>/... on kvs-demo-api, signed
with its own device credentials (SigV4 for execute-api, from aws_device_creds) -- outbound HTTPS, like
everything else the Pi does. IAM lets a Pi call only its own Thing's path, and the function behind it
(cloud/lambda/vsz_registry.py) writes only that Thing's rows; so once the device role loses its direct
table writes (10.2f), no adapter can change another's cameras.

The route's address is REGISTRY_URL in /etc/adapter/adapter.env (the API's stage URL), read when a call
is made, so a Pi without it runs on until something needs the route.

The writers call update(), claim(), adopt(), add_clip() and elsewhere() (10.2b's second half); each
raises RegistryError when the route refuses or can't be reached -- ConditionFailed (409: the row changed
meanwhile, or a claim was taken) and NotMine (404: no such camera of this adapter) by name, so a caller
handles exactly the refusals it expects.

  venv-adapter/bin/python3 adapter/registry_client.py check [cameraId]
      the route answers this Pi; another Thing's path is refused by IAM; one harmless write -- the time
      of the check, registryCheckedAt, on one of this Pi's cameras (the first, unless one is named)
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402

TIMEOUT = 15


class RegistryError(Exception):
    """The route refused or failed: the HTTP status (None: it couldn't be reached) and its message."""

    def __init__(self, status, message):
        super().__init__(f"{status}: {message}" if status else message)
        self.status, self.message = status, message


class ConditionFailed(RegistryError):
    """409: the row didn't meet the write's condition (it changed meanwhile), or a given camera ID is taken."""


class NotMine(RegistryError):
    """404: no such camera of this adapter -- another adapter's, or none at all."""


def _jsonable(value):
    """Decimals come from DynamoDB reads; the route reads numbers back exactly (parse_float=Decimal)."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"{type(value).__name__} can't go to the registry route")


def _open(request):
    """The one place the route is reached -- tests put the real vsz-registry behind it (tests/fakes.py)."""
    return urllib.request.urlopen(request, timeout=TIMEOUT)


def call(method, path, body=None, thing=None, session=None):
    """(status, answer) of one signed call to /registry/<thing>/<path>; `thing` other than this Pi's is
    for the check alone -- IAM refuses it."""
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    if session is None:
        from aws_device_creds import get_session
        session = get_session()
    url = f"{config.get('REGISTRY_URL').rstrip('/')}/registry/{thing or config.THING_NAME}/{path.lstrip('/')}"
    data = None if body is None else json.dumps(body, default=_jsonable).encode()
    signed = AWSRequest(method=method, url=url, data=data,
                        headers={"Content-Type": "application/json"} if data is not None else {})
    SigV4Auth(session.get_credentials().get_frozen_credentials(), "execute-api", config.AWS_REGION).add_auth(signed)
    request = urllib.request.Request(url, data=data, method=method, headers=dict(signed.headers.items()))
    try:
        with _open(request) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except ValueError:
            return e.code, {"message": raw.decode(errors="replace")[:200]}
    except (urllib.error.URLError, OSError) as e:          # offline, DNS, a timeout: no answer at all
        raise RegistryError(None, f"the registry route can't be reached ({type(e).__name__}: {e})") from e


def _answer(status, out):
    if 200 <= status < 300:
        return out
    message = out.get("error") or out.get("message") or out.get("Message") or ""
    raise (ConditionFailed if status == 409 else NotMine if status == 404 else RegistryError)(status, message)


def update(camera_id, set=None, remove=None, expect=None, exists=None, missing=None, session=None):
    """Fields of one of this adapter's rows: `set` {field: value}, `remove` [field], under the conditions
    `expect` {field: value}, `exists` [field] and `missing` [field]. Field by field, as the data contract
    asks; never whose the row is. A camera secret among them (the ONVIF password, the RTSP URIs) is then
    written to SSM as well (camera_secrets.keep_beside, Phase 10.2d) -- every writer on the Pi comes here."""
    body = {k: v for k, v in (("set", set), ("remove", remove), ("expect", expect), ("exists", exists),
                              ("missing", missing)) if v}
    out = _answer(*call("POST", f"cameras/{camera_id}", body, session=session))
    import camera_secrets
    camera_secrets.keep_beside(camera_id, set, remove, session=session)
    return out


def claim(camera_id=None, session=None):
    """(camera ID, token): the one given, or the next free one past every adapter's (D11). The claim is
    completed or abandoned by an update that expects the token. ConditionFailed: a given ID is taken."""
    out = _answer(*call("POST", "cameras/claim", {"cameraId": camera_id} if camera_id else {}, session=session))
    return out["cameraId"], out["token"]


def adopt(camera_id, session=None):
    """Stamp a row without an owner as this adapter's (the adopting adapter only, D12). False when it was
    stamped meanwhile, released, or isn't a camera."""
    try:
        _answer(*call("POST", f"cameras/{camera_id}/adopt", {}, session=session))
        return True
    except ConditionFailed:
        return False


def add_clip(item, session=None):
    """A clip row for one of this adapter's cameras; ConditionFailed when one with that start time is there."""
    return _answer(*call("POST", "clips", {"item": item}, session=session))


def elsewhere(endpoint_refs, session=None):
    """Which of these WS-Discovery identities are cameras of another adapter."""
    refs = sorted(set(endpoint_refs))
    if not refs:
        return set()
    return set(_answer(*call("POST", "devices", {"endpointRefs": refs}, session=session))["elsewhere"])


def camera_request(request_id, session=None):
    """The web page's camera request for this adapter (D16), password included; None when there is no
    such request for this adapter (another adapter's is none) -- read through the route (Phase 10.2f)."""
    try:
        return _answer(*call("GET", f"requests/{request_id}", session=session))["request"]
    except NotMine:
        return None


def check(camera_id=None, session=None):
    """The route's three promises, on this Pi: it answers; another Thing's path is refused; a write
    to one of this Pi's cameras goes through. Returns how many failed."""
    if session is None:
        from aws_device_creds import get_session
        session = get_session()
    failed = 0

    def report(ok, what, got):
        nonlocal failed
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {what}: {got}", flush=True)

    status, out = call("GET", "ping", session=session)
    report(status == 200 and out.get("thing") == config.THING_NAME, f"the route answers {config.THING_NAME}",
           f"{status} {out}")
    other = "adapter-99" if config.THING_NAME != "adapter-99" else "adapter-98"
    status, out = call("GET", "ping", thing=other, session=session)
    report(status == 403, f"IAM refuses {other}'s path to this Pi", f"{status} {out}")
    if camera_id is None:
        camera_id = _first_own_camera(session)
    if camera_id is None:
        report(False, "a harmless write", "this Pi has no camera to write to -- name one")
        return failed
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status, out = call("POST", f"cameras/{camera_id}", {"set": {"registryCheckedAt": stamp}}, session=session)
    report(status == 200, f"a harmless write: registryCheckedAt on {camera_id}", f"{status} {out}")
    return failed


def _first_own_camera(session):
    """The first camera this Pi serves and acts on, by the adapterId-index (registry_reads)."""
    import camera_control
    import registry_reads
    mine = [r["cameraId"] for r in registry_reads.own_rows(["mode"], session=session)
            if r.get("mode") in ("transcode", "passthrough") and camera_control.acts_on(r)]
    return mine[0] if mine else None


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "check" or len(sys.argv) > 3:
        sys.exit(__doc__)
    sys.exit(1 if check(sys.argv[2] if len(sys.argv) == 3 else None) else 0)
