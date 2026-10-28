"""camera_control: the one place cam-03 becomes cam03 and a unit name (PYTEST.md, step 5).

Two bugs came from getting a name wrong, and systemctl hid both by reporting a unit that doesn't
exist as merely "inactive": #7 (kvs-cam-02.service) and #37 (the kvs-cam@ family missed).
"""
from pathlib import Path

import pytest

import camera_control as cc


def _provision(camera_path: str):
    """What provision-camera.sh leaves behind for a GUI-registered camera: its channel file."""
    d = Path(cc.CHANNELS_DIR)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{camera_path}.env").write_text("")


@pytest.mark.parametrize("camera_id, path", [
    ("cam-01", "cam01"), ("cam-02", "cam02"), ("cam-03", "cam03"), ("cam-12", "cam12"),
])
def test_mediamtx_path_name_drops_the_hyphen(camera_id, path):
    assert cc.mediamtx_path_name(camera_id) == path


def test_ff07_legacy_unit_name_has_no_hyphen():
    # agent.py once built f"kvs-{camera_id}.service" -> kvs-cam-02.service, which doesn't exist.
    assert cc.unit_name("cam-02") == "kvs-cam02.service"
    assert cc.unit_name("cam-01") == "kvs-cam01.service"


def test_ff37_gui_registered_camera_uses_the_template_unit():
    _provision("cam03")
    assert cc.unit_name("cam-03") == "kvs-cam@cam03.service"


def test_ff37_without_a_channel_file_the_legacy_name_is_used():
    _provision("cam04")             # another camera's file must not count
    assert cc.unit_name("cam-03") == "kvs-cam03.service"


def test_live_names():
    assert cc.live_path_name("cam-03") == "cam03_live"
    assert cc.live_stream_name("cam-03") == "cam-03-live"
    assert cc.live_unit_name("cam-03") == "kvs-live@cam03.service"


def test_live_stream_stays_inside_the_iam_wildcard():
    # The producer role allows stream/cam-*/*; a live stream name outside it would need an IAM edit.
    assert cc.live_stream_name("cam-07").startswith("cam-")


@pytest.mark.parametrize("row, expected", [
    ({}, False),
    ({"unregisteredAt": None}, False),
    ({"unregisteredAt": ""}, False),
    ({"unregisteredAt": "2026-10-25T12:00:00+00:00"}, True),
])
def test_is_unregistered(row, expected):
    assert cc.is_unregistered(row) is expected


@pytest.mark.parametrize("on, action", [(True, "start"), (False, "stop")])
def test_ff37_set_stream_targets_the_template_unit(fake_run, on, action):
    _provision("cam03")
    fake = fake_run(cc)
    cc.set_stream("cam-03", on)
    assert fake.calls == [["sudo", "systemctl", action, "kvs-cam@cam03.service"]]


def test_set_boot_start_never_uses_now(fake_run):
    # Whether the producer runs right now is set_stream's job (FoundAndFixed.md #81).
    fake = fake_run(cc)
    cc.set_boot_start("cam-01", False)
    assert fake.calls == [["sudo", "systemctl", "disable", "kvs-cam01.service"]]


@pytest.mark.parametrize("load_state, expected", [
    ("loaded\n", True), ("not-found\n", False), ("", False),
])
def test_has_producer_reads_load_state(fake_run, load_state, expected):
    fake = fake_run(cc, {("systemctl", "show"): load_state})
    assert cc.has_producer("cam-02") is expected
    assert fake.calls[0][-1] == "kvs-cam02.service"


def test_get_stream_status_reads_stdout_whatever_the_exit_code(fake_run):
    # `systemctl is-active` exits 3 for "inactive" -- the word is still the answer.
    fake_run(cc, {("systemctl", "is-active"): ("inactive\n", 3)})
    assert cc.get_stream_status("cam-01") == "inactive"


@pytest.mark.parametrize("mode", ["auto", "On", "NIGHT", ""])
def test_set_ir_mode_rejects_unknown_modes_before_any_camera_call(mode):
    with pytest.raises(ValueError):
        cc.set_ir_mode({"onvifHost": "192.0.2.1"}, mode)
