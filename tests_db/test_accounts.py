"""An account's own pages on the real schema (DEALER-PANEL-PLAN.md Phase 9, D18): Plan & Billing and
Additional users, asked as the demo people.

The rules Phase 9 names, each pinned: one holder; a Super Admin can't touch the holder; a share outside
the account is refused; a staff address is refused (with the generic answer); a released adapter ends
its shares. Also: who may see and change what, an address with an account getting access at once and
a new one invited, a Super Admin's shares going, a pending invite cancelled leaving nothing behind,
handing the account over, and the adapters /directory/me gives for the camera list's groups.

The seed: Demo customer has holder@, superadmin@ (Super Admin), user@ (cam-03 on adapter-01 shared)
and disabled@; suspended@ holds the suspended demo customer; dealer@, owner@, admin@ are staff.
"""
import pytest

from dbhelpers import SUBS, activate, adapter, ask, customer, dealer_id, demo_spec, one, refused_op, site
from loaders import load_lambda

directory = load_lambda("vsz_directory")

CAM03 = [{"cameraId": "cam-03", "thing": "adapter-01"}]


@pytest.fixture
def demo(app):
    directory.operator_seed(app, demo_spec())
    return app


def demo_customer(app):
    return str(one(app, "SELECT id FROM vsz.org WHERE name = 'Demo customer'"))


def person(app, email):
    return str(one(app, "SELECT id FROM vsz.app_user WHERE lower(email) = lower(:e)", e=email))


def people(app, who="holder", account=None):
    got = ask(app, "account_users", who, id=account or demo_customer(app))
    return {p["email"]: p for p in got["people"]}


def other_account(app, thing="adapter-0501"):
    """Another customer of the demo dealer, with an adapter of its own."""
    other = customer(app, dealer_id(app), "Other account")
    activate(app, adapter(app, thing), other, site(app, other, "Shop"))
    return str(other)


def claims(app, who):
    return directory.token_claims(app, SUBS[who], f"{who}@example.test", True, False)


# --- who sees what -------------------------------------------------------------------------------

def test_the_holder_and_super_admins_see_the_accounts_people(demo):
    got = ask(demo, "account_users", "holder", id=demo_customer(demo))
    assert (got["account"]["name"], got["role"], got["things"]) == ("Demo customer", "customer", ["adapter-01"])
    assert [(p["email"], p["role"], p["cameras"], p["you"]) for p in got["people"]] == [
        ("holder@example.test", "customer", [], True),
        ("superadmin@example.test", "customer_admin", [], False),
        ("disabled@example.test", "customer_user", [], False),
        ("user@example.test", "customer_user", ["cam-03"], False)]
    assert ask(demo, "account_users", "superadmin", id=demo_customer(demo))["role"] == "customer_admin"
    assert ask(demo, "account_users", "admin", id=demo_customer(demo))["role"] == "system_admin"   # support
    for who in ("user", "dealer", "owner"):
        assert refused_op(demo, "account_users", who, 404, id=demo_customer(demo)) == "no such account"


def test_only_the_holder_sees_plan_and_billing(demo):
    got = ask(demo, "account_plans", "holder", id=demo_customer(demo))
    [hall] = got["adapters"]
    assert (hall["thingName"], hall["name"], hall["site"], hall["plan"]["status"]) == ("adapter-01", "Hall", "Home", "trial")
    assert [p["version"] for p in hall["history"]] == [1]
    assert got["dealer"]["name"] == "Demo dealer" and got["invoices"] == [] and got["next"]["lines"] == []
    hall_id = str(one(demo, "SELECT id FROM vsz.adapter WHERE thing_name = 'adapter-01'"))
    ask(demo, "put_plan", "dealer", adapterId=hall_id, lines=[{"resolution": "4mp", "recording": "motion", "quantity": 4}],
        retentionDays=30, interval="monthly")
    again = ask(demo, "account_plans", "holder", id=demo_customer(demo))["adapters"][0]
    assert [p["version"] for p in again["history"]] == [2, 1] and again["plan"]["centsPeriod"] == 2240
    for who in ("superadmin", "user", "admin", "dealer"):
        refused_op(demo, "account_plans", who, 404, id=demo_customer(demo))


# --- Add user ------------------------------------------------------------------------------------

def test_an_address_with_an_account_gets_access_at_once(demo):
    out = ask(demo, "add_account_user", "holder", id=demo_customer(demo), email="Suspended@Example.test",
              superAdmin=False, cameras=CAM03)
    assert (out["new"], out["status"]) == (False, "active")
    assert people(demo)["suspended@example.test"]["cameras"] == ["cam-03"]
    assert claims(demo, "suspended") == {"vsz_role": "customer", "vsz_cameras": "cam-03@adapter-01"}
    assert one(demo, "SELECT detail ->> 'new' FROM vsz.audit_event WHERE action = 'add_account_user'") == "false"


def test_a_new_address_is_invited_and_waits_under_pending_invites(demo):
    out = ask(demo, "add_account_user", "superadmin", id=demo_customer(demo), email="new@example.test", superAdmin=True)
    assert (out["new"], out["status"]) == (True, "invited")
    p = people(demo)["new@example.test"]
    assert (p["role"], p["status"], p["cameras"], p["lastSignInAt"]) == ("customer_admin", "invited", [], None)


@pytest.mark.parametrize("email", ["dealer@example.test", "OWNER@example.test", "admin@example.test"])
def test_a_staff_address_is_refused_with_the_generic_answer(demo, email):
    msg = refused_op(demo, "add_account_user", "holder", 409, id=demo_customer(demo), email=email, superAdmin=False)
    assert msg == "can't add this address"
    assert one(demo, "SELECT count(*) FROM vsz.membership WHERE user_id = CAST(:u AS uuid)", u=person(demo, email)) == 1


def test_someone_on_the_account_already_isnt_added_twice(demo):
    assert "already" in refused_op(demo, "add_account_user", "holder", 409, id=demo_customer(demo),
                                   email="superadmin@example.test", superAdmin=False)


@pytest.mark.parametrize("args", [
    {"email": "nobody", "superAdmin": False},
    {"email": "x@example.test"},
    {"email": "x@example.test", "superAdmin": "yes"},
    {"email": "x@example.test", "superAdmin": False, "cameras": [{"cameraId": "cam-03"}]},
    {"email": "x@example.test", "superAdmin": False, "cameras": CAM03 * 2},
    {"email": "x@example.test", "superAdmin": False, "cameras": "cam-03"},
])
def test_what_add_user_needs(demo, args):
    refused_op(demo, "add_account_user", "holder", 400, id=demo_customer(demo), **args)
    assert not demo.run("SELECT 1 FROM vsz.app_user WHERE email = 'x@example.test'")


def test_a_share_outside_the_account_is_refused(demo):
    other_account(demo)
    theirs = [{"cameraId": "cam-50", "thing": "adapter-0501"}]
    assert "isn't one of this account's cameras" in refused_op(
        demo, "add_account_user", "holder", 400, id=demo_customer(demo), email="x@example.test", superAdmin=False,
        cameras=theirs)
    refused_op(demo, "update_account_user", "holder", 400, id=demo_customer(demo), userId=person(demo, "user@example.test"),
               superAdmin=False, cameras=theirs)
    assert people(demo)["user@example.test"]["cameras"] == ["cam-03"]          # unchanged


def test_at_most_100_cameras_are_shared_with_a_person(demo):
    many = [{"cameraId": f"cam-{n}", "thing": "adapter-01"} for n in range(1000, 1100)]
    user = person(demo, "user@example.test")
    ask(demo, "update_account_user", "holder", id=demo_customer(demo), userId=user, superAdmin=False, cameras=many)
    other = other_account(demo)
    msg = refused_op(demo, "add_account_user", "admin", 400, id=other, email="user@example.test", superAdmin=False,
                     cameras=[{"cameraId": "cam-50", "thing": "adapter-0501"}])
    assert "at most 100" in msg
    refused_op(demo, "update_account_user", "holder", 400, id=demo_customer(demo), userId=user, superAdmin=False,
               cameras=many + [{"cameraId": "cam-1100", "thing": "adapter-01"}])


# --- Edit, Remove, Resend ------------------------------------------------------------------------

def test_a_super_admin_cant_touch_the_holder_and_nobody_their_own_access(demo):
    cid, holder = demo_customer(demo), person(demo, "holder@example.test")
    assert refused_op(demo, "update_account_user", "superadmin", 403, id=cid, userId=holder, superAdmin=False) == \
        "the account holder's access can't be changed"
    refused_op(demo, "remove_account_user", "superadmin", 403, id=cid, userId=holder)
    refused_op(demo, "resend_account_invite", "superadmin", 403, id=cid, userId=holder)
    refused_op(demo, "update_account_user", "admin", 403, id=cid, userId=holder, superAdmin=True)
    me = person(demo, "superadmin@example.test")
    assert "your own" in refused_op(demo, "update_account_user", "superadmin", 400, id=cid, userId=me, superAdmin=False)
    refused_op(demo, "remove_account_user", "superadmin", 400, id=cid, userId=me)
    assert one(demo, "SELECT count(*) FROM vsz.membership WHERE org_id = CAST(:c AS uuid) AND role = 'customer'", c=cid) == 1


def test_a_user_cant_manage_the_account(demo):
    cid = demo_customer(demo)
    refused_op(demo, "add_account_user", "user", 404, id=cid, email="x@example.test", superAdmin=True)
    refused_op(demo, "update_account_user", "user", 404, id=cid, userId=person(demo, "disabled@example.test"), superAdmin=True)
    refused_op(demo, "remove_account_user", "dealer", 404, id=cid, userId=person(demo, "user@example.test"))


def test_a_super_admin_sees_every_camera_so_their_shares_go(demo):
    cid, user = demo_customer(demo), person(demo, "user@example.test")
    assert ask(demo, "update_account_user", "superadmin", id=cid, userId=user, superAdmin=True) == \
        {"userId": user, "role": "customer_admin", "cameras": []}
    assert claims(demo, "user") == {"vsz_role": "customer", "vsz_adapters": "adapter-01"}
    back = ask(demo, "update_account_user", "holder", id=cid, userId=user, superAdmin=False,
               cameras=[{"cameraId": "cam-04", "thing": "adapter-01"}] + CAM03)
    assert (back["role"], back["cameras"]) == ("customer_user", ["cam-04", "cam-03"])
    assert claims(demo, "user") == {"vsz_role": "customer", "vsz_cameras": "cam-03@adapter-01,cam-04@adapter-01"}


def test_removing_someone_who_has_signed_in_keeps_their_login(demo):
    cid, user = demo_customer(demo), person(demo, "user@example.test")
    assert ask(demo, "remove_account_user", "holder", id=cid, userId=user) == \
        {"userId": user, "deleted": False, "email": None, "sub": None}
    assert "user@example.test" not in people(demo)
    assert not demo.run("SELECT 1 FROM vsz.camera_share WHERE user_id = CAST(:u AS uuid)", u=user)  # 001's cascade
    assert claims(demo, "user") is None                                       # no account left: no sign-in
    assert person(demo, "user@example.test") == user                          # the person is kept


def test_cancelling_a_pending_invite_leaves_nothing_behind(demo):
    cid = demo_customer(demo)
    made = ask(demo, "add_account_user", "holder", id=cid, email="new@example.test", superAdmin=False, cameras=CAM03)
    demo.run("UPDATE vsz.app_user SET cognito_sub = gen_random_uuid() WHERE id = CAST(:u AS uuid)", u=made["userId"])
    out = ask(demo, "remove_account_user", "superadmin", id=cid, userId=made["userId"])
    assert (out["deleted"], out["email"]) == (True, "new@example.test") and out["sub"]
    assert not demo.run("SELECT 1 FROM vsz.app_user WHERE email = 'new@example.test'")
    again = ask(demo, "add_account_user", "holder", id=cid, email="new@example.test", superAdmin=True)
    assert again["new"] is True                                               # the address can be invited again


def test_an_invite_another_account_shares_isnt_deleted(demo):
    cid, other = demo_customer(demo), other_account(demo)
    made = ask(demo, "add_account_user", "holder", id=cid, email="both@example.test", superAdmin=True)
    ask(demo, "add_account_user", "admin", id=other, email="both@example.test", superAdmin=True)
    assert ask(demo, "remove_account_user", "holder", id=cid, userId=made["userId"])["deleted"] is False
    assert set(people(demo, "admin", other)) >= {"both@example.test"}


def test_an_invitation_is_resent_only_to_someone_who_hasnt_signed_in(demo):
    cid = demo_customer(demo)
    refused_op(demo, "resend_account_invite", "holder", 409, id=cid, userId=person(demo, "user@example.test"))
    made = ask(demo, "add_account_user", "holder", id=cid, email="new@example.test", superAdmin=True)
    assert ask(demo, "resend_account_invite", "superadmin", id=cid, userId=made["userId"]) == \
        {"userId": made["userId"], "email": "new@example.test"}


# --- the holder, and a release ---------------------------------------------------------------------

def test_the_holder_hands_the_account_over_and_stays_on_as_super_admin(demo):
    cid, user = demo_customer(demo), person(demo, "user@example.test")
    refused_op(demo, "hand_over_holder", "superadmin", 404, id=cid, userId=user)          # the holder's alone
    invited = ask(demo, "add_account_user", "holder", id=cid, email="new@example.test", superAdmin=True)["userId"]
    refused_op(demo, "hand_over_holder", "holder", 409, id=cid, userId=invited)            # hasn't signed in
    out = ask(demo, "hand_over_holder", "holder", id=cid, userId=user)
    assert out["holder"]["email"] == "user@example.test"
    roles = {email: (p["role"], p["cameras"]) for email, p in people(demo, "user").items()}
    assert roles["user@example.test"] == ("customer", [])                                 # a holder sees every camera
    assert roles["holder@example.test"] == ("customer_admin", [])
    assert one(demo, "SELECT count(*) FROM vsz.membership WHERE org_id = CAST(:c AS uuid) AND role = 'customer'", c=cid) == 1
    ask(demo, "account_plans", "user", id=cid)
    refused_op(demo, "account_plans", "holder", 404, id=cid)
    assert claims(demo, "holder") == {"vsz_role": "customer", "vsz_adapters": "adapter-01"}


def test_a_released_adapter_ends_its_shares(demo):
    demo.run("UPDATE vsz.adapter_activation SET released_at = now() WHERE adapter_id = "
             "(SELECT id FROM vsz.adapter WHERE thing_name = 'adapter-01')")
    assert people(demo)["user@example.test"]["cameras"] == []
    assert ask(demo, "account_users", "holder", id=demo_customer(demo))["things"] == []
    assert claims(demo, "user") == {"vsz_role": "customer"}


# --- where each camera is, for the camera list (/directory/me) -------------------------------------

def test_me_gives_each_accounts_adapters_for_the_camera_lists_groups(demo):
    site_id = str(one(demo, "SELECT id FROM vsz.site WHERE name = 'Home'"))
    hall = [{"thingName": "adapter-01", "name": "Hall", "siteId": site_id}]
    assert ask(demo, "me", "holder")["accounts"][0]["adapters"] == hall
    assert ask(demo, "me", "user")["accounts"][0]["adapters"] == hall                     # cam-03 is shared there
    ask(demo, "update_account_user", "holder", id=demo_customer(demo), userId=person(demo, "user@example.test"),
        superAdmin=False, cameras=[])
    assert ask(demo, "me", "user")["accounts"][0]["adapters"] == []                       # nothing shared: none


def test_the_holder_stores_the_sub_of_someone_they_invited_and_no_one_elses(demo):
    cid, sub = demo_customer(demo), "00000000-0000-4000-8000-000000000001"
    made = ask(demo, "add_account_user", "holder", id=cid, email="new@example.test", superAdmin=True)
    ask(demo, "link_sub", "holder", userId=made["userId"], sub=sub)
    assert one(demo, "SELECT cognito_sub::text FROM vsz.app_user WHERE email = 'new@example.test'") == sub
    later = "00000000-0000-4000-8000-000000000002"
    refused_op(demo, "link_sub", "holder", 403, userId=person(demo, "user@example.test"), sub=later)   # signed in
    theirs = ask(demo, "add_account_user", "admin", id=other_account(demo), email="theirs@example.test", superAdmin=True)
    refused_op(demo, "link_sub", "holder", 403, userId=theirs["userId"], sub=later)                   # another account's
