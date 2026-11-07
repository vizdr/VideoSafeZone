"""vsz-directory (DEALER-PANEL-PLAN.md Phase 2): the trigger and the operator operations, offline.

The claims themselves are computed on a real PostgreSQL in tests_db/test_directory.py. Here the
decisions around them: what Cognito gets back, that a refusal is an exception (the only way a
trigger stops a sign-in), which events count as a first sign-in, that only the operator's
operations exist yet, and that a dropped connection is replaced once while an SQL error isn't.
"""
import pytest

from loaders import load_lambda

directory = load_lambda("vsz_directory")


def cognito_event(source="TokenGeneration_Authentication", **attrs):
    return {"version": "1", "triggerSource": source, "userPoolId": "eu-central-1_test",
            "request": {"userAttributes": {"sub": "s-1", "email": "a@example.test",
                                           "email_verified": "true", **attrs}},
            "response": {"claimsOverrideDetails": None}}


@pytest.fixture
def claims(monkeypatch):
    """Replaces the database: token_claims answers what the test says; calls are recorded."""
    calls, answer = [], {"value": {"vsz_role": "customer", "vsz_adapters": "adapter-01"}}

    def fake_token_claims(con, sub, email, verified, first):
        calls.append({"sub": sub, "email": email, "verified": verified, "first": first})
        return answer["value"]
    monkeypatch.setattr(directory, "token_claims", fake_token_claims)
    monkeypatch.setattr(directory, "with_db", lambda work: work(object()))
    return calls, answer


# --- the pre-token trigger ---------------------------------------------------------------------------

def test_the_claims_go_into_the_id_token(claims):
    out = directory.lambda_handler(cognito_event(), None)
    assert out["response"]["claimsOverrideDetails"] == {
        "claimsToAddOrOverride": {"vsz_role": "customer", "vsz_adapters": "adapter-01"}}
    assert out["triggerSource"] == "TokenGeneration_Authentication"          # the event goes back whole


def test_no_claims_means_no_sign_in(claims):
    calls, answer = claims
    answer["value"] = None
    with pytest.raises(directory.DirectoryError, match="can't sign in"):
        directory.lambda_handler(cognito_event(), None)


@pytest.mark.parametrize("source, first", [
    ("TokenGeneration_Authentication", True),
    ("TokenGeneration_NewPasswordChallenge", True),
    ("TokenGeneration_RefreshTokens", False),
    ("TokenGeneration_AuthenticateDevice", False),
])
def test_only_a_real_sign_in_counts_as_one(claims, source, first):
    calls, _ = claims
    directory.lambda_handler(cognito_event(source), None)
    assert calls[-1]["first"] is first


def test_an_unverified_email_is_passed_as_unverified(claims):
    calls, _ = claims
    directory.lambda_handler(cognito_event(email_verified="false"), None)
    assert calls[-1] == {"sub": "s-1", "email": "a@example.test", "verified": False, "first": True}


# --- operator operations ----------------------------------------------------------------------------

@pytest.mark.parametrize("event", [{"op": "ping"}, {"op": "ping", "actor": {"kind": "dealer"}}, None, "ping"])
def test_only_the_operator_has_operations_yet(event, monkeypatch):
    monkeypatch.setattr(directory, "with_db", lambda work: pytest.fail("reached the database"))
    assert directory.lambda_handler(event, None) == {
        "ok": False, "error": "only the operator's operations exist yet"}


def test_operations_are_named(monkeypatch):
    monkeypatch.setattr(directory, "with_db", lambda work: pytest.fail("reached the database"))
    operator = {"kind": "operator"}
    assert directory.lambda_handler({"op": "drop", "actor": operator}, None)["error"] == \
        "op must be ping, claims_for or operator_seed"
    assert directory.lambda_handler({"op": "claims_for", "actor": operator}, None)["error"] == "give email"


def test_the_seed_runs_in_one_transaction(monkeypatch):
    class Con:
        def __init__(self):
            self.sent = []

        def run(self, sql, **params):
            self.sent.append(sql)
            return []
    con = Con()
    monkeypatch.setattr(directory, "with_db", lambda work: work(con))
    monkeypatch.setattr(directory, "operator_seed", lambda c, spec: (c.run("-- the seed's work"), ["x"])[1])
    assert directory.lambda_handler({"op": "operator_seed", "actor": {"kind": "operator"}, "spec": {}}, None) == \
        {"ok": True, "done": ["x"]}
    assert con.sent == ["BEGIN", "-- the seed's work", "COMMIT"]


def test_a_failed_seed_is_rolled_back_and_reported():
    class Con:
        sent = []

        def run(self, sql, **params):
            self.sent.append(sql)
            if sql.startswith("INSERT"):
                raise Exception({"C": "23514", "M": "staff hold exactly one membership"})
            return []
    con = Con()
    with pytest.raises(Exception):
        directory.in_transaction(con, lambda c: c.run("INSERT INTO vsz.membership ..."))
    assert con.sent[-1] == "ROLLBACK"
    assert directory.sql_message(Exception({"C": "23514", "M": "staff hold exactly one membership"})) == \
        "staff hold exactly one membership (SQLSTATE 23514)"


# --- the warm connection --------------------------------------------------------------------------------

class InterfaceError(Exception):
    """pg8000's name for a connection that dropped."""


def test_a_dropped_connection_is_replaced_once(monkeypatch):
    made = []

    class Con:
        def __init__(self):
            made.append(self)

        def close(self):
            pass
    monkeypatch.setattr(directory, "connect", lambda user, db, password=None: Con())
    monkeypatch.setattr(directory, "_con", None)
    attempts = []

    def work(con):
        attempts.append(con)
        if len(attempts) == 1:
            raise InterfaceError("network error")
        return "answer"
    assert directory.with_db(work) == "answer"
    assert len(made) == 2 and attempts == made


def test_an_sql_error_is_not_retried(monkeypatch):
    monkeypatch.setattr(directory, "connect", lambda user, db, password=None: object())
    monkeypatch.setattr(directory, "_con", None)
    attempts = []

    def work(con):
        attempts.append(con)
        raise Exception({"C": "42501", "M": "permission denied"})
    with pytest.raises(Exception):
        directory.with_db(work)
    assert len(attempts) == 1


def test_it_logs_in_as_vsz_app_with_an_iam_token(monkeypatch):
    seen = []
    monkeypatch.setattr(directory, "connect", lambda user, db, password=None: seen.append((user, db, password)) or object())
    monkeypatch.setattr(directory, "_con", None)
    directory.with_db(lambda con: None)
    assert seen == [("vsz_app", "videosafezone", None)]


def test_ff90_the_refusal_leaves_the_full_stop_to_cognito():
    """FoundAndFixed.md #90: Cognito appends "." to a trigger's message; ours ended with one too."""
    assert directory.REFUSED.endswith("account") and not directory.REFUSED.endswith(".")

