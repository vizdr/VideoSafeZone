"""Billing on the real schema (DEALER-PANEL-PLAN.md Phase 8, D9, D19): the fake period run, the
invoices in each role's scope, the next invoice's estimate, and what an invoice may hold (003).

What is pinned: a run makes one invoice per customer with something to pay, with a line per adapter
plan at the price stored with the plan (an annual plan a twelfth of its year); a trial adds no line
until it has ended; a month is billed as its plans stood at its end; a second run of a month bills
only those not billed yet; only owners and administrators run it; a dealer sees its own customers'
invoices only. The database keeps an invoice's own rules whatever the code does.

The test's transaction is never committed, so the total's check -- made at COMMIT -- is made here
with SET CONSTRAINTS ALL IMMEDIATE (committed()).
"""
import datetime

import pytest

from dbhelpers import activate, adapter, ask, customer, dealer, dealer_id, demo_spec, one, refused, refused_op, site
from loaders import load_lambda

directory = load_lambda("vsz_directory")

UTC = datetime.timezone.utc
FOUR_4MP = [{"resolution": "4mp", "recording": "motion", "quantity": 4}]       # 2240 cents a month at 30 days


@pytest.fixture
def demo(app):
    directory.operator_seed(app, demo_spec())
    return app


def month(offset=0):
    """('2026-11', its first day as a UTC datetime) for this month moved by `offset` months."""
    today = datetime.datetime.now(UTC).date()
    index = today.year * 12 + today.month - 1 + offset
    first = datetime.datetime(index // 12, index % 12 + 1, 1, tzinfo=UTC)
    return first.strftime("%Y-%m"), first


def plan(app, act, version, status, cents, interval="monthly", created=None, trial_ends=None, currency="EUR"):
    """A plan version written directly (vsz_app may insert versions), at a chosen time."""
    pid = one(app, "INSERT INTO vsz.plan (activation_id, version, status, retention_days, billing_interval, "
                   "cents_period, currency, trial_ends_at, created_at) VALUES (:a, :v, :s, 30, :i, :c, :cur, "
                   "CAST(:t AS timestamptz), coalesce(CAST(:at AS timestamptz), now())) RETURNING id",
              a=act, v=version, s=status, i=interval, c=cents, cur=currency, t=trial_ends, at=created)
    app.run("INSERT INTO vsz.plan_line (plan_id, resolution, recording, quantity) VALUES (:p, '4mp', 'motion', 2)", p=pid)
    return pid


def with_adapter(app, cust, thing, activated=None, site_name="Home"):
    """An adapter in nobody's stock, activated at the customer (at a chosen time)."""
    a = adapter(app, thing)
    s = site(app, cust, site_name)
    if activated is None:
        return activate(app, a, cust, s)
    return one(app, "INSERT INTO vsz.adapter_activation (adapter_id, customer_id, site_id, activated_at) "
                    "VALUES (:a, :c, :s, :t) RETURNING id", a=a, c=cust, s=s, t=activated)


def paid(app, name, thing, cents=2240, interval="monthly", dealer_org=None):
    """A customer with one adapter on an active plan."""
    cust = customer(app, dealer_org or dealer_id(app), name)
    act = with_adapter(app, cust, thing)
    return str(cust), plan(app, act, 1, "active", cents, interval)


def committed(app):
    """The checks the database makes at COMMIT, made now."""
    app.run("SET CONSTRAINTS ALL IMMEDIATE")
    app.run("SET CONSTRAINTS ALL DEFERRED")


def invoices_of(app, who="owner", **args):
    return {i["customer"]["name"]: i for i in ask(app, "list_invoices", who, **args)["invoices"]}


# --- the run ---------------------------------------------------------------------------------------

def test_a_run_bills_each_customer_once_with_a_line_per_paid_adapter_plan(demo):
    now, _ = month()
    demo_customer = str(one(demo, "SELECT id FROM vsz.org WHERE name = 'Demo customer'"))
    hall = str(one(demo, "SELECT id FROM vsz.adapter WHERE thing_name = 'adapter-01'"))
    ask(demo, "put_plan", "dealer", adapterId=hall, lines=FOUR_4MP, retentionDays=30, interval="monthly")  # still a trial
    garage = with_adapter(demo, demo_customer, "adapter-0401", site_name="Garage")
    garage_plan = plan(demo, garage, 1, "active", 1250)
    paid(demo, "Annual Ltd", "adapter-0402", 24192, "annual")
    both, first = paid(demo, "Two adapters", "adapter-0403", 560)
    second = plan(demo, with_adapter(demo, both, "adapter-0404", site_name="Yard"), 1, "active", 12000, "annual")
    _, gone = paid(demo, "Cancelled Ltd", "adapter-0405", 999)
    plan(demo, one(demo, "SELECT activation_id FROM vsz.plan WHERE id = :p", p=gone), 2, "cancelled", 0)

    run = ask(demo, "run_billing_period", "admin", period=now)
    committed(demo)                                                   # every total is the sum of its lines
    assert {i["customer"]["name"]: (i["cents"], i["lines"]) for i in run["invoices"]} == {
        "Demo customer": (1250, 1),                                   # adapter-01's trial adds nothing
        "Annual Ltd": (2016, 1),                                      # a twelfth of 24192, to the cent
        "Two adapters": (560 + 1000, 2)}
    assert (run["period"], run["alreadyInvoiced"], run["nothingToBill"], run["skipped"]) == (now, 0, 2, [])
    listed = invoices_of(demo, period=now)
    assert [ln["planId"] for ln in listed["Demo customer"]["lines"]] == [str(garage_plan)]
    assert {(ln["planId"], ln["cents"], ln["centsPeriod"], ln["interval"]) for ln in listed["Two adapters"]["lines"]} == {
        (str(first), 560, 560, "monthly"), (str(second), 1000, 12000, "annual")}
    inv = listed["Annual Ltd"]
    assert (inv["status"], inv["provider"], inv["currency"], inv["dealer"]["name"]) == ("issued", "fake", "EUR", "Demo dealer")
    assert one(demo, "SELECT detail ->> 'invoices' FROM vsz.audit_event WHERE action = 'run_billing_period'") == "3"


def test_a_line_is_the_price_stored_with_the_plan_not_todays_price_list(demo):
    now, _ = month()
    paid(demo, "Smith", "adapter-0410", 2240)
    demo.run("UPDATE vsz.price_camera SET cents_month = cents_month * 10")    # the owner raises every price
    assert ask(demo, "run_billing_period", "owner", period=now)["invoices"][0]["cents"] == 2240


def test_a_trial_adds_no_line_until_it_has_ended(demo):
    now, _ = month()
    trial = customer(demo, dealer_id(demo), "On trial")
    plan(demo, with_adapter(demo, trial, "adapter-0420"), 1, "trial", 2240,
         trial_ends=datetime.datetime.now(UTC) + datetime.timedelta(days=5))
    ended = customer(demo, dealer_id(demo), "Trial ended")
    plan(demo, with_adapter(demo, ended, "adapter-0421"), 1, "trial", 2240,
         trial_ends=datetime.datetime.now(UTC) - datetime.timedelta(days=1))
    run = ask(demo, "run_billing_period", "admin", period=now)
    assert [(i["customer"]["name"], i["cents"]) for i in run["invoices"]] == [("Trial ended", 2240)]
    assert ask(demo, "billing_preview", "dealer", customerId=str(trial))["lines"] == []


def test_a_month_is_billed_as_its_plans_stood_at_its_end(demo):
    last, start = month(-1)
    day = datetime.timedelta(days=1)
    changed = customer(demo, dealer_id(demo), "Changed since")
    act = with_adapter(demo, changed, "adapter-0430", activated=start)
    old = plan(demo, act, 1, "active", 1000, created=start + day)
    plan(demo, act, 2, "active", 3000)                                 # this month: not last month's
    late = customer(demo, dealer_id(demo), "Started this month")
    plan(demo, with_adapter(demo, late, "adapter-0431"), 1, "active", 500)
    left = customer(demo, dealer_id(demo), "Left last month")
    gone = with_adapter(demo, left, "adapter-0432", activated=start)
    plan(demo, gone, 1, "active", 700, created=start + day)
    demo.run("UPDATE vsz.adapter_activation SET released_at = :t WHERE id = :a", t=start + 2 * day, a=gone)
    stayed = customer(demo, dealer_id(demo), "Left this month")
    kept = with_adapter(demo, stayed, "adapter-0433", activated=start)
    plan(demo, kept, 1, "active", 800, created=start + day)
    demo.run("UPDATE vsz.adapter_activation SET released_at = now() WHERE id = :a", a=kept)
    ended_mid = customer(demo, dealer_id(demo), "Trial ended last month")
    plan(demo, with_adapter(demo, ended_mid, "adapter-0434", activated=start), 1, "trial", 900,
         created=start, trial_ends=start + 10 * day)

    run = ask(demo, "run_billing_period", "admin", period=last)
    assert {i["customer"]["name"]: i["cents"] for i in run["invoices"]} == {
        "Changed since": 1000, "Left this month": 800, "Trial ended last month": 900}
    assert invoices_of(demo, period=last)["Changed since"]["lines"][0]["planId"] == str(old)
    assert run["periodEnd"] == month()[1].date().isoformat() and run["billedAt"].startswith(month()[0])
    this = {i["customer"]["name"]: i["cents"] for i in ask(demo, "run_billing_period", "admin", period=month()[0])["invoices"]}
    assert this == {"Changed since": 3000, "Started this month": 500, "Trial ended last month": 900}


def test_running_a_month_again_bills_only_those_not_billed_yet(demo):
    now, _ = month()
    first, _ = paid(demo, "First", "adapter-0440", 1000)
    quit_, _ = paid(demo, "Cancelled since", "adapter-0442", 3000)
    assert len(ask(demo, "run_billing_period", "admin", period=now)["invoices"]) == 2
    plan(demo, one(demo, "SELECT id FROM vsz.adapter_activation WHERE customer_id = :c", c=first), 2, "active", 5000)
    plan(demo, one(demo, "SELECT id FROM vsz.adapter_activation WHERE customer_id = :c", c=quit_), 2, "cancelled", 0)
    paid(demo, "Second", "adapter-0441", 2000)
    again = ask(demo, "run_billing_period", "owner", period=now)
    assert [(i["customer"]["name"], i["cents"]) for i in again["invoices"]] == [("Second", 2000)]
    assert (again["alreadyInvoiced"], again["nothingToBill"]) == (2, 2)  # "Cancelled since" is invoiced already
                                                                         # (the seed's two have nothing to pay)
    assert invoices_of(demo, period=now)["First"]["cents"] == 1000    # its invoice stands as issued
    committed(demo)


@pytest.mark.parametrize("period", [None, "", "2026-13", "2026-1", "26-11", 202611, "2026-11-01"])
def test_a_period_is_a_month(demo, period):
    assert "period" in refused_op(demo, "run_billing_period", "admin", 400, period=period)


def test_a_month_that_hasnt_begun_is_refused(demo):
    paid(demo, "Early", "adapter-0450")
    assert "hasn't begun" in refused_op(demo, "run_billing_period", "admin", 400, period=month(1)[0])
    assert one(demo, "SELECT count(*) FROM vsz.invoice") == 0


def test_only_owners_and_administrators_run_billing(demo):
    now, _ = month()
    paid(demo, "Shop", "adapter-0460")
    for who in ("dealer", "holder", "superadmin", "user"):
        refused_op(demo, "run_billing_period", who, 403, period=now)
    assert len(ask(demo, "run_billing_period", "owner", period=now)["invoices"]) == 1


# --- seeing invoices -------------------------------------------------------------------------------

def test_a_dealer_sees_only_its_own_customers_invoices(demo):
    now, _ = month()
    mine, _ = paid(demo, "Mine", "adapter-0470")
    other = dealer(demo, "Other dealer")
    theirs, _ = paid(demo, "Theirs", "adapter-0471", dealer_org=other)
    ask(demo, "run_billing_period", "admin", period=now)
    assert set(invoices_of(demo, "dealer")) == {"Mine"}
    assert set(invoices_of(demo, "dealer", dealerId=str(other))) == {"Mine"}       # a dealer's own, whatever it asks
    assert refused_op(demo, "list_invoices", "dealer", 404, customerId=theirs) == "no such customer"
    assert set(invoices_of(demo, "owner")) == {"Mine", "Theirs"}
    assert set(invoices_of(demo, "admin", dealerId=str(other))) == {"Theirs"}
    assert set(invoices_of(demo, "owner", customerId=mine)) == {"Mine"}
    assert invoices_of(demo, "owner", period=month(-1)[0]) == {}
    for who in ("holder", "user"):
        refused_op(demo, "list_invoices", who, 403)
    assert ask(demo, "list_invoices", "owner")["truncated"] is False


def test_the_estimate_is_what_the_run_would_bill_and_writes_nothing(demo):
    now, _ = month()
    cust, plan_id = paid(demo, "Estimate", "adapter-0480", 2240)
    est = ask(demo, "billing_preview", "dealer", customerId=cust)
    assert (est["period"], est["cents"], est["currency"], est["invoiceId"]) == (now, 2240, "EUR", None)
    assert [(ln["planId"], ln["thingName"], ln["site"], ln["cameras"]) for ln in est["lines"]] == [
        (str(plan_id), "adapter-0480", "Home", 2)]
    assert one(demo, "SELECT count(*) FROM vsz.invoice") == 0
    made = ask(demo, "run_billing_period", "admin", period=now)["invoices"][0]
    assert ask(demo, "billing_preview", "owner", customerId=cust)["invoiceId"] == made["id"]
    theirs, _ = paid(demo, "Not yours", "adapter-0481", dealer_org=dealer(demo, "Another"))
    refused_op(demo, "billing_preview", "dealer", 404, customerId=theirs)


# --- what an invoice may hold (003_invoice_rules.sql) ----------------------------------------------

def _invoice(app, cust, cents, currency="EUR"):
    return one(app, "INSERT INTO vsz.invoice (customer_id, period_start, period_end, cents, currency, status) "
                    "VALUES (:c, DATE '2026-10-01', DATE '2026-11-01', :x, :cur, 'issued') RETURNING id",
               c=cust, x=cents, cur=currency)


def test_an_invoice_bills_only_its_own_customers_plans_in_its_currency(demo):
    a, a_plan = paid(demo, "A", "adapter-0490", 1000)
    _, b_plan = paid(demo, "B", "adapter-0491", 2000)
    inv = _invoice(demo, a, 1000)
    msg = refused(demo, "INSERT INTO vsz.invoice_line (invoice_id, plan_id, cents) VALUES (:i, :p, 2000)", "23514",
                  i=inv, p=b_plan)
    assert "own customer" in msg
    dollars = plan(demo, one(demo, "SELECT activation_id FROM vsz.plan WHERE id = :p", p=a_plan), 2, "active", 1000,
                   currency="USD")
    refused(demo, "INSERT INTO vsz.invoice_line (invoice_id, plan_id, cents) VALUES (:i, :p, 1000)", "23514",
            i=inv, p=dollars)
    demo.run("INSERT INTO vsz.invoice_line (invoice_id, plan_id, cents) VALUES (:i, :p, 1000)", i=inv, p=a_plan)
    committed(demo)


def test_an_invoice_bills_each_adapter_once(demo):
    a, v1 = paid(demo, "A", "adapter-0492", 1000)
    v2 = plan(demo, one(demo, "SELECT activation_id FROM vsz.plan WHERE id = :p", p=v1), 2, "active", 3000)
    inv = _invoice(demo, a, 4000)
    demo.run("INSERT INTO vsz.invoice_line (invoice_id, plan_id, cents) VALUES (:i, :p, 1000)", i=inv, p=v1)
    refused(demo, "INSERT INTO vsz.invoice_line (invoice_id, plan_id, cents) VALUES (:i, :p, 3000)", "23505",
            i=inv, p=v2)


def test_an_invoices_total_is_the_sum_of_its_lines(demo):
    a, a_plan = paid(demo, "A", "adapter-0493", 1000)
    inv = _invoice(demo, a, 1500)
    demo.run("INSERT INTO vsz.invoice_line (invoice_id, plan_id, cents) VALUES (:i, :p, 1000)", i=inv, p=a_plan)
    assert "sum of its lines" in refused(demo, "SET CONSTRAINTS ALL IMMEDIATE", "23514")


def test_an_invoices_total_cant_be_changed_afterwards(demo):
    a, a_plan = paid(demo, "A", "adapter-0494", 1000)
    inv = _invoice(demo, a, 1000)
    demo.run("INSERT INTO vsz.invoice_line (invoice_id, plan_id, cents) VALUES (:i, :p, 1000)", i=inv, p=a_plan)
    committed(demo)
    demo.run("UPDATE vsz.invoice SET cents = 1 WHERE id = :i", i=inv)
    refused(demo, "SET CONSTRAINTS ALL IMMEDIATE", "23514")
