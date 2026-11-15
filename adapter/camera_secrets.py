"""Each camera's secrets in SSM Parameter Store, per adapter (DEALER-PANEL-PLAN.md D13, Phase 10.2d).

The ONVIF password and the RTSP URIs -- which carry the camera's login -- are SecureStrings under
/vsz/adapters/<this Thing>/cameras/<cameraId>/<field>, which IAM lets only that Thing read and write
(${credentials-iot:ThingName} in the device role's policy, tried in the spike, Phase 10.1). One
parameter per field: a write of one field never races a write of another, and removing a field is
deleting its parameter. Values are JSON, so an empty password (cam-04's) is kept too -- SSM refuses an
empty value. The default key (aws/ssm) encrypts them; the role needs no KMS grant for it.

Until the role is narrowed (10.2f) the row keeps the secrets as well, and stays what readers use: VMS's
card shares the table and may change a row, and an SSM copy taken for the truth would then shadow the
newer row. So:
  - registry_client.update() writes a secret field to SSM before the row, and a failure fails the update
    (since the narrowing SSM is the only copy: FoundAndFixed.md #97); a refused row write puts SSM back;
  - rematch mirrors every row that holds secrets into SSM (mirror) -- the older cameras' backfill, and a
    change VMS's card made -- asking SSM again for each camera once a day, or when its row changes;
  - registry_reads fills a row that holds no secrets from SSM (fill): none yet, every one after 10.2f.
`camera_secrets.py check` compares, on the Pi, each camera's SSM copy with its row and tries another
Thing's path (refused); `camera_secrets.py mirror` copies now. Values are never printed: field names only.
"""
import hashlib
import json
import os
import secrets as _random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402

FIELDS = ("onvifPassword", "rtspUrl", "liveRtspUrl")
MIRROR_AGAIN_AFTER = 24 * 3600


def path(camera_id):
    return f"/vsz/adapters/{config.THING_NAME}/cameras/{camera_id}"


def _name(camera_id, field):
    return f"{path(camera_id)}/{field}"


def _ssm(session=None):
    if session is None:
        from aws_device_creds import get_session
        session = get_session()
    return session.client("ssm")


def _code(e):
    return getattr(e, "response", {}).get("Error", {}).get("Code")


def holds_secrets(row):
    """Whether the row itself carries the camera's secrets: every passthrough row until 10.2f."""
    return "onvifPassword" in row


def read(camera_id, session=None):
    """{field: value} of the camera's secrets in SSM -- the fields that are there."""
    out = _ssm(session).get_parameters(Names=[_name(camera_id, f) for f in FIELDS], WithDecryption=True)
    return {p["Name"].rsplit("/", 1)[1]: json.loads(p["Value"]) for p in out.get("Parameters", [])}


def write(camera_id, set=None, remove=None, session=None):
    """The secret fields among `set` written, those among `remove` deleted; other fields ignored."""
    from botocore.exceptions import ClientError
    ssm = _ssm(session)
    for field, value in sorted((set or {}).items()):
        if field in FIELDS:
            ssm.put_parameter(Name=_name(camera_id, field), Value=json.dumps(value), Type="SecureString",
                              Overwrite=True)
    for field in remove or []:
        if field in FIELDS:
            try:
                ssm.delete_parameter(Name=_name(camera_id, field))
            except ClientError as e:
                if _code(e) != "ParameterNotFound":
                    raise


def secret_fields(set=None, remove=None):
    """The camera secrets among a registry write's fields, sorted."""
    return sorted({*(set or {}), *(remove or [])} & {*FIELDS})


def restore(camera_id, before, fields, session=None, log=print):
    """SSM's `fields` put back as `before` had them (a field it didn't have, deleted) -- after the registry
    write they went with was refused. Best effort: a failure is logged, by field name."""
    try:
        write(camera_id, {f: before[f] for f in fields if f in before}, [f for f in fields if f not in before], session)
    except Exception as e:  # noqa: BLE001 -- the refusal is what the caller hears; this is logged beside it
        log(f"camera_secrets: {camera_id}: {', '.join(fields)} not put back in SSM ({_code(e) or type(e).__name__})")


def fill(row, session=None):
    """The row with its secrets from SSM when it doesn't hold them itself (after 10.2f); else as it is."""
    if row.get("mode") != "passthrough" or holds_secrets(row):
        return row
    return {**row, **read(row["cameraId"], session)}


def _wanted(row):
    return {f: row[f] for f in FIELDS if row.get(f) is not None}


def mirror(rows=None, dry_run=False, force=False, now=None, session=None, log=print):
    """Every row of this Pi's that holds secrets, copied into SSM where SSM differs: what is missing or
    other is written, what the row no longer has is deleted. A camera is asked about again only when
    its row's secrets change or a day has passed (a local record of a salted digest -- never the values;
    `force` asks every camera). Returns the camera IDs changed (or that would be, in a dry run)."""
    import registry_reads
    import sync_mediamtx_paths as paths
    if rows is None:
        rows = registry_reads.own_rows(session=session, secrets=False)    # as the table holds them, never filled
    record_file = paths.STATE_DIR / "secrets-mirrored.json"
    try:
        record = json.loads(record_file.read_text())
    except (OSError, ValueError):
        record = {}
    salt = record.get("salt") or _random.token_hex(16)
    done = record.get("cameras") or {}
    now = time.time() if now is None else now
    changed = []
    for row in rows:
        if row.get("mode") != "passthrough" or not holds_secrets(row):
            continue
        cid, want = row["cameraId"], _wanted(row)
        digest = hashlib.sha256((salt + json.dumps(want, sort_keys=True)).encode()).hexdigest()
        prev = done.get(cid) or {}
        if not force and prev.get("digest") == digest and now - prev.get("at", 0) < MIRROR_AGAIN_AFTER:
            continue
        have = read(cid, session)
        sets = {f: v for f, v in want.items() if have.get(f) != v}
        removes = [f for f in have if f not in want]
        if sets or removes:
            what = ", ".join([*sorted(sets), *(f"-{f}" for f in removes)])
            changed.append(cid)
            if dry_run:
                log(f"{cid}: would mirror its secrets to SSM ({what})")
                continue
            write(cid, sets, removes, session)
            log(f"{cid}: secrets mirrored to SSM ({what})")
        if not dry_run:
            done[cid] = {"digest": digest, "at": now}
    if not dry_run:
        paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = record_file.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"salt": salt, "cameras": done}, f, indent=1)
        os.replace(tmp, record_file)
    return changed


def strip(session=None, log=print):
    """After the narrowing (Phase 10.2f, the route's ROW_SECRETS=drop): each of this Pi's rows rid of its
    secrets, once SSM is seen to hold exactly the row's -- a row whose SSM copy differs keeps them (run
    `mirror` first). The route's every write now takes the secrets out of a row, so a harmless one is
    enough (`updatedAt`); a row that still holds them afterwards means the route still keeps them.
    Returns how many rows still hold secrets. Values are never printed."""
    import registry_client
    import registry_reads
    from datetime import datetime, timezone
    left = 0
    for row in registry_reads.own_rows(session=session, secrets=False):
        if row.get("mode") != "passthrough" or not holds_secrets(row):
            continue
        cid, want = row["cameraId"], _wanted(row)
        have = read(cid, session)
        diff = sorted(f for f in {*want, *have} if want.get(f) != have.get(f))
        if diff:
            log(f"{cid}: SSM doesn't hold the row's secrets ({', '.join(diff)} differ) -- left in the row; "
                f"camera_secrets.py mirror, then strip again")
            left += 1
            continue
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        registry_client.update(cid, set={"updatedAt": stamp}, session=session)
    for row in registry_reads.own_rows(session=session, secrets=False):
        if row.get("mode") == "passthrough" and holds_secrets(row) and _wanted(row) == read(row["cameraId"], session):
            log(f"{row['cameraId']}: the row still holds its secrets -- is vsz-registry's ROW_SECRETS=drop set "
                f"(provision.sh narrow)?")
            left += 1
        elif row.get("mode") == "passthrough" and not holds_secrets(row):
            log(f"{row['cameraId']}: secrets in SSM only")
    return left


def check(session=None):
    """On this Pi: IAM refuses another Thing's secrets, and each camera's SSM copy is its row's (or, for
    a row that holds none, SSM has them). Returns how many failed."""
    import registry_reads
    from botocore.exceptions import ClientError
    if session is None:
        from aws_device_creds import get_session
        session = get_session()
    failed = 0

    def report(ok, what, got):
        nonlocal failed
        failed += ok is False
        print(f"{'PASS' if ok else 'FAIL'}  {what}: {got}", flush=True)

    other = "adapter-99" if config.THING_NAME != "adapter-99" else "adapter-98"
    try:
        _ssm(session).get_parameters(Names=[f"/vsz/adapters/{other}/cameras/cam-01/onvifPassword"],
                                     WithDecryption=True)
        report(False, f"IAM refuses {other}'s camera secrets to this Pi", "the read was answered")
    except ClientError as e:
        report(_code(e) == "AccessDeniedException", f"IAM refuses {other}'s camera secrets to this Pi", _code(e))
    for row in registry_reads.own_rows(session=session, secrets=False):     # as the table holds them
        if row.get("mode") != "passthrough":
            continue
        cid = row["cameraId"]
        try:
            have = read(cid, session)
        except ClientError as e:
            report(False, f"{cid}: its secrets in SSM", _code(e))
            continue
        if not holds_secrets(row):
            report("rtspUrl" in have and "onvifPassword" in have, f"{cid}: its secrets in SSM only",
                   ", ".join(sorted(have)) or "none")
            continue
        want = _wanted(row)
        diff = sorted(f for f in {*want, *have} if want.get(f) != have.get(f))
        report(not diff, f"{cid}: SSM holds the row's secrets",
               ", ".join(sorted(have)) if not diff else f"{', '.join(diff)} differ -- camera_secrets.py mirror")
    return failed


if __name__ == "__main__":
    if sys.argv[1:] == ["check"]:
        sys.exit(1 if check() else 0)
    if sys.argv[1:] in (["mirror"], ["mirror", "--dry-run"]):
        mirror(dry_run=sys.argv[2:] == ["--dry-run"], force=True)
        sys.exit(0)
    if sys.argv[1:] == ["strip"]:
        sys.exit(1 if strip() else 0)
    sys.exit("usage: camera_secrets.py check | mirror [--dry-run] | strip   (strip: after provision.sh narrow only)")
