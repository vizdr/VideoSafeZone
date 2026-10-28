"""event_watcher.ClipGate: when a detection becomes a clip (PYTEST.md, step 5).

Two rules, both measured in Phase 0: fire on the rising edge only, and not again within the
cooldown. The replay test runs the gate over the recorded cam-02 event logs in measurements/ the
way adapter/bin/replay-gate.py does, and pins the clip counts that script prints today -- the
numbers COSTS-1.4 §7.2's duty cycle rests on.
"""
import json
from datetime import datetime
from pathlib import Path

import pytest

from event_watcher import MODE_TOPICS, ClipGate

MEASUREMENTS = Path(__file__).resolve().parent.parent.parent / "measurements"


def test_rising_edge_fires():
    assert ClipGate(60).update(True, 0) == (True, "fire")


def test_a_held_detection_fires_once():
    gate = ClipGate(60)
    gate.update(True, 0)
    assert gate.update(True, 5) == (False, "already asserted")


def test_clear_then_assert_inside_the_cooldown_is_suppressed():
    gate = ClipGate(60)
    gate.update(True, 0)
    assert gate.update(False, 5) == (False, "cleared")
    fire, reason = gate.update(True, 30)
    assert not fire and reason.startswith("cooldown")


def test_fires_again_once_the_cooldown_has_passed():
    gate = ClipGate(60)
    gate.update(True, 0)
    gate.update(False, 5)
    assert gate.update(True, 60) == (True, "fire")


def test_suppressed_edges_do_not_restart_the_cooldown():
    gate = ClipGate(60)
    gate.update(True, 0)
    gate.update(False, 1)
    gate.update(True, 50)          # suppressed
    gate.update(False, 51)
    assert gate.update(True, 61)[0]


def _replay(log: Path, cooldown=60):
    """{mode: (rising edges, clips)} -- replay-gate.py's loop."""
    events = []
    for line in log.open(errors="replace"):
        line = line.strip("\x00 \t\r\n")
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("kind") == "event":
            events.append(rec)
    t0 = datetime.fromisoformat(events[0]["t"])
    out = {}
    for mode, topic in MODE_TOPICS.items():
        gate, rises, clips = ClipGate(cooldown), 0, 0
        for e in events:
            if e["topic"] != topic:
                continue
            raw = (e.get("data") or {}).get("Data.State") or (e.get("data") or {}).get("Data.IsMotion")
            if raw is None:
                continue
            state = str(raw).lower() == "true"
            if state and not gate.asserted:
                rises += 1
            fire, _ = gate.update(state, (datetime.fromisoformat(e["t"]) - t0).total_seconds())
            clips += fire
        out[mode] = (rises, clips)
    return out


@pytest.mark.parametrize("log, expected", [
    ("events-cam-02-2026-09-06.jsonl",
     {"motion": (98, 39), "cellMotion": (107, 42), "human": (127, 46)}),
    ("events-cam-02-2026-09-08-overnight.jsonl",
     {"motion": (1817, 687), "cellMotion": (1800, 665), "human": (2975, 853)}),
])
def test_replay_of_recorded_cam02_detections(log, expected):
    assert _replay(MEASUREMENTS / log) == expected
