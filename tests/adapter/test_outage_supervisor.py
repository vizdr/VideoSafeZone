"""outage_buffer's supervisor: when AWS counts as gone and back, when a producer counts as running,
the reachability probe, and the storage checks (PYTEST.md, Phase 2).

Time, sockets, systemd and the agent's heartbeat are faked; the decisions are the real code.
"""
import os
import socket
from types import SimpleNamespace

import pytest

import aws_state
import outage_buffer as ob
from fakes import FakeClock, ModuleShim


# --- Connectivity -----------------------------------------------------------------------------

@pytest.fixture
def signals(monkeypatch):
    """signals(agent, probe) sets what the next poll() sees: the agent's heartbeat (True, False,
    or None for stale) and whether the probe reached AWS."""
    now = {"agent": True, "probe": True}
    monkeypatch.setattr(ob, "aws_state", ModuleShim(aws_state,
                                                    read_state=lambda: {"online": now["agent"]}))
    monkeypatch.setattr(ob, "probe_aws", lambda: now["probe"])

    def set_(agent, probe):
        now["agent"], now["probe"] = agent, probe
    return set_


def _poll(conn, signals, agent, probe):
    signals(agent, probe)
    return conn.poll()


def test_mqtt_interruption_is_an_outage_at_once(signals):
    conn = ob.Connectivity()
    assert _poll(conn, signals, False, True) == (False, "mqtt interrupted")


def test_two_failed_probes_are_an_outage(signals):
    conn = ob.Connectivity()
    assert _poll(conn, signals, True, False) == (True, "")
    assert _poll(conn, signals, True, False) == (False, f"probe failed x{ob.PROBE_FAILS_FOR_OUTAGE}")


def test_a_stale_heartbeat_alone_is_not_an_outage(signals):
    # A crashed agent must read as "unknown"; treating it as an outage would record forever.
    conn = ob.Connectivity()
    for _ in range(10):
        assert _poll(conn, signals, None, True) == (True, "")


def test_stale_heartbeat_and_failing_probe_say_so(signals):
    conn = ob.Connectivity()
    _poll(conn, signals, None, False)
    online, why = _poll(conn, signals, None, False)
    assert not online and "agent heartbeat stale" in why


def _offline(signals):
    conn = ob.Connectivity()
    _poll(conn, signals, False, False)
    assert conn.online is False
    return conn


def test_ff22_one_good_probe_is_not_a_recovery(signals):
    # Recovering on a single probe made the supervisor flap (#22).
    conn = _offline(signals)
    for _ in range(ob.PROBE_OKS_FOR_RECOVERY - 1):
        assert _poll(conn, signals, True, True)[0] is False
    assert _poll(conn, signals, True, True) == (True, f"probe ok x{ob.PROBE_OKS_FOR_RECOVERY}")


def test_ff22_a_failed_probe_restarts_the_count(signals):
    conn = _offline(signals)
    _poll(conn, signals, True, True)
    _poll(conn, signals, True, True)
    _poll(conn, signals, True, False)            # flap
    for _ in range(ob.PROBE_OKS_FOR_RECOVERY - 1):
        assert _poll(conn, signals, True, True)[0] is False
    assert _poll(conn, signals, True, True)[0] is True


def test_recovery_needs_the_agent_to_agree(signals):
    conn = _offline(signals)
    for _ in range(ob.PROBE_OKS_FOR_RECOVERY + 3):
        assert _poll(conn, signals, False, True)[0] is False


def test_recovery_with_a_stale_heartbeat_says_so(signals):
    conn = _offline(signals)
    for _ in range(ob.PROBE_OKS_FOR_RECOVERY - 1):
        _poll(conn, signals, None, True)
    online, why = _poll(conn, signals, None, True)
    assert online and "agent heartbeat stale" in why


# --- ProducerLatch ----------------------------------------------------------------------------

@pytest.fixture
def unit_state(monkeypatch):
    """unit_state("activating") sets what systemctl reports for every producer; returns the
    clock the latch measures its grace period on."""
    clock = FakeClock()
    now = {"state": "inactive"}
    monkeypatch.setattr(ob, "time", ModuleShim(ob.time, monotonic=clock.monotonic))
    monkeypatch.setattr(ob, "_systemd_active_state", lambda unit: now["state"])

    def set_(state):
        now["state"] = state
        return clock
    return set_


@pytest.mark.parametrize("state", ["active", "activating", "reloading", "deactivating"])
def test_a_crash_looping_producer_still_counts_as_running(unit_state, state):
    # A producer in its restart loop reads "activating"; disarming then would drop buffering at
    # exactly the moment it matters.
    unit_state(state)
    assert ob.ProducerLatch().active("cam-02")


def test_a_never_seen_producer_is_not_running(unit_state):
    unit_state("inactive")
    assert not ob.ProducerLatch().active("cam-02")


def test_disarm_only_after_the_grace_period(unit_state):
    latch = ob.ProducerLatch()
    clock = unit_state("active")
    assert latch.active("cam-02")
    unit_state("failed")
    clock.advance(ob.PRODUCER_DISARM_GRACE_SEC - 1)
    assert latch.active("cam-02")
    clock.advance(1)
    assert not latch.active("cam-02")


def test_the_latch_is_per_camera(unit_state):
    latch = ob.ProducerLatch()
    unit_state("active")
    latch.active("cam-02")
    unit_state("inactive")
    assert latch.active("cam-02") and not latch.active("cam-03")


# --- probe_aws ---------------------------------------------------------------------------------

class _Net:
    """getaddrinfo + sockets: `reachable` addresses connect at once, the rest time out, taking
    their full timeout off the fake clock."""

    def __init__(self, clock, addresses, reachable=(), dns_fails=False):
        self.clock, self.addresses, self.reachable = clock, addresses, set(reachable)
        self.dns_fails = dns_fails
        self.attempts = []

    def getaddrinfo(self, host, port, type=None):
        if self.dns_fails:
            raise socket.gaierror("Temporary failure in name resolution")
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port))
                for a in self.addresses]

    def socket(self, family, stype, proto):
        net = self

        class Sock:
            def settimeout(self, t):
                self.timeout = t

            def connect(self, addr):
                net.attempts.append((addr[0], self.timeout))
                if addr[0] in net.reachable:
                    return
                net.clock.advance(self.timeout)
                raise TimeoutError("timed out")

            def close(self):
                pass
        return Sock()


@pytest.fixture
def net(monkeypatch):
    def install(addresses, reachable=(), dns_fails=False):
        clock = FakeClock()
        n = _Net(clock, addresses, reachable, dns_fails)
        monkeypatch.setattr(ob, "socket", ModuleShim(socket, getaddrinfo=n.getaddrinfo, socket=n.socket))
        monkeypatch.setattr(ob, "time", ModuleShim(ob.time, monotonic=clock.monotonic))
        return n
    return install


def test_ff23_the_whole_probe_keeps_to_its_budget(net):
    # create_connection applies its timeout per address: with an A and an AAAA record a 4 s
    # timeout cost 8 s, and outage detection took 86 s of a 120 s pre-roll (#23).
    n = net(["2001:db8::1", "192.0.2.1", "192.0.2.2"])
    start = n.clock.now
    assert ob.probe_aws() is False
    assert n.clock.now - start == ob.PROBE_BUDGET_SEC
    assert len(n.attempts) == ob.PROBE_BUDGET_SEC // ob.PROBE_TIMEOUT


def test_probe_falls_through_to_a_reachable_address(net):
    n = net(["2001:db8::1", "192.0.2.1"], reachable={"192.0.2.1"})
    assert ob.probe_aws() is True
    assert [a for a, _ in n.attempts] == ["2001:db8::1", "192.0.2.1"]


def test_probe_without_dns_is_unreachable(net):
    net([], dns_fails=True)
    assert ob.probe_aws() is False


def test_probe_asks_the_iot_data_endpoint_on_443(net, monkeypatch):
    asked = []
    n = net(["192.0.2.1"], reachable={"192.0.2.1"})
    real = n.getaddrinfo
    monkeypatch.setattr(ob.socket, "getaddrinfo", lambda h, p, type=None: asked.append((h, p)) or real(h, p))
    ob.probe_aws()
    assert asked == [("data.iot.invalid", 443)]       # tests/fixtures/adapter.env


# --- storage ----------------------------------------------------------------------------------

@pytest.fixture
def stick(monkeypatch):
    """stick(mounted, sentinel, free_gb, total_gb): what the buffer stick looks like."""
    def install(mounted=True, sentinel=True, free_gb=50, total_gb=64):
        ob.BUFFER_ROOT.mkdir(parents=True, exist_ok=True)
        if sentinel:
            ob.SENTINEL.write_text("")
        gb = 1024 ** 3
        fake_path = ModuleShim(os.path, ismount=lambda p: mounted and str(p) == str(ob.BUFFER_ROOT))
        stat = SimpleNamespace(f_bavail=int(free_gb * gb / 4096), f_frsize=4096,
                               f_blocks=int(total_gb * gb / 4096))
        monkeypatch.setattr(ob, "os", ModuleShim(os, path=fake_path, statvfs=lambda p: stat))
    return install


def test_no_stick_means_not_ready_rather_than_a_crash():
    # The design #39 relies on: a Pi without the stick idles, disarmed. The sandboxed
    # BUFFER_ROOT doesn't even exist here.
    assert ob.buffer_ready() == (False, "not mounted")


def test_mounted_without_the_sentinel_is_not_ready(stick):
    # A plain directory or the wrong filesystem: recording would land on the SD card.
    stick(sentinel=False)
    ready, why = ob.buffer_ready()
    assert not ready and "sentinel" in why


def test_mounted_with_the_sentinel_is_ready(stick):
    stick()
    assert ob.buffer_ready() == (True, "")


@pytest.mark.parametrize("free_gb, total_gb, ok", [
    (50, 64, True),
    (10, 64, True),       # floor is 15 % of 64 GB = 9.6 GB
    (9, 64, False),
    (5, 8, True),         # floor is the 4 GB minimum on a small stick
    (3, 8, False),
])
def test_disk_floor(stick, free_gb, total_gb, ok):
    stick(free_gb=free_gb, total_gb=total_gb)
    assert ob.disk_ok()[0] is ok


# --- registry ---------------------------------------------------------------------------------

def test_registry_cache_missing_or_corrupt_is_empty():
    assert ob.read_registry_cache() == {}
    ob.REGISTRY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    ob.REGISTRY_CACHE.write_text("{half")
    assert ob.read_registry_cache() == {}


def test_fetch_registry_reads_each_cameras_limit(registry):
    from decimal import Decimal
    registry([{"cameraId": "cam-01"}, {"cameraId": "cam-02", "outageBufferSec": Decimal("300")},
              {"cameraId": "cam-03", "outageBufferSec": None}])
    assert ob._fetch_registry() == {"cam-01": 0, "cam-02": 300, "cam-03": 0}
