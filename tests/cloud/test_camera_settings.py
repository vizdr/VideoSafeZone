"""set-camera-settings: POST /cameras/settings, the web panel's per-camera settings
(WEB-PANEL-PLAN.md D7, §5.1): tags, schedule, recorded-quality choice.

Each field is checked and written on its own, emptied fields are removed, a camera that is
unknown or unregistered is refused, and every answer carries CORS (fakes.invoke checks it, #8).
"""
import pytest

from fakes import invoke
from loaders import load_lambda

lam = load_lambda("set_camera_settings")

SUB = "rtsp://admin:pw@192.0.2.12:554/sub"
CAMERAS = [
    {"cameraId": "cam-01", "mode": "transcode"},
    {"cameraId": "cam-02", "mode": "passthrough", "onvifHost": "192.0.2.12", "recordingMode": "manual",
     "detectionModes": ["motion", "cellMotion"], "recordStream": "main", "liveRtspUrl": SUB,
     "tags": ["old"]},
    {"cameraId": "cam-03", "mode": "passthrough", "onvifHost": "192.0.2.13", "recordStream": "sub"},
    {"cameraId": "cam-05", "mode": "passthrough", "onvifHost": "192.0.2.15", "unregisteredAt": "2026-10-25T12:00:00Z"},
]
WEEKNIGHTS = {"tz": "Europe/Berlin", "entries": [
    {"days": ["mon", "tue", "wed", "thu", "fri"], "at": "18:00", "action": "start"},
    {"days": ["mon", "tue", "wed", "thu", "fri"], "at": "18:00", "action": "mode:motion"},
    {"days": ["tue", "wed", "thu", "fri", "sat"], "at": "07:00", "action": "stop"},
]}


@pytest.fixture
def table(aws):
    return aws(lam, CAMERAS).tables["cameras"]


def post(body):
    return invoke(lam, body)


def test_tags_round_trip_and_replace_the_old_list(table):
    assert post({"cameraId": "cam-02", "tags": ["outdoor", "Gate 1"]}) == \
        (200, {"cameraId": "cam-02", "saved": {"tags": ["outdoor", "Gate 1"]}})
    assert table.row("cam-02")["tags"] == ["outdoor", "Gate 1"]


def test_a_schedule_round_trips(table):
    assert post({"cameraId": "cam-02", "schedule": WEEKNIGHTS})[0] == 200
    assert table.row("cam-02")["schedule"] == WEEKNIGHTS


def test_emptied_fields_are_removed_not_stored(table):
    post({"cameraId": "cam-02", "schedule": WEEKNIGHTS})
    assert post({"cameraId": "cam-02", "tags": [], "schedule": {"tz": "UTC", "entries": []}}) == \
        (200, {"cameraId": "cam-02", "saved": {"tags": None, "schedule": None}})
    row = table.row("cam-02")
    assert "tags" not in row and "schedule" not in row


def test_only_the_named_fields_are_written(table):
    post({"cameraId": "cam-02", "schedule": WEEKNIGHTS})
    row = table.row("cam-02")
    assert row["tags"] == ["old"] and row["recordingMode"] == "manual" and row["liveRtspUrl"] == SUB
    op, args = table.writes[-1]
    assert op == "update" and args["UpdateExpression"] == "SET #schedule = :schedule"


def test_the_sub_stream_is_the_one_step_below_main(table):
    assert post({"cameraId": "cam-02", "recordQuality": {"stream": "sub"}})[0] == 200
    assert table.row("cam-02")["recordQuality"] == {"stream": "sub"}
    # Back to the ceiling (or null): the choice goes.
    assert post({"cameraId": "cam-02", "recordQuality": {"stream": "main"}})[1]["saved"] == {"recordQuality": None}
    assert "recordQuality" not in table.row("cam-02")


@pytest.mark.parametrize("camera, stream", [
    ("cam-03", "main"),       # above the ceiling: cam-03 records its sub stream
    ("cam-02", "sub2"),       # not a step the page offers
    ("cam-01", "sub"),        # the USB camera: fixed on the Pi
])
def test_a_quality_above_the_ceiling_or_not_offered_is_refused(table, camera, stream):
    status, body = post({"cameraId": camera, "recordQuality": {"stream": stream}})
    assert status == 400 and "isn't offered" in body["error"]
    assert table.writes == []


@pytest.mark.parametrize("body, status, phrase", [
    ({"cameraId": "cam-02"}, 400, "at least one of"),
    ({"cameraId": "cam-02", "theme": "x"}, 400, "unknown setting(s): theme"),
    ({"cameraId": "cam-09", "tags": []}, 400, "unknown camera"),
    ({"tags": []}, 400, "unknown camera"),
    ({"cameraId": "cam-05", "tags": []}, 409, "unregistered"),
    ({"cameraId": "cam-02", "tags": ["a"] * 11}, 400, "at most 10"),
    ({"cameraId": "cam-02", "tags": [" padded"]}, 400, "no space at either end"),
    ({"cameraId": "cam-02", "tags": ["x" * 25]}, 400, "1-24"),
    ({"cameraId": "cam-02", "tags": ["Gate", "gate"]}, 400, "must not repeat"),
    ({"cameraId": "cam-02", "schedule": {"entries": []}}, 400, "{tz, entries}"),
    ({"cameraId": "cam-02", "schedule": {"tz": "Mars/Olympus Mons", "entries": []}}, 400, "time zone"),
    ({"cameraId": "cam-02", "schedule": {"tz": "UTC", "entries": [{"days": ["mon"], "at": "25:00", "action": "start"}]}}, 400, "HH:MM"),
    ({"cameraId": "cam-02", "schedule": {"tz": "UTC", "entries": [{"days": ["monday"], "at": "08:00", "action": "start"}]}}, 400, "days must be"),
    ({"cameraId": "cam-02", "schedule": {"tz": "UTC", "entries": [{"days": [], "at": "08:00", "action": "start"}]}}, 400, "days must be"),
    ({"cameraId": "cam-02", "schedule": {"tz": "UTC", "entries": [{"days": ["mon"], "at": "08:00", "action": "reboot"}]}}, 400, "action must be"),
    ({"cameraId": "cam-02", "schedule": {"tz": "UTC", "entries": [{"days": ["mon"], "at": "08:00", "action": "mode:human"}]}}, 409, "doesn't publish"),
    ({"cameraId": "cam-01", "schedule": {"tz": "UTC", "entries": [{"days": ["mon"], "at": "08:00", "action": "mode:motion"}]}}, 400, "only 'manual'"),
    ({"cameraId": "cam-02", "schedule": {"tz": "UTC", "entries": [
        {"days": ["mon"], "at": "08:00", "action": "start"}, {"days": ["mon", "tue"], "at": "08:00", "action": "stop"}]}}, 400, "two entries change the recording at mon 08:00"),
    ({"cameraId": "cam-02", "schedule": {"tz": "UTC", "entries": [{"days": ["mon"], "at": "08:00"}]}}, 400, "{days, at, action}"),
    ({"cameraId": "cam-02", "schedule": {"tz": "UTC", "entries": [{"days": ["mon"], "at": "08:00", "action": "start"}] * 51}}, 400, "at most 50"),
    ({"cameraId": "cam-02", "recordQuality": "sub"}, 400, "recordQuality must be"),
])
def test_bad_settings_are_refused_and_nothing_is_written(table, body, status, phrase):
    code, out = post(body)
    assert code == status and phrase in out["error"], out
    assert table.writes == []


def test_the_usb_camera_can_be_scheduled_to_start_stop_and_record_manually(table):
    sched = {"tz": "UTC", "entries": [{"days": ["sat"], "at": "09:30", "action": "start"},
                                      {"days": ["sat"], "at": "09:30", "action": "mode:manual"},
                                      {"days": ["sat"], "at": "17:00", "action": "stop"}]}
    assert post({"cameraId": "cam-01", "schedule": sched})[0] == 200


def test_a_body_that_isnt_json(table):
    resp = lam.lambda_handler({"body": "{nope"}, None)
    assert resp["statusCode"] == 400 and resp["headers"]["Access-Control-Allow-Origin"] == "*"


def test_a_failure_still_answers_with_cors(table, monkeypatch):
    def broken(**kw):
        raise RuntimeError("throttled")
    monkeypatch.setattr(table, "update_item", broken)
    assert post({"cameraId": "cam-02", "tags": ["x"]}) == (500, {"error": "throttled"})
