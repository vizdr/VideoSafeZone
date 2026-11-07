"""vsz-directory-api (DEALER-PANEL-PLAN.md Phase 4, D8): the routes, the invitation, account changes.

vsz-directory is replaced by a recorder (its decisions are tested on PostgreSQL in tests_db/);
Cognito is botocore's own client under a Stubber, so every call is checked against the service
model. What is pinned here: only the token's sub goes to the directory, never its role; each route
becomes the right operation; an invitation Cognito refuses is taken back out of the directory; a
disable signs every session out; every answer has CORS (invoke checks, #8).
"""
import pytest

from fakes import invoke
from loaders import load_lambda

api = load_lambda("vsz_directory_api")

ME = {"sub": "11111111-1111-4111-8111-111111111111", "vsz_role": "system_admin", "email": "me@example.test"}
NEW_SUB = "22222222-2222-4222-8222-222222222222"
USER = "33333333-3333-4333-8333-333333333333"
DEALER = "44444444-4444-4444-8444-444444444444"


@pytest.fixture
def directory(monkeypatch):
    """Records (op, sub, args); answers from `results` (op -> result or Refused)."""
    calls, results = [], {}

    def fake(op, sub, args=None):
        calls.append((op, sub, args or {}))
        r = results.get(op, {"done": op})
        if isinstance(r, api.Refused):
            raise r
        return r
    monkeypatch.setattr(api, "call_directory", fake)
    monkeypatch.setattr(api, "POOL_ID", "eu-central-1_test")
    return calls, results


@pytest.fixture
def route(monkeypatch):
    """invoke() with the proxy path set, as API Gateway sends /directory/{proxy+}."""
    def go(method, path, body=None, query=None, claims=ME):
        original = api.lambda_handler

        def with_path(event, context):
            event["pathParameters"] = {"proxy": path}
            return original(event, context)
        monkeypatch.setattr(api, "lambda_handler", with_path)
        try:
            kwargs = {"body": body, "query": query, "method": method}
            if claims is not None:
                kwargs["claims"] = claims
            return invoke(api, **kwargs)
        finally:
            monkeypatch.setattr(api, "lambda_handler", original)
    return go


# --- who asks, and which operation ------------------------------------------------------------------

def test_without_a_token_nothing_reaches_the_directory(route, directory):
    calls, _ = directory
    assert route("GET", "me", claims=None) == (401, {"error": "no signed-in user"})
    assert calls == []


@pytest.mark.parametrize("method, path, op", [
    ("GET", "me", "me"), ("GET", "overview", "overview"), ("GET", "dealers", "list_dealers"),
    ("GET", "customers", "list_customers"), ("GET", "adapters", "list_adapters"), ("GET", "users", "list_users"),
    ("GET", "pricing", "get_pricing"),
])
def test_each_read_is_one_operation_asked_with_the_sub_only(route, directory, method, path, op):
    calls, _ = directory
    assert route(method, path) == (200, {"done": op})
    assert calls == [(op, ME["sub"], {})]                 # the token's role goes nowhere


def test_writes_carry_the_body_and_the_path(route, directory):
    calls, _ = directory
    assert route("POST", "dealers", {"name": "North"})[0] == 201
    assert route("PATCH", f"dealers/{DEALER}", {"status": "suspended"})[0] == 200
    assert route("PUT", "pricing", {"prices": [], "settings": {}})[0] == 200
    assert route("GET", "customers", query={"dealerId": DEALER, "other": "x"})[0] == 200
    assert [c[0] for c in calls] == ["create_dealer", "update_dealer", "put_pricing", "list_customers"]
    assert calls[1][2] == {"status": "suspended", "id": DEALER}
    assert calls[3][2] == {"dealerId": DEALER}


def test_the_directorys_refusal_is_passed_on(route, directory):
    _, results = directory
    results["list_dealers"] = api.Refused(403, "not allowed for your role")
    assert route("GET", "dealers") == (403, {"error": "not allowed for your role"})


@pytest.mark.parametrize("method, path, status", [
    ("DELETE", "dealers", 405), ("GET", "nothing-here", 404), ("POST", "users/not-an-id/disable", 404),
    ("POST", f"users/{USER}/promote", 404),
])
def test_unknown_routes_and_methods(route, directory, method, path, status):
    assert route(method, path)[0] == status


def test_a_body_that_isnt_json_is_refused(directory):
    status, _ = invoke(api, raw_body="{not json", method="POST", claims=ME)
    assert status == 400


# --- the invitation ---------------------------------------------------------------------------------

INVITE = {"email": " Seller@North.example ", "orgId": DEALER, "role": "dealer"}


def _expect_create(fake, email="Seller@North.example"):
    fake.stub("cognito-idp").add_response(
        "admin_create_user",
        {"User": {"Username": NEW_SUB, "Attributes": [{"Name": "sub", "Value": NEW_SUB}]}},
        {"UserPoolId": "eu-central-1_test", "Username": email, "DesiredDeliveryMediums": ["EMAIL"],
         "UserAttributes": [{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}]})


def test_an_invitation_adds_the_person_creates_the_account_and_links_them(aws, route, directory):
    calls, results = directory
    results["invite_user"] = {"userId": USER}
    fake = aws(api)
    _expect_create(fake)
    assert route("POST", "users", INVITE) == (201, {"userId": USER, "invited": "Seller@North.example"})
    assert [c[0] for c in calls] == ["invite_user", "link_sub"]
    assert calls[1][2] == {"userId": USER, "sub": NEW_SUB}


def test_an_invitation_cognito_refuses_leaves_nothing_behind(aws, route, directory):
    calls, results = directory
    results["invite_user"] = {"userId": USER}
    fake = aws(api)
    fake.stub("cognito-idp").add_client_error("admin_create_user", "UsernameExistsException")
    assert route("POST", "users", INVITE) == (409, {"error": "can't add this address"})
    assert [c[0] for c in calls] == ["invite_user", "cancel_invite"]


def test_the_directory_refusing_an_invitation_never_reaches_cognito(aws, route, directory):
    _, results = directory
    results["invite_user"] = api.Refused(403, "you can't give that role there")
    aws(api)                                              # no Cognito answer queued: a call would fail
    assert route("POST", "users", INVITE) == (403, {"error": "you can't give that role there"})


# --- account changes --------------------------------------------------------------------------------

def test_disable_signs_every_session_out(aws, route, directory):
    calls, results = directory
    results["user_action"] = {"userId": USER, "email": "x@example.test", "sub": NEW_SUB, "status": "disabled"}
    fake = aws(api)
    stub = fake.stub("cognito-idp")
    stub.add_response("admin_disable_user", {}, {"UserPoolId": "eu-central-1_test", "Username": NEW_SUB})
    stub.add_response("admin_user_global_sign_out", {}, {"UserPoolId": "eu-central-1_test", "Username": NEW_SUB})
    assert route("POST", f"users/{USER}/disable") == (200, {"userId": USER, "status": "disabled"})
    assert calls == [("user_action", ME["sub"], {"userId": USER, "action": "disable"})]


@pytest.mark.parametrize("action, cognito_op, extra", [
    ("enable", "admin_enable_user", {}),
    ("reset-password", "admin_reset_user_password", {}),
])
def test_enable_and_reset(aws, route, directory, action, cognito_op, extra):
    _, results = directory
    results["user_action"] = {"userId": USER, "email": "x@example.test", "sub": NEW_SUB, "status": "active"}
    fake = aws(api)
    fake.stub("cognito-idp").add_response(cognito_op, {}, {"UserPoolId": "eu-central-1_test", "Username": NEW_SUB, **extra})
    assert route("POST", f"users/{USER}/{action}")[0] == 200


def test_resend_uses_the_address(aws, route, directory):
    _, results = directory
    results["user_action"] = {"userId": USER, "email": "x@example.test", "sub": None, "status": "invited"}
    fake = aws(api)
    fake.stub("cognito-idp").add_response(
        "admin_create_user", {"User": {"Username": "x"}},
        {"UserPoolId": "eu-central-1_test", "Username": "x@example.test", "MessageAction": "RESEND",
         "DesiredDeliveryMediums": ["EMAIL"]})
    assert route("POST", f"users/{USER}/resend-invite")[0] == 200


def test_a_disable_cognito_refuses_is_still_a_disable(aws, route, directory):
    _, results = directory
    results["user_action"] = {"userId": USER, "email": "x@example.test", "sub": NEW_SUB, "status": "disabled"}
    fake = aws(api)
    fake.stub("cognito-idp").add_client_error("admin_disable_user", "UserNotFoundException")
    status, out = route("POST", f"users/{USER}/disable")
    assert status == 502 and "refused at the next token" in out["error"]
