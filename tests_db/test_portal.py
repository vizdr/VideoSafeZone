"""The dealer portal's operations on the real schema (DEALER-PANEL-PLAN.md Phase 5, §7.1, D6, D19).

Asked as the demo people. What is pinned: a dealer works only on its own customers (another
dealer's is "no such customer"); a plan belongs to one adapter activation, and every change is a
new version priced by the server -- a price the page sends is ignored; two edits of one plan can't
overwrite each other unseen; a customer's total adds up its adapters' plans.

The seed's placeholder prices: 4MP motion at 30 days is 560 cents a month, 2MP continuous at 90
days 1250; the annual discount is 10 %, the trial 30 days.
"""
import pytest

from dbhelpers import SUBS, activate, adapter, ask, customer, dealer, dealer_id, demo_spec, one, refused_op, site
from loaders import load_lambda

directory = load_lambda("vsz_directory")

FOUR_4MP = [{"resolution": "4mp", "recording": "motion", "quantity": 4}]


@pytest.fixture
def demo(app):
    directory.operator_seed(app, demo_spec())
    return app


def demo_customer(app):
    return str(one(app, "SELECT id FROM vsz.org WHERE name = 'Demo customer'"))


def adapter_01(app):
    return str(one(app, "SELECT id FROM vsz.adapter WHERE thing_name = 'adapter-01'"))


def other_dealers_customer(app):
    """A customer of another dealer, with an activated adapter."""
    theirs = customer(app, dealer(app, "Other dealer"), "Not yours")
    a = adapter(app, "adapter-0201")
    activate(app, a, theirs, site(app, theirs))
    return str(theirs), str(a)


# --- customers -------------------------------------------------------------------------------------

def test_a_dealer_creates_its_customer_with_a_first_site(demo):
    made = ask(demo, "create_customer", "dealer", name=" Smith Ltd ", reference="S-1",
               email="office@smith.example", site={"name": "Shop", "timezone": "Europe/Vienna"},
               dealerId="00000000-0000-4000-8000-000000000000")        # a dealer's own, whatever it says
    got = ask(demo, "get_customer", "dealer", id=made["id"])
    assert (got["name"], got["reference"], got["dealer"]["name"], got["holder"]) == ("Smith Ltd", "S-1", "Demo dealer", None)
    assert got["sites"] == [{"id": made["siteId"], "name": "Shop", "timezone": "Europe/Vienna"}]
    assert one(demo, "SELECT action FROM vsz.audit_event WHERE target_id = CAST(:t AS uuid)", t=made["id"]) == "create_customer"


def test_owners_and_administrators_name_the_dealer(demo):
    shop = {"name": "Shop", "timezone": "Europe/Berlin"}
    refused_op(demo, "create_customer", "owner", 400, name="X", site=shop)
    made = ask(demo, "create_customer", "owner", name="Direct buyer", site=shop, dealerId=dealer_id(demo, "Direct"))
    assert ask(demo, "get_customer", "admin", id=made["id"])["dealer"]["name"] == "Direct"
    refused_op(demo, "create_customer", "holder", 403, name="X", site=shop)


@pytest.mark.parametrize("args", [
    {"name": "", "site": {"name": "Shop", "timezone": "Europe/Berlin"}},
    {"name": "X"},
    {"name": "X", "site": {"name": "", "timezone": "Europe/Berlin"}},
    {"name": "X", "site": {"name": "Shop", "timezone": "not a zone!"}},
    {"name": "X", "email": "nobody", "site": {"name": "Shop", "timezone": "Europe/Berlin"}},
])
def test_a_customer_needs_a_name_and_a_site_with_a_time_zone(demo, args):
    refused_op(demo, "create_customer", "dealer", 400, **args)


def test_another_dealers_customer_is_no_such_customer(demo):
    theirs, their_adapter = other_dealers_customer(demo)
    for op in ("get_customer", "customer_adapters", "customer_users"):
        assert refused_op(demo, op, "dealer", 404, id=theirs) == "no such customer"
        ask(demo, op, "owner", id=theirs)
    refused_op(demo, "update_customer", "dealer", 404, id=theirs, name="Mine now")
    refused_op(demo, "put_plan", "dealer", 404, adapterId=their_adapter, lines=FOUR_4MP, retentionDays=30, interval="monthly")
    refused_op(demo, "plan_history", "dealer", 404, adapterId=their_adapter)
    assert {c["name"] for c in ask(demo, "list_customers", "dealer")} == {"Demo customer", "Suspended demo customer"}


def test_suspending_a_customer_signs_its_people_out(demo):
    cid = demo_customer(demo)
    assert ask(demo, "update_customer", "dealer", id=cid, status="suspended", reference=" ")["changed"] == ["reference", "status"]
    assert directory.token_claims(demo, SUBS["holder"], "holder@example.test", True, False) is None
    ask(demo, "update_customer", "dealer", id=cid, status="active")
    assert directory.token_claims(demo, SUBS["holder"], "holder@example.test", True, False) is not None
    refused_op(demo, "update_customer", "dealer", 400, id=cid)                       # nothing to change
    refused_op(demo, "update_customer", "dealer", 400, id=cid, status="closed")


def test_the_customers_people_holder_first_and_who_may_manage_them(demo):
    people = ask(demo, "customer_users", "dealer", id=demo_customer(demo))
    assert [(p["email"], p["role"]) for p in people] == [
        ("holder@example.test", "customer"), ("superadmin@example.test", "customer_admin"),
        ("disabled@example.test", "customer_user"), ("user@example.test", "customer_user")]
    assert all(p["manageable"] for p in people)
    assert all(p["manageable"] for p in ask(demo, "customer_users", "owner", id=demo_customer(demo)))


# --- a plan per adapter: versions, prices, history (D19) ------------------------------------------

def test_a_plan_change_is_a_new_version_priced_by_the_server(demo):
    a = adapter_01(demo)
    out = ask(demo, "put_plan", "dealer", adapterId=a, lines=FOUR_4MP, retentionDays=30, interval="monthly",
              basedOn=1, centsPeriod=1)                                               # the page's price: ignored
    plan = out["plan"]
    assert out["changed"] and (plan["version"], plan["centsPeriod"], plan["centsMonth"]) == (2, 2240, 2240)
    assert (plan["status"], plan["createdBy"], plan["cameras"]) == ("trial", "dealer@example.test", 4)
    assert plan["trialEndsAt"] is not None                                           # the trial carries on
    annual = ask(demo, "put_plan", "owner", adapterId=a, lines=FOUR_4MP, retentionDays=30, interval="annual", basedOn=2)["plan"]
    assert (annual["version"], annual["centsPeriod"], annual["centsMonth"]) == (3, 24192, 2016)  # 2240 x 12 less 10 %
    history = ask(demo, "plan_history", "dealer", adapterId=a)
    assert history["adapter"] == {"thingName": "adapter-01", "name": "Hall", "site": "Home"}
    assert [(p["version"], p["interval"], p["centsPeriod"], p["createdBy"]) for p in history["plans"]] == [
        (3, "annual", 24192, "owner@example.test"), (2, "monthly", 2240, "dealer@example.test"), (1, "monthly", 0, None)]


def test_an_edit_of_a_version_that_isnt_the_newest_is_refused(demo):
    a = adapter_01(demo)
    ask(demo, "put_plan", "dealer", adapterId=a, lines=FOUR_4MP, retentionDays=30, interval="monthly", basedOn=1)
    message = refused_op(demo, "put_plan", "owner", 409, adapterId=a, lines=[], retentionDays=7,
                         interval="monthly", basedOn=1)
    assert "now version 2" in message
    refused_op(demo, "cancel_plan", "owner", 409, adapterId=a, basedOn=1)


def test_saving_the_same_plan_again_makes_no_version(demo):
    a = adapter_01(demo)
    ask(demo, "put_plan", "dealer", adapterId=a, lines=FOUR_4MP, retentionDays=30, interval="monthly")
    again = ask(demo, "put_plan", "dealer", adapterId=a, lines=FOUR_4MP + [
        {"resolution": "8mp", "recording": "continuous", "quantity": 0}], retentionDays=30, interval="monthly")
    assert (again["changed"], again["plan"]["version"]) == (False, 2)


@pytest.mark.parametrize("change", [
    {"retentionDays": 14}, {"retentionDays": True}, {"interval": "weekly"},
    {"lines": [{"resolution": "4mp", "recording": "motion", "quantity": 101}]},
    {"lines": [{"resolution": "4mp", "recording": "motion", "quantity": -1}]},
    {"lines": [{"resolution": "4mp", "recording": "motion", "quantity": 1.5}]},
    {"lines": [{"resolution": "16mp", "recording": "motion", "quantity": 1}]},
    {"lines": FOUR_4MP * 2}, {"lines": "four"},
])
def test_what_a_plan_may_hold(demo, change):
    args = {"adapterId": adapter_01(demo), "lines": FOUR_4MP, "retentionDays": 30, "interval": "monthly", **change}
    refused_op(demo, "put_plan", "dealer", 400, **args)


def test_cancel_and_subscribe_again(demo):
    a = adapter_01(demo)
    ask(demo, "put_plan", "dealer", adapterId=a, lines=FOUR_4MP, retentionDays=30, interval="monthly")
    cancelled = ask(demo, "cancel_plan", "dealer", adapterId=a, basedOn=2)["plan"]
    assert (cancelled["version"], cancelled["status"], cancelled["centsPeriod"], cancelled["cameras"]) == (3, "cancelled", 0, 4)
    assert ask(demo, "customer_adapters", "dealer", id=demo_customer(demo))["total"]["centsMonth"] == 0
    refused_op(demo, "cancel_plan", "dealer", 409, adapterId=a)
    again = ask(demo, "put_plan", "dealer", adapterId=a, lines=FOUR_4MP, retentionDays=30, interval="monthly")["plan"]
    assert (again["version"], again["status"]) == (4, "trial")                      # still within the trial


def test_a_trial_that_has_ended_is_an_active_plan(demo):
    d = dealer_id(demo)
    late = customer(demo, d, "Late")
    a = adapter(demo, "adapter-0301", d)
    act = activate(demo, a, late, site(demo, late))
    demo.run("INSERT INTO vsz.plan (activation_id, version, status, retention_days, billing_interval, cents_period, "
             "currency, trial_ends_at) VALUES (:a, 1, 'trial', 30, 'monthly', 0, 'EUR', now() - interval '1 day')", a=act)
    listed = ask(demo, "customer_adapters", "dealer", id=str(late))["adapters"][0]["plan"]
    assert (listed["status"], listed["effectiveStatus"]) == ("trial", "active")
    saved = ask(demo, "put_plan", "dealer", adapterId=str(a), lines=FOUR_4MP, retentionDays=30, interval="monthly")["plan"]
    assert saved["status"] == "active"


def test_a_plan_belongs_to_one_activation(demo):
    """Released, the adapter's plan is history; at its next customer it starts afresh (D19)."""
    a, first = adapter_01(demo), demo_customer(demo)
    ask(demo, "put_plan", "dealer", adapterId=a, lines=FOUR_4MP, retentionDays=30, interval="monthly")
    demo.run("UPDATE vsz.adapter_activation SET released_at = now() WHERE adapter_id = CAST(:a AS uuid)", a=a)
    refused_op(demo, "put_plan", "dealer", 404, adapterId=a, lines=FOUR_4MP, retentionDays=30, interval="monthly")
    refused_op(demo, "plan_history", "dealer", 404, adapterId=a)
    assert ask(demo, "customer_adapters", "dealer", id=first)["adapters"] == []
    second = customer(demo, dealer_id(demo), "Second owner")
    activate(demo, a, second, site(demo, second))
    assert ask(demo, "customer_adapters", "dealer", id=str(second))["adapters"][0]["plan"] is None
    fresh = ask(demo, "put_plan", "dealer", adapterId=a, lines=[], retentionDays=7, interval="monthly")["plan"]
    assert (fresh["version"], fresh["status"]) == (1, "active")
    assert one(demo, "SELECT count(*) FROM vsz.plan p JOIN vsz.adapter_activation act ON act.id = p.activation_id "
                     "WHERE act.customer_id = CAST(:c AS uuid)", c=first) == 2       # the first customer's history stays


def test_the_customer_list_adds_up_each_customers_plans(demo):
    d = dealer_id(demo)
    cid = demo_customer(demo)
    a2 = adapter(demo, "adapter-0302", d)
    activate(demo, a2, cid, site(demo, cid, "Garage"))
    ask(demo, "put_plan", "dealer", adapterId=adapter_01(demo), lines=FOUR_4MP, retentionDays=30, interval="monthly")
    ask(demo, "put_plan", "dealer", adapterId=str(a2), lines=[{"resolution": "2mp", "recording": "continuous",
                                                               "quantity": 1}], retentionDays=90, interval="monthly")
    listed = {c["name"]: c for c in ask(demo, "list_customers", "dealer")}["Demo customer"]
    assert (listed["adapters"], listed["things"], listed["inPlans"]) == (2, ["adapter-01", "adapter-0302"], 5)
    assert (listed["centsMonth"], listed["currency"]) == (2240 + 1250, "EUR")
    assert listed["holder"] == {"email": "holder@example.test", "status": "active"}
    total = ask(demo, "customer_adapters", "dealer", id=cid)["total"]
    assert total == {"adapters": 2, "centsMonth": 3490, "cameras": 5, "currency": "EUR"}


def test_a_dealer_sees_an_adapter_activated_at_its_customer(demo):
    cid = demo_customer(demo)
    loose = adapter(demo, "adapter-0303")                                            # in nobody's stock
    activate(demo, loose, cid, site(demo, cid, "Shed"))
    assert "adapter-0303" in {a["thingName"] for a in ask(demo, "list_adapters", "dealer")}


# --- the dealer's own details ----------------------------------------------------------------------

def test_a_dealer_keeps_its_own_details(demo):
    out = ask(demo, "update_own_dealer", "dealer", phone="+49 30 1234", address={"city": "Berlin", "line1": " Main St 1 "})
    assert out["changed"] == ["address", "phone"]
    got = ask(demo, "get_dealer", "dealer")
    assert (got["name"], got["phone"], got["address"]) == ("Demo dealer", "+49 30 1234", {"line1": "Main St 1", "city": "Berlin"})
    refused_op(demo, "update_own_dealer", "dealer", 409, name="direct")
    refused_op(demo, "update_own_dealer", "dealer", 400, address={"planet": "Mars"})
    refused_op(demo, "update_own_dealer", "dealer", 400)
    refused_op(demo, "get_dealer", "owner", 403)
    refused_op(demo, "update_own_dealer", "dealer", 400, status="suspended")         # not its own to suspend
