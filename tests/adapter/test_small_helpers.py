"""Small helpers: hiding credentials in logs, following a camera to a new address, and counting
covered seconds (PYTEST.md, step 5)."""
from datetime import datetime, timedelta, timezone

import pytest

import rematch_cameras
import sync_mediamtx_paths
from loaders import load_script

gap_fill = load_script("gap-fill")


@pytest.mark.parametrize("url, expected", [
    ("rtsp://admin:s3cr:et@192.0.2.10:554/Streaming/101", "rtsp://admin:***@192.0.2.10:554/Streaming/101"),
    ("rtsps://admin:pw@192.0.2.10/s", "rtsps://admin:***@192.0.2.10/s"),
    ("rtsp://192.0.2.11/live?username=admin&password=abc&ch=1",
     "rtsp://192.0.2.11/live?username=admin&password=***&ch=1"),
    ("rtsp://192.0.2.12/s", "rtsp://192.0.2.12/s"),
])
def test_redact_hides_rtsp_passwords_from_the_journal(url, expected):
    # cam-02's credentials once ended up in git (FoundAndFixed.md #31); the journal is next.
    assert sync_mediamtx_paths.redact(url) == expected


@pytest.mark.parametrize("url, old, new, expected", [
    ("rtsp://admin:pw@192.168.178.67:554/s", "192.168.178.67", "192.168.178.80",
     "rtsp://admin:pw@192.168.178.80:554/s"),
    ("rtsp://192.168.178.67/s?ch=1", "192.168.178.67", "192.168.178.80", "rtsp://192.168.178.80/s?ch=1"),
    # the password contains the old address: only the host changes
    ("rtsp://admin:192.168.178.67@192.168.178.67/s", "192.168.178.67", "192.168.178.80",
     "rtsp://admin:192.168.178.67@192.168.178.80/s"),
])
def test_move_url_replaces_only_the_host(url, old, new, expected):
    assert rematch_cameras.move_url(url, old, new) == expected


def test_move_url_refuses_a_url_pointing_elsewhere():
    # Edited by hand to point somewhere else: rewriting it would be a guess.
    assert rematch_cameras.move_url("rtsp://192.0.2.99/s", "192.168.178.67", "192.168.178.80") is None


T0 = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _iv(a, b):
    return (T0 + timedelta(seconds=a), T0 + timedelta(seconds=b))


@pytest.mark.parametrize("intervals, expected", [
    ([], 0.0),
    ([_iv(0, 10)], 10.0),
    ([_iv(0, 10), _iv(5, 15)], 15.0),                  # overlap counted once
    ([_iv(20, 30), _iv(0, 10)], 20.0),                 # any order
    ([_iv(0, 10), _iv(10, 20)], 20.0),                 # touching
    ([_iv(-50, 10), _iv(90, 200)], 20.0),              # clipped to [start, end]
    ([_iv(200, 300)], 0.0),                            # wholly outside
    ([_iv(0, 50), _iv(10, 20), _iv(30, 40)], 50.0),    # nested
])
def test_union_seconds(intervals, expected):
    start, end = _iv(0, 100)
    assert gap_fill.union_seconds(intervals, start, end) == expected
