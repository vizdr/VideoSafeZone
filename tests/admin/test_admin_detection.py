"""The admin GUI's detection routes: the camera's event list, the trigger test, and the read-only
analytics view (PYTEST.md, Phase 3).

The trigger test listens to the camera for up to a minute while someone walks past. Its counting
rules come from real cameras (FoundAndFixed.md #65, #68): what arrives in the first two seconds
is the camera's opening report, not a detection; a later `Initialized` message isn't one either;
a `Changed` one is, whatever field name it uses.
"""
import asyncio
import threading
from types import SimpleNamespace as NS

import onvif
import pytest

import onvif_events
from fakes import ModuleShim
from gui_world import CAM01, CAM03, ROWS, UNREG, admin

MOTION_T, HUMAN_T = "tns1:VideoSource/MotionAlarm", "tns1:UserAlarm/IVA/HumanShapeDetect"
DMAP = {"motion": {"topic": MOTION_T, "field": "State", "match": "known"},
        "human": {"topic": HUMAN_T, "field": "State", "match": "known"}}


def note(topic, value, op="Changed", field="State"):
    return NS(Topic=NS(_value_1=topic),
              Message=NS(_value_1=NS(PropertyOperation=op, Data=NS(SimpleItem=[NS(Name=field, Value=value)]))))


# --- GET .../detection ----------------------------------------------------------------------------

def _reads(dmap, web_url="http://192.0.2.13/"):
    return lambda host, port, user, password: (dmap, {v["topic"] for v in dmap.values()}, web_url)


def test_detection_is_read_from_the_camera_and_recorded(gui):
    g = gui(detection=_reads(DMAP))
    status, body = g.get("/api/cameras/cam-03/detection")
    assert status == 200 and body["modes"] == ["motion", "human"]
    assert body["topics"] == sorted([MOTION_T, HUMAN_T]) and body["webUrl"] == "http://192.0.2.13/"
    row = g.table.row("cam-03")
    assert row["detectionModes"] == ["motion", "human"] and row["webUrl"] == "http://192.0.2.13/"
    assert row["detectionTopics"]["human"] == {"topic": HUMAN_T, "field": "State"}
    assert row["recordingMode"] == "manual"          # the four shared values are not touched


def test_an_unchanged_event_list_writes_nothing(gui):
    topics = {m: {"topic": v["topic"], "field": v["field"]} for m, v in DMAP.items()}
    cam03 = {**CAM03, "detectionModes": ["motion", "human"], "detectionTopics": topics,
             "webUrl": "http://192.0.2.13/"}
    g = gui(rows=[cam03], detection=_reads(DMAP))
    assert g.get("/api/cameras/cam-03/detection")[0] == 200
    assert g.table.writes == []


def test_an_unreachable_camera_shows_what_was_recorded(gui):
    status, body = gui().get("/api/cameras/cam-03/detection")
    assert status == 502 and body["modes"] == ["motion"]


def test_the_usb_camera_has_no_events(gui):
    assert gui().get("/api/cameras/cam-01/detection") == \
        (200, {"modes": [], "reason": "no ONVIF host: manual recording only"})


def test_detection_with_the_registry_unreachable(gui, monkeypatch):
    g = gui()

    def unreachable(*a, **k):
        raise OSError("timeout")
    monkeypatch.setattr(admin, "get_session", unreachable)
    assert g.get("/api/cameras/cam-03/detection")[0] == 503
    assert g.get("/api/cameras/cam-09/detection")[0] == 503


def test_detection_of_an_unknown_camera(gui):
    assert gui().get("/api/cameras/cam-09/detection")[0] == 404


# --- the trigger test, run against a scripted camera ---------------------------------------------

class FakeONVIFCamera:
    instances = []

    def __init__(self, host, port, user, password, wsdl_dir=None, fail=None):
        self.closed = False
        FakeONVIFCamera.instances.append(self)

    async def update_xaddrs(self):
        if getattr(FakeONVIFCamera, "fail", None):
            raise FakeONVIFCamera.fail

    async def close(self):
        self.closed = True


class Subscription:
    """PullSubscription: each pull takes one second of the fake clock and returns what the
    script has for that second since subscribing."""

    def __init__(self, clock, script):
        self.clock, self.script, self.exited = clock, script, False

    async def __aenter__(self):
        self.t0 = self.clock.now
        return self

    async def __aexit__(self, *exc):
        self.exited = True              # given back: cameras have few subscription slots (#65)

    async def pull(self):
        notes = self.script.get(round(self.clock.now - self.t0), [])
        self.clock.advance(1)
        return notes


@pytest.fixture
def camera_events(monkeypatch):
    """camera_events(gui, script, dmap=DMAP) -> the Subscription; `script` maps a second since
    subscribing to the notifications the camera sends then."""
    def install(g, script, dmap=DMAP, fail=None):
        FakeONVIFCamera.instances, FakeONVIFCamera.fail = [], fail
        monkeypatch.setattr(onvif, "ONVIFCamera", FakeONVIFCamera)

        async def detection(cam):
            return dmap, {v["topic"] for v in dmap.values()}
        monkeypatch.setattr(onvif_events, "camera_detection", detection)
        sub = Subscription(g.clock, script)
        monkeypatch.setattr(onvif_events, "PullSubscription", lambda cam: sub)
        return sub
    return install


def _run(seconds=60, stop=False, item=CAM03):
    state = {"state": "starting", "cameraId": item["cameraId"], "seconds": seconds, "startedAt": "t",
             "modes": {}, "otherTopics": {}, "error": None, "_stop": threading.Event()}
    if stop:
        state["_stop"].set()
    asyncio.run(admin._trigger_test(item, state))
    return state


def test_a_detection_during_the_walk_is_seen(gui, camera_events):
    g = gui()
    camera_events(g, {5: [note(MOTION_T, "true")], 7: [note(MOTION_T, "false")]})
    state = _run()
    assert state["state"] == "done" and state["modes"]["motion"]["seen"] == 1
    assert state["modes"]["motion"]["firstAfterSec"] is not None
    assert state["modes"]["human"]["seen"] == 0


def test_the_opening_report_is_the_state_at_the_start_not_a_detection(gui, camera_events):
    # cam-03 sends its Initialized state and any backlog together, 0.3 s after subscribing.
    g = gui()
    camera_events(g, {0: [note(MOTION_T, "true", op="Initialized"), note(HUMAN_T, "true")]})
    state = _run()
    assert state["modes"]["motion"] | {"topic": None, "field": None} == \
        {"topic": None, "field": None, "seen": 0, "firstAfterSec": None, "atStart": True}
    assert state["modes"]["human"]["seen"] == 0 and state["modes"]["human"]["atStart"] is True


def test_ff68_initialized_never_counts_changed_always_does(gui, camera_events):
    # cam-04 sends both an Initialized and a Changed message for every detection.
    g = gui()
    camera_events(g, {10: [note(MOTION_T, "true", op="Initialized"), note(MOTION_T, "true")]})
    assert _run()["modes"]["motion"]["seen"] == 1


def test_ff68_a_detection_in_another_field_than_declared_counts(gui, camera_events):
    # cam-04 declares MotionAlarm with State and sends IsMotion.
    g = gui()
    camera_events(g, {5: [note(MOTION_T, "true", field="IsMotion")]})
    assert _run()["modes"]["motion"]["seen"] == 1


def test_other_topics_are_counted_apart(gui, camera_events):
    g = gui()
    camera_events(g, {4: [note("tns1:Device/Trigger/DigitalInput", "true")]})
    assert _run()["otherTopics"] == {"tns1:Device/Trigger/DigitalInput": 1}


def test_the_test_ends_as_soon_as_every_trigger_fired(gui, camera_events):
    g = gui()
    sub = camera_events(g, {3: [note(MOTION_T, "true")], 4: [note(HUMAN_T, "true")]})
    start = g.clock.now
    state = _run(seconds=60)
    assert g.clock.now - start < 10 and state["state"] == "done"
    assert sub.exited and FakeONVIFCamera.instances[0].closed


def test_the_outcome_is_merged_into_the_registry(gui, camera_events):
    earlier = {"cellMotion": {"result": "seen", "at": "2026-10-20T10:00:00Z", "events": 2}}
    g = gui(rows=[{**CAM03, "detectionTests": earlier}])
    camera_events(g, {5: [note(MOTION_T, "true")]})
    state = _run(seconds=20)
    tests = g.table.row("cam-03")["detectionTests"]
    assert tests["cellMotion"] == earlier["cellMotion"]
    assert (tests["motion"]["result"], tests["motion"]["events"]) == ("seen", 1)
    assert (tests["human"]["result"], tests["human"]["events"]) == ("not-seen", 0)
    assert tests["motion"]["at"] == state["endedAt"]


def test_a_stopped_test_records_only_what_it_saw(gui, camera_events):
    # "not seen" needs the full time.
    g = gui()
    camera_events(g, {})
    state = _run(stop=True)
    assert state["stopped"] is True and "detectionTests" not in g.table.row("cam-03")


def test_a_camera_error_is_reported_and_nothing_recorded(gui, camera_events):
    g = gui()
    camera_events(g, {}, fail=OSError("no route to host"))
    state = _run()
    assert state["state"] == "error" and "no route to host" in state["error"]
    assert "detectionTests" not in g.table.row("cam-03")
    assert FakeONVIFCamera.instances[0].closed


def test_a_camera_without_detection_ends_at_once(gui, camera_events):
    g = gui()
    camera_events(g, {}, dmap={})
    state = _run()
    assert (state["state"], state["modes"]) == ("done", {})


# --- the trigger test's routes --------------------------------------------------------------------

@pytest.fixture
def started(monkeypatch):
    """The routes start the test in a thread: run it inline, and only mark it started."""
    async def begin(item, state):
        pass
    monkeypatch.setattr(admin, "_trigger_test", begin)

    class InlineThread:
        def __init__(self, target, daemon=None, **kw):
            self.target = target

        def start(self):
            self.target()
    monkeypatch.setattr(admin, "threading", ModuleShim(threading, Thread=InlineThread))


def test_start_status_and_stop(gui, started):
    g = gui()
    status, body = g.post("/api/cameras/cam-03/detection/test", {"seconds": 30})
    assert status == 202 and (body["state"], body["seconds"]) == ("starting", 30)
    assert "_stop" not in body
    assert g.post("/api/cameras/cam-03/detection/test", {})[0] == 409        # one at a time
    assert g.get("/api/cameras/cam-03/detection/test")[1]["state"] == "starting"
    g.delete("/api/cameras/cam-03/detection/test")
    assert admin._trigger_tests["cam-03"]["_stop"].is_set()


def test_status_while_listening_counts_down(gui, started):
    g = gui()
    g.post("/api/cameras/cam-03/detection/test", {"seconds": 60})
    admin._trigger_tests["cam-03"].update(state="listening", _deadline=g.clock.now + 42)
    assert g.get("/api/cameras/cam-03/detection/test")[1]["secondsLeft"] == 42


def test_no_test_yet(gui):
    assert gui().get("/api/cameras/cam-02/detection/test")[1] == {"state": "none", "cameraId": "cam-02"}


@pytest.mark.parametrize("seconds", [14, 181, "60", True, 30.5])
def test_test_length_is_checked(gui, started, seconds):
    assert gui().post("/api/cameras/cam-03/detection/test", {"seconds": seconds})[0] == 400


@pytest.mark.parametrize("camera_id, status", [("cam-09", 404), ("cam-05", 409), ("cam-01", 400)])
def test_test_refuses(gui, started, camera_id, status):
    assert gui().post(f"/api/cameras/{camera_id}/detection/test", {})[0] == status
    assert admin._trigger_tests == {}


# --- analytics (read-only) -----------------------------------------------------------------------

def _simple(**items):
    return NS(SimpleItem=[NS(Name=k, Value=v) for k, v in items.items()])


@pytest.fixture
def analytics(monkeypatch):
    def install(fail=None):
        class Media:
            async def GetVideoAnalyticsConfigurations(self):  # noqa: N802 -- ONVIF's name
                return [NS(Name="VideoAnalyticsConfig",
                           AnalyticsEngineConfiguration=NS(AnalyticsModule=[NS(
                               Name="MyCellMotionModule", Type="tt:CellMotionEngine",
                               Parameters=_simple(Sensitivity="80"))]),
                           RuleEngineConfiguration=NS(Rule=[NS(
                               Name="MyMotionDetectorRule", Type="tt:CellMotionDetector",
                               Parameters=_simple(MinCount="5", ActiveCells="5wACQAAB7AA="))]))]

        class Camera(FakeONVIFCamera):
            async def update_xaddrs(self):
                if fail:
                    raise fail

            async def create_media_service(self):
                return Media()
        monkeypatch.setattr(onvif, "ONVIFCamera", Camera)
    return install


def test_analytics_are_shown_read_only(gui, analytics):
    analytics()
    status, body = gui().get("/api/cameras/cam-02/analytics")
    assert status == 200 and body["writable"] is False
    assert body["module"]["params"] == {"Sensitivity": "80"}
    assert body["activeCells"]["cellCount"] == 64            # 8 bytes of bitmap


def test_analytics_of_an_unreachable_camera(gui, analytics):
    analytics(fail=OSError("timeout"))
    assert gui().get("/api/cameras/cam-02/analytics")[0] == 502


@pytest.mark.parametrize("camera_id", ["cam-01", "cam-09"])
def test_analytics_need_an_onvif_camera(gui, analytics, camera_id):
    analytics()
    assert gui().get(f"/api/cameras/{camera_id}/analytics")[0] == 404
