"""vsz-db-admin: set up the directory database and apply its migrations (DEALER-PANEL-PLAN.md Phase 1).

It runs inside the account's default VPC (security group vsz-db-client), where it reaches the
PostgreSQL instance and nothing else -- no Secrets Manager, no internet (D3). Only the operator's
own credentials invoke it, through cloud/provision.sh and cloud/deploy.sh; API Gateway never does.

Actions (the event's "action"):
  status     the server, our database, our two roles and the migrations applied -- as vsz_migrator.
             Before the bootstrap it answers {"ok": false, "bootstrapped": false}.
  bootstrap  once, as the master user, whose password comes in the event: provision.sh reads it
             from Secrets Manager on the operator's machine, as this function can't. Creates the
             database and the login roles vsz_migrator (owns the schema, runs migrations) and
             vsz_app (rows only), both IAM-authenticated and capped in connections so the shared
             micro instance keeps room for the other project. Repeatable. The password is used for
             one connection, never logged, never returned. The master user itself is not changed:
             granting it rds_iam would force IAM login on the other project and lock it out.
  migrate    as vsz_migrator: applies migrations/NNN_*.sql in order, each in one transaction with
             its row in vsz.schema_migrations. Refuses when an applied file has changed or gone,
             or when a new file sorts before one already applied. {"dryRun": true} only lists.

Every connection is TLS, verified against the RDS CA bundle in the vsz-pg layer with the hostname
checked: pg8000 on its own would skip the check and even fall back to plain text, and the IAM
token it sends is a password for 15 minutes.
"""
import hashlib
import os
import re
import ssl

import boto3

REGION = os.environ["AWS_REGION"]
DB_HOST = os.environ.get("DB_HOST", "")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "videosafezone")
DB_CA_BUNDLE = os.environ.get("DB_CA_BUNDLE", "/opt/rds-ca/bundle.pem")
_HERE = os.path.dirname(os.path.abspath(__file__))
# In the deployed package migrations/ sits beside this file; in the repository it is cloud/db/migrations.
MIGRATIONS_DIR = next((d for d in (os.path.join(_HERE, "migrations"),
                                   os.path.normpath(os.path.join(_HERE, "..", "db", "migrations")))
                       if os.path.isdir(d)), os.path.join(_HERE, "migrations"))

MIGRATOR, APP = "vsz_migrator", "vsz_app"
CONNECTION_LIMITS = {MIGRATOR: 3, APP: 10}          # D2: the other project keeps the rest
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
MASTER_USER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
MIGRATION_FILE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")
NOT_BOOTSTRAPPED = {"28000", "28P01", "3D000"}      # no such role / bad login / no such database
# One migration run at a time, whoever starts it. (Reserved concurrency would do it on the Lambda
# side, but the account's total is 10 and AWS keeps all 10 unreserved.)
MIGRATION_LOCK = 0x56535A00                         # a session advisory lock key: "VSZ\0"


class AdminError(Exception):
    """A refusal with a message meant for the operator."""


def ident(name):
    """A constant identifier, checked before it goes into SQL text (identifiers can't be parameters)."""
    if not IDENTIFIER.match(name):
        raise AdminError(f"not a plain identifier: {name!r}")
    return name


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


# --- bootstrap ------------------------------------------------------------------------------------

def bootstrap(con, database=DB_NAME):
    """As the master user, on the instance's 'postgres' database. Returns what it did."""
    db, done = ident(database), []
    existing = {r[0]: r[1] for r in con.run(
        "SELECT rolname, rolconnlimit FROM pg_roles WHERE rolname = ANY(:names)",
        names=[MIGRATOR, APP])}
    if not con.run("SELECT 1 FROM pg_roles WHERE rolname = 'rds_iam'"):
        raise AdminError("no rds_iam role: is this an RDS instance with IAM authentication on?")
    for role, limit in CONNECTION_LIMITS.items():
        if role not in existing:
            con.run(f"CREATE ROLE {ident(role)} LOGIN CONNECTION LIMIT {int(limit)}")
            done.append(f"created role {role} (connection limit {limit})")
        elif existing[role] != limit:
            con.run(f"ALTER ROLE {ident(role)} LOGIN CONNECTION LIMIT {int(limit)}")
            done.append(f"role {role}: connection limit set to {limit}")
        con.run(f"GRANT rds_iam TO {ident(role)}")    # IAM tokens only, never a password
    # PostgreSQL 16+: making a database owned by another role takes SET on that role. Granted for
    # the bootstrap only; ADMIN, which the master user got by creating the role, stays.
    con.run(f"GRANT {MIGRATOR} TO CURRENT_USER WITH INHERIT FALSE, SET TRUE")
    try:
        owner = con.run("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = :d", d=db)
        if not owner:
            con.run(f"CREATE DATABASE {db} OWNER {MIGRATOR}")
            done.append(f"created database {db} (owner {MIGRATOR})")
        elif owner[0][0] != MIGRATOR:
            con.run(f"ALTER DATABASE {db} OWNER TO {MIGRATOR}")
            done.append(f"database {db}: owner set to {MIGRATOR}")
        con.run(f"REVOKE ALL ON DATABASE {db} FROM PUBLIC")   # the other project's roles stay out
        con.run(f"GRANT CONNECT ON DATABASE {db} TO {APP}")
    finally:
        con.run(f"REVOKE SET OPTION FOR {MIGRATOR} FROM CURRENT_USER")
    return done or ["nothing to do: database and roles exist as they should"]


# --- migrations -----------------------------------------------------------------------------------

def read_migrations(directory=MIGRATIONS_DIR):
    """The migration files, in order: [{version, name, sql, sha256}]."""
    found, versions = [], {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".sql"):
            continue
        m = MIGRATION_FILE.match(name)
        if not m:
            raise AdminError(f"migration file {name!r} isn't named NNN_lowercase_words.sql")
        if m.group(1) in versions:
            raise AdminError(f"two migrations numbered {m.group(1)}: {versions[m.group(1)]}, {name}")
        versions[m.group(1)] = name
        with open(os.path.join(directory, name), "rb") as f:
            raw = f.read()
        found.append({"version": m.group(1), "name": name, "sql": raw.decode("utf-8"),
                      "sha256": hashlib.sha256(raw).hexdigest()})
    return found


def applied_migrations(con):
    """{version: (name, sha256)} from vsz.schema_migrations; {} before the first migration."""
    if con.run("SELECT to_regclass('vsz.schema_migrations')")[0][0] is None:
        return {}
    return {v: (n, s) for v, n, s in con.run("SELECT version, name, sha256 FROM vsz.schema_migrations")}


def plan_migrations(found, applied):
    """The migrations to apply, after checking the files against what the database has."""
    by_version = {m["version"]: m for m in found}
    problems = []
    for v, (name, sha) in sorted(applied.items()):
        if v not in by_version:
            problems.append(f"{name} was applied but its file is gone")
        elif by_version[v]["sha256"] != sha:
            problems.append(f"{name} was applied and its file has changed since: add a new migration instead")
    pending = [m for m in found if m["version"] not in applied]
    if applied and pending and pending[0]["version"] < max(applied):
        problems.append(f"{pending[0]['name']} sorts before {applied[max(applied)][0]}, which is applied")
    if problems:
        raise AdminError("; ".join(problems))
    return pending


def migrate(con, found, dry_run=False):
    """As vsz_migrator, on our database."""
    applied = applied_migrations(con)
    pending = plan_migrations(found, applied)
    result = {"applied": sorted(n for n, _ in applied.values()), "pending": [m["name"] for m in pending]}
    if dry_run:
        return result
    if not con.run("SELECT pg_try_advisory_lock(:k)", k=MIGRATION_LOCK)[0][0]:
        raise AdminError("another migration run holds the lock: try again when it has finished")
    try:
        return _apply(con, pending, applied, result)
    finally:
        con.run("SELECT pg_advisory_unlock(:k)", k=MIGRATION_LOCK)


def _apply(con, pending, applied, result):
    if not applied:
        con.run("CREATE SCHEMA IF NOT EXISTS vsz")
        con.run("CREATE TABLE IF NOT EXISTS vsz.schema_migrations ("
                "version text PRIMARY KEY, name text NOT NULL, sha256 text NOT NULL, "
                "applied_at timestamptz NOT NULL DEFAULT now())")
        con.run("REVOKE ALL ON vsz.schema_migrations FROM PUBLIC")
    done = []
    for m in pending:
        con.run("BEGIN")
        try:
            con.run(m["sql"])          # no parameters: the simple protocol, several statements
            con.run("INSERT INTO vsz.schema_migrations (version, name, sha256) VALUES (:v, :n, :s)",
                    v=m["version"], n=m["name"], s=m["sha256"])
            con.run("COMMIT")
        except Exception as exc:
            con.run("ROLLBACK")
            raise AdminError(f"{m['name']} failed and was rolled back: {sql_message(exc)}"
                             + (f"; applied before it: {', '.join(done)}" if done else "")) from exc
        done.append(m["name"])
    result["now_applied"] = done
    result["pending"] = []
    return result


# --- status ---------------------------------------------------------------------------------------

def status(con):
    """As vsz_migrator, on our database: what an operator wants to see."""
    roles = {r[0]: {"connectionLimit": r[1], "iamOnly": r[2]} for r in con.run(
        "SELECT r.rolname, r.rolconnlimit, pg_has_role(r.oid, 'rds_iam', 'MEMBER') "
        "FROM pg_roles r WHERE r.rolname = ANY(:names)", names=[MIGRATOR, APP])}
    return {
        "server": con.run("SHOW server_version")[0][0],
        "maxConnections": int(con.run("SHOW max_connections")[0][0]),
        "connectionsNow": {u or "(background)": n for u, n in con.run(
            "SELECT usename, count(*) FROM pg_stat_activity GROUP BY usename ORDER BY usename")},
        "databases": [r[0] for r in con.run(
            "SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname")],
        "roles": roles,
        "applied": sorted(n for n, _ in applied_migrations(con).values()),
    }


# --- the handler ----------------------------------------------------------------------------------

def lambda_handler(event, context):
    action = (event or {}).get("action")
    secret = None
    try:
        if action == "status":
            try:
                con = connect(MIGRATOR, DB_NAME)
            except Exception as exc:
                if sql_state(exc) in NOT_BOOTSTRAPPED:
                    return {"ok": False, "bootstrapped": False, "error": sql_message(exc)}
                raise
            try:
                return {"ok": True, "bootstrapped": True, **status(con)}
            finally:
                con.close()
        if action == "bootstrap":
            user, secret = event.get("masterUser"), event.get("masterPassword")
            if not isinstance(user, str) or not MASTER_USER.match(user):
                raise AdminError("give masterUser")
            if not isinstance(secret, str) or not secret:
                raise AdminError("give masterPassword")
            con = connect(user, "postgres", password=secret)
            try:
                return {"ok": True, "done": bootstrap(con)}
            finally:
                con.close()
        if action == "migrate":
            found = read_migrations()
            con = connect(MIGRATOR, DB_NAME)
            try:
                return {"ok": True, **migrate(con, found, dry_run=bool(event.get("dryRun")))}
            finally:
                con.close()
        raise AdminError("action must be status, bootstrap or migrate")
    except Exception as exc:
        message = exc.args[0] if isinstance(exc, AdminError) else f"{type(exc).__name__}: {sql_message(exc)}"
        if secret:
            message = message.replace(secret, "***")
        return {"ok": False, "error": message}
