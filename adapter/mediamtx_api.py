"""MediaMTX control-API access, in one place.

`adapter/onvif-admin/app.py` grew the first copy of these calls; the outage-buffer
supervisor would have been the second. Factored out before that happened.

The API is localhost-only (mediamtx.yml binds 127.0.0.1:9997), so there is no auth here
and none is needed -- reaching it already means local access to the Pi.

**Which fields can be changed in place matters more than it looks.** MediaMTX decides
per-update whether a path config can be applied live or needs the path closed and
recreated (`core.pathConfCanBeUpdated`), and it compares a fixed whitelist of fields.
Recreating a path disconnects every reader -- a running KVS producer included. The record
fields used by the outage buffer ARE in that whitelist; `source` is NOT. So changing a
camera's source URI costs a reconnect and changing its recording settings does not.
"""
import re
import time
from decimal import Decimal

import requests

API = "http://127.0.0.1:9997"
TIMEOUT = 5


class MediaMTXError(RuntimeError):
    pass


def get_path_conf(path: str) -> dict | None:
    """Configured settings for a path, or None if it has no explicit entry.

    Returns None rather than raising for the "not found" case, which is normal: a path
    served by the `all_others` catch-all has no config of its own. That is exactly why
    cam01 needs an explicit entry before its record settings can be patched.
    """
    r = requests.get(f"{API}/v3/config/paths/get/{path}", timeout=TIMEOUT)
    if r.status_code == 404 or (r.ok and r.json().get("status") == "error"):
        return None
    if not r.ok:
        raise MediaMTXError(f"get conf {path}: {r.status_code} {r.text}")
    return r.json()


def get_path_state(path: str) -> dict | None:
    """Live runtime state (ready, tracks, readyTime, source), not configuration."""
    r = requests.get(f"{API}/v3/paths/get/{path}", timeout=TIMEOUT)
    if not r.ok:
        return None
    return r.json()


def path_video_codec(path: str) -> str | None:
    """The video codec MediaMTX is actually receiving on `path` -- "h264" or "h265" -- or
    None when the path is not ready (source offline, publisher stopped) or carries neither.

    This is ground truth, unlike anything a camera *reports* about its own encoder: it is
    what every reader (preview, outage buffer, KVS producer) will get. `tracks` is a list
    of codec names here ("H264", "G711"); a codec change shows up after the source
    reconnects, which a camera-side encoder switch forces (measurements/codec-phase0.md §2).
    """
    try:
        state = get_path_state(path)
    except requests.RequestException:
        return None
    if not state or not state.get("ready"):
        return None
    for track in state.get("tracks") or []:
        name = track if isinstance(track, str) else (track or {}).get("codec", "")
        if name in ("H264", "H265"):
            return name.lower()
    return None


def path_video_track(path: str) -> dict | None:
    """{"codec": "h264"|"h265", "width", "height"} of the video MediaMTX is receiving on
    `path`, or None -- path_video_codec() plus the picture size, for the admin GUI's codec
    and stream switches, which show what actually arrives and keep the size across a switch."""
    try:
        state = get_path_state(path)
    except requests.RequestException:
        return None
    if not state or not state.get("ready"):
        return None
    for track in state.get("tracks2") or []:
        if track.get("codec") in ("H264", "H265"):
            props = track.get("codecProps") or {}
            return {"codec": track["codec"].lower(), "width": props.get("width"),
                    "height": props.get("height")}
    return None


def path_audio_track(path: str) -> dict | None:
    """{"codec": "pcma"|"pcmu"|<other, lowercased>, "sampleRate", "channels"} of the audio
    MediaMTX is receiving on `path`, or None (no audio track, or the path is not ready).

    G.711 is split by law because the producer needs a different depayloader for each, and
    ONVIF can't say which: its enum is just "G711". The wire can, and a camera's own audio page
    can change it (cam-02 went from A-law to mu-law, FoundAndFixed.md #71)."""
    try:
        state = get_path_state(path)
    except requests.RequestException:
        return None
    if not state or not state.get("ready"):
        return None
    for track in state.get("tracks2") or []:
        codec = track.get("codec") or ""
        props = track.get("codecProps") or {}
        if codec != "G711" and "sampleRate" not in props:
            continue                      # a video track: only audio tracks carry a rate
        name = ("pcmu" if props.get("muLaw") else "pcma") if codec == "G711" else codec.lower()
        return {"codec": name, "sampleRate": props.get("sampleRate"),
                "channels": props.get("channelCount")}
    return None


def flowing_paths(sample_sec: float = 1.0) -> set:
    """Names of the paths whose media is actually arriving: `bytesReceived` grew across
    `sample_sec`, with the path ready at both samples.

    `ready` is not proof. It describes a handshake -- a source connected and declared its
    tracks -- and a publisher that then stalls stays `ready`, tracks and all, while nothing
    flows (FoundAndFixed.md #50; the same trap as `systemctl is-active`, one layer further
    in). A source that reconnects between the samples resets its counter and reads as not
    flowing, which is the safe side: the caller simply asks again.
    """
    def snapshot():
        try:
            r = requests.get(f"{API}/v3/paths/list", timeout=TIMEOUT)
            r.raise_for_status()
            return {p["name"]: p for p in r.json().get("items", []) if p.get("ready")}
        except (requests.RequestException, ValueError):
            return {}
    before = snapshot()
    time.sleep(sample_sec)
    after = snapshot()
    return {name for name, p in after.items()
            if name in before and (p.get("bytesReceived") or 0) > (before[name].get("bytesReceived") or 0)}


def path_is_flowing(path: str, sample_sec: float = 1.0) -> bool:
    return path in flowing_paths(sample_sec)


def patch_path(path: str, conf: dict) -> None:
    """Merge `conf` into a path's configuration.

    A merge, not a replace -- fields left out keep their current values. Sending a field
    with the value it already holds is a no-op as far as path restarts are concerned,
    because MediaMTX compares values rather than tracking which keys were present.
    """
    r = requests.patch(f"{API}/v3/config/paths/patch/{path}", json=conf, timeout=TIMEOUT)
    if not r.ok:
        raise MediaMTXError(f"patch {path}: {r.status_code} {r.text}")


def add_path(path: str, conf: dict) -> None:
    r = requests.post(f"{API}/v3/config/paths/add/{path}", json=conf, timeout=TIMEOUT)
    if not r.ok:
        raise MediaMTXError(f"add {path}: {r.status_code} {r.text}")


def delete_path(path: str) -> None:
    """Remove a path's configuration; a path that has none is already gone, not an error.

    DELETE, not POST: MediaMTX answers POST on this route with "404 page not found", which
    once left an orphan path behind every failed registration (FoundAndFixed.md #36).
    """
    r = requests.delete(f"{API}/v3/config/paths/delete/{path}", timeout=TIMEOUT)
    if r.status_code != 404 and not r.ok:
        raise MediaMTXError(f"delete {path}: {r.status_code} {r.text}")


def ensure_path_conf(path: str, conf: dict) -> None:
    """Create the path config if absent, otherwise patch it.

    cam01 is published into the `all_others` catch-all and has no entry of its own, so a
    plain patch 404s. Adding one with `source: publisher` was verified not to disturb the
    live publisher (readyTime and source id unchanged across the add).
    """
    if get_path_conf(path) is None:
        add_path(path, {"source": "publisher", **conf})
    else:
        patch_path(path, conf)


def set_recording(path: str, on: bool, record_path: str = None,
                  segment_duration: str = "30s", part_duration: str = "1s") -> None:
    """Arm or disarm recording for one path.

    `recordDeleteAfter: 0s` disables MediaMTX's own cleaner permanently and is deliberate:
    the outage supervisor owns retention itself. Letting MediaMTX delete would put the
    captured footage at the mercy of a cleaner tick racing the supervisor -- and the thing
    it would delete is the footage the whole feature exists to save.
    """
    conf = {"record": bool(on)}
    if on:
        conf.update({
            "recordPath": record_path,
            "recordFormat": "fmp4",          # mandatory: mpegts cannot carry LPCM/G711
            "recordSegmentDuration": segment_duration,
            "recordPartDuration": part_duration,
            "recordDeleteAfter": "0s",
        })
    patch_path(path, conf)


def recording_conf_matches(path: str, on: bool, record_path: str = None,
                           segment_duration: str = "30s") -> bool:
    """Whether the live config already says what we want.

    Used by the supervisor's reconcile loop rather than tracking what it last sent:
    MediaMTX does NOT persist API config changes to mediamtx.yml, so a restart silently
    reverts every armed path. Comparing against reality catches that within one tick;
    remembering what we sent would not.

    A path with no configuration of its own records nothing (`pathDefaults` has `record: false`),
    so it already matches "off". Answering False there made the supervisor *create* the path
    just to disarm it, which resurrected the path of a camera unregistered a moment earlier.
    """
    conf = get_path_conf(path)
    if conf is None:
        return not on
    if bool(conf.get("record")) != bool(on):
        return False
    if not on:
        return True
    # MediaMTX reports a disabled cleaner as an EMPTY STRING, not the "0s" that was sent.
    # Comparing against the literal we wrote made this return False on every tick, so the
    # reconcile loop re-patched forever. Harmless at MediaMTX's end (same values compare
    # equal, so the path is not recreated) but it spammed the log and did real work every
    # 5 s. Compare parsed seconds, and treat empty as zero.
    return (conf.get("recordPath") == record_path
            and conf.get("recordFormat") == "fmp4"
            and _dur_secs(conf.get("recordDeleteAfter") or "0s") == 0
            and _dur_eq(conf.get("recordSegmentDuration"), segment_duration))


def _dur_eq(a: str, b: str) -> bool:
    # MediaMTX normalises durations on read: "30s" comes back as "30s" but "2m" becomes
    # "2m0s", "1h" becomes "1h0m0s", and the path defaults' cleaner comes back as "1d". Compare
    # parsed seconds, not strings, or the reconcile loop patches the same value forever.
    return _dur_secs(a) == _dur_secs(b)


# One term of a duration: a decimal number and its unit. Go's units, as time.ParseDuration reads
# them ("us", or "µ"/"μ" + "s"), plus MediaMTX's own "d", which it accepts and also reports: the
# path defaults come back as "recordDeleteAfter": "1d". "ms" is listed before "m" and "s", so it is
# never read as minutes followed by seconds.
_DUR_TERM = re.compile(r"(\d+\.?\d*|\.\d+)(ns|us|µs|μs|ms|s|m|h|d)")
_DUR_UNIT_NS = {"ns": 1, "us": 10**3, "µs": 10**3, "μs": 10**3, "ms": 10**6, "s": 10**9,
                "m": 60 * 10**9, "h": 3600 * 10**9, "d": 86400 * 10**9}


def _dur_secs(s) -> float | None:
    """Seconds in a duration as MediaMTX reports it ("30s", "2m0s", "1h0m0s", "200ms", "1d"), or
    None when it is empty or can't be read.

    Term by term, as Go parses it. The first version went one character at a time, so the "m" of
    "ms" counted minutes and "500ms" came out as 30 000 s (FoundAndFixed.md #84). Summed in whole
    nanoseconds, as Go stores a duration, so two spellings of one value ("1500ms", "1.5s") compare
    exactly equal.
    """
    if not s:
        return None
    text = str(s).strip()
    sign = -1 if text[:1] == "-" else 1
    text = text[1:] if text[:1] in "+-" else text
    if text == "0":                      # the one unitless duration Go accepts
        return 0.0
    total_ns, pos = 0, 0
    while pos < len(text):
        term = _DUR_TERM.match(text, pos)
        if not term:
            return None
        total_ns += int(Decimal(term.group(1)) * _DUR_UNIT_NS[term.group(2)])
        pos = term.end()
    return sign * total_ns / 10**9 if pos else None
