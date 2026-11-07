"""Adapters on the real schema: Cloud IDs, activation, release, revoke, presence (DEALER-PANEL-PLAN.md Phase 6, D15).

Asked as the demo people. What is pinned: a code is stored only as its keyed hash and works once; a
wrong code -- unknown, used, another dealer's -- gets the same answer and is counted, five a person
or twenty an address an hour, then everything is refused for the hour; an activation starts the
adapter's own trial and shows in the holder's next token; a release ends the activation and its
plan, keeps the history and issues a new code; a revoked adapter never comes back; presence keeps
the newest lifecycle event.

A refused activation must leave its attempt behind, which the handler commits (run_user_op,
KeepAndRefuse; tested offline in tests/cloud/test_directory.py). Here the operation is called
directly and its attempt row is read in the same transaction.
"""
import hashlib
import hmac

import pytest

from dbhelpers import SUBS, ask, customer, dealer, dealer_id, demo_spec, member, one, refused_op
from loaders import load_lambda

directory = load_lambda("vsz_directory")
KEY = "a1" * 32
HOME = {"name": "Garage", "timezone": "Europe/Berlin"}


@pytest.fixture(autouse=True)
def cloud_id_key(monkeypatch):
    monkeypatch.setenv("CLOUD_ID_KEY", KEY)


@pytest.fixture
def demo(app):
    directory.operator_seed(app, demo_spec())
    return app


def demo_customer(app):
    return str(one(app, "SELECT id FROM vsz.org WHERE name = 'Demo customer'"))


def generate(app, n=1, who="owner", **args):
    return ask(app, "generate_adapters", who, count=n, **args)


def activate(app, who, code, **args):
    """The activation, or the refusal it raised -- its attempt stays in the transaction."""
    try:
        return ask(app, "activate_adapter", who, cloudId=code, **args)
    except directory.DirectoryError as refusal:
        return refusal


def attempts(app, ok):
    return one(app, "SELECT count(*) FROM vsz.activation_attempt WHERE ok = :ok", ok=ok)


def adapter_row(app, thing):
    return app.run("SELECT status, dealer_id, cloud_id_hmac, certificate_id, connected FROM vsz.adapter "
                   "WHERE thing_name = :t", t=thing)[0]


# --- new adapters and their codes -----------------------------------------------------------------

def test_new_adapters_are_numbered_and_their_codes_kept_only_as_keyed_hashes(demo):
    made = generate(demo, 2, label="Box A")
    assert [m["thingName"] for m in made] == ["adapter-02", "adapter-03"]          # adapter-01 is the seed's
    for m in made:
        shown = m["cloudId"]
        assert len(shown) == 11 and shown[5] == "-"
        assert set(shown.replace("-", "")) <= set(directory.CLOUD_ID_ALPHABET)
        stored = bytes(adapter_row(demo, m["thingName"])[2])
        assert stored == hmac.new(bytes.fromhex(KEY), shown.replace("-", "").encode(), hashlib.sha256).digest()
        assert shown.replace("-", "").encode() not in stored
    listed = {a["thingName"]: a for a in ask(demo, "list_adapters", "admin")}
    assert (listed["adapter-02"]["status"], listed["adapter-02"]["hasCode"], listed["adapter-02"]["identity"],
            listed["adapter-02"]["label"]) == ("provisioned", True, False, "Box A")
    assert generate(demo, 1, who="admin")[0]["thingName"] == "adapter-04"


def test_only_owners_and_administrators_make_adapters(demo):
    refused_op(demo, "generate_adapters", "dealer", 403, count=1)
    refused_op(demo, "generate_adapters", "holder", 403, count=1)
    refused_op(demo, "generate_adapters", "owner", 400, count=21)
    refused_op(demo, "generate_adapters", "owner", 400, count=0)


def test_without_the_key_no_code_is_made(demo, monkeypatch):
    monkeypatch.setenv("CLOUD_ID_KEY", "")
    assert "CLOUD_ID_KEY" in refused_op(demo, "generate_adapters", "owner", 500, count=1)


@pytest.mark.parametrize("typed, read", [
    ("k7qm4-xrpz9", "K7QM4XRPZ9"), (" K7QM4 XRPZ9 ", "K7QM4XRPZ9"), ("O1LI2-ABCDE", "01112ABCDE"),
    ("K7QM4-XRPZU", None), ("K7QM4-XRPZ", None), ("", None), (None, None),
])
def test_a_cloud_id_is_read_as_crockford_reads_it(typed, read):
    assert directory.normalize_cloud_id(typed) == read


# --- activation (D15) ------------------------------------------------------------------------------

def test_the_holder_activates_at_a_new_site_and_the_adapter_starts_its_own_trial(demo):
    code = generate(demo)[0]["cloudId"]
    done = activate(demo, "holder", code.lower().replace("-", " "), newSite=HOME, name="Garage", sourceIp="203.0.113.7")
    assert done["thingName"] == "adapter-02"
    status, dealer_of, stored, _, _ = adapter_row(demo, "adapter-02")
    assert (status, stored) == ("activated", None)                                   # single use
    assert str(dealer_of) == dealer_id(demo)                                         # joined its customer's dealer's stock
    adapters = ask(demo, "customer_adapters", "dealer", id=demo_customer(demo))["adapters"]
    garage = next(a for a in adapters if a["thingName"] == "adapter-02")
    assert (garage["name"], garage["site"]["name"], garage["site"]["timezone"]) == ("Garage", "Garage", "Europe/Berlin")
    assert (garage["plan"]["version"], garage["plan"]["status"], garage["plan"]["retentionDays"]) == (1, "trial", 30)
    assert directory.token_claims(demo, SUBS["holder"], "holder@example.test", True, False)["vsz_adapters"] == \
        "adapter-01,adapter-02"                                                      # in the next token
    assert attempts(demo, True) == 1


def test_the_same_code_twice_is_refused_as_any_wrong_one(demo):
    code = generate(demo)[0]["cloudId"]
    activate(demo, "holder", code, newSite=HOME)
    again = activate(demo, "superadmin", code, newSite=HOME)
    assert isinstance(again, directory.KeepAndRefuse) and (again.status, again.args[0]) == (400, directory.REFUSED_CLOUD_ID)
    unknown = activate(demo, "superadmin", "ZZZZZ-ZZZZZ", newSite=HOME)
    assert unknown.args[0] == directory.REFUSED_CLOUD_ID
    assert attempts(demo, False) == 2


def test_five_wrong_codes_and_the_person_is_refused_for_the_hour(demo):
    code = generate(demo)[0]["cloudId"]
    for _ in range(directory.TRIES_PER_USER):
        assert activate(demo, "holder", "ZZZZZ-ZZZZZ", newSite=HOME).status == 400
    blocked = activate(demo, "holder", code, newSite=HOME)                           # even the right one
    assert (blocked.status, blocked.args[0]) == (429, "too many wrong Cloud IDs: try again in an hour")
    assert attempts(demo, False) == 5                                                # a blocked try isn't counted
    assert activate(demo, "superadmin", code, newSite=HOME)["thingName"] == "adapter-02"   # another person may


def test_an_hour_later_the_tries_are_back(demo):
    code = generate(demo)[0]["cloudId"]
    holder = one(demo, "SELECT id FROM vsz.app_user WHERE email = 'holder@example.test'")
    for _ in range(directory.TRIES_PER_USER):
        demo.run("INSERT INTO vsz.activation_attempt (at, user_id, ok) VALUES (now() - interval '61 minutes', :u, false)",
                 u=holder)
    assert activate(demo, "holder", code, newSite=HOME)["thingName"] == "adapter-02"


def test_twenty_wrong_codes_from_one_address_block_it_for_everyone(demo):
    code = generate(demo)[0]["cloudId"]
    demo.run("INSERT INTO vsz.activation_attempt (source_ip, ok) "
             "SELECT '198.51.100.9', false FROM generate_series(1, :n)", n=directory.TRIES_PER_ADDRESS)
    assert activate(demo, "holder", code, newSite=HOME, sourceIp="198.51.100.9").status == 429
    assert activate(demo, "holder", code, newSite=HOME, sourceIp="198.51.100.10")["thingName"] == "adapter-02"


def test_another_dealers_adapter_is_refused_as_if_it_didnt_exist(demo):
    code = generate(demo, dealerId=dealer_id(demo, "Direct"))[0]["cloudId"]
    refusal = activate(demo, "holder", code, newSite=HOME)
    assert (refusal.status, refusal.args[0]) == (400, directory.REFUSED_CLOUD_ID)
    assert adapter_row(demo, "adapter-02")[0] == "provisioned"


def test_mistakes_beside_the_code_cost_no_try(demo):
    code = generate(demo)[0]["cloudId"]
    assert activate(demo, "holder", code, newSite={"name": "", "timezone": "Europe/Berlin"}).status == 400
    assert activate(demo, "holder", code, siteId="00000000-0000-4000-8000-000000000000").status == 404
    assert activate(demo, "holder", code, newSite=HOME, name="x" * 61).status == 400
    assert attempts(demo, False) == 0


def test_a_dealer_activates_for_its_own_customers_and_a_user_for_nobody(demo):
    code = generate(demo, dealerId=dealer_id(demo))[0]["cloudId"]
    theirs = str(customer(demo, dealer(demo, "Other dealer"), "Not yours"))
    assert activate(demo, "dealer", code, customerId=theirs, newSite=HOME).status == 404     # not a code failure
    home = one(demo, "SELECT id FROM vsz.site WHERE name = 'Home'")
    assert activate(demo, "dealer", code, customerId=demo_customer(demo), siteId=str(home))["thingName"] == "adapter-02"
    assert activate(demo, "user", "ZZZZZ-ZZZZZ", newSite=HOME).status == 403        # shared cameras only
    assert attempts(demo, False) == 0


def test_a_person_in_two_accounts_names_the_one(demo):
    code = generate(demo)[0]["cloudId"]
    second = customer(demo, dealer_id(demo), "Second account")
    holder = one(demo, "SELECT id FROM vsz.app_user WHERE email = 'holder@example.test'")
    member(demo, holder, second, "customer")
    assert activate(demo, "holder", code, newSite=HOME).status == 403
    assert activate(demo, "holder", code, accountId=str(second), newSite=HOME)["customerId"] == str(second)


# --- release, remove, revoke, allocate ------------------------------------------------------------

def test_a_release_ends_the_activation_and_its_plan_and_issues_a_new_code(demo):
    code = generate(demo)[0]["cloudId"]
    a = activate(demo, "holder", code, newSite=HOME)["adapterId"]
    ask(demo, "put_plan", "dealer", adapterId=a, lines=[{"resolution": "4mp", "recording": "motion", "quantity": 2}],
        retentionDays=30, interval="monthly")
    out = ask(demo, "release_adapter", "owner", id=a)
    assert out["thingName"] == "adapter-02" and out["cloudId"] != code
    status, _, stored, _, _ = adapter_row(demo, "adapter-02")
    assert status == "released" and stored is not None
    history = demo.run("SELECT p.version, p.status, p.cents_period, (SELECT sum(quantity) FROM vsz.plan_line l "
                       "WHERE l.plan_id = p.id) FROM vsz.plan p JOIN vsz.adapter_activation act ON act.id = p.activation_id "
                       "JOIN vsz.adapter d ON d.id = act.adapter_id WHERE d.thing_name = 'adapter-02' ORDER BY p.version")
    assert [tuple(h) for h in history] == [(1, "trial", 0, 0), (2, "trial", 1120, 2), (3, "ended", 0, 2)]
    assert directory.token_claims(demo, SUBS["holder"], "holder@example.test", True, False)["vsz_adapters"] == "adapter-01"
    # its next customer starts afresh, with the new code
    nxt = customer(demo, dealer_id(demo), "Next owner")
    again = activate(demo, "dealer", out["cloudId"], customerId=str(nxt), newSite=HOME)
    plans = ask(demo, "customer_adapters", "dealer", id=str(nxt))["adapters"][0]["plan"]
    assert (again["thingName"], plans["version"], plans["status"]) == ("adapter-02", 1, "trial")
    refused_op(demo, "release_adapter", "dealer", 403, id=a)


def test_removing_from_the_account_is_a_release_without_a_code(demo):
    code = generate(demo)[0]["cloudId"]
    a = activate(demo, "holder", code, newSite=HOME)["adapterId"]
    refused_op(demo, "remove_adapter", "dealer", 404, id=a)
    refused_op(demo, "remove_adapter", "user", 404, id=a)
    assert ask(demo, "remove_adapter", "superadmin", id=a)["thingName"] == "adapter-02"
    assert tuple(adapter_row(demo, "adapter-02")[:3:2]) == ("released", None)
    fresh = ask(demo, "new_code", "admin", id=a)["cloudId"]
    assert activate(demo, "holder", fresh, newSite=HOME)["thingName"] == "adapter-02"


def test_a_revoked_adapter_never_comes_back(demo):
    code = generate(demo)[0]["cloudId"]
    a = activate(demo, "holder", code, newSite=HOME)["adapterId"]
    out = ask(demo, "revoke_adapter", "owner", id=a)
    assert (out["wasActivated"], out["identity"]) == (True, False)
    assert tuple(adapter_row(demo, "adapter-02")[:3:2]) == ("revoked", None)
    refused_op(demo, "new_code", "owner", 409, id=a)
    refused_op(demo, "allocate_adapter", "owner", 409, id=a, dealerId=dealer_id(demo))
    refused_op(demo, "revoke_adapter", "owner", 409, id=a)
    assert ask(demo, "adapter_state", "admin", id=a) == {"id": a, "thingName": "adapter-02", "activated": False,
                                                        "status": "revoked"}


def test_allocation_moves_an_adapter_between_stocks_until_it_is_activated(demo):
    made = generate(demo)[0]
    other = str(dealer(demo, "Other dealer"))
    assert ask(demo, "allocate_adapter", "owner", id=made["id"], dealerId=other)["dealerId"] == other
    assert activate(demo, "holder", made["cloudId"], newSite=HOME).status == 400     # now another dealer's
    ask(demo, "allocate_adapter", "owner", id=made["id"], dealerId=None)
    activate(demo, "holder", made["cloudId"], newSite=HOME)
    refused_op(demo, "allocate_adapter", "owner", 409, id=made["id"], dealerId=other)


# --- presence, identity, what customers see ---------------------------------------------------------

def test_presence_keeps_the_newest_lifecycle_event(demo):
    def event(kind, ts, thing="adapter-01"):
        return directory.record_presence(demo, {"clientId": thing, "eventType": kind, "timestamp": ts})
    assert event("connected", 1_700_000_000_000) == {"updated": True}
    assert adapter_row(demo, "adapter-01")[4] is True
    assert event("disconnected", 1_700_000_060_000) == {"updated": True}
    assert event("connected", 1_700_000_030_000) == {"updated": False}               # older: out of order
    assert adapter_row(demo, "adapter-01")[4] is False
    assert one(demo, "SELECT extract(epoch FROM last_seen_at)::bigint FROM vsz.adapter WHERE thing_name = 'adapter-01'") \
        == 1_700_000_060
    assert event("connected", 1_700_000_000_000, thing="adapter-99") == {"updated": False}
    with pytest.raises(directory.DirectoryError):
        directory.record_presence(demo, {"clientId": "adapter-01", "eventType": "subscribed", "timestamp": 1})


def test_the_owners_script_records_each_certificate(demo):
    made = generate(demo)[0]
    cert = "c" * 64
    assert directory.operator_identity_made(demo, {"thingName": "adapter-02", "certificateId": cert}) == \
        {"thingName": "adapter-02"}
    assert adapter_row(demo, "adapter-02")[3] == cert
    states = {a["thingName"]: a for a in directory.operator_adapters(demo)}
    assert (states["adapter-02"]["identity"], states["adapter-01"]["status"]) == (True, "activated")
    with pytest.raises(directory.DirectoryError):
        directory.operator_identity_made(demo, {"thingName": "adapter-02", "certificateId": "not hex"})
    ask(demo, "revoke_adapter", "owner", id=made["id"])
    with pytest.raises(directory.DirectoryError):
        directory.operator_identity_made(demo, {"thingName": "adapter-02", "certificateId": cert})


def test_customers_see_their_own_adapters_and_their_sites(demo):
    generate(demo)
    assert [a["thingName"] for a in ask(demo, "list_adapters", "holder")] == ["adapter-01"]
    assert ask(demo, "list_adapters", "user") == []                                  # shared cameras only
    holder_me = ask(demo, "me", "holder")
    assert [s["name"] for s in holder_me["accounts"][0]["sites"]] == ["Home"]
    dealers = {a["thingName"]: a for a in ask(demo, "list_adapters", "dealer")}
    assert dealers["adapter-01"]["activation"]["siteId"] and "connected" in dealers["adapter-01"]
