#!/usr/bin/env bash
# cloud/deploy.sh -- deploy the cloud side (Lambdas, browser page) safely. (LAUNCH.md Part H)
#
#   bash cloud/deploy.sh status                    what is live, against this tree and git HEAD
#   bash cloud/deploy.sh lambda get-hls-url ...    deploy the named function(s)
#   bash cloud/deploy.sh lambdas                   deploy every function whose live code differs
#   bash cloud/deploy.sh page                      build client-app/ (the web panel), publish it at / (the site)
#   bash cloud/deploy.sh app                       the same build at /app/ only: a preview beside the site
#   bash cloud/deploy.sh all                       lambdas, then page
#   bash cloud/deploy.sh rollback lambda NAME      put back the copy saved before the last deploy
#   bash cloud/deploy.sh rollback page | app
#   bash cloud/deploy.sh db-status                 the directory database, as vsz-db-admin sees it
#   bash cloud/deploy.sh db-migrate                apply new migrations (cloud/db/migrations/) through it
#   bash cloud/deploy.sh tenancy status            the tenant check (DEALER-PANEL-PLAN.md 3.3): each function's
#                                                  TENANCY, the pools kvs-demo-api's authorizer accepts
#   bash cloud/deploy.sh tenancy log [HOURS]       what the check refused / would refuse, last 24 h
#   bash cloud/deploy.sh tenancy enforce | report  switch every function with the check
#   bash cloud/deploy.sh tenancy old-pool-off      kvs-demo-api stops accepting kvs-demo-users (VMS keeps it)
#   ... --dry-run   say what would happen, change nothing
#   ... --force     deploy over live code that matches neither this tree nor git HEAD
#
# The rules it enforces, each from something that went wrong:
#   - The account is shared with VMS (github.com/vizdr/VMS). This repository deploys the
#     unprefixed functions, the API and the page; VMS deploys only `pir-*` copies, and this
#     script refuses any `pir-*` name. Live code that equals neither this tree nor git HEAD was
#     put there by someone else, and is not overwritten without --force (FoundAndFixed #48).
#   - Before anything is replaced, the live copy is downloaded into BACKUPS (below). The page's
#     bucket keeps no versions, so without this an overwritten page could only come back from a
#     working tree that still has it; `rollback` uses these copies.
#   - `clip-to-s3` is also VMS's: its event shape and clip rows must stay backward compatible.
#   - One file per function, zipped fresh in a temporary directory (never the tracked .zip files).
#     vsz-db-admin also carries cloud/db/migrations/*.sql (cloud/package.sh), and every file of a
#     package counts when live code is compared. `db-migrate` applies them -- only from a live
#     package equal to this tree, so it never runs older migrations than the ones committed here.
#   - The page goes up with `Cache-Control: no-cache, must-revalidate`: stale clients after a
#     deploy (FoundAndFixed #11), and CloudFront revalidates, so an upload is live at once.
#   - Done means verified: after each deploy the live code is downloaded again and compared with
#     the tree, and the page is fetched back through CloudFront.
#   - The page is the web panel (client-app/, WEB-PANEL-PLAN.md), built fresh from its lockfile in
#     a temporary directory: `page` publishes it at the root, `app` at /app/ as a preview. Hashed
#     assets are cached for good and go up first; index.html last, revalidated. A version.json
#     records what was uploaded: a live index.html without a matching one was put there by
#     something else and is not overwritten without --force -- except, at the root, a committed
#     version of the old single-file page (client/index.html), which the cutover replaces
#     (WEB-PANEL-PLAN.md Phase 3). At the root only index.html and version.json are backed up;
#     `rollback page` puts them back, the old page included.
#
# Needs the operator's AWS CLI, logged in to the account below (setup-user.sh installs it;
# `aws login --remote` logs in). The adapter's device certificate cannot deploy, by design.
set -euo pipefail
VMS_HOME="${VMS_HOME:-$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)}"

# --- this deployment (cloud-side identity lives here and in CLAUDE.md, not in adapter.env) --
ACCOUNT=596633517506
REGION=eu-central-1
BUCKET=vms-demo-client-596633517506
SITE=https://dugyd3kkt36pw.cloudfront.net
LAMBDA_DIR="$VMS_HOME/cloud/lambda"
BACKUPS="${XDG_STATE_HOME:-$HOME/.local/state}/vms-deploy-backups"
SHARED_WITH_VMS=(clip-to-s3)
API_NAME=kvs-demo-api
STAGE=prod
AUTHORIZER_NAME=CognitoAuthorizer
OLD_POOL_NAME=kvs-demo-users       # shared with VMS; this API stops accepting it at the end of 3.3
APP_SRC="$VMS_HOME/client-app"
APP_BUILD=""                      # set by build_app
WEB_PREFIX=""                     # where `page` (root: "") or `app` ("app") publishes the build
IMMUTABLE="public, max-age=31536000, immutable"
REVALIDATE="no-cache, must-revalidate"

AWS="$(command -v aws || echo "$HOME/.local/bin/aws")"
NPM="$(command -v npm || echo "$HOME/.local/bin/npm")"
aws() { "$AWS" --region "$REGION" "$@"; }
say()  { printf '%s\n' "$*"; }
warn() { printf '!! %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

DRY=0 FORCE=0 ARGS=()
for a in "$@"; do
  case "$a" in --dry-run) DRY=1 ;; --force) FORCE=1 ;; *) ARGS+=("$a") ;; esac
done
set -- "${ARGS[@]+"${ARGS[@]}"}"

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
# shellcheck source=cloud/package.sh
. "$VMS_HOME/cloud/package.sh"

preflight() {
  [ -x "$AWS" ] || die "no AWS CLI (bash setup/setup-user.sh installs it)"
  local acct; acct=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) ||
    die "the AWS CLI isn't logged in -- aws login --remote"
  [ "$acct" = "$ACCOUNT" ] || die "logged in to account $acct, not $ACCOUNT"
}

file_of() { printf '%s/%s.py' "$LAMBDA_DIR" "${1//-/_}"; }      # get-hls-url -> get_hls_url.py
all_functions() { for f in "$LAMBDA_DIR"/*.py; do b=$(basename "$f" .py); printf '%s\n' "${b//_/-}"; done; }

live_lambda() {   # live_lambda NAME DIR -- the deployed package, unpacked into DIR
  mkdir -p "$2"
  curl -fsS -o "$2/live.zip" "$(aws lambda get-function --function-name "$1" --query Code.Location --output text)"
  (cd "$2" && unzip -oq live.zip)
}

live_migrations() { (cd "$1" && ls -1 migrations/*.sql 2>/dev/null | LC_ALL=C sort) || true; }
head_migrations() {   # the migrations git HEAD's package of NAME would carry
  [ "$1" = vsz-db-admin ] || return 0
  git -C "$VMS_HOME" ls-tree --name-only HEAD cloud/db/migrations/ 2>/dev/null | grep '\.sql$' |
    sed 's|^cloud/db/||' | LC_ALL=C sort || true
}

# same | head | OTHER: live code is this tree / only git HEAD's version / neither. Every file of the
# package counts (cloud/package.sh), and so does a migration that only one side has.
compare_lambda() {
  local name=$1 dir="$TMP/live-$1" py f tree=same head=head
  py=$(basename "$(file_of "$1")")
  live_lambda "$name" "$dir"
  [ "$(live_migrations "$dir")" = "$(package_files "$name" | grep '^migrations/' || true)" ] || tree=""
  while IFS= read -r f; do
    cmp -s "$dir/$f" "$VMS_HOME/$(package_source "$f")" || tree=""
  done < <(package_files "$name")
  if [ -n "$tree" ]; then echo same; return; fi
  [ "$(live_migrations "$dir")" = "$(head_migrations "$name")" ] || head=""
  for f in "$py" $(head_migrations "$name"); do
    git -C "$VMS_HOME" show "HEAD:$(package_source "$f")" 2>/dev/null | cmp -s - "$dir/$f" || head=""
  done
  if [ -n "$head" ]; then echo head; else echo OTHER; fi
}

# The commit holding FILE as a committed version of the old single-file page, or nothing.
old_page_commit() {
  local blob; blob=$(git -C "$VMS_HOME" hash-object "$1")
  git -C "$VMS_HOME" log --all --format=%h --find-object="$blob" -- client/index.html 2>/dev/null | head -1
}

# What a web location serves, for status: the web panel's version.json, or the old page.
describe_live_web() {   # describe_live_web PREFIX
  local prefix=$1 v c
  v=$(curl -fsS "$SITE/${prefix:+$prefix/}version.json" 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print("web panel, commit " + d["commit"] + (" + uncommitted changes" if d.get("dirty") else ""))' 2>/dev/null) || v=""
  if [ -n "$v" ]; then echo "$v (page/app --dry-run compares)"; return; fi
  if curl -fsS "$SITE/${prefix:+$prefix/}index.html" -o "$TMP/live-status.html" 2>/dev/null; then
    c=$(old_page_commit "$TMP/live-status.html")
    if [ -n "$c" ]; then echo "the old single-file page (as committed in $c)"
    else echo "NEITHER -- not put there by this script (FoundAndFixed #48)"; fi
  else
    echo "(not deployed)"
  fi
}

status() {
  preflight
  printf '%-26s %-26s %s\n' FUNCTION "LIVE SINCE" "LIVE CODE IS"
  for name in $(all_functions); do
    local since state
    since=$(aws lambda get-function-configuration --function-name "$name" --query LastModified --output text 2>/dev/null) || { printf '%-26s %s\n' "$name" "(not deployed)"; continue; }
    state=$(compare_lambda "$name")
    printf '%-26s %-26s %s\n' "$name" "${since:0:19}" "$(describe "$state")"
  done
  # The web panel is compared by building it, which takes a minute: `page/app --dry-run` does that.
  local since
  since=$(aws s3api head-object --bucket "$BUCKET" --key index.html --query LastModified --output text 2>/dev/null | cut -c1-19) || since=""
  printf '%-26s %-26s %s\n' "page (/)" "${since:--}" "$(describe_live_web "")"
  since=$(aws s3api head-object --bucket "$BUCKET" --key app/index.html --query LastModified --output text 2>/dev/null | cut -c1-19) || since=""
  printf '%-26s %-26s %s\n' "app (/app/)" "${since:--}" "$(describe_live_web app)"
}
describe() {
  case "$1" in
    same) echo "this tree" ;;
    head) echo "git HEAD -- this tree has undeployed changes" ;;
    *)    echo "NEITHER this tree nor HEAD -- deployed from elsewhere? (FoundAndFixed #48)" ;;
  esac
}

deploy_lambda() {
  local name=$1 py state
  [[ "$name" != pir-* ]] || die "$name is VMS's (pir-*): this repository never deploys it"
  py=$(file_of "$name"); [ -e "$py" ] || die "no $py"
  # A function this tree has but AWS doesn't yet is created by provision.sh, not here.
  if ! aws lambda get-function-configuration --function-name "$name" >/dev/null 2>&1; then
    say "$name: not created yet -- skipped (bash cloud/provision.sh creates it)"; return 0
  fi
  state=$(compare_lambda "$name")
  case "$state" in
    same) say "$name: already live, unchanged -- skipped"; return 0 ;;
    OTHER) [ "$FORCE" -eq 1 ] || die "$name: live code is neither this tree nor HEAD -- someone else deployed it. Look first (bash cloud/deploy.sh status); --force overrides." ;;
  esac
  for s in "${SHARED_WITH_VMS[@]}"; do
    [ "$name" != "$s" ] || warn "$name is also used by VMS: its event shape and clip rows must stay backward compatible"
  done
  # The tenant check enforces when TENANCY is unset: never deploy it to a function that has no
  # setting yet, or the page loses every camera at once (DEALER-PANEL-PLAN.md 3.3).
  if grep -q '^def tenancy_refusal' "$py"; then
    local mode
    mode=$(aws lambda get-function-configuration --function-name "$name" --query Environment.Variables.TENANCY --output text)
    [ -n "$mode" ] && [ "$mode" != None ] ||
      die "$name: no TENANCY setting yet -- bash cloud/provision.sh tenancy first (it sets report)"
  fi
  if [ "$DRY" -eq 1 ]; then say "$name: would deploy $(package_files "$name" | tr '\n' ' ')(live: $(describe "$state"))"; return 0; fi
  mkdir -p "$BACKUPS/$STAMP"
  cp "$TMP/live-$name/live.zip" "$BACKUPS/$STAMP/$name.zip"
  package_lambda "$name" "$TMP/$name.zip"
  aws lambda update-function-code --function-name "$name" --zip-file "fileb://$TMP/$name.zip" --query LastModified --output text >/dev/null
  aws lambda wait function-updated --function-name "$name"
  [ "$(compare_lambda "$name")" = same ] || die "$name: deployed, but the live code doesn't match the tree"
  say "$name: deployed and verified (previous copy: $BACKUPS/$STAMP/$name.zip)"
  [ "$name" != vsz-db-admin ] || say "$name: its migrations are applied by: bash cloud/deploy.sh db-migrate"
}

# --- the tenant check's rollout (DEALER-PANEL-PLAN.md 3.3) ------------------------------------------

tenancy_functions() { grep -l '^def tenancy_refusal' "$LAMBDA_DIR"/*.py | xargs -n1 basename | sed 's/\.py$//; s/_/-/g'; }

api_ids() {           # sets API, AUTHORIZER
  API=$(aws apigateway get-rest-apis --query "items[?name=='$API_NAME'].id" --output text)
  [ -n "$API" ] && [ "$API" != None ] || die "no REST API named $API_NAME"
  AUTHORIZER=$(aws apigateway get-authorizers --rest-api-id "$API" --query "items[?name=='$AUTHORIZER_NAME'].id" --output text)
  [ -n "$AUTHORIZER" ] && [ "$AUTHORIZER" != None ] || die "no authorizer $AUTHORIZER_NAME on $API_NAME"
}
authorizer_pools() { aws apigateway get-authorizer --rest-api-id "$API" --authorizer-id "$AUTHORIZER" \
  --query providerARNs --output text | tr '\t' '\n'; }
pool_arn() {          # pool_arn NAME -- the ARN of the user pool with that name
  local id; id=$(aws cognito-idp list-user-pools --max-results 60 --query "UserPools[?Name=='$1'].Id" --output text)
  [ -n "$id" ] && [ "$id" != None ] && [ "$(wc -w <<<"$id")" -eq 1 ] || die "not exactly one user pool named $1"
  printf 'arn:aws:cognito-idp:%s:%s:userpool/%s\n' "$REGION" "$ACCOUNT" "$id"
}
tenancy_mode() { aws lambda get-function-configuration --function-name "$1" --query Environment.Variables.TENANCY --output text; }

set_tenancy() {       # set_tenancy FUNCTION MODE -- TENANCY changed, every other variable kept
  local vars merged
  vars=$(aws lambda get-function-configuration --function-name "$1" --query 'Environment.Variables' --output json)
  merged=$(python3 -c 'import json, sys
d = json.loads(sys.argv[1]) or {}; d["TENANCY"] = sys.argv[2]; print(json.dumps({"Variables": d}))' "$vars" "$2")
  aws lambda update-function-configuration --function-name "$1" --environment "$merged" >/dev/null
  aws lambda wait function-updated --function-name "$1"
}

tenancy_command() {   # tenancy_command status | log [HOURS] | report | enforce | old-pool-off
  local fn mode old
  api_ids
  case "${1:-status}" in
    status)
      for fn in $(tenancy_functions); do printf '  %-26s TENANCY=%s\n' "$fn" "$(tenancy_mode "$fn")"; done
      say "$AUTHORIZER_NAME accepts: $(authorizer_pools | tr '\n' ' ')" ;;
    log)
      local since=$(( ($(date +%s) - ${2:-24} * 3600) * 1000 ))
      for fn in $(tenancy_functions); do
        aws logs filter-log-events --log-group-name "/aws/lambda/$fn" --start-time "$since" \
          --filter-pattern '"tenancy"' --query 'events[].message' --output json 2>/dev/null || echo '[]'
      done | python3 -c '
import collections, json, sys
seen = collections.Counter()
for chunk in sys.stdin.read().replace("][", "]\n[").splitlines():
    for message in json.loads(chunk or "[]"):
        try:
            e = json.loads(message[message.index("{"):])
        except ValueError:
            continue
        seen[(e["tenancy"], e["verdict"], e.get("role") or "(no vsz claims: old pool)", e["need"], e.get("camera"))] += 1
if not seen:
    print("  nothing refused, nothing that would be")
for (what, verdict, role, need, camera), n in sorted(seen.items()):
    print(f"  {n:5d}  {what:12s} {verdict:9s} {role:28s} {need:7s} {camera}")'
      say "(the last ${2:-24} h; refusals of callers with no vsz claims are the old pool's page -- expected until the switch)" ;;
    report|enforce)
      for fn in $(tenancy_functions); do
        mode=$(tenancy_mode "$fn")
        if [ "$mode" = "$1" ]; then say "  $fn: $1 already"; continue; fi
        if [ "$DRY" -eq 1 ]; then say "  $fn: would switch $mode -> $1"; continue; fi
        set_tenancy "$fn" "$1"
        [ "$(tenancy_mode "$fn")" = "$1" ] || die "$fn: TENANCY isn't $1 afterwards"
        say "  $fn: $mode -> $1"
      done ;;
    old-pool-off)
      old=$(pool_arn "$OLD_POOL_NAME")
      grep -qxF "$(pool_arn vsz-users)" <<<"$(authorizer_pools)" || die "$AUTHORIZER_NAME doesn't accept vsz-users: nothing to switch to"
      for fn in $(tenancy_functions); do
        [ "$(tenancy_mode "$fn")" = enforce ] || die "$fn doesn't enforce yet: bash cloud/deploy.sh tenancy enforce first"
      done
      if ! grep -qxF "$old" <<<"$(authorizer_pools)"; then say "$AUTHORIZER_NAME accepts $OLD_POOL_NAME no more already"; return; fi
      if [ "$DRY" -eq 1 ]; then say "would remove $OLD_POOL_NAME from $AUTHORIZER_NAME and deploy stage $STAGE (VMS's pir-api keeps it)"; return; fi
      aws apigateway update-authorizer --rest-api-id "$API" --authorizer-id "$AUTHORIZER" \
        --patch-operations "op=remove,path=/providerARNs,value=$old" >/dev/null
      ! grep -qxF "$old" <<<"$(authorizer_pools)" || die "$AUTHORIZER_NAME still accepts $OLD_POOL_NAME after the patch"
      aws apigateway create-deployment --rest-api-id "$API" --stage-name "$STAGE" --description "deploy.sh tenancy old-pool-off" >/dev/null
      say "$AUTHORIZER_NAME now accepts only: $(authorizer_pools | tr '\n' ' ') -- stage $STAGE deployed" ;;
    *) die "tenancy status | log [HOURS] | report | enforce | old-pool-off" ;;
  esac
}

# The page signs in to the pool of its COGNITO_CLIENT_ID; publishing it before kvs-demo-api's
# authorizer accepts that pool would answer every call 401 (DEALER-PANEL-PLAN.md 3.3).
check_page_pool() {
  local client pool arn
  client=$(sed -n 's/^export const COGNITO_CLIENT_ID = "\([^"]*\)".*/\1/p' "$APP_SRC/src/config.ts")
  [ -n "$client" ] || die "no COGNITO_CLIENT_ID in client-app/src/config.ts"
  for pool in $(aws cognito-idp list-user-pools --max-results 60 --query 'UserPools[].Id' --output text); do
    if aws cognito-idp describe-user-pool-client --user-pool-id "$pool" --client-id "$client" >/dev/null 2>&1; then
      arn="arn:aws:cognito-idp:$REGION:$ACCOUNT:userpool/$pool"; break
    fi
  done
  [ -n "${arn:-}" ] || die "client-app/src/config.ts names app client $client, which no user pool here has"
  api_ids
  grep -qxF "$arn" <<<"$(authorizer_pools)" ||
    die "the page signs in to $arn, which $AUTHORIZER_NAME doesn't accept yet -- bash cloud/provision.sh tenancy first"
}

# The directory database through vsz-db-admin (DEALER-PANEL-PLAN.md Phase 1): status, or the
# migrations its live package carries.
db_command() {        # db_command status | migrate
  local event="$TMP/db-event.json" state
  aws lambda get-function-configuration --function-name vsz-db-admin >/dev/null 2>&1 ||
    die "vsz-db-admin doesn't exist yet: bash cloud/provision.sh directory-db"
  case "$1" in
    status) printf '{"action": "status"}\n' > "$event" ;;
    migrate)
      state=$(compare_lambda vsz-db-admin)
      if [ "$DRY" -eq 1 ]; then
        [ "$state" = same ] || warn "vsz-db-admin's live package isn't this tree: this lists the live package's migrations"
        printf '{"action": "migrate", "dryRun": true}\n' > "$event"
      else
        [ "$state" = same ] || die "vsz-db-admin's live package isn't this tree ($(describe "$state")): bash cloud/deploy.sh lambda vsz-db-admin first -- the package carries the migrations"
        printf '{"action": "migrate"}\n' > "$event"
      fi ;;
  esac
  invoke_db_admin "$event"
}

content_type() {
  case "$1" in
    *.html) echo text/html ;;           *.js) echo text/javascript ;;
    *.css) echo text/css ;;             *.json) echo application/json ;;
    *.svg) echo image/svg+xml ;;        *.png) echo image/png ;;
    *.ico) echo image/x-icon ;;         *.woff2) echo font/woff2 ;;
    *.woff) echo font/woff ;;           *.ttf) echo font/ttf ;;
    *.eot) echo application/vnd.ms-fontobject ;;
    *) echo application/octet-stream ;;
  esac
}

# A fresh build of this tree's client-app/ from its lockfile, in $TMP -- never the working dist/
# or node_modules, so what goes up is what the lockfile says. Sets APP_BUILD.
build_app() {
  [ -x "$NPM" ] || die "no npm -- LAUNCH.md Part H installs Node"
  local src="$TMP/app-src" bin; bin=$(dirname "$NPM")
  mkdir -p "$src"
  (cd "$APP_SRC" && tar --exclude=./node_modules --exclude=./dist -cf - .) | tar -xf - -C "$src"
  (cd "$src" && PATH="$bin:$PATH" "$NPM" ci --no-fund --no-audit --loglevel=error >/dev/null &&
     PATH="$bin:$PATH" "$NPM" run --silent build >/dev/null) || die "client-app: the build failed"
  APP_BUILD="$src/dist"
  local dirty=false
  [ -z "$(git -C "$VMS_HOME" status --porcelain -- client-app)" ] || dirty=true
  printf '{"commit": "%s", "dirty": %s, "builtAt": "%s", "index": "%s"}\n' \
    "$(git -C "$VMS_HOME" rev-parse --short HEAD)" "$dirty" "$STAMP" \
    "$(sha256sum < "$APP_BUILD/index.html" | cut -d' ' -f1)" > "$APP_BUILD/version.json"
}

web_path() { printf '%s' "${WEB_PREFIX:+$WEB_PREFIX/}$1"; }     # index.html -> app/index.html
web_where() { if [ -n "$WEB_PREFIX" ]; then echo "/$WEB_PREFIX/"; else echo "/"; fi; }
web_name() { if [ -n "$WEB_PREFIX" ]; then echo app; else echo page; fi; }

# same | ours | old | none | OTHER | ERROR-<code>: the live index.html against the build.
# "ours" = put there by this script (its version.json records that index.html's sha256);
# "old"  = at the root, a committed version of the old single-file page (the cutover replaces it).
compare_web() {
  local code recorded
  code=$(curl -s -o "$TMP/live-web.html" -w '%{http_code}' "$SITE/$(web_path index.html)")
  case "$code" in
    200) ;;
    403|404) echo none; return ;;       # S3 under OAC answers 403 for a key that doesn't exist
    *) echo "ERROR-$code"; return ;;
  esac
  if cmp -s "$TMP/live-web.html" "$APP_BUILD/index.html"; then echo same; return; fi
  recorded=$(curl -fsS "$SITE/$(web_path version.json)" 2>/dev/null |
    python3 -c 'import json,sys; print(json.load(sys.stdin).get("index", ""))' 2>/dev/null) || recorded=""
  if [ -n "$recorded" ] && [ "$recorded" = "$(sha256sum < "$TMP/live-web.html" | cut -d' ' -f1)" ]; then
    echo ours
  elif [ -z "$WEB_PREFIX" ] && [ -n "$(old_page_commit "$TMP/live-web.html")" ]; then
    echo old
  else
    echo OTHER
  fi
}
describe_web() {
  case "$1" in
    same) echo "this tree" ;;
    ours) echo "an earlier deploy by this script" ;;
    old)  echo "the old single-file page, as committed in $(old_page_commit "$TMP/live-web.html") -- this is the cutover" ;;
    none) echo "nothing yet -- first deploy" ;;
    *)    echo "NEITHER -- not put there by this script (FoundAndFixed #48)" ;;
  esac
}

# Uploads a built app directory: hashed assets first, cached for good; index.html and
# version.json last, revalidated (FoundAndFixed #11), so no browser gets an index.html whose
# assets aren't there yet. Earlier assets are left in place for pages still open on them.
upload_web_dir() {
  local dir=$1 f files
  files=$(cd "$dir" && find . -type f | sed 's|^\./||' | sort)
  for f in $files; do
    case "$f" in index.html|version.json) continue ;; esac
    aws s3 cp "$dir/$f" "s3://$BUCKET/$(web_path "$f")" --content-type "$(content_type "$f")" \
      --cache-control "$([[ $f == assets/* ]] && echo "$IMMUTABLE" || echo "$REVALIDATE")" --only-show-errors
  done
  for f in version.json index.html; do
    [ -e "$dir/$f" ] || continue
    aws s3 cp "$dir/$f" "s3://$BUCKET/$(web_path "$f")" --content-type "$(content_type "$f")" \
      --cache-control "$REVALIDATE" --only-show-errors
  done
}

deploy_web() {   # deploy_web PREFIX -- "" for the site (page), "app" for the preview
  WEB_PREFIX=$1
  check_page_pool
  build_app
  local state i name; state=$(compare_web); name=$(web_name)
  case "$state" in
    same) say "$name: already live at $(web_where), unchanged -- skipped"; return 0 ;;
    ours|none|old) ;;
    OTHER) [ "$FORCE" -eq 1 ] || die "$name: the live $(web_where)index.html wasn't put there by this script -- look first; --force overrides." ;;
    *) die "$name: CloudFront answered ${state#ERROR-} for $(web_where)index.html" ;;
  esac
  if [ "$DRY" -eq 1 ]; then
    say "$name: would upload $(find "$APP_BUILD" -type f | wc -l) files to $(web_where) (live: $(describe_web "$state"))"
    return 0
  fi
  if [ "$state" != none ]; then
    mkdir -p "$BACKUPS/$STAMP"
    if [ -n "$WEB_PREFIX" ]; then
      mkdir -p "$BACKUPS/$STAMP/$WEB_PREFIX"
      aws s3 sync "s3://$BUCKET/$WEB_PREFIX/" "$BACKUPS/$STAMP/$WEB_PREFIX/" --only-show-errors
    else
      # At the root, only what this deploy replaces -- not the whole bucket (app/ lives there too).
      cp "$TMP/live-web.html" "$BACKUPS/$STAMP/index.html"
      if curl -fsS "$SITE/version.json" -o "$TMP/live-version.json" 2>/dev/null; then
        cp "$TMP/live-version.json" "$BACKUPS/$STAMP/version.json"
      fi
    fi
  fi
  upload_web_dir "$APP_BUILD"
  # CloudFront keeps an error answer for a few seconds -- the 403 from before a first deploy.
  for i in 1 2 3 4 5 6 7 8; do [ "$(compare_web)" = same ] && break; sleep 5; done
  [ "$(compare_web)" = same ] || die "$name: uploaded, but CloudFront doesn't serve it yet -- see LAUNCH.md Part H"
  local js; js=$(grep -o 'assets/[^"]*\.js' "$APP_BUILD/index.html" | head -1)
  curl -fsS -o /dev/null "$SITE/$(web_path "$js")" || die "$name: $js doesn't load through CloudFront"
  say "$name: deployed and verified at $SITE/$(web_path index.html)" \
      "$([ "$state" != none ] && echo "(previous copy: $BACKUPS/$STAMP/)")"
}

rollback() {
  local what=$1 name=${2:-} last
  last=$(ls -1d "$BACKUPS"/*/ 2>/dev/null | sort | tail -1) || true
  case "$what" in
    lambda)
      [ -n "$name" ] || die "rollback lambda NAME"
      last=$(ls -1 "$BACKUPS"/*/"$name.zip" 2>/dev/null | sort | tail -1) || true
      [ -n "$last" ] || die "no saved copy of $name in $BACKUPS"
      [ "$DRY" -eq 1 ] && { say "would put back $last"; return 0; }
      aws lambda update-function-code --function-name "$name" --zip-file "fileb://$last" --query LastModified --output text >/dev/null
      aws lambda wait function-updated --function-name "$name"
      say "$name: rolled back to $last" ;;
    page)
      # The root's index.html as it was before the last page deploy -- the old single-file page
      # after the cutover -- with its version.json, or without one when it had none.
      last=$(ls -1 "$BACKUPS"/*/index.html 2>/dev/null | sort | tail -1) || true
      [ -n "$last" ] || die "no saved copy of the page in $BACKUPS"
      [ "$DRY" -eq 1 ] && { say "would put back $last"; return 0; }
      if [ -e "$(dirname "$last")/version.json" ]; then
        aws s3 cp "$(dirname "$last")/version.json" "s3://$BUCKET/version.json" --content-type application/json \
          --cache-control "$REVALIDATE" --only-show-errors
      else
        aws s3api delete-object --bucket "$BUCKET" --key version.json >/dev/null || true
      fi
      aws s3 cp "$last" "s3://$BUCKET/index.html" --content-type text/html \
        --cache-control "$REVALIDATE" --only-show-errors
      say "page: rolled back to $last" ;;
    app)
      last=$(ls -1d "$BACKUPS"/*/app 2>/dev/null | sort | tail -1) || true
      [ -n "$last" ] || die "no saved copy of the app in $BACKUPS -- a first deploy leaves none"
      [ "$DRY" -eq 1 ] && { say "would put back $last"; return 0; }
      WEB_PREFIX=app
      upload_web_dir "$last"
      say "app: rolled back to $last" ;;
    *) die "rollback lambda NAME | rollback page | rollback app" ;;
  esac
}

case "${1:-}" in
  status)   status ;;
  lambda)   preflight; shift; [ $# -gt 0 ] || die "lambda NAME..."; for n in "$@"; do deploy_lambda "$n"; done ;;
  lambdas)  preflight; for n in $(all_functions); do deploy_lambda "$n"; done ;;
  page)     preflight; deploy_web "" ;;
  app)      preflight; deploy_web app ;;
  all)      preflight; for n in $(all_functions); do deploy_lambda "$n"; done; deploy_web "" ;;
  rollback) preflight; shift; rollback "$@" ;;
  db-status)  preflight; db_command status ;;
  tenancy)    preflight; shift; tenancy_command "$@" ;;
  db-migrate) preflight; db_command migrate ;;
  *) sed -n '2,20p' "$0"; exit 1 ;;
esac
