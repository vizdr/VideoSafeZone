"""rematch_cameras: following an ONVIF camera to a new IP address (PYTEST.md, Phase 2).

A camera's identity is its WS-Discovery endpoint reference, not its address. The rules: learn the
identity of rows that lack it; move a row whose identity answers from a new address; never guess
-- duplicate identities, address clashes and hand-edited URLs are skipped; write the registry
conditionally, so a Re-register in the GUI between read and write is never overwritten.
"""
import sys

import pytest

import mediamtx_api
import onvif_discovery
import rematch_cameras as rc
import sync_mediamtx_paths
from fakes import FakeMediaMTX

EPR2 = "urn:uuid:00000000-0000-0000-0000-0000000000c2"
EPR3 = "urn:uuid:00000000-0000-0000-0000-0000000000c3"


def _cam(camera_id, host, ref=None, url=None, **extra):
    row = {"cameraId": camera_id, "mode": "passthrough", "onvifHost": host, "onvifPort": 80,
           "rtspUrl": url or f"rtsp://admin:pw@{host}:554/main"}
    if ref:
        row["onvifEndpointRef"] = ref
    return {**row, **extra}


def test_learns_the_identity_of_a_row_without_one(registry):
    table = registry([_cam("cam-02", "192.0.2.12")])
    cams = [_cam("cam-02", "192.0.2.12")]
    changed, moved = rc.rematch(cams, {EPR2: ("192.0.2.12", 80)})
    assert (changed, moved) == (True, [])
    assert table.row("cam-02")["onvifEndpointRef"] == EPR2
    assert table.writes[0][1]["ConditionExpression"] == \
        "onvifHost = :expect AND attribute_not_exists(onvifEndpointRef)"


def test_follows_a_camera_to_its_new_address(registry):
    table = registry([_cam("cam-02", "192.0.2.12", EPR2)])
    cams = [_cam("cam-02", "192.0.2.12", EPR2)]
    changed, moved = rc.rematch(cams, {EPR2: ("192.0.2.40", 8080)})
    assert changed and [c["cameraId"] for c in moved] == ["cam-02"]
    row = table.row("cam-02")
    assert (row["onvifHost"], row["onvifPort"], row["rtspUrl"]) == \
        ("192.0.2.40", 8080, "rtsp://admin:pw@192.0.2.40:554/main")
    assert "updatedAt" in row
    assert cams[0]["onvifHost"] == "192.0.2.40"          # the cache follows too


def test_moves_the_live_view_url_with_it(registry):
    sub = "rtsp://admin:pw@192.0.2.13:554/sub"
    table = registry([_cam("cam-03", "192.0.2.13", EPR3, liveRtspUrl=sub)])
    rc.rematch([_cam("cam-03", "192.0.2.13", EPR3, liveRtspUrl=sub)], {EPR3: ("192.0.2.41", 80)})
    assert table.row("cam-03")["liveRtspUrl"] == "rtsp://admin:pw@192.0.2.41:554/sub"


def test_same_address_changes_nothing(registry):
    table = registry([_cam("cam-02", "192.0.2.12", EPR2)])
    assert rc.rematch([_cam("cam-02", "192.0.2.12", EPR2)], {EPR2: ("192.0.2.12", 80)}) == (False, [])
    assert table.writes == []


def test_a_camera_not_found_is_left_alone(registry):
    table = registry([_cam("cam-02", "192.0.2.12", EPR2)])
    assert rc.rematch([_cam("cam-02", "192.0.2.12", EPR2)], {}) == (False, [])
    assert table.writes == []


@pytest.mark.parametrize("cams, found, why", [
    # one identity on two rows: which one moved?
    ([_cam("cam-02", "192.0.2.12", EPR2), _cam("cam-03", "192.0.2.13", EPR2)],
     {EPR2: ("192.0.2.40", 80)}, "identity"),
    # the new address belongs to another registered camera
    ([_cam("cam-02", "192.0.2.12", EPR2), _cam("cam-03", "192.0.2.13", EPR3)],
     {EPR2: ("192.0.2.13", 80)}, "another camera"),
    # rtspUrl was edited by hand to point elsewhere
    ([_cam("cam-02", "192.0.2.12", EPR2, url="rtsp://192.0.2.99/main")],
     {EPR2: ("192.0.2.40", 80)}, "hand-edited"),
    # ... or its live view URL was
    ([_cam("cam-02", "192.0.2.12", EPR2, liveRtspUrl="rtsp://192.0.2.99/sub")],
     {EPR2: ("192.0.2.40", 80)}, "hand-edited"),
])
def test_never_guesses(registry, capsys, cams, found, why):
    table = registry(cams)
    assert rc.rematch(cams, found) == (False, [])
    assert table.writes == []
    assert why in capsys.readouterr().out


@pytest.mark.parametrize("cam", [
    _cam("cam-01", "192.0.2.11", EPR2, mode="transcode"),
    {"cameraId": "cam-04", "mode": "passthrough", "onvifEndpointRef": EPR2},   # no ONVIF host
])
def test_only_onvif_passthrough_cameras_are_followed(registry, cam):
    table = registry([cam])
    assert rc.rematch([cam], {EPR2: ("192.0.2.40", 80)}) == (False, [])
    assert table.writes == []


def test_dry_run_writes_nothing_and_changes_nothing(registry):
    table = registry([_cam("cam-02", "192.0.2.12", EPR2), _cam("cam-03", "192.0.2.13")])
    cams = [_cam("cam-02", "192.0.2.12", EPR2), _cam("cam-03", "192.0.2.13")]
    before = [dict(c) for c in cams]
    assert rc.rematch(cams, {EPR2: ("192.0.2.40", 80), EPR3: ("192.0.2.13", 80)},
                      dry_run=True) == (False, [])
    assert table.writes == [] and cams == before


def test_a_reregister_in_between_is_not_overwritten(registry, capsys):
    # The registry already shows a different address (the GUI re-registered the camera after
    # this run read it): the conditional write is refused, and the row keeps the GUI's value.
    table = registry([_cam("cam-02", "192.0.2.77", EPR2)])
    cams = [_cam("cam-02", "192.0.2.12", EPR2)]            # what this run read
    changed, moved = rc.rematch(cams, {EPR2: ("192.0.2.40", 80)})
    assert table.row("cam-02")["onvifHost"] == "192.0.2.77"
    assert "registry write failed" in capsys.readouterr().out
    # The local MediaMTX path still moves; the registry is retried on the next run.
    assert changed and moved


def test_main_moves_the_registry_cache_and_mediamtx_path(registry, monkeypatch):
    old = "rtsp://admin:pw@192.0.2.12:554/main"
    table = registry([_cam("cam-02", "192.0.2.12", EPR2)])
    mtx = FakeMediaMTX(conf={"cam02": {"source": old, "rtspTransport": "tcp"}})
    monkeypatch.setattr(mediamtx_api, "requests", mtx)
    monkeypatch.setattr(sync_mediamtx_paths, "requests", mtx)
    monkeypatch.setattr(onvif_discovery, "locate", lambda timeout: {EPR2: ("192.0.2.40", 80)})
    monkeypatch.setattr(sys, "argv", ["rematch_cameras.py"])
    assert rc.main() == 0
    new = "rtsp://admin:pw@192.0.2.40:554/main"
    assert table.row("cam-02")["rtspUrl"] == new
    assert mtx.conf["cam02"]["source"] == new
    assert new in sync_mediamtx_paths.CACHE.read_text()
