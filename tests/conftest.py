"""Shared setup for every test -- the safety guards (PYTEST.md, step 3).

The tests run on the live Pi: MediaMTX answers on 127.0.0.1:9997, /etc/adapter/adapter.env names
the real AWS account (shared with VMS), and `sudo systemctl start` can start a producer that is
billed. So before any project module is imported, this file

  1. points config.py at a fake identity (tests/fixtures/adapter.env) and gives boto3 fake
     credentials and no config files, so nothing can sign a request to the real account;
  2. makes HOME, XDG_RUNTIME_DIR and XDG_STATE_HOME a throw-away directory;
  3. blocks outbound network use from Python (requests, urllib3, botocore, WS-Discovery's UDP);
  4. blocks every subprocess except the read-only probes Python's own ctypes runs;

and around every test it

  5. moves the modules' absolute paths (/etc/adapter/channels, /mnt/vms-buffer, ~/.local/state/...)
     into that test's tmp_path, and
  6. fails the test if it tried 3. or 4., even when the code under test swallowed the error.

A test that needs a command or an HTTP answer fakes it with monkeypatch (fake_run below, or a fake
`requests` call), never by loosening these guards.
"""
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# --- 1 + 2: the environment, before anything imports config.py or boto3 -----------------------

_SANDBOX = Path(tempfile.mkdtemp(prefix="vsz-tests-"))

# Environment overrides adapter.env in config.py, so a real value exported in the shell would win
# over the fake file: remove them. VMS_HOME too, so the code finds this checkout from its own
# location, as under systemd, rather than wherever the shell's VMS_HOME points.
for _key in ("THING_NAME", "IOT_ROLE_ALIAS", "IOT_CRED_ENDPOINT", "IOT_DATA_ENDPOINT",
             "EVIDENCE_BUCKET", "VMS_HOME", "STREAM_NAME", "AWS_PROFILE", "AWS_DEFAULT_PROFILE",
             "AWS_DEFAULT_REGION", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE",
             "AWS_CONTAINER_CREDENTIALS_FULL_URI", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"):
    os.environ.pop(_key, None)

os.environ.update({
    "ADAPTER_CONFIG": str(FIXTURES / "adapter.env"),
    "AWS_REGION": "eu-central-1",        # the same value as tests/fixtures/adapter.env
    "BUCKET": "test-bucket",             # the clip Lambdas read it at import
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_CONFIG_FILE": os.devnull,
    "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
    "AWS_EC2_METADATA_DISABLED": "true",
    "HOME": str(_SANDBOX / "home"),
    "XDG_RUNTIME_DIR": str(_SANDBOX / "run"),
    "XDG_STATE_HOME": str(_SANDBOX / "state"),
})
for _d in ("home", "run", "state"):
    (_SANDBOX / _d).mkdir()


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_SANDBOX, ignore_errors=True)


# --- 3 + 4: network and command guards, installed for the whole run --------------------------

class Blocked(RuntimeError):
    """A test reached for the network or a command.

    Deliberately not an OSError: the code under test catches OSError and requests' errors as
    "camera / AWS / MediaMTX unreachable", which would quietly turn a missing fake into a pass.
    """


_attempts = []      # what the current test tried; checked after every test (6.)


def _block(what):
    _attempts.append(what)
    raise Blocked(f"blocked in tests: {what} -- fake it instead (PYTEST.md, step 3)")


_INET = (socket.AF_INET, socket.AF_INET6)
_real_connect, _real_connect_ex = socket.socket.connect, socket.socket.connect_ex
_real_sendto = socket.socket.sendto


def _guarded_connect(self, address):
    if self.family in _INET:
        _block(f"network connect to {address!r}")
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    if self.family in _INET:
        _block(f"network connect to {address!r}")
    return _real_connect_ex(self, address)


def _guarded_sendto(self, *args):
    # UDP needs no connect: WS-Discovery's multicast probe is a bare sendto.
    if self.family in _INET:
        _block(f"network sendto {args[-1]!r}")
    return _real_sendto(self, *args)


def _guarded_getaddrinfo(host, *args, **kwargs):
    _block(f"DNS lookup of {host!r}")


socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex
socket.socket.sendto = _guarded_sendto
socket.getaddrinfo = _guarded_getaddrinfo

# ctypes.util.find_library's probes: read-only, and run by a dependency at import (WS-Discovery's
# ifaddr asks ldconfig). Everything else -- systemctl, sudo, ffmpeg, gst-launch-1.0, the project's
# own scripts -- is blocked. subprocess.run, check_output and asyncio's subprocesses all go
# through subprocess.Popen, so guarding it covers them.
_ALLOWED_COMMANDS = {"ldconfig", "gcc", "cc", "ld", "objdump"}
_RealPopen = subprocess.Popen


class _GuardedPopen(_RealPopen):
    def __init__(self, args, *a, **kw):
        argv = [args] if isinstance(args, (str, bytes, os.PathLike)) else list(args)
        first = os.fsdecode(argv[0]).split()[0] if argv and argv[0] else ""
        if os.path.basename(first) not in _ALLOWED_COMMANDS:
            _block(f"command {[os.fsdecode(x) for x in argv]!r}")
        super().__init__(args, *a, **kw)


subprocess.Popen = _GuardedPopen
os.system = lambda command: _block(f"os.system({command!r})")


# --- 5: absolute paths into tmp_path ------------------------------------------------------------

# Absolute paths the modules compute when imported. Each test gets its own copy under tmp_path, so
# no test reads the Pi's real state (a camera's channel file decides unit_name(); the USB stick
# holds captured outages) or writes over it. A module that isn't imported is skipped: test modules
# import theirs at collection, before any test runs. str stays str, Path stays Path.
_SANDBOXED_PATHS = {
    "camera_control": {"CHANNELS_DIR": "etc/adapter/channels"},
    "agent": {"LIVE_DIR": "run/vms-live"},          # the live view deadlines the producers read
    "aws_state": {"STATE_FILE": "run/vms/aws-state.json"},
    "outage_buffer": {
        "BUFFER_ROOT": "mnt/vms-buffer",
        "SENTINEL": "mnt/vms-buffer/.vms-buffer-ok",
        "LIVE_DIR": "mnt/vms-buffer/live",
        "OUTAGE_DIR": "mnt/vms-buffer/outage",
        "RECORD_PATH": "mnt/vms-buffer/live/%path/%Y-%m-%d_%H-%M-%S-%f",
        "STATE_DIR": "state/vms",
        "REGISTRY_CACHE": "state/vms/cameras-cache.json",
    },
    "outage_uploader": {"BUFFER_ROOT": "mnt/vms-buffer", "OUTAGE_DIR": "mnt/vms-buffer/outage"},
    "sync_mediamtx_paths": {"STATE_DIR": "state/vms-adapter",
                            "CACHE": "state/vms-adapter/cameras-cache.json"},
}


@pytest.fixture(autouse=True)
def _sandbox_paths(tmp_path, monkeypatch):
    import sys
    root = tmp_path / "sandbox"
    for module_name, attrs in _SANDBOXED_PATHS.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for attr, rel in attrs.items():
            if hasattr(module, attr):
                original = getattr(module, attr)
                new = root / rel
                monkeypatch.setattr(module, attr, str(new) if isinstance(original, str) else new)
    return root


# --- 6: fail a test that tried to escape --------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_escape():
    _attempts.clear()
    yield
    if _attempts:
        tried = "\n  ".join(_attempts)
        _attempts.clear()
        pytest.fail(f"the test reached outside its sandbox:\n  {tried}\n"
                    "Fake the call (PYTEST.md, step 3).", pytrace=False)


@pytest.fixture
def expect_blocked():
    """For tests of the guards themselves: the attempts so far, forgiven at the end."""
    yield _attempts
    _attempts.clear()


# --- fakes shared by many tests -----------------------------------------------------------------

class FakeRun:
    """Stands in for subprocess.run in one module: records each argv and answers from a table.

        fake = fake_run(camera_control, {("systemctl", "is-active"): "active\\n"})
        ...
        assert fake.calls == [["systemctl", "is-active", "kvs-cam01.service"]]

    A key is a prefix of the argv; the longest matching key wins; no match answers "" with
    returncode 0. An answer is stdout, or (stdout, returncode), or (stdout, returncode, stderr).
    """

    def __init__(self, answers):
        self.answers = {tuple(k): v for k, v in (answers or {}).items()}
        self.calls = []

    def __call__(self, argv, *args, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        best = max((k for k in self.answers if tuple(argv[:len(k)]) == k), key=len, default=None)
        answer = self.answers.get(best, "")
        returncode, stderr = 0, ""
        if isinstance(answer, tuple):
            answer, returncode, stderr = (answer + ("",))[:3]
        if kwargs.get("check") and returncode:
            raise subprocess.CalledProcessError(returncode, argv, answer, stderr)
        return subprocess.CompletedProcess(argv, returncode, stdout=answer, stderr=stderr)


@pytest.fixture
def fake_run(monkeypatch):
    """fake_run(module, answers) -> FakeRun, replacing `module.subprocess.run` for one test;
    `module` may be a tuple of modules sharing one FakeRun, so `calls` shows their commands in
    order. For modules that import subprocess at module level; one that imports it inside a
    function (outage_buffer._systemd_active_state) is faked by patching that function instead."""
    # The module's own `subprocess` name is replaced, not subprocess.run, so the fake stays out
    # of every other module, pytest's included; CompletedProcess, PIPE... stay real.
    from fakes import ModuleShim

    def install(modules, answers=None):
        fake = FakeRun(answers)
        for module in modules if isinstance(modules, tuple) else (modules,):
            monkeypatch.setattr(module, "subprocess", ModuleShim(subprocess, run=fake))
        return fake
    return install


@pytest.fixture
def registry(monkeypatch):
    """registry(rows, *modules, page_size=None) -> FakeTable: the `cameras` table as
    aws_device_creds.get_session() serves it (PYTEST.md, Phase 2). Covers every module that
    imports get_session inside a function; a module that bound its own name at import
    (event_watcher, app.py) is passed in `modules` and patched too.

    The session is a FakeBoto3, at `table.session`: queue AWS client answers on
    table.session.stub("kinesisvideo"); one left unused fails the test afterwards."""
    import aws_device_creds
    from fakes import FakeBoto3, FakeTable
    sessions = []

    def install(rows=(), *modules, page_size=None):
        table = FakeTable(rows, page_size=page_size)
        table.session = FakeBoto3(cameras=table)
        sessions.append(table.session)
        for module in (aws_device_creds, *modules):
            monkeypatch.setattr(module, "get_session", lambda *a, **k: table.session)
        return table
    yield install
    for session in sessions:
        session.verify()


@pytest.fixture
def aws(monkeypatch):
    """aws(lambda_module, cameras=[...], clips=[...], page_size=None) -> FakeBoto3: the fake AWS a
    Lambda handler runs against (PYTEST.md, Phase 2). Here rather than in tests/cloud, because the
    admin GUI's tests compare its routes with the Lambdas they mirror.

    Replaces the module's `boto3` and its module-level `cameras_table`. Queue client answers with
    fake.stub("s3").add_response(...); a queued answer left unused fails the test afterwards, so
    a call the handler should have made and didn't is caught too.
    """
    made = []

    def install(lam, cameras=(), clips=(), page_size=None):
        from fakes import FakeBoto3, FakeTable
        fake = FakeBoto3(cameras=FakeTable(cameras, page_size=page_size),
                         clips=FakeTable(clips, range_key="startTs", page_size=page_size))
        monkeypatch.setattr(lam, "boto3", fake)
        if hasattr(lam, "cameras_table"):
            monkeypatch.setattr(lam, "cameras_table", fake.tables["cameras"])
        made.append(fake)
        return fake

    yield install
    for fake in made:
        fake.verify()
