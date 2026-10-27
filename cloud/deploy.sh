#!/usr/bin/env bash
# cloud/deploy.sh -- deploy the cloud side (Lambdas, browser page) safely. (LAUNCH.md Part H)
#
#   bash cloud/deploy.sh status                    what is live, against this tree and git HEAD
#   bash cloud/deploy.sh lambda get-hls-url ...    deploy the named function(s)
#   bash cloud/deploy.sh lambdas                   deploy every function whose live code differs
#   bash cloud/deploy.sh page                      deploy client/index.html
#   bash cloud/deploy.sh all                       lambdas, then page
#   bash cloud/deploy.sh rollback lambda NAME      put back the copy saved before the last deploy
#   bash cloud/deploy.sh rollback page
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
#   - The page goes up with `Cache-Control: no-cache, must-revalidate`: stale clients after a
#     deploy (FoundAndFixed #11), and CloudFront revalidates, so an upload is live at once.
#   - Done means verified: after each deploy the live code is downloaded again and compared with
#     the tree, and the page is fetched back through CloudFront.
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
PAGE="$VMS_HOME/client/index.html"
BACKUPS="${XDG_STATE_HOME:-$HOME/.local/state}/vms-deploy-backups"
SHARED_WITH_VMS=(clip-to-s3)

AWS="$(command -v aws || echo "$HOME/.local/bin/aws")"
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

# same | head | OTHER: live code is this tree / only git HEAD's version / neither
compare_lambda() {
  local name=$1 dir="$TMP/live-$1" py; py=$(basename "$(file_of "$1")")
  live_lambda "$name" "$dir"
  if cmp -s "$dir/$py" "$(file_of "$name")"; then echo same
  elif git -C "$VMS_HOME" show "HEAD:cloud/lambda/$py" 2>/dev/null | cmp -s - "$dir/$py"; then echo head
  else echo OTHER; fi
}

compare_page() {
  curl -fsS "$SITE/index.html" -o "$TMP/live.html"
  if cmp -s "$TMP/live.html" "$PAGE"; then echo same
  elif git -C "$VMS_HOME" show HEAD:client/index.html | cmp -s - "$TMP/live.html"; then echo head
  else echo OTHER; fi
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
  printf '%-26s %-26s %s\n' "page (index.html)" \
    "$(aws s3api head-object --bucket "$BUCKET" --key index.html --query LastModified --output text | cut -c1-19)" \
    "$(describe "$(compare_page)")"
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
  state=$(compare_lambda "$name")
  case "$state" in
    same) say "$name: already live, unchanged -- skipped"; return 0 ;;
    OTHER) [ "$FORCE" -eq 1 ] || die "$name: live code is neither this tree nor HEAD -- someone else deployed it. Look first (bash cloud/deploy.sh status); --force overrides." ;;
  esac
  for s in "${SHARED_WITH_VMS[@]}"; do
    [ "$name" != "$s" ] || warn "$name is also used by VMS: its event shape and clip rows must stay backward compatible"
  done
  if [ "$DRY" -eq 1 ]; then say "$name: would deploy $(basename "$py") (live: $(describe "$state"))"; return 0; fi
  mkdir -p "$BACKUPS/$STAMP"
  cp "$TMP/live-$name/live.zip" "$BACKUPS/$STAMP/$name.zip"
  (cd "$TMP" && cp "$py" . && zip -qX "$name.zip" "$(basename "$py")")
  aws lambda update-function-code --function-name "$name" --zip-file "fileb://$TMP/$name.zip" --query LastModified --output text >/dev/null
  aws lambda wait function-updated --function-name "$name"
  [ "$(compare_lambda "$name")" = same ] || die "$name: deployed, but the live code doesn't match the tree"
  say "$name: deployed and verified (previous copy: $BACKUPS/$STAMP/$name.zip)"
}

deploy_page() {
  local state; state=$(compare_page)
  case "$state" in
    same) say "page: already live, unchanged -- skipped"; return 0 ;;
    OTHER) [ "$FORCE" -eq 1 ] || die "page: the live page is neither this tree nor HEAD -- someone else deployed it. --force overrides." ;;
  esac
  if [ "$DRY" -eq 1 ]; then say "page: would upload client/index.html (live: $(describe "$state"))"; return 0; fi
  mkdir -p "$BACKUPS/$STAMP"
  cp "$TMP/live.html" "$BACKUPS/$STAMP/index.html"
  aws s3 cp "$PAGE" "s3://$BUCKET/index.html" --content-type text/html \
    --cache-control "no-cache, must-revalidate" --only-show-errors
  [ "$(compare_page)" = same ] || die "page: uploaded, but CloudFront doesn't serve it yet -- see LAUNCH.md Part H"
  say "page: deployed and verified through $SITE (previous copy: $BACKUPS/$STAMP/index.html)"
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
      last=$(ls -1 "$BACKUPS"/*/index.html 2>/dev/null | sort | tail -1) || true
      [ -n "$last" ] || die "no saved copy of the page in $BACKUPS"
      [ "$DRY" -eq 1 ] && { say "would put back $last"; return 0; }
      aws s3 cp "$last" "s3://$BUCKET/index.html" --content-type text/html \
        --cache-control "no-cache, must-revalidate" --only-show-errors
      say "page: rolled back to $last" ;;
    *) die "rollback lambda NAME | rollback page" ;;
  esac
}

case "${1:-}" in
  status)   status ;;
  lambda)   preflight; shift; [ $# -gt 0 ] || die "lambda NAME..."; for n in "$@"; do deploy_lambda "$n"; done ;;
  lambdas)  preflight; for n in $(all_functions); do deploy_lambda "$n"; done ;;
  page)     preflight; deploy_page ;;
  all)      preflight; for n in $(all_functions); do deploy_lambda "$n"; done; deploy_page ;;
  rollback) preflight; shift; rollback "$@" ;;
  *) sed -n '2,15p' "$0"; exit 1 ;;
esac
