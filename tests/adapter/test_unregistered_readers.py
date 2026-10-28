"""Every reader of the `cameras` table that acts on cameras skips an unregistered one
(PYTEST.md, Phase 2).

Unregister is a soft mark, `unregisteredAt`: the row, KVS stream and clips stay so Re-register can
bring the camera back. So every reader has to skip such a row itself (CLAUDE.md, "The camera
registry"). One test per adapter reader; the cloud's list-cameras is in tests/cloud. A new reader
of the table belongs here too.
"""
import event_watcher
import outage_buffer
import sync_mediamtx_paths
import codec_caps
from fakes import FakeTable

UNREGISTERED = "2026-10-25T12:00:00Z"
ROWS = [
    {"cameraId": "cam-02", "mode": "passthrough", "rtspUrl": "rtsp://192.0.2.12/s",
     "onvifHost": "192.0.2.12", "recordingMode": "motion", "outageBufferSec": 300},
    {"cameraId": "cam-05", "mode": "passthrough", "rtspUrl": "rtsp://192.0.2.15/s",
     "onvifHost": "192.0.2.15", "recordingMode": "motion", "outageBufferSec": 300,
     "unregisteredAt": UNREGISTERED},
]


def test_outage_supervisor(registry):
    registry(ROWS)
    assert set(outage_buffer._fetch_registry()) == {"cam-02"}


def test_mediamtx_path_sync(registry):
    registry(ROWS)
    assert [c["cameraId"] for c in sync_mediamtx_paths.fetch_registry()] == ["cam-02"]


def test_event_watcher(registry):
    registry(ROWS, event_watcher)
    assert set(event_watcher.load_cameras()) == {"cam-02"}


def test_event_watcher_also_needs_a_detection_mode_and_a_host(registry):
    registry([{**ROWS[0], "recordingMode": "manual"},
              {**ROWS[0], "cameraId": "cam-03", "onvifHost": None},
              {**ROWS[0], "cameraId": "cam-04", "recordingMode": "human"}], event_watcher)
    assert set(event_watcher.load_cameras()) == {"cam-04"}


def test_codec_caps(monkeypatch):
    table = FakeTable(ROWS)
    monkeypatch.setattr(codec_caps, "probe", lambda item: {k: None for k in codec_caps.FIELDS})
    assert set(codec_caps.refresh(table, log=lambda msg: None)) == {"cam-02"}
