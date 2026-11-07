"""activation.py (DEALER-PANEL-PLAN.md Phase 6.3): the adapter's activation as agent.py last heard it.

conftest moves STATE_FILE into the test's tmp_path; the Pi's own /run/user/<uid>/vms is never
touched.
"""
import json
from pathlib import Path

import pytest

import activation


def test_nothing_heard_refuses_as_not_activated_does():
    assert activation.load() is None
    assert "hasn't heard from the cloud" in activation.registration_refusal()


def test_saved_atomically_and_read_back():
    state = activation.save('{"activated": true, "at": "2026-11-07T20:00:00Z", "extra": 1}')
    assert state["activated"] is True and state["at"] == "2026-11-07T20:00:00Z"
    on_disk = json.loads(Path(activation.STATE_FILE).read_text())
    assert on_disk == state and "extra" not in on_disk
    assert [p.name for p in Path(activation.STATE_FILE).parent.iterdir()] == ["activation.json"]   # no temp left


def test_not_activated_says_how_to_activate():
    activation.save(b'{"activated": false}')
    assert "Cloud ID" in activation.registration_refusal()


@pytest.mark.parametrize("payload", [b"", b"null", b'{"activated": 1}', b'"activated"'])
def test_a_message_that_isnt_a_config_is_refused(payload):
    with pytest.raises(ValueError):
        activation.save(payload)


def test_a_damaged_file_reads_as_nothing_heard():
    Path(activation.STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(activation.STATE_FILE).write_text('{"activated": "maybe"')
    assert activation.load() is None
