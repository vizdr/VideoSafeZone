"""tests_db/: the directory's SQL against a real PostgreSQL (PYTEST.md, DEALER-PANEL-PLAN.md 1.4).

Apart from tests/ on purpose: that suite blocks every network connection, and these need one -- to
a throwaway server made for the tests, never the account's database. VSZ_TEST_PG_DSN names it, as
a superuser: postgresql://USER:PASSWORD@HOST:PORT/postgres. CI runs a postgres:18 service container
(the RDS instance's major version); on the Pi, tests_db/run-local.sh starts and removes a cluster of
its own. Without VSZ_TEST_PG_DSN every test here is skipped, and an address at AWS is refused.

What the session builds, once: the rds_iam role RDS has (a stand-in here), vsz-db-admin's bootstrap
of the test database (run twice -- it must be repeatable), test-only passwords for the two roles
(on RDS they log in with IAM tokens), and the migrations, applied twice. Each test then works as
vsz_app inside a transaction that is rolled back, so tests can't see each other's rows.
"""
import os
import sys
from pathlib import Path

import pytest

# No AWS identity here, ever, and nothing taken from the shell: vsz-db-admin reads AWS_REGION and
# DB_* when it is imported, and a value the operator's shell happens to export made these tests
# pass on the Pi and fail in CI, where nothing is exported (FoundAndFixed.md #88). The same fakes
# as tests/conftest.py.
for _key in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_DEFAULT_REGION", "AWS_ROLE_ARN",
             "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_CONTAINER_CREDENTIALS_FULL_URI",
             "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "DB_HOST", "DB_PORT", "DB_NAME", "DB_CA_BUNDLE"):
    os.environ.pop(_key, None)
os.environ.update({
    "AWS_REGION": "eu-central-1",
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_CONFIG_FILE": os.devnull,
    "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
    "AWS_EC2_METADATA_DISABLED": "true",
})

sys.path.insert(0, str(Path(__file__).resolve().parent))   # dbhelpers, by name

from dbhelpers import DSN, PASSWORDS, TEST_DB, _server, connect  # noqa: E402
from loaders import load_lambda  # noqa: E402


@pytest.fixture(autouse=True)
def _need_a_server():
    if not DSN:
        pytest.skip("VSZ_TEST_PG_DSN isn't set: no throwaway PostgreSQL (tests_db/run-local.sh, or CI)")


@pytest.fixture(scope="session")
def server():
    return _server() if DSN else None


@pytest.fixture(scope="session")
def admin():
    return load_lambda("vsz_db_admin")


@pytest.fixture(scope="session")
def bootstrapped(server, admin):
    if server is None:
        pytest.skip("no server")
    su = connect(server)
    try:
        su.run(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        if not su.run("SELECT 1 FROM pg_roles WHERE rolname = 'rds_iam'"):
            su.run("CREATE ROLE rds_iam NOLOGIN")          # RDS has it; a stand-in here
        first = admin.bootstrap(su, database=TEST_DB)
        second = admin.bootstrap(su, database=TEST_DB)
        for role, password in PASSWORDS.items():           # IAM tokens on RDS; passwords here
            su.run(f"ALTER ROLE {role} PASSWORD '{password}'")
    finally:
        su.close()
    return {"first": first, "second": second}


@pytest.fixture(scope="session")
def migrated(server, admin, bootstrapped):
    con = connect(server, "vsz_migrator", PASSWORDS["vsz_migrator"], TEST_DB)
    try:
        first = admin.migrate(con, admin.read_migrations())
        again = admin.migrate(con, admin.read_migrations())
    finally:
        con.close()
    return {"first": first, "again": again}


@pytest.fixture
def app(server, migrated):
    """vsz_app on the test database, in a transaction rolled back after the test."""
    con = connect(server, "vsz_app", PASSWORDS["vsz_app"], TEST_DB)
    con.run("BEGIN")
    yield con
    con.run("ROLLBACK")
    con.close()


@pytest.fixture
def superuser(server, migrated):
    con = connect(server, database=TEST_DB)
    yield con
    con.close()


