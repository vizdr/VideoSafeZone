"""Registering a camera (DEALER-PANEL-PLAN.md Phase 7.1, D16): one implementation, used by the
local admin GUI's form (adapter/onvif-admin/app.py) and by the agent's `register` command, which
the web page's Add cameras sends through the cloud.

A new camera gets its MediaMTX path (live, through MediaMTX's API), its kvs-cam@ producer
(provision-camera.sh through sudo, started, never enabled: #81), its codec capabilities, its KVS
stream and its registry row, in that order -- the row last, so no reader sees half a camera. A
camera this Pi already has is re-registered: its details refreshed, nothing re-provisioned that
exists (cam-01 and cam-02 run as their own units). Each caller hands over its collaborators in an
`Env` -- the registry table, a KVS client, provisioning, the wait for video, the camera's event
list -- made at call time, so each process keeps its own (and the admin GUI's tests their fakes).
"""
import asyncio
import re
import secrets
import subprocess
import time
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import requests
from botocore.exceptions import ClientError

import activation
import camera_control
import codec_caps
import config
import live_view
import mediamtx_api
import onvif_discovery
import onvif_media2

CAMERA_ID_RE = re.compile(r"^cam-\d{2,4}$")      # D11: cam-01 ... cam-9999
# A camera's display name ("Kitchen", "Hall-1", "Küche"): what people read in both GUIs (ported
# from VMS, which already wrote names into the shared registry). The cameraId stays the key
# everywhere else (KVS, S3, DynamoDB, MediaMTX, units), so a name is only ever displayed. Letters
# and digits in any script, space, - _ . -- nothing that HTML or a shell would read as syntax:
# both pages render it, and the admin GUI has no login.
DISPLAY_NAME_RE = re.compile(r"^[\w .-]{1,32}$")
DISPLAY_NAME_MAX = 32
# cam-01 and cam-02 predate the kvs-cam@ template and run as their own units: provisioning
# one of them would start a second, conflicting producer on the same KVS stream.
LEGACY_UNITS = ("cam-01", "cam-02")


@dataclass
class Env:
    """What a registration needs from its caller."""
    table: Any                                     # the registry's `cameras` table
    kinesisvideo: Callable[[], Any]                # a KVS client, made when one is needed
    provision: Callable[[str, bool], Any]          # (camera ID, start) -> CompletedProcess
    wait_for_flowing: Callable[[str], bool]        # MediaMTX path -> video arrived in time?
    read_detection: Callable[..., Any]             # (host, port, user, password) -> (map, topics, web URL)
    record_detection: Callable[..., Any]           # (camera ID, map, web_url=) -> the modes recorded


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_camera_id(table):
    """The next free camera ID (D11): one past the highest in the registry -- every adapter's rows
    and unregistered ones count, as their streams and recordings keep their IDs. The row is written
    only if the ID is still free when the camera is ready (register), so two registrations racing
    for one ID can't both have it."""
    taken, kw = [0], {"ProjectionExpression": "cameraId"}
    while True:
        page = table.scan(**kw)
        taken += [int(m.group(1)) for i in page["Items"] if (m := re.fullmatch(r"cam-(\d{2,4})", i["cameraId"]))]
        if "LastEvaluatedKey" not in page:
            break
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    n = max(taken) + 1
    if n > 9999:
        raise ValueError("no camera ID left (cam-9999 is the last)")
    return f"cam-{n:02d}"


def register(body, env):
    """Register a camera, or re-register one this Pi already has, from the admin GUI's form or the
    agent's `register` command. Returns (the answer as a dict, its HTTP status). A new camera
    without a cameraId gets the next free one (D11); one that names an ID gets it only if free."""
    camera_id = body.get("cameraId") or ""
    allocated = not camera_id
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

    if not allocated and not CAMERA_ID_RE.match(camera_id):
        return {"error": "cameraId must look like 'cam-03' -- or leave it out: a new camera gets the next free one"}, 400
    if not all([host, user, stream_uri]):
        return {"error": "host, user, and streamUri are required (the password may be empty)"}, 400
    if not isinstance(user, str) or not isinstance(password, str):
        return {"error": "user and password must be strings"}, 400
    if audio_capable is not None and not isinstance(audio_capable, bool):
        return {"error": "audioCapable must be true or false"}, 400
    if fps is not None and fps not in onvif_media2.FPS_CHOICES:
        return {"error": f"fps must be one of {list(onvif_media2.FPS_CHOICES)}"}, 400

    table = env.table
    if allocated:
        try:
            camera_id = next_camera_id(table)
        except ValueError as e:
            return {"error": str(e)}, 409
    existing = None if allocated else table.get_item(Key={"cameraId": camera_id}).get("Item")
    # A new camera only on an activated adapter (DEALER-PANEL-PLAN.md D16): on one that isn't, no
    # customer's token would reach it. Re-registering a camera that is already here stays possible.
    if not existing and (refusal := activation.registration_refusal()):
        return {"error": refusal}, 409
    mediamtx_path = camera_control.mediamtx_path_name(camera_id)
    display_name = None
    if name_given:
        display_name, err = check_display_name(body.get("displayName"), camera_id, all_names(table))
        if err:
            return {"error": err}, 400

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
            return {"error": f"'{camera_id}' exists but isn't a re-registerable ONVIF camera"}, 409
        # Another adapter's camera isn't this Pi's to re-register: that would hand it, and its
        # customer's recordings, to this Pi's customer (DEALER-PANEL-PLAN.md D11).
        owner = existing.get("adapterId")
        if owner and owner != config.THING_NAME:
            return {"error": f"'{camera_id}' belongs to another adapter ({owner})"}, 409
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
            return {"error": str(e)}, 400
        fps_result = registration_fps(host, port, user, password, stream_uri, fps)

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
            return {"error": f"MediaMTX path update failed: {e}"}, 502

        # REMOVE is a no-op on a row without the mark, so every re-registration can carry it.
        # adapterId: which Thing serves the camera -- what the camera API's tenant check compares
        # with the caller's token (DEALER-PANEL-PLAN.md D4); stamped here if the row predates it.
        sets = ("hasIrControl = :ir, onvifHost = :h, onvifPort = :p, onvifUser = :u, "
                "onvifPassword = :pw, rtspUrl = :uri, adapterId = :aid, updatedAt = :t")
        values = {":ir": has_ir_control, ":h": host, ":p": port, ":u": user, ":pw": password,
                  ":uri": stream_uri, ":aid": config.THING_NAME, ":t": now()}
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
            p = env.provision(camera_id, False)
            producer = ("provisioned (not started)" if p.returncode == 0
                        else f"provisioning failed: {(p.stderr or p.stdout).strip()}")

        # Re-ask the codec capabilities: the stream URI may now point at another profile,
        # or the row at another device. If the camera can't be asked right now the
        # existing answer is kept (codec_caps.refresh_item).
        _, caps_status = codec_caps.refresh_item(
            table, table.get_item(Key={"cameraId": camera_id})["Item"])
        detection = registration_detection(env, camera_id, host, port, user, password)
        # The stream may have changed, and an unregistered camera's live path went with
        # Unregister: set the live view up again from what the camera says now.
        reregistered = {**existing, "onvifHost": host, "onvifPort": port,
                        "onvifUser": user, "onvifPassword": password, "rtspUrl": stream_uri}
        live = apply_live_view(table, env.kinesisvideo, camera_id, reregistered)
        registration_ceiling(table, camera_id, reregistered)
        refresh_path_cache()
        return {"cameraId": camera_id, "kvsStreamArn": existing["kvsStreamArn"],
                        "updated": True, "reactivated": reactivate, "producer": producer,
                        "codecCaps": caps_status, "fps": fps_result,
                        "detectionModes": detection, "liveView": live}, 200

    # The login the camera's RTSP demands, in the URI MediaMTX pulls (FoundAndFixed.md #67).
    # Refused: nothing is created, rather than a camera whose every pull is a 401.
    try:
        stream_uri = onvif_discovery.rtsp_login(stream_uri, user, password)
    except onvif_discovery.RtspUnauthorized as e:
        return {"error": str(e)}, 400

    # 0. The frame rate, on the camera, before MediaMTX ever connects: a change can restart the
    #    camera's RTSP session, which costs nothing yet. Not fatal -- a camera without Media2
    #    keeps its own rate, and the response says so.
    fps_result = registration_fps(host, port, user, password, stream_uri, fps)

    # The ID, claimed before anything is made (D11). Another registration that picked the same one
    # -- this Pi's admin GUI, the web page through the agent, another Pi -- then finds it taken
    # here, before it has touched a path, a unit or a stream; claimed at the end instead, the loser
    # would already have rewritten the winner's channel file, started its unit into the winner's
    # KVS stream, and stopped it again on the way out.
    # A path MediaMTX has already (left by another camera, or by hand) refuses before the claim, so
    # the commonest failure leaves no claim behind.
    try:
        if mediamtx_api.get_path_conf(mediamtx_path) is not None:
            return {"error": f"MediaMTX has a path {mediamtx_path} already: nothing was registered"}, 502
    except (mediamtx_api.MediaMTXError, requests.RequestException) as e:
        return {"error": f"MediaMTX can't be asked: {e}"}, 502
    claimed, token_or_status = claim_camera_id(table, camera_id, allocated)
    if isinstance(claimed, dict):
        return claimed, token_or_status                   # (error, status)
    camera_id, token = claimed, token_or_status
    mediamtx_path = camera_control.mediamtx_path_name(camera_id)
    try:
        return _create(env, body_fields={
            "camera_id": camera_id, "token": token, "host": host, "port": port, "user": user,
            "password": password, "stream_uri": stream_uri, "has_ir_control": has_ir_control,
            "endpoint_ref": endpoint_ref, "audio_capable": audio_capable, "display_name": display_name,
            "fps_result": fps_result, "mediamtx_path": mediamtx_path})
    except Exception:
        abandon_claim(table, camera_id, token)
        raise


def claim_camera_id(table, camera_id, allocated, tries=5):
    """(camera ID, claim token), or (error, status). The claim is a row of this registration's own,
    marked unregistered so that every reader skips it (CLAUDE.md, Unregister) until register writes
    the camera over it. An allocated ID taken meanwhile is followed by the next free one."""
    token = secrets.token_hex(8)
    for _ in range(tries):
        try:
            table.put_item(Item={"cameraId": camera_id, "adapterId": config.THING_NAME, "unregisteredAt": now(),
                                 "registering": token},
                           ConditionExpression="attribute_not_exists(cameraId)")
            return camera_id, token
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
        if not allocated:
            return {"error": f"{camera_id} is taken -- leave the camera ID out and the next free one is used"}, 409
        try:
            camera_id = next_camera_id(table)
        except ValueError as e:
            return {"error": str(e)}, 409
    return {"error": "couldn't claim a camera ID: registrations keep taking them -- try again"}, 409


def abandon_claim(table, camera_id, token):
    """A registration that failed after its claim leaves the row unregistered -- skipped by every
    reader -- and marked abandoned; the ID stays used. The device role may update rows but not
    delete them, and widening the role (shared with the VMS card) for this isn't worth it."""
    try:
        table.update_item(Key={"cameraId": camera_id}, UpdateExpression="SET abandonedAt = :t REMOVE registering",
                          ConditionExpression="registering = :tok", ExpressionAttributeValues={":t": now(), ":tok": token})
    except ClientError:
        pass


def _create(env, body_fields):
    """A new camera, its ID claimed: the path, the unit, its codecs, the KVS stream, then the row
    written over the claim. A failure before the row gives up the claim (abandon_claim)."""
    f = body_fields
    table, camera_id, token, mediamtx_path = env.table, f["camera_id"], f["token"], f["mediamtx_path"]
    host, port, user, password, stream_uri = f["host"], f["port"], f["user"], f["password"], f["stream_uri"]

    # 1. MediaMTX path, live via its local API -- no YAML edit, no restart, so the
    #    other already-running cameras are undisturbed.
    try:
        mediamtx_api.add_path(mediamtx_path, {"source": stream_uri, "rtspTransport": "tcp"})
    except (mediamtx_api.MediaMTXError, requests.RequestException) as e:
        abandon_claim(table, camera_id, token)
        return {"error": f"MediaMTX path add failed: {e}"}, 502

    # 2. Local systemd wiring: env file for the kvs-cam@.service template, then
    #    daemon-reload + start (never enable: #81). See provision-camera.sh's own docstring
    #    for why this is a separate validated script rather than this process writing under
    #    /etc directly.
    provision = env.provision(camera_id, True)
    if provision.returncode != 0:
        # DELETE, not POST: MediaMTX answers POST on this route with a 404, so the rollback
        # silently did nothing and left an orphan path behind (FoundAndFixed.md #36).
        mediamtx_api.delete_path(mediamtx_path)
        abandon_claim(table, camera_id, token)
        return {"error": f"provisioning failed: {provision.stderr.strip()}"}, 500

    # 3. Which codecs the camera can deliver and which is the default (codec_caps.py) --
    #    asked before the KVS stream exists, because its MediaType is set at creation.
    #    The new path's video first: the default should come from what MediaMTX receives,
    #    not from the camera's claim (cam-03 claims H264 and sends H.265, FoundAndFixed.md
    #    #69). A camera that can't be asked is recorded conservatively (H.265 not offered)
    #    rather than failing a registration whose path and unit already exist.
    env.wait_for_flowing(mediamtx_path)
    row = {"cameraId": camera_id, "mode": "passthrough", "onvifHost": host, "onvifPort": port,
           "onvifUser": user, "onvifPassword": password, "rtspUrl": stream_uri}
    try:
        codec_fields = codec_caps.probe(row)
    except Exception as e:  # noqa: BLE001 -- see above
        codec_fields = codec_caps.fallback(row, e)

    # 4. The KVS stream itself.
    kv = env.kinesisvideo()
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
        **({"onvifEndpointRef": f["endpoint_ref"]} if f["endpoint_ref"] else {}),
        "hasIrControl": f["has_ir_control"],
        "kvsStreamArn": stream_arn,
        "adapterId": config.THING_NAME,          # the tenant check's key (DEALER-PANEL-PLAN.md D4)
        "audioCapable": bool(f["audio_capable"]),
        "createdAt": now(),
        **({"displayName": f["display_name"]} if f["display_name"] else {}),
    }
    # Written over this registration's own claim -- the whole row, so the claim's marks go. Only
    # while the claim is still this one's: nothing else may write this ID meanwhile.
    try:
        table.put_item(Item=row, ConditionExpression="registering = :tok", ExpressionAttributeValues={":tok": token})
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        camera_control.set_stream(camera_id, False)
        try:
            mediamtx_api.delete_path(mediamtx_path)
        except (mediamtx_api.MediaMTXError, requests.RequestException):
            pass
        return {"error": f"{camera_id}'s claim was lost meanwhile: nothing was kept -- register again"}, 409

    detection = registration_detection(env, camera_id, host, port, user, password)
    live = apply_live_view(table, env.kinesisvideo, camera_id, row)    # registered on the main stream: live view on sub
    registration_ceiling(table, camera_id, row)
    refresh_path_cache()
    return {"cameraId": camera_id, "kvsStreamArn": stream_arn, "updated": False,
            "fps": f["fps_result"], "detectionModes": detection, "liveView": live}, 201


def registration_detection(env, camera_id, host, port, user, password):
    """Record the camera's recording triggers at registration, so both GUIs restrict the
    selector from the first load. Never fails the registration; None when it couldn't ask."""
    try:
        dmap, _, web_url = env.read_detection(host, port, user, password)
    except Exception:  # noqa: BLE001
        return None
    return env.record_detection(camera_id, dmap, web_url=web_url)


def set_camera_fps(client, stream_uri, fps):
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


def registration_fps(host, port, user, password, stream_uri, fps):
    """The registration form's frame rate applied to the camera, or None when none was asked.
    Never fails the registration: the camera's read-back or the reason goes in the response."""
    if fps is None:
        return None
    try:
        got = set_camera_fps(onvif_media2.Media2(host, port, user, password), stream_uri, fps)
        return {"asked": fps, "configured": got["frameRate"], "govLength": got["govLength"]}
    except Exception as e:  # noqa: BLE001
        return {"asked": fps, "error": f"{type(e).__name__}: {e}"}


def ceiling_fields(stream, fps=None):
    """The recorded stream as the cloud page's quality ceiling (WEB-PANEL-PLAN.md D5): which
    stream, its picture size (the camera's claim), and its frame rate when it is one of the two
    this system offers (D5 of VMS-PORT-PLAN.md: no other rate is shown)."""
    rate = fps if fps is not None else stream.get("fps")
    # The camera's read-back is a float (10.0); DynamoDB through boto3 takes no floats at all.
    rate = int(rate) if isinstance(rate, (int, float)) and float(rate).is_integer() else None
    return {"recordStream": stream["name"],
            "recordSize": f'{stream["width"]}x{stream["height"]}',
            "recordFps": rate if rate in onvif_media2.FPS_CHOICES else None}


def record_ceiling(table, camera_id, item, stream, fps=None):
    """Write the ceiling when it differs from the row, field by field, and clear a cloud choice
    that is no longer below it: the only lower step is the sub stream, so a camera that no longer
    records main loses a "sub" choice (WEB-PANEL-PLAN.md D5). Best effort -- the stream already
    is what it is -- so the outcome goes in the response, never an error."""
    want = ceiling_fields(stream, fps)
    sets = {k: v for k, v in want.items() if v is not None and item.get(k) != v}
    removes = [k for k, v in want.items() if v is None and k in item]
    if want["recordStream"] != "main" and "recordQuality" in item:
        removes.append("recordQuality")
    if not sets and not removes:
        return {"ceiling": want, "written": False}
    parts = (["SET " + ", ".join(f"{k} = :{k}" for k in sets)] if sets else []) + \
            (["REMOVE " + ", ".join(removes)] if removes else [])
    try:
        table.update_item(
            Key={"cameraId": camera_id}, UpdateExpression=" ".join(parts),
            **({"ExpressionAttributeValues": {f":{k}": v for k, v in sets.items()}} if sets else {}))
        return {"ceiling": want, "written": True,
                **({"clearedCloudChoice": True} if "recordQuality" in removes else {})}
    except Exception as e:  # noqa: BLE001
        return {"ceiling": want, "written": False, "reason": f"{type(e).__name__}: {e}"}


def registration_ceiling(table, camera_id, item):
    """At registration the recorded stream is the URI the form chose: record its ceiling from the
    camera's own stream list. Never fails the registration."""
    try:
        found = onvif_media2.streams(onvif_media2.for_camera(item))
        stream = next((x for x in found if onvif_media2.same_stream(x["streamUri"], item.get("rtspUrl"))), None)
        return record_ceiling(table, camera_id, item, stream) if stream else {"written": False}
    except Exception as e:  # noqa: BLE001
        return {"written": False, "reason": f"{type(e).__name__}: {e}"}


def apply_live_view(table, kinesisvideo, camera_id, item, found=None):
    """Point the camera's live view at its sub stream, or back at the recorded stream
    (live_view.py), from the camera's own stream list. Never fails the action that called it --
    a registration or a stream switch has already worked; the outcome, or why the live view
    wasn't set up, goes in the response."""
    try:
        if found is None:
            found = onvif_media2.streams(onvif_media2.for_camera(item))
        uri = live_view.live_uri(found, item.get("rtspUrl"))
        return live_view.apply(camera_id, item, uri, table, kinesisvideo())
    except Exception as e:  # noqa: BLE001
        return {"error": f"live view not set up ({type(e).__name__}: {e}) -- "
                         f"re-register the camera to retry"}


def refresh_path_cache():
    """Rewrite sync_mediamtx_paths.py's offline copy of the registry now, through its own
    code, so a MediaMTX start without AWS sets up the paths as of this change rather than as
    of the last start. Best effort: the next online MediaMTX start rewrites it anyway."""
    try:
        import sync_mediamtx_paths
        sync_mediamtx_paths.load_cameras()
    except Exception as e:  # noqa: BLE001
        print(f"path cache not refreshed: {type(e).__name__}: {e}", flush=True)


def all_names(table):
    """{cameraId: displayName} of this Pi's rows (D12), unregistered ones included: they come back
    with Re-register, and must not come back sharing a name. Another adapter's cameras are left out:
    their names are another customer's, and a clash must not show their camera IDs here."""
    names, kw = {}, {"ProjectionExpression": "cameraId, displayName, adapterId, releasedFrom"}
    while True:
        page = table.scan(**kw)
        names.update({i["cameraId"]: i.get("displayName") for i in page["Items"] if camera_control.is_mine(i)})
        if "LastEvaluatedKey" not in page:
            return names
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def check_display_name(raw, camera_id, names):
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


# --- collaborators the admin GUI and the agent share (Phase 7.3) ------------------------------------

PROVISION_SH = str(Path(__file__).resolve().parent / "bin" / "provision-camera.sh")
WIRE_WAIT_SEC = 15       # registration: how long to wait for the new path's video before probing
DEFAULT_FPS = 10         # what a camera registered from the web page gets, as the admin form's default


def provision(camera_id, start, run=subprocess.run):
    """provision-camera.sh through sudo (the LAUNCH.md A8 rule): the kvs-cam@ instance."""
    args = ["sudo", PROVISION_SH, camera_id, camera_control.mediamtx_path_name(camera_id)]
    if not start:
        args.append("no-start")
    return run(args, capture_output=True, text=True)


def wait_for_flowing(path, budget=WIRE_WAIT_SEC, clock=time):
    """True once `path` has video actually flowing, False after `budget` seconds."""
    deadline = clock.monotonic() + budget
    while clock.monotonic() < deadline:
        if mediamtx_api.path_video_codec(path) and mediamtx_api.path_is_flowing(path):
            return True
        clock.sleep(1)
    return False


def read_detection(host, port, user, password):
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


def record_detection(table, camera_id, dmap, item=None, web_url=None):
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
        table.update_item(Key={"cameraId": camera_id}, UpdateExpression=f"SET {sets}",
                          ExpressionAttributeValues=values)
    except Exception:  # noqa: BLE001
        pass
    return modes


def foreign_error(item):
    """Another adapter's camera, or one released from this one (D12, D15): not this Pi's to change."""
    owner = item.get("adapterId") or (f"released from {item['releasedFrom']}" if item.get("releasedFrom") else "none")
    return {"error": f"'{item['cameraId']}' isn't this adapter's camera ({owner})"}, 409


def unregister(table, camera_id, run=subprocess.run):
    """Hide a camera from both GUIs and stop everything that acts on it, keeping what it has.

    The row stays: the registry is shared with VMS, so rows are updated field by field. So do
    the KVS stream, the clips and every setting, which is what lets Re-register (Discover, then
    the button on the matching device) bring the camera back as it was. Three things change:
      1. unregisteredAt is set, the mark every reader skips (camera_control.is_unregistered);
      2. the producer is stopped and disabled: it costs nothing and doesn't start at boot;
      3. the MediaMTX paths are deleted (recording and live view), so the Pi stops pulling.
    The registry goes first: with AWS unreachable nothing has changed yet, and the cloud page
    couldn't learn of it anyway. Returns (the answer, its HTTP status).

    ONVIF cameras only, because Re-register finds a camera by discovery, which can't see the
    USB camera. Unregistering here hides the camera in VMS's GUIs as well (D4).
    """
    item = table.get_item(Key={"cameraId": camera_id}).get("Item")
    if not item:
        return {"error": "unknown camera"}, 404
    if item.get("mode") != "passthrough" or not item.get("onvifHost"):
        return {"error": f"only ONVIF cameras can be unregistered: Re-register finds "
                         f"them again by discovery, which can't see '{camera_id}'"}, 409
    if not camera_control.is_mine(item):
        return foreign_error(item)
    if camera_control.is_unregistered(item):
        return {"cameraId": camera_id, "unregisteredAt": item["unregisteredAt"]}, 200

    stamp = now()
    table.update_item(
        Key={"cameraId": camera_id},
        UpdateExpression="SET unregisteredAt = :t, updatedAt = :t",
        ExpressionAttributeValues={":t": stamp},
    )
    camera_control.set_stream(camera_id, False)
    camera_control.set_boot_start(camera_id, False)
    run(["sudo", "systemctl", "stop", camera_control.live_unit_name(camera_id)], check=False, capture_output=True)
    result = {"cameraId": camera_id, "unregisteredAt": stamp}
    try:
        # The live view's path too (live_view.py). Its registry field stays, like everything
        # else, until Re-register sets the live view up again.
        mediamtx_api.delete_path(camera_control.mediamtx_path_name(camera_id))
        mediamtx_api.delete_path(camera_control.live_path_name(camera_id))
    except (mediamtx_api.MediaMTXError, requests.RequestException) as e:
        result["warning"] = f"MediaMTX path not removed ({e}); it goes at the next MediaMTX restart"
    refresh_path_cache()
    return result, 200


# --- the web page's registration, through the agent (D16) ---------------------------------------------

def scope_value(scopes, kind):
    """The value of an ONVIF scope such as onvif://www.onvif.org/name/IPC-1 (kind "name"), or None."""
    for scope in scopes or []:
        m = re.match(rf"onvif://www\.onvif\.org/{kind}/(.+)$", str(scope))
        if m:
            return m.group(1).replace("%20", " ").replace("_", " ")
    return None


def body_from_discovery(request, details):
    """The registration the admin form would send, for a camera the web page picked (D16): its
    largest stream, 10 fps where its encoder offers it (else 15, else its own rate), and the IR
    and microphone facts the scan found. `request`: endpointRef, xaddr, user, password, displayName."""
    host, port = onvif_discovery.xaddr_host_port(request["xaddr"])
    streams = details.get("streams") or []
    main = max(streams, key=lambda x: (x.get("width") or 0) * (x.get("height") or 0), default=None)
    uri = main["uri"] if main else details.get("stream_uri")
    choices = (main or {}).get("fpsChoices") or []
    fps = next((f for f in (DEFAULT_FPS, 15) if f in choices and f in onvif_media2.FPS_CHOICES), None)
    audio = (main or {}).get("audio") if main else details.get("audio")
    body = {"host": host, "port": port, "user": request["user"], "password": request.get("password") or "",
            "streamUri": uri, "hasIrControl": bool(details.get("has_ir_control")),
            "endpointRef": request.get("endpointRef") or None, "audioCapable": bool(audio)}
    if fps is not None:
        body["fps"] = fps
    if request.get("displayName"):
        body["displayName"] = request["displayName"]
    return body


def mine_by_endpoint(table, endpoint_ref):
    """This Pi's row for a device (by its WS-Discovery identity), unregistered ones included -- or
    None. Adding a camera this Pi has unregistered brings it back as it was (Re-register)."""
    if not endpoint_ref:
        return None
    rows, kw = [], {}
    while True:
        page = table.scan(**kw)
        rows += [r for r in page["Items"] if r.get("onvifEndpointRef") == endpoint_ref]
        if "LastEvaluatedKey" not in page:
            break
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    mine = [r for r in rows if camera_control.is_mine(r)]
    return mine[0] if mine else None
