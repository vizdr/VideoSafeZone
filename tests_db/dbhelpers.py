"""Helpers for tests_db/ (see conftest.py): the server, connections, refusals, builders.

A module of its own, imported by name, because `from conftest import ...` would find tests/conftest.py
first -- the main suite's, which blocks the network.
"""
import os
from urllib.parse import unquote, urlsplit

import pytest

DSN = os.environ.get("VSZ_TEST_PG_DSN", "")
TEST_DB = "vsz_test"
PASSWORDS = {"vsz_migrator": "migrator-test-only", "vsz_app": "app-test-only"}


def _server():
    url = urlsplit(DSN)
    if url.scheme not in ("postgresql", "postgres") or not url.hostname:
        raise pytest.UsageError("VSZ_TEST_PG_DSN must look like postgresql://user:password@host:port/postgres")
    if url.hostname.endswith("amazonaws.com"):
        raise pytest.UsageError("VSZ_TEST_PG_DSN points at AWS: tests_db run only against a throwaway server")
    return {"host": url.hostname, "port": url.port or 5432, "user": unquote(url.username or "postgres"),
            "password": unquote(url.password) if url.password else None}


def connect(server, user=None, password=None, database="postgres"):
    import pg8000.native
    return pg8000.native.Connection(
        user or server["user"], host=server["host"], port=server["port"], database=database,
        password=password if user else server["password"], ssl_context=False, timeout=15)


def refused(con, sql, state, **params):
    """Run SQL that must fail with SQLSTATE `state`; the transaction goes on. Returns the message."""
    con.run("SAVEPOINT expect_refusal")
    try:
        con.run(sql, **params)
    except Exception as exc:  # pg8000.exceptions.DatabaseError: args[0] is the server's fields
        con.run("ROLLBACK TO SAVEPOINT expect_refusal")
        detail = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {"M": str(exc)}
        assert detail.get("C") == state, f"expected SQLSTATE {state}, got {detail}"
        return detail.get("M", "")
    con.run("RELEASE SAVEPOINT expect_refusal")
    pytest.fail(f"accepted, but should have been refused ({state}): {sql}")


# --- builders: rows a test needs, as vsz_app -------------------------------------------------------

def one(con, sql, **params):
    return con.run(sql, **params)[0][0]


def platform(con):
    return one(con, "SELECT id FROM vsz.org WHERE kind = 'platform'")


def dealer(con, name="Dealer A"):
    return one(con, "INSERT INTO vsz.org (kind, parent_id, name) VALUES ('dealer', :p, :n) RETURNING id",
               p=platform(con), n=name)


def customer(con, dealer_id, name="Customer"):
    return one(con, "INSERT INTO vsz.org (kind, parent_id, name) VALUES ('customer', :p, :n) RETURNING id",
               p=dealer_id, n=name)


def person(con, email):
    return one(con, "INSERT INTO vsz.app_user (email) VALUES (:e) RETURNING id", e=email)


def member(con, user_id, org_id, role):
    con.run("INSERT INTO vsz.membership (user_id, org_id, role) VALUES (:u, :o, :r)", u=user_id, o=org_id, r=role)


def site(con, customer_id, name="Home"):
    return one(con, "INSERT INTO vsz.site (customer_id, name, timezone) VALUES (:c, :n, 'Europe/Berlin') RETURNING id",
               c=customer_id, n=name)


def adapter(con, thing, dealer_id=None):
    return one(con, "INSERT INTO vsz.adapter (thing_name, dealer_id) VALUES (:t, :d) RETURNING id",
               t=thing, d=dealer_id)


def activate(con, adapter_id, customer_id, site_id):
    return one(con, "INSERT INTO vsz.adapter_activation (adapter_id, customer_id, site_id) "
                    "VALUES (:a, :c, :s) RETURNING id", a=adapter_id, c=customer_id, s=site_id)
