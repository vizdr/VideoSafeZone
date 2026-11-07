# cloud/package.sh -- what a Lambda's package holds; sourced by deploy.sh and provision.sh, which
# define VMS_HOME, TMP, aws, say and die before sourcing it.
#
# Every function is one file, cloud/lambda/<name_with_underscores>.py, at the root of its zip.
# vsz-db-admin also carries the directory's migrations, cloud/db/migrations/*.sql, as migrations/
# beside it (DEALER-PANEL-PLAN.md Phase 1): deploying its code is how new migrations reach it.

package_files() {     # package_files NAME -- each file's path inside the zip, one per line, sorted
  {
    printf '%s\n' "${1//-/_}.py"
    if [ "$1" = vsz-db-admin ]; then
      (cd "$VMS_HOME/cloud/db" && ls -1 migrations/*.sql)
    fi
  } | LC_ALL=C sort
}

package_source() {    # package_source PATH_IN_ZIP -- that file in the tree, relative to VMS_HOME
  case "$1" in
    migrations/*) printf 'cloud/db/%s\n' "$1" ;;
    *)            printf 'cloud/lambda/%s\n' "$1" ;;
  esac
}

package_lambda() {    # package_lambda NAME ZIP -- a fresh package of this tree's files
  local name=$1 zip=$2 dir="$TMP/package-$1" f
  mkdir -p "$dir"
  while IFS= read -r f; do
    mkdir -p "$dir/$(dirname "$f")"
    cp "$VMS_HOME/$(package_source "$f")" "$dir/$f"
  done < <(package_files "$name")
  (cd "$dir" && package_files "$name" | zip -qX "$zip" -@)
}

# The answer of a directory function (vsz-db-admin, vsz-directory) to EVENT_FILE, pretty-printed;
# returns non-zero when it says {"ok": false} or crashes. The event goes in a file so that a
# password never sits on a command line.
invoke_lambda() {     # invoke_lambda NAME EVENT_FILE
  local name=$1 out="$TMP/$1-answer.json" crashed
  crashed=$(aws lambda invoke --function-name "$name" --cli-binary-format raw-in-base64-out \
    --payload "fileb://$2" "$out" --query FunctionError --output text) ||
    die "couldn't invoke $name"
  [ "$crashed" = None ] || die "$name crashed ($crashed): $(cat "$out")"
  python3 -m json.tool "$out"
  python3 -c 'import json, sys; sys.exit(0 if json.load(open(sys.argv[1])).get("ok") else 1)' "$out"
}

invoke_db_admin() { invoke_lambda vsz-db-admin "$1"; }   # invoke_db_admin EVENT_FILE
