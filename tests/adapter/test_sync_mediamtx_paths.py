"""sync_mediamtx_paths: re-adding the camera paths after every MediaMTX start (PYTEST.md, Phase 2).

MediaMTX keeps no API changes, so mediamtx.yml has no camera paths and this script puts them back
from the registry, or from a local cache while AWS is unreachable (FoundAndFixed.md #31, #32).
"""
import json
import os
import stat

import pytest

import aws_device_creds
import mediamtx_api
import sync_mediamtx_paths as sp
from fakes import FakeMediaMTX

URL2 = "rtsp://admin:pw@192.0.2.12:554/Streaming/Channels/101"
URL3 = "rtsp://admin:pw@192.0.2.13:554/main"
URL3_SUB = "rtsp://admin:pw@192.0.2.13:554/sub"


@pytest.fixture
def mtx(monkeypatch):
    def install(**kw):
        fake = FakeMediaMTX(**kw)
        monkeypatch.setattr(mediamtx_api, "requests", fake)
        monkeypatch.setattr(sp, "requests", fake)
        return fake
    return install


def _cam(camera_id, url=None, mode="passthrough", **extra):
    return {"cameraId": camera_id, "mode": mode, **({"rtspUrl": url} if url else {}), **extra}


def test_adds_a_missing_path_over_tcp(mtx):
    fake = mtx()
    sp.sync([_cam("cam-02", URL2)])
    assert fake.writes() == [("POST", "/v3/config/paths/add/cam02",
                              {"source": URL2, "rtspTransport": "tcp"})]


def test_leaves_an_up_to_date_path_alone(mtx):
    # Patching `source` reconnects every reader -- a running producer included.
    fake = mtx(conf={"cam02": {"source": URL2, "rtspTransport": "tcp"}})
    sp.sync([_cam("cam-02", URL2)])
    assert fake.writes() == []


def test_patches_a_path_whose_camera_moved(mtx):
    fake = mtx(conf={"cam02": {"source": "rtsp://admin:pw@192.0.2.99/old", "rtspTransport": "tcp"}})
    sp.sync([_cam("cam-02", URL2)])
    assert fake.writes() == [("PATCH", "/v3/config/paths/patch/cam02",
                              {"source": URL2, "rtspTransport": "tcp"})]


def test_never_deletes_a_path(mtx):
    # A path whose camera is gone from the registry stays: removing paths is the GUI's job.
    fake = mtx(conf={"cam07": {"source": "rtsp://192.0.2.17/s", "rtspTransport": "tcp"}})
    sp.sync([_cam("cam-02", URL2)])
    assert "cam07" in fake.conf
    assert all(method != "DELETE" for method, _, _ in fake.calls)


def test_transcode_cameras_publish_their_own_path(mtx):
    fake = mtx()
    sp.sync([_cam("cam-01", mode="transcode")])
    assert fake.calls == []


def test_passthrough_without_rtsp_url_is_skipped_and_said(mtx, capsys):
    fake = mtx()
    sp.sync([_cam("cam-04")])
    assert fake.calls == []
    assert "no rtspUrl" in capsys.readouterr().out


def test_live_view_gets_its_own_path(mtx):
    fake = mtx()
    sp.sync([_cam("cam-03", URL3, liveRtspUrl=URL3_SUB)])
    assert {route: body["source"] for _, route, body in fake.writes()} == {
        "/v3/config/paths/add/cam03": URL3, "/v3/config/paths/add/cam03_live": URL3_SUB}


def test_dry_run_changes_nothing(mtx):
    fake = mtx()
    sp.sync([_cam("cam-02", URL2), _cam("cam-03", URL3)], dry_run=True)
    assert fake.writes() == []


def test_the_log_never_shows_a_password(mtx, capsys):
    mtx()
    sp.sync([_cam("cam-02", "rtsp://admin:s3cret@192.0.2.12/s")])
    assert "s3cret" not in capsys.readouterr().out


# --- the registry and its cache ------------------------------------------------------------------

ROWS = [
    {"cameraId": "cam-01", "mode": "transcode"},
    {"cameraId": "cam-02", "mode": "passthrough", "rtspUrl": URL2, "onvifHost": "192.0.2.12",
     "onvifPort": 80, "onvifUser": "admin", "onvifPassword": "pw", "displayName": "Hall"},
    {"cameraId": "cam-05", "mode": "passthrough", "rtspUrl": "rtsp://192.0.2.15/s",
     "unregisteredAt": "2026-10-25T12:00:00Z"},
]


def test_fetch_keeps_only_what_paths_need(registry):
    from decimal import Decimal
    registry([{**ROWS[1], "onvifPort": Decimal("80")}])
    assert sp.fetch_registry() == [{"cameraId": "cam-02", "mode": "passthrough", "rtspUrl": URL2,
                                    "onvifHost": "192.0.2.12", "onvifPort": 80}]


def test_unregistered_cameras_get_no_path_and_stay_out_of_the_cache(registry):
    registry(ROWS)
    cameras = sp.load_cameras()
    assert [c["cameraId"] for c in cameras] == ["cam-01", "cam-02"]
    assert "cam-05" not in sp.CACHE.read_text()


def test_the_cache_is_private(registry):
    # It holds RTSP URLs with their logins.
    registry(ROWS)
    sp.load_cameras()
    assert stat.S_IMODE(os.stat(sp.CACHE).st_mode) == 0o600
    assert "onvifPassword" not in sp.CACHE.read_text()


def test_registry_unreachable_falls_back_to_the_cache(registry, monkeypatch):
    registry(ROWS)
    sp.load_cameras()

    def unreachable(*a, **k):
        raise OSError("Could not connect to the endpoint URL")
    monkeypatch.setattr(aws_device_creds, "get_session", unreachable)
    assert [c["cameraId"] for c in sp.load_cameras()] == ["cam-01", "cam-02"]


def test_no_registry_and_no_cache_means_no_network_paths(monkeypatch):
    def unreachable(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(aws_device_creds, "get_session", unreachable)
    assert sp.load_cameras() is None


def test_dry_run_leaves_the_cache_alone(registry):
    registry(ROWS)
    sp.load_cameras(dry_run=True)
    assert not sp.CACHE.exists()


def test_cache_records_when_it_was_fetched(registry):
    registry(ROWS)
    sp.load_cameras()
    assert json.loads(sp.CACHE.read_text())["fetchedAt"].endswith("Z")
