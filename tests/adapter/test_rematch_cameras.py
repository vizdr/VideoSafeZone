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
    [(_, path, body)] = table.route.calls                              # through the registry route (10.2b)
    assert (path, body["expect"], body["missing"]) == ("cameras/cam-02", {"onvifHost": "192.0.2.12"}, ["onvifEndpointRef"])


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


# --- adopting unstamped rows (DEALER-PANEL-PLAN.md D4, D12) ----------------------------------------

@pytest.fixture
def adopting(monkeypatch):
    import config
    monkeypatch.setattr(config, "ADOPT_UNSTAMPED_ROWS", True)


def _five(registry):
    return registry([
        {"cameraId": "cam-01", "mode": "transcode"},                          # USB: published, not configured
        _cam("cam-02", "192.0.2.12"),
        _cam("cam-03", "192.0.2.13", adapterId="adapter-07"),                  # another adapter's
        _cam("cam-04", "192.0.2.14", unregisteredAt="2026-11-01T00:00:00Z"),
        _cam("cam-05", "192.0.2.15"),                                          # not served here
    ])


def test_rows_this_pi_serves_get_its_thing_name(registry, adopting, monkeypatch):
    table = _five(registry)
    monkeypatch.setattr(mediamtx_api, "requests", FakeMediaMTX(
        conf={"cam02": {"source": "rtsp://x"}, "cam03": {"source": "rtsp://y"}, "cam04": {"source": "rtsp://z"}},
        state={"cam01": {"ready": True}}))
    assert rc.adopt_unstamped() == ["cam-01", "cam-02"]
    assert table.row("cam-01")["adapterId"] == table.row("cam-02")["adapterId"] == "test-thing"
    assert table.row("cam-03")["adapterId"] == "adapter-07"           # someone else's stays theirs
    assert "adapterId" not in table.row("cam-04") and "adapterId" not in table.row("cam-05")
    assert [c[1] for c in table.route.calls] == ["cameras/cam-01/adopt", "cameras/cam-02/adopt"]   # the route stamps
    assert all("attribute_not_exists(adapterId)" in w[1]["ConditionExpression"] for w in table.writes)


def test_without_the_switch_no_row_is_claimed(registry, monkeypatch):
    import config
    monkeypatch.setattr(config, "ADOPT_UNSTAMPED_ROWS", False)
    table = _five(registry)
    monkeypatch.setattr(mediamtx_api, "requests", FakeMediaMTX(conf={"cam02": {"source": "rtsp://x"}}))
    assert rc.adopt_unstamped() == [] and table.writes == []


def test_a_row_stamped_meanwhile_is_left_to_whoever_did(registry, adopting, monkeypatch):
    table = registry([_cam("cam-02", "192.0.2.12", adapterId="adapter-09")])
    stale = [_cam("cam-02", "192.0.2.12")]                                 # as our scan saw it
    monkeypatch.setattr(table, "scan", lambda **kw: {"Items": stale})
    monkeypatch.setattr(mediamtx_api, "requests", FakeMediaMTX(conf={"cam02": {"source": "rtsp://x"}}))
    assert rc.adopt_unstamped() == []
    assert table.row("cam-02")["adapterId"] == "adapter-09"


def test_a_dry_run_and_a_silent_mediamtx_write_nothing(registry, adopting, monkeypatch):
    table = _five(registry)
    monkeypatch.setattr(mediamtx_api, "requests", FakeMediaMTX(conf={"cam02": {"source": "rtsp://x"}}))
    assert rc.adopt_unstamped(dry_run=True) == ["cam-02"] and table.writes == []
    monkeypatch.setattr(mediamtx_api, "requests", FakeMediaMTX(down=True))
    assert rc.adopt_unstamped() == [] and table.writes == []



def test_ff93_a_row_that_isnt_a_camera_is_never_adopted(registry, adopting, monkeypatch):
    """FoundAndFixed.md #93: a row a mistaken write made -- cam01, codec fields only, no mode --
    was stamped because MediaMTX has a path cam01, and reached a customer's page as a camera."""
    table = registry([{"cameraId": "cam01", "videoCodecActive": "h264"}, {"cameraId": "cam-01", "mode": "transcode"}])
    monkeypatch.setattr(mediamtx_api, "requests", FakeMediaMTX(state={"cam01": {"ready": True}}))
    assert rc.adopt_unstamped() == ["cam-01"]
    assert "adapterId" not in table.row("cam01")


# --- #94: a camera whose announced identity changed ---------------------------------------------------

NEW = "urn:uuid:5dd0ffe6-5a80-4b84-dc66-0000000000c4"      # the old one's MAC part, a new front


def test_ff94_an_identity_that_changed_is_learned_at_the_cameras_own_address(registry):
    table = registry([_cam("cam-04", "192.0.2.14", EPR2)])
    cams = [_cam("cam-04", "192.0.2.14", EPR2)]
    assert rc.rematch(cams, {NEW: ("192.0.2.14", 80)}) == (True, [])
    assert table.row("cam-04")["onvifEndpointRef"] == NEW and cams[0]["onvifEndpointRef"] == NEW
    [(_, _, body)] = table.route.calls
    assert body["expect"] == {"onvifHost": "192.0.2.14", "onvifEndpointRef": EPR2}
    # ... so it is followed when its address changes too
    changed, moved = rc.rematch(cams, {NEW: ("192.0.2.44", 80)})
    assert [c["cameraId"] for c in moved] == ["cam-04"] and table.row("cam-04")["onvifHost"] == "192.0.2.44"


@pytest.mark.parametrize("cams, found", [
    # another ONVIF port at that address: another service, not provably the camera
    ([_cam("cam-04", "192.0.2.14", EPR2)], {NEW: ("192.0.2.14", 8080)}),
    # the identity there is another camera's
    ([_cam("cam-04", "192.0.2.14", EPR2), _cam("cam-03", "192.0.2.13", NEW)], {NEW: ("192.0.2.14", 80)}),
], ids=["another port", "another camera's identity"])
def test_ff94_an_identity_is_never_learned_by_guessing(registry, cams, found):
    table = registry([dict(c) for c in cams])
    assert rc.rematch(cams, found) == (False, [])
    assert table.row("cam-04")["onvifEndpointRef"] == EPR2


def test_ff94_a_camera_still_announcing_its_identity_moves_and_keeps_it(registry):
    # Its own identity answers at a new address, and another device took the old one: a move.
    table = registry([_cam("cam-04", "192.0.2.14", EPR2)])
    changed, moved = rc.rematch([_cam("cam-04", "192.0.2.14", EPR2)],
                                {EPR2: ("192.0.2.40", 80), NEW: ("192.0.2.14", 80)})
    assert [c["cameraId"] for c in moved] == ["cam-04"]
    assert (table.row("cam-04")["onvifHost"], table.row("cam-04")["onvifEndpointRef"]) == ("192.0.2.40", EPR2)


# --- tagging this Pi's KVS streams (DEALER-PANEL-PLAN.md D13, Phase 10.2a) ---------------------------

TAGS = {"adapterId": "test-thing"}


def _expect_tags(table, *names, missing=()):
    kv = table.session.stub("kinesisvideo")
    for name in names:
        if name in missing:
            kv.add_client_error("tag_stream", "ResourceNotFoundException", http_status_code=404,
                                expected_params={"StreamName": name, "Tags": TAGS})
        else:
            kv.add_response("tag_stream", {}, {"StreamName": name, "Tags": TAGS})


def test_every_stream_of_this_pis_cameras_is_tagged_with_its_thing(registry, adopting):
    table = registry([
        {"cameraId": "cam-01", "mode": "transcode"},                            # USB, unstamped: adopted here
        _cam("cam-02", "192.0.2.12", adapterId="test-thing"),
        _cam("cam-04", "192.0.2.14", adapterId="test-thing", unregisteredAt="2026-11-01T00:00:00Z"),
        _cam("cam-03", "192.0.2.13", adapterId="adapter-07"),                    # another adapter's
        _cam("cam-05", "192.0.2.15", releasedFrom="test-thing"),                 # released
        {"cameraId": "cam01", "videoCodecActive": "h264"},                       # not a camera (#93)
    ], page_size=2)
    _expect_tags(table, "cam-01", "cam-01-live", "cam-02", "cam-02-live", "cam-04", "cam-04-live",
                 missing={"cam-01-live", "cam-04-live"})
    assert rc.tag_own_streams(now=1000) == ["cam-01", "cam-02", "cam-02-live", "cam-04"]
    import json
    record = json.loads((sync_mediamtx_paths.STATE_DIR / "tagged-streams.json").read_text())
    assert set(record) == {"cam-01", "cam-01-live", "cam-02", "cam-02-live", "cam-04", "cam-04-live"}  # none missing asked again


def test_a_tagged_stream_is_asked_again_only_after_a_day(registry):
    table = registry([_cam("cam-02", "192.0.2.12", adapterId="test-thing")])
    _expect_tags(table, "cam-02", "cam-02-live")
    rc.tag_own_streams(now=1000)
    assert rc.tag_own_streams(now=1000 + rc.RETAG_AFTER - 1) == []           # nothing queued: a call would fail
    _expect_tags(table, "cam-02", "cam-02-live")
    assert rc.tag_own_streams(now=1000 + rc.RETAG_AFTER) == ["cam-02", "cam-02-live"]


def test_a_refused_tag_is_asked_again_next_run(registry):
    table = registry([_cam("cam-02", "192.0.2.12", adapterId="test-thing")])
    kv = table.session.stub("kinesisvideo")
    kv.add_client_error("tag_stream", "AccessDeniedException", http_status_code=403)
    kv.add_response("tag_stream", {}, {"StreamName": "cam-02-live", "Tags": TAGS})
    assert rc.tag_own_streams(now=1000) == ["cam-02-live"]
    _expect_tags(table, "cam-02")
    assert rc.tag_own_streams(now=1001) == ["cam-02"]


def test_a_dry_run_tags_nothing_and_remembers_nothing(registry):
    registry([_cam("cam-02", "192.0.2.12", adapterId="test-thing")])
    assert rc.tag_own_streams(dry_run=True, now=1000) == ["cam-02", "cam-02-live"]
    assert not (sync_mediamtx_paths.STATE_DIR / "tagged-streams.json").exists()


def test_tagging_reads_this_pis_rows_by_the_index_never_a_scan(registry, monkeypatch):
    # DEALER-PANEL-PLAN.md Phase 10.2c: a Pi that doesn't adopt never scans the table.
    import config
    monkeypatch.setattr(config, "ADOPT_UNSTAMPED_ROWS", False)
    table = registry([_cam("cam-02", "192.0.2.12", adapterId="test-thing"), {"cameraId": "cam-01", "mode": "transcode"}])
    monkeypatch.setattr(table, "scan", lambda **kw: pytest.fail("scanned the whole table"))
    _expect_tags(table, "cam-02", "cam-02-live")
    assert rc.tag_own_streams(now=1000) == ["cam-02", "cam-02-live"]


def test_adoption_once_the_role_cant_scan_adopts_nothing(registry, adopting, monkeypatch):
    # After the narrowing (10.2f) the adopting Pi's scan is refused: nothing is left to adopt by then.
    from botocore.exceptions import ClientError
    table = _five(registry)

    def scan(**kw):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Scan")
    monkeypatch.setattr(table, "scan", scan)
    monkeypatch.setattr(mediamtx_api, "requests", FakeMediaMTX(conf={"cam02": {"source": "rtsp://x"}}))
    assert rc.adopt_unstamped() == [] and table.route.calls == []


def test_new_streams_are_made_tagged_with_this_adapters_thing():
    import camera_control
    assert camera_control.stream_tags() == {"adapterId": "test-thing"}
