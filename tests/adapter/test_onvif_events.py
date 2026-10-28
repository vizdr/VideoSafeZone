"""onvif_events: which detection modes a camera serves, and reading its notifications (PYTEST.md,
step 5).

The sample is cam-02's own GetEventProperties reply, copied from CAM02-CAPABILITIES.md §6 into
tests/fixtures/onvif/. Notifications are built the way zeep hands them over: objects with
attributes, SimpleItems sometimes as dicts.
"""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from lxml import etree

import onvif_events as ev

CAM02_PROPERTIES = Path(__file__).resolve().parent.parent / "fixtures" / "onvif" / \
    "cam02-GetEventProperties.xml"


def _props(xml_path):
    """A GetEventProperties response as zeep returns it: TopicSet._value_1 holds the raw elements."""
    root = etree.parse(str(xml_path)).getroot()
    topic_set = root.find("{http://docs.oasis-open.org/wsn/t-1}TopicSet")
    return NS(TopicSet=NS(_value_1=list(topic_set)))


@pytest.fixture(scope="module")
def cam02_details():
    return ev.topic_details(_props(CAM02_PROPERTIES))


def test_topic_details_of_cam02(cam02_details):
    assert cam02_details == {
        "tns1:RuleEngine/CellMotionDetector/Motion": [("IsMotion", "boolean")],
        "tns1:VideoSource/MotionAlarm": [("State", "boolean")],
        "tns1:Device/Trigger/AlarmIn": [("State", "boolean")],
        "tns1:Device/Trigger/DigitalInput": [("LogicalState", "boolean")],
        "tns1:Device/Trigger/Relay": [("LogicalState", "RelayLogicalState")],
        "tns1:UserAlarm/IVA/HumanShapeDetect": [("State", "boolean")],
    }


def test_source_fields_are_not_data_fields(cam02_details):
    # Only MessageDescription/Data carries the state; Source tokens must not be mistaken for it.
    fields = [name for topic in cam02_details.values() for name, _ in topic]
    assert "VideoSourceConfigurationToken" not in fields


def test_empty_topic_set():
    assert ev.topic_details(NS(TopicSet=NS(_value_1=None))) == {}


def test_cam02_serves_all_three_modes_by_known_name(cam02_details):
    assert ev.detection_map(cam02_details) == {
        "motion": {"topic": "tns1:VideoSource/MotionAlarm", "field": "State", "match": "known"},
        "cellMotion": {"topic": "tns1:RuleEngine/CellMotionDetector/Motion", "field": "IsMotion",
                       "match": "known"},
        "human": {"topic": "tns1:UserAlarm/IVA/HumanShapeDetect", "field": "State",
                  "match": "known"},
    }
    assert ev.supported_modes(ev.detection_map(cam02_details)) == ["motion", "cellMotion", "human"]


def test_another_vendors_people_topic_is_found_by_its_wording():
    details = {"tns1:RuleEngine/PeopleDetector/People": [("IsPeople", "boolean")]}
    assert ev.detection_map(details) == {
        "human": {"topic": "tns1:RuleEngine/PeopleDetector/People", "field": "IsPeople",
                  "match": "wording"}}


def test_cell_motion_wording_is_never_taken_for_plain_motion():
    details = {"tns1:VideoSource/CellMotionAlarm": [("State", "boolean")]}
    assert list(ev.detection_map(details)) == ["cellMotion"]


def test_human_words_win_over_motion_words():
    details = {"tns1:RuleEngine/HumanMotion/Detect": [("State", "boolean")]}
    assert list(ev.detection_map(details)) == ["human"]


def test_a_topic_serves_one_mode_only():
    # The known cell-motion topic is not offered again as "motion" by its wording.
    details = {"tns1:RuleEngine/CellMotionDetector/Motion": [("IsMotion", "boolean")]}
    assert list(ev.detection_map(details)) == ["cellMotion"]


def test_a_topic_without_an_on_off_field_is_skipped():
    details = {"tns1:VideoSource/MotionAlarm": [("Source", "ReferenceToken")],
               "tns1:RuleEngine/MotionRegion/Level": [("Level", "int")]}
    assert ev.detection_map(details) == {}


def test_result_is_in_mode_order_whatever_the_topic_order():
    details = {"tns1:A/People": [("State", "boolean")], "tns1:B/Motion": [("State", "boolean")]}
    assert list(ev.detection_map(details)) == ["motion", "human"]


@pytest.mark.parametrize("fields, expected", [
    ([("Value", "boolean"), ("State", "boolean")], "State"),        # preferred name first
    ([("Foo", "boolean")], "Foo"),                                  # else any on/off field
    ([("LogicalState", "RelayLogicalState")], "LogicalState"),
    ([("State", "string")], None),                                  # a name alone isn't enough
    ([], None),
])
def test_state_field(fields, expected):
    assert ev._state_field(fields) == expected


def test_supported_modes_from_a_plain_topic_set_uses_exact_names():
    assert ev.supported_modes({"tns1:VideoSource/MotionAlarm", "tns1:X/People"}) == ["motion"]


# --- notifications ------------------------------------------------------------------------------

def _note(topic, items, op="Changed"):
    return NS(Topic=NS(_value_1=topic),
              Message=NS(_value_1=NS(PropertyOperation=op, Data=NS(SimpleItem=items))))


def test_parse_notification():
    note = _note("tns1:VideoSource/MotionAlarm", [NS(Name="State", Value="true")])
    assert ev.parse_notification(note) == ("tns1:VideoSource/MotionAlarm", "Changed",
                                           {"State": "true"})


def test_parse_notification_accepts_dict_items():
    note = _note("tns1:X", [{"Name": "IsMotion", "Value": "false"}])
    assert ev.parse_notification(note)[2] == {"IsMotion": "false"}


def test_parse_notification_without_a_topic():
    assert ev.parse_notification(NS()) == (None, None, {})


def test_parse_notification_with_an_unreadable_payload_keeps_the_topic():
    note = NS(Topic=NS(_value_1="tns1:X"), Message=NS(_value_1=NS(PropertyOperation="Initialized")))
    assert ev.parse_notification(note) == ("tns1:X", "Initialized", {})


def test_ff68_state_value_falls_back_to_a_known_field():
    # cam-04 declares MotionAlarm with State and sends IsMotion.
    assert ev.state_value({"IsMotion": "true"}, "State") == "true"


def test_state_value_prefers_the_declared_field():
    assert ev.state_value({"State": "false", "IsMotion": "true"}, "State") == "false"


def test_state_value_none_when_no_state_field():
    assert ev.state_value({"Source": "VideoSourceMain"}, "State") is None


@pytest.mark.parametrize("value", [True, "true", "TRUE", "active", "1", 1])
def test_is_on(value):
    assert ev.is_on(value)


@pytest.mark.parametrize("value", [False, "false", "inactive", "0", 0, None, ""])
def test_is_off(value):
    assert not ev.is_on(value)
