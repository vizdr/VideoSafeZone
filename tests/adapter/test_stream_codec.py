"""adapter/bin/stream-codec.py: what a recording producer pulls, and what it records about it
(WEB-PANEL-PLAN.md D5, Phase 5.2).

The cloud page's sub-stream choice is read once at start and honoured only below a main-stream
ceiling and when MediaMTX has the live-view path; anything else -- or an unreachable registry --
records the camera's own stream. A change of codec or picture is stamped, so clip windows split
there (KVS refuses a clip whose codec private data changes, and the picture size is part of it).
"""
import sys

import pytest

import mediamtx_api
from fakes import FakeMediaMTX, path_state
from loaders import load_script

sc = load_script("stream-codec")

MAIN = {"source": "rtsp://admin:pw@192.0.2.13/main", "rtspTransport": "tcp"}
SUB = {"source": "rtsp://admin:pw@192.0.2.13/sub", "rtspTransport": "tcp"}


@pytest.fixture
def mtx(monkeypatch):
    def make(live_path=True):
        conf = {"cam03": MAIN, **({"cam03_live": SUB} if live_path else {})}
        fake = FakeMediaMTX(conf=conf, state={"cam03": path_state("H265", 2560, 1440),
                                              "cam03_live": path_state("H264", 800, 448)},
                            flowing={"cam03", "cam03_live"})
        monkeypatch.setattr(mediamtx_api, "requests", fake)
        return fake
    return make


def row(**over):
    return {"cameraId": "cam-03", "mode": "passthrough", "onvifHost": "192.0.2.13",
            "recordStream": "main", **over}


@pytest.mark.parametrize("fields, live_path, expect", [
    ({"recordQuality": {"stream": "sub"}}, True, ("cam03_live", True)),
    ({}, True, ("cam03", False)),                                          # no choice: the ceiling
    ({"recordQuality": {"stream": "sub"}}, False, ("cam03", False)),       # no live-view path
    ({"recordQuality": {"stream": "sub"}, "recordStream": "sub"}, True, ("cam03", False)),  # already sub
])
def test_the_recording_pulls_the_sub_stream_only_when_chosen_and_there(registry, mtx, fields, live_path, expect):
    registry([row(**fields)])
    mtx(live_path)
    assert sc.recorded_path("cam-03") == expect


def test_an_unreachable_registry_records_the_cameras_own_stream(monkeypatch, mtx, capsys):
    import aws_device_creds
    def no_aws(*a, **k):
        raise ConnectionError("no route to AWS")
    monkeypatch.setattr(aws_device_creds, "get_session", no_aws)
    mtx()
    assert sc.recorded_path("cam-03") == ("cam03", False)
    assert "registry unreachable (ConnectionError): recording the camera's own stream" in capsys.readouterr().err


def test_what_was_pulled_is_recorded_and_a_change_of_picture_is_stamped(registry):
    table = registry([row(recordQuality={"stream": "sub"}, videoCodecActive="h264",
                          recordQualityActive={"stream": "main", "size": "2560x1440"})])
    sc.record_registry(table, "cam-03", "h264", sub=True, size="800x448")
    r = table.row("cam-03")
    assert r["recordQualityActive"] == {"stream": "sub", "size": "800x448"}
    assert r["videoCodecActive"] == "h264"                        # same codec...
    assert r["videoCodecActiveSince"].endswith("Z")               # ...but another picture: split there


def test_a_same_picture_restart_writes_nothing(registry):
    active = {"stream": "main", "size": "2560x1440"}
    table = registry([row(videoCodecActive="h265", recordQualityActive=active)])
    sc.record_registry(table, "cam-03", "h265", sub=False, size="2560x1440")
    assert table.writes == []


def test_the_first_record_marks_no_boundary(registry):
    # Nothing earlier in the archive: no window to clamp clips to.
    table = registry([row()])
    sc.record_registry(table, "cam-03", "h265", sub=False, size="2560x1440")
    r = table.row("cam-03")
    assert r["recordQualityActive"] == {"stream": "main", "size": "2560x1440"}
    assert "videoCodecActiveSince" not in r


def test_a_codec_switch_is_still_stamped(registry):
    table = registry([row(videoCodecActive="h264", recordQualityActive={"stream": "main", "size": "2560x1440"})])
    sc.record_registry(table, "cam-03", "h265", sub=False, size="2560x1440")
    assert table.row("cam-03")["videoCodecActiveSince"].endswith("Z")


def test_main_prints_the_path_the_producer_must_pull(registry, mtx, monkeypatch, capsys):
    registry([row(recordQuality={"stream": "sub"}, recordQualityActive={"stream": "sub", "size": "800x448"},
                  videoCodecActive="h264")])
    mtx()
    monkeypatch.setattr(sys, "argv", ["stream-codec.py", "cam-03", "--wait", "1"])
    monkeypatch.setattr(mediamtx_api, "path_is_flowing", lambda path, sample_sec=1.0: True)
    assert sc.main() == 0
    out = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert (out["VIDEO_PATH"], out["VIDEO_CODEC"]) == ("cam03_live", "h264")
