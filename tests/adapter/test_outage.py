"""outage_buffer and outage_uploader: segment times and the pure helpers (PYTEST.md, step 5).

The supervisor's state machines (Connectivity, ProducerLatch) need fakes and are Phase 2.
"""
import os
import time
from datetime import datetime, timezone

import pytest

import outage_buffer as ob
import outage_uploader as ou


@pytest.fixture
def berlin():
    """The Pi's zone (CET/CEST), whatever zone the test machine is in. By hand, not monkeypatch:
    tzset() has to run again after TZ is restored."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Berlin"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def test_ff20_segment_names_are_local_time_not_utc(berlin, tmp_path):
    # MediaMTX writes %Y-%m-%d_%H-%M-%S-%f in the machine's local zone. Read as UTC, every segment
    # sat two hours in the future on a CEST box, the retention cutoff never matched, and the
    # rolling window grew without bound.
    start = ob.segment_start(tmp_path / "2026-09-25_14-30-00-123456.mp4")
    assert start.astimezone(timezone.utc) == datetime(2026, 9, 25, 12, 30, 0, 123456,
                                                      tzinfo=timezone.utc)


def test_ff20_winter_time_too(berlin, tmp_path):
    start = ob.segment_start(tmp_path / "2026-12-01_08-00-00-000000.mp4")
    assert start.astimezone(timezone.utc).hour == 7


def test_unparseable_name_falls_back_to_mtime(tmp_path):
    # Never None for a file that exists: an unparseable name must not become un-prunable.
    f = tmp_path / "renamed-by-hand.mp4"
    f.write_bytes(b"")
    os.utime(f, (1_760_000_000, 1_760_000_000))
    assert ob.segment_start(f).timestamp() == 1_760_000_000


def test_unparseable_missing_file_is_none(tmp_path):
    assert ob.segment_start(tmp_path / "gone.mp4") is None


def test_completed_segments_leave_out_the_open_one(tmp_path):
    names = ["2026-09-25_14-30-00-000000.mp4", "2026-09-25_14-30-30-000000.mp4",
             "2026-09-25_14-31-00-000000.mp4"]
    for n in names + ["notes.txt"]:
        (tmp_path / n).write_bytes(b"")
    assert [p.name for p in ob.completed_segments(tmp_path)] == names[:2]


@pytest.mark.parametrize("files", [[], ["2026-09-25_14-30-00-000000.mp4"]])
def test_completed_segments_none_yet(tmp_path, files):
    for n in files:
        (tmp_path / n).write_bytes(b"")
    assert ob.completed_segments(tmp_path) == []


def test_completed_segments_missing_dir(tmp_path):
    assert ob.completed_segments(tmp_path / "no-such-camera") == []


# --- outage_uploader -----------------------------------------------------------------------------

def _probe(video=("h264", 1280, 720), audio=None, duration="30.0"):
    streams = [{"codec_type": "video", "codec_name": video[0], "width": video[1], "height": video[2]}]
    if audio:
        streams.append({"codec_type": "audio", "codec_name": audio[0], "sample_rate": audio[1],
                        "channels": audio[2]})
    return {"streams": streams, "format": {"duration": duration}}


def test_track_signature_ignores_stream_order():
    p = _probe(audio=("aac", "8000", 1))
    q = {"streams": list(reversed(p["streams"]))}
    assert ou.track_signature(p) == ou.track_signature(q)


@pytest.mark.parametrize("other", [
    _probe(),                                         # audio switched off mid-outage
    _probe(audio=("aac", "16000", 1)),                # another rate
    _probe(video=("hevc", 1280, 720), audio=("aac", "8000", 1)),   # codec switched
    _probe(video=("h264", 1920, 1080), audio=("aac", "8000", 1)),  # size changed
])
def test_track_signature_differs_when_segments_cannot_be_joined(other):
    assert ou.track_signature(_probe(audio=("aac", "8000", 1))) != ou.track_signature(other)


def test_video_codec_and_audio_rate():
    p = _probe(video=("hevc", 1280, 720), audio=("aac", "8000", 1))
    assert ou.video_codec(p) == "hevc"
    assert ou.audio_rate(p) == 8000
    assert ou.audio_rate(_probe()) is None
    assert ou.audio_rate(_probe(audio=("aac", "N/A", 1))) is None


@pytest.mark.parametrize("total, expected", [
    (60, ou.CHUNK_MIN_SEC), (6 * 900, 900), (24 * 3600, ou.CHUNK_MAX_SEC),
])
def test_chunk_length_is_a_sixth_within_bounds(total, expected):
    assert ou.chunk_length(total) == expected


@pytest.mark.parametrize("probe, expected", [
    (_probe(duration="29.97"), 29.97), ({"format": {}}, 0.0), ({}, 0.0),
    ({"format": {"duration": "N/A"}}, 0.0), (None, 0.0),
])
def test_seg_duration(probe, expected):
    assert ou.seg_duration(probe) == expected


# --- Phase 2: grouping segments into clips, with ffprobe faked -----------------------------------

@pytest.fixture
def probes(monkeypatch):
    """probes({name: probe-or-None}) answers ffprobe per file name; None = unreadable."""
    def install(table):
        monkeypatch.setattr(ou, "ffprobe", lambda p: table[p.name])
    return install


def _names(runs):
    return [[p.name for p, _ in run] for run in runs]


def test_one_layout_is_one_run(probes, tmp_path):
    probes({"a": _probe(), "b": _probe(), "c": _probe()})
    assert _names(ou.partition_runs([tmp_path / n for n in "abc"])) == [["a", "b", "c"]]


def test_an_unreadable_segment_splits_the_run_and_is_dropped(probes, tmp_path):
    # A bad segment must never cost the whole outage; the gap becomes a separate clip.
    probes({"a": _probe(), "b": None, "c": _probe()})
    assert _names(ou.partition_runs([tmp_path / n for n in "abc"])) == [["a"], ["c"]]


def test_a_layout_change_starts_a_new_clip(probes, tmp_path):
    # audioEnabled applies at the producer's next start, which can land inside an outage.
    probes({"a": _probe(), "b": _probe(), "c": _probe(audio=("aac", "8000", 1))})
    assert _names(ou.partition_runs([tmp_path / n for n in "abc"])) == [["a", "b"], ["c"]]


@pytest.mark.parametrize("table, expected", [
    ({"a": None, "b": _probe()}, [["b"]]),        # leading bad segment: no empty run
    ({"a": None, "b": None}, []),
])
def test_no_empty_runs(probes, tmp_path, table, expected):
    probes(table)
    assert _names(ou.partition_runs([tmp_path / n for n in table])) == expected


def test_no_segments():
    assert ou.partition_runs([]) == []


def test_ffprobe_failure_reads_as_unreadable(fake_run, tmp_path):
    fake_run(ou, {("ffprobe",): ("", 1)})
    assert ou.ffprobe(tmp_path / "x.mp4") is None


def test_ffprobe_without_streams_reads_as_unreadable(fake_run, tmp_path):
    fake_run(ou, {("ffprobe",): '{"streams": [], "format": {}}'})
    assert ou.ffprobe(tmp_path / "x.mp4") is None
