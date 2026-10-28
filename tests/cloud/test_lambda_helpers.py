"""Pure helpers of the other Lambdas (PYTEST.md, step 5). Handlers are Phase 2."""
import json
from decimal import Decimal

import pytest

from loaders import load_lambda

play_clip = load_lambda("play_clip")
list_cameras = load_lambda("list_cameras")
record_clip = load_lambda("record_clip")


@pytest.mark.parametrize("size, seconds", [
    (0, 900), (None, 900),                           # the 15-minute floor
    (5 * 1024 * 1024, 900 + 51),                     # a 45 s evidence clip
    (100 * 1024 * 600, 1500),                        # +1 s per 100 KB
    (1100 * 1024 * 1024, 12164),                     # a 2 h outage clip (~1.1 GB): ~3.4 h
    (3 * 1024 ** 3, 21600),                          # the 6 h ceiling
])
def test_presign_seconds_outlive_the_viewing(size, seconds):
    assert play_clip.presign_seconds(size) == seconds


def test_a_two_hour_outage_clip_can_be_watched_to_the_end():
    # OUTAGE.md 4.6: outage clips run to 2 h and ~1.1 GB; a flat 300 s URL 403'd mid-playback.
    assert play_clip.presign_seconds(1100 * 1024 * 1024) > 2 * 3600


@pytest.mark.parametrize("item, codec", [
    ({"videoCodec": "h265", "videoCodecDefault": "h264"}, "h265"),   # the admin's choice first
    ({"videoCodecDefault": "h265"}, "h265"),
    ({}, "h264"),                                                     # cameras predating it
])
def test_video_codec(item, codec):
    assert list_cameras.video_codec(item) == codec


def test_ff82_plain_makes_decimals_serialisable():
    # One Decimal anywhere turned the whole camera list into a 500.
    row = {"cameraId": "cam-03", "onvifPort": Decimal("80"), "ratio": Decimal("1.5"),
           "detectionTests": [{"events": Decimal("3")}], "caps": {"fps": [Decimal("10")]},
           "flag": True, "none": None}
    out = list_cameras.plain(row)
    assert out == {"cameraId": "cam-03", "onvifPort": 80, "ratio": 1.5,
                   "detectionTests": [{"events": 3}], "caps": {"fps": [10]}, "flag": True,
                   "none": None}
    assert isinstance(out["onvifPort"], int)
    json.dumps(out)


def test_unregistered_response():
    assert record_clip.unregistered_response("cam-03", {"cameraId": "cam-03"}) is None
    assert record_clip.unregistered_response("cam-03", None) is None
    resp = record_clip.unregistered_response("cam-03", {"unregisteredAt": "2026-10-25T12:00:00Z"})
    assert resp["statusCode"] == 409
    assert resp["headers"]["Access-Control-Allow-Origin"] == "*"
    assert "re-register" in json.loads(resp["body"])["error"]
