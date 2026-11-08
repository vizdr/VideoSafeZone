"""Local ONVIF admin GUI -- discover, register, and control ONVIF cameras on this LAN.

Deliberately separate from the cloud client (client-app/): WS-Discovery is UDP multicast, which only
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
import camera_registration
import codec_caps
import config
import live_view
import mediamtx_api
import onvif_discovery
import onvif_media2
from aws_device_creds import get_session
from outage_buffer import read_registry_cache

app = Flask(__name__, static_folder="static")

CAMERA_ID_RE = camera_registration.CAMERA_ID_RE
MEDIAMTX_API = "http://127.0.0.1:9997"
REGION = config.AWS_REGION
PROVISION_SH = str(Path(__file__).resolve().parent.parent / "bin" / "provision-camera.sh")
LEGACY_UNITS = camera_registration.LEGACY_UNITS
WIRE_WAIT_SEC = 15       # registration: how long to wait for the new path's video before probing


def cameras_table():
    return get_session(REGION).resource("dynamodb").Table("cameras")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unregistered_error(camera_id):
    return jsonify({"error": f"'{camera_id}' is unregistered: Re-register it first"}), 409


def _foreign_error(item):
    """Another adapter's camera, or one released from this one (DEALER-PANEL-PLAN.md D12, D15): not
    this Pi's to change. Stop stays possible: its producer may still run here."""
    payload, status = camera_registration.foreign_error(item)
    return jsonify(payload), status


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
    # This Pi's cameras only (D12): another adapter's are on another LAN, and the page would offer
    # Start, codec and IR for cameras this Pi can't reach. Unregistered ones stay: Re-register.
    items = [i for i in cameras_table().scan()["Items"] if camera_control.is_mine(i)]
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
    """True once `path` has video actually flowing, False after `budget` seconds (camera_registration);
    this module's `time`, which its tests replace with a fake clock."""
    return camera_registration.wait_for_flowing(path, budget, clock=time)


def _provision(camera_id, start):
    """provision-camera.sh through sudo (camera_registration.provision), with this module's subprocess."""
    return camera_registration.provision(camera_id, start, run=subprocess.run)


def _registration_env():
    """The admin GUI's collaborators for camera_registration, looked up now: its tests patch them."""
    return camera_registration.Env(
        table=cameras_table(), kinesisvideo=lambda: get_session(REGION).client("kinesisvideo"),
        provision=lambda camera_id, start: _provision(camera_id, start), wait_for_flowing=_wait_for_flowing,
        read_detection=_read_detection, record_detection=_record_detection)


@app.post("/api/cameras")
def register_camera():
    payload, status = camera_registration.register(request.get_json(force=True, silent=True) or {},
                                                   _registration_env())
    return jsonify(payload), status


# The registration helpers the other routes share (camera_registration.py).
_set_camera_fps = camera_registration.set_camera_fps
_refresh_path_cache = camera_registration.refresh_path_cache
_all_names = camera_registration.all_names
_display_name = camera_registration.check_display_name


def _record_ceiling(camera_id, item, stream, fps=None):
    return camera_registration.record_ceiling(cameras_table(), camera_id, item, stream, fps)


def _apply_live_view(camera_id, item, found=None):
    return camera_registration.apply_live_view(
        cameras_table(), lambda: get_session(REGION).client("kinesisvideo"), camera_id, item, found)

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
    """Unregister: the soft mark, the producer stopped and disabled, the paths deleted
    (camera_registration.unregister, shared with the agent's `unregister`, D16)."""
    payload, status = camera_registration.unregister(cameras_table(), camera_id, run=subprocess.run)
    return jsonify(payload), status


@app.post("/api/cameras/<camera_id>/reregister")
def reregister_camera(camera_id):
    """Re-register an unregistered camera from what its row holds -- the USB camera too, which
    Discover can't find (camera_registration.reregister, shared with the agent's `reregister`)."""
    payload, status = camera_registration.reregister(_registration_env(), camera_id, run=subprocess.run)
    return jsonify(payload), status


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
    if item and not camera_control.is_mine(item):
        return _foreign_error(item)
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
    """(detection map, topics, web URL) read from the camera (camera_registration.read_detection)."""
    return camera_registration.read_detection(host, port, user, password)


def _record_detection(camera_id, dmap, item=None, web_url=None):
    """The camera's detection modes into the registry (camera_registration.record_detection)."""
    return camera_registration.record_detection(cameras_table(), camera_id, dmap, item=item, web_url=web_url)


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
    if item and not camera_control.is_mine(item):
        return _foreign_error(item)
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
    if item and not camera_control.is_mine(item):
        return _foreign_error(item)
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
    if item and not camera_control.is_mine(item):
        return _foreign_error(item)
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
    if item and not camera_control.is_mine(item):
        return _foreign_error(item)
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
    accepts. Once per table load (FoundAndFixed.md #56). Read-only but for the cloud page's quality
    ceiling, written when the recorded stream differs from what the row says -- a switch made in
    VMS's admin GUI or the camera's own page -- as the codec is learnt on read (#69)."""
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
    recorded = next((x for x in found if x["name"] == current), None)
    ceiling = _record_ceiling(camera_id, item, recorded) if recorded else None
    return jsonify({"cameraId": camera_id, "switchable": True, "current": current, "ceiling": ceiling,
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
    if item and not camera_control.is_mine(item):
        return _foreign_error(item)
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
        # The new ceiling for the cloud page; a "sub" choice there goes if this isn't main (D5).
        out["ceiling"] = _record_ceiling(camera_id, item, target,
                                         out["fps"]["configured"] if "fps" in out else None)
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
    if item and not camera_control.is_mine(item):
        return _foreign_error(item)
    if camera_control.is_unregistered(item):
        return _unregistered_error(camera_id)
    if not item.get("hasIrControl"):
        return jsonify({"error": "camera has no IR control"}), 400
    # Checked here, as publish-cmd does: set_ir_mode's own check raises, and the catch-all below
    # turned a bad request into a 500 (FoundAndFixed.md #85).
    if mode not in camera_control.ALLOWED_IR_MODES:
        return jsonify({"error": "mode must be 'AUTO', 'ON', or 'OFF'"}), 400
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
    if action == "start" and item and not camera_control.is_mine(item):
        return _foreign_error(item)
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
