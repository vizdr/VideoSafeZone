#!/usr/bin/env python3
"""Run the camera schedules the web panel stores (WEB-PANEL-PLAN.md D6), once a minute.

kvs-schedule.timer starts this every minute. Each run acts on the entries whose moment passed
since the last run: start or stop the camera's cloud recording (its producer unit), or set its
recording mode in the registry. Edge-triggered, never level: an entry acts once, when its time
passes, so a manual Stop at 20:00 inside a scheduled window stays stopped -- a "should be
recording now" rule would start it again a minute later.

- Times are wall-clock in the schedule's own zone (zoneinfo), so 18:00 stays 18:00 across a DST
  change. A time the clock skips (spring forward) acts when the clock jumps past it; a time it
  repeats (fall back) acts once, at the first.
- After a gap -- a reboot, the Pi switched off -- entries missed by up to CATCH_UP_SEC still act;
  older ones are logged and skipped, never replayed hours late. The first run ever replays
  nothing older than a minute.
- The registry is read at most every REFRESH_SEC and kept in a 0600 cache that holds only the
  schedules (no credentials), so schedules still run while AWS is unreachable: starting and
  stopping a producer is local. (The outage supervisor's cache holds only each camera's buffer
  seconds, so this one keeps its own.) A mode change needs the registry; while AWS is down it is
  logged as not set.
- Cost (guide §1.2): a scheduled Start is a person's choice, made in the web panel, which warns
  when a Start has no Stop within 24 hours. Nothing here starts a producer no entry asks for, and
  an unregistered camera's schedule is never run.
"""
import datetime as dt
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import camera_control

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "vms-schedule"
LAST_RUN = STATE_DIR / "last-run.json"
CACHE = STATE_DIR / "schedules.json"
CATCH_UP_SEC = 600
REFRESH_SEC = 300
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")         # set_camera_settings.py DAYS
VALID_MODES = {"manual", "motion", "cellMotion", "human"}         # set_camera_mode.py


def log(msg: str) -> None:
    print(f"schedule: {msg}", flush=True)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def read_json(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:  # noqa: BLE001 -- missing or half-written: as if never written
        return None


def write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.chmod(tmp, 0o600)
    tmp.replace(path)                              # never a half-written file to read back


# --- the schedules ------------------------------------------------------------------------------

def fetch_schedules() -> dict:
    """{cameraId: schedule} for every registered camera that has one: one scan, on timeouts short
    enough that an unreachable AWS costs seconds, not the minute (outage_buffer._fetch_registry)."""
    from botocore.config import Config
    from aws_device_creds import get_session
    cfg = Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1})
    table = get_session().resource("dynamodb", config=cfg).Table("cameras")
    items, kw = [], {}
    while True:
        page = table.scan(**kw)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            break
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return {i["cameraId"]: i["schedule"] for i in items
            if i.get("schedule") and not camera_control.is_unregistered(i)}


def load_schedules(now: dt.datetime) -> dict:
    """The schedules, from the registry when the cache is older than REFRESH_SEC, else -- or
    while AWS is unreachable -- from the cache."""
    cache = read_json(CACHE) or {}
    fetched = cache.get("fetchedAt")
    if fetched and 0 <= (now - parse(fetched)).total_seconds() < REFRESH_SEC:
        return cache.get("cameras") or {}
    try:
        cameras = fetch_schedules()
    except Exception as e:  # noqa: BLE001
        log(f"registry unreachable ({type(e).__name__}): using the schedules as of {fetched or 'never'}")
        return cache.get("cameras") or {}
    write_json(CACHE, {"fetchedAt": iso(now), "cameras": cameras})
    return cameras


def occurrences(entry: dict, tz: ZoneInfo, after: dt.datetime, until: dt.datetime) -> list:
    """The UTC moments in (after, until] at which `entry` acts, in the schedule's zone."""
    hh, mm = (int(x) for x in entry["at"].split(":"))
    day = after.astimezone(tz).date() - dt.timedelta(days=1)
    last = until.astimezone(tz).date() + dt.timedelta(days=1)
    out = []
    while day <= last:
        if DAYS[day.weekday()] in entry["days"]:
            # fold=0: a skipped time maps past the jump, a repeated one to its first occurrence.
            t = dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz).astimezone(dt.timezone.utc)
            if after < t <= until:
                out.append(t)
        day += dt.timedelta(days=1)
    return out


# --- acting ---------------------------------------------------------------------------------------

def set_mode(camera_id: str, mode: str) -> None:
    from botocore.config import Config
    from aws_device_creds import get_session
    if mode not in VALID_MODES:
        raise ValueError(f"unknown mode {mode!r}")
    cfg = Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1})
    # Field by field (the registry is shared with VMS), and never on a camera unregistered since
    # the schedules were last read.
    get_session().resource("dynamodb", config=cfg).Table("cameras").update_item(
        Key={"cameraId": camera_id}, UpdateExpression="SET recordingMode = :m",
        ConditionExpression="attribute_exists(cameraId) AND attribute_not_exists(unregisteredAt)",
        ExpressionAttributeValues={":m": mode})


def act(camera_id: str, entry: dict, when: dt.datetime) -> None:
    action = entry["action"]
    what = f"{camera_id}: {action} due {iso(when)}"
    if action in ("start", "stop"):
        # A camera registered from the other SD card has a row but no producer here (#37's trap).
        if not camera_control.has_producer(camera_id):
            log(f"{what} -- no producer on this Pi: skipped")
            return
        camera_control.set_stream(camera_id, action == "start")
        log(f"{what} -- done")
    elif action.startswith("mode:"):
        mode = action[len("mode:"):]
        try:
            set_mode(camera_id, mode)
            log(f"{what} -- recording mode is now {mode}")
        except Exception as e:  # noqa: BLE001
            log(f"{what} -- not set ({type(e).__name__}: {e})")
    else:
        log(f"{what} -- unknown action: skipped")


def run(now: dt.datetime | None = None) -> None:
    now = now or utc_now()
    state = read_json(LAST_RUN) or {}
    last = parse(state["lastRun"]) if state.get("lastRun") else now - dt.timedelta(seconds=60)
    if last > now:
        if (last - now).total_seconds() <= CATCH_UP_SEC:
            # The clock stepped back a little (an NTP correction): what already acted stays acted.
            # The later time is kept, or the minutes between would act a second time.
            return
        # Far ahead: the clock was wrong when that run was recorded (a Pi has no battery clock).
        # Keeping it would stall every schedule until that time came round again.
        log(f"the last run is recorded at {iso(last)}, ahead of the clock: starting over from now")
        last = now
    floor = now - dt.timedelta(seconds=CATCH_UP_SEC)

    due, missed = [], []
    for camera_id, schedule in load_schedules(now).items():
        try:
            tz = ZoneInfo(schedule["tz"])
        except (ZoneInfoNotFoundError, ValueError, KeyError, TypeError):
            log(f"{camera_id}: unknown time zone {schedule.get('tz')!r} -- schedule not run")
            continue
        for entry in schedule.get("entries") or []:
            for t in occurrences(entry, tz, last, now):
                (missed if t <= floor else due).append((t, camera_id, entry))

    for t, camera_id, entry in sorted(missed, key=lambda x: (x[0], x[1])):
        log(f"{camera_id}: {entry['action']} due {iso(t)} missed by more than "
            f"{CATCH_UP_SEC // 60} min -- skipped")
    # In time order; at one moment a mode change first, so a Start records in the new mode.
    for t, camera_id, entry in sorted(due, key=lambda x: (x[0], x[1], not x[2]["action"].startswith("mode:"))):
        act(camera_id, entry, t)
    write_json(LAST_RUN, {"lastRun": iso(now)})


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
