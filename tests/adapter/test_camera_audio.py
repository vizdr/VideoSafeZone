"""adapter/bin/camera-audio.py: whether a producer records audio (PYTEST.md, Phase 2).

The producers `eval` its output once at start. Failure must be soft -- AUDIO=off and exit 0 -- so a
registry blip degrades to video-only instead of leaving the camera without a producer.
"""
import sys

import pytest

import aws_device_creds
from loaders import load_script

camera_audio = load_script("camera-audio")


def _run(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["camera-audio.py", *args])
    code = camera_audio.main()
    out, err = capsys.readouterr()
    return code, dict(line.split("=", 1) for line in out.splitlines()), err


@pytest.mark.parametrize("row, audio", [
    ({"audioCapable": True, "audioEnabled": True}, "on"),
    ({"audioCapable": True, "audioEnabled": False}, "off"),
    # a stale audioEnabled on a camera without a microphone: the pipeline would wait forever
    # for an audio pad that never delivers, and stall the video with it
    ({"audioCapable": False, "audioEnabled": True}, "off"),
    ({}, "off"),
])
def test_audio_needs_both_flags(registry, monkeypatch, capsys, row, audio):
    registry([{"cameraId": "cam-02", "audioCodec": "G711", "audioDevice": "", **row}])
    code, out, _ = _run(monkeypatch, capsys, "cam-02")
    assert code == 0 and out["AUDIO"] == audio
    assert out["AUDIO_CODEC"] == "G711"


def test_unknown_camera_is_video_only(registry, monkeypatch, capsys):
    registry([])
    assert _run(monkeypatch, capsys, "cam-09")[1]["AUDIO"] == "off"


def test_registry_unreachable_is_video_only_not_a_failure(monkeypatch, capsys):
    def unreachable(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(aws_device_creds, "get_session", unreachable)
    code, out, err = _run(monkeypatch, capsys, "cam-02")
    assert code == 0 and out == {"AUDIO": "off"}
    assert "video-only" in err


def test_wrong_usage_is_still_sourceable(monkeypatch, capsys):
    code, out, err = _run(monkeypatch, capsys)
    assert code == 0 and out == {"AUDIO": "off"} and "usage" in err
