"""vsz-directory against a real server: the claims in every ID token, and the demo seed (Phase 2).

The claims decide what every Lambda lets a caller do (D4), so each role's are checked here on the
real schema, as vsz_app: the staff roles, a holder and a Super Admin (full access to the account's
adapters), a user with one shared camera (view only), a person in two accounts, and everyone who
must get no token at all -- unknown, disabled, suspended, or with nothing left after a release.
"""
import uuid

import pytest

from dbhelpers import activate, adapter, customer, dealer, member, one, person, platform, site
from loaders import load_lambda

directory = load_lambda("vsz_directory")

SUBS = {name: str(uuid.uuid4()) for name in
        ("admin", "owner", "dealer", "holder", "superadmin", "user", "disabled", "suspended")}


def demo_spec():
    """The shape cloud/provision.sh directory-seed sends."""
    e = {name: f"{name}@example.test" for name in SUBS}
    return {
        "users": [{"email": e[n], "sub": SUBS[n], "status": "disabled" if n == "disabled" else "active"}
                  for n in SUBS],
        "platform": {"members": [{"email": e["admin"], "role": "system_admin"},
                                 {"email": e["owner"], "role": "system_owner"}]},
        "dealers": [{
            "name": "Demo dealer", "members": [{"email": e["dealer"], "role": "dealer"}],
            "customers": [
                {"name": "Demo customer", "reference": "DEMO-1",
                 "members": [{"email": e["holder"], "role": "customer"},
                             {"email": e["superadmin"], "role": "customer_admin"},
                             {"email": e["user"], "role": "customer_user"},
                             {"email": e["disabled"], "role": "customer_user"}],
                 "sites": [{"name": "Home", "timezone": "Europe/Berlin",
                            "adapters": [{"thing": "adapter-01", "name": "Hall"}]}],
                 "shares": [{"email": e["user"], "camera": "cam-03", "thing": "adapter-01"}]},
                {"name": "Suspended demo customer", "status": "suspended",
                 "members": [{"email": e["suspended"], "role": "customer"}]},
            ]}],
    }


@pytest.fixture
def seeded(app):
    first = directory.operator_seed(app, demo_spec())
    return {"app": app, "first": first, "again": directory.operator_seed(app, demo_spec())}


def claims(app, name, first=False):
    return directory.token_claims(app, SUBS[name], f"{name}@example.test", True, first)


# --- the seed --------------------------------------------------------------------------------------

def test_the_seed_builds_the_demo_once(seeded):
    assert any(line.startswith("trial plan for adapter-01") for line in [s.strip() for s in seeded["first"]])
    assert seeded["again"] == ["nothing to do: the demo directory is there"]
    app = seeded["app"]
    assert one(app, "SELECT count(*) FROM vsz.plan p JOIN vsz.adapter_activation a ON a.id = p.activation_id "
                    "JOIN vsz.adapter d ON d.id = a.adapter_id WHERE d.thing_name = 'adapter-01'") == 1
    assert one(app, "SELECT currency FROM vsz.plan LIMIT 1") == "EUR"


# --- the claims, role by role (D4, D6, D18) --------------------------------------------------------

def test_staff_claims(seeded):
    app = seeded["app"]
    platform_id = str(platform(app))
    dealer_id = str(one(app, "SELECT id FROM vsz.org WHERE name = 'Demo dealer'"))
    assert claims(app, "admin") == {"vsz_role": "system_admin", "vsz_org": platform_id, "vsz_adapters": "*"}
    assert claims(app, "owner") == {"vsz_role": "system_owner", "vsz_org": platform_id}
    assert claims(app, "dealer") == {"vsz_role": "dealer", "vsz_org": dealer_id}


def test_holder_and_super_admin_get_the_accounts_adapters(seeded):
    app = seeded["app"]
    assert claims(app, "holder") == {"vsz_role": "customer", "vsz_adapters": "adapter-01"}
    assert claims(app, "superadmin") == {"vsz_role": "customer", "vsz_adapters": "adapter-01"}


def test_a_user_gets_only_the_shared_camera(seeded):
    assert claims(seeded["app"], "user") == {"vsz_role": "customer", "vsz_cameras": "cam-03@adapter-01"}


@pytest.mark.parametrize("name", ["disabled", "suspended"])
def test_disabled_and_suspended_get_no_token(seeded, name):
    assert claims(seeded["app"], name) is None
    preview = directory.claims_for_email(seeded["app"], f"{name}@example.test")
    assert preview["found"] and not preview["signsIn"]


def test_someone_unknown_gets_no_token(seeded):
    assert directory.token_claims(seeded["app"], str(uuid.uuid4()), "nobody@example.test", True, True) is None
    assert directory.claims_for_email(seeded["app"], "nobody@example.test") == {"found": False}


def test_a_suspended_dealer_signs_its_staff_out(seeded):
    app = seeded["app"]
    app.run("UPDATE vsz.org SET status = 'suspended' WHERE name = 'Demo dealer'")
    assert claims(app, "dealer") is None
    assert claims(app, "holder") == {"vsz_role": "customer", "vsz_adapters": "adapter-01"}   # its customers go on


def test_a_release_takes_the_adapter_and_its_shares_away(seeded):
    app = seeded["app"]
    app.run("UPDATE vsz.adapter_activation SET released_at = now()")
    assert claims(app, "holder") == {"vsz_role": "customer"}      # still signs in: the account is there
    assert claims(app, "user") == {"vsz_role": "customer"}


def test_a_person_in_two_accounts_gets_both(app):
    d = dealer(app)
    mine, neighbours = customer(app, d, "Mine"), customer(app, d, "Neighbours")
    activate(app, adapter(app, "adapter-0101"), mine, site(app, mine))
    theirs = adapter(app, "adapter-0102")
    activate(app, theirs, neighbours, site(app, neighbours))
    sub = str(uuid.uuid4())
    me = one(app, "INSERT INTO vsz.app_user (email, cognito_sub) VALUES ('me@example.test', :s) RETURNING id", s=sub)
    member(app, me, mine, "customer")
    member(app, me, neighbours, "customer_user")
    app.run("INSERT INTO vsz.camera_share (account_id, user_id, camera_id, adapter_id) "
            "VALUES (:a, :u, 'cam-11', :d)", a=neighbours, u=me, d=theirs)
    assert directory.token_claims(app, sub, "me@example.test", True, False) == {
        "vsz_role": "customer", "vsz_adapters": "adapter-0101", "vsz_cameras": "cam-11@adapter-0102"}


def test_shares_in_the_token_are_capped(app):
    d = dealer(app)
    acc = customer(app, d)
    a = adapter(app, "adapter-0103")
    activate(app, a, acc, site(app, acc))
    sub = str(uuid.uuid4())
    guest = one(app, "INSERT INTO vsz.app_user (email, cognito_sub) VALUES ('g@example.test', :s) RETURNING id", s=sub)
    member(app, guest, acc, "customer_user")
    for n in range(directory.MAX_SHARES + 5):
        app.run("INSERT INTO vsz.camera_share (account_id, user_id, camera_id, adapter_id) "
                "VALUES (:acc, :u, :c, :a)", acc=acc, u=guest, c=f"cam-{n:04d}", a=a)
    shared = directory.token_claims(app, sub, "g@example.test", True, False)["vsz_cameras"].split(",")
    assert len(shared) == directory.MAX_SHARES


# --- linking, first sign-in, a changed address -----------------------------------------------------

def test_the_first_sign_in_links_by_verified_email_and_activates(app):
    d = dealer(app)
    acc = customer(app, d)
    invited = person(app, "new@example.test")              # no cognito_sub yet, status 'invited'
    member(app, invited, acc, "customer")
    sub = str(uuid.uuid4())
    assert directory.token_claims(app, sub, "NEW@example.test", False, True) is None   # unverified: no link
    assert directory.token_claims(app, sub, "NEW@example.test", True, True) == {"vsz_role": "customer"}
    status, linked, signed_in = app.run("SELECT status, cognito_sub::text, last_sign_in_at IS NOT NULL "
                                        "FROM vsz.app_user WHERE id = :u", u=invited)[0]
    assert (status, linked, signed_in) == ("active", sub, True)


def test_a_renewal_doesnt_count_as_a_sign_in(app):
    d = dealer(app)
    acc = customer(app, d)
    sub = str(uuid.uuid4())
    u = one(app, "INSERT INTO vsz.app_user (email, cognito_sub) VALUES ('r@example.test', :s) RETURNING id", s=sub)
    member(app, u, acc, "customer")
    directory.token_claims(app, sub, "r@example.test", True, False)
    assert app.run("SELECT status, last_sign_in_at FROM vsz.app_user WHERE id = :u", u=u)[0] == ["invited", None]


def test_a_confirmed_new_address_reaches_the_directory(app):
    d = dealer(app)
    acc = customer(app, d)
    sub = str(uuid.uuid4())
    u = one(app, "INSERT INTO vsz.app_user (email, cognito_sub) VALUES ('old@example.test', :s) RETURNING id", s=sub)
    member(app, u, acc, "customer")
    person(app, "taken@example.test")
    directory.token_claims(app, sub, "taken@example.test", True, False)          # someone else's: kept
    assert one(app, "SELECT email FROM vsz.app_user WHERE id = :u", u=u) == "old@example.test"
    directory.token_claims(app, sub, "renamed@example.test", True, False)
    assert one(app, "SELECT email FROM vsz.app_user WHERE id = :u", u=u) == "renamed@example.test"
