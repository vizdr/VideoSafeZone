"""The Lambdas that change a camera: publish-cmd and the three settings routes (PYTEST.md, Phase 2).

Each answers 400 for bad input, 409 for an unregistered camera, writes one registry field and
nothing else (the registry is shared with VMS and updated field by field), and answers every path
-- 500 included -- with CORS headers (fakes.invoke checks that on every call, #8).
"""
import json

import pytest

import outage_buffer
from fakes import invoke
from loaders import load_lambda

publish_cmd = load_lambda("publish_cmd")
set_camera_mode = load_lambda("set_camera_mode")
set_camera_audio = load_lambda("set_camera_audio")
set_camera_outage_buffer = load_lambda("set_camera_outage_buffer")

UNREG = "2026-10-25T12:00:00Z"
CAMERAS = [
    {"cameraId": "cam-01", "mode": "transcode", "audioCapable": True},
    {"cameraId": "cam-02", "mode": "passthrough", "onvifHost": "192.0.2.12", "hasIrControl": True,
     "audioCapable": True, "detectionModes": ["motion", "cellMotion", "human"],
     "rtspUrl": "rtsp://admin:pw@192.0.2.12/s"},
    {"cameraId": "cam-03", "mode": "passthrough", "onvifHost": "192.0.2.13",
     "detectionModes": ["motion"]},
    {"cameraId": "cam-04", "mode": "passthrough", "onvifHost": "192.0.2.14"},   # modes not known yet
    {"cameraId": "cam-05", "mode": "passthrough", "onvifHost": "192.0.2.15", "unregisteredAt": UNREG},
]
CMD_TOPIC = "adapter/adapter-01/cmd"      # THING_NAME unset in the tests: the Lambda's default


def _expect_publish(fake, payload):
    fake.stub("iot-data").add_response("publish", {}, {"topic": CMD_TOPIC, "qos": 1,
                                                       "payload": json.dumps(payload)})


# --- publish-cmd ------------------------------------------------------------------------------

@pytest.mark.parametrize("action", ["start", "stop", "live", "live-stop"])
def test_publish_cmd_relays_to_the_agent(aws, action):
    fake = aws(publish_cmd, CAMERAS)
    _expect_publish(fake, {"action": action, "camera": "cam-02"})
    assert invoke(publish_cmd, {"action": action, "camera": "cam-02"}) == \
        (200, {"published": action, "camera": "cam-02"})


def test_publish_cmd_without_camera_means_cam01(aws):
    # Older pages send no camera field.
    fake = aws(publish_cmd, CAMERAS)
    _expect_publish(fake, {"action": "start", "camera": "cam-01"})
    assert invoke(publish_cmd, {"action": "start"})[0] == 200


@pytest.mark.parametrize("body, status, phrase", [
    ({"action": "reboot", "camera": "cam-02"}, 400, "action must be"),
    ({"camera": "cam-02"}, 400, "action must be"),
    ({"action": "start", "camera": "cam-09"}, 400, "unknown camera"),
    ({"action": "start", "camera": "cam-05"}, 409, "unregistered"),
    ({"action": "live", "camera": "cam-05"}, 409, "unregistered"),
    ({"action": "ir", "camera": "cam-03", "mode": "ON"}, 400, "no IR control"),
    ({"action": "ir", "camera": "cam-02", "mode": "on"}, 400, "mode must be"),
    ({"action": "ir", "camera": "cam-02"}, 400, "mode must be"),
])
def test_publish_cmd_refuses(aws, body, status, phrase):
    aws(publish_cmd, CAMERAS)                 # no publish queued: one would fail the test
    code, out = invoke(publish_cmd, body)
    assert code == status and phrase in out["error"]


@pytest.mark.parametrize("action", ["stop", "live-stop"])
def test_an_unregistered_camera_can_still_be_stopped(aws, action):
    # A camera unregistered while running must not be left pushing to KVS.
    fake = aws(publish_cmd, CAMERAS)
    _expect_publish(fake, {"action": action, "camera": "cam-05"})
    assert invoke(publish_cmd, {"action": action, "camera": "cam-05"})[0] == 200


def test_publish_cmd_ir(aws):
    fake = aws(publish_cmd, CAMERAS)
    _expect_publish(fake, {"action": "ir", "camera": "cam-02", "mode": "AUTO"})
    assert invoke(publish_cmd, {"action": "ir", "camera": "cam-02", "mode": "AUTO"}) == \
        (200, {"published": "ir", "camera": "cam-02", "mode": "AUTO"})


def test_publish_cmd_failure_is_a_500_with_cors(aws):
    fake = aws(publish_cmd, CAMERAS)
    fake.stub("iot-data").add_client_error("publish", "ForbiddenException", http_status_code=403)
    code, out = invoke(publish_cmd, {"action": "start", "camera": "cam-02"})
    assert code == 500 and "ForbiddenException" in out["error"]


def test_bad_json_is_a_500_with_cors(aws):
    aws(publish_cmd, CAMERAS)
    resp = publish_cmd.lambda_handler({"body": "{not json"}, None)
    assert resp["statusCode"] == 500 and resp["headers"]["Access-Control-Allow-Origin"] == "*"


# --- set-camera-mode --------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["manual", "motion", "cellMotion", "human"])
def test_recording_mode_accepts_exactly_the_four_shared_values(aws, mode):
    # The registry is shared with VMS: no new values in an existing enum (CLAUDE.md).
    fake = aws(set_camera_mode, CAMERAS)
    assert invoke(set_camera_mode, {"cameraId": "cam-02", "recordingMode": mode}) == \
        (200, {"cameraId": "cam-02", "recordingMode": mode})
    assert fake.tables["cameras"].writes == [("update", {
        "Key": {"cameraId": "cam-02"}, "UpdateExpression": "SET recordingMode = :m",
        "ExpressionAttributeValues": {":m": mode}, "ExpressionAttributeNames": {},
        "ConditionExpression": None})]


def test_the_lambdas_list_is_the_four_values():
    assert set_camera_mode.VALID_MODES == {"manual", "motion", "cellMotion", "human"}


@pytest.mark.parametrize("mode", ["Motion", "person", "smartMotion", "", None, 1])
def test_recording_mode_refuses_anything_else(aws, mode):
    fake = aws(set_camera_mode, CAMERAS)
    assert invoke(set_camera_mode, {"cameraId": "cam-02", "recordingMode": mode})[0] == 400
    assert fake.tables["cameras"].writes == []


@pytest.mark.parametrize("camera, mode, status, phrase", [
    ("cam-01", "motion", 400, "no ONVIF host"),          # the USB camera: manual only
    ("cam-03", "human", 409, "doesn't publish"),         # not in its own event list (#65)
    ("cam-05", "manual", 409, "unregistered"),
    ("cam-09", "manual", 400, "unknown camera"),
    (None, "manual", 400, "unknown camera"),
])
def test_recording_mode_refuses(aws, camera, mode, status, phrase):
    fake = aws(set_camera_mode, CAMERAS)
    code, out = invoke(set_camera_mode, {"cameraId": camera, "recordingMode": mode})
    assert code == status and phrase in out["error"]
    assert fake.tables["cameras"].writes == []


def test_modes_not_known_yet_are_allowed(aws):
    aws(set_camera_mode, CAMERAS)
    assert invoke(set_camera_mode, {"cameraId": "cam-04", "recordingMode": "human"})[0] == 200


def test_manual_is_always_allowed(aws):
    aws(set_camera_mode, CAMERAS)
    assert invoke(set_camera_mode, {"cameraId": "cam-01", "recordingMode": "manual"})[0] == 200


def test_the_rest_of_the_row_is_untouched(aws):
    fake = aws(set_camera_mode, CAMERAS)
    invoke(set_camera_mode, {"cameraId": "cam-02", "recordingMode": "human"})
    assert fake.tables["cameras"].row("cam-02") == {**CAMERAS[1], "recordingMode": "human"}


# --- set-camera-audio -------------------------------------------------------------------------

@pytest.mark.parametrize("enabled", [True, False])
def test_audio_applies_on_next_start(aws, enabled):
    # KVS refuses a stream whose fragments change from video-only to audio+video, so a running
    # producer is never switched (guide §18).
    fake = aws(set_camera_audio, CAMERAS)
    assert invoke(set_camera_audio, {"cameraId": "cam-02", "audioEnabled": enabled}) == \
        (200, {"cameraId": "cam-02", "audioEnabled": enabled, "appliesOn": "next start"})
    assert fake.tables["cameras"].row("cam-02")["audioEnabled"] is enabled


@pytest.mark.parametrize("value", ["true", 1, None])
def test_audio_flag_must_be_a_boolean(aws, value):
    fake = aws(set_camera_audio, CAMERAS)
    assert invoke(set_camera_audio, {"cameraId": "cam-02", "audioEnabled": value})[0] == 400
    assert fake.tables["cameras"].writes == []


def test_audio_on_without_a_microphone_is_refused(aws):
    # A pipeline waiting on an audio pad that never delivers stalls the video too.
    aws(set_camera_audio, CAMERAS)
    code, out = invoke(set_camera_audio, {"cameraId": "cam-03", "audioEnabled": True})
    assert code == 400 and "no usable audio" in out["error"]


def test_audio_off_is_always_allowed(aws):
    aws(set_camera_audio, CAMERAS)
    assert invoke(set_camera_audio, {"cameraId": "cam-03", "audioEnabled": False})[0] == 200


def test_audio_unregistered(aws):
    aws(set_camera_audio, CAMERAS)
    assert invoke(set_camera_audio, {"cameraId": "cam-05", "audioEnabled": False})[0] == 409


# --- set-camera-outage-buffer -----------------------------------------------------------------

def test_outage_limits_match_the_supervisors():
    # Duplicated on purpose (a Lambda can't import adapter code); a value this API accepts and
    # the supervisor rejects would be silently ignored on the Pi.
    assert set_camera_outage_buffer.VALID_LIMITS == {0} | outage_buffer.VALID_LIMITS


@pytest.mark.parametrize("secs", sorted({0} | outage_buffer.VALID_LIMITS))
def test_outage_buffer_accepts_every_valid_limit(aws, secs):
    fake = aws(set_camera_outage_buffer, CAMERAS)
    assert invoke(set_camera_outage_buffer, {"cameraId": "cam-02", "outageBufferSec": secs}) == \
        (200, {"cameraId": "cam-02", "outageBufferSec": secs, "appliesOn": "within 60s"})
    assert fake.tables["cameras"].row("cam-02")["outageBufferSec"] == secs


@pytest.mark.parametrize("secs", [
    45, -1, "300", 300.0, True, None,
    False,      # equals 0, a valid limit: only the explicit bool check stops it
])
def test_outage_buffer_refuses_other_values(aws, secs):
    fake = aws(set_camera_outage_buffer, CAMERAS)
    assert invoke(set_camera_outage_buffer, {"cameraId": "cam-02", "outageBufferSec": secs})[0] == 400
    assert fake.tables["cameras"].writes == []


def test_outage_buffer_unregistered(aws):
    aws(set_camera_outage_buffer, CAMERAS)
    assert invoke(set_camera_outage_buffer, {"cameraId": "cam-05", "outageBufferSec": 0})[0] == 409
