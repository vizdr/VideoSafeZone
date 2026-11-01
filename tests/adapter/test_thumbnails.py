"""thumbnails.py: one frame per camera with video, uploaded for the web panel (WEB-PANEL-PLAN.md
Phase 5.3). From the sub stream when there is one; only from a path whose bytes are flowing (#50);
a camera without video, or ffmpeg failing, keeps its last thumbnail."""
import subprocess

import pytest

import mediamtx_api
import outage_buffer
import thumbnails as th
from fakes import FakeMediaMTX, ModuleShim, path_state

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


@pytest.fixture
def world(monkeypatch, registry):
    """cam-02 (no sub stream) and cam-03 (with one) in the registry cache; ffmpeg faked."""
    def make(flowing=("cam02", "cam03_live"), ffmpeg_ok=True):
        outage_buffer.STATE_DIR.mkdir(parents=True, exist_ok=True)
        outage_buffer.REGISTRY_CACHE.write_text('{"cam-02": 0, "cam-03": 120}')
        conf = {"cam02": {}, "cam03": {}, "cam03_live": {}}
        monkeypatch.setattr(mediamtx_api, "requests", FakeMediaMTX(
            conf=conf, state={p: path_state("H264") for p in conf}))
        monkeypatch.setattr(mediamtx_api, "path_is_flowing", lambda path, sample_sec=1.0: path in flowing)
        calls = []

        def ffmpeg(argv, *a, **k):
            calls.append(argv)
            if ffmpeg_ok:
                open(argv[-1], "wb").write(JPEG)
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.CompletedProcess(argv, 1, "", "Connection refused")
        monkeypatch.setattr(th, "subprocess", ModuleShim(subprocess, run=ffmpeg))
        table = registry([])
        return calls, table.session.stub("s3")
    return make


def put(camera_id):
    return {"Bucket": "test-evidence-bucket", "Key": f"thumbs/{camera_id}.jpg", "Body": JPEG,
            "ContentType": "image/jpeg", "CacheControl": "no-cache"}


def test_one_frame_per_camera_from_its_smallest_stream(world):
    calls, s3 = world()
    s3.add_response("put_object", {}, put("cam-02"))
    s3.add_response("put_object", {}, put("cam-03"))
    assert th.run() == 2
    assert [c[c.index("-i") + 1] for c in calls] == ["rtsp://127.0.0.1:8554/cam02",
                                                    "rtsp://127.0.0.1:8554/cam03_live"]  # the sub stream


def test_a_camera_without_flowing_video_keeps_its_last_thumbnail(world):
    calls, s3 = world(flowing=("cam03_live",))
    s3.add_response("put_object", {}, put("cam-03"))
    assert th.run() == 1
    assert len(calls) == 1


def test_a_failed_grab_uploads_nothing(world, capsys):
    calls, s3 = world(ffmpeg_ok=False)
    assert th.run() == 0
    assert "ffmpeg failed (Connection refused)" in capsys.readouterr().out


def test_an_upload_failure_is_logged_and_the_others_still_go(world, capsys):
    calls, s3 = world()
    s3.add_client_error("put_object", "AccessDenied", expected_params=put("cam-02"))
    s3.add_response("put_object", {}, put("cam-03"))
    assert th.run() == 1
    assert "cam-02: upload failed (AccessDenied" in capsys.readouterr().out


def test_no_registry_cache_no_run(monkeypatch, capsys):
    assert th.run() == 0
    assert "no registry cache yet" in capsys.readouterr().out


def test_only_keyframes_are_decoded(world, monkeypatch):
    # Joining between keyframes, the first frame out was a P-frame without its reference: a grey
    # picture with only the edges of what moved (seen on cam-03, 2026-11-01). The flag must come
    # before -i, where it applies to the decoder.
    calls, s3 = world(flowing=("cam02",))
    s3.add_response("put_object", {}, put("cam-02"))
    th.run()
    argv = calls[0]
    assert argv[argv.index("-skip_frame") + 1] == "nokey" and argv.index("-skip_frame") < argv.index("-i")
