"""Local ONVIF admin GUI -- discover, register, and control ONVIF cameras on this LAN.

Deliberately separate from client/index.html: WS-Discovery is UDP multicast, which only
works from a process on the same LAN segment as the cameras -- the cloud-hosted client
(served from S3, reached over the internet through CloudFront/API Gateway) has no route
to do that, and Lambda has no route to the LAN at all. So the *discovery and
registration* half of camera onboarding has to run here, on the Pi, not in the cloud.
Once a camera is registered (a row in the "cameras" DynamoDB table plus local systemd/
MediaMTX wiring), the existing cloud client and MQTT control plane pick it up with no
further change -- this app's job ends at registration and basic local control.

Access: LAN-only, no login. Consistent with this project's existing trust boundary --
MediaMTX's local RTSP endpoints and the camera's own ONVIF/RTSP services are equally
unauthenticated on this LAN. Not intended to be port-forwarded or exposed beyond it.

The registry is shared with VMS, which runs on the same board from another SD card
(VMS-PORT-PLAN.md D1). Rows are therefore updated field by field, never overwritten whole,
and a camera registered from the other card can have a row here and no producer.
"""
import asyncio
import base64
import copy
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import camera_control
import codec_caps
import config
import live_view
import mediamtx_api
import onvif_discovery
import onvif_media2
from aws_device_creds import get_session
from outage_buffer import read_registry_cache

app = Flask(__name__, static_folder="static")

CAMERA_ID_RE = re.compile(r"^cam-\d{2}$")
# A camera's display name ("Kitchen", "Hall-1", "Küche"): what people read in both GUIs (ported
# from VMS, which already wrote names into the shared registry). The cameraId stays the key
# everywhere else (KVS, S3, DynamoDB, MediaMTX, units), so a name is only ever displayed. Letters
# and digits in any script, space, - _ . -- nothing that HTML or a shell would read as syntax:
# both pages render it, and this page has no login.
DISPLAY_NAME_RE = re.compile(r"^[\w .-]{1,32}$")
DISPLAY_NAME_MAX = 32
MEDIAMTX_API = "http://127.0.0.1:9997"
REGION = config.AWS_REGION
PROVISION_SH = str(Path(__file__).resolve().parent.parent / "bin" / "provision-camera.sh")
# cam-01 and cam-02 predate the kvs-cam@ template and have their own unit files; provisioning
# one of them would start a second, conflicting producer on the same KVS stream.
LEGACY_UNITS = ("cam-01", "cam-02")
WIRE_WAIT_SEC = 15       # registration: how long to wait for the new path's video before probing


def cameras_table():
    return get_session(REGION).resource("dynamodb").Table("cameras")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unregistered_error(camera_id):
    return jsonify({"error": f"'{camera_id}' is unregistered: Re-register it first"}), 409


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.post("/api/scan")
def scan():
    body = request.get_json(force=True, silent=True) or {}
    # A camera may have no password at all (FoundAndFixed.md #67): only the username decides
    # whether the scan logs in.
    user, password = body.get("user"), body.get("password") or ""

    services = onvif_discovery.scan(timeout=int(body.get("timeout", 4)))
    results = []
    for svc in services:
        xaddrs = svc.getXAddrs()
        scopes = [s.getValue() if hasattr(s, "getValue") else str(s) for s in svc.getScopes()]
        # epr: the device's stable WS-Discovery identity, stored at registration so
        # rematch_cameras.py can follow the camera to a new IP address, and Re-register can
        # find an unregistered camera wherever it now is.
        entry = {"xaddr": xaddrs[0] if xaddrs else None, "scopes": scopes, "epr": svc.getEPR()}
        if user and xaddrs:
            try:
                entry["details"] = onvif_discovery.enrich_sync(xaddrs[0], user, password)
            except Exception as e:
                entry["enrichError"] = str(e)
        results.append(entry)
    return jsonify({"devices": results})


@app.get("/api/cameras")
def list_cameras():
    items = cameras_table().scan()["Items"]
    # One ~1 s sample for all paths: MediaMTX's `ready` is not proof that video arrives
    # (FoundAndFixed.md #50), its byte counter growing is.
    flowing = mediamtx_api.flowing_paths()
    for i in items:
        i.pop("onvifPassword", None)  # never echo the credential back to the page
        # Every camera has a MediaMTX path regardless of mode (cam-01's transcode
        # pipeline publishes into MediaMTX exactly like cam-02's passthrough pull does),
        # so this is always derivable -- send it rather than have the page re-implement
        # the naming convention itself.
        i["mediamtxPath"] = camera_control.mediamtx_path_name(i["cameraId"])
        # The local preview is live view, so it plays the sub stream's path when the camera
        # records its main stream (live_view.py). The URI itself carries credentials and the
        # page doesn't need it; rtspUrl the same.
        i["previewPath"] = (camera_control.live_path_name(i["cameraId"])
                            if i.pop("liveRtspUrl", None) else i["mediamtxPath"])
        # The recorded stream without its credentials: the registration form preselects the
        # stream a camera already uses by comparing path and query, credentials aside.
        if "rtspUrl" in i:
            i["streamUri"] = onvif_media2.redact_uri(i.pop("rtspUrl"))
        # Codec selector support: what MediaMTX actually receives right now (None when the
        # source is offline), and why any codec is not selectable -- shown in the GUI
        # rather than silently leaving an option out (codec_caps.unavailable_reason).
        i["onWireCodec"] = mediamtx_api.path_video_codec(i["mediamtxPath"])
        i["onWireFlowing"] = i["mediamtxPath"] in flowing
        i["videoCodecUnavailable"] = {
            c: r for c in codec_caps.CODECS if (r := codec_caps.unavailable_reason(i, c))}
        # A camera registered from the VMS card has no producer on this one until Re-register
        # provisions it: say so, rather than let a missing unit look stopped (#37's trap).
        i["hasProducer"] = camera_control.has_producer(i["cameraId"])
    # Unregistered cameras too, with their unregisteredAt: the page leaves them out of the
    # table and needs them only to offer Re-register on a matching discovered device.
    return jsonify({"cameras": sorted(items, key=lambda c: c["cameraId"])})


def _wait_for_flowing(path, budget=WIRE_WAIT_SEC):
    """True once `path` has video actually flowing, False after `budget` seconds."""
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if mediamtx_api.path_video_codec(path) and mediamtx_api.path_is_flowing(path):
            return True
        time.sleep(1)
    return False


def _provision(camera_id, start):
    """provision-camera.sh through sudo (the LAUNCH.md A8 rule): the kvs-cam@ instance."""
    args = ["sudo", PROVISION_SH, camera_id, camera_control.mediamtx_path_name(camera_id)]
    if not start:
        args.append("no-start")
    return subprocess.run(args, capture_output=True, text=True)


@app.post("/api/cameras")
def register_camera():
    body = request.get_json(force=True, silent=True) or {}
    camera_id = body.get("cameraId", "")
    host, port = body.get("host"), int(body.get("port", 80))
    # A camera may have no password at all (a factory "admin" with a blank one, cam-04): ""
    # is stored as given, and every ONVIF call still sends the username with an empty
    # password's digest (FoundAndFixed.md #67).
    user, password = body.get("user") or "", body.get("password") or ""
    stream_uri = body.get("streamUri")
    has_ir_control = bool(body.get("hasIrControl"))
    endpoint_ref = body.get("endpointRef") or None   # absent for hand-typed hosts; rematch backfills it
    # Found at discovery (onvif_discovery: a G.711 microphone on the profile, or in the stream's
    # SDP). Absent means "not known" -- the scan couldn't log in -- and a re-registration then
    # leaves the stored value alone. audioEnabled is never set here: recording audio stays the
    # user's choice (FoundAndFixed.md #60).
    audio_capable = body.get("audioCapable")
    fps = body.get("fps")             # the form preselects onvif_media2.DEFAULT_FPS (D5)
    # Optional display name. Absent: a re-registration keeps the stored one; "" clears it.
    # Checked before anything is changed anywhere.
    name_given = "displayName" in body

    if not CAMERA_ID_RE.match(camera_id):
        return jsonify({"error": "cameraId must look like 'cam-03'"}), 400
    if not all([host, user, stream_uri]):
        return jsonify({"error": "host, user, and streamUri are required (the password may be empty)"}), 400
    if not isinstance(user, str) or not isinstance(password, str):
        return jsonify({"error": "user and password must be strings"}), 400
    if audio_capable is not None and not isinstance(audio_capable, bool):
        return jsonify({"error": "audioCapable must be true or false"}), 400
    if fps is not None and fps not in onvif_media2.FPS_CHOICES:
        return jsonify({"error": f"fps must be one of {list(onvif_media2.FPS_CHOICES)}"}), 400

    table = cameras_table()
    existing = table.get_item(Key={"cameraId": camera_id}).get("Item")
    mediamtx_path = camera_control.mediamtx_path_name(camera_id)
    display_name = None
    if name_given:
        display_name, err = _display_name(body.get("displayName"), camera_id, _all_names(table))
        if err:
            return jsonify({"error": err}), 400

    if existing:
        # Re-register: an already-discovered device commonly IS an already-registered
        # camera (its ONVIF host doesn't change just because you scanned again), so this
        # updates the stored details -- credentials, RTSP URI if a DHCP lease moved the
        # IP -- rather than re-running the full provisioning flow. It never re-provisions
        # cam-01/cam-02: they predate the kvs-cam@.service template and run as their own
        # individually-named units, so provision-camera.sh against them would create a
        # second, conflicting producer for the same KVS stream. The MediaMTX path source is
        # still safe to refresh -- editing a path's config doesn't care which unit feeds it.
        if existing.get("mode") != "passthrough" or not existing.get("onvifHost"):
            return jsonify({"error": f"'{camera_id}' exists but isn't a re-registerable ONVIF camera"}), 409
        # Re-registering an unregistered camera also brings it back: its path is added again
        # (Unregister deleted it) and the mark is removed. Its producer stays stopped until
        # someone presses Start, as every producer does after a reboot (FoundAndFixed.md #81).
        reactivate = camera_control.is_unregistered(existing)
        # The form offers the stream URIs the camera reports, which usually lack the credentials
        # a registered URI may carry (cam-02's ?username=..&password=..): keep those.
        stream_uri = onvif_media2.with_credentials(stream_uri, existing.get("rtspUrl"))
        try:
            stream_uri = onvif_discovery.rtsp_login(stream_uri, user, password)
        except onvif_discovery.RtspUnauthorized as e:
            return jsonify({"error": str(e)}), 400
        fps_result = _registration_fps(host, port, user, password, stream_uri, fps)

        # Only patch when the source actually changed. MediaMTX decides whether a config
        # update can be applied in place or needs the path closed and recreated
        # (core.pathConfCanBeUpdated): `source` is NOT in its in-place whitelist, so
        # patching it disconnects every reader -- including a running KVS producer, and
        # any outage-buffer recording mid-segment. A re-registration that changes nothing
        # (the common case: same camera, same IP, user just re-scanned) should cost
        # nothing. When the URI really has moved, the restart is correct and unavoidable.
        #
        # `rtspTransport`, not `sourceProtocol`: the latter is a deprecated alias that
        # MediaMTX still accepts but may drop. A path with no configuration (an unregistered
        # camera's, or one a MediaMTX restart dropped) is added rather than patched: a
        # patch 404s.
        want = {"source": stream_uri, "rtspTransport": "tcp"}
        try:
            conf = mediamtx_api.get_path_conf(mediamtx_path)
            if conf is None:
                mediamtx_api.add_path(mediamtx_path, want)
            elif conf.get("source") != stream_uri:
                mediamtx_api.patch_path(mediamtx_path, want)
        except (mediamtx_api.MediaMTXError, requests.RequestException) as e:
            return jsonify({"error": f"MediaMTX path update failed: {e}"}), 502

        # REMOVE is a no-op on a row without the mark, so every re-registration can carry it.
        sets = ("hasIrControl = :ir, onvifHost = :h, onvifPort = :p, onvifUser = :u, "
                "onvifPassword = :pw, rtspUrl = :uri, updatedAt = :t")
        values = {":ir": has_ir_control, ":h": host, ":p": port, ":u": user, ":pw": password,
                  ":uri": stream_uri, ":t": _now()}
        if endpoint_ref:
            sets += ", onvifEndpointRef = :ref"
            values[":ref"] = endpoint_ref
        if audio_capable is not None:
            sets += ", audioCapable = :ac"
            values[":ac"] = audio_capable
        removes = "unregisteredAt"
        if name_given and display_name:
            sets += ", displayName = :dn"
            values[":dn"] = display_name
        elif name_given:
            removes += ", displayName"
        table.update_item(Key={"cameraId": camera_id},
                          UpdateExpression=f"SET {sets} REMOVE {removes}",
                          ExpressionAttributeValues=values)

        # The producer. A camera registered from the VMS card has none on this one: provision
        # it, not started (D1). Never enabled: no producer starts at boot (#81).
        producer = "unchanged"
        if camera_id not in LEGACY_UNITS and not camera_control.has_producer(camera_id):
            p = _provision(camera_id, start=False)
            producer = ("provisioned (not started)" if p.returncode == 0
                        else f"provisioning failed: {(p.stderr or p.stdout).strip()}")

        # Re-ask the codec capabilities: the stream URI may now point at another profile,
        # or the row at another device. If the camera can't be asked right now the
        # existing answer is kept (codec_caps.refresh_item).
        _, caps_status = codec_caps.refresh_item(
            table, table.get_item(Key={"cameraId": camera_id})["Item"])
        detection = _registration_detection(camera_id, host, port, user, password)
        # The stream may have changed, and an unregistered camera's live path went with
        # Unregister: set the live view up again from what the camera says now.
        live = _apply_live_view(camera_id, {**existing, "onvifHost": host, "onvifPort": port,
                                            "onvifUser": user, "onvifPassword": password,
                                            "rtspUrl": stream_uri})
        _refresh_path_cache()
        return jsonify({"cameraId": camera_id, "kvsStreamArn": existing["kvsStreamArn"],
                        "updated": True, "reactivated": reactivate, "producer": producer,
                        "codecCaps": caps_status, "fps": fps_result,
                        "detectionModes": detection, "liveView": live}), 200

    # The login the camera's RTSP demands, in the URI MediaMTX pulls (FoundAndFixed.md #67).
    # Refused: nothing is created, rather than a camera whose every pull is a 401.
    try:
        stream_uri = onvif_discovery.rtsp_login(stream_uri, user, password)
    except onvif_discovery.RtspUnauthorized as e:
        return jsonify({"error": str(e)}), 400

    # 0. The frame rate, on the camera, before MediaMTX ever connects: a change can restart the
    #    camera's RTSP session, which costs nothing yet. Not fatal -- a camera without Media2
    #    keeps its own rate, and the response says so.
    fps_result = _registration_fps(host, port, user, password, stream_uri, fps)

    # 1. MediaMTX path, live via its local API -- no YAML edit, no restart, so the
    #    other already-running cameras are undisturbed.
    try:
        mediamtx_api.add_path(mediamtx_path, {"source": stream_uri, "rtspTransport": "tcp"})
    except (mediamtx_api.MediaMTXError, requests.RequestException) as e:
        return jsonify({"error": f"MediaMTX path add failed: {e}"}), 502

    # 2. Local systemd wiring: env file for the kvs-cam@.service template, then
    #    daemon-reload + start (never enable: #81). See provision-camera.sh's own docstring
    #    for why this is a separate validated script rather than this process writing under
    #    /etc directly.
    provision = _provision(camera_id, start=True)
    if provision.returncode != 0:
        # DELETE, not POST: MediaMTX answers POST on this route with a 404, so the rollback
        # silently did nothing and left an orphan path behind (FoundAndFixed.md #36).
        mediamtx_api.delete_path(mediamtx_path)
        return jsonify({"error": f"provisioning failed: {provision.stderr.strip()}"}), 500

    # 3. Which codecs the camera can deliver and which is the default (codec_caps.py) --
    #    asked before the KVS stream exists, because its MediaType is set at creation.
    #    The new path's video first: the default should come from what MediaMTX receives,
    #    not from the camera's claim (cam-03 claims H264 and sends H.265, FoundAndFixed.md
    #    #69). A camera that can't be asked is recorded conservatively (H.265 not offered)
    #    rather than failing a registration whose path and unit already exist.
    _wait_for_flowing(mediamtx_path)
    row = {"cameraId": camera_id, "mode": "passthrough", "onvifHost": host, "onvifPort": port,
           "onvifUser": user, "onvifPassword": password, "rtspUrl": stream_uri}
    try:
        codec_fields = codec_caps.probe(row)
    except Exception as e:  # noqa: BLE001 -- see above
        codec_fields = codec_caps.fallback(row, e)

    # 4. The KVS stream itself.
    kv = get_session(REGION).client("kinesisvideo")
    try:
        stream_arn = kv.create_stream(
            StreamName=camera_id, DataRetentionInHours=24,
            MediaType=f"video/{codec_fields['videoCodecDefault'] or 'h264'}",
        )["StreamARN"]
    except kv.exceptions.ResourceInUseException:
        stream_arn = kv.describe_stream(StreamName=camera_id)["StreamInfo"]["StreamARN"]

    # 5. The registry row -- Phase 1 of the credential-storage plan: plain attributes.
    #    Phase 2 (SSM Parameter Store SecureString + a credentialRef here instead of
    #    onvifPassword) is a deliberately deferred follow-up, not done in this pass.
    row = {
        **codec_fields,
        **row,
        **({"onvifEndpointRef": endpoint_ref} if endpoint_ref else {}),
        "hasIrControl": has_ir_control,
        "kvsStreamArn": stream_arn,
        "audioCapable": bool(audio_capable),
        "createdAt": _now(),
        **({"displayName": display_name} if display_name else {}),
    }
    table.put_item(Item=row)

    detection = _registration_detection(camera_id, host, port, user, password)
    live = _apply_live_view(camera_id, row)    # registered on the main stream: live view on sub
    _refresh_path_cache()
    return jsonify({"cameraId": camera_id, "kvsStreamArn": stream_arn, "updated": False,
                    "fps": fps_result, "detectionModes": detection, "liveView": live}), 201


def _registration_detection(camera_id, host, port, user, password):
    """Record the camera's recording triggers at registration, so both GUIs restrict the
    selector from the first load. Never fails the registration; None when it couldn't ask."""
    try:
        dmap, _, web_url = _read_detection(host, port, user, password)
    except Exception:  # noqa: BLE001
        return None
    return _record_detection(camera_id, dmap, web_url=web_url)


def _set_camera_fps(client, stream_uri, fps):
    """Set the frame rate of the encoder behind `stream_uri`, keeping the keyframe interval in
    seconds (onvif_media2.fps_config). Returns the camera's read-back, which is only its claim;
    callers that need proof measure the wire (_verify_fps)."""
    prof = onvif_media2.stream_profile(client, stream_uri)
    cfg = client.encoder_config(prof["encoderToken"])
    now = onvif_media2.summarize(cfg)
    opts = client.encoder_options(prof["encoderToken"]).get(now["encoding"]) or {}
    if fps not in onvif_media2.fps_choices(opts, (now["width"], now["height"])):
        raise onvif_media2.OnvifError(f"this stream's encoder doesn't offer {fps} fps")
    if now["frameRate"] != fps:
        client.set_encoder_config(onvif_media2.fps_config(cfg, opts, fps))
    return onvif_media2.summarize(client.encoder_config(prof["encoderToken"]))


def _registration_fps(host, port, user, password, stream_uri, fps):
    """The registration form's frame rate applied to the camera, or None when none was asked.
    Never fails the registration: the camera's read-back or the reason goes in the response."""
    if fps is None:
        return None
    try:
        got = _set_camera_fps(onvif_media2.Media2(host, port, user, password), stream_uri, fps)
        return {"asked": fps, "configured": got["frameRate"], "govLength": got["govLength"]}
    except Exception as e:  # noqa: BLE001
        return {"asked": fps, "error": f"{type(e).__name__}: {e}"}


def _apply_live_view(camera_id, item, found=None):
    """Point the camera's live view at its sub stream, or back at the recorded stream
    (live_view.py), from the camera's own stream list. Never fails the action that called it --
    a registration or a stream switch has already worked; the outcome, or why the live view
    wasn't set up, goes in the response."""
    try:
        if found is None:
            found = onvif_media2.streams(onvif_media2.for_camera(item))
        uri = live_view.live_uri(found, item.get("rtspUrl"))
        return live_view.apply(camera_id, item, uri, cameras_table(),
                               get_session(REGION).client("kinesisvideo"))
    except Exception as e:  # noqa: BLE001
        return {"error": f"live view not set up ({type(e).__name__}: {e}) -- "
                         f"re-register the camera to retry"}


def _refresh_path_cache():
    """Rewrite sync_mediamtx_paths.py's offline copy of the registry now, through its own
    code, so a MediaMTX start without AWS sets up the paths as of this change rather than as
    of the last start. Best effort: the next online MediaMTX start rewrites it anyway."""
    try:
        import sync_mediamtx_paths
        sync_mediamtx_paths.load_cameras()
    except Exception as e:  # noqa: BLE001
        print(f"path cache not refreshed: {type(e).__name__}: {e}", flush=True)


def _all_names(table):
    """{cameraId: displayName} of every row, unregistered ones included: they come back with
    Re-register, and must not come back sharing a name."""
    names, kw = {}, {"ProjectionExpression": "cameraId, displayName"}
    while True:
        page = table.scan(**kw)
        names.update({i["cameraId"]: i.get("displayName") for i in page["Items"]})
        if "LastEvaluatedKey" not in page:
            return names
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _display_name(raw, camera_id, names):
    """(name or None, error or None). Empty means "no name": the GUIs then show the ID."""
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, "displayName must be text"
    name = " ".join(raw.split())                  # trimmed, inner whitespace collapsed
    if not name:
        return None, None
    if len(name) > DISPLAY_NAME_MAX or not DISPLAY_NAME_RE.match(name):
        return None, (f"a name has 1-{DISPLAY_NAME_MAX} characters: letters, digits, "
                      f"space, '-', '_' and '.'")
    if not re.search(r"[^\W_]", name):
        return None, "a name needs at least one letter or digit"
    if re.fullmatch(r"(?i)cam-?\d+", name):
        return None, "a name can't look like a camera ID"
    taken = next((cid for cid, n in names.items()
                  if cid != camera_id and n and n.casefold() == name.casefold()), None)
    if taken:
        return None, f"'{name}' is already the name of {taken}"
    return name, None


@app.post("/api/cameras/<camera_id>/name")
def set_display_name(camera_id):
    """Set or clear a camera's display name: {"displayName": "Kitchen"}, or "" to clear. Any
    camera, cam-01 included, unregistered ones too. Writes only that field (and updatedAt),
    and nothing when nothing changes. Edited here only; the cloud page shows it (GET /cameras)."""
    if not CAMERA_ID_RE.match(camera_id):
        return jsonify({"error": "bad camera id"}), 400
    raw = (request.get_json(force=True, silent=True) or {}).get("displayName", "")
    table = cameras_table()
    names = _all_names(table)
    if camera_id not in names:
        return jsonify({"error": "unknown camera"}), 404
    name, err = _display_name(raw, camera_id, names)
    if err:
        return jsonify({"error": err}), 400
    if name == names[camera_id]:
        return jsonify({"cameraId": camera_id, "displayName": name, "unchanged": True})
    if name:
        table.update_item(Key={"cameraId": camera_id},
                          UpdateExpression="SET displayName = :n, updatedAt = :t",
                          ExpressionAttributeValues={":n": name, ":t": _now()})
    else:
        table.update_item(Key={"cameraId": camera_id},
                          UpdateExpression="SET updatedAt = :t REMOVE displayName",
                          ExpressionAttributeValues={":t": _now()})
    return jsonify({"cameraId": camera_id, "displayName": name})


@app.post("/api/cameras/<camera_id>/unregister")
def unregister_camera(camera_id):
    """Hide a camera from both GUIs and stop everything that acts on it, keeping what it has.

    The row stays: the registry is shared with VMS, so rows are updated field by field. So do
    the KVS stream, the clips and every setting, which is what lets Re-register (Discover, then
    the button on the matching device) bring the camera back as it was. Three things change:
      1. unregisteredAt is set, the mark every reader skips (camera_control.is_unregistered);
      2. the producer is stopped and disabled: it costs nothing and doesn't start at boot;
      3. the MediaMTX paths are deleted (recording and live view), so the Pi stops pulling.
    The registry goes first: with AWS unreachable nothing has changed yet, and the cloud page
    couldn't learn of it anyway.

    ONVIF cameras only, because Re-register finds a camera by discovery, which can't see the
    USB camera. Unregistering here hides the camera in VMS's GUIs as well (D4).
    """
    table = cameras_table()
    item = table.get_item(Key={"cameraId": camera_id}).get("Item")
    if not item:
        return jsonify({"error": "unknown camera"}), 404
    if item.get("mode") != "passthrough" or not item.get("onvifHost"):
        return jsonify({"error": f"only ONVIF cameras can be unregistered: Re-register finds "
                                 f"them again by discovery, which can't see '{camera_id}'"}), 409
    if camera_control.is_unregistered(item):
        return jsonify({"cameraId": camera_id, "unregisteredAt": item["unregisteredAt"]})

    now = _now()
    table.update_item(
        Key={"cameraId": camera_id},
        UpdateExpression="SET unregisteredAt = :t, updatedAt = :t",
        ExpressionAttributeValues={":t": now},
    )
    camera_control.set_stream(camera_id, False)
    camera_control.set_boot_start(camera_id, False)
    subprocess.run(["sudo", "systemctl", "stop", camera_control.live_unit_name(camera_id)],
                   check=False, capture_output=True)
    result = {"cameraId": camera_id, "unregisteredAt": now}
    try:
        # The live view's path too (live_view.py). Its registry field stays, like everything
        # else, until Re-register sets the live view up again.
        mediamtx_api.delete_path(camera_control.mediamtx_path_name(camera_id))
        mediamtx_api.delete_path(camera_control.live_path_name(camera_id))
    except (mediamtx_api.MediaMTXError, requests.RequestException) as e:
        result["warning"] = f"MediaMTX path not removed ({e}); it goes at the next MediaMTX restart"
    _refresh_path_cache()
    return jsonify(result)


@app.get("/api/cameras/<camera_id>/status")
def stream_status(camera_id):
    # Polled every few seconds per camera, so the ID is checked against the outage
    # supervisor's local registry cache, not DynamoDB: this LAN page must keep answering with
    # the internet down. In VMS a lookup per poll hung ~36 s each while DNS was failing (an
    # unresolvable name isn't bound by urlopen's timeout), and six of them filled the browser's
    # connection pool and froze the whole page (FoundAndFixed.md #56). DynamoDB only on a Pi
    # without the cache.
    if not CAMERA_ID_RE.match(camera_id):
        return jsonify({"error": "bad camera id"}), 400
    known = read_registry_cache()
    if known:
        if camera_id not in known:
            return jsonify({"error": "unknown camera"}), 404
    elif "Item" not in cameras_table().get_item(Key={"cameraId": camera_id}):
        return jsonify({"error": "unknown camera"}), 404
    # A camera with no producer on this Pi (registered from the VMS card) is not "inactive":
    # say what it is, so Start isn't offered for a unit that doesn't exist (#37).
    if not camera_control.has_producer(camera_id):
        return jsonify({"cameraId": camera_id, "state": "no-producer"})
    # Queries systemctl directly rather than tracking "what the last button click did" --
    # this is the actual ground truth (also catches the unit crashing or being stopped
    # by something other than this GUI, not just this GUI's own actions).
    return jsonify({"cameraId": camera_id, "state": camera_control.get_stream_status(camera_id)})


@app.get("/api/cameras/<camera_id>/analytics")
def get_analytics(camera_id):
    """Read the camera's motion-analytics tuning (Phase 4).

    Read-only, and not by choice: `SetVideoAnalyticsConfiguration` is a **silent no-op**
    on this camera. It returns success and changes nothing -- verified by sending
    Sensitivity=55 and reading back 80 immediately after, twice. The advertised ONVIF 2.0
    analytics service (`ver20/analytics/wsdl`) exposes no callable operations either, and
    the vendor ISAPI path 404s. So these values can only be changed in the camera's own
    web UI; surfacing them here at least explains *why* detection behaves as it does.
    """
    item = cameras_table().get_item(Key={"cameraId": camera_id}).get("Item")
    if not item or not item.get("onvifHost"):
        return jsonify({"error": "unknown or non-ONVIF camera"}), 404

    async def _read():
        from onvif import ONVIFCamera
        cam = ONVIFCamera(item["onvifHost"], int(item.get("onvifPort", 80)),
                          item["onvifUser"], item["onvifPassword"],
                          wsdl_dir=camera_control.WSDL_DIR)
        await cam.update_xaddrs()
        media = await cam.create_media_service()
        vac = (await media.GetVideoAnalyticsConfigurations())[0]
        mod = vac.AnalyticsEngineConfiguration.AnalyticsModule[0]
        rule = vac.RuleEngineConfiguration.Rule[0]
        out = {
            "configName": vac.Name,
            "module": {"name": mod.Name, "type": mod.Type,
                       "params": {s.Name: s.Value for s in mod.Parameters.SimpleItem}},
            "rule": {"name": rule.Name, "type": rule.Type,
                     "params": {s.Name: s.Value for s in rule.Parameters.SimpleItem}},
            "writable": False,
            "readOnlyReason": "SetVideoAnalyticsConfiguration is a no-op on this camera "
                              "(accepted, ignored) — change these in the camera's web UI",
        }
        await cam.close()
        return out

    try:
        data = asyncio.run(_read())
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {str(e)[:200]}"}), 502

    # ActiveCells is a base64 bitmap of enabled detection cells. The camera returns an
    # empty Layout element, so it never tells us the grid's Columns/Rows -- report the
    # bit count and let the reader draw their own conclusion rather than inventing a
    # geometry that might be wrong.
    cells = data["rule"]["params"].get("ActiveCells")
    if cells:
        try:
            raw = base64.b64decode(cells)
            data["activeCells"] = {
                "base64": cells,
                "hex": raw.hex(),
                "bits": "".join(f"{b:08b}" for b in raw),
                "cellCount": len(raw) * 8,
                "gridGeometry": "unknown — camera returns an empty Layout element",
            }
        except Exception:
            pass
    return jsonify(data)


@app.post("/api/cameras/<camera_id>/mode")
def set_recording_mode(camera_id):
    """Phase 1 (Camera-Features.md §9) -- writes recordingMode to the registry only.

    Mirrors the cloud `set-camera-mode` Lambda deliberately: same validation, same
    allowed values, same ONVIF-host requirement, same refusal of a mode the camera doesn't
    publish. Both write the one registry row, so whichever GUI you use, the other sees it on
    next refresh.
    """
    VALID = {"manual", "motion", "cellMotion", "human"}
    mode = (request.get_json(force=True, silent=True) or {}).get("recordingMode")
    if mode not in VALID:
        return jsonify({"error": f"recordingMode must be one of {sorted(VALID)}"}), 400

    table = cameras_table()
    item = table.get_item(Key={"cameraId": camera_id}).get("Item")
    if not item:
        return jsonify({"error": "unknown camera"}), 404
    if camera_control.is_unregistered(item):
        return _unregistered_error(camera_id)
    if mode != "manual" and not item.get("onvifHost"):
        return jsonify({"error": "no ONVIF host — this camera supports only 'manual'"}), 400
    # detectionModes: what the camera's own event list supports, recorded by GET .../detection.
    # A mode outside it would subscribe to a topic the camera never sends (FoundAndFixed.md #65).
    supported = item.get("detectionModes")
    if mode != "manual" and supported is not None and mode not in supported:
        return jsonify({"error": f"this camera doesn't publish the events for '{mode}' "
                                 f"(it supports: {', '.join(supported) or 'no detection mode'})"}), 409

    table.update_item(
        Key={"cameraId": camera_id},
        UpdateExpression="SET recordingMode = :m",
        ExpressionAttributeValues={":m": mode},
    )
    return jsonify({"cameraId": camera_id, "recordingMode": mode})


def _read_detection(host, port, user, password):
    """(detection map, topics, web URL) read from the camera: its event list (onvif_events) and
    its own settings page, if it has one (onvif_discovery.web_url)."""
    async def read():
        from onvif import ONVIFCamera
        import onvif_events
        cam = ONVIFCamera(host, int(port or 80), user, password, wsdl_dir=camera_control.WSDL_DIR)
        try:
            await cam.update_xaddrs()
            dmap, topics = await onvif_events.camera_detection(cam)
            try:
                ports = await onvif_discovery.http_ports(cam)
            except Exception:  # noqa: BLE001
                ports = []
            return dmap, topics, ports
        finally:
            await cam.close()
    dmap, topics, ports = asyncio.run(asyncio.wait_for(read(), 15))
    return dmap, topics, onvif_discovery.web_url(host, ports)


def _record_detection(camera_id, dmap, item=None, web_url=None):
    """Write detectionModes (the modes), detectionTopics (mode -> topic and on/off field) and
    webUrl (the camera's own settings page, where it has one) to the registry, field-level and
    only when they changed. The same fields and meanings VMS writes; recordingMode keeps its
    four shared values. Best effort: the next table load retries."""
    import onvif_events
    modes = onvif_events.supported_modes(dmap)
    topics = {m: {"topic": v["topic"], "field": v["field"]} for m, v in dmap.items()}
    if item is not None and item.get("detectionModes") == modes and \
            item.get("detectionTopics") == topics and (web_url is None or item.get("webUrl") == web_url):
        return modes
    sets, values = "detectionModes = :m, detectionTopics = :t", {":m": modes, ":t": topics}
    if web_url:
        sets += ", webUrl = :w"
        values[":w"] = web_url
    try:
        cameras_table().update_item(Key={"cameraId": camera_id}, UpdateExpression=f"SET {sets}",
                                    ExpressionAttributeValues=values)
    except Exception:  # noqa: BLE001
        pass
    return modes


@app.get("/api/cameras/<camera_id>/detection")
def get_detection_modes(camera_id):
    """The detection modes this camera can serve and the topic behind each, read from its own
    event list (onvif_events.detection_map), once per table load like the codec, and recorded in
    the registry so the cloud page (via GET /cameras) and the mode routes offer the same choice."""
    try:
        item = _camera_item_quick(camera_id)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"registry unreachable ({type(e).__name__})"}), 503
    if not item:
        return jsonify({"error": "unknown camera"}), 404
    if not item.get("onvifHost"):
        return jsonify({"modes": [], "reason": "no ONVIF host: manual recording only"})
    try:
        dmap, topics, web_url = _read_detection(item["onvifHost"], item.get("onvifPort", 80),
                                                item["onvifUser"], item["onvifPassword"])
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"camera unreachable ({type(e).__name__})",
                        "modes": item.get("detectionModes"), "webUrl": item.get("webUrl"),
                        "tests": item.get("detectionTests")}), 502
    modes = _record_detection(camera_id, dmap, item, web_url)
    return jsonify({"modes": modes, "detection": dmap, "topics": sorted(topics),
                    "webUrl": web_url or item.get("webUrl"), "tests": item.get("detectionTests")})


# --- trigger test ------------------------------------------------------------------------------
#
# "Offered" is not "works": a camera's event list says which detections it has, not that they are
# switched on (cam-03's cell motion needed motion detection ticked in its own web interface) or
# that they reach ONVIF at all (its human alarm never does). The test listens to the camera for up
# to a minute while someone walks past, for every trigger it offers at once, and records what
# arrived. In memory while it runs, polled by the page without any AWS call (FoundAndFixed.md #56);
# the outcome goes to the registry as detectionTests when it ends, for both GUIs to show.

TRIGGER_TEST_SEC = 60
START_STATE_SEC = 2.0
_trigger_tests = {}                 # cameraId -> state, written by the test's thread
_trigger_lock = threading.Lock()


@app.post("/api/cameras/<camera_id>/detection/test")
def start_trigger_test(camera_id):
    secs = (request.get_json(force=True, silent=True) or {}).get("seconds", TRIGGER_TEST_SEC)
    if not isinstance(secs, int) or isinstance(secs, bool) or not 15 <= secs <= 180:
        return jsonify({"error": "seconds must be a whole number from 15 to 180"}), 400
    try:
        item = _camera_item_quick(camera_id)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"registry unreachable ({type(e).__name__})"}), 503
    if not item:
        return jsonify({"error": "unknown camera"}), 404
    if camera_control.is_unregistered(item):
        return _unregistered_error(camera_id)
    if not item.get("onvifHost"):
        return jsonify({"error": "no ONVIF host: this camera has no events to test"}), 400
    with _trigger_lock:
        running = _trigger_tests.get(camera_id)
        if running and running["state"] in ("starting", "listening"):
            return jsonify({"error": "a test is already running for this camera"}), 409
        state = {"state": "starting", "cameraId": camera_id, "seconds": secs,
                 "startedAt": _now(), "modes": {}, "otherTopics": {}, "error": None,
                 "_stop": threading.Event()}
        _trigger_tests[camera_id] = state
    threading.Thread(target=lambda: asyncio.run(_trigger_test(item, state)), daemon=True).start()
    return jsonify(_public_test(state)), 202


@app.get("/api/cameras/<camera_id>/detection/test")
def trigger_test_status(camera_id):
    with _trigger_lock:
        state = _trigger_tests.get(camera_id)
        return jsonify(_public_test(state) if state else {"state": "none", "cameraId": camera_id})


@app.delete("/api/cameras/<camera_id>/detection/test")
def stop_trigger_test(camera_id):
    with _trigger_lock:
        state = _trigger_tests.get(camera_id)
        if state:
            state["_stop"].set()
        return jsonify(_public_test(state) if state else {"state": "none", "cameraId": camera_id})


def _public_test(state):
    out = {k: copy.deepcopy(v) for k, v in state.items() if not k.startswith("_")}
    if state["state"] == "listening":
        out["secondsLeft"] = max(0, int(state["_deadline"] - time.monotonic()))
    return out


async def _trigger_test(item, state):
    """Listen for every trigger the camera offers until each has fired, the time is up, or Stop.

    What a fresh subscription delivers first -- the Initialized state and any backlog of earlier
    Changed messages, which cam-03 sends together 0.3 s after subscribing -- is the state *at the
    start*, not a detection during the walk: anything in the first START_STATE_SEC is recorded
    apart and never counts as seen. After that window an Initialized message is still not counted,
    but a Changed one is: cam-04 sends both for every detection (FoundAndFixed.md #68), so a test
    that skipped both would never see it fire."""
    from onvif import ONVIFCamera
    import onvif_events
    cam = None
    try:
        cam = ONVIFCamera(item["onvifHost"], int(item.get("onvifPort", 80)), item["onvifUser"],
                          item["onvifPassword"], wsdl_dir=camera_control.WSDL_DIR)
        await cam.update_xaddrs()
        dmap, _ = await onvif_events.camera_detection(cam)
        by_topic = {v["topic"]: m for m, v in dmap.items()}
        with _trigger_lock:
            state["modes"] = {m: {"topic": v["topic"], "field": v["field"], "seen": 0,
                                  "firstAfterSec": None, "atStart": None} for m, v in dmap.items()}
        if not dmap:
            return
        async with onvif_events.PullSubscription(cam) as sub:
            t0 = time.monotonic()
            with _trigger_lock:
                state["state"], state["_deadline"] = "listening", t0 + state["seconds"]
            while time.monotonic() < state["_deadline"] and not state["_stop"].is_set():
                notes = await sub.pull()
                # The camera's opening report (Initialized state, backlog) arrives at once: cam-03
                # sent it 0.3 s after subscribing. A person can't have walked in by then.
                first = time.monotonic() - t0 < START_STATE_SEC
                with _trigger_lock:
                    for note in notes:
                        topic, op, data = onvif_events.parse_notification(note)
                        mode = by_topic.get(topic)
                        if mode is None:
                            state["otherTopics"][topic] = state["otherTopics"].get(topic, 0) + 1
                            continue
                        value = onvif_events.state_value(data, dmap[mode]["field"])
                        if value is None:
                            continue
                        on, entry = onvif_events.is_on(value), state["modes"][mode]
                        if first or op == "Initialized":
                            entry["atStart"] = on
                        elif on:
                            entry["seen"] += 1
                            if entry["firstAfterSec"] is None:
                                entry["firstAfterSec"] = round(time.monotonic() - t0, 1)
                    if all(e["seen"] for e in state["modes"].values()):
                        break
    except Exception as e:  # noqa: BLE001
        with _trigger_lock:
            state["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    finally:
        if cam is not None:
            try:
                await cam.close()
            except Exception:  # noqa: BLE001
                pass
        with _trigger_lock:
            state["endedAt"] = _now()
            state["stopped"] = state["_stop"].is_set()
            state["state"] = "error" if state["error"] else "done"
        if not state["error"] and state["modes"]:
            _record_trigger_test(item["cameraId"], state)


def _record_trigger_test(camera_id, state):
    """Merge this test's outcome into the registry's detectionTests (mode -> result, when, how
    many), best effort. A stopped test records only what it saw: "not seen" needs the full time."""
    results = {}
    for mode, e in state["modes"].items():
        if e["seen"]:
            results[mode] = {"result": "seen", "at": state["endedAt"], "events": e["seen"]}
        elif not state["stopped"]:
            results[mode] = {"result": "not-seen", "at": state["endedAt"], "events": 0}
    if not results:
        return
    try:
        table = cameras_table()
        current = (table.get_item(Key={"cameraId": camera_id}).get("Item") or {}).get("detectionTests") or {}
        table.update_item(Key={"cameraId": camera_id}, UpdateExpression="SET detectionTests = :d",
                          ExpressionAttributeValues={":d": {**current, **results}})
    except Exception:  # noqa: BLE001
        pass


@app.post("/api/cameras/<camera_id>/audio")
def set_audio(camera_id):
    """Writes audioEnabled to the registry. Mirrors the cloud `set-camera-audio` Lambda.

    Takes effect on the camera's next Start, not immediately: the producer reads the flag
    once at launch (adapter/bin/camera-audio.py) because KVS refuses a stream whose
    fragments change from video-only to audio+video partway through.
    """
    enabled = (request.get_json(force=True, silent=True) or {}).get("audioEnabled")
    if not isinstance(enabled, bool):
        return jsonify({"error": "audioEnabled must be true or false"}), 400

    table = cameras_table()
    item = table.get_item(Key={"cameraId": camera_id}).get("Item")
    if not item:
        return jsonify({"error": "unknown camera"}), 404
    if camera_control.is_unregistered(item):
        return _unregistered_error(camera_id)
    if enabled and not item.get("audioCapable"):
        return jsonify({"error": "camera has no usable audio source"}), 400

    table.update_item(
        Key={"cameraId": camera_id},
        UpdateExpression="SET audioEnabled = :a",
        ExpressionAttributeValues={":a": enabled},
    )
    return jsonify({"cameraId": camera_id, "audioEnabled": enabled, "appliesOn": "next start"})


@app.post("/api/cameras/<camera_id>/outage-buffer")
def set_outage_buffer(camera_id):
    """Writes outageBufferSec. Mirrors the cloud `set-camera-outage-buffer` Lambda.

    Unlike the audio flag this needs no restart: kvs-outage-buffer.service re-reads the
    registry every 60s and arms or disarms MediaMTX recording in place. It only has any
    effect while the camera's KVS producer is running, though -- with no producer there is
    no cloud stream to protect, and that gate is what keeps the rolling pre-roll off the
    USB stick 24/7.
    """
    VALID = {0, 30, 120, 300, 600, 1800, 3600, 18000, 43200, 86400}
    secs = (request.get_json(force=True, silent=True) or {}).get("outageBufferSec")
    if not isinstance(secs, int) or isinstance(secs, bool) or secs not in VALID:
        return jsonify({"error": f"outageBufferSec must be one of {sorted(VALID)}"}), 400

    table = cameras_table()
    item = table.get_item(Key={"cameraId": camera_id}).get("Item")
    if not item:
        return jsonify({"error": "unknown camera"}), 404
    if camera_control.is_unregistered(item):
        return _unregistered_error(camera_id)

    table.update_item(
        Key={"cameraId": camera_id},
        UpdateExpression="SET outageBufferSec = :s",
        ExpressionAttributeValues={":s": secs},
    )
    return jsonify({"cameraId": camera_id, "outageBufferSec": secs, "appliesOn": "within 60s"})


# --- Video codec (guide §21) ------------------------------------------------------------------
#
# The codec is a property of the camera, so switching it means switching the camera's own
# encoder over ONVIF Media2 (adapter/onvif_media2.py); nothing on the Pi transcodes. Everything
# downstream follows by itself: the camera drops RTSP, MediaMTX reconnects with the new track,
# the producer's watchdog restarts it on the matching chain, and stream-codec.py records
# videoCodecActive and the KVS MediaType. Measured on cam-03 (in VMS): ~6 s without video.
#
# The switch changes the *recorded* stream's encoder only (rtspUrl). A camera recording main
# with live view on sub keeps the sub stream's codec; the live producer records that codec only
# in its own KVS stream's MediaType (stream-codec.py --live).
#
# cam-01 is encoded by the Pi, which has no H.265 encoder (guide §21.1), so it is shown, not
# offered.

CODEC_VERIFY_SEC = 45     # the camera's reconnect, plus one failed attempt on a half-ready SDP


def _camera_item_quick(camera_id):
    """The registry row, on short timeouts. The codec, stream and detection routes need the ONVIF
    password, which the local cache deliberately doesn't hold; they run once per page load, never
    from a poll (FoundAndFixed.md #56)."""
    from botocore.config import Config
    quick = Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1})
    table = get_session(REGION).resource("dynamodb", config=quick).Table("cameras")
    return table.get_item(Key={"cameraId": camera_id}).get("Item")


def _codec_options(opts):
    return {c: ({"available": True, "encodedBy": "camera"} if e in opts else
                {"available": False, "reason": "the camera's encoder doesn't offer it"})
            for c, e in onvif_media2.ENCODING.items()}


@app.get("/api/cameras/<camera_id>/codec")
def get_codec(camera_id):
    """What the camera can send, what it is set to, and what MediaMTX actually receives.

    `wire` is the truth and `configured` only the camera's claim: cam-03 claimed H264 2880x1620
    while it sent H.265 2560x1440, so the page shows both when they differ. Read once per table
    load, never polled.
    """
    try:
        item = _camera_item_quick(camera_id)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"registry unreachable ({type(e).__name__})"}), 503
    if not item:
        return jsonify({"error": "unknown camera"}), 404
    out = {"cameraId": camera_id, "chosen": item.get("videoCodec"),
           "wire": mediamtx_api.path_video_track(camera_control.mediamtx_path_name(camera_id))}
    if not item.get("onvifHost"):
        return jsonify({**out, "switchable": False, "configured": "h264", "options": {
            c: ({"available": True, "encodedBy": "pi-hw"} if not codec_caps.unavailable_reason(item, c)
                else {"available": False, "reason": codec_caps.unavailable_reason(item, c)})
            for c in codec_caps.CODECS}})
    try:
        cam = onvif_media2.for_camera(item)
        prof = onvif_media2.stream_profile(cam, item.get("rtspUrl"))
        settings = onvif_media2.summarize(cam.encoder_config(prof["encoderToken"]))
        opts = cam.encoder_options(prof["encoderToken"])
        options = _codec_options(opts)
    except onvif_media2.OnvifError as e:
        return jsonify({**out, "error": str(e)}), 502
    # The registry learns the codec from a switch, so a camera registered already sending
    # H.265, or switched in its own web page, had no videoCodec or a stale one: the cloud page
    # then labelled it from list_cameras' fallback (FoundAndFixed.md #69). The wire is the
    # authority: record it when the registry disagrees. Once, since the next read agrees.
    if out["wire"] and item.get("videoCodec") != out["wire"]["codec"]:
        out["registry"] = _record_codec(camera_id, out["wire"]["codec"], opts, prof["encoderToken"])
        if out["registry"]["recorded"]:
            out["chosen"] = out["wire"]["codec"]
    return jsonify({**out, "options": options,
                    "switchable": sum(o["available"] for o in options.values()) > 1,
                    "configured": onvif_media2.CODEC.get(settings["encoding"], settings["encoding"]),
                    "settings": settings, "encoderToken": prof["encoderToken"]})


def _wait_for_wire(path, codec, budget=CODEC_VERIFY_SEC):
    """The video track once MediaMTX receives `codec` on `path` and it is flowing, or None
    after `budget`. Flowing too, not just declared: a path can be `ready` with the new track
    while nothing arrives yet (FoundAndFixed.md #50)."""
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        track = mediamtx_api.path_video_track(path)
        if track and track["codec"] == codec and mediamtx_api.path_is_flowing(path):
            return track
        time.sleep(0.5)
    return None


def _record_codec(camera_id, codec, opts, encoder_token):
    """Write the choice to the registry, field by field. Best effort: the camera has already
    switched, and the producer records what it streams on its own (videoCodecActive).

    videoCodec is the admin's choice -- the cloud page labels the camera and warns a browser
    without HEVC from it -- videoCodecCaps says who encodes each codec, videoEncoderToken the
    encoder behind the recorded stream (it changes with a stream switch). VMS writes the same
    fields with the same meanings. videoCodecDefault is codec_caps.py's and is not touched.
    """
    caps = {c: ("camera" if e in opts else "none") for c, e in onvif_media2.ENCODING.items()}
    try:
        from botocore.config import Config
        quick = Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1})
        get_session(REGION).resource("dynamodb", config=quick).Table("cameras").update_item(
            Key={"cameraId": camera_id},
            UpdateExpression=("SET videoCodec = :c, videoCodecCaps = :caps, "
                              "videoEncoderToken = :t, videoCodecProbe = :p"),
            ExpressionAttributeValues={":c": codec, ":caps": caps, ":t": encoder_token,
                                       ":p": "media2"})
        return {"recorded": True}
    except Exception as e:  # noqa: BLE001
        return {"recorded": False, "reason": f"{type(e).__name__}: {e}"}


@app.post("/api/cameras/<camera_id>/codec")
def set_codec(camera_id):
    """Switch the camera's encoder to {"videoCodec": "h264"|"h265"}, and prove it.

    Done means MediaMTX receives the new codec, not that the camera said yes: a camera's
    read-back can disagree with its stream (get_codec), so the read-back is reported, never
    the test. Every other encoder setting is kept where the new codec allows it, and the
    picture size is taken from the wire rather than from the camera's possibly stale
    configuration (onvif_media2.plan_config). The encoder is found per request from the
    registered rtspUrl, never from a stored token, which a stream switch makes stale. If the
    wire already carries the codec, the camera is not touched at all -- no reason to cost
    ~6 s of video.

    A running KVS producer is not stopped first: MediaMTX closes its session when the source's
    codec changes, and the producer restarts itself on the new codec (stream-codec.py,
    FoundAndFixed.md #44). A stopped producer catches up at its next Start. Not rolled back on
    failure: either codec is a working stream, so a camera that is slow to come back is
    reported, not reverted into a second interruption.
    """
    target = (request.get_json(force=True, silent=True) or {}).get("videoCodec")
    if target not in onvif_media2.ENCODING:
        return jsonify({"error": f"videoCodec must be one of {sorted(onvif_media2.ENCODING)}"}), 400
    try:
        item = _camera_item_quick(camera_id)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"registry unreachable ({type(e).__name__})"}), 503
    if not item:
        return jsonify({"error": "unknown camera"}), 404
    if camera_control.is_unregistered(item):
        return _unregistered_error(camera_id)
    reason = codec_caps.unavailable_reason(item, target) if not item.get("onvifHost") else None
    if not item.get("onvifHost"):
        if reason:
            return jsonify({"error": f"{codec_caps.NAMES[target]} not available: {reason}"}), 400
        return jsonify({"cameraId": camera_id, "videoCodec": target, "unchanged": True})

    path = camera_control.mediamtx_path_name(camera_id)
    t0 = time.monotonic()
    try:
        cam = onvif_media2.for_camera(item)
        prof = onvif_media2.stream_profile(cam, item.get("rtspUrl"))
        cfg = cam.encoder_config(prof["encoderToken"])
        opts = cam.encoder_options(prof["encoderToken"])
        encoding = onvif_media2.ENCODING[target]
        if encoding not in opts:
            return jsonify({"error": f"the camera's encoder doesn't offer {encoding}"}), 409
        wire = mediamtx_api.path_video_track(path)
        unchanged = bool(wire and wire["codec"] == target)
        if not unchanged:
            size = (wire["width"], wire["height"]) if wire else None
            cam.set_encoder_config(onvif_media2.plan_config(cfg, opts, encoding, size))
        settings = onvif_media2.summarize(cam.encoder_config(prof["encoderToken"]))
    except onvif_media2.OnvifError as e:
        return jsonify({"error": str(e)}), 502

    if not unchanged:
        wire = _wait_for_wire(path, target)
        if not wire:
            now = mediamtx_api.path_video_track(path)
            return jsonify({
                "error": f"the camera accepted {encoding} (it reads back "
                         f"{settings['encoding']}), but after {CODEC_VERIFY_SEC} s MediaMTX "
                         f"receives {now['codec'].upper() if now else 'no video'}",
                "settings": settings}), 504
    return jsonify({"cameraId": camera_id, "videoCodec": target, "unchanged": unchanged,
                    "wire": wire, "settings": settings,
                    "tookSec": round(time.monotonic() - t0, 1),
                    "producer": camera_control.get_stream_status(camera_id),
                    "registry": _record_codec(camera_id, target, opts, prof["encoderToken"])})


@app.get("/api/cameras/<camera_id>/on-wire")
def on_wire_codec(camera_id):
    """The codec MediaMTX receives right now, and whether video is actually flowing --
    polled by the GUI while a switch settles. `flowing` samples the byte counter for ~1 s:
    after a switch the path can be `ready` with the new track declared while nothing
    arrives yet (FoundAndFixed.md #50). Local only, so polling it costs AWS nothing."""
    if not CAMERA_ID_RE.match(camera_id):
        return jsonify({"error": "bad camera id"}), 400
    path = camera_control.mediamtx_path_name(camera_id)
    return jsonify({"cameraId": camera_id, "onWire": mediamtx_api.path_video_codec(path),
                    "flowing": mediamtx_api.path_is_flowing(path)})


# --- stream and frame rate ---------------------------------------------------------------------
#
# Which of the camera's streams this system records (main or sub) and at what frame rate. Like the
# codec, both are properties of the camera and stay LAN-side. Done means the wire: MediaMTX
# receives the other stream, or the frames arrive at the new rate -- never the camera's read-back.
# Only 10 and 15 fps exist here (D5): onvif_media2.FPS_CHOICES.

FPS_TOLERANCE = 0.15       # cam-03 sends 19.8 fps at a 20 fps limit; a camera capping lower is reported
FPS_MEASURE_SEC = 4
FPS_VERIFY_SEC = 30


def _measure_fps(path, seconds=FPS_MEASURE_SEC):
    """Frames per second MediaMTX re-serves on `path`, from the timestamps of `seconds` of video
    packets. One reader for a few seconds; None when nothing usable arrives."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-select_streams", "v:0",
             "-show_entries", "packet=pts_time", "-of", "csv=p=0",
             "-read_intervals", f"%+{seconds}", f"rtsp://127.0.0.1:8554/{path}"],
            capture_output=True, text=True, timeout=seconds + 10)
    except subprocess.TimeoutExpired:
        return None
    ts = []
    for line in r.stdout.split():
        try:
            ts.append(float(line.strip(",")))
        except ValueError:
            pass                          # "N/A": the first packets before RTCP sync
    ts.sort()
    if len(ts) < 3 or ts[-1] <= ts[0]:
        return None
    return round((len(ts) - 1) / (ts[-1] - ts[0]), 1)


def _fps_ok(measured, fps):
    return measured is not None and abs(measured - fps) <= FPS_TOLERANCE * fps


def _verify_fps(path, fps, budget=FPS_VERIFY_SEC):
    """Measure until the wire shows `fps` or the budget runs out; the last measurement either
    way. A camera may apply the rate in place or restart its RTSP session first."""
    deadline = time.monotonic() + budget
    measured = None
    while time.monotonic() < deadline:
        if (mediamtx_api.get_path_state(path) or {}).get("ready"):
            measured = _measure_fps(path)
            if _fps_ok(measured, fps):
                return measured
        time.sleep(2)
    return measured


def _wait_for_new_session(path, ready_before, budget=CODEC_VERIFY_SEC):
    """The video track once MediaMTX has reconnected to the path's (new) source -- readyTime
    differs from `ready_before` -- and video flows, or None after `budget`."""
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        state = mediamtx_api.get_path_state(path) or {}
        if state.get("ready") and state.get("readyTime") != ready_before:
            track = mediamtx_api.path_video_track(path)
            if track and mediamtx_api.path_is_flowing(path):
                return track
        time.sleep(0.5)
    return None


def _stream_view(x):
    # D5: a rate other than 10 or 15 is never shown as a choice; the page shows "choose 10 or
    # 15" for a camera currently at another one.
    return {**{k: x[k] for k in ("name", "encoding", "width", "height", "govLength", "fpsChoices")},
            "fps": x["fps"] if x["fps"] in onvif_media2.FPS_CHOICES else None}


@app.get("/api/cameras/<camera_id>/stream")
def get_stream_choice(camera_id):
    """The camera's streams, which one this system records, and the frame rates (10/15) each
    accepts. Read-only, once per table load (FoundAndFixed.md #56)."""
    try:
        item = _camera_item_quick(camera_id)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"registry unreachable ({type(e).__name__})"}), 503
    if not item:
        return jsonify({"error": "unknown camera"}), 404
    if not item.get("onvifHost"):
        return jsonify({"switchable": False,
                        "reason": "the USB camera is encoded on the Pi at a fixed 15 fps"})
    try:
        found = onvif_media2.streams(onvif_media2.for_camera(item))
    except onvif_media2.OnvifError as e:
        return jsonify({"error": str(e)}), 502
    current = next((x["name"] for x in found
                    if onvif_media2.same_stream(x["streamUri"], item.get("rtspUrl"))), None)
    live_path = (camera_control.live_path_name(camera_id) if item.get("liveRtspUrl")
                 else camera_control.mediamtx_path_name(camera_id))
    return jsonify({"cameraId": camera_id, "switchable": True, "current": current,
                    "streams": [_stream_view(x) for x in found],
                    "wire": mediamtx_api.path_video_track(camera_control.mediamtx_path_name(camera_id)),
                    # What live view (preview and cloud Live player) shows.
                    "live": {"stream": "sub" if item.get("liveRtspUrl") else current,
                             "path": live_path, "wire": mediamtx_api.path_video_track(live_path)}})


@app.post("/api/cameras/<camera_id>/stream")
def set_stream_choice(camera_id):
    """Switch to {"stream": "main"|"sub"} and/or set {"fps": 10|15}, and prove it.

    A stream switch points the MediaMTX path at the other profile's URI (credentials carried
    over, onvif_media2.with_credentials) and writes rtspUrl first, because a MediaMTX restart
    rebuilds the path from the registry. MediaMTX reconnecting with video is the proof; the
    producer follows on its own, like after a codec switch. The frame rate is then set on the
    encoder of the stream now pulled, keyframe interval kept in seconds, and proved by counting
    frames on the wire. A camera that sends fewer than asked is reported, not rolled back.
    """
    body = request.get_json(force=True, silent=True) or {}
    want_stream, want_fps = body.get("stream"), body.get("fps")
    if want_stream is None and want_fps is None:
        return jsonify({"error": "give stream and/or fps"}), 400
    if want_fps is not None and (isinstance(want_fps, bool) or want_fps not in onvif_media2.FPS_CHOICES):
        return jsonify({"error": f"fps must be one of {list(onvif_media2.FPS_CHOICES)}"}), 400
    try:
        item = _camera_item_quick(camera_id)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"registry unreachable ({type(e).__name__})"}), 503
    if not item:
        return jsonify({"error": "unknown camera"}), 404
    if not item.get("onvifHost"):
        return jsonify({"error": "the USB camera is encoded on the Pi at a fixed 15 fps"}), 400
    if camera_control.is_unregistered(item):
        return _unregistered_error(camera_id)

    path = camera_control.mediamtx_path_name(camera_id)
    t0 = time.monotonic()
    out = {"cameraId": camera_id}
    try:
        cam = onvif_media2.for_camera(item)
        found = onvif_media2.streams(cam)
        target = next((x for x in found
                       if onvif_media2.same_stream(x["streamUri"], item.get("rtspUrl"))), None)
        if target is None:
            return jsonify({"error": "none of the camera's streams is the registered one -- "
                                     "re-register the camera"}), 502

        if want_stream and want_stream != target["name"]:
            new = next((x for x in found if x["name"] == want_stream), None)
            if new is None:
                return jsonify({"error": f"the camera has no '{want_stream}' stream"}), 400
            new_uri = onvif_media2.with_credentials(new["streamUri"], item["rtspUrl"])
            ready_before = (mediamtx_api.get_path_state(path) or {}).get("readyTime")
            table = cameras_table()
            table.update_item(Key={"cameraId": camera_id},
                              UpdateExpression="SET rtspUrl = :u, updatedAt = :t",
                              ExpressionAttributeValues={":u": new_uri, ":t": _now()})
            try:
                mediamtx_api.patch_path(path, {"source": new_uri, "rtspTransport": "tcp"})
            except (mediamtx_api.MediaMTXError, requests.RequestException) as e:
                table.update_item(Key={"cameraId": camera_id}, UpdateExpression="SET rtspUrl = :u",
                                  ExpressionAttributeValues={":u": item["rtspUrl"]})
                return jsonify({"error": f"MediaMTX path update failed: {e}"}), 502
            _refresh_path_cache()
            wire = _wait_for_new_session(path, ready_before)
            if not wire:
                return jsonify({"error": f"switched to the {want_stream} stream, but after "
                                         f"{CODEC_VERIFY_SEC} s MediaMTX receives no video from it"}), 504
            # videoCodec and the encoder token follow the stream: the codec now delivered is
            # the new stream's, which can differ (cam-03: main H.265, sub claims H.264 and
            # sends H.265) -- so from the wire, not the claim.
            out["registry"] = _record_codec(camera_id, wire["codec"],
                                            cam.encoder_options(new["encoderToken"]), new["encoderToken"])
            out["stream"], out["wire"] = new["name"], wire
            target, item = new, {**item, "rtspUrl": new_uri}
            # Recording moved to main: live view goes on to sub; moved to sub: live view is the
            # recorded stream again.
            out["liveView"] = _apply_live_view(camera_id, item, found)
            _refresh_path_cache()

        if want_fps is not None:
            settings = _set_camera_fps(cam, item["rtspUrl"], want_fps)
            measured = _verify_fps(path, want_fps)
            out["fps"] = {"asked": want_fps, "measured": measured,
                          "configured": settings["frameRate"], "govLength": settings["govLength"]}
            if not _fps_ok(measured, want_fps):
                out["warning"] = (f"the camera accepted {want_fps} fps, but MediaMTX receives "
                                  + (f"{measured} fps" if measured else "no countable video"))
    except onvif_media2.OnvifError as e:
        return jsonify({"error": str(e), **out}), 502

    out["tookSec"] = round(time.monotonic() - t0, 1)
    return jsonify(out)


@app.post("/api/cameras/<camera_id>/ir")
def set_ir(camera_id):
    mode = (request.get_json(force=True, silent=True) or {}).get("mode")
    item = cameras_table().get_item(Key={"cameraId": camera_id}).get("Item")
    if not item:
        return jsonify({"error": "unknown camera"}), 404
    if camera_control.is_unregistered(item):
        return _unregistered_error(camera_id)
    if not item.get("hasIrControl"):
        return jsonify({"error": "camera has no IR control"}), 400
    try:
        camera_control.set_ir_mode(item, mode)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"cameraId": camera_id, "irMode": mode})


@app.post("/api/cameras/<camera_id>/<action>")
def stream_action(camera_id, action):
    if action not in ("start", "stop"):
        return jsonify({"error": "action must be 'start' or 'stop'"}), 400
    item = cameras_table().get_item(Key={"cameraId": camera_id}).get("Item")
    if not item:
        return jsonify({"error": "unknown camera"}), 404
    if action == "start" and camera_control.is_unregistered(item):
        return _unregistered_error(camera_id)
    if action == "start" and not camera_control.has_producer(camera_id):
        return jsonify({"error": f"no producer for '{camera_id}' on this Pi (it was registered "
                                 f"from the other SD card): Re-register it first"}), 409
    camera_control.set_stream(camera_id, action == "start")
    return jsonify({"cameraId": camera_id, "action": action})


if __name__ == "__main__":
    # Codec capabilities for every camera, refreshed each time this service starts (it runs
    # from boot). In the background: AWS or a camera being unreachable must not delay the
    # GUI, and a failure here only ever means stale information, never a dead camera.
    codec_caps.refresh_in_background(cameras_table)
    app.run(host="0.0.0.0", port=8080)
