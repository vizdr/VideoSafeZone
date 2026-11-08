"""agent.py: the MQTT control agent (PYTEST.md, Phase 4).

Commands arrive from the cloud page through publish-cmd and AWS IoT; the agent starts and stops
producers, runs live view against its deadline, sets IR, and reports on its state topic. It also
writes the AWS reachability the outage buffer reads. The MQTT connection is a recording fake;
systemd, the registry and KVS are the usual fakes.
"""
import json
import os
import time
from types import SimpleNamespace

import pytest
from awscrt import mqtt

import activation
import agent
import aws_state
import camera_control
from fakes import ModuleShim

STATE = "adapter/test-thing/state"            # tests/fixtures/adapter.env: THING_NAME=test-thing
CMD = "adapter/test-thing/cmd"
CONFIG = "adapter/test-thing/config"
NOW = 1_800_000_000
UNREG = "2026-10-25T12:00:00Z"
ROWS = [
    {"cameraId": "cam-01", "mode": "transcode"},
    {"cameraId": "cam-02", "mode": "passthrough", "onvifHost": "192.0.2.12", "hasIrControl": True},
    {"cameraId": "cam-03", "mode": "passthrough", "onvifHost": "192.0.2.13"},
    {"cameraId": "cam-05", "mode": "passthrough", "onvifHost": "192.0.2.15", "unregisteredAt": UNREG},
    {"cameraId": "cam-06", "mode": "passthrough", "onvifHost": "192.0.2.16"},   # from the other card
]


class FakeConnection:
    """What mqtt_connection_builder returns: records what is published, connected, subscribed."""

    def __init__(self, **kwargs):
        self.kwargs, self.published, self.subscribed, self.connected = kwargs, [], [], False

    def _done(self):
        return SimpleNamespace(result=lambda: None)

    def connect(self):
        self.connected = True
        return self._done()

    def subscribe(self, topic, qos, callback):
        self.subscribed.append((topic, qos, callback))
        return self._done(), 1

    def publish(self, topic, payload, qos, retain=False):
        self.published.append((topic, json.loads(payload), qos, retain))
        return self._done(), 1

    def states(self):
        return [p[1] for p in self.published if p[0] == STATE]


@pytest.fixture
def world(monkeypatch, registry, fake_run):
    """The agent between messages: a connection, the registry, systemd, a fixed clock."""
    def install(rows=ROWS, producers=("cam-01", "cam-02", "cam-03", "cam-05"), run=None):
        table = registry(rows, agent)
        channels = os.path.join(camera_control.CHANNELS_DIR)
        os.makedirs(channels, exist_ok=True)
        for cid in producers:
            if cid not in ("cam-01", "cam-02"):
                open(os.path.join(channels, f"{camera_control.mediamtx_path_name(cid)}.env"), "w").close()
        answers = {("systemctl", "show", "-p", "LoadState", "--value", camera_control.unit_name(c)): "loaded\n"
                   for c in producers}
        answers.update(run or {})
        system = fake_run((agent, camera_control), answers)
        conn = FakeConnection()
        monkeypatch.setattr(agent, "conn", conn)
        monkeypatch.setattr(agent, "time", ModuleShim(time, time=lambda: NOW))
        monkeypatch.setattr(agent, "_aws", {"online": True, "since": "2026-10-28T10:00:00+00:00", "seq": 0})
        return SimpleNamespace(table=table, kv=table.session, system=system, conn=conn)
    return install


def send(**msg):
    agent.on_message(CMD, json.dumps(msg).encode())


def sudo(system):
    return [c for c in system.calls if c[0] == "sudo"]


# --- Start / Stop ------------------------------------------------------------------------------

@pytest.mark.parametrize("camera, unit", [
    ("cam-01", "kvs-cam01.service"),
    ("cam-02", "kvs-cam02.service"),            # once built as kvs-cam-02.service, a no-op (#7)
    ("cam-03", "kvs-cam@cam03.service"),        # the template family (#37)
])
@pytest.mark.parametrize("action", ["start", "stop"])
def test_start_and_stop_reach_the_right_unit(world, camera, unit, action):
    w = world()
    send(action=action, camera=camera)
    assert sudo(w.system) == [["sudo", "systemctl", action, unit]]
    assert w.conn.published == [(STATE, {"camera": camera, "streaming": action == "start"},
                                 mqtt.QoS.AT_LEAST_ONCE, False)]


def test_a_message_without_camera_means_cam01(world):
    w = world()
    send(action="start")
    assert sudo(w.system) == [["sudo", "systemctl", "start", "kvs-cam01.service"]]


def test_start_without_a_producer_here_says_so(world):
    # A camera registered from the VMS card has no unit here; systemctl would only say "inactive".
    w = world()
    send(action="start", camera="cam-06")
    assert sudo(w.system) == []
    assert w.conn.states() == [{"camera": "cam-06", "streaming": False,
                                "error": "no producer on this Pi: Re-register it"}]


@pytest.mark.parametrize("action", ["start", "live", "ir"])
def test_an_unregistered_camera_is_ignored(world, action):
    w = world()
    send(action=action, camera="cam-05", mode="ON")
    assert sudo(w.system) == [] and w.conn.published == []


@pytest.mark.parametrize("action, unit", [("stop", "kvs-cam@cam05.service"), ("live-stop", "kvs-live@cam05.service")])
def test_an_unregistered_camera_can_still_be_stopped(world, action, unit):
    w = world()
    send(action=action, camera="cam-05")
    assert sudo(w.system) == [["sudo", "systemctl", "stop", unit]]


@pytest.mark.parametrize("msg", [{"action": "start", "camera": "cam-09"}, {"action": "reboot", "camera": "cam-02"}])
def test_unknown_cameras_and_actions_do_nothing(world, msg):
    w = world()
    send(**msg)
    assert sudo(w.system) == [] and w.conn.published == []


@pytest.mark.parametrize("owner", [{"adapterId": "adapter-02"}, {"releasedFrom": "test-thing"}])
@pytest.mark.parametrize("action", ["start", "live", "ir"])
def test_another_adapters_camera_is_ignored(world, owner, action):
    """DEALER-PANEL-PLAN.md D12: its producer, live view and IR are another Pi's (or nobody's)."""
    w = world([*ROWS, {"cameraId": "cam-07", "mode": "passthrough", "onvifHost": "192.0.2.17", **owner}],
              producers=("cam-01", "cam-02", "cam-03", "cam-05", "cam-07"))
    send(action=action, camera="cam-07", mode="ON")
    assert sudo(w.system) == [] and w.conn.published == []


def test_a_released_cameras_producer_can_still_be_stopped(world):
    w = world([*ROWS, {"cameraId": "cam-07", "mode": "passthrough", "releasedFrom": "test-thing"}],
              producers=("cam-01", "cam-02", "cam-03", "cam-05", "cam-07"))
    send(action="stop", camera="cam-07")
    assert sudo(w.system) == [["sudo", "systemctl", "stop", "kvs-cam@cam07.service"]]


# --- Live view ---------------------------------------------------------------------------------

def _deadline(camera_path):
    with open(os.path.join(agent.LIVE_DIR, camera_path)) as f:
        return f.read()


def test_live_writes_the_deadline_then_starts_the_live_producer(world):
    # The deadline is the cost guarantee: the producer stops itself once it has passed.
    w = world()
    w.kv.stub("kinesisvideo").add_response(
        "describe_stream", {"StreamInfo": {"StreamName": "cam-03-live"}}, {"StreamName": "cam-03-live"})
    send(action="live", camera="cam-03")
    assert _deadline("cam03") == f"{NOW + agent.LIVE_DEADLINE_SEC}\n"
    assert sudo(w.system) == [["sudo", "systemctl", "start", "kvs-live@cam03.service"]]
    assert w.conn.states() == [{"camera": "cam-03", "live": True, "liveUntil": NOW + 300}]
    assert os.listdir(agent.LIVE_DIR) == ["cam03"]            # written atomically: no .tmp left


def test_the_first_live_view_creates_its_kvs_stream(world):
    # A camera without a sub stream gets "<camera>-live" the first time someone watches.
    w = world()
    kv = w.kv.stub("kinesisvideo")
    kv.add_client_error("describe_stream", "ResourceNotFoundException", http_status_code=404)
    kv.add_response("create_stream", {"StreamARN": "arn:aws:kinesisvideo:eu-central-1:0:stream/cam-02-live/1"},
                    {"StreamName": "cam-02-live", "DataRetentionInHours": 1, "MediaType": "video/h264"})
    send(action="live", camera="cam-02")
    assert w.conn.states()[0]["live"] is True


def test_live_stop_ends_the_deadline_without_asking_kvs(world):
    w = world()
    send(action="live-stop", camera="cam-03")
    assert _deadline("cam03") == "0\n"
    assert sudo(w.system) == [["sudo", "systemctl", "stop", "kvs-live@cam03.service"]]
    assert w.conn.states() == [{"camera": "cam-03", "live": False, "liveUntil": 0}]


def test_a_live_failure_is_reported_not_fatal(world):
    w = world()
    w.kv.stub("kinesisvideo").add_client_error("describe_stream", "AccessDeniedException", http_status_code=403)
    send(action="live", camera="cam-03")
    state = w.conn.states()[0]
    assert state["live"] is False and "AccessDeniedException" in state["liveError"]
    assert sudo(w.system) == []                  # the producer isn't started without its stream


def test_a_failing_systemctl_is_logged(world, capsys):
    w = world(run={("sudo", "systemctl", "stop"): ("", 1, "Unit not allowed")})
    send(action="live-stop", camera="cam-03")
    assert "Unit not allowed" in capsys.readouterr().out


def test_at_startup_every_live_producer_is_stopped(world):
    # No live push outlives the agent that started it.
    w = world(run={("systemctl", "list-units"): "kvs-live@cam03.service loaded active running KVS live\n"
                                                "kvs-live@cam04.service loaded activating start KVS live\n"})
    agent.stop_all_live()
    assert sudo(w.system) == [["sudo", "systemctl", "stop", "kvs-live@cam03.service"],
                              ["sudo", "systemctl", "stop", "kvs-live@cam04.service"]]


# --- IR ---------------------------------------------------------------------------------------

@pytest.fixture
def ir(monkeypatch):
    sent = []

    def set_ir_mode(item, mode):
        if mode not in camera_control.ALLOWED_IR_MODES:
            raise ValueError(f"invalid IR mode '{mode}'")
        sent.append((item["cameraId"], mode))
    monkeypatch.setattr(camera_control, "set_ir_mode", set_ir_mode)
    return sent


def test_ir(world, ir):
    w = world()
    send(action="ir", camera="cam-02", mode="OFF")
    assert ir == [("cam-02", "OFF")] and w.conn.states() == [{"camera": "cam-02", "irMode": "OFF"}]


def test_ir_failure_is_reported(world, ir):
    w = world()
    send(action="ir", camera="cam-02", mode="night")
    assert ir == [] and "invalid IR mode" in w.conn.states()[0]["irError"]


def test_ir_on_a_camera_without_it_does_nothing(world, ir):
    w = world()
    send(action="ir", camera="cam-03", mode="ON")
    assert ir == [] and w.conn.published == []


# --- AWS reachability for the outage buffer ---------------------------------------------------

def test_interrupted_and_resumed_reach_the_heartbeat_file(world):
    world()
    agent.on_connection_interrupted(None, RuntimeError("keepalive timeout"))
    st = aws_state.read_state()
    assert (st["online"], st["seq"]) == (False, 1)
    agent.on_connection_interrupted(None, RuntimeError("again"))      # no change, no new seq
    assert aws_state.read_state()["seq"] == 1
    agent.on_connection_resumed(None, 0, True)
    st = aws_state.read_state()
    assert (st["online"], st["seq"]) == (True, 2)


# --- main(): the connection as it is made ------------------------------------------------------

def test_main_connects_subscribes_and_announces(world, monkeypatch):
    world(run={("systemctl", "list-units"): ""})
    made = {}
    order = []

    def build(**kwargs):
        made["conn"] = FakeConnection(**kwargs)
        return made["conn"]
    monkeypatch.setattr(agent, "mqtt_connection_builder", SimpleNamespace(mtls_from_path=build))
    monkeypatch.setattr(agent, "stop_all_live", lambda: order.append("stop live (before connecting)"))
    monkeypatch.setattr(agent, "threading", ModuleShim(
        agent.threading, Thread=lambda target, daemon: SimpleNamespace(start=lambda: order.append("heartbeat")),
        Event=lambda: SimpleNamespace(wait=lambda: order.append("wait"))))
    agent.main()
    conn = made["conn"]
    kw = conn.kwargs
    assert (kw["port"], kw["client_id"], kw["keep_alive_secs"], kw["clean_session"]) == (443, "test-thing", 30, False)
    # The Last Will is not retained: a retained Will got the CONNECT rejected (#6).
    assert (kw["will"].topic, json.loads(kw["will"].payload), kw["will"].retain) == (STATE, {"online": False}, False)
    assert kw["cert_filepath"].endswith("certs/adapter.cert.pem")
    assert conn.connected and conn.subscribed == [(CMD, mqtt.QoS.AT_LEAST_ONCE, agent.on_message),
                                                  (CONFIG, mqtt.QoS.AT_LEAST_ONCE, agent.on_config)]
    assert conn.published == [(STATE, {"online": True}, mqtt.QoS.AT_LEAST_ONCE, False)]
    assert order == ["stop live (before connecting)", "heartbeat", "wait"]
    assert aws_state.read_state()["online"] is True                   # first heartbeat written
    assert agent.conn is conn


def test_importing_the_agent_connects_nothing():
    # The point of main(): before Phase 4, importing agent.py opened the MQTT session. (The
    # `world` fixture puts conn back to this import-time value after every test.)
    assert agent.conn is None


# --- the activation message (DEALER-PANEL-PLAN.md Phase 6.3) -----------------------------------------

def test_the_retained_config_tells_the_admin_gui_whether_the_adapter_is_activated(capsys, monkeypatch):
    stops = []                       # "not activated" also stops released cameras (tested below)
    monkeypatch.setattr(agent, "threading", ModuleShim(agent.threading, Thread=SyncThread))
    monkeypatch.setattr(agent, "stop_released_producers", lambda: stops.append(1))
    assert activation.registration_refusal().startswith("this adapter hasn't heard from the cloud")
    agent.on_config(CONFIG, b'{"activated": false, "at": "2026-11-07T20:00:00Z"}')
    assert "isn't activated" in activation.registration_refusal() and stops == [1]
    agent.on_config(CONFIG, b'{"activated": true, "at": "2026-11-07T20:05:00Z"}')
    assert activation.registration_refusal() is None
    assert activation.load()["at"] == "2026-11-07T20:05:00Z"
    assert "the cloud says this adapter is activated" in capsys.readouterr().out


@pytest.mark.parametrize("payload", [b"not json", b"[]", b'{"activated": "yes"}', b"{}"])
def test_a_config_message_that_isnt_one_changes_nothing(capsys, payload):
    agent.on_config(CONFIG, b'{"activated": true}')
    agent.on_config(CONFIG, payload)
    assert activation.registration_refusal() is None                    # what was known stands
    assert "config message ignored" in capsys.readouterr().out


class Resumable:
    def __init__(self):
        self.resubscribed = 0

    def resubscribe_existing_topics(self):
        self.resubscribed += 1
        return SimpleNamespace(result=lambda: None), 1


def test_ff91_a_session_aws_discarded_gets_its_subscriptions_back():
    """FoundAndFixed.md #91: offline past the session expiry, the agent came back without its
    subscriptions -- cmd and config messages no longer arrived until it restarted."""
    conn = Resumable()
    agent.on_connection_resumed(conn, mqtt.ConnectReturnCode.ACCEPTED, False)
    assert conn.resubscribed == 1
    agent.on_connection_resumed(conn, mqtt.ConnectReturnCode.ACCEPTED, True)   # kept: nothing to renew
    assert conn.resubscribed == 1


# --- adding and removing cameras from the web page (DEALER-PANEL-PLAN.md Phase 7.3, D16) -------------

import camera_registration  # noqa: E402
import onvif_discovery  # noqa: E402
from fakes import FakeTable  # noqa: E402

DISCOVERY = "adapter/test-thing/discovery"
REGISTRATION = "adapter/test-thing/registration"
REF = {k: f"urn:uuid:00000000-0000-0000-0000-0000000000{k}" for k in ("a2", "b5", "c9", "d0")}


class SyncThread:
    """threading.Thread that runs at start(), so a test sees what the command did."""
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class Found:
    """A device as WS-Discovery returns it."""
    def __init__(self, ref, host, name=None):
        self.ref, self.host, self.name = ref, host, name

    def getXAddrs(self):  # noqa: N802 -- WSDiscovery's names
        return [f"http://{self.host}:80/onvif/device_service"]

    def getEPR(self):  # noqa: N802
        return self.ref

    def getScopes(self):  # noqa: N802
        return ([f"onvif://www.onvif.org/name/{self.name}"] if self.name else []) + ["onvif://www.onvif.org/hardware/IPC_X"]


CAMERA_ROWS = [
    {"cameraId": "cam-02", "mode": "passthrough", "onvifHost": "192.0.2.12", "onvifEndpointRef": REF["a2"], "displayName": "Door"},
    {"cameraId": "cam-05", "mode": "passthrough", "onvifHost": "192.0.2.15", "onvifEndpointRef": REF["b5"], "unregisteredAt": UNREG},
    {"cameraId": "cam-09", "mode": "passthrough", "onvifHost": "192.0.2.19", "onvifEndpointRef": REF["c9"], "adapterId": "adapter-99"},
]
DETAILS = {"host": "192.0.2.20", "port": 80, "stream_uri": "rtsp://192.0.2.20:554/profile0", "has_ir_control": True,
           "audio": None, "streams": [
               {"name": "sub", "uri": "rtsp://192.0.2.20:554/sub", "width": 640, "height": 360, "fpsChoices": [10, 15], "audio": None},
               {"name": "main", "uri": "rtsp://192.0.2.20:554/main", "width": 2560, "height": 1440, "fpsChoices": [15, 20],
                "audio": {"encoding": "G711"}}]}


@pytest.fixture
def web(world, monkeypatch):
    """The agent with the registry above, a camera-requests table, and commands run synchronously."""
    def install(requests=(), found=(), details=DETAILS):
        w = world(rows=CAMERA_ROWS, producers=("cam-02",))
        w.requests = FakeTable(requests, hash_key="requestId")
        w.table.session.tables["camera-requests"] = w.requests
        monkeypatch.setattr(agent, "threading", ModuleShim(agent.threading, Thread=SyncThread))
        monkeypatch.setattr(onvif_discovery, "scan", lambda timeout=4: list(found))
        w.enriched = []

        def enrich(xaddr, user, password):
            w.enriched.append((xaddr, user, password))
            if isinstance(details, Exception):
                raise details
            return details
        monkeypatch.setattr(onvif_discovery, "enrich_sync", enrich)
        w.registered = []

        def register(body, env):
            w.registered.append((body, env))
            return w.answer
        w.answer = ({"cameraId": "cam-10", "kvsStreamArn": "arn"}, 201)
        monkeypatch.setattr(camera_registration, "register", register)
        w.sent = lambda topic: [p[1] for p in w.conn.published if p[0] == topic]
        return w
    return install


def test_discovery_publishes_each_device_marked_against_the_registry(web):
    w = web(found=[Found(REF["a2"], "192.0.2.12", "Door_cam"), Found(REF["b5"], "192.0.2.15"),
                   Found(REF["c9"], "192.0.2.19"), Found(REF["d0"], "192.0.2.20", "New%20one")])
    agent.on_message(CMD, json.dumps({"action": "discover", "requestId": "1234abcd"}).encode())
    [out] = w.sent(DISCOVERY)
    assert out["requestId"] == "1234abcd" and out["at"].endswith("Z") and "error" not in out
    by_ref = {d["endpointRef"]: d for d in out["devices"]}
    assert by_ref[REF["a2"]] | {} == {"endpointRef": REF["a2"], "xaddr": "http://192.0.2.12:80/onvif/device_service",
                                      "host": "192.0.2.12", "port": 80, "name": "Door cam", "hardware": "IPC X",
                                      "state": "added", "cameraId": "cam-02", "displayName": "Door"}
    assert (by_ref[REF["b5"]]["state"], by_ref[REF["b5"]]["cameraId"]) == ("removed", "cam-05")
    assert by_ref[REF["c9"]]["state"] == "elsewhere" and "cameraId" not in by_ref[REF["c9"]]   # another customer's ID stays theirs
    assert (by_ref[REF["d0"]]["state"], by_ref[REF["d0"]]["name"]) == (None, "New one")


def test_a_failed_scan_is_reported_to_the_waiting_page(web, monkeypatch):
    w = web()
    monkeypatch.setattr(onvif_discovery, "scan", lambda timeout=4: (_ for _ in ()).throw(OSError("no multicast")))
    agent.on_message(CMD, json.dumps({"action": "discover", "requestId": "1234abcd"}).encode())
    assert w.sent(DISCOVERY)[0]["error"] == "OSError: no multicast"


REQUEST = {"requestId": "5678efab-0000-4000-8000-000000000001", "thing": "test-thing", "xaddr": "http://192.0.2.20:80/onvif/device_service",
           "endpointRef": REF["d0"], "user": "admin", "password": "s3cret-pw", "displayName": "Yard"}


def test_a_request_is_registered_as_the_admin_gui_would_and_the_password_never_travels(web):
    w = web(requests=[REQUEST])
    agent.on_message(CMD, json.dumps({"action": "register", "requestId": "5678efab-0000-4000-8000-000000000001"}).encode())
    assert [m["status"] for m in w.sent(REGISTRATION)] == ["registering", "done"]
    assert w.sent(REGISTRATION)[1]["cameraId"] == "cam-10"
    assert w.enriched == [(REQUEST["xaddr"], "admin", "s3cret-pw")]
    [(body, env)] = w.registered
    assert body == {"host": "192.0.2.20", "port": 80, "user": "admin", "password": "s3cret-pw",
                    "streamUri": "rtsp://192.0.2.20:554/main", "hasIrControl": True, "endpointRef": REF["d0"],
                    "audioCapable": True, "fps": 15, "displayName": "Yard"}          # the largest stream; 10 isn't offered
    assert env.table is w.table and env.provision is camera_registration.provision
    assert "s3cret-pw" not in json.dumps(w.conn.published)                         # in no MQTT message


@pytest.mark.parametrize("request_row, error", [
    ({**REQUEST, "thing": "adapter-99"}, "no such request for this adapter"),
    ({**REQUEST, "requestId": "other"}, "no such request for this adapter"),
])
def test_a_request_that_isnt_this_adapters_is_refused_untouched(web, request_row, error):
    w = web(requests=[request_row])
    agent.on_message(CMD, json.dumps({"action": "register", "requestId": "5678efab-0000-4000-8000-000000000001"}).encode())
    assert w.sent(REGISTRATION) == [{"requestId": "5678efab-0000-4000-8000-000000000001", "status": "failed", "at": w.sent(REGISTRATION)[0]["at"],
                                     "error": error}]
    assert w.enriched == [] and w.registered == []


def test_a_camera_removed_here_comes_back_with_its_id_and_an_added_one_is_refused(web):
    w = web(requests=[{**REQUEST, "endpointRef": REF["b5"]}])
    agent.on_message(CMD, json.dumps({"action": "register", "requestId": "5678efab-0000-4000-8000-000000000001"}).encode())
    assert w.registered[0][0]["cameraId"] == "cam-05"                              # Re-register: as it was
    w = web(requests=[{**REQUEST, "endpointRef": REF["a2"]}])
    agent.on_message(CMD, json.dumps({"action": "register", "requestId": "5678efab-0000-4000-8000-000000000001"}).encode())
    assert w.sent(REGISTRATION)[-1]["error"] == "this camera is added already, as cam-02" and w.registered == []


def test_a_wrong_login_or_a_refused_registration_is_reported(web):
    w = web(requests=[REQUEST], details=RuntimeError("401"))
    agent.on_message(CMD, json.dumps({"action": "register", "requestId": "5678efab-0000-4000-8000-000000000001"}).encode())
    assert w.sent(REGISTRATION)[-1]["error"] == "the camera didn't answer with that login (RuntimeError)"
    w = web(requests=[REQUEST])
    w.answer = ({"error": "this adapter isn't activated: activate it first"}, 409)
    agent.on_message(CMD, json.dumps({"action": "register", "requestId": "5678efab-0000-4000-8000-000000000001"}).encode())
    assert w.sent(REGISTRATION)[-1] | {"at": None} == {"requestId": "5678efab-0000-4000-8000-000000000001", "status": "failed", "at": None,
                                                      "error": "this adapter isn't activated: activate it first"}


@pytest.mark.parametrize("msg", [{"action": "discover"}, {"action": "register", "requestId": "../etc"}])
def test_a_command_without_a_valid_request_id_is_ignored(web, msg):
    w = web(requests=[REQUEST])
    agent.on_message(CMD, json.dumps(msg).encode())
    assert w.conn.published == []


def test_unregister_from_the_page_is_the_admin_guis_and_only_for_this_adapters_cameras(web, monkeypatch):
    w = web()
    done = []
    monkeypatch.setattr(camera_registration, "unregister",
                        lambda table, camera, run=None: done.append((table, camera)) or ({"cameraId": camera}, 200))
    agent.on_message(CMD, json.dumps({"action": "unregister", "camera": "cam-02"}).encode())
    agent.on_message(CMD, json.dumps({"action": "unregister", "camera": "cam-09"}).encode())    # another adapter's
    agent.on_message(CMD, json.dumps({"action": "unregister"}).encode())                       # never cam-01 by default
    assert done == [(w.table, "cam-02")]
    assert w.conn.states() == [{"camera": "cam-02", "unregistered": True}]


def test_the_registration_body_follows_what_the_camera_offers():
    req = {"xaddr": "http://192.0.2.30:8000/onvif/device_service", "user": "u", "password": "", "endpointRef": None}
    no_media2 = {"stream_uri": "rtsp://192.0.2.30/0", "has_ir_control": False, "audio": {"encoding": "AAC"}, "streams": []}
    assert camera_registration.body_from_discovery(req, no_media2) == {
        "host": "192.0.2.30", "port": 8000, "user": "u", "password": "", "streamUri": "rtsp://192.0.2.30/0",
        "hasIrControl": False, "endpointRef": None, "audioCapable": True}                # its own rate, no name
    ten = {**DETAILS, "streams": [{**DETAILS["streams"][1], "fpsChoices": [10, 15, 25]}]}
    assert camera_registration.body_from_discovery(req, ten)["fps"] == 10


def test_scopes_give_a_devices_name_and_hardware():
    scopes = ["onvif://www.onvif.org/type/video_encoder", "onvif://www.onvif.org/name/Front%20Gate",
              "onvif://www.onvif.org/hardware/DS-2CD"]
    assert camera_registration.scope_value(scopes, "name") == "Front Gate"
    assert camera_registration.scope_value(scopes, "hardware") == "DS-2CD"
    assert camera_registration.scope_value([], "name") is None


def test_once_the_adapter_isnt_activated_released_cameras_stop_pushing(world, monkeypatch, capsys):
    """After a release (D15) nobody with access would press Stop: the cost rule (guide §1.2) has the
    agent stop every producer here for a camera that isn't this adapter's any more."""
    released = {"cameraId": "cam-07", "mode": "passthrough", "onvifHost": "192.0.2.17", "releasedFrom": "test-thing"}
    w = world(rows=ROWS[:3] + [released], producers=("cam-01", "cam-02", "cam-07"))
    monkeypatch.setattr(agent, "threading", ModuleShim(agent.threading, Thread=SyncThread))
    agent.on_config(CONFIG, b'{"activated": true}')
    assert [c for c in w.system.calls if "stop" in c] == []
    agent.on_config(CONFIG, b'{"activated": false}')
    stops = [c for c in w.system.calls if c[:3] == ["sudo", "systemctl", "stop"]]
    assert stops == [["sudo", "systemctl", "stop", "kvs-cam@cam07.service"],
                     ["sudo", "systemctl", "stop", camera_control.live_unit_name("cam-07")]]   # cam-01..03 are this Pi's
    assert "cam-07 isn't this adapter's any more" in capsys.readouterr().out


def test_re_register_from_the_page_is_for_this_adapters_unregistered_cameras(web, monkeypatch):
    """The user, 2026-11-08: an unregistered camera stays listed, and Re-register brings it back --
    the one command, beside Stop, an unregistered camera takes."""
    w = web()
    asked = []
    monkeypatch.setattr(camera_registration, "reregister",
                        lambda env, camera, run=None: asked.append((env.table, camera)) or ({"cameraId": camera, "reactivated": True}, 200))
    agent.on_message(CMD, json.dumps({"action": "reregister", "camera": "cam-05"}).encode())   # unregistered here
    agent.on_message(CMD, json.dumps({"action": "reregister", "camera": "cam-09"}).encode())   # another adapter's
    agent.on_message(CMD, json.dumps({"action": "reregister"}).encode())                        # never cam-01 by default
    agent.on_message(CMD, json.dumps({"action": "start", "camera": "cam-05"}).encode())         # still refused
    assert asked == [(w.table, "cam-05")]
    assert w.conn.states() == [{"camera": "cam-05", "reregistered": True}]
