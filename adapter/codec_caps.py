"""Which video codecs each camera can deliver, and where the encoding would happen.

One place for the rule, so the admin GUI, the registration flow and anything later cannot
drift apart. Registry fields written here are hardware facts, like audioCapable -- never the
user's choice:

  videoCodecCaps     {"h264": <where>, "h265": <where>}, where is
                       "camera"  the camera's own encoder produces it (passthrough, no Pi CPU)
                       "pi-hw"   the Pi's hardware encoder produces it (cam-01's publisher)
                       "none"    not available
  videoCodecDefault  the hardware-first default: the codec needing no software encode;
                     if both qualify, what the camera sends now; tie -> h264, which every
                     browser plays (measurements/codec-phase0.md §7)
  videoEncoderToken  the ONVIF Media2 encoder configuration behind the registered stream --
                     what the admin GUI switches when the user picks a codec
  videoCodecProbe    how the caps were found: "local", "media2", or why it fell back

The user's choice (videoCodec) is written only by the admin GUI, when it switches a camera.

There is no software-encode ("pi-sw") value on purpose: x265 cannot hold 720p15 on a Pi 4
and the user decided cam-01 stays H.264-only (codec-phase0.md §6). An ONVIF camera whose
encoder lacks H.265 gets "none", with the camera -- not the Pi -- named as the reason.

These fields are for the GUIs. No pipeline reads them to pick hardware: cam-01 must start
while AWS is unreachable (CLAUDE.md), and producers build their chain from the codec
MediaMTX actually receives (mediamtx_api.path_video_codec).

    codec_caps.py [--dry-run] [cameraId ...]      # probe and (unless --dry-run) write
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import camera_control
import mediamtx_api
from onvif_media2 import Media2Client, Media2Error, profile_for_stream

VMS_HOME = os.environ.get("VMS_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODECS = ("h264", "h265")
HARDWARE = ("camera", "pi-hw")
FIELDS = ("videoCodecCaps", "videoCodecDefault", "videoEncoderToken", "videoCodecProbe")


def pi_hw_encoders() -> set:
    """Codecs this machine encodes in hardware -- detect-hw.sh decides, not a model name."""
    r = subprocess.run([os.path.join(VMS_HOME, "adapter", "bin", "detect-hw.sh"), "--encoders"],
                       capture_output=True, text=True, timeout=30)
    return set(r.stdout.split()) if r.returncode == 0 else set()


def default_codec(caps: dict, current: str | None = None) -> str | None:
    hw = [c for c in CODECS if caps.get(c) in HARDWARE]
    if current in hw:
        return current
    if "h264" in hw:
        return "h264"
    return hw[0] if hw else None


def probe(item: dict) -> dict:
    """The FIELDS for one registry row (or a registration's row-to-be). Raises Media2Error
    when an ONVIF camera cannot be asked; the caller decides what that means."""
    path = camera_control.mediamtx_path_name(item["cameraId"])
    on_wire = mediamtx_api.path_video_codec(path)

    if item.get("mode") == "transcode":
        hw = pi_hw_encoders()
        caps = {c: "pi-hw" if c in hw else "none" for c in CODECS}
        return {"videoCodecCaps": caps, "videoCodecDefault": default_codec(caps, on_wire),
                "videoEncoderToken": None, "videoCodecProbe": "local"}

    if not item.get("onvifHost"):
        # Passthrough without ONVIF: all we can know is what arrives.
        caps = {c: "camera" if c == on_wire else "none" for c in CODECS}
        return {"videoCodecCaps": caps, "videoCodecDefault": default_codec(caps, on_wire),
                "videoEncoderToken": None, "videoCodecProbe": "stream only (no ONVIF)"}

    cam = Media2Client.for_camera(item)
    prof = profile_for_stream(cam.profiles(), item.get("rtspUrl", ""))
    if not prof or not prof["encoder_token"]:
        raise Media2Error("registered stream URI matches no single Media2 profile")
    offered = cam.encoder_options(prof["encoder_token"])
    caps = {c: "camera" if c in offered else "none" for c in CODECS}
    current = on_wire or prof["codec"]
    return {"videoCodecCaps": caps, "videoCodecDefault": default_codec(caps, current),
            "videoEncoderToken": prof["encoder_token"], "videoCodecProbe": "media2"}


NAMES = {"h264": "H.264", "h265": "H.265"}


def unavailable_reason(item: dict, codec: str) -> str | None:
    """Why `codec` can't be selected for this camera, in words for the admin GUI -- or None
    when it can. The transcode wording is the user-facing form of the cam-01 decision:
    the choice would need software encoding, which this Pi cannot sustain."""
    caps = item.get("videoCodecCaps") or {}
    if caps.get(codec) in HARDWARE:
        return None
    name = NAMES.get(codec, codec)
    if not caps:
        return "codec capabilities not known yet -- they are read when the admin GUI starts"
    if item.get("mode") == "transcode":
        return (f"{name} would need software encoding: this Pi has no hardware {name} "
                f"encoder, and software encoding can't sustain this camera's resolution")
    probe_note = item.get("videoCodecProbe") or ""
    if probe_note.startswith("fallback"):
        return f"the camera could not be asked ({probe_note}); re-register it once it is online"
    if not item.get("onvifHost"):
        return "no ONVIF access to this camera, so its encoder can't be switched from here"
    return f"the camera's encoder does not offer {name}"


def fallback(item: dict, err: Exception) -> dict:
    """What to record for an ONVIF camera that could not be asked and has no caps yet:
    only what is observably arriving, H.265 not offered. Conservative on purpose -- a wrong
    "camera" would offer a switch the camera cannot make."""
    on_wire = mediamtx_api.path_video_codec(camera_control.mediamtx_path_name(item["cameraId"]))
    current = on_wire or "h264"
    caps = {c: "camera" if c == current else "none" for c in CODECS}
    return {"videoCodecCaps": caps, "videoCodecDefault": current, "videoEncoderToken": None,
            "videoCodecProbe": f"fallback: {str(err)[:160]}"}


def refresh_item(table, item: dict, write: bool = True) -> tuple:
    """Probe one row and write the FIELDS if they changed. Returns (fields, status).

    A camera that cannot be asked right now keeps the caps it already has: cam-02 drops off
    the network for minutes at a time on its own (codec-phase0.md §2), and overwriting a
    good Media2 answer with a fallback on every such blip would make the GUI flap.
    """
    try:
        fields, status = probe(item), "ok"
    except (Media2Error, OSError, subprocess.SubprocessError) as e:
        if item.get("videoCodecCaps"):
            return {k: item.get(k) for k in FIELDS}, f"kept existing ({e})"
        fields, status = fallback(item, e), f"fallback ({e})"
    if write and any(item.get(k) != fields[k] for k in FIELDS):
        table.update_item(
            Key={"cameraId": item["cameraId"]},
            UpdateExpression="SET " + ", ".join(f"{k} = :{k}" for k in FIELDS),
            ExpressionAttributeValues={f":{k}": fields[k] for k in FIELDS},
        )
        status += ", written"
    return fields, status


def refresh(table, camera_ids=None, write: bool = True, log=print) -> dict:
    """Refresh every registry row (or the given ones). Returns {cameraId: status}."""
    items = table.scan()["Items"]
    done = {}
    for item in sorted(items, key=lambda i: i["cameraId"]):
        if camera_ids and item["cameraId"] not in camera_ids:
            continue
        try:
            fields, status = refresh_item(table, item, write)
        except Exception as e:  # noqa: BLE001 -- one bad row must not stop the others
            status = f"error ({type(e).__name__}: {e})"
            log(f"codec caps {item['cameraId']}: {status}")
        else:
            log(f"codec caps {item['cameraId']}: {status} -> "
                f"{json.dumps(fields['videoCodecCaps'])} default={fields['videoCodecDefault']}")
        done[item["cameraId"]] = status
    return done


def refresh_in_background(table_factory, attempts: int = 12, interval: int = 300, log=print):
    """Startup refresh for a long-running process (the admin GUI, which runs from boot).
    Retries while AWS or a camera is unreachable; a failure here only ever means stale GUI
    information, never a camera that does not stream."""
    def run():
        for n in range(attempts):
            try:
                result = refresh(table_factory(), log=log)
                if all(s.startswith("ok") for s in result.values()):
                    return
            except Exception as e:  # noqa: BLE001 -- AWS unreachable at boot is normal
                log(f"codec caps refresh failed ({type(e).__name__}: {e}); retry {n + 1}/{attempts}")
            time.sleep(interval)
    threading.Thread(target=run, name="codec-caps", daemon=True).start()


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe and record each camera's codec capabilities.")
    ap.add_argument("--dry-run", action="store_true", help="probe only, write nothing")
    ap.add_argument("cameras", nargs="*", help="cameraIds (default: all)")
    args = ap.parse_args()
    from aws_device_creds import get_session
    table = get_session().resource("dynamodb").Table("cameras")
    refresh(table, set(args.cameras) or None, write=not args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
