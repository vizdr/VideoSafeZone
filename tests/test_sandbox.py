"""The guards in conftest.py hold (PYTEST.md, step 3).

If one of these fails, stop: the other tests may be reading the Pi's real configuration or
reaching the real MediaMTX, AWS account or systemd.
"""
import os
import socket
import subprocess
from pathlib import Path

import pytest
import requests

import camera_control
import config
import outage_buffer


def test_config_reads_the_fake_identity_not_the_pis():
    assert config.CONFIG_FILE.endswith("tests/fixtures/adapter.env")
    assert config.THING_NAME == "test-thing"
    assert config.TOPIC_PREFIX == "adapter/test-thing"
    assert config.IOT_DATA_ENDPOINT.endswith(".invalid")


def test_aws_has_fake_credentials_and_no_config_files():
    assert os.environ["AWS_ACCESS_KEY_ID"] == "testing"
    assert os.environ["AWS_CONFIG_FILE"] == os.devnull
    assert os.environ["AWS_SHARED_CREDENTIALS_FILE"] == os.devnull
    assert "AWS_PROFILE" not in os.environ


def test_home_and_runtime_dirs_are_throwaway():
    for key in ("HOME", "XDG_RUNTIME_DIR", "XDG_STATE_HOME"):
        assert "vsz-tests-" in os.environ[key], key
    assert "VMS_HOME" not in os.environ


def test_mediamtx_api_on_localhost_is_unreachable(expect_blocked):
    # The real MediaMTX listens there on the Pi: a missing fake must fail, not reach it.
    with pytest.raises(RuntimeError, match="blocked in tests"):
        requests.get("http://127.0.0.1:9997/v3/paths/list", timeout=1)
    assert expect_blocked


def test_dns_and_udp_are_blocked(expect_blocked):
    with pytest.raises(RuntimeError):
        socket.getaddrinfo("example.com", 443)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(RuntimeError):
            s.sendto(b"probe", ("239.255.255.250", 3702))    # WS-Discovery's multicast group
    finally:
        s.close()
    assert len(expect_blocked) == 2


@pytest.mark.parametrize("argv", [
    ["systemctl", "is-active", "kvs-cam01.service"],
    ["sudo", "systemctl", "start", "kvs-cam01.service"],
    ["/usr/bin/ffprobe", "-v", "error", "x.mp4"],
    "gst-launch-1.0 videotestsrc ! fakesink",
])
def test_commands_are_blocked(argv, expect_blocked):
    with pytest.raises(RuntimeError, match="blocked in tests"):
        subprocess.run(argv, shell=isinstance(argv, str))
    assert expect_blocked


def test_a_swallowed_attempt_is_still_recorded(expect_blocked):
    # outage_uploader.ffprobe() catches every exception; the record is what fails such a test.
    try:
        subprocess.run(["ffprobe", "x.mp4"])
    except Exception:
        pass
    assert expect_blocked == ["command ['ffprobe', 'x.mp4']"]


def test_absolute_paths_point_into_tmp_path(tmp_path):
    assert camera_control.CHANNELS_DIR.startswith(str(tmp_path))
    assert str(outage_buffer.BUFFER_ROOT).startswith(str(tmp_path))
    assert isinstance(outage_buffer.BUFFER_ROOT, Path)
    assert isinstance(camera_control.CHANNELS_DIR, str)


def test_fake_run_records_and_answers(fake_run):
    fake = fake_run(camera_control, {("systemctl", "is-active"): "active\n"})
    assert camera_control.get_stream_status("cam-01") == "active"
    assert fake.calls == [["systemctl", "is-active", "kvs-cam01.service"]]
