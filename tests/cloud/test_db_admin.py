"""vsz-db-admin (DEALER-PANEL-PLAN.md Phase 1): the decisions around the SQL, offline.

The SQL itself runs against a real PostgreSQL in tests_db/. Here a fake connection records what
would be sent, to pin what the function decides: which names can reach SQL text, what is created
only when it's missing, when migrations are refused, that each migration is one transaction with
its record, that every connection is verified TLS with an IAM token, and that the master password
never comes back out.
"""
import ssl

import pytest

from loaders import load_lambda

admin = load_lambda("vsz_db_admin")


class FakeConnection:
    """Records run(sql, **params); answers from (substring, rows) pairs; can fail on a substring."""

    def __init__(self, answers=(), fail_on=None):
        self.sent, self.answers, self.fail_on, self.closed = [], list(answers), fail_on, False

    def run(self, sql, **params):
        self.sent.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise Exception({"C": "42P07", "M": "relation already exists"})   # pg8000's shape
        for needle, rows in self.answers:
            if needle in sql:
                return rows
        return []

    def close(self):
        self.closed = True

    def sql(self):
        return [s for s, _ in self.sent]


def migration(version, name="x", sql="SELECT 1", sha="aa"):
    return {"version": version, "name": f"{version}_{name}.sql", "sql": sql, "sha256": sha}


# --- the migration files ---------------------------------------------------------------------------

def test_the_repositorys_migrations_are_found_in_order():
    found = admin.read_migrations()
    assert found[0]["name"] == "001_directory.sql"
    assert [m["version"] for m in found] == sorted(m["version"] for m in found)
    assert all(len(m["sha256"]) == 64 for m in found)


@pytest.mark.parametrize("names, problem", [
    (["1_short.sql"], "isn't named"),
    (["001_Upper.sql"], "isn't named"),
    (["001_a.sql", "001_b.sql"], "two migrations numbered 001"),
])
def test_badly_named_migrations_are_refused(tmp_path, names, problem):
    for name in names:
        (tmp_path / name).write_text("SELECT 1;")
    with pytest.raises(admin.AdminError, match=problem):
        admin.read_migrations(str(tmp_path))


def test_files_that_arent_sql_are_ignored(tmp_path):
    (tmp_path / "README").write_text("notes")
    (tmp_path / "001_first.sql").write_text("SELECT 1;")
    assert [m["name"] for m in admin.read_migrations(str(tmp_path))] == ["001_first.sql"]


# --- which migrations run, and when the runner refuses --------------------------------------------

def test_only_the_new_ones_are_pending():
    found = [migration("001", sha="a1"), migration("002", sha="b2")]
    assert admin.plan_migrations(found, {"001": ("001_x.sql", "a1")}) == [found[1]]


@pytest.mark.parametrize("found, applied, problem", [
    ([migration("001", sha="changed")], {"001": ("001_x.sql", "a1")}, "has changed since"),
    ([], {"001": ("001_x.sql", "a1")}, "its file is gone"),
    ([migration("001", sha="a1"), migration("002"), migration("003", sha="c3")],
     {"001": ("001_x.sql", "a1"), "003": ("003_x.sql", "c3")}, "sorts before 003_x.sql"),
])
def test_files_that_disagree_with_the_database_stop_everything(found, applied, problem):
    with pytest.raises(admin.AdminError, match=problem):
        admin.plan_migrations(found, applied)


def test_each_migration_is_one_transaction_with_its_record():
    con = FakeConnection([("to_regclass", [[None]]), ("pg_try_advisory_lock", [[True]])])   # an empty database
    result = admin.migrate(con, [migration("001", sql="CREATE TABLE vsz.a ()", sha="s1")])
    sent = con.sql()
    assert sent.index("BEGIN") < sent.index("CREATE TABLE vsz.a ()") < sent.index("COMMIT")
    record = next(p for s, p in con.sent if s.startswith("INSERT INTO vsz.schema_migrations"))
    assert record == {"v": "001", "n": "001_x.sql", "s": "s1"}
    assert any(s.startswith("CREATE TABLE IF NOT EXISTS vsz.schema_migrations") for s in sent)
    assert result["now_applied"] == ["001_x.sql"] and result["pending"] == []


def test_a_dry_run_only_reads():
    con = FakeConnection([("to_regclass", [[None]])])
    result = admin.migrate(con, [migration("001")], dry_run=True)
    assert result == {"applied": [], "pending": ["001_x.sql"]}
    assert all(s.startswith("SELECT") for s in con.sql())


def test_a_failing_migration_is_rolled_back_and_named():
    con = FakeConnection([("to_regclass", [["vsz.schema_migrations"]]),
                          ("SELECT version, name, sha256", [["001", "001_x.sql", "a1"]]),
                          ("pg_try_advisory_lock", [[True]])],
                         fail_on="CREATE TABLE vsz.b")
    with pytest.raises(admin.AdminError, match=r"002_x.sql failed and was rolled back: .*42P07"):
        admin.migrate(con, [migration("001", sha="a1"), migration("002", sql="CREATE TABLE vsz.b ()")])
    assert "ROLLBACK" in con.sql() and "COMMIT" not in con.sql()
    assert con.sql()[-1].startswith("SELECT pg_advisory_unlock")     # the lock goes back, failure or not


def test_a_second_migration_run_waits_its_turn():
    con = FakeConnection([("to_regclass", [[None]]), ("pg_try_advisory_lock", [[False]])])
    with pytest.raises(admin.AdminError, match="another migration run holds the lock"):
        admin.migrate(con, [migration("001")])
    assert "BEGIN" not in con.sql()


# --- bootstrap -------------------------------------------------------------------------------------

def test_a_first_bootstrap_creates_roles_and_database():
    con = FakeConnection([("rolname = 'rds_iam'", [[1]])])
    done = admin.bootstrap(con, database="videosafezone")
    sent = con.sql()
    assert "CREATE ROLE vsz_migrator LOGIN CONNECTION LIMIT 3" in sent
    assert "CREATE ROLE vsz_app LOGIN CONNECTION LIMIT 10" in sent
    assert "GRANT rds_iam TO vsz_migrator" in sent and "GRANT rds_iam TO vsz_app" in sent
    assert "CREATE DATABASE videosafezone OWNER vsz_migrator" in sent
    assert "REVOKE ALL ON DATABASE videosafezone FROM PUBLIC" in sent
    assert sent[-1] == "REVOKE SET OPTION FOR vsz_migrator FROM CURRENT_USER"
    assert len(done) == 3


def test_a_repeated_bootstrap_creates_nothing():
    con = FakeConnection([("rolname = ANY", [["vsz_migrator", 3], ["vsz_app", 10]]),
                          ("rolname = 'rds_iam'", [[1]]),
                          ("pg_get_userbyid", [["vsz_migrator"]])])
    assert admin.bootstrap(con) == ["nothing to do: database and roles exist as they should"]
    assert not [s for s in con.sql() if s.startswith(("CREATE", "ALTER"))]


def test_the_set_option_is_taken_back_even_when_the_database_cant_be_made():
    con = FakeConnection([("rolname = 'rds_iam'", [[1]])], fail_on="CREATE DATABASE")
    with pytest.raises(Exception):
        admin.bootstrap(con)
    assert con.sql()[-1] == "REVOKE SET OPTION FOR vsz_migrator FROM CURRENT_USER"


def test_without_rds_iam_nothing_is_created():
    con = FakeConnection()
    with pytest.raises(admin.AdminError, match="rds_iam"):
        admin.bootstrap(con)
    assert not [s for s in con.sql() if s.startswith("CREATE")]


@pytest.mark.parametrize("name", ["Videosafezone", "db; DROP DATABASE postgres", "x" * 64, ""])
def test_only_plain_names_reach_sql_text(name):
    with pytest.raises(admin.AdminError, match="not a plain identifier"):
        admin.bootstrap(FakeConnection([("rolname = 'rds_iam'", [[1]])]), database=name)


# --- connections: verified TLS, IAM tokens --------------------------------------------------------

def test_tls_verifies_the_certificate_and_the_hostname(monkeypatch):
    monkeypatch.setattr(admin, "DB_CA_BUNDLE", ssl.get_default_verify_paths().cafile or "/etc/ssl/certs/ca-certificates.crt")
    ctx = admin.tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_the_iam_token_is_signed_locally_for_the_named_user(monkeypatch):
    monkeypatch.setattr(admin, "DB_HOST", "db.example.internal")
    token = admin.rds_token("vsz_migrator")                    # the network is blocked: local only
    assert token.startswith("db.example.internal:5432/?") and "DBUser=vsz_migrator" in token
    assert "Action=connect" in token


# --- the handler -----------------------------------------------------------------------------------

@pytest.fixture
def connections(monkeypatch):
    """Replaces connect(); records (user, database, password) and hands out FakeConnections."""
    made = []

    def fake_connect(user, database, password=None):
        con = FakeConnection([("rolname = 'rds_iam'", [[1]]), ("to_regclass", [[None]]),
                              ("SHOW server_version", [["18.3"]]), ("SHOW max_connections", [["81"]])])
        made.append({"user": user, "database": database, "password": password, "con": con})
        return con
    monkeypatch.setattr(admin, "connect", fake_connect)
    return made


def test_an_unknown_action_is_refused(connections):
    assert admin.lambda_handler({"action": "drop"}, None) == {
        "ok": False, "error": "action must be status, bootstrap or migrate"}
    assert admin.lambda_handler(None, None)["ok"] is False
    assert connections == []


@pytest.mark.parametrize("event, problem", [
    ({"action": "bootstrap", "masterPassword": "pw"}, "give masterUser"),
    ({"action": "bootstrap", "masterUser": "postgres; --", "masterPassword": "pw"}, "give masterUser"),
    ({"action": "bootstrap", "masterUser": "postgres"}, "give masterPassword"),
])
def test_bootstrap_needs_a_plain_master_user_and_a_password(connections, event, problem):
    assert admin.lambda_handler(event, None) == {"ok": False, "error": problem}
    assert connections == []


def test_bootstrap_connects_as_the_master_user_to_postgres(connections):
    result = admin.lambda_handler({"action": "bootstrap", "masterUser": "postgres", "masterPassword": "s3cret!"}, None)
    assert result["ok"] is True
    assert (connections[0]["user"], connections[0]["database"], connections[0]["password"]) == \
        ("postgres", "postgres", "s3cret!")
    assert connections[0]["con"].closed


def test_the_master_password_never_comes_back(monkeypatch):
    def failing(user, database, password=None):
        raise Exception({"C": "28P01", "M": f"password authentication failed, got {password}"})
    monkeypatch.setattr(admin, "connect", failing)
    result = admin.lambda_handler({"action": "bootstrap", "masterUser": "postgres", "masterPassword": "s3cret!"}, None)
    assert result["ok"] is False and "s3cret!" not in str(result) and "***" in result["error"]


def test_migrate_and_status_log_in_as_the_migrator_with_an_iam_token(connections):
    assert admin.lambda_handler({"action": "migrate", "dryRun": True}, None)["pending"][0] == "001_directory.sql"
    status = admin.lambda_handler({"action": "status"}, None)
    assert status["ok"] and status["bootstrapped"] and status["maxConnections"] == 81
    assert {(c["user"], c["database"], c["password"]) for c in connections} == \
        {("vsz_migrator", "videosafezone", None)}                   # None: connect() signs a token


def test_status_before_the_bootstrap_says_so(monkeypatch):
    def no_role(user, database, password=None):
        raise Exception({"C": "28000", "M": 'role "vsz_migrator" does not exist'})
    monkeypatch.setattr(admin, "connect", no_role)
    result = admin.lambda_handler({"action": "status"}, None)
    assert result["ok"] is False and result["bootstrapped"] is False
