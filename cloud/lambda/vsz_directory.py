"""vsz-directory: who may do what, from the directory database (DEALER-PANEL-PLAN.md Phase 2, D3, D4).

It runs inside the account's default VPC (security group vsz-db-client) and logs in to the
directory database as vsz_app with an IAM token -- the only code that reads or writes the
directory's rows. Two kinds of caller, told apart by the event:

  Cognito, on every sign-in and token renewal of the user pool vsz-users (the pre-token-generation
  trigger, event version 1): the person is looked up by their Cognito sub -- once, at the first
  sign-in, by their verified email -- and the ID token gets the claims of D4:
      vsz_role      system_admin | system_owner | dealer | customer (the side a person is on)
      vsz_org       the platform's or dealer's id (staff only)
      vsz_adapters  full access: Thing names of every account the person holds or is Super Admin
                    of; "*" for a system administrator
      vsz_cameras   view only: cameras shared with the person, as cameraId@thing (at most 100)
  A person who isn't in the directory, is disabled, or has no active organisation gets no token:
  the trigger raises, and Cognito refuses the sign-in. Cognito waits 5 seconds at most.

  The operator, through cloud/provision.sh, with {"op": ..., "actor": {"kind": "operator"}}:
      ping           connects and answers who it is
      claims_for     the claims an email would get now, without signing in
      operator_seed  the demo directory of Phase 2.3, from a spec; a second run changes nothing
  Invoking this function at all needs IAM permission (Cognito for vsz-users, and the operator),
  so the actor names who asks rather than proving it. Phase 4's API will pass the signed-in
  caller, checked against their token, and every operation will scope by it.

Nothing it returns or raises carries a credential, and it logs nothing of its own.
"""
import os
import ssl

import boto3

REGION = os.environ["AWS_REGION"]
DB_HOST = os.environ.get("DB_HOST", "")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "videosafezone")
DB_CA_BUNDLE = os.environ.get("DB_CA_BUNDLE", "/opt/rds-ca/bundle.pem")

APP = "vsz_app"
STAFF = ("system_admin", "system_owner", "dealer")
FULL_ACCESS = ("customer", "customer_admin")          # holder, Super Admin (D18)
MAX_SHARES = 100                                      # keeps the ID token small (D18)
FIRST_SIGN_IN = ("TokenGeneration_Authentication", "TokenGeneration_NewPasswordChallenge")
# No full stop at the end: Cognito adds one after a trigger's message (FoundAndFixed.md #90).
REFUSED = "This account can't sign in. If it should, ask whoever manages your account"


class DirectoryError(Exception):
    """A refusal with a message meant for the caller."""


# --- the database: these five are copies of vsz-db-admin's, kept identical (test_lambda_twins) ----

def rds_token(user):
    """An IAM login token: signed locally with this function's role, no network call (D3)."""
    return boto3.client("rds", region_name=REGION).generate_db_auth_token(
        DBHostname=DB_HOST, Port=DB_PORT, DBUsername=user, Region=REGION)


def tls_context():
    ctx = ssl.create_default_context(cafile=DB_CA_BUNDLE)    # verifies the chain and the hostname
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def connect(user, database, password=None):
    import pg8000.native                             # from the vsz-pg layer
    if not DB_HOST:
        raise RuntimeError("DB_HOST is not set")
    con = pg8000.native.Connection(
        user, host=DB_HOST, port=DB_PORT, database=database,
        password=rds_token(user) if password is None else password,
        ssl_context=tls_context(), timeout=15,
        application_name=os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "vsz"))
    con.run("SET statement_timeout = '50s'")
    con.run("SET lock_timeout = '10s'")
    return con


def sql_state(exc):
    """PostgreSQL's SQLSTATE from a pg8000 error, or None."""
    detail = exc.args[0] if exc.args else None
    return detail.get("C") if isinstance(detail, dict) else None


def sql_message(exc):
    detail = exc.args[0] if exc.args else None
    if isinstance(detail, dict):
        return f"{detail.get('M', '')} (SQLSTATE {detail.get('C', '?')})"
    return str(exc)


# --- one connection while the function is warm ---------------------------------------------------

_con = None


def with_db(work):
    """work(connection), on the warm connection; a dropped one is replaced once and work retried."""
    global _con
    for attempt in (1, 2):
        if _con is None:
            _con = connect(APP, DB_NAME)
        try:
            return work(_con)
        except Exception as exc:
            if type(exc).__name__ != "InterfaceError" or attempt == 2:   # an SQL error isn't retried
                raise
            try:
                _con.close()
            except Exception:
                pass
            _con = None


def in_transaction(con, work):
    con.run("BEGIN")
    try:
        result = work(con)
    except Exception:
        con.run("ROLLBACK")
        raise
    con.run("COMMIT")
    return result


# --- the claims (D4) --------------------------------------------------------------------------------

def find_user(con, sub, email, email_verified):
    """(id, status) of the directory's person behind a Cognito sub, or None. The first sign-in links
    the sub through the verified email, in case storing it at the invitation failed."""
    rows = con.run("SELECT id, status FROM vsz.app_user WHERE cognito_sub = :s", s=sub) if sub else []
    if not rows and sub and email and email_verified:
        rows = con.run("UPDATE vsz.app_user SET cognito_sub = :s "
                       "WHERE lower(email) = lower(:e) AND cognito_sub IS NULL RETURNING id, status",
                       s=sub, e=email)
    return tuple(rows[0]) if rows else None


def claims_for_user(con, user_id):
    """The vsz_* claims for a person, or None when they may not sign in."""
    members = con.run(
        "SELECT m.role, m.org_id FROM vsz.membership m JOIN vsz.org o ON o.id = m.org_id "
        "WHERE m.user_id = :u AND o.status = 'active' ORDER BY m.created_at", u=user_id)
    if not members:
        return None                                   # no organisation, or only suspended ones
    staff = [(role, org) for role, org in members if role in STAFF]
    if staff:
        role, org = staff[0]                          # the database allows staff one membership
        claims = {"vsz_role": role, "vsz_org": str(org)}
        if role == "system_admin":
            claims["vsz_adapters"] = "*"
        return claims
    claims = {"vsz_role": "customer"}
    adapters = [r[0] for r in con.run(
        "SELECT DISTINCT a.thing_name FROM vsz.membership m "
        "JOIN vsz.org o ON o.id = m.org_id AND o.status = 'active' "
        "JOIN vsz.adapter_activation act ON act.customer_id = m.org_id AND act.released_at IS NULL "
        "JOIN vsz.adapter a ON a.id = act.adapter_id "
        "WHERE m.user_id = :u AND m.role = ANY(:full) ORDER BY 1", u=user_id, full=list(FULL_ACCESS))]
    shared = [r[0] for r in con.run(
        "SELECT s.camera_id || '@' || a.thing_name FROM vsz.camera_share s "
        "JOIN vsz.org o ON o.id = s.account_id AND o.status = 'active' "
        "JOIN vsz.adapter a ON a.id = s.adapter_id "
        "WHERE s.user_id = :u ORDER BY 1 LIMIT :n", u=user_id, n=MAX_SHARES)]
    if adapters:
        claims["vsz_adapters"] = ",".join(adapters)
    if shared:
        claims["vsz_cameras"] = ",".join(shared)
    return claims


def token_claims(con, sub, email, email_verified, first_sign_in):
    """What the trigger adds to the ID token, or None to refuse the sign-in."""
    found = find_user(con, sub, email, email_verified)
    if not found:
        return None
    user_id, status = found
    if status == "disabled":
        return None
    claims = claims_for_user(con, user_id)
    if claims is None:
        return None
    if email and email_verified:                      # an address changed and confirmed in Cognito (D18)
        con.run("UPDATE vsz.app_user SET email = :e WHERE id = :u AND email <> :e "
                "AND NOT EXISTS (SELECT 1 FROM vsz.app_user o WHERE lower(o.email) = lower(:e) AND o.id <> :u)",
                e=email, u=user_id)                   # someone else's address here: ours is kept
    if first_sign_in:
        con.run("UPDATE vsz.app_user SET last_sign_in_at = now(), "
                "status = CASE WHEN status = 'invited' THEN 'active' ELSE status END WHERE id = :u", u=user_id)
    return claims


def pre_token(event):
    """Cognito's pre-token-generation trigger, event version 1 (ID token claims)."""
    attrs = (event.get("request") or {}).get("userAttributes") or {}
    claims = with_db(lambda con: token_claims(
        con, attrs.get("sub"), attrs.get("email"), attrs.get("email_verified") == "true",
        event.get("triggerSource") in FIRST_SIGN_IN))
    if claims is None:
        raise DirectoryError(REFUSED)                 # Cognito refuses the sign-in with this text
    event.setdefault("response", {})["claimsOverrideDetails"] = {"claimsToAddOrOverride": claims}
    return event


# --- operator operations ------------------------------------------------------------------------

def ping(con):
    who, orgs = con.run("SELECT current_user, (SELECT count(*) FROM vsz.org)")[0]
    return {"user": who, "organisations": int(orgs)}


def claims_for_email(con, email):
    rows = con.run("SELECT id, status FROM vsz.app_user WHERE lower(email) = lower(:e)", e=email)
    if not rows:
        return {"found": False}
    user_id, status = rows[0]
    claims = None if status == "disabled" else claims_for_user(con, user_id)
    return {"found": True, "status": status, "signsIn": claims is not None, "claims": claims}


def _one(con, sql, **params):
    rows = con.run(sql, **params)
    return rows[0][0] if rows else None


def operator_seed(con, spec):
    """The demo directory (DEALER-PANEL-PLAN.md 2.3) from a spec, in the caller's transaction.
    Everything is found by its natural key first, so a second run changes nothing."""
    if not isinstance(spec, dict):
        raise DirectoryError("give spec")
    done = []
    users = {}
    for u in spec.get("users", []):
        email, sub, status = u["email"], u.get("sub"), u.get("status", "active")
        row = con.run("SELECT id, cognito_sub FROM vsz.app_user WHERE lower(email) = lower(:e)", e=email)
        if row:
            users[email.lower()] = row[0][0]
            if sub and row[0][1] is None:
                con.run("UPDATE vsz.app_user SET cognito_sub = :s WHERE id = :u", s=sub, u=row[0][0])
        else:
            users[email.lower()] = _one(con, "INSERT INTO vsz.app_user (email, cognito_sub, status) "
                                             "VALUES (:e, :s, :st) RETURNING id", e=email, s=sub, st=status)
            done.append(f"user {email} ({status})")

    def members(org_id, entries):
        for m in entries:
            added = con.run("INSERT INTO vsz.membership (user_id, org_id, role) VALUES (:u, :o, :r) "
                            "ON CONFLICT (user_id, org_id) DO NOTHING RETURNING role",
                            u=users[m["email"].lower()], o=org_id, r=m["role"])
            if added:
                done.append(f"  {m['email']}: {m['role']}")

    platform = _one(con, "SELECT id FROM vsz.org WHERE kind = 'platform'")
    members(platform, spec.get("platform", {}).get("members", []))
    for d in spec.get("dealers", []):
        dealer = _one(con, "SELECT id FROM vsz.org WHERE kind = 'dealer' AND parent_id = :p AND name = :n",
                      p=platform, n=d["name"])
        if dealer is None:
            dealer = _one(con, "INSERT INTO vsz.org (kind, parent_id, name) VALUES ('dealer', :p, :n) "
                               "RETURNING id", p=platform, n=d["name"])
            done.append(f"dealer {d['name']}")
        members(dealer, d.get("members", []))
        for c in d.get("customers", []):
            customer = _one(con, "SELECT id FROM vsz.org WHERE kind = 'customer' AND parent_id = :p "
                                 "AND name = :n", p=dealer, n=c["name"])
            if customer is None:
                customer = _one(con, "INSERT INTO vsz.org (kind, parent_id, name, reference, status) "
                                     "VALUES ('customer', :p, :n, :r, :s) RETURNING id",
                                p=dealer, n=c["name"], r=c.get("reference"), s=c.get("status", "active"))
                done.append(f"customer {c['name']} ({c.get('status', 'active')})")
            members(customer, c.get("members", []))
            for s in c.get("sites", []):
                site = _one(con, "SELECT id FROM vsz.site WHERE customer_id = :c AND name = :n",
                            c=customer, n=s["name"])
                if site is None:
                    site = _one(con, "INSERT INTO vsz.site (customer_id, name, timezone) VALUES (:c, :n, :t) "
                                     "RETURNING id", c=customer, n=s["name"], t=s["timezone"])
                    done.append(f"  site {s['name']}")
                for a in s.get("adapters", []):
                    adapter = _one(con, "SELECT id FROM vsz.adapter WHERE thing_name = :t", t=a["thing"])
                    if adapter is None:
                        adapter = _one(con, "INSERT INTO vsz.adapter (thing_name, dealer_id, status, label) "
                                            "VALUES (:t, :d, 'activated', :l) RETURNING id",
                                       t=a["thing"], d=dealer, l=a.get("label"))
                        done.append(f"  adapter {a['thing']}")
                    open_at = con.run("SELECT id, customer_id FROM vsz.adapter_activation "
                                      "WHERE adapter_id = :a AND released_at IS NULL", a=adapter)
                    if open_at and open_at[0][1] != customer:
                        raise DirectoryError(f"{a['thing']} is activated for another customer")
                    activation = open_at[0][0] if open_at else _one(
                        con, "INSERT INTO vsz.adapter_activation (adapter_id, customer_id, site_id, name) "
                             "VALUES (:a, :c, :s, :n) RETURNING id",
                        a=adapter, c=customer, s=site, n=a.get("name"))
                    if not open_at:
                        done.append(f"  activated {a['thing']} at {s['name']}")
                    if _one(con, "SELECT count(*) FROM vsz.plan WHERE activation_id = :a", a=activation) == 0:
                        con.run("INSERT INTO vsz.plan (activation_id, version, status, retention_days, "
                                "billing_interval, cents_period, currency, trial_ends_at) "
                                "SELECT :a, 1, 'trial', 30, 'monthly', 0, value #>> '{}', now() + make_interval("
                                "days => (SELECT (value #>> '{}')::int FROM vsz.price_setting WHERE key = 'trial_days')) "
                                "FROM vsz.price_setting WHERE key = 'currency'", a=activation)
                        done.append(f"  trial plan for {a['thing']}")
            for sh in c.get("shares", []):
                added = con.run(
                    "INSERT INTO vsz.camera_share (account_id, user_id, camera_id, adapter_id) "
                    "SELECT :c, :u, :cam, id FROM vsz.adapter WHERE thing_name = :t "
                    "ON CONFLICT (user_id, camera_id) DO NOTHING RETURNING camera_id",
                    c=customer, u=users[sh["email"].lower()], cam=sh["camera"], t=sh["thing"])
                if added:
                    done.append(f"  shared {sh['camera']} with {sh['email']}")
    if done:
        con.run("INSERT INTO vsz.audit_event (action, detail) VALUES ('operator_seed', :d)",
                d=f'{{"changes": {len(done)}}}')
    return done or ["nothing to do: the demo directory is there"]


# --- the handler -------------------------------------------------------------------------------------

def lambda_handler(event, context):
    if isinstance(event, dict) and str(event.get("triggerSource", "")).startswith("TokenGeneration_"):
        return pre_token(event)                       # raising here is how a sign-in is refused
    event = event if isinstance(event, dict) else {}
    try:
        if (event.get("actor") or {}).get("kind") != "operator":
            raise DirectoryError("only the operator's operations exist yet")
        op = event.get("op")
        if op == "ping":
            return {"ok": True, **with_db(ping)}
        if op == "claims_for":
            if not isinstance(event.get("email"), str):
                raise DirectoryError("give email")
            return {"ok": True, **with_db(lambda con: claims_for_email(con, event["email"]))}
        if op == "operator_seed":
            return {"ok": True, "done": with_db(lambda con: in_transaction(
                con, lambda c: operator_seed(c, event.get("spec"))))}
        raise DirectoryError("op must be ping, claims_for or operator_seed")
    except DirectoryError as exc:
        return {"ok": False, "error": exc.args[0]}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {sql_message(exc)}"}
