#!/usr/bin/env bash
# cloud/close-signup.sh -- close self sign-up on the shared user pool (FoundAndFixed #87,
# DEALER-PANEL-PLAN.md Phase 0.1).
#
#   bash cloud/close-signup.sh --dry-run          say what would change, list the users; change nothing
#   bash cloud/close-signup.sh                    close sign-up, prove it, list the users
#   bash cloud/close-signup.sh restore FILE       put back a description saved by an earlier run
#                                                 (--dry-run works here too)
#
# The pool, kvs-demo-users, let anyone call SignUp with a client ID from the page's JavaScript and
# then use every route of kvs-demo-api and VMS's pir-api (#87). The switch that closes it,
# AdminCreateUserConfig.AllowAdminCreateUserOnly, is set only through UpdateUserPool -- and
# UpdateUserPool resets every setting it isn't given to its default: password policy, MFA,
# verification messages, the email sender, the tier, deletion protection. So the update is built
# from the pool's own description: every setting UpdateUserPool accepts (the list comes from the
# CLI's own input skeleton, not from this file) is passed back as it is, and only that switch
# changes. Then:
#   - the description before and after are compared; any other difference is a FAIL, and the copy
#     saved before the change puts the pool back (restore);
#   - a sign-up is tried on every app client of the pool and must be refused ("SignUp is not
#     permitted"); should one be accepted after all, the probe user is deleted at once;
#   - the pool's users are listed, to check that each is known (one created through the open
#     sign-up would be among them).
#
# The pool is shared with VMS (its pir-web client and pir-api): the switch applies to both apps and
# protects both. Nothing else of VMS's is touched. Users made by an administrator
# (admin-create-user, as guide §8.2 does) are unaffected.
#
# Needs the operator's AWS CLI, logged in to the account below (aws login --remote).
set -euo pipefail

ACCOUNT=596633517506
REGION=eu-central-1
POOL_NAME=kvs-demo-users
BACKUPS="${XDG_STATE_HOME:-$HOME/.local/state}/vms-deploy-backups"   # deploy.sh's; one folder per run
SWITCH=AdminCreateUserConfig.AllowAdminCreateUserOnly

AWS="$(command -v aws || echo "$HOME/.local/bin/aws")"
export AWS_PAGER=""     # the CLI would otherwise stop at a pager for the users table
aws() { "$AWS" --region "$REGION" "$@"; }
say()  { printf '%s\n' "$*"; }
warn() { printf '!! %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

DRY=0 MODE=close RESTORE_FILE=
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    restore)   MODE=restore; RESTORE_FILE=${2:-}; [ -n "$RESTORE_FILE" ] || die "usage: restore FILE"; shift ;;
    -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d'; exit 0 ;;
    *)         die "unknown argument: $1 (--help)" ;;
  esac
  shift
done
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
FAILED=0

# --- JSON work: the update input, and what differs between two descriptions -----------------------
cat > "$TMP/pool.py" <<'PY'
import json, sys

def pool(path):
    d = json.load(open(path))
    return d.get("UserPool", d)

def build(desc_path, skel_path, mode):
    """UpdateUserPool's input: every key it accepts, as the description has it; 'close' sets the switch."""
    desc, skel = pool(desc_path), json.load(open(skel_path))
    inp = {"UserPoolId": desc["Id"]}
    for key in skel:
        if key == "UserPoolId":
            continue
        if key == "PoolName":                      # the description calls it Name
            if "Name" in desc:
                inp[key] = desc["Name"]
        elif key in desc:
            inp[key] = desc[key]
    acc = inp.get("AdminCreateUserConfig")
    # The description still carries the retired UnusedAccountValidityDays beside the password
    # policy's TemporaryPasswordValidityDays, and UpdateUserPool refuses the two together. The
    # policy's value is the one in force, so the retired one is left out.
    if acc and "UnusedAccountValidityDays" in acc and \
            "TemporaryPasswordValidityDays" in inp.get("Policies", {}).get("PasswordPolicy", {}):
        del acc["UnusedAccountValidityDays"]
        print("left out: AdminCreateUserConfig.UnusedAccountValidityDays (retired; "
              "Policies.PasswordPolicy.TemporaryPasswordValidityDays is passed back)", file=sys.stderr)
    if mode == "close":
        inp.setdefault("AdminCreateUserConfig", {})["AllowAdminCreateUserOnly"] = True
    print("passed back: " + ", ".join(k for k in inp if k != "UserPoolId"), file=sys.stderr)
    json.dump(inp, sys.stdout, indent=2)

IGNORED = {"LastModifiedDate", "EstimatedNumberOfUsers"}   # move by themselves

def flat(value, prefix=""):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if not prefix and k in IGNORED:
                continue
            out.update(flat(v, f"{prefix}{k}."))
        return out or {prefix[:-1]: {}}
    return {prefix[:-1]: value}

def diff(before_path, after_path, expected):
    """Print every difference; exit 1 if one isn't expected."""
    b, a = flat(pool(before_path)), flat(pool(after_path))
    expected = set(filter(None, expected.split(",")))
    retired = "AdminCreateUserConfig.UnusedAccountValidityDays"
    temp = "Policies.PasswordPolicy.TemporaryPasswordValidityDays"
    bad, seen = 0, 0
    for path in sorted(set(b) | set(a)):
        old, new = b.get(path, "<absent>"), a.get(path, "<absent>")
        if old == new:
            continue
        seen += 1
        show = f"{path}: {json.dumps(old)} -> {json.dumps(new)}"
        if path in expected:
            print(f"  changed    {show}")
        elif path == retired and b.get(temp) == a.get(temp) and new in ("<absent>", a.get(temp)):
            print(f"  benign     {show}  (retired; {temp} unchanged)")
        else:
            print(f"  UNEXPECTED {show}")
            bad += 1
    if not seen:
        print("  no difference")
    sys.exit(1 if bad else 0)

if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"build": build, "diff": diff}[cmd](*args)
PY
py() { python3 "$TMP/pool.py" "$@"; }

# --- the steps -----------------------------------------------------------------------------------
preflight() {
  [ -x "$AWS" ] || die "no AWS CLI (bash setup/setup-user.sh installs it)"
  command -v python3 >/dev/null || die "python3 is needed for the JSON work"
  local acct; acct=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) ||
    die "the AWS CLI isn't logged in -- aws login --remote"
  [ "$acct" = "$ACCOUNT" ] || die "logged in to account $acct, not $ACCOUNT"
  local ids; ids=$(aws cognito-idp list-user-pools --max-results 60 \
    --query "UserPools[?Name=='$POOL_NAME'].Id" --output text)
  [ -n "$ids" ] && [ "$ids" != None ] || die "no user pool named $POOL_NAME"
  [ "$(wc -w <<<"$ids")" -eq 1 ] || die "several user pools named $POOL_NAME: $ids -- won't guess"
  POOL=$ids
  say "pool:    $POOL_NAME ($POOL)"
  say "clients: $(aws cognito-idp list-user-pool-clients --user-pool-id "$POOL" \
    --query 'UserPoolClients[].ClientName' --output text | tr '\t' ' ')"
}

describe() { aws cognito-idp describe-user-pool --user-pool-id "$POOL" > "$1"; }
switch_of() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["UserPool"]["AdminCreateUserConfig"].get("AllowAdminCreateUserOnly", False))' "$1"; }

# One sign-up per app client; refused = proof. Sent with no attributes, so no email goes out.
probe() {
  local cid name user pass out rc
  while IFS=$'\t' read -r cid name; do
    [ -n "$cid" ] || continue
    user="vsz-signup-probe-$(date +%s)-$RANDOM"
    pass="Pr0be!$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')Aa"
    rc=0; out=$(aws cognito-idp sign-up --client-id "$cid" --username "$user" --password "$pass" </dev/null 2>&1) || rc=$?
    if [ "$rc" -ne 0 ] && grep -q NotAuthorizedException <<<"$out" && grep -qi "not permitted" <<<"$out"; then
      say "PASS  $name: sign-up refused (SignUp is not permitted)"
    elif [ "$rc" -eq 0 ]; then
      aws cognito-idp admin-delete-user --user-pool-id "$POOL" --username "$user" </dev/null >/dev/null &&
        warn "the probe user $user was created, and deleted again"
      say "FAIL  $name: sign-up is still accepted"; FAILED=1
    else
      say "FAIL  $name: refused, but not as expected -- no proof: $(tail -1 <<<"$out")"; FAILED=1
    fi
  done < <(aws cognito-idp list-user-pool-clients --user-pool-id "$POOL" \
             --query 'UserPoolClients[].[ClientId,ClientName]' --output text)
}

users() {
  say ""
  say "Users in the pool -- check that you know each one (a stranger here signed up while it was open):"
  aws cognito-idp list-users --user-pool-id "$POOL" \
    --query 'Users[].{user:Username,status:UserStatus,enabled:Enabled,created:UserCreateDate,email:Attributes[?Name==`email`]|[0].Value}' \
    --output table
}

update() {   # $1 = the input file; all or nothing on AWS's side
  local out
  out=$(aws cognito-idp update-user-pool --cli-input-json "file://$1" 2>&1) ||
    die "UpdateUserPool refused, so nothing changed: $out"
}

preflight
describe "$TMP/before.json"

if [ "$MODE" = restore ]; then
  [ -r "$RESTORE_FILE" ] || die "can't read $RESTORE_FILE"
  saved_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["UserPool"]["Id"])' "$RESTORE_FILE") ||
    die "$RESTORE_FILE isn't a saved describe-user-pool"
  [ "$saved_id" = "$POOL" ] || die "$RESTORE_FILE describes $saved_id, not $POOL"
  [ "$(switch_of "$RESTORE_FILE")" = True ] ||
    warn "the saved copy has self sign-up OPEN: restoring it reopens FoundAndFixed #87"
  aws cognito-idp update-user-pool --generate-cli-skeleton input > "$TMP/skel.json"
  py build "$RESTORE_FILE" "$TMP/skel.json" restore > "$TMP/input.json"
  say ""; say "Differences between the pool now and the saved copy (what restore would undo):"
  py diff "$TMP/before.json" "$RESTORE_FILE" "" || true
  if [ "$DRY" -eq 1 ]; then say ""; say "dry run: nothing changed"; exit 0; fi
  update "$TMP/input.json"
  describe "$TMP/after.json"
  say ""; say "The pool after restore, against the saved copy:"
  py diff "$RESTORE_FILE" "$TMP/after.json" "" && say "PASS  restored" || { say "FAIL  still differs"; exit 1; }
  exit 0
fi

if [ "$(switch_of "$TMP/before.json")" = True ]; then
  say "Self sign-up is already closed ($SWITCH = true). Proving it:"
  probe; users
  [ "$FAILED" -eq 0 ] || exit 1
  exit 0
fi

say "Self sign-up is OPEN ($SWITCH = false)."
aws cognito-idp update-user-pool --generate-cli-skeleton input > "$TMP/skel.json"
py build "$TMP/before.json" "$TMP/skel.json" close > "$TMP/input.json"
say "The one change: $SWITCH false -> true; every other setting above is passed back as it is."

if [ "$DRY" -eq 1 ]; then
  say ""; say "The UpdateUserPool input that would be sent:"; cat "$TMP/input.json"; say ""
  users
  say ""; say "dry run: nothing changed"
  exit 0
fi

STAMP=$(date -u +%Y%m%dT%H%M%SZ)-cognito
mkdir -p "$BACKUPS/$STAMP"; chmod 700 "$BACKUPS/$STAMP"
cp "$TMP/before.json" "$BACKUPS/$STAMP/user-pool-before.json"
cp "$TMP/input.json" "$BACKUPS/$STAMP/update-input.json"
say "saved the description before the change: $BACKUPS/$STAMP/user-pool-before.json"

update "$TMP/input.json"
describe "$TMP/after.json"
cp "$TMP/after.json" "$BACKUPS/$STAMP/user-pool-after.json"

say ""; say "The pool before and after:"
if py diff "$TMP/before.json" "$TMP/after.json" "$SWITCH"; then
  say "PASS  only the sign-up switch changed"
else
  say "FAIL  something else changed -- put it back with:"
  say "      bash cloud/close-signup.sh restore $BACKUPS/$STAMP/user-pool-before.json"
  say "      (that reopens sign-up; then look at the UNEXPECTED lines before trying again)"
  FAILED=1
fi
[ "$(switch_of "$TMP/after.json")" = True ] || { say "FAIL  the switch still reads false"; FAILED=1; }

say ""; say "Proof:"
probe
users
[ "$FAILED" -eq 0 ] || exit 1
say ""
say "Done. Next: VMS's page and this one still sign in (an existing user is unaffected);"
say "FoundAndFixed.md #87 can be marked fixed."
