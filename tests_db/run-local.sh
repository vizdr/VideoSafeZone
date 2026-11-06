#!/usr/bin/env bash
# tests_db/run-local.sh -- run tests_db/ against a throwaway PostgreSQL on this machine (PYTEST.md).
#
#   bash tests_db/run-local.sh [pytest arguments]
#
# Makes a fresh cluster in a temporary directory, listening on 127.0.0.1 only, on a free port, with
# a random superuser password; runs the tests with VSZ_TEST_PG_DSN pointing at it; then stops it and
# removes the directory -- also when a test fails. Nothing is installed, nothing keeps running, no
# system service is touched.
#
# Needs PostgreSQL's server binaries: PG_BIN=/path/to/bin, else the newest /usr/lib/postgresql/*/bin.
# On the Pi: sudo apt install postgresql-17, then sudo systemctl disable --now postgresql, so the
# packaged server doesn't run all the time -- only these binaries are used. (CI runs PostgreSQL 18,
# the RDS instance's version, in a service container.) And pg8000 in venv-adapter:
#   venv-adapter/bin/python -m pip install --require-hashes --no-deps -r cloud/layers/vsz-pg/requirements.txt
set -euo pipefail
VMS_HOME="${VMS_HOME:-$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)}"

PG_BIN="${PG_BIN:-$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)}"
[ -n "$PG_BIN" ] && [ -x "$PG_BIN/initdb" ] && [ -x "$PG_BIN/pg_ctl" ] ||
  { echo "no PostgreSQL server binaries: install postgresql-17, or set PG_BIN" >&2; exit 2; }
PY="$VMS_HOME/venv-adapter/bin/python"; [ -x "$PY" ] || PY=python3
"$PY" -c 'import pg8000' 2>/dev/null ||
  { echo "no pg8000 for $PY: pip install --require-hashes --no-deps -r cloud/layers/vsz-pg/requirements.txt" >&2; exit 2; }

DIR=$(mktemp -d "${TMPDIR:-/tmp}/vsz-pg.XXXXXX")
cleanup() {
  "$PG_BIN/pg_ctl" -D "$DIR/data" -m immediate -w stop >/dev/null 2>&1 || true
  rm -rf -- "$DIR"
}
trap cleanup EXIT

PORT=$("$PY" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
PASSWORD=$("$PY" -c 'import secrets; print(secrets.token_hex(16))')
( umask 077; printf '%s\n' "$PASSWORD" > "$DIR/pw" )
"$PG_BIN/initdb" -D "$DIR/data" -U postgres --pwfile="$DIR/pw" --auth=scram-sha-256 -E UTF8 \
  --locale=C.UTF-8 >"$DIR/initdb.log" 2>&1 || { cat "$DIR/initdb.log" >&2; exit 1; }
# TCP on 127.0.0.1, no Unix socket: a socket path under a long TMPDIR exceeds the 107-byte limit.
"$PG_BIN/pg_ctl" -D "$DIR/data" -l "$DIR/server.log" -w \
  -o "-c listen_addresses=127.0.0.1 -p $PORT -c unix_socket_directories=''" start >/dev/null ||
  { cat "$DIR/server.log" >&2; exit 1; }
echo "throwaway PostgreSQL $("$PG_BIN/postgres" --version | awk '{print $3}') on 127.0.0.1:$PORT"

cd "$VMS_HOME"
VSZ_TEST_PG_DSN="postgresql://postgres:$PASSWORD@127.0.0.1:$PORT/postgres" "$PY" -m pytest tests_db "$@"
