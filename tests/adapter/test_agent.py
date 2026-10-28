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

import agent
import aws_state
import camera_control
from fakes import ModuleShim

STATE = "adapter/test-thing/state"            # tests/fixtures/adapter.env: THING_NAME=test-thing
CMD = "adapter/test-thing/cmd"
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
    assert conn.connected and conn.subscribed == [(CMD, mqtt.QoS.AT_LEAST_ONCE, agent.on_message)]
    assert conn.published == [(STATE, {"online": True}, mqtt.QoS.AT_LEAST_ONCE, False)]
    assert order == ["stop live (before connecting)", "heartbeat", "wait"]
    assert aws_state.read_state()["online"] is True                   # first heartbeat written
    assert agent.conn is conn


def test_importing_the_agent_connects_nothing():
    # The point of main(): before Phase 4, importing agent.py opened the MQTT session. (The
    # `world` fixture puts conn back to this import-time value after every test.)
    assert agent.conn is None
