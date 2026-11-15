"""This Pi's camera rows, read through the cameras table's adapterId-index (DEALER-PANEL-PLAN.md D13,
Phase 10.2c; adapter/registry_reads.py).

What the index returns must be exactly what camera_control.is_mine keeps of the whole table -- the
readers move from a scan filtered by is_mine to this, and a row lost or gained in the move would be a
camera left alone or one acted on from the wrong Pi. The fixture's Thing is test-thing; the fixture's
ADOPT_UNSTAMPED_ROWS=yes makes this Pi the adopting one.
"""
import json
import pathlib

import camera_control
import config
import pytest
import registry_reads
from botocore.exceptions import ClientError

ROWS = [
    {"cameraId": "cam-05", "adapterId": "test-thing", "mode": "passthrough", "recordingMode": "motion"},
    {"cameraId": "cam-02", "adapterId": "test-thing", "mode": "transcode", "recordingMode": "manual"},
    {"cameraId": "cam-03", "mode": "passthrough", "recordingMode": "manual"},          # unstamped
    {"cameraId": "cam-07", "adapterId": "adapter-02", "mode": "passthrough"},          # another adapter's
    {"cameraId": "cam-08", "releasedFrom": "test-thing", "mode": "passthrough"},       # released
    {"cameraId": "cam-09", "adapterId": "test-thing", "mode": "passthrough", "unregisteredAt": "2026-11-01"},
]


def refused(op):
    return ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}}, op)


def leading_keys(monkeypatch, table):
    """IAM's LeadingKeys condition on the index, as the role has it: this Thing's rows only."""
    real = table.query

    def query(**kw):
        if kw["KeyConditionExpression"].get_expression()["values"][1] != config.THING_NAME:
            raise refused("Query")
        return real(**kw)
    monkeypatch.setattr(table, "query", query)
    return real


@pytest.mark.parametrize("adopting", [True, False])
@pytest.mark.parametrize("page_size", [None, 1])
def test_own_rows_are_what_is_mine_keeps(registry, monkeypatch, adopting, page_size):
    monkeypatch.setattr(config, "ADOPT_UNSTAMPED_ROWS", adopting)
    registry(ROWS, page_size=page_size)
    want = sorted(r["cameraId"] for r in ROWS if camera_control.is_mine(r))
    assert [r["cameraId"] for r in registry_reads.own_rows()] == want
    assert want == (["cam-02", "cam-03", "cam-05", "cam-09"] if adopting else ["cam-02", "cam-05", "cam-09"])


def test_rows_come_whole_and_unregistered_ones_too(registry):
    # Readers filter with acts_on themselves; the unregistered row is still this Pi's (Re-register).
    registry(ROWS)
    rows = {r["cameraId"]: r for r in registry_reads.own_rows()}
    assert rows["cam-05"] == ROWS[0] and rows["cam-03"] == ROWS[2]
    assert "unregisteredAt" in rows["cam-09"]


def test_only_the_fields_asked_for_reserved_words_too(registry):
    registry(ROWS)
    rows = registry_reads.own_rows(["mode", "recordingMode"])
    assert rows[0] == {"cameraId": "cam-02", "adapterId": "test-thing", "mode": "transcode", "recordingMode": "manual"}
    assert {k for r in rows for k in r} <= {*registry_reads.ACTS_ON_FIELDS, "mode", "recordingMode"}


@pytest.mark.parametrize("adopting", [True, False])
def test_ff96_rows_read_by_field_still_say_whose_they_are(registry, monkeypatch, adopting):
    # FoundAndFixed.md #96. Every reader filters what it reads with camera_control.acts_on. Without adapterId in the row it
    # looks unstamped: the adopting Pi would keep it by luck, any other Pi would drop all its cameras;
    # without unregisteredAt an unregistered camera (cam-09) would be acted on.
    monkeypatch.setattr(config, "ADOPT_UNSTAMPED_ROWS", adopting)
    registry(ROWS)
    rows = registry_reads.own_rows(["outageBufferSec"])
    assert [r["cameraId"] for r in rows if camera_control.acts_on(r)] == \
        sorted(r["cameraId"] for r in ROWS if camera_control.acts_on(r))


def test_a_pi_that_doesnt_adopt_never_scans(registry, monkeypatch):
    monkeypatch.setattr(config, "ADOPT_UNSTAMPED_ROWS", False)
    table = registry(ROWS)
    monkeypatch.setattr(table, "scan", lambda **kw: pytest.fail("scanned the whole table"))
    assert [r["cameraId"] for r in registry_reads.own_rows()] == ["cam-02", "cam-05", "cam-09"]


def test_the_index_is_queried_for_this_thing_only(registry, monkeypatch):
    table = registry(ROWS)
    asked = []
    real = table.query
    monkeypatch.setattr(table, "query", lambda **kw: asked.append(kw) or real(**kw))
    registry_reads.own_rows()
    assert [(kw["IndexName"], kw["KeyConditionExpression"].get_expression()["values"][1]) for kw in asked] == \
        [("adapterId-index", "test-thing")]


def test_a_refused_scan_reads_as_no_unstamped_rows(registry, monkeypatch):
    # Once the role is narrowed (10.2f) the adopting Pi may no longer scan; by then nothing is left to adopt.
    table = registry(ROWS)

    def scan(**kw):
        raise refused("Scan")
    monkeypatch.setattr(table, "scan", scan)
    assert registry_reads.unstamped_rows() == []
    assert [r["cameraId"] for r in registry_reads.own_rows()] == ["cam-02", "cam-05", "cam-09"]


def test_any_other_scan_error_is_raised(registry, monkeypatch):
    table = registry(ROWS)

    def scan(**kw):
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow"}}, "Scan")
    monkeypatch.setattr(table, "scan", scan)
    with pytest.raises(ClientError):
        registry_reads.unstamped_rows()


def test_unstamped_rows_skip_released_and_owned(registry):
    registry(ROWS)
    assert [r["cameraId"] for r in registry_reads.unstamped_rows(["mode"])] == ["cam-03"]


@pytest.mark.parametrize("camera_id, found", [("cam-05", True), ("cam-03", True), ("cam-07", False),
                                              ("cam-08", False), ("cam-42", False)])
def test_own_row_answers_another_adapters_camera_as_unknown(registry, camera_id, found):
    registry(ROWS)
    row = registry_reads.own_row(camera_id)
    assert (row is not None) is found
    if found:
        assert row == next(r for r in ROWS if r["cameraId"] == camera_id)


def test_own_row_never_scans(registry, monkeypatch):
    table = registry(ROWS)
    monkeypatch.setattr(table, "scan", lambda **kw: pytest.fail("scanned the whole table for one row"))
    assert registry_reads.own_row("cam-03")["cameraId"] == "cam-03"
    assert registry_reads.own_row("cam-05")["cameraId"] == "cam-05"


def test_a_pi_that_doesnt_adopt_reads_one_row_by_the_index_alone(registry, monkeypatch):
    monkeypatch.setattr(config, "ADOPT_UNSTAMPED_ROWS", False)
    table = registry(ROWS)
    monkeypatch.setattr(table, "get_item", lambda **kw: pytest.fail("read a row by its key"))
    assert registry_reads.own_row("cam-05")["cameraId"] == "cam-05"
    assert registry_reads.own_row("cam-03") is None and registry_reads.own_row("cam-07") is None


def test_own_row_finds_its_own_row_the_index_hasnt_caught_up_with(registry, monkeypatch):
    # A GSI is eventually consistent: right after a registration the row may be in the table only.
    table = registry(ROWS + [{"cameraId": "cam-10", "adapterId": "test-thing", "mode": "passthrough"}])
    real = table.query
    monkeypatch.setattr(table, "query", lambda **kw: {**real(**kw), "Items": [
        r for r in real(**kw)["Items"] if r["cameraId"] != "cam-10"]})
    assert registry_reads.own_row("cam-10")["cameraId"] == "cam-10"
    assert registry_reads.own_row("cam-07") is None                     # the key read keeps the owner check


def test_a_refused_key_read_reads_as_unknown(registry, monkeypatch):
    table = registry(ROWS)

    def get_item(**kw):
        raise refused("GetItem")
    monkeypatch.setattr(table, "get_item", get_item)
    assert registry_reads.own_row("cam-03") is None
    assert registry_reads.own_row("cam-05")["cameraId"] == "cam-05"      # the index still answers

    def get_item(**kw):
        raise ClientError({"Error": {"Code": "InternalServerError", "Message": "x"}}, "GetItem")
    monkeypatch.setattr(table, "get_item", get_item)
    with pytest.raises(ClientError):
        registry_reads.own_row("cam-03")


def test_the_role_may_query_this_index_for_its_own_thing_only():
    # The policy KVSAdapterRole gets (provision.sh adapter-index) names the index this module reads.
    root = pathlib.Path(__file__).resolve().parents[2]
    policy = json.loads((root / "cloud/iam/kvs-adapter-own-rows-policy.json").read_text())
    [statement] = policy["Statement"]
    assert statement["Action"] == "dynamodb:Query"
    assert statement["Resource"].endswith(f":table/{registry_reads.TABLE}/index/{registry_reads.INDEX}")
    assert statement["Condition"] == {"ForAllValues:StringEquals": {
        "dynamodb:LeadingKeys": ["${credentials-iot:ThingName}"]}}


def test_check_passes_and_fails_as_it_should(registry, monkeypatch, capsys):
    table = registry(ROWS)
    real = leading_keys(monkeypatch, table)
    assert registry_reads.check() == 0
    out = capsys.readouterr().out
    assert out.count("PASS") == 3 and "cam-02, cam-05, cam-09" in out

    monkeypatch.setattr(table, "query", real)            # a role that answers any Thing's rows
    assert registry_reads.check() == 1
    assert "FAIL  IAM refuses adapter-99's rows" in capsys.readouterr().out


def test_check_skips_the_comparison_once_the_role_cant_scan(registry, monkeypatch, capsys):
    table = registry(ROWS)
    leading_keys(monkeypatch, table)

    def scan(**kw):
        raise refused("Scan")
    monkeypatch.setattr(table, "scan", scan)
    assert registry_reads.check() == 0
    assert "SKIP  the index agrees with the table" in capsys.readouterr().out


def test_check_fails_before_the_index_exists(registry, monkeypatch, capsys):
    # Refused for another reason than IAM's isn't a refusal by IAM.
    table = registry(ROWS)

    def query(**kw):
        raise ClientError({"Error": {"Code": "ValidationException", "Message": "no such index"}}, "Query")
    monkeypatch.setattr(table, "query", query)
    assert registry_reads.check() == 3
    out = capsys.readouterr().out
    assert "FAIL  adapterId-index lists test-thing's rows: ValidationException" in out
    assert "FAIL  IAM refuses adapter-99's rows to this Pi: ValidationException" in out
    assert "FAIL  own_rows() is what is_mine keeps of the table: ValidationException" in out
