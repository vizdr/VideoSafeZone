import json, os, re, subprocess, threading, time
from datetime import datetime, timezone

from awscrt import mqtt
from awsiot import mqtt_connection_builder

import activation
import aws_state
import config
import camera_control
import camera_registration
import onvif_discovery
from aws_device_creds import get_session

# $VMS_HOME if set, otherwise the repo root (this file lives in adapter/).
VMS_HOME = os.environ.get("VMS_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

THING   = config.THING_NAME
CMD_T   = f"{config.TOPIC_PREFIX}/cmd"
STATE_T = f"{config.TOPIC_PREFIX}/state"
CONFIG_T = f"{config.TOPIC_PREFIX}/config"   # retained: whether the adapter is activated (D15)
# Adding cameras from the web page (DEALER-PANEL-PLAN.md D16): what a scan found, and how a camera
# request is going. IoT rules store both for the page, with the Thing name from the topic -- the
# Thing policy keeps each Pi to its own topics, so no Pi can answer for another.
DISCOVERY_T    = f"{config.TOPIC_PREFIX}/discovery"
REGISTRATION_T = f"{config.TOPIC_PREFIX}/registration"
REQUESTS_TABLE = "camera-requests"     # the Lambda adapter-cameras' requests, password included
DISCOVERY_SEC  = 5
REQUEST_ID_RE  = re.compile(r"^[0-9a-f-]{8,64}$")

# Camera existence/capabilities come from the "cameras" DynamoDB registry, not a
# hardcoded set/dict -- the same registry the local ONVIF admin GUI (adapter/onvif-admin)
# writes to when a camera is discovered and registered, and the same one the cloud
# Lambdas already read. A camera added through the GUI works over this MQTT control path
# immediately, with no code edit here.
#
# A fresh session is fetched per message rather than cached at module scope: the IoT
# role-alias credentials this borrows are only valid for 3600s (see aws_device_creds.py),
# and this process is a long-running daemon -- caching them once at import time would
# work fine in every manual test and then start failing with ExpiredTokenException on
# every command after an hour of real uptime. The extra round trip is cheap next to how
# infrequently this fires (a human pressing a button), so there's no reason to build a
# refreshing-credential cache for it.
def get_cameras_table():
    return get_session().resource("dynamodb").Table("cameras")

# --- Live view, on demand (VMS-PORT-PLAN.md D2/D3) -----------------------------------------
#
# "Live" means "watch", independent of Start ("record to the cloud"): the cloud page's Live
# control sends `live`, which starts kvs-live@<path>.service -- the camera's sub stream (or, with
# none, its recorded stream) pushed to its own KVS stream "<camera>-live". The page re-sends
# `live` every 2 minutes while the player is visible and `live-stop` when it closes. The
# guarantee is the deadline, not live-stop: each `live` writes now + LIVE_DEADLINE_SEC to
# LIVE_DIR/<path>, and the producer stops itself, exit 0, once that time has passed
# (stream-channel.sh LIVE_OF, producer-lib.sh). So a closed laptop or a lost live-stop costs at
# most LIVE_DEADLINE_SEC of live ingest -- the cost rule (guide §1.2) for a unit that runs
# without anyone pressing Stop. Not RuntimeMaxSec=: systemd counts that timeout as a failure,
# and Restart=on-failure would bring the producer straight back.
LIVE_DEADLINE_SEC = 300
LIVE_DIR = f"/run/user/{os.getuid()}/vms-live"


def _sudo_systemctl(action, unit):
    r = subprocess.run(["sudo", "systemctl", action, unit], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"agent: systemctl {action} {unit} failed: {(r.stderr or r.stdout).strip()}", flush=True)
    return r.returncode == 0


def set_live(camera, on):
    """Start (or extend) or end the camera's live view. Returns the deadline (epoch s) or 0."""
    path = camera_control.mediamtx_path_name(camera)
    os.makedirs(LIVE_DIR, exist_ok=True)
    deadline = int(time.time()) + LIVE_DEADLINE_SEC if on else 0
    tmp = os.path.join(LIVE_DIR, f".{path}.tmp")
    with open(tmp, "w") as f:
        f.write(f"{deadline}\n")
    os.replace(tmp, os.path.join(LIVE_DIR, path))
    unit = camera_control.live_unit_name(camera)
    if on:
        _ensure_live_stream(camera)
        _sudo_systemctl("start", unit)      # no-op when already running: the deadline moved
    else:
        _sudo_systemctl("stop", unit)
    return deadline


def _ensure_live_stream(camera):
    """The camera's "-live" KVS stream. live_view.py creates it for a camera recording its main
    stream; a camera with no sub stream gets it here, the first time someone watches it."""
    name = camera_control.live_stream_name(camera)
    kv = get_session().client("kinesisvideo")
    try:
        kv.describe_stream(StreamName=name)
    except kv.exceptions.ResourceNotFoundException:
        # 1 h: KVS serves HLS only from a stream that keeps data, and nothing else reads it.
        kv.create_stream(StreamName=name, DataRetentionInHours=1, MediaType="video/h264")
        print(f"agent: created KVS stream {name}", flush=True)


def stop_all_live():
    """At startup: no live producer outlives the agent that started it unattended."""
    r = subprocess.run(["systemctl", "list-units", "kvs-live@*", "--plain", "--no-legend",
                        "--state=active,activating"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        unit = line.split()[0] if line.split() else ""
        if unit.startswith("kvs-live@"):
            _sudo_systemctl("stop", unit)


def on_message(topic, payload, **kwargs):
    msg = json.loads(payload)
    cmd = msg.get("action")
    if cmd in ("discover", "register"):
        # About the adapter, not one camera; seconds to minutes of work, so not in MQTT's callback,
        # where Start and Stop presses would wait behind it.
        threading.Thread(target=adapter_command, args=(msg,), daemon=True).start()
        return
    if cmd in ("unregister", "reregister") and not msg.get("camera"):
        return                                 # never the cam-01 default below
    camera = msg.get("camera", "cam-01")   # older clients/messages omit this -- default cam-01

    cam_item = get_cameras_table().get_item(Key={"cameraId": camera}).get("Item")
    if not cam_item:
        return
    # An unregistered camera is hidden from both GUIs, but an open page or a direct API call can
    # still name it, and the MQTT session is shared with VMS: refuse everything but Stop.
    if camera_control.is_unregistered(cam_item) and cmd not in ("stop", "live-stop", "reregister"):
        print(f"agent: ignoring {cmd!r} for unregistered {camera}", flush=True)
        return
    # Another adapter's camera, or one released from this one (D12, D15): not this Pi's to start,
    # light or point anywhere. Stop stays possible -- its producer may still run here.
    if not camera_control.is_mine(cam_item) and cmd not in ("stop", "live-stop"):
        print(f"agent: ignoring {cmd!r} for {camera}: not this adapter's camera", flush=True)
        return

    if cmd == "start" and not camera_control.has_producer(camera):
        # Registered from the VMS card: no unit here, and systemctl would report the missing
        # unit as merely "inactive" (#37's trap). Say so instead of pretending to start.
        print(f"agent: no producer for {camera} on this Pi -- Re-register it in the admin GUI",
              flush=True)
        conn.publish(topic=STATE_T,
                     payload=json.dumps({"camera": camera, "streaming": False,
                                         "error": "no producer on this Pi: Re-register it"}),
                     qos=mqtt.QoS.AT_LEAST_ONCE)
        return

    if cmd in ("live", "live-stop"):
        try:
            deadline = set_live(camera, cmd == "live")
            state = {"camera": camera, "live": cmd == "live", "liveUntil": deadline}
        except Exception as e:  # noqa: BLE001 -- report, don't kill the agent
            state = {"camera": camera, "live": False, "liveError": f"{type(e).__name__}: {e}"}
        conn.publish(topic=STATE_T, payload=json.dumps(state), qos=mqtt.QoS.AT_LEAST_ONCE)
    elif cmd in ("start", "stop"):
        camera_control.set_stream(camera, cmd == "start")
        conn.publish(topic=STATE_T,
                     payload=json.dumps({"camera": camera, "streaming": cmd == "start"}),
                     qos=mqtt.QoS.AT_LEAST_ONCE)
    elif cmd == "unregister":
        threading.Thread(target=unregister_camera, args=(camera,), daemon=True).start()
    elif cmd == "reregister":
        threading.Thread(target=reregister_camera, args=(camera,), daemon=True).start()
    elif cmd == "ir" and cam_item.get("hasIrControl"):
        mode = msg.get("mode")
        try:
            camera_control.set_ir_mode(cam_item, mode)
            conn.publish(topic=STATE_T,
                         payload=json.dumps({"camera": camera, "irMode": mode}),
                         qos=mqtt.QoS.AT_LEAST_ONCE)
        except Exception as e:
            conn.publish(topic=STATE_T,
                         payload=json.dumps({"camera": camera, "irError": str(e)}),
                         qos=mqtt.QoS.AT_LEAST_ONCE)

# --- Adding and removing cameras from the web page (DEALER-PANEL-PLAN.md D16) ---------------------
#
# The browser can't reach the customer's LAN, and WS-Discovery works only on it: the page asks this
# agent, over its own outbound MQTT session. `discover` scans and publishes what it found; `register`
# carries only a request's ID -- the password waits in the camera-requests table, read here and
# never sent over MQTT -- and registers the camera exactly as the admin GUI does
# (camera_registration); `unregister` is the admin GUI's Unregister.

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _publish(topic, payload):
    conn.publish(topic=topic, payload=json.dumps(payload), qos=mqtt.QoS.AT_LEAST_ONCE)


def agent_env():
    """camera_registration's collaborators, as the admin GUI's (app._registration_env)."""
    table = get_cameras_table()
    return camera_registration.Env(
        table=table, kinesisvideo=lambda: get_session().client("kinesisvideo"),
        provision=camera_registration.provision, wait_for_flowing=camera_registration.wait_for_flowing,
        read_detection=camera_registration.read_detection,
        record_detection=lambda camera_id, dmap, item=None, web_url=None: camera_registration.record_detection(
            table, camera_id, dmap, item=item, web_url=web_url))


def discover_devices(table):
    """What WS-Discovery finds on this LAN, each device marked against the registry: added here (its
    camera ID), removed here (unregistered: adding it brings it back), another adapter's, or new."""
    rows = camera_registration.all_rows(table, "cameraId, adapterId, onvifEndpointRef, onvifHost, onvifPort, "
                                               "unregisteredAt, releasedFrom, displayName")
    devices = []
    for svc in onvif_discovery.scan(timeout=DISCOVERY_SEC):
        xaddrs, epr = svc.getXAddrs(), svc.getEPR()
        scopes = [x.getValue() if hasattr(x, "getValue") else str(x) for x in svc.getScopes()]
        host, port = onvif_discovery.xaddr_host_port(xaddrs[0]) if xaddrs else (None, None)
        # By identity or at this Pi's camera's address (FoundAndFixed.md #94); another adapter's by
        # identity only -- its LAN's addresses aren't this one's.
        here = camera_registration.match_device(rows, epr, host, port)
        elsewhere = any(epr and r.get("onvifEndpointRef") == epr and not camera_control.is_mine(r) for r in rows)
        state = ("removed" if camera_control.is_unregistered(here) else "added") if here else \
            ("elsewhere" if elsewhere else None)
        device = {"endpointRef": epr, "xaddr": xaddrs[0] if xaddrs else None, "host": host, "port": port,
                  "name": camera_registration.scope_value(scopes, "name"),
                  "hardware": camera_registration.scope_value(scopes, "hardware"), "state": state}
        if here:
            device.update(cameraId=here["cameraId"], displayName=here.get("displayName"))
        devices.append(device)
    return devices


def run_discovery(request_id):
    try:
        out = {"requestId": request_id, "at": _now(), "devices": discover_devices(get_cameras_table())}
    except Exception as e:  # noqa: BLE001 -- the page is waiting: tell it
        out = {"requestId": request_id, "at": _now(), "devices": [], "error": f"{type(e).__name__}: {e}"}
    _publish(DISCOVERY_T, out)


def run_registration(request_id):
    """One camera request from the page. The first progress message replaces the request in its table
    (the IoT rule writes the whole item), so the password leaves it as soon as it was read here."""
    def report(status, **fields):
        _publish(REGISTRATION_T, {"requestId": request_id, "status": status, "at": _now(), **fields})

    try:
        req = get_session().resource("dynamodb").Table(REQUESTS_TABLE).get_item(
            Key={"requestId": request_id}).get("Item")
    except Exception as e:  # noqa: BLE001
        return report("failed", error=f"the request couldn't be read ({type(e).__name__})")
    if not req or req.get("thing") != THING or not req.get("xaddr") or not req.get("user"):
        return report("failed", error="no such request for this adapter")
    report("registering")
    try:
        details = onvif_discovery.enrich_sync(req["xaddr"], req["user"], req.get("password") or "")
    except Exception as e:  # noqa: BLE001
        return report("failed", error=f"the camera didn't answer with that login ({type(e).__name__})")
    body = camera_registration.body_from_discovery(req, details)
    if not body["streamUri"]:
        return report("failed", error="the camera reports no video stream")
    table = get_cameras_table()
    existing = camera_registration.mine_for_device(table, req.get("endpointRef"), body["host"], body["port"])
    if existing and not camera_control.is_unregistered(existing):
        return report("failed", error=f"this camera is added already, as {existing['cameraId']}",
                      cameraId=existing["cameraId"])
    if existing:
        body["cameraId"] = existing["cameraId"]     # removed here before: Re-register brings it back
    try:
        payload, status = camera_registration.register(body, agent_env())
    except Exception as e:  # noqa: BLE001
        return report("failed", error=f"{type(e).__name__}: {e}")
    if status in (200, 201):
        report("done", cameraId=payload["cameraId"], reactivated=bool(payload.get("reactivated")))
    else:
        report("failed", error=payload.get("error", f"HTTP {status}"))


def adapter_command(msg):
    request_id = msg.get("requestId")
    if not isinstance(request_id, str) or not REQUEST_ID_RE.match(request_id):
        print(f"agent: {msg.get('action')} without a valid requestId: ignored", flush=True)
        return
    print(f"agent: {msg['action']} ({request_id})", flush=True)
    (run_discovery if msg["action"] == "discover" else run_registration)(request_id)


def unregister_camera(camera):
    payload, status = camera_registration.unregister(get_cameras_table(), camera)
    _publish(STATE_T, {"camera": camera, "unregistered": status == 200,
                       **({"error": payload["error"]} if "error" in payload else {})})


def reregister_camera(camera):
    """Re-register from the web page: the camera back from what its row holds (the USB camera too)."""
    try:
        payload, status = camera_registration.reregister(agent_env(), camera)
    except Exception as e:  # noqa: BLE001 -- report, don't kill the agent
        payload, status = {"error": f"{type(e).__name__}: {e}"}, 500
    _publish(STATE_T, {"camera": camera, "reregistered": status in (200, 201) and "error" not in payload,
                       **({"error": payload["error"]} if "error" in payload else {})})


# --- AWS reachability, published for the outage buffer (adapter/outage_buffer.py) ------
#
# This connection is the only MQTT session the adapter is permitted (the IoT policy grants
# iot:Connect on one client id), so this process is the only place the SDK's
# interrupted/resumed callbacks can be observed. It publishes them to a file rather than
# acting on them: recording during an outage is a separate unit's job, and wedging that
# logic in here would put Start/Stop control behind it.
#
# The callbacks set the flag; a heartbeat thread rewrites the file regardless. Without the
# heartbeat, this process dying would leave a file that says "online" forever -- see
# aws_state.py.
_aws = {"online": True, "since": datetime.now(timezone.utc).isoformat(), "seq": 0}
_aws_lock = threading.Lock()


def _publish_aws_state():
    with _aws_lock:
        aws_state.write_state(_aws["online"], _aws["since"], _aws["seq"])


def _set_aws_online(online: bool, why: str):
    with _aws_lock:
        if _aws["online"] == online:
            return
        _aws["online"] = online
        _aws["since"] = datetime.now(timezone.utc).isoformat()
        _aws["seq"] += 1
    print(f"agent: AWS {'RESUMED' if online else 'INTERRUPTED'} ({why})", flush=True)
    _publish_aws_state()


def on_connection_interrupted(connection, error, **kwargs):
    # Fires on a keepalive miss, a TLS failure, throttling, or another client taking this
    # client id. It is a proxy for AWS reachability and not proof about KVS specifically,
    # which is why outage_buffer.py corroborates it with its own probe.
    _set_aws_online(False, str(error))


def on_connection_resumed(connection, return_code, session_present, **kwargs):
    _set_aws_online(True, f"rc={return_code}")
    # A session AWS IoT has discarded -- offline longer than its expiry, an hour by default --
    # comes back with no subscriptions, and awscrt doesn't renew them: cmd and config messages
    # would stop arriving until the agent restarted (FoundAndFixed.md #91).
    if return_code == mqtt.ConnectReturnCode.ACCEPTED and not session_present:
        print("agent: the session didn't persist: subscribing again", flush=True)
        connection.resubscribe_existing_topics()


def on_config(topic, payload, **kwargs):
    """The retained config message (Phase 6.3): whether this adapter is activated, for the admin
    GUI (activation.py). A message that isn't one is logged and ignored. Not activated -- released
    or revoked (D15) -- also stops what pushes for cameras that aren't this adapter's any more."""
    try:
        state = activation.save(payload)
    except ValueError as e:
        print(f"agent: config message ignored: {e}", flush=True)
        return
    print(f"agent: the cloud says this adapter is {'activated' if state['activated'] else 'not activated'}",
          flush=True)
    if not state["activated"]:
        threading.Thread(target=stop_released_producers, daemon=True).start()


def stop_released_producers():
    """After a release (D15) the adapter's camera rows lose adapterId, so no customer reaches them --
    and nobody would press Stop: their producers would push to KVS, unattended, until someone found
    them (the cost rule, guide §1.2). Every producer and live view this Pi has for a camera that
    isn't its own is stopped. Stopping is always allowed (D12)."""
    try:
        table, rows, kw = get_cameras_table(), [], {}
        while True:
            page = table.scan(**kw)
            rows += page["Items"]
            if "LastEvaluatedKey" not in page:
                break
            kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    except Exception as e:  # noqa: BLE001 -- AWS unreachable: the next config message tries again
        print(f"agent: couldn't read the registry to stop released cameras: {e}", flush=True)
        return
    for row in rows:
        camera = row["cameraId"]
        if camera_control.is_mine(row) or not camera_control.has_producer(camera):
            continue
        print(f"agent: {camera} isn't this adapter's any more: stopping its producer", flush=True)
        camera_control.set_stream(camera, False)
        try:
            set_live(camera, False)
        except Exception as e:  # noqa: BLE001
            print(f"agent: live view of {camera} not stopped: {e}", flush=True)


def _heartbeat_loop():
    while True:
        try:
            _publish_aws_state()
        except Exception as e:
            print(f"agent: heartbeat write failed: {e}", flush=True)
        time.sleep(aws_state.HEARTBEAT_SEC)


# The MQTT connection, made by main(); the handlers above publish through it.
conn = None


def main():
    # Connecting happens here, not at import, so the handlers can be tested without an MQTT
    # session (PYTEST.md, Phase 4). Run as a script -- kvs-agent.service -- it does exactly what
    # this file always did at the top level.
    global conn
    conn = mqtt_connection_builder.mtls_from_path(
        endpoint=config.IOT_DATA_ENDPOINT,
        port=443,                       # ALPN x-amzn-mqtt-ca -- traverses HTTPS-only firewalls
        cert_filepath=os.path.join(VMS_HOME, "certs", "adapter.cert.pem"),
        pri_key_filepath=os.path.join(VMS_HOME, "certs", "adapter.private.key"),
        ca_filepath=os.path.join(VMS_HOME, "certs", "AmazonRootCA1.pem"),
        client_id=THING,
        keep_alive_secs=30,
        clean_session=False,
        will=mqtt.Will(topic=STATE_T,
                       qos=mqtt.QoS.AT_LEAST_ONCE,
                       payload=json.dumps({"online": False}).encode(),
                       retain=False),
        on_connection_interrupted=on_connection_interrupted,
        on_connection_resumed=on_connection_resumed,
    )
    stop_all_live()
    conn.connect().result()
    _publish_aws_state()
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    conn.subscribe(topic=CMD_T, qos=mqtt.QoS.AT_LEAST_ONCE, callback=on_message)[0].result()
    conn.subscribe(topic=CONFIG_T, qos=mqtt.QoS.AT_LEAST_ONCE, callback=on_config)[0].result()
    conn.publish(topic=STATE_T, payload=json.dumps({"online": True}),
                 qos=mqtt.QoS.AT_LEAST_ONCE, retain=False)[0].result()
    threading.Event().wait()


if __name__ == "__main__":
    main()
