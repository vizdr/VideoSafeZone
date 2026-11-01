#!/usr/bin/env python3
"""Camera thumbnails for the web panel (WEB-PANEL-PLAN.md Phase 5.3), every 10 minutes.

kvs-thumbnails.timer runs this. For each registered camera whose MediaMTX path has video actually
flowing (its byte counter growing, never `ready` alone -- FoundAndFixed.md #50), one frame is
decoded locally from MediaMTX -- from the sub stream when the camera has one, the smaller picture
-- scaled to at most 640 px wide and uploaded to the evidence bucket as thumbs/<cameraId>.jpg.
list-cameras hands the page a short-lived link to it. No camera is asked anything, and nothing
outside the Pi sees MediaMTX; a camera without video keeps its last thumbnail.

The camera list is the outage supervisor's registry cache (registered cameras only, no
credentials), so a run needs AWS only for the upload. Cost: one small S3 PUT per camera per run.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import camera_control
import config
import mediamtx_api
import outage_buffer

GRAB_TIMEOUT_SEC = 20          # an RTSP connect plus up to one keyframe interval
MAX_WIDTH = 640


def log(msg: str) -> None:
    print(f"thumbnails: {msg}", flush=True)


def source_path(camera_id: str) -> str:
    """The camera's live-view path (its sub stream) when MediaMTX has one, else its recorded path."""
    live = camera_control.live_path_name(camera_id)
    try:
        if mediamtx_api.get_path_conf(live) is not None:
            return live
    except Exception:  # noqa: BLE001 -- MediaMTX not answering: the flow check below says no
        pass
    return camera_control.mediamtx_path_name(camera_id)


def grab(path: str, out: Path) -> bool:
    """One frame of `path` into `out` as JPEG; False when ffmpeg can't get one in time."""
    try:
        r = subprocess.run(
            # -skip_frame nokey: decode keyframes only. Joining a stream between keyframes, the
            # first frame out is otherwise a P-frame decoded without its reference -- a grey
            # picture with only the moving edges drawn (seen on cam-03). The wait is at most one
            # keyframe interval.
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp", "-skip_frame", "nokey",
             "-i", f"rtsp://127.0.0.1:8554/{path}", "-frames:v", "1",
             "-vf", f"scale='min({MAX_WIDTH},iw)':-2", "-q:v", "6", "-y", str(out)],
            capture_output=True, text=True, timeout=GRAB_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        log(f"{path}: no frame within {GRAB_TIMEOUT_SEC} s")
        return False
    if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        log(f"{path}: ffmpeg failed ({(r.stderr or '').strip()[:200]})")
        return False
    return True


def upload(camera_id: str, data: bytes) -> None:
    from botocore.config import Config
    from aws_device_creds import get_session
    quick = Config(connect_timeout=3, read_timeout=10, retries={"max_attempts": 1})
    get_session().client("s3", config=quick).put_object(
        Bucket=config.EVIDENCE_BUCKET, Key=f"thumbs/{camera_id}.jpg", Body=data,
        ContentType="image/jpeg", CacheControl="no-cache")


def run() -> int:
    """Returns how many thumbnails went up."""
    cameras = sorted(outage_buffer.read_registry_cache())
    if not cameras:
        log("no registry cache yet (the outage supervisor writes it): nothing to do")
        return 0
    done = 0
    with tempfile.TemporaryDirectory(prefix="vms-thumbs-") as tmp:
        for camera_id in cameras:
            path = source_path(camera_id)
            if not mediamtx_api.path_is_flowing(path):
                continue                            # no video now: the last thumbnail stays
            out = Path(tmp) / f"{camera_id}.jpg"
            if not grab(path, out):
                continue
            try:
                upload(camera_id, out.read_bytes())
                done += 1
            except Exception as e:  # noqa: BLE001 -- AWS unreachable: the next run tries again
                log(f"{camera_id}: upload failed ({type(e).__name__}: {e})")
    return done


def main() -> int:
    n = run()
    log(f"{n} thumbnail(s) uploaded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
