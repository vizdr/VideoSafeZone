"""mediamtx_api: duration parsing and the reconcile check (PYTEST.md, step 5).

The outage supervisor compares MediaMTX's live config with what it wants every 5 s. MediaMTX
normalises what it reports ("2m" comes back as "2m0s", a disabled cleaner as ""), so a string
comparison re-patched the path forever. These tests pin the parsed comparison.
"""
import pytest

import mediamtx_api as m


@pytest.mark.parametrize("text, seconds", [
    # what this MediaMTX (v1.20.1) reports, read from its API on 2026-10-28
    ("10s", 10), ("1s", 1), ("200ms", 0.2), ("1m0s", 60), ("1h0m0s", 3600), ("1d", 86400),
    # what the code sends, and other spellings Go and MediaMTX accept
    ("30s", 30), ("2m", 120), ("1h", 3600), ("1.5s", 1.5), ("0s", 0), ("0", 0), ("1h30m", 5400),
    ("1d12h", 129600), (".5s", 0.5), ("1.5m", 90), ("-1s", -1), ("+2s", 2),
    ("100ns", 1e-7), ("1us", 1e-6), ("1.5µs", 1.5e-6), ("1.5μs", 1.5e-6),
])
def test_dur_secs(text, seconds):
    assert m._dur_secs(text) == seconds


@pytest.mark.parametrize("text", ["", None])
def test_dur_secs_empty_is_none(text):
    assert m._dur_secs(text) is None


@pytest.mark.parametrize("text", [
    "3w",            # no such unit
    "30",            # a number without a unit: only "0" may go without one
    "s", "-", "ms",  # a unit without a number
    "1h junk", "1 h", "1.2.3s", "abc", "1hh",
])
def test_dur_secs_unreadable_is_none(text):
    assert m._dur_secs(text) is None


def test_ff84_milliseconds_are_not_minutes():
    # Read character by character, "m" + "s" made 500 ms into 500 minutes plus nothing: 30 000 s.
    assert m._dur_secs("500ms") == 0.5
    assert not m._dur_eq("500ms", "8h20m0s")


@pytest.mark.parametrize("a, b, equal", [
    ("2m", "2m0s", True), ("30s", "30s", True), ("1h", "1h0m0s", True), ("30s", "1m", False),
    ("1500ms", "1.5s", True),      # summed in nanoseconds: no float noise between spellings
    ("1d", "24h0m0s", True), ("0.1s", "100ms", True),
])
def test_dur_eq_compares_seconds_not_strings(a, b, equal):
    assert m._dur_eq(a, b) is equal


RECORD_PATH = "/mnt/vms-buffer/live/%path/%Y-%m-%d_%H-%M-%S-%f"


def _armed(**over):
    conf = {"record": True, "recordPath": RECORD_PATH, "recordFormat": "fmp4",
            "recordDeleteAfter": "0s", "recordSegmentDuration": "30s"}
    conf.update(over)
    return conf


@pytest.fixture
def live_conf(monkeypatch):
    """Sets what get_path_conf() answers; None means 'the path has no config of its own'."""
    def answer(conf):
        monkeypatch.setattr(m, "get_path_conf", lambda path: conf)
    return answer


def test_armed_path_matches(live_conf):
    live_conf(_armed())
    assert m.recording_conf_matches("cam02", True, RECORD_PATH)


def test_disabled_cleaner_reported_as_empty_string_still_matches(live_conf):
    # MediaMTX reports recordDeleteAfter "0s" back as "" -- reading that as a mismatch made the
    # reconcile loop patch every 5 s.
    live_conf(_armed(recordDeleteAfter=""))
    assert m.recording_conf_matches("cam02", True, RECORD_PATH)


def test_normalised_segment_duration_still_matches(live_conf):
    live_conf(_armed(recordSegmentDuration="2m0s"))
    assert m.recording_conf_matches("cam02", True, RECORD_PATH, segment_duration="2m")


@pytest.mark.parametrize("over", [
    {"recordFormat": "mpegts"},                 # can't carry G.711
    {"recordDeleteAfter": "1h0m0s"},            # MediaMTX's cleaner must stay off
    {"recordDeleteAfter": "1d"},                # ... including the path defaults' own value
    {"recordPath": "/somewhere/else/%path"},
    {"recordSegmentDuration": "1m0s"},
])
def test_any_other_difference_is_a_mismatch(live_conf, over):
    live_conf(_armed(**over))
    assert not m.recording_conf_matches("cam02", True, RECORD_PATH)


def test_record_flag_mismatch(live_conf):
    live_conf(_armed(record=False))
    assert not m.recording_conf_matches("cam02", True, RECORD_PATH)
    live_conf(_armed(record=True))
    assert not m.recording_conf_matches("cam02", False)


def test_path_without_config_already_matches_off(live_conf):
    # Answering False here made the supervisor create a path just to disarm it, resurrecting the
    # path of a camera unregistered a moment earlier.
    live_conf(None)
    assert m.recording_conf_matches("cam05", False)
    assert not m.recording_conf_matches("cam05", True, RECORD_PATH)


# --- Phase 2: the HTTP calls, against a fake MediaMTX ---------------------------------------------

from fakes import FakeMediaMTX, ready_path   # noqa: E402


@pytest.fixture
def mtx(monkeypatch):
    def install(**kw):
        fake = FakeMediaMTX(**kw)
        monkeypatch.setattr(m, "requests", fake)
        return fake
    return install


def test_get_path_conf(mtx):
    mtx(conf={"cam02": {"source": "rtsp://192.0.2.12/s"}})
    assert m.get_path_conf("cam02") == {"source": "rtsp://192.0.2.12/s"}
    assert m.get_path_conf("cam09") is None        # served by all_others: no config of its own


def test_ff36_delete_uses_delete_not_post(mtx):
    # POST on this route is MediaMTX's "404 page not found": a failed registration's rollback
    # once left an orphan path behind every time (#36).
    fake = mtx(conf={"cam05": {"source": "rtsp://192.0.2.15/s"}})
    m.delete_path("cam05")
    assert fake.writes() == [("DELETE", "/v3/config/paths/delete/cam05", None)]
    assert "cam05" not in fake.conf


def test_deleting_an_absent_path_is_not_an_error(mtx):
    mtx()
    m.delete_path("cam05")


def test_write_errors_raise(mtx):
    mtx()
    with pytest.raises(m.MediaMTXError):
        m.patch_path("cam05", {"record": True})        # no such path: 404


def test_ensure_path_conf_adds_a_publisher_path_when_absent(mtx):
    # cam01 is published into all_others and has no entry; a plain patch would 404.
    fake = mtx()
    m.ensure_path_conf("cam01", {"record": False})
    assert fake.writes() == [("POST", "/v3/config/paths/add/cam01",
                              {"source": "publisher", "record": False})]


def test_ensure_path_conf_patches_an_existing_path(mtx):
    fake = mtx(conf={"cam02": {"source": "rtsp://192.0.2.12/s"}})
    m.ensure_path_conf("cam02", {"record": False})
    assert fake.writes() == [("PATCH", "/v3/config/paths/patch/cam02", {"record": False})]
    assert fake.conf["cam02"]["source"] == "rtsp://192.0.2.12/s"      # a merge, not a replace


def test_set_recording_on_owns_retention(mtx):
    fake = mtx(conf={"cam02": {}})
    m.set_recording("cam02", True, RECORD_PATH)
    assert fake.conf["cam02"] == {"record": True, "recordPath": RECORD_PATH, "recordFormat": "fmp4",
                                  "recordSegmentDuration": "30s", "recordPartDuration": "1s",
                                  "recordDeleteAfter": "0s"}


def test_set_recording_off_touches_only_the_flag(mtx):
    fake = mtx(conf={"cam02": {}})
    m.set_recording("cam02", False)
    assert fake.writes()[0][2] == {"record": False}


def test_ff50_flowing_means_bytes_grew_not_ready(mtx):
    # MediaMTX keeps a stalled path "ready", tracks and all (#50).
    mtx(snapshots=[
        [ready_path("cam01", 1000), ready_path("cam02", 5000), ready_path("cam03", 10)],
        [ready_path("cam01", 9000), ready_path("cam02", 5000), ready_path("cam03", 20, ready=False)],
    ])
    assert m.flowing_paths(sample_sec=0) == {"cam01"}


def test_a_reconnected_source_reads_as_not_flowing(mtx):
    # Its counter restarts; the caller asks again rather than trusting one sample.
    mtx(snapshots=[[ready_path("cam02", 90000)], [ready_path("cam02", 400)]])
    assert m.flowing_paths(sample_sec=0) == set()


def test_flowing_paths_with_mediamtx_down_is_empty(mtx):
    mtx(down=True)
    assert m.flowing_paths(sample_sec=0) == set()
    assert m.path_is_flowing("cam01", sample_sec=0) is False


@pytest.mark.parametrize("state, codec", [
    ({"ready": True, "tracks": ["H265", "MPEG-4 Audio"]}, "h265"),
    ({"ready": True, "tracks": [{"codec": "H264"}]}, "h264"),
    ({"ready": True, "tracks": ["G711"]}, None),
    ({"ready": False, "tracks": ["H264"]}, None),           # source offline
])
def test_path_video_codec_is_what_arrives(mtx, state, codec):
    mtx(state={"cam03": state})
    assert m.path_video_codec("cam03") == codec


def test_path_video_codec_with_mediamtx_down(mtx):
    mtx(down=True)
    assert m.path_video_codec("cam03") is None


def test_path_video_track_has_the_picture_size(mtx):
    mtx(state={"cam03": {"ready": True, "tracks2": [
        {"codec": "G711", "codecProps": {"sampleRate": 8000, "muLaw": False}},
        {"codec": "H265", "codecProps": {"width": 2560, "height": 1440}}]}})
    assert m.path_video_track("cam03") == {"codec": "h265", "width": 2560, "height": 1440}


@pytest.mark.parametrize("track, expected", [
    ({"codec": "G711", "codecProps": {"muLaw": True, "sampleRate": 8000, "channelCount": 1}},
     {"codec": "pcmu", "sampleRate": 8000, "channels": 1}),
    ({"codec": "G711", "codecProps": {"muLaw": False, "sampleRate": 8000, "channelCount": 1}},
     {"codec": "pcma", "sampleRate": 8000, "channels": 1}),
    ({"codec": "MPEG-4 Audio", "codecProps": {"sampleRate": 16000, "channelCount": 1}},
     {"codec": "mpeg-4 audio", "sampleRate": 16000, "channels": 1}),
])
def test_ff71_audio_law_comes_from_the_wire(mtx, track, expected):
    # ONVIF says only "G711"; cam-02 switched from A-law to mu-law in its own web page (#71).
    mtx(state={"cam02": {"ready": True, "tracks2": [
        {"codec": "H264", "codecProps": {"width": 1280, "height": 720}}, track]}})
    assert m.path_audio_track("cam02") == expected


def test_no_audio_track(mtx):
    mtx(state={"cam02": {"ready": True, "tracks2": [{"codec": "H264", "codecProps": {}}]}})
    assert m.path_audio_track("cam02") is None
