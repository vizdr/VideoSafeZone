"""The Lambdas that read KVS: get-hls-url, record-clip and clip-to-s3 (PYTEST.md, Phase 2).

KVS and S3 are Stubber-checked clients: each call's parameters are compared with what the test
expects, against the service model, and answered from the queue. The clips are tiny MP4s from
tests/mp4.py, so the duration and codec in each clip row are read from a real file structure.

clip-to-s3 is also called by VMS for its ONVIF detection clips (CLAUDE.md, "Deploying a
Lambda"), so its event shape and keys must stay as they are.
"""
import io
from datetime import datetime, timedelta, timezone

import pytest
from botocore.response import StreamingBody
from botocore.stub import ANY

from fakes import invoke
from loaders import load_lambda
from mp4 import clip

get_hls_url = load_lambda("get_hls_url")
record_clip = load_lambda("record_clip")
clip_to_s3 = load_lambda("clip_to_s3")

BUCKET = "test-bucket"
ENDPOINT = "https://b-1234abcd.kinesisvideo.eu-central-1.amazonaws.com"
CAMERAS = [
    {"cameraId": "cam-02", "mode": "passthrough"},
    {"cameraId": "cam-03", "mode": "passthrough", "videoCodecActiveSince": "2026-10-25T12:00:20+00:00"},
    {"cameraId": "cam-05", "mode": "passthrough", "unregisteredAt": "2026-10-25T12:00:00Z"},
]
T0 = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


class Near:
    """Matches a datetime within a few seconds of `when` (for "now - since")."""

    def __init__(self, when, slack=5):
        self.when, self.slack = when, slack

    def __eq__(self, other):
        return isinstance(other, datetime) and abs((other - self.when).total_seconds()) < self.slack

    def __ne__(self, other):
        return not self == other


def _endpoint(fake, stream, api):
    fake.stub("kinesisvideo").add_response("get_data_endpoint", {"DataEndpoint": ENDPOINT},
                                           {"StreamName": stream, "APIName": api})


# --- get-hls-url -------------------------------------------------------------------------------

def _session(fake, stream, **mode):
    _endpoint(fake, stream, "GET_HLS_STREAMING_SESSION_URL")
    fake.stub("kinesis-video-archived-media").add_response(
        "get_hls_streaming_session_url",
        {"HLSStreamingSessionURL": f"{ENDPOINT}/hls/v1/getHLSMasterPlaylist.m3u8?SessionToken=t"},
        {"StreamName": stream, **(mode or {"PlaybackMode": "LIVE"}), "Expires": 3600,
         "ContainerFormat": "FRAGMENTED_MP4"})


def test_hls_session_for_the_recorded_stream(aws):
    # Expires 3600, not 300: the session URL used to die five minutes into every viewing (#14).
    # FRAGMENTED_MP4 is pinned: H.265 plays only in fMP4.
    fake = aws(get_hls_url, CAMERAS)
    _session(fake, "cam-02")
    code, out = invoke(get_hls_url, query={"stream": "cam-02"})
    assert code == 200
    assert (out["expires_in"], out["stream"], out["view"]) == (3600, "cam-02", "recorded")
    assert fake.client_kwargs["kinesis-video-archived-media"]["endpoint_url"] == ENDPOINT


def test_live_view_reads_its_own_stream(aws):
    # The name is derived here, never taken from the caller.
    fake = aws(get_hls_url, CAMERAS)
    _session(fake, "cam-02-live")
    code, out = invoke(get_hls_url, query={"stream": "cam-02", "view": "live"})
    assert code == 200 and (out["stream"], out["view"]) == ("cam-02-live", "live")


@pytest.mark.parametrize("since, seconds", [("30", 30), ("0.1", 1), ("9999", 600)])
def test_ff66_since_replays_from_the_button_press(aws, since, seconds):
    # LIVE right after Stop -> Start played the old run's last fragment and stalled (#66).
    fake = aws(get_hls_url, CAMERAS)
    _session(fake, "cam-02", PlaybackMode="LIVE_REPLAY", HLSFragmentSelector={
        "FragmentSelectorType": "SERVER_TIMESTAMP",
        "TimestampRange": {"StartTimestamp": Near(datetime.now(timezone.utc) - timedelta(seconds=seconds))}})
    assert invoke(get_hls_url, query={"stream": "cam-02", "since": since})[0] == 200


@pytest.mark.parametrize("query, status", [
    ({"stream": "cam-02", "since": "soon"}, 400),
    ({"stream": "cam-09"}, 400),
    ({"stream": "cam-05"}, 409),
])
def test_hls_refuses(aws, query, status):
    aws(get_hls_url, CAMERAS)
    assert invoke(get_hls_url, query=query)[0] == status


@pytest.mark.parametrize("view, phrase", [(None, "stream is not live"), ("live", "live view not arriving")])
def test_no_fragments_is_a_503_not_a_500(aws, view, phrase):
    # Raised by the archived-media client, matched by code (#45, #8).
    fake = aws(get_hls_url, CAMERAS)
    stream = "cam-02-live" if view else "cam-02"
    _endpoint(fake, stream, "GET_HLS_STREAMING_SESSION_URL")
    fake.stub("kinesis-video-archived-media").add_client_error(
        "get_hls_streaming_session_url", "ResourceNotFoundException", http_status_code=404)
    code, out = invoke(get_hls_url, query={"stream": "cam-02", **({"view": view} if view else {})})
    assert code == 503 and phrase in out["error"]


def test_other_kvs_errors_are_a_500(aws):
    fake = aws(get_hls_url, CAMERAS)
    fake.stub("kinesisvideo").add_client_error("get_data_endpoint", "AccessDeniedException",
                                               http_status_code=403)
    assert invoke(get_hls_url, query={"stream": "cam-02"})[0] == 500


# --- record-clip and clip-to-s3: helpers ---------------------------------------------------------

def _get_clip(fake, stream, start, end, payload=None, error=None):
    stub = fake.stub("kinesis-video-archived-media")
    expected = {"StreamName": stream, "ClipFragmentSelector": {
        "FragmentSelectorType": "PRODUCER_TIMESTAMP",
        "TimestampRange": {"StartTimestamp": start, "EndTimestamp": end}}}
    if error:
        stub.add_client_error("get_clip", error, http_status_code=404, expected_params=expected)
    else:
        stub.add_response("get_clip", {"ContentType": "video/mp4",
                                       "Payload": StreamingBody(io.BytesIO(payload), len(payload))},
                          expected)


def _put(fake, key):
    fake.stub("s3").add_response("put_object", {}, {"Bucket": BUCKET, "Key": key, "Body": ANY,
                                                    "ContentType": "video/mp4"})


CLIP_ROW_FIELDS = {"cameraId", "startTs", "s3Key", "labels", "durationSec", "videoCodec"}


# --- record-clip -------------------------------------------------------------------------------

PAD = timedelta(seconds=2)


def _record(start=T0, end=T0 + timedelta(seconds=30), stream="cam-02"):
    return invoke(record_clip, {"stream": stream, "startTs": start.isoformat(),
                                "endTs": end.isoformat()})


def test_manual_recording(aws):
    fake = aws(record_clip, CAMERAS)
    s, e = T0 - PAD, T0 + timedelta(seconds=30) + PAD
    _endpoint(fake, "cam-02", "GET_CLIP")
    _get_clip(fake, "cam-02", s, e, clip(33, b"avc1"))
    key = "clips/cam-02/2026/10/25/115958-manual.mp4"
    _put(fake, key)
    assert _record() == (200, {"key": key, "keys": [key]})
    # The clip row carries the fields the data contract with VMS names, the duration read from
    # the file (#54), not the window asked for.
    row = fake.tables["clips"].row("cam-02", s.isoformat())
    assert set(row) == CLIP_ROW_FIELDS
    assert (row["s3Key"], row["labels"], row["durationSec"], row["videoCodec"]) == \
        (key, ["manual-recording"], 33, "h264")


def test_times_without_a_zone_are_utc(aws):
    fake = aws(record_clip, CAMERAS)
    _endpoint(fake, "cam-02", "GET_CLIP")
    _get_clip(fake, "cam-02", T0 - PAD, T0 + timedelta(seconds=30) + PAD, clip(30))
    _put(fake, "clips/cam-02/2026/10/25/115958-manual.mp4")
    assert invoke(record_clip, {"stream": "cam-02", "startTs": "2026-10-25T12:00:00",
                                "endTs": "2026-10-25T12:00:30"})[0] == 200


def test_an_unreadable_clip_keeps_the_window_length_and_no_codec(aws):
    fake = aws(record_clip, CAMERAS)
    _endpoint(fake, "cam-02", "GET_CLIP")
    _get_clip(fake, "cam-02", T0 - PAD, T0 + timedelta(seconds=30) + PAD, b"not an mp4")
    _put(fake, "clips/cam-02/2026/10/25/115958-manual.mp4")
    _record()
    row = fake.tables["clips"].items[0]
    assert row["durationSec"] == 34 and "videoCodec" not in row


def test_a_recording_across_a_codec_switch_becomes_two_clips(aws):
    # KVS refuses a clip whose fragments change codec.
    fake = aws(record_clip, CAMERAS)
    switch = T0 + timedelta(seconds=20)
    _endpoint(fake, "cam-03", "GET_CLIP")
    _get_clip(fake, "cam-03", T0 - PAD, switch, clip(22, b"avc1"))
    _get_clip(fake, "cam-03", switch, T0 + timedelta(seconds=30) + PAD, clip(12, b"hvc1"))
    keys = ["clips/cam-03/2026/10/25/115958-manual.mp4", "clips/cam-03/2026/10/25/120020-manual.mp4"]
    for k in keys:
        _put(fake, k)
    assert _record(stream="cam-03") == (200, {"key": keys[0], "keys": keys})
    rows = fake.tables["clips"].items
    assert [(r["videoCodec"], r["labels"]) for r in rows] == [
        ("h264", ["manual-recording", "codec-switch"]), ("h265", ["manual-recording", "codec-switch"])]


def test_one_empty_side_of_a_switch_still_saves_the_other(aws):
    # The ~10 s the producer takes to restart on the new codec.
    fake = aws(record_clip, CAMERAS)
    switch = T0 + timedelta(seconds=20)
    _endpoint(fake, "cam-03", "GET_CLIP")
    _get_clip(fake, "cam-03", T0 - PAD, switch, error="ResourceNotFoundException")
    _get_clip(fake, "cam-03", switch, T0 + timedelta(seconds=30) + PAD, clip(12, b"hvc1"))
    key = "clips/cam-03/2026/10/25/120020-manual.mp4"
    _put(fake, key)
    assert _record(stream="cam-03") == (200, {"key": key, "keys": [key]})


def test_ff45_no_footage_is_a_503(aws):
    fake = aws(record_clip, CAMERAS)
    _endpoint(fake, "cam-02", "GET_CLIP")
    _get_clip(fake, "cam-02", T0 - PAD, T0 + timedelta(seconds=30) + PAD,
              error="ResourceNotFoundException")
    code, out = _record()
    assert code == 503 and "no footage" in out["error"]
    assert fake.tables["clips"].writes == []


def test_no_footage_on_either_side_of_a_switch_is_a_503(aws):
    fake = aws(record_clip, CAMERAS)
    switch = T0 + timedelta(seconds=20)
    _endpoint(fake, "cam-03", "GET_CLIP")
    _get_clip(fake, "cam-03", T0 - PAD, switch, error="ResourceNotFoundException")
    _get_clip(fake, "cam-03", switch, T0 + timedelta(seconds=30) + PAD, error="ResourceNotFoundException")
    assert _record(stream="cam-03")[0] == 503


@pytest.mark.parametrize("kwargs, status", [
    ({"end": T0 - timedelta(seconds=10)}, 400),     # still before the start after padding
    ({"stream": "cam-09"}, 400),
    ({"stream": "cam-05"}, 409),
])
def test_record_clip_refuses(aws, kwargs, status):
    aws(record_clip, CAMERAS)
    assert _record(**kwargs)[0] == status


def test_equal_start_and_end_ask_for_the_padded_four_seconds(aws):
    # Both ends are padded by 2 s for clock skew between the viewer's machine and the producer.
    fake = aws(record_clip, CAMERAS)
    _endpoint(fake, "cam-02", "GET_CLIP")
    _get_clip(fake, "cam-02", T0 - PAD, T0 + PAD, clip(4))
    _put(fake, "clips/cam-02/2026/10/25/115958-manual.mp4")
    assert _record(end=T0)[0] == 200


# --- clip-to-s3 ----------------------------------------------------------------------------------

def _detection(fake, stream, windows, payloads):
    _endpoint(fake, stream, "GET_CLIP")
    for (s, e), payload in zip(windows, payloads):
        if isinstance(payload, str):
            _get_clip(fake, stream, s, e, error=payload)
        else:
            _get_clip(fake, stream, s, e, payload)


def test_detection_clip_window_key_and_row(aws):
    fake = aws(clip_to_s3, CAMERAS)
    _detection(fake, "cam-02", [(T0 - timedelta(seconds=12), T0 + timedelta(seconds=33))],
               [clip(45, b"hvc1")])
    key = "clips/cam-02/2026/10/25/120000.mp4"
    _put(fake, key)
    assert clip_to_s3.lambda_handler({"timestamp": "2026-10-25T12:00:00Z", "stream": "cam-02",
                                      "labels": ["human"]}, None) == {"keys": [key]}
    row = fake.tables["clips"].items[0]
    assert set(row) == CLIP_ROW_FIELDS
    assert (row["startTs"], row["labels"], row["durationSec"], row["videoCodec"]) == \
        ((T0 - timedelta(seconds=12)).isoformat(), ["human"], 45, "h265")


def test_an_event_without_labels_still_works(aws):
    # VMS's events; and a camera with no registry row reads as "no codec switch".
    fake = aws(clip_to_s3, [])
    _detection(fake, "cam-02", [(T0 - timedelta(seconds=12), T0 + timedelta(seconds=33))],
               [clip(45)])
    _put(fake, "clips/cam-02/2026/10/25/120000.mp4")
    clip_to_s3.lambda_handler({"timestamp": "2026-10-25T12:00:00+00:00", "stream": "cam-02"}, None)
    assert fake.tables["clips"].items[0]["labels"] == []


def test_a_detection_across_a_codec_switch_gets_numbered_keys(aws):
    # The two parts of one detection must not overwrite each other.
    fake = aws(clip_to_s3, CAMERAS)
    switch = T0 + timedelta(seconds=20)
    _detection(fake, "cam-03", [(T0 - timedelta(seconds=12), switch), (switch, T0 + timedelta(seconds=33))],
               [clip(32), clip(13, b"hvc1")])
    keys = ["clips/cam-03/2026/10/25/120000-1.mp4", "clips/cam-03/2026/10/25/120000-2.mp4"]
    for k in keys:
        _put(fake, k)
    assert clip_to_s3.lambda_handler({"timestamp": "2026-10-25T12:00:00Z", "stream": "cam-03",
                                      "labels": ["motion"]}, None) == {"keys": keys}
    assert [r["labels"] for r in fake.tables["clips"].items] == [["motion", "codec-switch"]] * 2


def test_no_footage_raises_for_the_iot_rule(aws):
    # Fire-and-forget: the failure must reach the rule's error action, not vanish.
    fake = aws(clip_to_s3, CAMERAS)
    _detection(fake, "cam-02", [(T0 - timedelta(seconds=12), T0 + timedelta(seconds=33))],
               ["ResourceNotFoundException"])
    with pytest.raises(Exception) as err:
        clip_to_s3.lambda_handler({"timestamp": "2026-10-25T12:00:00Z", "stream": "cam-02"}, None)
    assert clip_to_s3.no_fragments(err.value)
    assert fake.tables["clips"].writes == []
