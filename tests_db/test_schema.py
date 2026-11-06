"""The directory's rules, kept by the database itself (001_directory.sql, DEALER-PANEL-PLAN.md §5).

Each test works as vsz_app -- the role the directory's code will use -- in a transaction that is
rolled back. A rule that holds here holds whatever the code above it does: who sits under whom,
which role fits which organisation, one holder per account, one open activation per adapter, shares
only within an account, plan versions never edited, and vsz_app able to do what it needs and not
one thing more.
"""
from dbhelpers import (activate, adapter, customer, dealer, member, one, person, platform, refused,
                       site)

CHECK, UNIQUE, FOREIGN_KEY, DENIED = "23514", "23505", "23503", "42501"


# --- what the migration seeds ----------------------------------------------------------------------

def test_one_platform_and_its_direct_dealer(app):
    assert app.run("SELECT name FROM vsz.org WHERE kind = 'platform'") == [["VideoSafeZone"]]
    assert app.run("SELECT name FROM vsz.org WHERE kind = 'dealer' AND parent_id = :p", p=platform(app)) == [["Direct"]]
    refused(app, "INSERT INTO vsz.org (kind, name) VALUES ('platform', 'Another')", UNIQUE)


def test_the_price_list_is_marked_as_placeholders(app):
    assert one(app, "SELECT count(*) FROM vsz.price_camera") == 3 * 2 * 3
    assert one(app, "SELECT value FROM vsz.price_setting WHERE key = 'placeholder_prices'") is True
    assert one(app, "SELECT value FROM vsz.price_setting WHERE key = 'retention_days'") == [7, 30, 90]


# --- organisations ---------------------------------------------------------------------------------

def test_a_customer_sits_under_a_dealer_and_a_dealer_under_the_platform(app):
    d = dealer(app)
    customer(app, d)
    refused(app, "INSERT INTO vsz.org (kind, parent_id, name) VALUES ('customer', :p, 'x')", CHECK, p=platform(app))
    refused(app, "INSERT INTO vsz.org (kind, parent_id, name) VALUES ('dealer', :p, 'x')", CHECK, p=d)
    refused(app, "INSERT INTO vsz.org (kind, name) VALUES ('dealer', 'orphan')", CHECK)


def test_an_organisations_kind_never_changes(app):
    d = dealer(app)
    refused(app, "UPDATE vsz.org SET kind = 'customer' WHERE id = :d", CHECK, d=d)


# --- people and roles (D6, D18) --------------------------------------------------------------------

def test_a_role_fits_its_kind_of_organisation(app):
    d = dealer(app)
    c = customer(app, d)
    refused(app, "INSERT INTO vsz.membership (user_id, org_id, role) VALUES (:u, :o, 'dealer')", CHECK,
            u=person(app, "a@example.com"), o=c)
    refused(app, "INSERT INTO vsz.membership (user_id, org_id, role) VALUES (:u, :o, 'system_admin')", CHECK,
            u=person(app, "b@example.com"), o=d)
    refused(app, "INSERT INTO vsz.membership (user_id, org_id, role) VALUES (:u, :o, 'customer')", CHECK,
            u=person(app, "c@example.com"), o=d)


def test_staff_hold_one_membership_and_no_customer_one(app):
    d1, d2 = dealer(app, "A"), dealer(app, "B")
    staff = person(app, "staff@example.com")
    member(app, staff, d1, "dealer")
    refused(app, "INSERT INTO vsz.membership (user_id, org_id, role) VALUES (:u, :o, 'dealer')", CHECK, u=staff, o=d2)
    refused(app, "INSERT INTO vsz.membership (user_id, org_id, role) VALUES (:u, :o, 'customer_user')", CHECK,
            u=staff, o=customer(app, d1))
    shopper = person(app, "shopper@example.com")
    member(app, shopper, customer(app, d1, "Shop"), "customer")
    refused(app, "INSERT INTO vsz.membership (user_id, org_id, role) VALUES (:u, :o, 'dealer')", CHECK, u=shopper, o=d2)


def test_one_holder_per_account_but_a_person_in_several_accounts(app):
    d = dealer(app)
    home, neighbour = customer(app, d, "Home"), customer(app, d, "Neighbour")
    anna, ben = person(app, "anna@example.com"), person(app, "ben@example.com")
    member(app, anna, home, "customer")
    refused(app, "INSERT INTO vsz.membership (user_id, org_id, role) VALUES (:u, :o, 'customer')", UNIQUE, u=ben, o=home)
    member(app, ben, neighbour, "customer")
    member(app, ben, home, "customer_user")            # Ben's own account, and cameras shared from Anna's
    member(app, anna, neighbour, "customer_admin")
    assert one(app, "SELECT count(*) FROM vsz.membership WHERE user_id = :u", u=ben) == 2


def test_an_email_is_one_person_whatever_its_case(app):
    person(app, "Anna@Example.com")
    refused(app, "INSERT INTO vsz.app_user (email) VALUES ('anna@example.COM')", UNIQUE)
    refused(app, "INSERT INTO vsz.app_user (email) VALUES ('not an address')", CHECK)


# --- adapters, activations, plans (D15, D19) -------------------------------------------------------

def test_an_activations_site_belongs_to_its_customer(app):
    d = dealer(app)
    c1, c2 = customer(app, d, "One"), customer(app, d, "Two")
    refused(app, "INSERT INTO vsz.adapter_activation (adapter_id, customer_id, site_id) VALUES (:a, :c, :s)",
            FOREIGN_KEY, a=adapter(app, "adapter-0901"), c=c1, s=site(app, c2))


def test_one_open_activation_per_adapter_and_a_release_frees_it(app):
    d = dealer(app)
    c1, c2 = customer(app, d, "One"), customer(app, d, "Two")
    a = adapter(app, "adapter-0902")
    first = activate(app, a, c1, site(app, c1))
    refused(app, "INSERT INTO vsz.adapter_activation (adapter_id, customer_id, site_id) VALUES (:a, :c, :s)",
            UNIQUE, a=a, c=c2, s=site(app, c2))
    app.run("UPDATE vsz.adapter_activation SET released_at = now() WHERE id = :i", i=first)
    activate(app, a, c2, site(app, c2, "Shop"))


def test_an_allocated_adapter_is_activated_only_for_its_dealers_customers(app):
    mine, theirs = dealer(app, "Mine"), dealer(app, "Theirs")
    a = adapter(app, "adapter-0903", dealer_id=mine)
    stranger = customer(app, theirs)
    message = refused(app, "INSERT INTO vsz.adapter_activation (adapter_id, customer_id, site_id) VALUES (:a, :c, :s)",
                      CHECK, a=a, c=stranger, s=site(app, stranger))
    assert "another dealer" in message
    own = customer(app, mine)
    activate(app, a, own, site(app, own))
    refused(app, "INSERT INTO vsz.adapter (thing_name, dealer_id) VALUES ('adapter-0904', :c)", CHECK, c=own)


def test_plan_versions_are_never_edited(app):
    d = dealer(app)
    c = customer(app, d)
    act = activate(app, adapter(app, "adapter-0905"), c, site(app, c))
    insert = ("INSERT INTO vsz.plan (activation_id, version, status, retention_days, billing_interval, "
              "cents_period, currency) VALUES (:a, :v, 'trial', 30, 'monthly', 0, 'EUR') RETURNING id")
    plan = one(app, insert, a=act, v=1)
    refused(app, insert, UNIQUE, a=act, v=1)
    refused(app, "UPDATE vsz.plan SET cents_period = 1 WHERE id = :p", DENIED, p=plan)
    refused(app, "DELETE FROM vsz.plan WHERE id = :p", DENIED, p=plan)


# --- camera shares (D18) ---------------------------------------------------------------------------

def _account_with_adapter(app, thing):
    d = dealer(app)
    acc = customer(app, d)
    a = adapter(app, thing)
    act = activate(app, a, acc, site(app, acc))
    return acc, a, act


SHARE = "INSERT INTO vsz.camera_share (account_id, user_id, camera_id, adapter_id) VALUES (:acc, :u, :cam, :a)"


def test_a_camera_is_shared_only_with_a_user_of_the_account(app):
    acc, a, _ = _account_with_adapter(app, "adapter-0906")
    guest, admin_ = person(app, "guest@example.com"), person(app, "admin@example.com")
    member(app, guest, acc, "customer_user")
    member(app, admin_, acc, "customer_admin")
    app.run(SHARE, acc=acc, u=guest, cam="cam-07", a=a)
    refused(app, SHARE, CHECK, acc=acc, u=admin_, cam="cam-07", a=a)                 # sees everything anyway
    refused(app, SHARE, CHECK, acc=acc, u=person(app, "x@example.com"), cam="cam-07", a=a)   # not a member at all
    refused(app, SHARE, CHECK, acc=acc, u=guest, cam="camera seven", a=a)


def test_a_share_must_be_on_one_of_the_accounts_adapters(app):
    acc, _, _ = _account_with_adapter(app, "adapter-0907")
    guest = person(app, "guest@example.com")
    member(app, guest, acc, "customer_user")
    message = refused(app, SHARE, CHECK, acc=acc, u=guest, cam="cam-08", a=adapter(app, "adapter-0908"))
    assert "isn't one of the account's" in message


def test_a_release_ends_the_shares_and_leaving_the_account_too(app):
    acc, a, act = _account_with_adapter(app, "adapter-0909")
    guest, other = person(app, "guest@example.com"), person(app, "other@example.com")
    member(app, guest, acc, "customer_user")
    member(app, other, acc, "customer_user")
    app.run(SHARE, acc=acc, u=guest, cam="cam-09", a=a)
    app.run(SHARE, acc=acc, u=other, cam="cam-09", a=a)
    app.run("DELETE FROM vsz.membership WHERE user_id = :u AND org_id = :o", u=other, o=acc)
    assert one(app, "SELECT count(*) FROM vsz.camera_share WHERE user_id = :u", u=other) == 0
    app.run("UPDATE vsz.adapter_activation SET released_at = now() WHERE id = :i", i=act)
    assert one(app, "SELECT count(*) FROM vsz.camera_share WHERE adapter_id = :a", a=a) == 0


# --- what vsz_app may do ---------------------------------------------------------------------------

def test_the_audit_log_is_append_only(app):
    event = one(app, "INSERT INTO vsz.audit_event (action) VALUES ('test') RETURNING id")
    refused(app, "UPDATE vsz.audit_event SET action = 'changed' WHERE id = :e", DENIED, e=event)
    refused(app, "DELETE FROM vsz.audit_event WHERE id = :e", DENIED, e=event)


def test_vsz_app_cannot_change_the_schema_or_read_the_migration_log(app):
    refused(app, "CREATE TABLE vsz.mine (id int)", DENIED)
    refused(app, "SELECT * FROM vsz.schema_migrations", DENIED)


def test_every_table_is_readable_by_vsz_app_and_none_by_public(superuser):
    rows = superuser.run(
        "SELECT c.relname, has_table_privilege('vsz_app', c.oid, 'SELECT'), "
        "  EXISTS (SELECT 1 FROM aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) acl "
        "          WHERE acl.grantee = 0) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'vsz' AND c.relkind = 'r' ORDER BY c.relname")
    tables = {name: (readable, public) for name, readable, public in rows}
    assert tables.pop("schema_migrations") == (False, False)
    assert tables, "no tables in schema vsz"
    assert {name for name, (readable, _) in tables.items() if not readable} == set()
    assert {name for name, (_, public) in tables.items() if public} == set()
