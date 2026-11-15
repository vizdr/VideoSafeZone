"""vsz-directory-api (DEALER-PANEL-PLAN.md Phase 4, D8): the routes, the invitation, account changes.

vsz-directory is replaced by a recorder (its decisions are tested on PostgreSQL in tests_db/);
Cognito is botocore's own client under a Stubber, so every call is checked against the service
model. What is pinned here: only the token's sub goes to the directory, never its role; each route
becomes the right operation; an invitation Cognito refuses is taken back out of the directory; a
disable signs every session out; every answer has CORS (invoke checks, #8).
"""
import json

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
    def go(method, path, body=None, query=None, claims=ME, source_ip="203.0.113.7"):
        original = api.lambda_handler

        def with_path(event, context):
            event["pathParameters"] = {"proxy": path}
            event.setdefault("requestContext", {})["identity"] = {"sourceIp": source_ip}   # API Gateway's
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
    ("GET", "adapters", "list_adapters"), ("GET", "users", "list_users"), ("GET", "pricing", "get_pricing"),
    ("GET", "dealer", "get_dealer"),
])
def test_each_read_is_one_operation_asked_with_the_sub_only(route, directory, method, path, op):
    calls, _ = directory
    assert route(method, path) == (200, {"done": op})
    assert calls == [(op, ME["sub"], {})]                 # the token's role goes nowhere


def test_writes_carry_the_body_and_the_path(aws, route, directory):
    calls, results = directory
    results["list_customers"] = []
    aws(api)
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
    ("POST", f"users/{USER}/promote", 404), ("DELETE", f"customers/{DEALER}", 405), ("GET", "adapters/x/plans", 404),
    ("PUT", "dealer", 405),
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


# --- the dealer portal (Phase 5) --------------------------------------------------------------------

CUSTOMER = "55555555-5555-4555-8555-555555555555"
ADAPTER = "66666666-6666-4666-8666-666666666666"


@pytest.mark.parametrize("method, path, body, op, args", [
    ("GET", f"customers/{CUSTOMER}", None, "get_customer", {"id": CUSTOMER}),
    ("PATCH", f"customers/{CUSTOMER}", {"status": "suspended"}, "update_customer", {"status": "suspended", "id": CUSTOMER}),
    ("GET", f"customers/{CUSTOMER}/users", None, "customer_users", {"id": CUSTOMER}),
    ("PUT", f"adapters/{ADAPTER}/plan", {"lines": [], "retentionDays": 7, "interval": "monthly", "basedOn": 1},
     "put_plan", {"lines": [], "retentionDays": 7, "interval": "monthly", "basedOn": 1, "adapterId": ADAPTER}),
    ("POST", f"adapters/{ADAPTER}/plan/cancel", {"basedOn": 2}, "cancel_plan", {"basedOn": 2, "adapterId": ADAPTER}),
    ("GET", f"adapters/{ADAPTER}/plans", None, "plan_history", {"adapterId": ADAPTER}),
    ("PATCH", "dealer", {"phone": "1"}, "update_own_dealer", {"phone": "1"}),
])
def test_the_portals_routes_are_operations_with_their_path(route, directory, method, path, body, op, args):
    calls, _ = directory
    assert route(method, path, body)[0] == 200
    assert calls == [(op, ME["sub"], args)]


def test_a_path_id_wins_over_one_in_the_body(route, directory):
    calls, _ = directory
    route("PUT", f"adapters/{ADAPTER}/plan", {"adapterId": "someone-elses", "lines": []})
    assert calls[0][2]["adapterId"] == ADAPTER


REGISTRY = [
    {"cameraId": "cam-01", "adapterId": "adapter-01", "recordingMode": "manual", "rtspUrl": "rtsp://u:secret@x/1"},
    {"cameraId": "cam-02", "adapterId": "adapter-01", "recordSize": "1920x1080", "recordingMode": "motion",
     "displayName": "Gate", "onvifPassword": "secret", "videoCodec": "h265"},
    {"cameraId": "cam-03", "adapterId": "adapter-01", "recordSize": "2560x1440", "recordingMode": "human",
     "liveRtspUrl": "rtsp://u:secret@x/2", "videoCodecDefault": "h264"},
    {"cameraId": "cam-04", "adapterId": "adapter-01", "unregisteredAt": "2026-11-01T00:00:00Z"},
    {"cameraId": "cam-05", "adapterId": "adapter-02", "recordSize": "3840x2160"},
    {"cameraId": "cam-06"},                                             # no adapterId: an administrator's
]


def test_the_customer_list_counts_the_cameras_on_each_customers_adapters(aws, route, directory):
    _, results = directory
    results["list_customers"] = [{"name": "A", "things": ["adapter-01"]}, {"name": "B", "things": ["adapter-02", "adapter-09"]},
                                 {"name": "C", "things": []}]
    aws(api, cameras=REGISTRY, page_size=2)                             # pages: every one must be read
    status, out = route("GET", "customers")
    assert status == 200
    assert [(c["name"], c["registered"]) for c in out] == [("A", 3), ("B", 1), ("C", 0)]


def test_without_the_registry_the_customer_list_stands(monkeypatch, route, directory):
    _, results = directory
    results["list_customers"] = [{"name": "A", "things": ["adapter-01"]}]

    def denied():
        raise api.ClientError({"Error": {"Code": "AccessDeniedException"}}, "Scan")
    monkeypatch.setattr(api, "registry_cameras", denied)
    assert route("GET", "customers") == (200, [{"name": "A", "things": ["adapter-01"], "registered": None}])


def _adapters_answer():
    return {"customer": {"id": CUSTOMER, "name": "Acme"}, "total": {"adapters": 2},
            "adapters": [{"thingName": "adapter-01", "plan": None}, {"thingName": "adapter-07", "plan": None}]}


def test_each_adapter_gets_its_cameras_and_plan_vs_actual_without_a_credential(aws, monkeypatch, route, directory):
    from datetime import datetime, timezone
    _, results = directory
    results["customer_adapters"] = _adapters_answer()
    monkeypatch.setattr(api, "THUMB_BUCKET", "evidence")
    fake = aws(api, cameras=REGISTRY)
    s3 = fake.stub("s3")
    # Under the camera's adapter (DEALER-PANEL-PLAN.md Phase 10.2e/f); a row without one has none.
    assert api.thumbnail_keys("cam-02", "adapter-01") == ["thumbs/adapter-01/cam-02.jpg"]
    assert api.thumbnail_keys("cam-02", None) == []
    s3.add_response("head_object", {"LastModified": datetime(2026, 11, 7, 9, 30, tzinfo=timezone.utc)},
                    {"Bucket": "evidence", "Key": "thumbs/adapter-01/cam-01.jpg"})
    s3.add_client_error("head_object", "404", expected_params={"Bucket": "evidence", "Key": "thumbs/adapter-01/cam-02.jpg"})
    s3.add_response("head_object", {"LastModified": datetime(2026, 11, 7, 9, 31, tzinfo=timezone.utc)},
                    {"Bucket": "evidence", "Key": "thumbs/adapter-01/cam-03.jpg"})
    status, out = route("GET", f"customers/{CUSTOMER}/adapters")
    assert status == 200 and "registryError" not in out
    first, second = out["adapters"]
    assert first["cameras"] == [
        {"id": "cam-01", "name": None, "codec": "h264", "size": None, "class": None, "recordingMode": "manual",
         "recording": "continuous", "thumbnailAt": "2026-11-07T09:30:00Z"},
        {"id": "cam-02", "name": "Gate", "codec": "h265", "size": "1920x1080", "class": "2mp", "recordingMode": "motion",
         "recording": "motion", "thumbnailAt": None},
        {"id": "cam-03", "name": None, "codec": "h264", "size": "2560x1440", "class": "4mp", "recordingMode": "human",
         "recording": "motion", "thumbnailAt": "2026-11-07T09:31:00Z"},
    ]
    assert first["actual"] == {"total": 3, "byClass": {"2mp/motion": 1, "4mp/motion": 1}, "unclassified": 1}
    assert (second["cameras"], second["actual"]) == ([], {"total": 0, "byClass": {}, "unclassified": 0})
    assert "secret" not in str(out)


def test_an_unreadable_registry_leaves_the_plans_and_says_so(monkeypatch, route, directory):
    _, results = directory
    results["customer_adapters"] = _adapters_answer()

    def denied():
        raise api.ClientError({"Error": {"Code": "AccessDeniedException"}}, "Scan")
    monkeypatch.setattr(api, "registry_cameras", denied)
    status, out = route("GET", f"customers/{CUSTOMER}/adapters")
    assert status == 200 and "AccessDeniedException" in out["registryError"]
    assert [(a["cameras"], a["actual"]) for a in out["adapters"]] == [(None, None), (None, None)]


@pytest.mark.parametrize("size, cls", [
    ("1280x720", "2mp"), ("1920x1080", "2mp"), ("2560x1440", "4mp"), ("2688x1520", "4mp"), ("3840x2160", "8mp"),
    (None, None), ("", None), ("big", None),
])
def test_a_picture_size_is_a_camera_class(size, cls):
    assert api.size_class(size) == cls


@pytest.mark.parametrize("mode, recording", [
    (None, "continuous"), ("manual", "continuous"), ("motion", "motion"), ("cellMotion", "motion"), ("human", "motion"),
])
def test_a_recording_mode_is_motion_or_continuous(mode, recording):
    assert api.recording_class(mode) == recording


NEW_CUSTOMER = {"name": "Acme", "site": {"name": "Shop", "timezone": "Europe/Berlin"}}


def test_a_new_customer_and_its_holders_invitation(aws, route, directory):
    calls, results = directory
    results["create_customer"] = {"id": CUSTOMER, "siteId": "s-1"}
    results["invite_user"] = {"userId": USER}
    fake = aws(api)
    _expect_create(fake, "boss@acme.example")
    status, out = route("POST", "customers", {**NEW_CUSTOMER, "holder": {"email": "boss@acme.example", "name": "Boss"}})
    assert (status, out) == (201, {"id": CUSTOMER, "siteId": "s-1", "holder": {"userId": USER, "invited": "boss@acme.example"}})
    assert [c[0] for c in calls] == ["create_customer", "invite_user", "link_sub"]
    assert calls[0][2] == NEW_CUSTOMER                                  # the holder isn't the directory's business here
    assert calls[1][2] == {"email": "boss@acme.example", "name": "Boss", "orgId": CUSTOMER, "role": "customer"}


def test_a_refused_holder_invitation_leaves_the_customer_and_says_why(aws, route, directory):
    calls, results = directory
    results["create_customer"] = {"id": CUSTOMER, "siteId": "s-1"}
    results["invite_user"] = {"userId": USER}
    fake = aws(api)
    fake.stub("cognito-idp").add_client_error("admin_create_user", "UsernameExistsException")
    status, out = route("POST", "customers", {**NEW_CUSTOMER, "holder": {"email": "taken@example.test"}})
    assert (status, out) == (201, {"id": CUSTOMER, "siteId": "s-1", "holderError": "can't add this address"})
    assert [c[0] for c in calls] == ["create_customer", "invite_user", "cancel_invite"]


@pytest.mark.parametrize("holder", [None, {}, {"email": "  "}])
def test_no_holder_no_invitation(route, directory, holder):
    calls, results = directory
    results["create_customer"] = {"id": CUSTOMER, "siteId": "s-1"}
    body = dict(NEW_CUSTOMER, **({"holder": holder} if holder is not None else {}))
    assert route("POST", "customers", body) == (201, {"id": CUSTOMER, "siteId": "s-1"})
    assert [c[0] for c in calls] == ["create_customer"]


def test_the_registry_scan_never_reads_a_credential(aws, monkeypatch):
    """A second guard behind inventory()'s own field list: the scan asks DynamoDB for the portal's
    fields only, so a camera's address, user and password never reach this function at all."""
    fake = aws(api, cameras=REGISTRY, page_size=4)
    table, seen = fake.tables["cameras"], []
    original = table.scan
    monkeypatch.setattr(table, "scan", lambda **kw: seen.append(kw) or original(**kw))
    rows = api.registry_cameras()
    fields = {f.strip() for f in api.REGISTRY_FIELDS.split(",")}
    assert len(seen) == 2 and all(kw["ProjectionExpression"] == api.REGISTRY_FIELDS for kw in seen)
    assert not fields & {"rtspUrl", "liveRtspUrl", "onvifPassword", "onvifUser", "onvifHost", "webUrl"}
    assert rows and all(set(r) <= fields for r in rows)
    assert "cam-04" not in {r["cameraId"] for r in rows}                # unregistered: left out


# --- adapters (Phase 6, D15) ------------------------------------------------------------------------

@pytest.fixture
def told(monkeypatch):
    """Records each retained config the API publishes, as (thing, activated)."""
    sent = []
    monkeypatch.setattr(api, "publish_config", lambda thing, activated: sent.append((thing, activated)))
    return sent


@pytest.mark.parametrize("path, op, args", [
    (f"adapters/{ADAPTER}/allocate", "allocate_adapter", {"dealerId": DEALER, "id": ADAPTER}),
    (f"adapters/{ADAPTER}/new-code", "new_code", {"id": ADAPTER}),
])
def test_allocation_and_a_new_code_are_directory_operations(route, directory, path, op, args):
    calls, _ = directory
    assert route("POST", path, {"dealerId": DEALER} if "allocate" in path else None)[0] == 200
    assert calls == [(op, ME["sub"], args)]


def test_new_adapters_are_told_they_arent_activated_and_the_identity_command_is_given(route, directory, told):
    calls, results = directory
    results["generate_adapters"] = [{"id": "a-2", "thingName": "adapter-02", "cloudId": "K7QM4-XRPZ9"},
                                    {"id": "a-3", "thingName": "adapter-03", "cloudId": "QQQQQ-RRRRR"}]
    status, out = route("POST", "adapters", {"count": 2, "label": "Box"})
    assert status == 201 and calls[0] == ("generate_adapters", ME["sub"], {"count": 2, "label": "Box"})
    assert out["identityCommand"] == "bash cloud/adapter-identity.sh create adapter-02 adapter-03"
    assert [a["cloudId"] for a in out["adapters"]] == ["K7QM4-XRPZ9", "QQQQQ-RRRRR"]
    assert told == [("adapter-02", False), ("adapter-03", False)] and "configUnsent" not in out


def test_the_retained_config_is_published_as_the_pi_reads_it(aws):
    from botocore.stub import ANY
    fake = aws(api)
    fake.stub("iot-data").add_response("publish", {}, {"topic": "adapter/adapter-02/config", "qos": 1,
                                                       "retain": True, "payload": ANY})
    api.publish_config("adapter-02", True)


def test_an_activation_takes_its_address_from_api_gateway_never_the_body(aws, route, directory, told):
    calls, results = directory
    results["activate_adapter"] = {"adapterId": ADAPTER, "thingName": "adapter-02", "customerId": CUSTOMER,
                                   "siteId": "s-1", "activationId": "act-9"}
    fake = aws(api, cameras=[{"cameraId": "cam-20", "adapterId": "adapter-02"}, {"cameraId": "cam-01", "adapterId": "adapter-01"}])
    status, out = route("POST", "adapters/activate", {"cloudId": "k7qm4-xrpz9", "newSite": {"name": "Garage"},
                                                      "sourceIp": "10.0.0.1"}, source_ip="198.51.100.4")
    assert status == 200 and calls[0][2]["sourceIp"] == "198.51.100.4"
    assert out["camerasReleased"] == 1                                    # a leftover from before: out of view
    assert fake.tables["cameras"].row("cam-20") == {"cameraId": "cam-20", "releasedFrom": "adapter-02"}
    assert fake.tables["cameras"].row("cam-01") == {"cameraId": "cam-01", "adapterId": "adapter-01"}
    assert told == [("adapter-02", True)]


def test_a_refused_activation_tells_nobody(route, directory, told):
    _, results = directory
    results["activate_adapter"] = api.Refused(400, "this Cloud ID can't be activated")
    assert route("POST", "adapters/activate", {"cloudId": "x"}) == (400, {"error": "this Cloud ID can't be activated"})
    assert told == []


@pytest.mark.parametrize("path, op, result", [
    ("release", "release_adapter", {"id": ADAPTER, "thingName": "adapter-02", "cloudId": "NEWCO-DE123"}),
    ("remove", "remove_adapter", {"id": ADAPTER, "thingName": "adapter-02"}),
])
def test_an_adapter_leaving_its_customer_takes_its_cameras_out_of_view(aws, route, directory, told, path, op, result):
    _, results = directory
    results[op] = result
    fake = aws(api, cameras=[{"cameraId": "cam-20", "adapterId": "adapter-02", "displayName": "Gate"},
                             {"cameraId": "cam-21", "adapterId": "adapter-02"},
                             {"cameraId": "cam-01", "adapterId": "adapter-01"}], page_size=1)
    status, out = route("POST", f"adapters/{ADAPTER}/{path}")
    assert status == 200 and out["camerasReleased"] == 2
    assert fake.tables["cameras"].row("cam-20") == {"cameraId": "cam-20", "releasedFrom": "adapter-02", "displayName": "Gate"}
    assert fake.tables["cameras"].row("cam-01")["adapterId"] == "adapter-01"
    assert told == [("adapter-02", False)]


def test_a_camera_moved_meanwhile_isnt_released(aws, monkeypatch, route, directory, told):
    _, results = directory
    results["release_adapter"] = {"id": ADAPTER, "thingName": "adapter-02", "cloudId": "X"}
    fake = aws(api, cameras=[{"cameraId": "cam-20", "adapterId": "adapter-02"}])
    table = fake.tables["cameras"]
    scan = table.scan

    def scan_then_move(**kwargs):            # rematched to another adapter between the scan and the write
        page = scan(**kwargs)
        table.items[0]["adapterId"] = "adapter-03"
        return page
    monkeypatch.setattr(table, "scan", scan_then_move)
    assert route("POST", f"adapters/{ADAPTER}/release")[1]["camerasReleased"] == 0
    assert table.items[0] == {"cameraId": "cam-20", "adapterId": "adapter-03"}     # the write's condition kept it


def test_a_registry_or_iot_failure_is_reported_and_the_directory_stands(monkeypatch, route, directory):
    _, results = directory
    results["release_adapter"] = {"id": ADAPTER, "thingName": "adapter-02", "cloudId": "NEWCO-DE123"}

    def broken(*args):
        raise api.ClientError({"Error": {"Code": "AccessDeniedException"}}, "x")
    monkeypatch.setattr(api, "release_cameras", broken)
    monkeypatch.setattr(api, "publish_config", broken)
    status, out = route("POST", f"adapters/{ADAPTER}/release")
    assert status == 200 and out["cloudId"] == "NEWCO-DE123"
    assert "sync it" in out["registryError"] and "sync it" in out["configUnsent"]


def test_a_revoke_points_at_the_owners_script_for_the_certificate(monkeypatch, route, directory, told):
    _, results = directory
    results["revoke_adapter"] = {"id": ADAPTER, "thingName": "adapter-02", "wasActivated": False, "identity": True}
    monkeypatch.setattr(api, "release_cameras", lambda thing: pytest.fail("never activated: no cameras to release"))
    status, out = route("POST", f"adapters/{ADAPTER}/revoke")
    assert status == 200 and out["identityCommand"] == "bash cloud/adapter-identity.sh revoke adapter-02"
    assert told == [("adapter-02", False)]


@pytest.mark.parametrize("activated", [True, False])
def test_sync_says_again_what_the_directory_says(monkeypatch, route, directory, told, activated):
    _, results = directory
    results["adapter_state"] = {"id": ADAPTER, "thingName": "adapter-02", "activated": activated, "status": "x"}
    released = []
    monkeypatch.setattr(api, "release_cameras", lambda thing: released.append(thing) or 0)
    status, out = route("POST", f"adapters/{ADAPTER}/sync")
    assert status == 200 and told == [("adapter-02", activated)]
    assert released == ([] if activated else ["adapter-02"])


# --- billing (Phase 8, D9) --------------------------------------------------------------------------

@pytest.fixture
def billing(monkeypatch):
    """A provider recording what it is told -- start, change, cancel -- the rest as the fake does it."""
    told = []

    class Recording(api.FakeBillingProvider):
        def start(self, adapter_id):
            told.append(("start", adapter_id))

        def change(self, adapter_id, plan):
            told.append(("change", adapter_id, plan["version"]))

        def cancel(self, adapter_id):
            told.append(("cancel", adapter_id))
    monkeypatch.setattr(api, "BILLING", Recording())
    return told


def test_invoices_are_asked_with_the_filters_a_page_may_give(route, directory):
    calls, results = directory
    results["list_invoices"] = {"invoices": [], "truncated": False}
    status, out = route("GET", "invoices", query={"dealerId": DEALER, "customerId": CUSTOMER, "period": "2026-11", "x": "y"})
    assert (status, out) == (200, {"invoices": [], "truncated": False})
    assert calls == [("list_invoices", ME["sub"], {"dealerId": DEALER, "customerId": CUSTOMER, "period": "2026-11"})]


def test_a_customers_invoices_come_with_the_estimate_of_its_next_one(route, directory):
    calls, results = directory
    results["list_invoices"] = {"invoices": [{"id": "i-1"}], "truncated": False}
    results["billing_preview"] = {"period": "2026-11", "cents": 2240, "lines": []}
    status, out = route("GET", f"customers/{CUSTOMER}/invoices", query={"customerId": "someone-elses"})
    assert status == 200 and out == {"invoices": [{"id": "i-1"}], "truncated": False, "next": results["billing_preview"]}
    assert calls == [("list_invoices", ME["sub"], {"customerId": CUSTOMER}),
                     ("billing_preview", ME["sub"], {"customerId": CUSTOMER})]


def test_a_billing_run_sends_the_period_only(route, directory):
    calls, _ = directory
    assert route("POST", "billing/run-period", {"period": "2026-11", "cents": 1}) == (200, {"done": "run_billing_period"})
    assert calls == [("run_billing_period", ME["sub"], {"period": "2026-11"})]
    assert route("GET", "billing/run-period")[0] == 405


def test_the_directorys_refusal_of_a_run_is_passed_on(route, directory):
    _, results = directory
    results["run_billing_period"] = api.Refused(403, "not allowed for your role")
    assert route("POST", "billing/run-period", {"period": "2026-11"}) == (403, {"error": "not allowed for your role"})


def test_plan_changes_are_told_to_the_provider_after_the_directory(route, directory, billing):
    _, results = directory
    results["put_plan"] = {"changed": True, "plan": {"version": 3}}
    assert route("PUT", f"adapters/{ADAPTER}/plan", {"lines": []})[0] == 200
    results["put_plan"] = {"changed": False, "plan": {"version": 3}}        # the same plan again: nothing to tell
    route("PUT", f"adapters/{ADAPTER}/plan", {"lines": []})
    route("POST", f"adapters/{ADAPTER}/plan/cancel", {"basedOn": 3})
    assert billing == [("change", ADAPTER, 3), ("cancel", ADAPTER)]


def test_an_activation_starts_a_plan_and_leaving_the_customer_ends_it(monkeypatch, route, directory, told, billing):
    _, results = directory
    monkeypatch.setattr(api, "release_cameras", lambda thing: 0)
    results["activate_adapter"] = {"adapterId": ADAPTER, "thingName": "adapter-02", "customerId": CUSTOMER,
                                   "siteId": "s-1", "activationId": "act-9"}
    results["release_adapter"] = {"id": ADAPTER, "thingName": "adapter-02", "cloudId": "X"}
    results["remove_adapter"] = {"id": ADAPTER, "thingName": "adapter-02"}
    results["revoke_adapter"] = {"id": ADAPTER, "thingName": "adapter-02", "wasActivated": True, "identity": False}
    assert route("POST", "adapters/activate", {"cloudId": "x"})[0] == 200
    for action in ("release", "remove", "revoke"):
        assert route("POST", f"adapters/{ADAPTER}/{action}")[0] == 200
    results["revoke_adapter"] = {**results["revoke_adapter"], "wasActivated": False}       # never activated: no plan
    route("POST", f"adapters/{ADAPTER}/revoke")
    assert billing == [("start", ADAPTER), ("cancel", ADAPTER), ("cancel", ADAPTER), ("cancel", ADAPTER)]


def test_a_refused_change_tells_the_provider_nothing(route, directory, told, billing):
    _, results = directory
    results["put_plan"] = api.Refused(409, "the plan was changed meanwhile")
    results["cancel_plan"] = api.Refused(409, "this adapter has no plan to cancel")
    results["activate_adapter"] = api.Refused(400, "this Cloud ID can't be activated")
    assert route("PUT", f"adapters/{ADAPTER}/plan", {"lines": []})[0] == 409
    assert route("POST", f"adapters/{ADAPTER}/plan/cancel", {})[0] == 409
    assert route("POST", "adapters/activate", {"cloudId": "x"})[0] == 400
    assert billing == []


def test_a_provider_that_fails_is_reported_and_the_directory_stands(monkeypatch, route, directory):
    _, results = directory
    results["put_plan"] = {"changed": True, "plan": {"version": 2}}

    class Down(api.FakeBillingProvider):
        def change(self, adapter_id, plan):
            raise api.ClientError({"Error": {"Code": "ServiceUnavailable"}}, "x")
    monkeypatch.setattr(api, "BILLING", Down())
    status, out = route("PUT", f"adapters/{ADAPTER}/plan", {"lines": []})
    assert status == 200 and out["plan"] == {"version": 2}
    assert out["billingUnsent"] == "the billing provider wasn't told (ServiceUnavailable)"


def test_the_fake_provider_tells_nobody_and_the_seam_has_d9s_five(monkeypatch):
    monkeypatch.setattr(api, "call_directory", lambda *a: pytest.fail("a plan change needs telling no one"))
    fake = api.FakeBillingProvider()
    assert (fake.name, fake.start(ADAPTER), fake.change(ADAPTER, {"version": 2}), fake.cancel(ADAPTER)) == \
        ("fake", None, None, None)
    for method in ("quote", "start", "change", "cancel", "list_invoices"):
        assert callable(getattr(api.BillingProvider, method))
    with pytest.raises(NotImplementedError):
        api.BillingProvider().change(ADAPTER, {"version": 2})


# --- an account's own pages (Phase 9, D18) -----------------------------------------------------------

ACCOUNT = "77777777-7777-4777-8777-777777777777"
ACCOUNT_CAMERAS = [
    {"cameraId": "cam-03", "adapterId": "adapter-01", "displayName": "Hall", "onvifPassword": "secret",
     "rtspUrl": "rtsp://u:secret@x/1"},
    {"cameraId": "cam-04", "adapterId": "adapter-01"},
    {"cameraId": "cam-05", "adapterId": "adapter-01", "unregisteredAt": "2026-11-08T00:00:00Z"},
    {"cameraId": "cam-20", "adapterId": "adapter-02"},
    {"cameraId": "cam-30"},                                             # no adapter: an administrator's
]
PEOPLE = {"account": {"id": ACCOUNT, "name": "Acme"}, "role": "customer", "things": ["adapter-01"], "people": []}


@pytest.mark.parametrize("method, path, body, op, args", [
    ("GET", f"accounts/{ACCOUNT}/plans", None, "account_plans", {"id": ACCOUNT}),
    ("POST", f"accounts/{ACCOUNT}/holder", {"userId": USER}, "hand_over_holder", {"userId": USER, "id": ACCOUNT}),
])
def test_plan_and_billing_and_the_hand_over_are_directory_operations(route, directory, method, path, body, op, args):
    calls, _ = directory
    assert route(method, path, body)[0] == 200
    assert calls == [(op, ME["sub"], args)]


def test_the_accounts_people_come_with_its_cameras_and_never_a_credential(aws, route, directory):
    calls, results = directory
    results["account_users"] = dict(PEOPLE)
    aws(api, cameras=ACCOUNT_CAMERAS)
    status, out = route("GET", f"accounts/{ACCOUNT}/users")
    assert status == 200 and calls == [("account_users", ME["sub"], {"id": ACCOUNT})]
    assert out["cameras"] == [{"id": "cam-03", "name": "Hall", "thing": "adapter-01"},
                              {"id": "cam-04", "name": None, "thing": "adapter-01"}]   # unregistered and others' left out
    assert "secret" not in json.dumps(out)


def test_without_the_registry_the_people_stand_and_it_says_why(monkeypatch, route, directory):
    _, results = directory
    results["account_users"] = dict(PEOPLE)

    def broken():
        raise api.ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "Scan")
    monkeypatch.setattr(api, "registry_cameras", broken)
    status, out = route("GET", f"accounts/{ACCOUNT}/users")
    assert status == 200 and out["cameras"] is None and "can't be shared just now" in out["registryError"]


def test_someone_with_an_account_is_added_with_each_cameras_adapter_and_no_cognito_call(aws, route, directory):
    calls, results = directory
    results["add_account_user"] = {"userId": USER, "email": "ann@example.test", "status": "active", "new": False}
    aws(api, cameras=ACCOUNT_CAMERAS)                     # no Cognito answer queued: a call would fail
    body = {"email": "ann@example.test", "superAdmin": False, "cameras": ["cam-04", "cam-03"]}
    assert route("POST", f"accounts/{ACCOUNT}/users", body) == \
        (201, {"userId": USER, "email": "ann@example.test", "status": "active", "new": False})
    assert calls == [("add_account_user", ME["sub"], {"email": "ann@example.test", "superAdmin": False, "id": ACCOUNT,
                      "cameras": [{"cameraId": "cam-04", "thing": "adapter-01"}, {"cameraId": "cam-03", "thing": "adapter-01"}]})]


def test_a_new_address_gets_its_sign_in_by_email_and_its_sub_is_stored(aws, route, directory):
    calls, results = directory
    results["add_account_user"] = {"userId": USER, "email": "Ann@example.test", "status": "invited", "new": True}
    fake = aws(api, cameras=ACCOUNT_CAMERAS)
    _expect_create(fake, "Ann@example.test")
    status, out = route("POST", f"accounts/{ACCOUNT}/users", {"email": "Ann@example.test", "superAdmin": True,
                                                              "cameras": ["cam-03"]})
    assert status == 201 and out["new"] is True
    assert [c[0] for c in calls] == ["add_account_user", "link_sub"]
    assert calls[0][2]["cameras"] == [] and calls[1][2] == {"userId": USER, "sub": NEW_SUB}   # a Super Admin shares none


def test_a_new_address_cognito_refuses_is_taken_back_out(aws, route, directory):
    calls, results = directory
    results["add_account_user"] = {"userId": USER, "email": "ann@example.test", "status": "invited", "new": True}
    fake = aws(api)
    fake.stub("cognito-idp").add_client_error("admin_create_user", "UsernameExistsException")
    assert route("POST", f"accounts/{ACCOUNT}/users", {"email": "ann@example.test", "superAdmin": True}) == \
        (409, {"error": "can't add this address"})
    assert calls[1] == ("remove_account_user", ME["sub"], {"id": ACCOUNT, "userId": USER})


@pytest.mark.parametrize("cameras, error", [
    (["cam-05"], "no such camera: cam-05"),                               # unregistered
    (["cam-99", "cam-03", "cam-30"], "no such camera: cam-99, cam-30"),   # unknown; no adapter
    ("cam-03", "cameras: a list of camera IDs"),
    ([{"cameraId": "cam-03", "thing": "adapter-01"}], "cameras: a list of camera IDs"),   # the adapter is the registry's
])
def test_a_camera_the_registry_doesnt_serve_is_refused_before_the_directory(aws, route, directory, cameras, error):
    calls, _ = directory
    aws(api, cameras=ACCOUNT_CAMERAS)
    assert route("POST", f"accounts/{ACCOUNT}/users", {"email": "x@example.test", "superAdmin": False,
                                                       "cameras": cameras}) == (400, {"error": error})
    assert calls == []


def test_an_edit_carries_each_cameras_adapter_as_the_registry_says(aws, route, directory):
    calls, _ = directory
    aws(api, cameras=ACCOUNT_CAMERAS)
    assert route("PATCH", f"accounts/{ACCOUNT}/users/{USER}", {"superAdmin": False, "cameras": ["cam-20"]})[0] == 200
    assert calls == [("update_account_user", ME["sub"], {"superAdmin": False, "id": ACCOUNT, "userId": USER,
                                                          "cameras": [{"cameraId": "cam-20", "thing": "adapter-02"}]})]


@pytest.mark.parametrize("path", [f"accounts/{ACCOUNT}/users/{USER}/remove", f"accounts/{ACCOUNT}/invites/{USER}/cancel"])
def test_an_invitation_cancelled_before_it_was_used_takes_its_sign_in_with_it(aws, route, directory, path):
    calls, results = directory
    results["remove_account_user"] = {"userId": USER, "deleted": True, "email": "ann@example.test", "sub": NEW_SUB}
    fake = aws(api)
    fake.stub("cognito-idp").add_response("admin_delete_user", {}, {"UserPoolId": "eu-central-1_test", "Username": NEW_SUB})
    assert route("POST", path) == (200, {"userId": USER, "deleted": True})
    assert calls == [("remove_account_user", ME["sub"], {"id": ACCOUNT, "userId": USER})]


def test_removing_someone_who_has_signed_in_leaves_their_sign_in(aws, route, directory):
    _, results = directory
    results["remove_account_user"] = {"userId": USER, "deleted": False, "email": None, "sub": None}
    aws(api)                                              # no Cognito answer queued: a call would fail
    assert route("POST", f"accounts/{ACCOUNT}/users/{USER}/remove") == (200, {"userId": USER, "deleted": False})


@pytest.mark.parametrize("code, reported", [("UserNotFoundException", False), ("AccessDeniedException", True)])
def test_a_sign_in_cognito_wont_delete_is_reported_and_the_removal_stands(aws, route, directory, code, reported):
    _, results = directory
    results["remove_account_user"] = {"userId": USER, "deleted": True, "email": "ann@example.test", "sub": None}
    fake = aws(api)
    fake.stub("cognito-idp").add_client_error("admin_delete_user", code)
    status, out = route("POST", f"accounts/{ACCOUNT}/invites/{USER}/cancel")
    assert status == 200 and out["deleted"] is True
    assert ("may be refused" in out.get("cognitoError", "")) is reported


def test_an_account_invitation_is_resent_to_its_address(aws, route, directory):
    _, results = directory
    results["resend_account_invite"] = {"userId": USER, "email": "ann@example.test"}
    fake = aws(api)
    fake.stub("cognito-idp").add_response(
        "admin_create_user", {"User": {"Username": "x"}},
        {"UserPoolId": "eu-central-1_test", "Username": "ann@example.test", "MessageAction": "RESEND",
         "DesiredDeliveryMediums": ["EMAIL"]})
    assert route("POST", f"accounts/{ACCOUNT}/invites/{USER}/resend") == (200, {"userId": USER, "resent": "ann@example.test"})


def test_removing_a_user_is_a_post_since_the_preflight_allows_no_delete(route, directory):
    assert route("DELETE", f"accounts/{ACCOUNT}/users/{USER}")[0] == 405


def test_the_new_policy_deletes_sign_ins_of_the_same_pool_only():
    from pathlib import Path
    iam = Path(__file__).resolve().parents[2] / "cloud" / "iam"
    [new] = json.loads((iam / "vsz-directory-api-accounts-policy.json").read_text())["Statement"]
    pool = next(s["Resource"] for s in json.loads((iam / "vsz-directory-api-policy.json").read_text())["Statement"]
                if "cognito-idp:AdminCreateUser" in s["Action"])
    assert (new["Action"], new["Resource"]) == ("cognito-idp:AdminDeleteUser", pool)
