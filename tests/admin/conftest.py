"""Fixtures for the admin GUI's tests (PYTEST.md, Phase 3); the shared rows, cameras and helpers
are in tests/gui_world.py.

`gui(...)` returns the Flask test client and handles on every fake, so a test can set up a
camera, call a route, and check exactly what reached the registry, MediaMTX and systemd:

    g = gui(camera=media2("192.0.2.13"))
    status, body = g.post("/api/cameras/cam-03/start")
    assert g.system.calls == [["sudo", "systemctl", "start", "kvs-cam@cam03.service"]]
"""
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import camera_control
import live_view
import mediamtx_api
import onvif_discovery
import onvif_media2
from fakes import FakeClock, FakeMediaMTX, ModuleShim
from gui_world import PRODUCERS, ROWS, Unreachable, admin, no_detection


@pytest.fixture(autouse=True)
def _no_trigger_tests_left():
    admin._trigger_tests.clear()
    yield
    admin._trigger_tests.clear()


@pytest.fixture
def gui(monkeypatch, registry, fake_run):
    """gui(rows=ROWS, producers=PRODUCERS, active=(), mtx=None, camera=None, detection=None,
    rtsp=None, run=None) -> handles:

      client, get(url), post(url, body), delete(url)    the Flask test client; get/post/delete
                                                        return (status, JSON body)
      table     the `cameras` FakeTable; table.session.stub("kinesisvideo") queues KVS answers
      mtx       FakeMediaMTX (the given one, or an empty one)
      system    the FakeRun behind app, camera_control and live_view: every systemctl/sudo/
                ffprobe call, in order. Units of `producers` are "loaded", of `active` "active";
                `run` adds answers (e.g. a failing provision-camera.sh)
      clock     FakeClock behind app's and mediamtx_api's time: waits cost no real time
      camera    what onvif_media2.for_camera()/Media2() return (default: unreachable)

    `detection` replaces app._read_detection (default: the camera can't be asked); `rtsp`
    answers onvif_discovery's RTSP DESCRIBE by user (default: the stream plays without login).
    """
    def install(rows=ROWS, producers=PRODUCERS, active=(), mtx=None, camera=None, detection=None,
                rtsp=None, run=None):
        table = registry(rows, admin)
        channels = Path(camera_control.CHANNELS_DIR)
        channels.mkdir(parents=True, exist_ok=True)
        for cid in producers:              # what provision-camera.sh leaves for a kvs-cam@ camera
            if cid not in admin.LEGACY_UNITS:
                (channels / f"{camera_control.mediamtx_path_name(cid)}.env").write_text("")
        answers = {("systemctl", "show", "-p", "LoadState", "--value", camera_control.unit_name(c)):
                   "loaded\n" for c in producers}
        answers[("systemctl", "is-active")] = ("inactive\n", 3)      # as systemctl answers a stopped unit
        answers.update({("systemctl", "is-active", camera_control.unit_name(c)): "active\n"
                        for c in active})
        answers.update(run or {})
        system = fake_run((admin, camera_control, live_view), answers)

        mtx = mtx if mtx is not None else FakeMediaMTX()
        monkeypatch.setattr(mediamtx_api, "requests", mtx)
        clock = FakeClock()
        shim = ModuleShim(time, monotonic=clock.monotonic, sleep=clock.sleep)
        monkeypatch.setattr(admin, "time", shim)
        monkeypatch.setattr(mediamtx_api, "time", shim)

        cam = camera if camera is not None else Unreachable()
        monkeypatch.setattr(onvif_media2, "for_camera", lambda item: cam)
        monkeypatch.setattr(onvif_media2, "Media2", lambda *a, **k: cam)
        monkeypatch.setattr(admin, "_read_detection", detection or no_detection)
        monkeypatch.setattr(onvif_discovery, "_describe",
                            lambda uri, user, password, timeout=3: (rtsp or (lambda u: "v=0\r\n"))(user))

        client = admin.app.test_client()

        def call(method, url, body=None):
            resp = getattr(client, method)(url, json=body)
            return resp.status_code, resp.get_json()

        return SimpleNamespace(
            client=client, table=table, mtx=mtx, system=system, clock=clock, camera=cam,
            get=lambda url: call("get", url), post=lambda url, body=None: call("post", url, body),
            delete=lambda url: call("delete", url))
    return install
