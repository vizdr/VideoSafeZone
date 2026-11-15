"""setup/setup-launch.sh, step 2: the cameras that need a producer on this Pi (FoundAndFixed.md #98).

The step's Python is run here as the script runs it -- taken from the script itself -- on the test
registry. After the device role was narrowed (DEALER-PANEL-PLAN.md Phase 10.2f) a Pi may read only
its own rows, by the adapterId-index: the step used to scan the whole table, and a refused scan left
an empty list that the script reported as "every registered camera already has its producer".
"""
import pathlib
import re

import pytest

import camera_control
import config

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / "setup/setup-launch.sh").read_text()


def step2_python():
    step = SCRIPT[SCRIPT.index('step "2. producers'):SCRIPT.index('step "3. proofs')]
    return re.search(r"<<'PY'\n(.*?)\nPY\n", step, re.S).group(1)


def test_ff98_the_cameras_needing_a_producer_come_from_this_adapters_rows_by_the_index(
        registry, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "ADOPT_UNSTAMPED_ROWS", False)
    mine = {"adapterId": "test-thing", "mode": "passthrough"}
    table = registry([
        {"cameraId": "cam-01", "adapterId": "test-thing", "mode": "transcode"},   # USB: its own unit
        {"cameraId": "cam-02", **mine},                                          # its own unit too
        {"cameraId": "cam-03", **mine},                                          # has its channel file
        {"cameraId": "cam-05", **mine},                                          # needs one
        {"cameraId": "cam-06", **mine, "unregisteredAt": "2026-11-01T00:00:00Z"},
        {"cameraId": "cam-07", "adapterId": "adapter-02", "mode": "passthrough"},  # another adapter's
        {"cameraId": "cam-08", "releasedFrom": "test-thing", "mode": "passthrough"},
    ])
    monkeypatch.setattr(table, "scan", lambda **kw: pytest.fail("scanned the whole table"))
    monkeypatch.setattr(table, "get_item", lambda **kw: pytest.fail("read a row by its key"))
    channels = tmp_path / "channels"
    channels.mkdir()
    (channels / "cam03.env").touch()
    monkeypatch.setattr(camera_control, "CHANNELS_DIR", str(channels))
    monkeypatch.setenv("SETUP_VMS_HOME", str(ROOT))
    exec(compile(step2_python(), "setup-launch.sh step 2", "exec"), {})
    assert capsys.readouterr().out == "cam-05 cam05\n"


def test_ff98_a_registry_read_that_fails_is_a_failure_not_all_done():
    step = SCRIPT[SCRIPT.index('step "2. producers'):SCRIPT.index('step "3. proofs')]
    assert ".scan(" not in step and "registry_reads.own_rows" in step
    assert re.search(r"^else\n  proof .*false\nfi", step, re.M), "a failed read must count as a FAIL"
    assert step.index("if TODO_OUT=$(") < step.index('ok "every registered camera already has its producer"')
