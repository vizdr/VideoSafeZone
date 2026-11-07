"""vsz-directory's operations for signed-in people, on the real schema (DEALER-PANEL-PLAN.md Phase 4, D6).

Each operation is asked as a demo person, with the role the database gives them -- the token isn't
trusted (resolve_actor). What every role may do: an administrator everything; an owner invite a
dealer's staff or a customer's holder, and manage everyone but the system's staff; a dealer invite
its own customers' holders and manage their people; the customer side nothing here yet.
"""
import pytest

from dbhelpers import SUBS, demo_spec, one, platform
from loaders import load_lambda

directory = load_lambda("vsz_directory")


@pytest.fixture
def demo(app):
    directory.operator_seed(app, demo_spec())
    return app


def ask(app, op, who, **args):
    fn, _ = directory.USER_OPS[op]
    actor = directory.resolve_actor(app, {"kind": "user", "sub": SUBS[who]})
    return directory.jsonable(fn(app, actor, args))


def refused_op(app, op, who, status, **args):
    """The operation must be refused with `status`, as the handler would answer; the transaction goes on."""
    app.run("SAVEPOINT op")
    try:
        ask(app, op, who, **args)
    except directory.DirectoryError as exc:
        app.run("ROLLBACK TO SAVEPOINT op")
        assert exc.status == status, f"{who} {op}: {exc.status} {exc.args[0]}"
        return exc.args[0]
    except Exception as exc:                           # a rule the database keeps
        app.run("ROLLBACK TO SAVEPOINT op")
        state = directory.sql_state(exc)
        assert (409 if state == "23505" else 400 if state in ("23514", "23503") else 500) == status, exc
        return str(exc)
    app.run("RELEASE SAVEPOINT op")
    pytest.fail(f"{who} was allowed {op}")


def dealer_id(app, name="Demo dealer"):
    return str(one(app, "SELECT id FROM vsz.org WHERE kind = 'dealer' AND name = :n", n=name))


# --- who is asking ---------------------------------------------------------------------------------

def test_the_database_decides_who_is_asking(demo):
    assert ask(demo, "me", "admin")["role"] == "system_admin"
    assert ask(demo, "me", "dealer")["org"]["name"] == "Demo dealer"
    holder = ask(demo, "me", "holder")
    assert (holder["role"], holder["org"]) == ("customer", None)
    assert [(a["name"], a["role"], a["dealer"]) for a in holder["accounts"]] == [("Demo customer", "customer", "Demo dealer")]


@pytest.mark.parametrize("who", ["disabled", "suspended"])
def test_the_disabled_and_the_suspended_are_turned_away(demo, who):
    assert refused_op(demo, "me", who, 403) == "this account can't use the directory"


def test_a_token_that_isnt_a_person_is_turned_away(demo):
    with pytest.raises(directory.DirectoryError) as e:
        directory.resolve_actor(demo, {"kind": "user", "sub": "not-a-uuid"})
    assert e.value.status == 401


# --- what each role may ask for (D6) -----------------------------------------------------------------

SYSTEM_ONLY = ["overview", "list_dealers", "list_users"]


@pytest.mark.parametrize("op", SYSTEM_ONLY)
def test_the_system_lists_are_for_owners_and_administrators(demo, op):
    ask(demo, op, "admin")
    ask(demo, op, "owner")
    for who in ("dealer", "holder", "superadmin", "user"):
        refused_op(demo, op, who, 403)


def test_the_price_list_is_read_by_dealers_and_changed_by_the_system(demo):
    assert len(ask(demo, "get_pricing", "dealer")["prices"]) == 18
    refused_op(demo, "get_pricing", "holder", 403)
    refused_op(demo, "put_pricing", "dealer", 403, prices=[], settings={})


def test_a_dealer_lists_only_its_own_customers(demo):
    other = one(demo, "INSERT INTO vsz.org (kind, parent_id, name) VALUES ('dealer', :p, 'Other') RETURNING id",
                p=platform(demo))
    demo.run("INSERT INTO vsz.org (kind, parent_id, name) VALUES ('customer', :d, 'Not yours')", d=other)
    assert {c["name"] for c in ask(demo, "list_customers", "dealer")} == {"Demo customer", "Suspended demo customer"}
    assert "Not yours" in {c["name"] for c in ask(demo, "list_customers", "admin")}
    assert {c["name"] for c in ask(demo, "list_customers", "admin", dealerId=str(other))} == {"Not yours"}


# --- dealers -----------------------------------------------------------------------------------------

def test_dealers_are_created_once_by_name_and_audited(demo):
    made = ask(demo, "create_dealer", "owner", name="North Security", email="sales@north.example")
    assert {d["name"] for d in ask(demo, "list_dealers", "admin")} >= {"Direct", "Demo dealer", "North Security"}
    refused_op(demo, "create_dealer", "admin", 409, name="north security")
    refused_op(demo, "create_dealer", "admin", 400, name="")
    refused_op(demo, "create_dealer", "admin", 400, name="X", email="not an address")
    assert one(demo, "SELECT action FROM vsz.audit_event WHERE target_id = CAST(:t AS uuid)", t=made["id"]) == "create_dealer"


def test_suspending_a_dealer_signs_its_staff_out(demo):
    ask(demo, "update_dealer", "admin", id=dealer_id(demo), status="suspended")
    assert directory.token_claims(demo, SUBS["dealer"], "dealer@example.test", True, False) is None
    refused_op(demo, "update_dealer", "admin", 404, id="00000000-0000-4000-8000-000000000000", name="x")
    refused_op(demo, "update_dealer", "admin", 400, id=dealer_id(demo))


# --- invitations -------------------------------------------------------------------------------------

def test_who_may_invite_whom(demo):
    platform_id, demo_dealer = str(platform(demo)), dealer_id(demo)
    customer = str(one(demo, "SELECT id FROM vsz.org WHERE name = 'Demo customer'"))
    ask(demo, "invite_user", "admin", email="new-owner@example.test", orgId=platform_id, role="system_owner")
    refused_op(demo, "invite_user", "owner", 403, email="x1@example.test", orgId=platform_id, role="system_admin")
    ask(demo, "invite_user", "owner", email="seller@example.test", orgId=demo_dealer, role="dealer")
    refused_op(demo, "invite_user", "owner", 400, email="x2@example.test", orgId=customer, role="dealer")  # wrong kind
    refused_op(demo, "invite_user", "dealer", 403, email="x3@example.test", orgId=customer, role="customer_user")
    refused_op(demo, "invite_user", "holder", 403, email="x4@example.test", orgId=customer, role="customer_user")
    # A Super Admin or a user is one of the account's Additional users: the holder's (Phase 9), or an administrator's
    refused_op(demo, "invite_user", "owner", 403, email="x5@example.test", orgId=customer, role="customer_admin")
    refused_op(demo, "invite_user", "owner", 403, email="x6@example.test", orgId=customer, role="customer_user")
    ask(demo, "invite_user", "admin", email="helper@example.test", orgId=customer, role="customer_user")
    fresh = str(one(demo, "INSERT INTO vsz.org (kind, parent_id, name) VALUES ('customer', CAST(:d AS uuid), 'Fresh') "
                          "RETURNING id", d=demo_dealer))
    ask(demo, "invite_user", "owner", email="holder4@example.test", orgId=fresh, role="customer")
    refused_op(demo, "invite_user", "owner", 409, email="x7@example.test", orgId=customer, role="customer")  # has one


def test_a_dealer_invites_the_holder_of_its_own_customers_only(demo):
    mine = str(one(demo, "INSERT INTO vsz.org (kind, parent_id, name) VALUES ('customer', CAST(:d AS uuid), 'Mine') "
                         "RETURNING id", d=dealer_id(demo)))
    ask(demo, "invite_user", "dealer", email="holder2@example.test", orgId=mine, role="customer")
    other = one(demo, "INSERT INTO vsz.org (kind, parent_id, name) VALUES ('dealer', :p, 'Other') RETURNING id",
                p=platform(demo))
    theirs = str(one(demo, "INSERT INTO vsz.org (kind, parent_id, name) VALUES ('customer', :d, 'Theirs') RETURNING id",
                     d=other))
    refused_op(demo, "invite_user", "dealer", 403, email="holder3@example.test", orgId=theirs, role="customer")


def test_an_address_in_use_is_refused_without_saying_whose(demo):
    message = refused_op(demo, "invite_user", "admin", 409, email="HOLDER@example.test",
                         orgId=dealer_id(demo), role="dealer")
    assert message == "can't add this address"


def test_an_invitation_is_linked_or_cancelled(demo):
    invited = ask(demo, "invite_user", "admin", email="later@example.test", orgId=dealer_id(demo), role="dealer")
    sub = "11111111-2222-4333-8444-555555555555"
    ask(demo, "link_sub", "admin", userId=invited["userId"], sub=sub)
    assert str(one(demo, "SELECT cognito_sub FROM vsz.app_user WHERE email = 'later@example.test'")) == sub
    assert ask(demo, "cancel_invite", "admin", userId=invited["userId"]) == {"cancelled": False}   # linked: kept
    other = ask(demo, "invite_user", "admin", email="never@example.test", orgId=dealer_id(demo), role="dealer")
    assert ask(demo, "cancel_invite", "admin", userId=other["userId"]) == {"cancelled": True}
    assert one(demo, "SELECT count(*) FROM vsz.app_user WHERE email = 'never@example.test'") == 0


# --- disable, enable, reset, resend ------------------------------------------------------------------

def _user(demo, email):
    return str(one(demo, "SELECT id FROM vsz.app_user WHERE email = :e", e=email))


def test_disabling_takes_effect_at_the_next_renewal_and_enabling_brings_back(demo):
    holder = _user(demo, "holder@example.test")
    out = ask(demo, "user_action", "admin", userId=holder, action="disable")
    assert (out["status"], out["sub"]) == ("disabled", SUBS["holder"])
    assert directory.token_claims(demo, SUBS["holder"], "holder@example.test", True, False) is None
    assert ask(demo, "user_action", "admin", userId=holder, action="enable")["status"] == "invited"   # never signed in
    assert directory.token_claims(demo, SUBS["holder"], "holder@example.test", True, False) is not None


def test_reset_is_for_the_signed_in_and_resend_for_the_invited(demo):
    holder = _user(demo, "holder@example.test")                            # seeded active
    refused_op(demo, "user_action", "admin", 409, userId=holder, action="resend")
    ask(demo, "user_action", "admin", userId=holder, action="reset")
    invited = ask(demo, "invite_user", "admin", email="fresh@example.test", orgId=dealer_id(demo), role="dealer")
    refused_op(demo, "user_action", "admin", 409, userId=invited["userId"], action="reset")
    ask(demo, "user_action", "admin", userId=invited["userId"], action="resend")


def test_nobody_manages_their_own_account_or_one_above_them(demo):
    refused_op(demo, "user_action", "admin", 400, userId=_user(demo, "admin@example.test"), action="disable")
    refused_op(demo, "user_action", "owner", 403, userId=_user(demo, "admin@example.test"), action="disable")
    ask(demo, "user_action", "dealer", userId=_user(demo, "holder@example.test"), action="disable")
    refused_op(demo, "user_action", "dealer", 403, userId=_user(demo, "owner@example.test"), action="disable")
    refused_op(demo, "user_action", "dealer", 403, userId=_user(demo, "admin@example.test"), action="reset")


def test_a_dealer_manages_all_its_customers_people_and_nobody_elses(demo):
    for email in ("superadmin@example.test", "user@example.test"):       # Additional users too (D6)
        ask(demo, "user_action", "dealer", userId=_user(demo, email), action="disable")
        ask(demo, "user_action", "owner", userId=_user(demo, email), action="enable")
    other = one(demo, "INSERT INTO vsz.org (kind, parent_id, name) VALUES ('dealer', :p, 'Other') RETURNING id",
                p=platform(demo))
    theirs = str(one(demo, "INSERT INTO vsz.org (kind, parent_id, name) VALUES ('customer', :d, 'Theirs') RETURNING id",
                     d=other))
    both = ask(demo, "invite_user", "admin", email="both@example.test", orgId=theirs, role="customer")["userId"]
    refused_op(demo, "user_action", "dealer", 403, userId=both, action="disable")
    demo.run("INSERT INTO vsz.membership (user_id, org_id, role) VALUES (CAST(:u AS uuid), "
             "(SELECT id FROM vsz.org WHERE name = 'Demo customer'), 'customer_user')", u=both)
    refused_op(demo, "user_action", "dealer", 403, userId=both, action="disable")   # in someone else's too
    ask(demo, "user_action", "owner", userId=both, action="disable")


# --- the lists and the price list ------------------------------------------------------------------

def test_the_lists_show_what_the_console_needs(demo):
    adapters = ask(demo, "list_adapters", "admin")
    assert [(a["thingName"], a["activation"]["customer"]["name"], a["activation"]["site"]) for a in adapters] == \
        [("adapter-01", "Demo customer", "Home")]
    users = {u["email"]: u for u in ask(demo, "list_users", "owner")}
    assert [m["role"] for m in users["superadmin@example.test"]["memberships"]] == ["customer_admin"]
    overview = ask(demo, "overview", "admin")
    assert overview["dealers"] == {"active": 2} and overview["customers"] == {"active": 1, "suspended": 1}
    assert (overview["plans"]["trial"], overview["adapters"]) == (1, {"activated": 1})


def _matrix(days, cents=500):
    return [{"resolution": r, "recording": c, "retentionDays": d, "centsMonth": cents}
            for r in ("2mp", "4mp", "8mp") for c in ("motion", "continuous") for d in days]


def test_the_price_list_is_replaced_whole(demo):
    settings = {"retention_days": [7, 30], "annual_discount_percent": 15, "trial_days": 14}
    assert ask(demo, "put_pricing", "owner", prices=_matrix([7, 30]), settings=settings) == {"prices": 12}
    got = ask(demo, "get_pricing", "dealer")
    assert len(got["prices"]) == 12 and {p["centsMonth"] for p in got["prices"]} == {500}
    assert (got["settings"]["placeholder_prices"], got["settings"]["trial_days"]) == (False, 14)
    refused_op(demo, "put_pricing", "admin", 400, prices=_matrix([7]), settings=settings)            # missing
    refused_op(demo, "put_pricing", "admin", 400, prices=_matrix([7, 30]) * 2, settings=settings)    # repeated
    refused_op(demo, "put_pricing", "admin", 400, prices=_matrix([7, 30], cents=-1), settings=settings)
    refused_op(demo, "put_pricing", "admin", 400, prices=_matrix([30, 7]), settings={**settings, "retention_days": [30, 7]})
