"""vsz-db-admin against a real server: the bootstrap and the migration runner (DEALER-PANEL-PLAN.md 1.2).

The bootstrap must be repeatable and leave exactly what D2 promises: two IAM-only login roles with
capped connections, a database the other project's roles can't even connect to, owned by the
migrator, and the master user without a lasting way to act as it. The runner must apply each file
once, in one transaction, and refuse to run when the files and the database disagree.
"""
import os
import shutil

import pytest

from dbhelpers import PASSWORDS, TEST_DB, connect


def test_the_bootstrap_is_repeatable(bootstrapped):
    assert bootstrapped["second"] == ["nothing to do: database and roles exist as they should"]


def test_two_iam_only_roles_with_capped_connections(server, bootstrapped):
    su = connect(server)
    try:
        rows = su.run("SELECT rolname, rolcanlogin, rolconnlimit, rolsuper, rolcreatedb, rolcreaterole, "
                      "pg_has_role(oid, 'rds_iam', 'MEMBER') FROM pg_roles "
                      "WHERE rolname IN ('vsz_migrator', 'vsz_app') ORDER BY rolname")
    finally:
        su.close()
    assert rows == [["vsz_app", True, 10, False, False, False, True],
                    ["vsz_migrator", True, 3, False, False, False, True]]


def test_the_database_belongs_to_the_migrator(server, bootstrapped):
    su = connect(server)
    try:
        assert su.run("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = :d",
                      d=TEST_DB) == [["vsz_migrator"]]
    finally:
        su.close()


def test_the_master_user_keeps_no_way_to_act_as_the_migrator(server, bootstrapped):
    su = connect(server)
    try:
        rows = su.run("SELECT set_option, inherit_option FROM pg_auth_members "
                      "WHERE roleid = 'vsz_migrator'::regrole AND member = (SELECT oid FROM pg_roles "
                      "WHERE rolname = current_user)")
    finally:
        su.close()
    assert all(r == [False, False] for r in rows)


def test_other_roles_cannot_connect_to_the_database(server, bootstrapped):
    su = connect(server)
    su.run("DROP ROLE IF EXISTS vsz_test_stranger")
    su.run("CREATE ROLE vsz_test_stranger LOGIN PASSWORD 'stranger-test-only'")
    try:
        with pytest.raises(Exception) as caught:
            connect(server, "vsz_test_stranger", "stranger-test-only", TEST_DB).close()
        assert caught.value.args[0].get("C") == "42501"         # permission denied for database
    finally:
        su.run("DROP ROLE vsz_test_stranger")
        su.close()


def test_the_migrations_apply_once(migrated):
    assert migrated["first"]["now_applied"][:1] == ["001_directory.sql"]
    assert migrated["again"]["now_applied"] == []
    assert migrated["again"]["pending"] == []
    assert "001_directory.sql" in migrated["again"]["applied"]


@pytest.fixture
def scratch_db(server, admin, bootstrapped):
    """A second database owned by the migrator, for runs that add or change migration files."""
    name = "vsz_test_runner"
    su = connect(server)
    su.run(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    su.run(f"CREATE DATABASE {name} OWNER vsz_migrator")
    con = connect(server, "vsz_migrator", PASSWORDS["vsz_migrator"], name)
    yield con
    con.close()
    su.run(f"DROP DATABASE {name} WITH (FORCE)")
    su.close()


def _copy_migrations(admin, tmp_path):
    folder = tmp_path / "migrations"
    shutil.copytree(admin.MIGRATIONS_DIR, folder)
    return folder


def test_a_new_migration_is_listed_then_applied(admin, scratch_db, tmp_path):
    folder = _copy_migrations(admin, tmp_path)
    admin.migrate(scratch_db, admin.read_migrations(folder))
    (folder / "900_extra.sql").write_text("CREATE TABLE vsz.extra (id int PRIMARY KEY);\n")
    listed = admin.migrate(scratch_db, admin.read_migrations(folder), dry_run=True)
    assert listed["pending"] == ["900_extra.sql"]
    assert scratch_db.run("SELECT to_regclass('vsz.extra')") == [[None]]       # a dry run changes nothing
    admin.migrate(scratch_db, admin.read_migrations(folder))
    assert scratch_db.run("SELECT to_regclass('vsz.extra')::text") == [["vsz.extra"]]


def test_a_failing_migration_leaves_nothing_behind(admin, scratch_db, tmp_path):
    folder = _copy_migrations(admin, tmp_path)
    admin.migrate(scratch_db, admin.read_migrations(folder))
    (folder / "900_broken.sql").write_text(
        "CREATE TABLE vsz.half_done (id int);\nCREATE TABLE vsz.half_done (id int);\n")
    with pytest.raises(admin.AdminError, match="900_broken.sql failed and was rolled back"):
        admin.migrate(scratch_db, admin.read_migrations(folder))
    assert scratch_db.run("SELECT to_regclass('vsz.half_done')") == [[None]]
    assert [r[0] for r in scratch_db.run("SELECT name FROM vsz.schema_migrations ORDER BY version")] == \
        [m["name"] for m in admin.read_migrations(admin.MIGRATIONS_DIR)]


def test_an_applied_migration_that_changed_is_refused(admin, scratch_db, tmp_path):
    folder = _copy_migrations(admin, tmp_path)
    admin.migrate(scratch_db, admin.read_migrations(folder))
    first = sorted(folder.glob("*.sql"))[0]
    first.write_text(first.read_text() + "\n-- edited after it was applied\n")
    with pytest.raises(admin.AdminError, match="has changed since"):
        admin.migrate(scratch_db, admin.read_migrations(folder), dry_run=True)


def test_ff88_no_aws_identity_from_the_shell(admin):
    """FoundAndFixed.md #88: the suite passed on the Pi only because the shell exported AWS_REGION."""
    assert os.environ["AWS_CONFIG_FILE"] == os.devnull
    assert os.environ["AWS_SHARED_CREDENTIALS_FILE"] == os.devnull
    assert os.environ["AWS_ACCESS_KEY_ID"] == "testing"
    assert "AWS_PROFILE" not in os.environ and "DB_HOST" not in os.environ
    assert admin.REGION == "eu-central-1" and admin.DB_HOST == ""

