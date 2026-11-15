"""This adapter's camera rows, read from the registry (DEALER-PANEL-PLAN.md D13, Phase 10.2c).

A Pi reads only its own rows: a Query on the `cameras` table's adapterId-index for this Thing, which
IAM limits to the caller's own Thing with dynamodb:LeadingKeys (tried in the spike, Phase 10.1) -- the
one read of the table the device role keeps once it is narrowed (10.2f). Another adapter's camera
isn't seen at all, so it is answered as unknown.

The index is sparse: a row without an owner isn't in it. Such a row is the adopting adapter's
(ADOPT_UNSTAMPED_ROWS, adapter-01, D12) until rematch stamps it, so that Pi also scans for rows
without an owner -- while the role allows a scan; once it doesn't (10.2f) there are none left to
adopt, and the refusal reads as none. What needs every adapter's rows -- the next free camera ID,
whether a device is another adapter's camera -- is the registry route's (registry_client).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402

INDEX = "adapterId-index"
TABLE = "cameras"
# Always read, whatever fields a caller asks for: what camera_control.acts_on decides by -- whose row
# it is (is_mine) and whether it is unregistered. Without them every row looked unstamped
# (FoundAndFixed.md #96).
ACTS_ON_FIELDS = ("cameraId", "adapterId", "releasedFrom", "unregisteredAt")
SECRET_FIELDS = ("onvifPassword", "rtspUrl", "liveRtspUrl")   # camera_secrets.FIELDS


def _table(session=None, table=None):
    if table is not None:
        return table
    if session is None:
        from aws_device_creds import get_session
        session = get_session()
    return session.resource("dynamodb").Table(TABLE)


def _projection(fields, *also):
    """ProjectionExpression for `fields` (and `also`), every name through a placeholder (reserved words
    such as `mode`); nothing when every field is wanted."""
    if not fields:
        return {}
    names = {f"#p{i}": f for i, f in enumerate(dict.fromkeys([*fields, *also]))}
    return {"ProjectionExpression": ", ".join(names), "ExpressionAttributeNames": names}


def _pages(call, **kw):
    rows = []
    while True:
        page = call(**kw)
        rows += page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            return rows
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def own_rows(fields=None, session=None, table=None, secrets=True):
    """Every row this Pi serves (camera_control.is_mine), by camera ID: this Thing's from the index, and
    for the adopting adapter the rows without an owner too. `fields` limits what is read -- never
    ACTS_ON_FIELDS, which the callers' camera_control.acts_on reads: without adapterId every row looks
    unstamped, which only the adopting Pi takes for its own; without unregisteredAt, none is. A row that
    holds no camera secrets gets them from SSM (Phase 10.2d) unless `secrets` is False: camera_secrets'
    own mirror and check compare the table with SSM, and need the rows as the table holds them."""
    from boto3.dynamodb.conditions import Key
    t = _table(session, table)
    also = ("mode", "onvifPassword") if _wants_secrets(fields) and fields else ()    # what fill() decides by
    rows = _pages(t.query, IndexName=INDEX, KeyConditionExpression=Key("adapterId").eq(config.THING_NAME),
                  **_projection(fields, *ACTS_ON_FIELDS, *also))
    if config.ADOPT_UNSTAMPED_ROWS:
        rows += unstamped_rows([*fields, *also] if fields else None, table=t)
    rows = sorted(rows, key=lambda r: r["cameraId"])
    return _with_secrets(rows, fields, session) if secrets else rows      # secrets=False: as the table holds them


def _wants_secrets(fields):
    return fields is None or bool({*fields} & {*SECRET_FIELDS})


def _with_secrets(rows, fields, session=None):
    """Rows asked for whole, or for a secret field, with the camera's secrets from SSM where the row
    doesn't hold them (camera_secrets.fill, Phase 10.2d): none before the narrowing (10.2f), when the
    rows still carry them -- so no SSM call -- and every passthrough row after it."""
    if not _wants_secrets(fields):
        return rows
    import camera_secrets
    return [camera_secrets.fill(r, session) for r in rows]


def unstamped_rows(fields=None, session=None, table=None):
    """Rows without an owner and never released -- the adopting adapter's to stamp (D12). A scan, which
    the narrowed role refuses (10.2f): by then there are none, and the refusal reads as none."""
    from botocore.exceptions import ClientError
    t = _table(session, table)
    try:
        rows = _pages(t.scan, **_projection(fields, *ACTS_ON_FIELDS))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "AccessDeniedException":
            return []
        raise
    return [r for r in rows if not r.get("adapterId") and not r.get("releasedFrom")]


def own_row(camera_id, session=None, table=None):
    """One of this Pi's rows, or None: another adapter's camera is unknown here, as one that doesn't
    exist. On the adopting Pi a row the index doesn't list is read by its key rather than by a scan
    (refused once the role is narrowed, 10.2f: by then there is none) -- a row without an owner, or one
    of its own the index hasn't caught up with yet (a GSI is eventually consistent: a camera just
    registered can be missing from it for a moment)."""
    from boto3.dynamodb.conditions import Key
    from botocore.exceptions import ClientError
    t = _table(session, table)
    rows = _pages(t.query, IndexName=INDEX, KeyConditionExpression=Key("adapterId").eq(config.THING_NAME))
    row = next((r for r in rows if r["cameraId"] == camera_id), None)
    if row is not None or not config.ADOPT_UNSTAMPED_ROWS:
        return _with_secrets([row], None, session)[0] if row is not None else None
    try:
        row = t.get_item(Key={"cameraId": camera_id}).get("Item")
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "AccessDeniedException":
            return None
        raise
    if not row:
        return None
    owner = row.get("adapterId")
    mine = owner == config.THING_NAME or (not owner and not row.get("releasedFrom"))
    return _with_secrets([row], None, session)[0] if mine else None


def check(session=None):
    """The index's promises, on this Pi: it lists this Pi's rows; IAM refuses another Thing's; and what it
    lists is what camera_control.is_mine keeps of the whole table (while the role may still scan).
    Returns how many failed."""
    import camera_control
    from boto3.dynamodb.conditions import Key
    from botocore.exceptions import ClientError
    t = _table(session)
    failed = 0

    def report(ok, what, got):
        nonlocal failed
        failed += ok is False
        mark = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
        print(f"{mark}  {what}: {got}", flush=True)

    try:
        mine = sorted(r["cameraId"] for r in _pages(t.query, IndexName=INDEX, ProjectionExpression="cameraId",
                                                     KeyConditionExpression=Key("adapterId").eq(config.THING_NAME)))
        report(True, f"{INDEX} lists {config.THING_NAME}'s rows", ", ".join(mine) or "none yet")
    except ClientError as e:
        report(False, f"{INDEX} lists {config.THING_NAME}'s rows", e.response.get("Error", {}).get("Code"))
    other = "adapter-99" if config.THING_NAME != "adapter-99" else "adapter-98"
    try:
        t.query(IndexName=INDEX, ProjectionExpression="cameraId", KeyConditionExpression=Key("adapterId").eq(other))
        report(False, f"IAM refuses {other}'s rows to this Pi", "the query was answered")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        report(code == "AccessDeniedException", f"IAM refuses {other}'s rows to this Pi", code)
    try:
        whole = _pages(t.scan, **_projection(["unregisteredAt"], "cameraId", "adapterId", "releasedFrom"))
    except ClientError as e:
        report(None, "the index agrees with the table", f"the role no longer scans ({e.response.get('Error', {}).get('Code')})")
        return failed
    want = sorted(r["cameraId"] for r in whole if camera_control.is_mine(r))
    try:
        got = [r["cameraId"] for r in own_rows(["cameraId"], table=t)]
    except ClientError as e:
        report(False, "own_rows() is what is_mine keeps of the table", e.response.get("Error", {}).get("Code"))
        return failed
    report(got == want, "own_rows() is what is_mine keeps of the table", f"{', '.join(got) or 'none'}"
           + ("" if got == want else f" -- the table says {', '.join(want) or 'none'}"))
    return failed


if __name__ == "__main__":
    if sys.argv[1:] != ["check"]:
        sys.exit("usage: registry_reads.py check")
    sys.exit(1 if check() else 0)
