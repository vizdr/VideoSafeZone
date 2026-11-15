"""Every reader of the `cameras` table that acts on cameras acts only on this Pi's (DEALER-PANEL-PLAN.md D12).

The registry is one table for every adapter. A Pi that acted on another's rows would add MediaMTX
paths for cameras on another LAN (private addresses that may reach some other device on its own),
subscribe to their events, start their producers. So each reader keeps a row only when its
`adapterId` is this Pi's Thing name (tests/fixtures/adapter.env: test-thing) -- or, on a Pi that
adopts unstamped rows (adapter-01; the fixture's ADOPT_UNSTAMPED_ROWS=yes), a row without one that
was never released (D15). The same list as tests/adapter/test_unregistered_readers.py: a new reader
of the table belongs in both. Thumbnails and rematch take their lists from the outage buffer's cache
and the path sync, tested here.
"""
import config
import camera_control
import codec_caps
import event_watcher
import outage_buffer
import pytest
import schedule_runner
import sync_mediamtx_paths

BASE = {"mode": "passthrough", "rtspUrl": "rtsp://192.0.2.10/s", "onvifHost": "192.0.2.10",
        "recordingMode": "motion", "outageBufferSec": 300,
        "schedule": {"timezone": "Europe/Berlin", "entries": []}}
ROWS = [
    {**BASE, "cameraId": "cam-02", "adapterId": "test-thing"},              # this Pi's
    {**BASE, "cameraId": "cam-03"},                                         # unstamped: adopted here
    {**BASE, "cameraId": "cam-07", "adapterId": "adapter-02"},              # another adapter's
    {**BASE, "cameraId": "cam-08", "releasedFrom": "test-thing"},           # released from this one
]
MINE = {"cam-02", "cam-03"}


@pytest.mark.parametrize("row, adopting, mine", [
    ({"adapterId": "test-thing"}, False, True), ({"adapterId": "adapter-02"}, True, False),
    ({}, True, True), ({}, False, False), ({"releasedFrom": "test-thing"}, True, False),
])
def test_whose_row_it_is(monkeypatch, row, adopting, mine):
    monkeypatch.setattr(config, "ADOPT_UNSTAMPED_ROWS", adopting)
    assert camera_control.is_mine({"cameraId": "cam-09", **row}) is mine


def test_outage_supervisor_and_thumbnails(registry):
    registry(ROWS)
    assert set(outage_buffer._fetch_registry()) == MINE        # thumbnails.py reads this cache


def test_mediamtx_path_sync_and_rematch(registry):
    registry(ROWS)
    assert {c["cameraId"] for c in sync_mediamtx_paths.fetch_registry()} == MINE   # rematch_cameras reads it too


def test_event_watcher(registry):
    registry(ROWS, event_watcher)
    assert set(event_watcher.load_cameras()) == MINE


def test_codec_caps(monkeypatch, registry):
    monkeypatch.setattr(codec_caps, "probe", lambda item: {k: None for k in codec_caps.FIELDS})
    assert set(codec_caps.refresh(registry(ROWS), log=lambda msg: None)) == MINE


def test_schedule_runner(registry):
    registry(ROWS)
    assert set(schedule_runner.fetch_schedules()) == MINE


def test_a_pi_that_doesnt_adopt_leaves_unstamped_rows_alone(monkeypatch, registry):
    monkeypatch.setattr(config, "ADOPT_UNSTAMPED_ROWS", False)
    registry(ROWS)
    assert {c["cameraId"] for c in sync_mediamtx_paths.fetch_registry()} == {"cam-02"}


READERS = {
    "outage supervisor": lambda: set(outage_buffer._fetch_registry()),
    "path sync": lambda: {c["cameraId"] for c in sync_mediamtx_paths.fetch_registry()},
    "event watcher": lambda: set(event_watcher.load_cameras()),
    "schedule runner": lambda: set(schedule_runner.fetch_schedules()),
}


@pytest.mark.parametrize("reader", READERS)
def test_readers_read_this_pis_rows_by_the_index_never_a_scan(monkeypatch, registry, reader):
    # DEALER-PANEL-PLAN.md Phase 10.2c: once the role is narrowed (10.2f) a Pi may only query the
    # adapterId-index for its own Thing; a reader still scanning would then read nothing at all.
    monkeypatch.setattr(config, "ADOPT_UNSTAMPED_ROWS", False)
    table = registry(ROWS, event_watcher)
    monkeypatch.setattr(table, "scan", lambda **kw: pytest.fail(f"the {reader} scanned the whole table"))
    assert READERS[reader]() == {"cam-02"}


def test_codec_caps_reads_by_the_index_never_a_scan(monkeypatch, registry):
    monkeypatch.setattr(config, "ADOPT_UNSTAMPED_ROWS", False)
    monkeypatch.setattr(codec_caps, "probe", lambda item: {k: None for k in codec_caps.FIELDS})
    table = registry(ROWS)
    monkeypatch.setattr(table, "scan", lambda **kw: pytest.fail("codec_caps scanned the whole table"))
    assert set(codec_caps.refresh(table, log=lambda msg: None)) == {"cam-02"}
