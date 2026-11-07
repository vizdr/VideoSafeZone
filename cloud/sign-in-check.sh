#!/usr/bin/env bash
# cloud/sign-in-check.sh -- sign in to the user pool vsz-users as someone, the way the web page does,
# and show what the ID token says (DEALER-PANEL-PLAN.md Phase 2).
#
#   bash cloud/sign-in-check.sh EMAIL      the password comes from the demo-accounts file when it
#                                          has one (provision.sh directory-seed), else it is asked for
#
# Prints how long the sign-in took -- the pre-token trigger, vsz-directory, runs inside it, and
# Cognito gives it 5 seconds -- and the vsz_* claims (D4), or Cognito's refusal. A first sign-in
# with a temporary password stops at "choose a new password", which the web page does.
#
# The sign-in itself is the same plain JSON call the page makes, over HTTPS, without AWS keys; the
# operator's AWS CLI only looks up the pool's and the client's IDs. The password goes in a 0600
# file, never on a command line.
set -euo pipefail

REGION=eu-central-1
POOL_NAME=vsz-users
CLIENT_NAME=vsz-web
DEMO_FILE="${XDG_STATE_HOME:-$HOME/.local/state}/videosafezone/demo-accounts.txt"

AWS="$(command -v aws || echo "$HOME/.local/bin/aws")"
export AWS_PAGER=""
aws() { "$AWS" --region "$REGION" "$@"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

EMAIL=${1:-}
[[ "$EMAIL" == *@* ]] || die "usage: bash cloud/sign-in-check.sh EMAIL"
POOL=$(aws cognito-idp list-user-pools --max-results 60 --query "UserPools[?Name=='$POOL_NAME'].Id" --output text)
[ -n "$POOL" ] && [ "$POOL" != None ] || die "no user pool $POOL_NAME (bash cloud/provision.sh identity)"
CLIENT=$(aws cognito-idp list-user-pool-clients --user-pool-id "$POOL" \
  --query "UserPoolClients[?ClientName=='$CLIENT_NAME'].ClientId" --output text)
[ -n "$CLIENT" ] && [ "$CLIENT" != None ] || die "no app client $CLIENT_NAME in $POOL_NAME"

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
( umask 077
  if [ -r "$DEMO_FILE" ] && awk -v e="$EMAIL" '$1 == e {found = 1} END {exit !found}' "$DEMO_FILE"; then
    awk -v e="$EMAIL" '$1 == e {print $2}' "$DEMO_FILE" > "$TMP/pw"
  else
    read -rsp "password for $EMAIL: " PASSWORD; echo >&2
    printf '%s\n' "$PASSWORD" > "$TMP/pw"
  fi )

python3 - "$REGION" "$CLIENT" "$EMAIL" "$TMP/pw" <<'PY'
import base64, json, sys, time, urllib.error, urllib.request
region, client, email, pw_file = sys.argv[1:5]
password = open(pw_file).read().rstrip("\n")
body = json.dumps({"AuthFlow": "USER_PASSWORD_AUTH", "ClientId": client,
                   "AuthParameters": {"USERNAME": email, "PASSWORD": password}}).encode()
req = urllib.request.Request(f"https://cognito-idp.{region}.amazonaws.com/", data=body, headers={
    "Content-Type": "application/x-amz-json-1.1",
    "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth"})
start = time.monotonic()
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        answer = json.load(r)
except urllib.error.HTTPError as e:
    took = time.monotonic() - start
    err = json.load(e)
    message = err.get("message", "").replace("PreTokenGeneration failed with error ", "")
    print(f"{email}: REFUSED after {took:.2f} s -- {err.get('__type', e.code)}: {message}")
    sys.exit(3)
took = time.monotonic() - start
if "AuthenticationResult" not in answer:
    print(f"{email}: {answer.get('ChallengeName')} after {took:.2f} s -- the first sign-in sets a new password (the web page)")
    sys.exit(0)
token = answer["AuthenticationResult"]["IdToken"].split(".")[1]
claims = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
shown = {k: v for k, v in sorted(claims.items()) if k.startswith("vsz_")}
print(f"{email}: signed in in {took:.2f} s -- {json.dumps(shown)}")
PY
