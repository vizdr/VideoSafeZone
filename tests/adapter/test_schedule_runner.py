"""schedule_runner.py: the camera schedules, run on the Pi (WEB-PANEL-PLAN.md D6, Phase 5.1).

Edge-triggered: each entry acts once when its time passes, a manual change in between stands,
missed entries act within the catch-up window and are skipped after it, times follow the schedule's
zone across DST, and an unregistered camera's schedule never runs.
"""
import datetime as dt

import pytest

import schedule_runner as sr

UTC = dt.timezone.utc
BERLIN = "Europe/Berlin"


def at(*args):
    return dt.datetime(*args, tzinfo=UTC)


def cam(cid, entries, tz=BERLIN, **over):
    return {"cameraId": cid, "mode": "passthrough", "onvifHost": "192.0.2.1",
            "schedule": {"tz": tz, "entries": entries}, **over}


START_18 = {"days": ["mon", "tue", "wed", "thu", "fri"], "at": "18:00", "action": "start"}
STOP_07 = {"days": ["tue", "wed", "thu", "fri", "sat"], "at": "07:00", "action": "stop"}
MON = (2026, 11, 2)       # a Monday; Berlin is UTC+1 in November, so 18:00 local = 17:00 UTC


@pytest.fixture
def world(registry, fake_run):
    """The registry, and the producers: every camera has one unless named in no_producer."""
    def make(rows, no_producer=()):
        table = registry(rows)
        answers = {("systemctl", "show"): "loaded\n"}
        for cid in no_producer:
            unit = sr.camera_control.unit_name(cid)
            answers[("systemctl", "show", "-p", "LoadState", "--value", unit)] = "not-found\n"
        run = fake_run(sr.camera_control, answers)
        return table, run
    return make


def started(run):
    return [c[-1] for c in run.calls if c[:3] == ["sudo", "systemctl", "start"]]


def stopped(run):
    return [c[-1] for c in run.calls if c[:3] == ["sudo", "systemctl", "stop"]]


def tick(start, end, step=60):
    t = start
    while t <= end:
        sr.run(t)
        t += dt.timedelta(seconds=step)


def test_an_entry_acts_once_when_its_time_passes(world):
    _, run = world([cam("cam-02", [START_18])])
    tick(at(*MON, 16, 58), at(*MON, 17, 5))
    assert started(run) == ["kvs-cam02.service"]


def test_the_first_run_replays_nothing_older_than_a_minute(world):
    _, run = world([cam("cam-02", [START_18])])
    sr.run(at(*MON, 17, 5))                         # 18:00 Berlin was five minutes ago
    assert started(run) == []


def test_a_missed_entry_acts_within_ten_minutes(world, capsys):
    _, run = world([cam("cam-02", [START_18])])
    sr.run(at(*MON, 16, 55))
    sr.run(at(*MON, 17, 9))                         # back after a reboot, 9 minutes late
    assert started(run) == ["kvs-cam02.service"]


def test_an_entry_missed_by_more_is_skipped_and_logged(world, capsys):
    _, run = world([cam("cam-02", [START_18])])
    sr.run(at(*MON, 16, 40))
    sr.run(at(*MON, 17, 20))                        # 20 minutes late
    assert started(run) == []
    assert "cam-02: start due 2026-11-02T17:00:00Z missed by more than 10 min -- skipped" in capsys.readouterr().out


def test_a_manual_stop_in_a_window_stands(world):
    # Level-triggered logic would start it again every minute until 07:00.
    _, run = world([cam("cam-02", [START_18, STOP_07])])
    tick(at(*MON, 16, 59), at(*MON, 17, 1))
    assert started(run) == ["kvs-cam02.service"]
    # ...the user presses Stop at 18:30; nothing in the schedule restarts it.
    tick(at(*MON, 17, 2), at(*MON, 23, 0), step=300)
    assert started(run) == ["kvs-cam02.service"]
    tick(at(2026, 11, 3, 5, 55), at(2026, 11, 3, 6, 5))   # 07:00 Berlin, Tuesday
    assert stopped(run) == ["kvs-cam02.service"]


def test_spring_forward_a_skipped_time_acts_once_after_the_jump(world):
    # 29 March 2026: Berlin jumps 02:00 -> 03:00. 02:30 never happens on the clock.
    _, run = world([cam("cam-02", [{"days": ["sun"], "at": "02:30", "action": "start"}])])
    tick(at(2026, 3, 29, 0, 30), at(2026, 3, 29, 2, 30))
    assert started(run) == ["kvs-cam02.service"]


def test_fall_back_a_repeated_time_acts_once(world):
    # 25 October 2026: Berlin goes 03:00 -> 02:00. 02:30 happens twice on the clock.
    _, run = world([cam("cam-02", [{"days": ["sun"], "at": "02:30", "action": "start"}])])
    tick(at(2026, 10, 25, 0, 0), at(2026, 10, 25, 2, 30))
    assert started(run) == ["kvs-cam02.service"]


def test_the_time_zone_is_the_schedules_own(world):
    _, run = world([cam("cam-02", [START_18], tz="America/New_York")])
    tick(at(*MON, 16, 58), at(*MON, 17, 5))          # 18:00 Berlin: nothing
    assert started(run) == []
    tick(at(*MON, 22, 58), at(*MON, 23, 5))          # 18:00 New York = 23:00 UTC
    assert started(run) == ["kvs-cam02.service"]


def test_an_unregistered_camera_or_one_without_a_producer_is_never_started(world, capsys):
    rows = [cam("cam-03", [START_18], unregisteredAt="2026-10-25T12:00:00Z"), cam("cam-04", [START_18])]
    _, run = world(rows, no_producer=("cam-04",))
    tick(at(*MON, 16, 59), at(*MON, 17, 1))
    assert started(run) == []
    out = capsys.readouterr().out
    assert "cam-04: start due 2026-11-02T17:00:00Z -- no producer on this Pi: skipped" in out
    assert "cam-03" not in out


def test_a_mode_change_writes_the_registry_before_a_start_at_the_same_moment(world):
    table, run = world([cam("cam-02", [START_18, {"days": ["mon"], "at": "18:00", "action": "mode:motion"}],
                            recordingMode="manual")])
    tick(at(*MON, 16, 59), at(*MON, 17, 1))
    assert table.row("cam-02")["recordingMode"] == "motion"
    assert started(run) == ["kvs-cam02.service"]
    mode_write = next(i for i, w in enumerate(table.writes) if w[0] == "update")
    assert mode_write == 0                           # the only write, made before the start


def test_offline_the_cached_schedules_still_run(world, monkeypatch, capsys):
    _, run = world([cam("cam-02", [START_18])])
    sr.run(at(*MON, 16, 50))                         # read once while AWS answers
    def unreachable():
        raise ConnectionError("no route to AWS")
    monkeypatch.setattr(sr, "fetch_schedules", unreachable)
    tick(at(*MON, 16, 56), at(*MON, 17, 1))          # the cache is now older than 5 minutes
    assert started(run) == ["kvs-cam02.service"]
    assert "registry unreachable (ConnectionError): using the schedules as of 2026-11-02T16:50:00Z" in capsys.readouterr().out


def test_a_mode_change_while_offline_is_logged_not_set(world, monkeypatch, capsys):
    table, _ = world([cam("cam-02", [{"days": ["mon"], "at": "18:00", "action": "mode:motion"}])])
    sr.run(at(*MON, 16, 59))
    def broken(*a, **k):
        raise ConnectionError("no route to AWS")
    monkeypatch.setattr(table, "update_item", broken)
    sr.run(at(*MON, 17, 0, 30))
    assert "mode:motion due 2026-11-02T17:00:00Z -- not set (ConnectionError" in capsys.readouterr().out


def test_an_unknown_zone_is_reported_and_the_clock_going_back_runs_nothing_twice(world, capsys):
    _, run = world([cam("cam-02", [START_18]), cam("cam-03", [START_18], tz="Mars/Olympus")])
    tick(at(*MON, 16, 59), at(*MON, 17, 1))
    sr.run(at(*MON, 16, 59, 30))                     # NTP moved the clock back
    sr.run(at(*MON, 17, 1, 30))
    assert started(run) == ["kvs-cam02.service"]
    assert "cam-03: unknown time zone 'Mars/Olympus' -- schedule not run" in capsys.readouterr().out


def test_the_cache_holds_schedules_only_and_is_private(world):
    world([cam("cam-02", [START_18], onvifPassword="s3cret", rtspUrl="rtsp://admin:s3cret@h/")])
    sr.run(at(*MON, 16, 59))
    assert "s3cret" not in sr.CACHE.read_text()
    assert oct(sr.CACHE.stat().st_mode & 0o777) == "0o600"


def test_a_clock_far_ahead_at_the_last_run_doesnt_stall_the_schedules(world, capsys):
    # A Pi has no battery clock: one run recorded under a wrong date must not block the rest.
    _, run = world([cam("cam-02", [START_18])])
    sr.write_json(sr.LAST_RUN, {"lastRun": "2027-06-01T00:00:00Z"})
    tick(at(*MON, 16, 59), at(*MON, 17, 1))
    assert started(run) == ["kvs-cam02.service"]
    assert "ahead of the clock: starting over from now" in capsys.readouterr().out
