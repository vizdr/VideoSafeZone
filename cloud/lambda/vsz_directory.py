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

  A signed-in person, through vsz-directory-api (Phase 4), with {"op": ..., "actor": {"kind":
  "user", "sub": <their Cognito sub>}, "args": {...}}. The token's claims are not trusted here:
  the person is looked up by sub on every call, so a disable takes effect at once, and every
  operation checks the role the database gives them (D6).

  The operator, through cloud/provision.sh, with {"op": ..., "actor": {"kind": "operator"}}:
      ping           connects and answers who it is
      claims_for     the claims an email would get now, without signing in
      operator_seed  the demo directory of Phase 2.3, from a spec; a second run changes nothing
  Invoking this function at all needs IAM permission (Cognito for vsz-users, and the operator),
  so the actor names who asks rather than proving it. Phase 4's API will pass the signed-in
  caller, checked against their token, and every operation will scope by it.

Nothing it returns or raises carries a credential, and it logs nothing of its own.
"""
import datetime
import json
import os
import re
import ssl
import uuid

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
    """A refusal with a message meant for the caller, and the HTTP status the API answers with."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


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


# --- signed-in people's operations (Phase 4, D6) ----------------------------------------------------

SYSTEM = ("system_admin", "system_owner")
CUSTOMER_ROLES = ("customer", "customer_admin", "customer_user")
ROLES = SYSTEM + ("dealer",) + CUSTOMER_ROLES
RESOLUTIONS = ("2mp", "4mp", "8mp")
RECORDINGS = ("motion", "continuous")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def jsonable(value):
    """Rows as JSON can carry them: ids and times as text."""
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


def an_id(value, what="id"):
    if not isinstance(value, str) or not UUID.match(value):
        raise DirectoryError(f"give a valid {what}")
    return value


def resolve_actor(con, actor):
    """The signed-in person as the database knows them now: user_id, email, role (a staff role, or
    "customer"), org (the staff organisation) and accounts ({customer org: role})."""
    sub = (actor or {}).get("sub")
    if (actor or {}).get("kind") != "user" or not isinstance(sub, str) or not UUID.match(sub):
        raise DirectoryError("not signed in", 401)
    rows = con.run("SELECT id, email, status FROM vsz.app_user WHERE cognito_sub = CAST(:s AS uuid)", s=sub)
    if not rows or rows[0][2] == "disabled":
        raise DirectoryError("this account can't use the directory", 403)
    user_id, email, _ = rows[0]
    members = con.run("SELECT m.role, m.org_id FROM vsz.membership m JOIN vsz.org o ON o.id = m.org_id "
                      "WHERE m.user_id = :u AND o.status = 'active' ORDER BY m.created_at", u=user_id)
    if not members:
        raise DirectoryError("this account can't use the directory", 403)
    staff = [(role, org) for role, org in members if role in STAFF]
    role, org = staff[0] if staff else ("customer", None)
    return {"user_id": user_id, "email": email, "role": role, "org": org,
            "accounts": {org_id: r for r, org_id in members if r in CUSTOMER_ROLES}}


def require(actor, *roles):
    if actor["role"] not in roles:
        raise DirectoryError("not allowed for your role", 403)


def _dealer_of(con, org_id):
    """The dealer a customer organisation sits under; None for any other organisation."""
    rows = con.run("SELECT parent_id FROM vsz.org WHERE id = CAST(:o AS uuid) AND kind = 'customer'", o=str(org_id))
    return rows[0][0] if rows else None


def may_give(con, actor, org_id, role):
    """Whether the actor may invite someone as `role` in organisation `org_id` (D6): an
    administrator any role; an owner a dealer's staff or a customer's holder; a dealer only the
    holder of one of its own customers. A holder's Super Admins and users are the account's
    Additional users, added by the holder (D18, Phase 9) or an administrator."""
    if actor["role"] == "system_admin":
        return True
    if actor["role"] == "system_owner":
        return role in ("dealer", "customer")
    if actor["role"] == "dealer":
        return role == "customer" and _dealer_of(con, org_id) == actor["org"]
    return False


def may_manage(con, actor, org_id, role):
    """Whether the actor may disable, enable, reset or re-invite someone who is `role` in
    `org_id` (D6): an administrator anyone; an owner everyone but the system's staff; a dealer
    the people of its own customers, Additional users included."""
    if actor["role"] == "system_admin":
        return True
    if actor["role"] == "system_owner":
        return role not in SYSTEM
    if actor["role"] == "dealer":
        return role in CUSTOMER_ROLES and _dealer_of(con, org_id) == actor["org"]
    return False


def audit(con, actor, action, target_kind=None, target_id=None, detail=None):
    con.run("INSERT INTO vsz.audit_event (actor_user_id, action, target_kind, target_id, detail) "
            "VALUES (:a, :x, :k, CAST(:t AS uuid), CAST(:d AS jsonb))",
            a=actor["user_id"], x=action, k=target_kind, t=None if target_id is None else str(target_id),
            d=json.dumps(detail or {}))


def op_me(con, actor, args):
    out = {"email": actor["email"], "role": actor["role"], "org": None, "accounts": []}
    if actor["org"]:
        kind, name = con.run("SELECT kind, name FROM vsz.org WHERE id = :o", o=actor["org"])[0]
        out["org"] = {"id": actor["org"], "kind": kind, "name": name}
    for org_id, role in actor["accounts"].items():
        name, dealer = con.run("SELECT c.name, d.name FROM vsz.org c JOIN vsz.org d ON d.id = c.parent_id "
                               "WHERE c.id = :o", o=org_id)[0]
        out["accounts"].append({"id": org_id, "name": name, "role": role, "dealer": dealer})
    out["accounts"].sort(key=lambda a: a["name"].lower())
    return out


def op_overview(con, actor, args):
    require(actor, *SYSTEM)

    def counts(sql):
        return {str(k): int(n) for k, n in con.run(sql)}
    plans = con.run(
        "SELECT DISTINCT ON (p.activation_id) p.status, p.cents_period, p.billing_interval, p.currency "
        "FROM vsz.plan p JOIN vsz.adapter_activation a ON a.id = p.activation_id AND a.released_at IS NULL "
        "ORDER BY p.activation_id, p.version DESC")
    monthly = sum(c if i == "monthly" else c / 12 for st, c, i, _ in plans if st == "active")
    return {
        "dealers": counts("SELECT status, count(*) FROM vsz.org WHERE kind = 'dealer' GROUP BY status"),
        "customers": counts("SELECT status, count(*) FROM vsz.org WHERE kind = 'customer' GROUP BY status"),
        "users": counts("SELECT role, count(*) FROM vsz.membership GROUP BY role"),
        "invited": int(con.run("SELECT count(*) FROM vsz.app_user WHERE status = 'invited'")[0][0]),
        "adapters": counts("SELECT status, count(*) FROM vsz.adapter GROUP BY status"),
        "plans": {**{s: sum(1 for p in plans if p[0] == s) for s in ("trial", "active", "cancelled")},
                  "monthlyCents": round(monthly), "currency": plans[0][3] if plans else None},
    }


def op_list_dealers(con, actor, args):
    require(actor, *SYSTEM)
    rows = con.run(
        "SELECT d.id, d.name, d.email, d.phone, d.status, d.created_at, "
        "(SELECT count(*) FROM vsz.org c WHERE c.parent_id = d.id), "
        "(SELECT count(*) FROM vsz.membership m WHERE m.org_id = d.id), "
        "(SELECT count(*) FROM vsz.adapter a WHERE a.dealer_id = d.id) "
        "FROM vsz.org d WHERE d.kind = 'dealer' ORDER BY lower(d.name)")
    return [{"id": r[0], "name": r[1], "email": r[2], "phone": r[3], "status": r[4], "createdAt": r[5],
             "customers": int(r[6]), "users": int(r[7]), "adapters": int(r[8])} for r in rows]


def _org_fields(args, partial, what="dealer", keys=(("name", 200), ("email", 254), ("phone", 40))):
    """The organisation fields given in args, trimmed and checked; blank text clears a field.
    `partial`: an update, where only the fields given change."""
    out = {}
    for key, limit in keys:
        if key in args:
            value = args[key]
            if value is not None and not isinstance(value, str):
                raise DirectoryError(f"{key} must be text")
            value = (value or "").strip() or None
            if value and len(value) > limit:
                raise DirectoryError(f"{key} is too long")
            out[key] = value
    if "status" in args:
        if args["status"] not in ("active", "suspended"):
            raise DirectoryError("status must be active or suspended")
        out["status"] = args["status"]
    if out.get("email") and not EMAIL.match(out["email"]):
        raise DirectoryError("give a valid email address")
    if ("name" in out or not partial) and not out.get("name"):
        raise DirectoryError(f"give the {what}'s name")
    return out


def _name_taken(con, name, but=None):
    return bool(con.run("SELECT 1 FROM vsz.org WHERE kind = 'dealer' AND lower(name) = lower(:n) "
                        "AND id IS DISTINCT FROM CAST(:b AS uuid)", n=name, b=but))


def op_create_dealer(con, actor, args):
    require(actor, *SYSTEM)
    f = _org_fields(args, partial=False)
    if _name_taken(con, f["name"]):
        raise DirectoryError(f"there is a dealer called {f['name']} already", 409)
    dealer = con.run("INSERT INTO vsz.org (kind, parent_id, name, email, phone) "
                     "SELECT 'dealer', id, :n, :e, :p FROM vsz.org WHERE kind = 'platform' RETURNING id",
                     n=f["name"], e=f.get("email"), p=f.get("phone"))[0][0]
    audit(con, actor, "create_dealer", "org", dealer, {"name": f["name"]})
    return {"id": dealer}


def op_update_dealer(con, actor, args):
    require(actor, *SYSTEM)
    dealer = an_id(args.get("id"), "dealer id")
    if not con.run("SELECT 1 FROM vsz.org WHERE id = CAST(:d AS uuid) AND kind = 'dealer'", d=dealer):
        raise DirectoryError("no such dealer", 404)
    f = _org_fields(args, partial=True)
    if not f:
        raise DirectoryError("nothing to change")
    if f.get("name") and _name_taken(con, f["name"], but=dealer):
        raise DirectoryError(f"there is a dealer called {f['name']} already", 409)
    sets = ", ".join(f"{k} = :{k}" for k in f)
    con.run(f"UPDATE vsz.org SET {sets}, updated_at = now() WHERE id = CAST(:d AS uuid)", d=dealer, **f)
    audit(con, actor, "update_dealer", "org", dealer, f)
    return {"id": dealer, "changed": sorted(f)}


def op_list_customers(con, actor, args):
    """Customers in the actor's scope, each with its holder, its adapters' Thing names (the API
    counts their cameras in the registry) and its plans added up (D19)."""
    require(actor, "dealer", *SYSTEM)
    dealer = actor["org"] if actor["role"] == "dealer" else args.get("dealerId")
    if dealer is not None and actor["role"] != "dealer":
        an_id(dealer, "dealer id")
    rows = con.run(
        "SELECT c.id, c.name, c.reference, c.status, c.created_at, d.id, d.name, "
        "(SELECT count(*) FROM vsz.membership m WHERE m.org_id = c.id), "
        "(SELECT json_build_object('email', u.email, 'status', u.status) FROM vsz.membership m "
        " JOIN vsz.app_user u ON u.id = m.user_id WHERE m.org_id = c.id AND m.role = 'customer') "
        "FROM vsz.org c JOIN vsz.org d ON d.id = c.parent_id WHERE c.kind = 'customer' "
        "AND (CAST(:d AS uuid) IS NULL OR d.id = CAST(:d AS uuid)) ORDER BY lower(c.name)",
        d=None if dealer is None else str(dealer))
    activations = [] if not rows else con.run(
        "SELECT act.customer_id, act.id, a.thing_name FROM vsz.adapter_activation act "
        "JOIN vsz.adapter a ON a.id = act.adapter_id WHERE act.released_at IS NULL "
        "AND act.customer_id = ANY(CAST(:c AS uuid[])) ORDER BY a.thing_name",
        c=[str(r[0]) for r in rows])
    plans = _current_plans(con, [a[1] for a in activations])
    currency = _setting(con, "currency")
    out = []
    for r in rows:
        mine = [a for a in activations if a[0] == r[0]]
        counted = [plans[a[1]] for a in mine if a[1] in plans and plans[a[1]]["effectiveStatus"] in BILLED]
        holder = r[8] if isinstance(r[8], dict) or r[8] is None else json.loads(r[8])
        out.append({"id": r[0], "name": r[1], "reference": r[2], "status": r[3], "createdAt": r[4],
                    "dealer": {"id": r[5], "name": r[6]}, "users": int(r[7]), "holder": holder,
                    "adapters": len(mine), "things": [a[2] for a in mine],
                    "inPlans": sum(p["cameras"] for p in counted),
                    "centsMonth": sum(p["centsMonth"] for p in counted), "currency": currency})
    return out


def op_list_adapters(con, actor, args):
    require(actor, "dealer", *SYSTEM)
    dealer = actor["org"] if actor["role"] == "dealer" else None
    rows = con.run(
        "SELECT a.id, a.thing_name, a.status, a.label, a.last_seen_at, a.created_at, d.id, d.name, "
        "act.name, act.activated_at, c.id, c.name, s.name "
        "FROM vsz.adapter a LEFT JOIN vsz.org d ON d.id = a.dealer_id "
        "LEFT JOIN vsz.adapter_activation act ON act.adapter_id = a.id AND act.released_at IS NULL "
        "LEFT JOIN vsz.org c ON c.id = act.customer_id LEFT JOIN vsz.site s ON s.id = act.site_id "
        "WHERE CAST(:d AS uuid) IS NULL OR a.dealer_id = CAST(:d AS uuid) OR c.parent_id = CAST(:d AS uuid) "
        "ORDER BY a.thing_name",
        d=None if dealer is None else str(dealer))
    return [{"id": r[0], "thingName": r[1], "status": r[2], "label": r[3], "lastSeenAt": r[4], "createdAt": r[5],
             "dealer": {"id": r[6], "name": r[7]} if r[6] else None,
             "activation": {"name": r[8], "activatedAt": r[9], "customer": {"id": r[10], "name": r[11]},
                            "site": r[12]} if r[10] else None} for r in rows]


def op_list_users(con, actor, args):
    require(actor, *SYSTEM)
    rows = con.run(
        "SELECT u.id, u.email, u.name, u.status, u.last_sign_in_at, u.created_at, "
        "coalesce(json_agg(json_build_object('orgId', o.id, 'org', o.name, 'kind', o.kind, 'role', m.role) "
        "ORDER BY o.name) FILTER (WHERE m.user_id IS NOT NULL), '[]') "
        "FROM vsz.app_user u LEFT JOIN vsz.membership m ON m.user_id = u.id "
        "LEFT JOIN vsz.org o ON o.id = m.org_id GROUP BY u.id ORDER BY lower(u.email)")
    return [{"id": r[0], "email": r[1], "name": r[2], "status": r[3], "lastSignInAt": r[4], "createdAt": r[5],
             "memberships": r[6] if isinstance(r[6], list) else json.loads(r[6])} for r in rows]


def op_invite_user(con, actor, args):
    """The directory's half of an invitation: the person, invited, with their membership. The API
    then creates the Cognito user (which emails a temporary password) and calls link_sub -- or
    cancel_invite when Cognito refuses."""
    email = str(args.get("email") or "").strip()
    name = str(args.get("name") or "").strip() or None
    role, org = args.get("role"), an_id(args.get("orgId"), "organisation")
    if not EMAIL.match(email) or len(email) > 254:
        raise DirectoryError("give a valid email address")
    if name and len(name) > 200:
        raise DirectoryError("the name is too long")
    if role not in ROLES:
        raise DirectoryError("role must be one of " + ", ".join(ROLES))
    if not con.run("SELECT 1 FROM vsz.org WHERE id = CAST(:o AS uuid)", o=org):
        raise DirectoryError("no such organisation", 404)
    if not may_give(con, actor, org, role):
        raise DirectoryError("you can't give that role there", 403)
    if con.run("SELECT 1 FROM vsz.app_user WHERE lower(email) = lower(:e)", e=email):
        raise DirectoryError("can't add this address", 409)      # never says whose it is
    user = con.run("INSERT INTO vsz.app_user (email, name) VALUES (:e, :n) RETURNING id", e=email, n=name)[0][0]
    con.run("INSERT INTO vsz.membership (user_id, org_id, role) VALUES (:u, CAST(:o AS uuid), :r)", u=user, o=org, r=role)
    audit(con, actor, "invite_user", "app_user", user, {"email": email, "role": role, "orgId": org})
    return {"userId": user}


def _manageable_user(con, actor, user_id):
    """(id, email, status, cognito_sub, last_sign_in_at) of a person the actor may manage: the
    actor may manage every one of their memberships (a person in two dealers' customers is neither
    dealer's alone), and it isn't the actor."""
    rows = con.run("SELECT id, email, status, cognito_sub, last_sign_in_at FROM vsz.app_user "
                   "WHERE id = CAST(:u AS uuid)", u=an_id(user_id, "user id"))
    if not rows:
        raise DirectoryError("no such user", 404)
    if rows[0][0] == actor["user_id"]:
        raise DirectoryError("not on your own account: ask another administrator")
    members = con.run("SELECT org_id, role FROM vsz.membership WHERE user_id = :u", u=rows[0][0])
    if not members and actor["role"] != "system_admin":
        raise DirectoryError("you can't manage this user", 403)
    if not all(may_manage(con, actor, org, role) for org, role in members):
        raise DirectoryError("you can't manage this user", 403)
    return rows[0]


def op_link_sub(con, actor, args):
    user = _manageable_user(con, actor, args.get("userId"))
    con.run("UPDATE vsz.app_user SET cognito_sub = CAST(:s AS uuid) WHERE id = :u AND cognito_sub IS NULL",
            s=an_id(args.get("sub"), "Cognito sub"), u=user[0])
    return {"userId": user[0]}


def op_cancel_invite(con, actor, args):
    user = _manageable_user(con, actor, args.get("userId"))
    gone = con.run("DELETE FROM vsz.app_user WHERE id = :u AND status = 'invited' AND cognito_sub IS NULL "
                   "RETURNING id", u=user[0])
    return {"cancelled": bool(gone)}


def op_user_action(con, actor, args):
    """disable, enable, reset (a password, for someone who has signed in) or resend (the invitation,
    for someone who hasn't). Returns what the API needs for Cognito: the sub and the email."""
    action = args.get("action")
    if action not in ("disable", "enable", "reset", "resend"):
        raise DirectoryError("action must be disable, enable, reset or resend")
    user_id, email, status, sub, signed_in = _manageable_user(con, actor, args.get("userId"))
    if action == "disable":
        status = "disabled"
    elif action == "enable":
        status = "active" if signed_in else "invited"
    elif action == "reset" and status != "active":
        raise DirectoryError("they haven't signed in yet: resend the invitation instead", 409)
    elif action == "resend" and status != "invited":
        raise DirectoryError("they have signed in already: reset their password instead", 409)
    if action in ("disable", "enable"):
        con.run("UPDATE vsz.app_user SET status = :s WHERE id = :u", s=status, u=user_id)
    audit(con, actor, f"user_{action}", "app_user", user_id)
    return {"userId": user_id, "email": email, "sub": sub, "status": status}


def _pricing(con):
    prices = [{"resolution": r, "recording": c, "retentionDays": d, "centsMonth": cents, "currency": cur}
              for r, c, d, cents, cur in con.run(
                  "SELECT resolution, recording, retention_days, cents_month, currency FROM vsz.price_camera "
                  "ORDER BY array_position(ARRAY['2mp','4mp','8mp'], resolution), recording DESC, retention_days")]
    settings = {k: v for k, v in con.run("SELECT key, value FROM vsz.price_setting ORDER BY key")}
    return {"prices": prices, "settings": settings}


def _setting(con, key):
    rows = con.run("SELECT value FROM vsz.price_setting WHERE key = :k", k=key)
    return rows[0][0] if rows else None


def op_get_pricing(con, actor, args):
    require(actor, "dealer", *SYSTEM)
    return _pricing(con)


def op_put_pricing(con, actor, args):
    """The whole price list at once: every resolution x recording x storage duration, one price each."""
    require(actor, *SYSTEM)
    settings, prices = args.get("settings") or {}, args.get("prices")
    days = settings.get("retention_days")
    if (not isinstance(days, list) or not 1 <= len(days) <= 12 or days != sorted(set(days))
            or not all(isinstance(d, int) and not isinstance(d, bool) and 1 <= d <= 3650 for d in days)):
        raise DirectoryError("retention_days: 1 to 12 different whole numbers of days, 1 to 3650, in order")
    for key, top in (("annual_discount_percent", 50), ("trial_days", 90)):
        v = settings.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= top:
            raise DirectoryError(f"{key}: a whole number from 0 to {top}")
    want = {(r, c, d) for r in RESOLUTIONS for c in RECORDINGS for d in days}
    got = {}
    for p in prices if isinstance(prices, list) else []:
        key = (p.get("resolution"), p.get("recording"), p.get("retentionDays"))
        cents = p.get("centsMonth")
        if key not in want or key in got:
            raise DirectoryError(f"unexpected or repeated price: {key}")
        if not isinstance(cents, int) or isinstance(cents, bool) or not 0 <= cents <= 1_000_000:
            raise DirectoryError(f"price for {key}: whole cents from 0 to 1000000")
        got[key] = cents
    if set(got) != want:
        raise DirectoryError(f"every combination needs a price: {len(want) - len(got)} missing")
    currency = con.run("SELECT value #>> '{}' FROM vsz.price_setting WHERE key = 'currency'")[0][0]
    con.run("DELETE FROM vsz.price_camera")
    for (r, c, d), cents in sorted(got.items()):
        con.run("INSERT INTO vsz.price_camera (resolution, recording, retention_days, cents_month, currency) "
                "VALUES (:r, :c, :d, :x, :cur)", r=r, c=c, d=d, x=cents, cur=currency)
    for key, value in (("retention_days", days), ("annual_discount_percent", settings["annual_discount_percent"]),
                       ("trial_days", settings["trial_days"]), ("placeholder_prices", False)):
        con.run("INSERT INTO vsz.price_setting (key, value) VALUES (:k, CAST(:v AS jsonb)) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                k=key, v=json.dumps(value))
    audit(con, actor, "put_pricing", detail={"prices": len(got), "retentionDays": days})
    return {"prices": len(got)}


# --- the dealer portal (Phase 5, §7.1, D19): customers, a plan per adapter, its history -------------

CLASSES = [(r, c) for r in RESOLUTIONS for c in RECORDINGS]
INTERVALS = ("monthly", "annual")
BILLED = ("trial", "active")                          # what a customer's total adds up
TIMEZONE = re.compile(r"^[A-Za-z_]+(/[A-Za-z0-9_+-]+)*$")
ADDRESS = ("line1", "line2", "city", "postcode", "country")


def price_plan(prices, discount_percent, lines, retention_days, interval):
    """A plan's price for its period, in cents (D9): each camera at the price list's monthly price
    for its class at the plan's storage duration; a year is twelve months less the annual
    discount, rounded to the cent. The page computes the same for its preview
    (client-app/src/pricing.ts); tests/fixtures/pricing-cases.json keeps the two the same."""
    table = {(p["resolution"], p["recording"], p["retentionDays"]): p["centsMonth"] for p in prices}
    monthly = 0
    for line in lines:
        if line["quantity"]:
            key = (line["resolution"], line["recording"], retention_days)
            if key not in table:
                raise DirectoryError(f"the price list has no price for {key[0]} {key[1]} at {retention_days} days")
            monthly += line["quantity"] * table[key]
    if interval == "monthly":
        return monthly
    return (monthly * 12 * (100 - discount_percent) + 50) // 100


def _effective_status(status, trial_ends_at):
    """A trial whose end has passed is an active plan: the stored version doesn't change with time."""
    if status == "trial" and trial_ends_at is not None and trial_ends_at <= datetime.datetime.now(datetime.timezone.utc):
        return "active"
    return status


def _plan_rows(con, where, **params):
    """Plan versions with their lines and who saved them, newest first."""
    rows = con.run(
        "SELECT p.id, p.activation_id, p.version, p.status, p.retention_days, p.billing_interval, "
        "p.cents_period, p.currency, p.trial_ends_at, p.created_at, u.email "
        f"FROM vsz.plan p LEFT JOIN vsz.app_user u ON u.id = p.created_by WHERE {where} "
        "ORDER BY p.activation_id, p.version DESC", **params)
    lines = {}
    for plan_id, r, c, q in [] if not rows else con.run("SELECT plan_id, resolution, recording, quantity FROM vsz.plan_line "
                                    "WHERE plan_id = ANY(CAST(:ids AS uuid[]))", ids=[str(x[0]) for x in rows]):
        lines.setdefault(plan_id, {})[(r, c)] = q
    out = []
    for pid, act, version, status, days, interval, cents, cur, trial_ends, created, by in rows:
        mine = lines.get(pid, {})
        effective = _effective_status(status, trial_ends)
        out.append({
            "id": pid, "activationId": act, "version": version, "status": status, "effectiveStatus": effective,
            "retentionDays": days, "interval": interval, "centsPeriod": cents, "currency": cur,
            "centsMonth": 0 if effective not in BILLED else cents if interval == "monthly" else (cents + 6) // 12,
            "trialEndsAt": trial_ends, "createdAt": created, "createdBy": by,
            "lines": [{"resolution": r, "recording": c, "quantity": mine.get((r, c), 0)} for r, c in CLASSES],
            "cameras": sum(mine.values())})
    return out


def _current_plans(con, activation_ids):
    """{activation id: its newest plan version}."""
    if not activation_ids:
        return {}
    current = {}
    for plan in _plan_rows(con, "p.activation_id = ANY(CAST(:a AS uuid[]))", a=[str(a) for a in activation_ids]):
        current.setdefault(plan["activationId"], plan)
    return current


def _customer(con, actor, customer_id):
    """(id, name, dealer id) of a customer the actor may manage (D6): a dealer its own, owners and
    administrators any. Another dealer's customer is "no such customer", as one that doesn't exist."""
    require(actor, "dealer", *SYSTEM)
    rows = con.run("SELECT id, name, parent_id FROM vsz.org WHERE id = CAST(:c AS uuid) AND kind = 'customer'",
                   c=an_id(customer_id, "customer id"))
    if not rows or actor["role"] == "dealer" and rows[0][2] != actor["org"]:
        raise DirectoryError("no such customer", 404)
    return rows[0]


def _activation(con, actor, adapter_id, lock=False):
    """(activation id, customer id) of the adapter's open activation, at a customer the actor may
    manage. `lock` holds the activation until the transaction ends, so two saves of a plan can't
    both take the same version number."""
    require(actor, "dealer", *SYSTEM)
    rows = con.run("SELECT act.id, act.customer_id, c.parent_id FROM vsz.adapter_activation act "
                   "JOIN vsz.org c ON c.id = act.customer_id "
                   "WHERE act.adapter_id = CAST(:a AS uuid) AND act.released_at IS NULL"
                   + (" FOR UPDATE OF act" if lock else ""), a=an_id(adapter_id, "adapter id"))
    if not rows or actor["role"] == "dealer" and rows[0][2] != actor["org"]:
        raise DirectoryError("no activated adapter with that id", 404)
    return rows[0][0], rows[0][1]


def _site_fields(site):
    if not isinstance(site, dict):
        raise DirectoryError("give the customer's first site: its name and time zone")
    name, tz = site.get("name"), site.get("timezone")
    name = name.strip() if isinstance(name, str) else ""
    if not 1 <= len(name) <= 100:
        raise DirectoryError("give the site's name (up to 100 characters)")
    if not isinstance(tz, str) or len(tz) > 64 or not TIMEZONE.match(tz):
        raise DirectoryError("give the site's time zone, such as Europe/Berlin")
    return name, tz


CUSTOMER_KEYS = (("name", 200), ("reference", 100), ("email", 254), ("phone", 40))


def op_create_customer(con, actor, args):
    """A customer with its first site (§7.1). A dealer's own; owners and administrators name the
    dealer. Inviting its holder is the API's next step (an invitation, as any other)."""
    require(actor, "dealer", *SYSTEM)
    if actor["role"] == "dealer":
        dealer = actor["org"]
    else:
        dealer = an_id(args.get("dealerId"), "dealer id")
        if not con.run("SELECT 1 FROM vsz.org WHERE id = CAST(:d AS uuid) AND kind = 'dealer'", d=dealer):
            raise DirectoryError("no such dealer", 404)
    f = _org_fields({k: v for k, v in args.items() if k != "status"}, partial=False, what="customer",
                    keys=CUSTOMER_KEYS)
    site_name, tz = _site_fields(args.get("site"))
    customer = con.run("INSERT INTO vsz.org (kind, parent_id, name, reference, email, phone) "
                       "VALUES ('customer', CAST(:d AS uuid), :n, :r, :e, :p) RETURNING id",
                       d=str(dealer), n=f["name"], r=f.get("reference"), e=f.get("email"), p=f.get("phone"))[0][0]
    site = con.run("INSERT INTO vsz.site (customer_id, name, timezone) VALUES (:c, :n, :t) RETURNING id",
                   c=customer, n=site_name, t=tz)[0][0]
    audit(con, actor, "create_customer", "org", customer, {"name": f["name"], "dealerId": str(dealer)})
    return {"id": customer, "siteId": site}


def op_get_customer(con, actor, args):
    cid = _customer(con, actor, args.get("id"))[0]
    name, ref, email, phone, status, created, dealer_id, dealer = con.run(
        "SELECT c.name, c.reference, c.email, c.phone, c.status, c.created_at, d.id, d.name "
        "FROM vsz.org c JOIN vsz.org d ON d.id = c.parent_id WHERE c.id = :c", c=cid)[0]
    holder = con.run("SELECT u.id, u.email, u.name, u.status FROM vsz.membership m JOIN vsz.app_user u "
                     "ON u.id = m.user_id WHERE m.org_id = :c AND m.role = 'customer'", c=cid)
    return {
        "id": cid, "name": name, "reference": ref, "email": email, "phone": phone, "status": status,
        "createdAt": created, "dealer": {"id": dealer_id, "name": dealer},
        "sites": [{"id": i, "name": n, "timezone": t} for i, n, t in con.run(
            "SELECT id, name, timezone FROM vsz.site WHERE customer_id = :c ORDER BY lower(name)", c=cid)],
        "holder": {"id": holder[0][0], "email": holder[0][1], "name": holder[0][2], "status": holder[0][3]}
        if holder else None,
        "users": int(con.run("SELECT count(*) FROM vsz.membership WHERE org_id = :c", c=cid)[0][0]),
        "adapters": int(con.run("SELECT count(*) FROM vsz.adapter_activation WHERE customer_id = :c "
                                "AND released_at IS NULL", c=cid)[0][0]),
    }


def op_update_customer(con, actor, args):
    """Name, reference, contact; suspend (its people can't sign in) or reactivate."""
    cid = _customer(con, actor, args.get("id"))[0]
    f = _org_fields({k: v for k, v in args.items() if k != "id"}, partial=True, what="customer", keys=CUSTOMER_KEYS)
    if not f:
        raise DirectoryError("nothing to change")
    sets = ", ".join(f"{k} = :{k}" for k in f)
    con.run(f"UPDATE vsz.org SET {sets}, updated_at = now() WHERE id = :c", c=cid, **f)
    audit(con, actor, "update_customer", "org", cid, f)
    return {"id": cid, "changed": sorted(f)}


def op_customer_adapters(con, actor, args):
    """The customer's adapters, each with its current plan, and the plans added up (D19). The API
    adds each adapter's cameras from the registry, which this function can't reach."""
    cid, name, _ = _customer(con, actor, args.get("id"))
    rows = con.run(
        "SELECT act.id, a.id, a.thing_name, a.label, a.last_seen_at, act.name, act.activated_at, s.id, s.name, "
        "s.timezone FROM vsz.adapter_activation act JOIN vsz.adapter a ON a.id = act.adapter_id "
        "JOIN vsz.site s ON s.id = act.site_id WHERE act.customer_id = :c AND act.released_at IS NULL "
        "ORDER BY lower(s.name), lower(coalesce(act.name, '')), a.thing_name", c=cid)
    plans = _current_plans(con, [r[0] for r in rows])
    adapters = [{"id": r[1], "activationId": r[0], "thingName": r[2], "label": r[3], "lastSeenAt": r[4],
                 "name": r[5], "activatedAt": r[6], "site": {"id": r[7], "name": r[8], "timezone": r[9]},
                 "plan": plans.get(r[0])} for r in rows]
    billed = [a["plan"] for a in adapters if a["plan"] and a["plan"]["effectiveStatus"] in BILLED]
    return {"customer": {"id": cid, "name": name}, "adapters": adapters,
            "total": {"adapters": len(adapters), "centsMonth": sum(p["centsMonth"] for p in billed),
                      "cameras": sum(p["cameras"] for p in billed), "currency": _setting(con, "currency")}}


def _plan_lines(given):
    """The six lines of a plan (class x recording), from what the page sent; a class not given is 0."""
    if not isinstance(given, list):
        raise DirectoryError("give lines: a quantity per camera class")
    got = {}
    for line in given:
        if not isinstance(line, dict):
            raise DirectoryError("each line is {resolution, recording, quantity}")
        key, q = (line.get("resolution"), line.get("recording")), line.get("quantity")
        if key not in CLASSES or key in got:
            raise DirectoryError(f"unexpected or repeated camera class: {key}")
        if not isinstance(q, int) or isinstance(q, bool) or not 0 <= q <= 100:
            raise DirectoryError(f"{key[0]} {key[1]}: a whole number of cameras from 0 to 100")
        got[key] = q
    return [{"resolution": r, "recording": c, "quantity": got.get((r, c), 0)} for r, c in CLASSES]


def _new_version(con, actor, act, current, status, lines, days, interval, cents, currency):
    version = current["version"] + 1 if current else 1
    trial_ends = current["trialEndsAt"] if current else None
    plan = con.run("INSERT INTO vsz.plan (activation_id, version, status, retention_days, billing_interval, "
                   "cents_period, currency, trial_ends_at, created_by) "
                   "VALUES (:a, :v, :s, :d, :i, :c, :cur, :t, :u) RETURNING id",
                   a=act, v=version, s=status, d=days, i=interval, c=cents, cur=currency, t=trial_ends,
                   u=actor["user_id"])[0][0]
    for line in lines:
        con.run("INSERT INTO vsz.plan_line (plan_id, resolution, recording, quantity) VALUES (:p, :r, :c, :q)",
                p=plan, r=line["resolution"], c=line["recording"], q=line["quantity"])
    return version


def _check_based_on(args, current):
    """The version the page edited must still be the newest: two people editing one plan don't
    overwrite each other unseen."""
    based = args.get("basedOn")
    if based is None:
        return
    if not isinstance(based, int) or isinstance(based, bool) or based != (current["version"] if current else 0):
        raise DirectoryError(f"the plan was changed meanwhile (now version {current['version'] if current else 0}): "
                             "reload it", 409)


def op_put_plan(con, actor, args):
    """A new version of the adapter's plan, priced by the server from the price list (D9, D19). A
    price the page sends is ignored. During the trial the plan stays a trial."""
    act, _ = _activation(con, actor, args.get("adapterId"), lock=True)
    current = _current_plans(con, [act]).get(act)
    _check_based_on(args, current)
    lines = _plan_lines(args.get("lines"))
    pricing = _pricing(con)
    days, interval = args.get("retentionDays"), args.get("interval")
    if isinstance(days, bool) or days not in pricing["settings"].get("retention_days", []):
        raise DirectoryError("retentionDays: one of the price list's storage durations, "
                             + ", ".join(map(str, pricing["settings"].get("retention_days", []))))
    if interval not in INTERVALS:
        raise DirectoryError("interval must be monthly or annual")
    cents = price_plan(pricing["prices"], int(pricing["settings"].get("annual_discount_percent", 0)),
                       lines, days, interval)
    trial = current and current["trialEndsAt"] and _effective_status("trial", current["trialEndsAt"]) == "trial"
    status = "trial" if trial else "active"
    if (current and current["effectiveStatus"] == status and current["lines"] == lines
            and current["retentionDays"] == days and current["interval"] == interval):
        return {"changed": False, "plan": current}
    version = _new_version(con, actor, act, current, status, lines, days, interval, cents,
                           pricing["settings"].get("currency", "EUR"))
    audit(con, actor, "put_plan", "adapter_activation", act, {"version": version, "centsPeriod": cents})
    return {"changed": True, "plan": _current_plans(con, [act])[act]}


def op_cancel_plan(con, actor, args):
    """Cancel subscription (D10: a plan status, nothing more): a new version, cancelled, at no charge."""
    act, _ = _activation(con, actor, args.get("adapterId"), lock=True)
    current = _current_plans(con, [act]).get(act)
    _check_based_on(args, current)
    if not current or current["status"] in ("cancelled", "ended"):
        raise DirectoryError("this adapter has no plan to cancel", 409)
    version = _new_version(con, actor, act, current, "cancelled", current["lines"], current["retentionDays"],
                           current["interval"], 0, current["currency"])
    audit(con, actor, "cancel_plan", "adapter_activation", act, {"version": version})
    return {"changed": True, "plan": _current_plans(con, [act])[act]}


def op_plan_history(con, actor, args):
    """Every version of the adapter's plan at its current customer, newest first (§7.1)."""
    act, _ = _activation(con, actor, args.get("adapterId"))
    thing, name, site = con.run("SELECT a.thing_name, act.name, s.name FROM vsz.adapter_activation act "
                                "JOIN vsz.adapter a ON a.id = act.adapter_id JOIN vsz.site s ON s.id = act.site_id "
                                "WHERE act.id = :a", a=act)[0]
    return {"adapter": {"thingName": thing, "name": name, "site": site},
            "plans": _plan_rows(con, "p.activation_id = :a", a=act)}


def op_customer_users(con, actor, args):
    """The customer's people: the holder first, then its Super Admins and users (D18), with whether
    the actor may disable, reset or re-invite each (may_manage)."""
    cid = _customer(con, actor, args.get("id"))[0]
    rows = con.run(
        "SELECT u.id, u.email, u.name, u.status, u.last_sign_in_at, m.role FROM vsz.membership m "
        "JOIN vsz.app_user u ON u.id = m.user_id WHERE m.org_id = :c "
        "ORDER BY array_position(ARRAY['customer','customer_admin','customer_user'], m.role), lower(u.email)", c=cid)
    out = []
    for uid, email, name, status, last, role in rows:
        others = con.run("SELECT org_id, role FROM vsz.membership WHERE user_id = :u", u=uid)
        out.append({"id": uid, "email": email, "name": name, "status": status, "lastSignInAt": last, "role": role,
                    "manageable": uid != actor["user_id"] and all(may_manage(con, actor, o, r) for o, r in others)})
    return out


def _address(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - set(ADDRESS):
        raise DirectoryError("address: " + ", ".join(ADDRESS))
    out = {}
    for key in ADDRESS:
        v = value.get(key)
        if v is not None and not isinstance(v, str):
            raise DirectoryError(f"address {key} must be text")
        v = (v or "").strip()
        if len(v) > 200:
            raise DirectoryError(f"address {key} is too long")
        if v:
            out[key] = v
    return out or None


def op_get_dealer(con, actor, args):
    """The dealer's own company details (Dealer details, §7.1)."""
    require(actor, "dealer")
    name, email, phone, address, created = con.run(
        "SELECT name, email, phone, address, created_at FROM vsz.org WHERE id = :d", d=actor["org"])[0]
    return {"id": actor["org"], "name": name, "email": email, "phone": phone,
            "address": address if isinstance(address, dict) or address is None else json.loads(address),
            "createdAt": created}


def op_update_own_dealer(con, actor, args):
    require(actor, "dealer")
    f = _org_fields({k: v for k, v in args.items() if k != "status"}, partial=True)
    if "address" in args:
        f["address"] = _address(args["address"])
    if not f:
        raise DirectoryError("nothing to change")
    if f.get("name") and _name_taken(con, f["name"], but=str(actor["org"])):
        raise DirectoryError(f"there is a dealer called {f['name']} already", 409)
    params = {k: (json.dumps(v) if k == "address" and v is not None else v) for k, v in f.items()}
    sets = ", ".join(f"{k} = CAST(:{k} AS jsonb)" if k == "address" else f"{k} = :{k}" for k in f)
    con.run(f"UPDATE vsz.org SET {sets}, updated_at = now() WHERE id = :d", d=actor["org"], **params)
    audit(con, actor, "update_own_dealer", "org", actor["org"], {k: v for k, v in f.items() if k != "address"})
    return {"id": actor["org"], "changed": sorted(f)}


# name: (function, writes) -- a writing operation runs in one transaction
USER_OPS = {
    "me": (op_me, False), "overview": (op_overview, False),
    "list_dealers": (op_list_dealers, False), "create_dealer": (op_create_dealer, True),
    "update_dealer": (op_update_dealer, True), "list_customers": (op_list_customers, False),
    "list_adapters": (op_list_adapters, False), "list_users": (op_list_users, False),
    "invite_user": (op_invite_user, True), "link_sub": (op_link_sub, True),
    "cancel_invite": (op_cancel_invite, True), "user_action": (op_user_action, True),
    "get_pricing": (op_get_pricing, False), "put_pricing": (op_put_pricing, True),
    # the dealer portal (Phase 5)
    "create_customer": (op_create_customer, True), "get_customer": (op_get_customer, False),
    "update_customer": (op_update_customer, True), "customer_adapters": (op_customer_adapters, False),
    "customer_users": (op_customer_users, False), "put_plan": (op_put_plan, True),
    "cancel_plan": (op_cancel_plan, True), "plan_history": (op_plan_history, False),
    "get_dealer": (op_get_dealer, False), "update_own_dealer": (op_update_own_dealer, True),
}


def run_user_op(con, op, actor, args):
    fn, writes = USER_OPS[op]

    def work(c):
        return fn(c, resolve_actor(c, actor), args if isinstance(args, dict) else {})
    return in_transaction(con, work) if writes else work(con)


# --- the handler -------------------------------------------------------------------------------------

def lambda_handler(event, context):
    if isinstance(event, dict) and str(event.get("triggerSource", "")).startswith("TokenGeneration_"):
        return pre_token(event)                       # raising here is how a sign-in is refused
    event = event if isinstance(event, dict) else {}
    try:
        kind, op = (event.get("actor") or {}).get("kind"), event.get("op")
        if kind == "user":
            if op not in USER_OPS:
                raise DirectoryError(f"no operation {op!r}", 404)
            return {"ok": True, "result": jsonable(with_db(
                lambda con: run_user_op(con, op, event["actor"], event.get("args"))))}
        if kind != "operator":
            raise DirectoryError("who is asking? actor.kind must be user or operator", 401)
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
        return {"ok": False, "status": exc.status, "error": exc.args[0]}
    except Exception as exc:
        state = sql_state(exc)
        if state in ("23505", "23514", "23503"):        # a rule the database keeps (001_directory.sql)
            detail = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {}
            return {"ok": False, "status": 409 if state == "23505" else 400, "error": detail.get("M", str(exc))}
        return {"ok": False, "status": 500, "error": f"{type(exc).__name__}: {sql_message(exc)}"}
