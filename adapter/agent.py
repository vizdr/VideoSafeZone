import json, os, subprocess, threading, time
from datetime import datetime, timezone

from awscrt import mqtt
from awsiot import mqtt_connection_builder

import aws_state
import config
import camera_control
from aws_device_creds import get_session

# $VMS_HOME if set, otherwise the repo root (this file lives in adapter/).
VMS_HOME = os.environ.get("VMS_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

THING   = config.THING_NAME
CMD_T   = f"{config.TOPIC_PREFIX}/cmd"
STATE_T = f"{config.TOPIC_PREFIX}/state"

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
    camera = msg.get("camera", "cam-01")   # older clients/messages omit this -- default cam-01

    cam_item = get_cameras_table().get_item(Key={"cameraId": camera}).get("Item")
    if not cam_item:
        return
    # An unregistered camera is hidden from both GUIs, but an open page or a direct API call can
    # still name it, and the MQTT session is shared with VMS: refuse everything but Stop.
    if camera_control.is_unregistered(cam_item) and cmd not in ("stop", "live-stop"):
        print(f"agent: ignoring {cmd!r} for unregistered {camera}", flush=True)
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


def _heartbeat_loop():
    while True:
        try:
            _publish_aws_state()
        except Exception as e:
            print(f"agent: heartbeat write failed: {e}", flush=True)
        time.sleep(aws_state.HEARTBEAT_SEC)


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
conn.publish(topic=STATE_T, payload=json.dumps({"online": True}),
             qos=mqtt.QoS.AT_LEAST_ONCE, retain=False)[0].result()
threading.Event().wait()
