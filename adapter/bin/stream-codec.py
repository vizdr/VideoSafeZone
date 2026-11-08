#!/usr/bin/env python3
"""Print the video codec a camera's MediaMTX path is actually receiving, shell-sourceable:

    eval "$(stream-codec.py cam-02)"      # sets VIDEO_CODEC=h264|h265, AUDIO_TRACK

AUDIO_TRACK is the audio MediaMTX receives on the same path: pcma or pcmu (G.711 by law, which
ONVIF can't tell apart and the depayloader must), another codec's name, or "none". The audio
choice itself stays the registry's (camera-audio.py); this only says what is there to take.

The producer builds its depayloader from this, not from the registry. A camera's codec is
set on the camera itself (admin GUI -> onvif_media2.py), so what MediaMTX receives is the
truth -- and asking it needs no AWS, which keeps every producer startable while AWS is
unreachable (the rule cam-01 already lives by).

Waits up to --wait seconds for the path to come up with H.264 or H.265 video that is
actually flowing (its byte counter growing -- `ready` alone is not proof, #50). If it does
not, prints nothing and exits 1, so the unit fails and systemd retries (Restart=on-failure)
-- what a dead source has always meant for a producer.

    eval "$(stream-codec.py --live cam-03)"   # the live view's path (cam03_live)

With --live it reads the camera's live-view path (cam03_live) when it has one, else the
recorded stream's path (a camera without a sub stream), says which as VIDEO_PATH, and corrects
only the live KVS stream's MediaType (cam-03-live). The registry's videoCodecActive describes the *recorded* stream, which
clip windows are clamped to, so the live view's codec goes to its own additive field,
liveVideoCodecActive, which only the cloud page's H.265 warning for the Live player reads: a
camera can record H.264 on main while its sub stream, the live view, stays H.265 (cam-04).

For the recording producer the path is the camera's recorded one -- unless the cloud page chose
the sub stream (recordQuality {"stream": "sub"} below a main-stream ceiling, WEB-PANEL-PLAN.md
D5): then it is the live-view path, which MediaMTX already pulls continuously. Read once, at start,
like audioEnabled (camera-audio.py), so a choice applies from the next Start; on a short budget, and
an unreachable registry means the recorded stream -- the most the camera records, never no producer.

When what it receives differs from the last one recorded, it also records the change,
soft-failing and on a hard time budget so AWS can never delay a start:
  - registry: videoCodecActive and recordQualityActive ({"stream", "size"}: what it pulled), plus
    videoCodecActiveSince when either replaces another: a new codec, or another picture -- the
    sub stream instead of main, or a stream switched in an admin GUI. Clip windows get clamped to
    it: KVS refuses any clip or HLS session whose fragments' codec private data differs, and the
    picture size is part of it, as the codec is (measurements/codec-phase0.md §4).
  - KVS: the stream's MediaType, which consumers such as the console player read.
    UpdateStream, not a new stream -- the archive and ARN stay (codec-phase0.md §3).
"""
import argparse
import re
import datetime as dt
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import camera_control  # noqa: E402
import mediamtx_api    # noqa: E402

RECORD_BUDGET_SEC = 8
QUALITY_BUDGET_SEC = 6


def wait_for_codec(path: str, wait: int) -> str | None:
    """The path's video codec once media is actually *flowing* -- not merely `ready`.
    A source can connect, declare its tracks and stall, and MediaMTX keeps calling that
    path ready (FoundAndFixed.md #50); starting a producer on it only builds a pipeline
    that waits for frames that never come. So the byte counter has to grow first."""
    deadline = time.monotonic() + wait
    while True:
        codec = mediamtx_api.path_video_codec(path)
        if codec and mediamtx_api.path_is_flowing(path):
            return codec
        if time.monotonic() >= deadline:
            return None
        time.sleep(1)


def chosen_sub(camera_id: str) -> bool:
    """Whether the cloud page chose the sub stream for this camera's recording: recordQuality
    {"stream": "sub"} below a main-stream ceiling (set-camera-settings checks the same)."""
    try:
        from botocore.config import Config
        from aws_device_creds import get_session   # loads config; inside the try on purpose
        quick = Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1})
        item = (get_session().resource("dynamodb", config=quick).Table("cameras")
                .get_item(Key={"cameraId": camera_id}).get("Item") or {})
    except Exception as e:  # noqa: BLE001 -- the camera's own stream is always a safe choice
        print(f"stream-codec.py: registry unreachable ({type(e).__name__}): recording the camera's "
              f"own stream", file=sys.stderr)
        return False
    return (item.get("recordQuality") or {}).get("stream") == "sub" and item.get("recordStream") == "main"


def recorded_path(camera_id: str) -> tuple[str, bool]:
    """(MediaMTX path, whether it is the sub stream) for the recording producer."""
    path = camera_control.mediamtx_path_name(camera_id)
    box = {}
    t = threading.Thread(target=lambda: box.update(sub=chosen_sub(camera_id)), daemon=True)
    t.start()
    t.join(QUALITY_BUDGET_SEC)
    if not box.get("sub"):
        return path, False
    live = camera_control.live_path_name(camera_id)
    try:
        if mediamtx_api.get_path_conf(live) is not None:
            return live, True
    except Exception:  # noqa: BLE001 -- MediaMTX not answering: the codec wait fails anyway
        pass
    print(f"stream-codec.py: {camera_id}: the sub stream was chosen, but MediaMTX has no "
          f"'{live}' path -- recording the camera's own stream", file=sys.stderr)
    return path, False


def record(camera_id: str, codec: str, stream: str, live: bool,
           sub: bool = False, size: str | None = None) -> None:
    try:
        from botocore.config import Config
        from aws_device_creds import get_session   # loads config; inside the try on purpose
        quick = Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1})
        session = get_session()
        table = session.resource("dynamodb", config=quick).Table("cameras")
        if live:
            record_live_registry(table, camera_id, codec)
        else:
            record_registry(table, camera_id, codec, sub, size)
        record_media_type(session.client("kinesisvideo", config=quick), stream, codec)
    except Exception as e:  # noqa: BLE001 -- recording is best effort, streaming is not
        print(f"stream-codec.py: could not record {codec} for {stream} "
              f"({type(e).__name__}: {e}); streaming anyway", file=sys.stderr)


def record_registry(table, camera_id: str, codec: str, sub: bool = False,
                    size: str | None = None) -> None:
    item = table.get_item(Key={"cameraId": camera_id}).get("Item") or {}
    prev, prev_active = item.get("videoCodecActive"), item.get("recordQualityActive")
    active = {"stream": "sub" if sub else (item.get("recordStream") or "recorded"),
              **({"size": size} if size else {})}
    sets, values = [], {}
    if prev != codec:
        sets.append("videoCodecActive = :c")
        values[":c"] = codec
    if prev_active != active:
        sets.append("recordQualityActive = :q")
        values[":q"] = active
    # "Since" only marks a real switch -- of codec or of picture. On the very first record there
    # is nothing earlier in the archive, so there is no boundary to clamp clips to.
    picture = lambda a: (a.get("stream"), a.get("size"))   # noqa: E731
    if (prev and prev != codec) or (prev_active and picture(prev_active) != picture(active)):
        sets.append("videoCodecActiveSince = :t")
        values[":t"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if sets and _update_camera(table, camera_id, "SET " + ", ".join(sets), values):
        print(f"stream-codec.py: {camera_id} now streams {codec} {active} "
              f"(was {prev or 'unrecorded'} {prev_active or ''})", file=sys.stderr)


def record_live_registry(table, camera_id: str, codec: str) -> None:
    prev = (table.get_item(Key={"cameraId": camera_id}).get("Item") or {}).get("liveVideoCodecActive")
    if prev != codec and _update_camera(table, camera_id, "SET liveVideoCodecActive = :c", {":c": codec}):
        print(f"stream-codec.py: {camera_id} live view now {codec} (was {prev or 'unrecorded'})",
              file=sys.stderr)


def _update_camera(table, camera_id: str, expression: str, values: dict) -> bool:
    """An update of an existing camera's row only. DynamoDB's UpdateItem creates the item when the
    key is new: a mistyped ID -- a MediaMTX path, cam01 for cam-01 -- made a registry row that the
    readers then took for a camera (FoundAndFixed.md #93)."""
    try:
        table.update_item(Key={"cameraId": camera_id}, UpdateExpression=expression,
                          ConditionExpression="attribute_exists(cameraId)", ExpressionAttributeValues=values)
        return True
    except Exception as e:  # noqa: BLE001
        if "ConditionalCheckFailed" not in type(e).__name__ + str(getattr(e, "response", "")):
            raise
        print(f"stream-codec.py: no registry row for {camera_id}: nothing recorded", file=sys.stderr)
        return False


def record_media_type(kv, stream: str, codec: str) -> None:
    info = kv.describe_stream(StreamName=stream)["StreamInfo"]
    parts = [p for p in (info.get("MediaType") or "").split(",") if p]
    want = f"video/{codec}"
    if want not in parts:
        # Replace only the video part; keep anything else (e.g. an audio type) as set.
        media_type = ",".join([want] + [p for p in parts if not p.startswith("video/")])
        kv.update_stream(StreamName=stream, CurrentVersion=info["Version"], MediaType=media_type)
        print(f"stream-codec.py: {stream} KVS MediaType -> {media_type}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("camera_id")
    ap.add_argument("--wait", type=int, default=30, help="seconds to wait for the source")
    ap.add_argument("--live", action="store_true",
                    help="the camera's live-view path and KVS stream")
    args = ap.parse_args()
    # A camera ID, never a MediaMTX path: cam-01, not cam01 (FoundAndFixed.md #93).
    if not re.fullmatch(r"cam-\d{2,4}", args.camera_id):
        print(f"stream-codec.py: '{args.camera_id}' isn't a camera ID (cam-01, not the path cam01)",
              file=sys.stderr)
        return 2

    if args.live:
        path = camera_control.live_path_name(args.camera_id)
        try:
            if mediamtx_api.get_path_conf(path) is None:
                path = camera_control.mediamtx_path_name(args.camera_id)
        except Exception:  # noqa: BLE001 -- MediaMTX not answering: the wait below fails anyway
            pass
        stream = camera_control.live_stream_name(args.camera_id)
    else:
        (path, sub), stream = recorded_path(args.camera_id), args.camera_id
    codec = wait_for_codec(path, args.wait)
    if not codec:
        print(f"stream-codec.py: no H.264/H.265 video flowing on MediaMTX path '{path}' after "
              f"{args.wait}s", file=sys.stderr)
        return 1

    track = mediamtx_api.path_video_track(path) or {}
    size = f"{track['width']}x{track['height']}" if track.get("width") and track.get("height") else None
    t = threading.Thread(target=record, args=(args.camera_id, codec, stream, args.live,
                                              not args.live and sub, size), daemon=True)
    t.start()
    t.join(RECORD_BUDGET_SEC)
    if t.is_alive():
        print(f"stream-codec.py: AWS slow -- not waiting to record {codec}; next start retries",
              file=sys.stderr)
    print(f"VIDEO_CODEC={codec}")
    print(f"VIDEO_PATH={path}")
    audio = mediamtx_api.path_audio_track(path)
    print(f"AUDIO_TRACK={audio['codec'] if audio else 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
