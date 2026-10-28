"""The clip Lambdas' helpers: codec windows and reading the clip's own MP4 (PYTEST.md, step 5).

Every test runs on both copies, record_clip.py and clip_to_s3.py. The MP4s are built from boxes
here, a few bytes each: only the structure the helpers walk matters.
"""
from datetime import datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError

from loaders import load_lambda
from mp4 import FTYP_SAYS_AVC1, box, large_box, mvhd_v0, mvhd_v1, sample_entry

COPIES = [load_lambda("record_clip"), load_lambda("clip_to_s3")]
both = pytest.mark.parametrize("lam", COPIES, ids=["record_clip", "clip_to_s3"])


# --- utc, codec_windows ---------------------------------------------------------------------------

T0 = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


@both
def test_utc(lam):
    assert lam.utc(datetime(2026, 10, 25, 12, 0)) == T0
    aware = datetime(2026, 10, 25, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    assert lam.utc(aware) is aware


@both
def test_codec_windows_split_at_the_switch(lam):
    # KVS refuses a clip whose fragments change codec: one window per codec.
    start, end = T0, T0 + timedelta(seconds=45)
    camera = {"videoCodecActiveSince": "2026-10-25T12:00:20+00:00"}
    switch = T0 + timedelta(seconds=20)
    assert lam.codec_windows(start, end, camera) == [(start, switch), (switch, end)]


@both
def test_codec_windows_naive_timestamp_is_utc(lam):
    start, end = T0, T0 + timedelta(seconds=45)
    assert len(lam.codec_windows(start, end, {"videoCodecActiveSince": "2026-10-25T12:00:20"})) == 2


@both
@pytest.mark.parametrize("camera", [
    None, {}, {"videoCodecActiveSince": "2026-10-25T11:00:00+00:00"},   # switched before
    {"videoCodecActiveSince": "2026-10-25T12:00:00+00:00"},              # exactly at start
    {"videoCodecActiveSince": "2026-10-25T12:00:45+00:00"},              # exactly at end
])
def test_codec_windows_no_switch_inside(lam, camera):
    start, end = T0, T0 + timedelta(seconds=45)
    assert lam.codec_windows(start, end, camera) == [(start, end)]


# --- mp4_video_codec ------------------------------------------------------------------------------

@both
@pytest.mark.parametrize("fourcc, codec", [
    (b"avc1", "h264"), (b"avc3", "h264"), (b"hvc1", "h265"), (b"hev1", "h265"),
])
def test_codec_from_the_sample_description(lam, fourcc, codec):
    mp4 = FTYP_SAYS_AVC1 + box(b"moov", mvhd_v0(1000, 45000) + sample_entry(fourcc)) + box(b"mdat")
    assert lam.mp4_video_codec(mp4) == codec


@both
def test_codec_ignores_ftyp_and_mdat(lam):
    # ftyp's brand list says avc1 and the media bytes happen to contain one: only moov counts.
    mp4 = FTYP_SAYS_AVC1 + box(b"mdat", b"..avc1..") + box(b"moov", sample_entry(b"hvc1"))
    assert lam.mp4_video_codec(mp4) == "h265"


@both
def test_codec_through_a_64_bit_box(lam):
    mp4 = FTYP_SAYS_AVC1 + large_box(b"mdat", bytes(32)) + box(b"moov", sample_entry(b"hvc1"))
    assert lam.mp4_video_codec(mp4) == "h265"


@both
def test_codec_of_a_box_running_to_end_of_file(lam):
    moov = box(b"moov", sample_entry(b"avc1"))
    assert lam.mp4_video_codec(FTYP_SAYS_AVC1 + bytes(4) + moov[4:]) == "h264"   # size 0


@both
@pytest.mark.parametrize("data", [
    b"",
    FTYP_SAYS_AVC1 + box(b"mdat", b"hvc1"),                      # no moov at all
    box(b"moov", sample_entry(b"mp4v")),                         # neither codec
    FTYP_SAYS_AVC1 + (4).to_bytes(4, "big") + b"moov",           # size below the header: malformed
])
def test_codec_unknown(lam, data):
    assert lam.mp4_video_codec(data) is None


# --- mp4_duration_sec -----------------------------------------------------------------------------

@both
def test_ff54_duration_is_what_the_file_holds_not_what_was_asked(lam):
    # A cam-02 H.265 detection clip listed as 45 s held 9 s.
    mp4 = FTYP_SAYS_AVC1 + box(b"moov", mvhd_v0(90000, 9 * 90000) + sample_entry(b"hvc1"))
    assert lam.mp4_duration_sec(mp4) == 9


@both
def test_duration_version_1_header(lam):
    mp4 = box(b"moov", mvhd_v1(1000, 45_400))
    assert lam.mp4_duration_sec(mp4) == 45


@both
def test_duration_rounds_to_whole_seconds(lam):
    assert lam.mp4_duration_sec(box(b"moov", mvhd_v0(1000, 8_600))) == 9


@both
@pytest.mark.parametrize("data", [
    b"", None, box(b"moov", sample_entry(b"avc1")),             # no mvhd
    box(b"moov", box(b"mvhd", bytes(10))),                      # truncated mvhd
    box(b"moov", mvhd_v0(0, 1000)),                             # timescale 0
    box(b"mdat", mvhd_v0(1000, 1000)),                          # mvhd outside moov
])
def test_duration_unknown(lam, data):
    assert lam.mp4_duration_sec(data) is None


# --- no_fragments ---------------------------------------------------------------------------------

def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "GetClip")


@both
def test_ff45_no_fragments_is_matched_by_error_code(lam):
    # The archived-media client raises its own exception class; matching the kinesisvideo
    # client's class never fired, so the intended 503 was dead code.
    assert lam.no_fragments(_client_error("ResourceNotFoundException"))


@both
@pytest.mark.parametrize("err", [_client_error("AccessDeniedException"), RuntimeError("x"),
                                 ValueError("ResourceNotFoundException")])
def test_other_errors_are_not_no_fragments(lam, err):
    assert not lam.no_fragments(err)
