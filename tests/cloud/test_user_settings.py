"""user-settings: GET/PUT /me/settings, the web panel's per-user settings (WEB-PANEL-PLAN.md D4).

Whose settings is decided by the authorizer's token claims alone; only known settings with valid
values are written, field by field; every answer carries CORS (fakes.invoke checks it, #8).
"""
import pytest

from fakes import FakeTable, invoke
from loaders import load_lambda

user_settings = load_lambda("user_settings")

ALICE = {"sub": "11111111-aaaa", "cognito:username": "alice"}
BOB = {"sub": "22222222-bbbb", "cognito:username": "bob"}


@pytest.fixture
def table(monkeypatch):
    t = FakeTable([{"userId": BOB["sub"], "liveLayout": "16", "largeCells": []}], hash_key="userId")
    monkeypatch.setattr(user_settings, "settings_table", t)
    return t


def test_a_new_user_has_no_settings_yet(table):
    assert invoke(user_settings, method="GET", claims=ALICE) == (200, {"settings": {}})


def test_put_then_get_round_trips_for_the_same_user_only(table):
    body = {"liveLayout": "1+12", "largeCells": ["cam-03"]}
    assert invoke(user_settings, body, method="PUT", claims=ALICE) == (200, {"settings": body})
    assert invoke(user_settings, method="GET", claims=ALICE) == (200, {"settings": body})
    assert invoke(user_settings, method="GET", claims=BOB) == \
        (200, {"settings": {"liveLayout": "16", "largeCells": []}})


def test_the_user_comes_from_the_token_never_the_request(table):
    # A body naming another user is refused as an unknown setting, and Bob's row is untouched.
    status, data = invoke(user_settings, {"userId": BOB["sub"], "liveLayout": "2+6"}, method="PUT", claims=ALICE)
    assert status == 400 and "unknown setting(s): userId" in data["error"]
    assert table.row(BOB["sub"])["liveLayout"] == "16"


def test_no_claims_no_settings(table):
    for claims in (None, {}, {"sub": ""}):
        assert invoke(user_settings, method="GET", claims=claims)[0] == 401
    assert table.writes == []


def test_a_put_writes_only_the_fields_it_names(table):
    invoke(user_settings, {"largeCells": ["cam-01", ""]}, method="PUT", claims=BOB)
    row = table.row(BOB["sub"])
    assert row["liveLayout"] == "16"                      # not named: left as it was
    assert row["largeCells"] == ["cam-01", ""]
    assert "updatedAt" in row
    op, args = table.writes[-1]
    assert op == "update" and args["UpdateExpression"].startswith("SET #largeCells = :largeCells")


@pytest.mark.parametrize("body, phrase", [
    ({}, "give at least one of"),
    (["2+6"], "give at least one of"),
    ({"liveLayout": "4x4"}, "liveLayout must be one of"),
    ({"liveLayout": None}, "liveLayout must be one of"),
    ({"largeCells": "cam-01"}, "largeCells must be a list"),
    ({"largeCells": ["cam-01", "cam-02", "cam-03"]}, "largeCells must be a list"),
    ({"largeCells": ["cam 01"]}, "largeCells must be a list"),
    ({"largeCells": [7]}, "largeCells must be a list"),
    ({"theme": "light"}, "unknown setting(s): theme"),
])
def test_bad_settings_are_refused_and_nothing_is_written(table, body, phrase):
    status, data = invoke(user_settings, body, method="PUT", claims=ALICE)
    assert status == 400 and phrase in data["error"]
    assert table.writes == []


def test_a_body_that_isnt_json_or_is_too_big(table):
    assert invoke(user_settings, raw_body="{nope", method="PUT", claims=ALICE)[0] == 400
    big = '{"liveLayout": "2+6", "x": "' + "y" * 5000 + '"}'
    assert invoke(user_settings, raw_body=big, method="PUT", claims=ALICE)[0] == 413
    assert table.writes == []


def test_other_methods_are_refused(table):
    assert invoke(user_settings, method="DELETE", claims=ALICE)[0] == 405


def test_a_failure_still_answers_with_cors(table, monkeypatch):
    def broken(**kw):
        raise RuntimeError("table unavailable")
    monkeypatch.setattr(table, "get_item", broken)
    assert invoke(user_settings, method="GET", claims=ALICE) == (500, {"error": "table unavailable"})
