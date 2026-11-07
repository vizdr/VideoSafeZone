#!/usr/bin/env bash
# cloud/adapter-identity.sh -- an adapter's AWS identity, made by the owner (DEALER-PANEL-PLAN.md
# Phase 6, D15 as amended 2026-11-07). (LAUNCH.md Part H)
#
#   bash cloud/adapter-identity.sh pending                       adapters the System console made that
#                                                                have no identity yet
#   bash cloud/adapter-identity.sh create THING... [--dry-run]   for each: its Thing, a certificate
#                                                                attached to it and to the Thing policy,
#                                                                and the folder its Pi restores from
#   bash cloud/adapter-identity.sh revoke THING [--dry-run]      deactivate its certificates, once the
#                                                                System console has revoked it
#
# Why a script the owner runs, and not the System console: making, attaching and deactivating a
# certificate needs IAM permissions on certificates, and IAM can't limit those to this project's --
# certificates are named by random IDs. A web function holding them could detach or deactivate any
# certificate in the shared account (VMS's adapter-01, the pico2w devices). So the System console
# makes the adapter and its Cloud ID, and this script, with the owner's own AWS login, makes the
# identity -- the same four files setup-identity.sh `create` makes for one Pi.
#
# The folder (0700, files 0600): ~/.local/state/videosafezone/adapter-identities/<thing>/ --
#   adapter.cert.pem, adapter.private.key (AWS shows it this once), cacert.pem, AmazonRootCA1.pem,
#   .cert-arn, thing-name. Whoever holds the folder holds the adapter's identity. Copy it to that
#   adapter's Pi (scp, or a stick), run there  bash setup/setup-identity.sh restore <folder>,  and
#   delete every other copy.
#
# Needs the operator's AWS CLI, logged in to the account below (aws login --remote).
set -euo pipefail
VMS_HOME="${VMS_HOME:-$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)}"

ACCOUNT=596633517506
REGION=eu-central-1
POLICY=KVSAdapterThingPolicy                # cloud/iot/kvs-adapter-thing-policy.json: scoped by Thing name
OUT_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/videosafezone/adapter-identities"
SHARED_THING=adapter-01                     # shared with the VMS card (VMS-PORT-PLAN.md D1): never here

AWS="$(command -v aws || echo "$HOME/.local/bin/aws")"
aws() { "$AWS" --region "$REGION" "$@"; }
say()  { printf '%s\n' "$*"; }
warn() { printf '!! %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

DRY=0 ARGS=()
for a in "$@"; do case "$a" in --dry-run) DRY=1 ;; *) ARGS+=("$a") ;; esac; done
set -- "${ARGS[@]+"${ARGS[@]}"}"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

preflight() {
  [ -x "$AWS" ] || die "no AWS CLI (bash setup/setup-user.sh installs it)"
  local acct; acct=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) ||
    die "the AWS CLI isn't logged in -- aws login --remote"
  [ "$acct" = "$ACCOUNT" ] || die "logged in to account $acct, not $ACCOUNT"
}

directory() {   # directory EVENT_JSON -- vsz-directory's answer on stdout; dies when it refuses
  printf '%s' "$1" > "$TMP/event.json"
  local crashed
  crashed=$(aws lambda invoke --function-name vsz-directory --cli-binary-format raw-in-base64-out \
    --payload "fileb://$TMP/event.json" "$TMP/answer.json" --query FunctionError --output text) ||
    die "couldn't invoke vsz-directory"
  [ "$crashed" = None ] || die "vsz-directory crashed: $(cat "$TMP/answer.json")"
  python3 - "$TMP/answer.json" <<'PY' || exit 1
import json, sys
answer = json.load(open(sys.argv[1]))
if not answer.get("ok"):
    sys.exit(f"ERROR: vsz-directory: {answer.get('error')} -- is its Phase 6 code deployed? (deploy.sh lambda vsz-directory)")
PY
  cat "$TMP/answer.json"
}

# "status identity" of THING in the directory (identity: true|false), or nothing
directory_state() {
  directory '{"op": "adapters", "actor": {"kind": "operator"}}' | python3 -c '
import json, sys
thing = sys.argv[1]
for a in json.load(sys.stdin)["adapters"]:
    if a["thingName"] == thing:
        print(a["status"], "true" if a["identity"] else "false")' "$1"
}

valid_thing() { [[ "$1" =~ ^adapter-[0-9]{2,6}$ ]] || die "$1 isn't an adapter's Thing name (adapter-02, ...)"; }

pending() {
  directory '{"op": "adapters", "actor": {"kind": "operator"}}' | python3 -c '
import json, sys
todo = [a for a in json.load(sys.stdin)["adapters"] if not a["identity"] and a["status"] != "revoked"
        and a["thingName"] != sys.argv[1]]
for a in todo:
    print(f"  {a[\"thingName\"]:<14} {a[\"status\"]:<12} {a[\"label\"] or \"\"}")
print(f"{len(todo)} without an identity" + (f":  bash cloud/adapter-identity.sh create {\" \".join(a[\"thingName\"] for a in todo)}" if todo else ""))' "$SHARED_THING"
}

create_one() {
  local thing=$1 out="$OUT_ROOT/$1" state arn cert principals
  valid_thing "$thing"
  [ "$thing" != "$SHARED_THING" ] || die "$thing has its identity already, shared with the VMS card -- setup-identity.sh is its tool"
  state=$(directory_state "$thing")
  [ -n "$state" ] || die "$thing isn't in the directory: make it in the System console (Adapters → New adapters) first"
  [ "${state% *}" != revoked ] || die "$thing is revoked"
  [ "${state#* }" = false ] || die "$thing has an identity already (its certificate is in the directory); revoke it to retire that one"
  [ ! -e "$out" ] || die "$out exists -- an identity made earlier and not yet taken to its Pi? Move it away first; nothing overwritten"
  if aws iot describe-thing --thing-name "$thing" >/dev/null 2>&1; then
    principals=$(aws iot list-thing-principals --thing-name "$thing" --query 'length(principals)' --output text)
    [ "$principals" = 0 ] || die "the Thing $thing has $principals certificate(s) in IoT already -- look before making another"
    say "$thing: the Thing exists, with no certificate"
  elif [ "$DRY" -eq 1 ]; then say "would create the Thing $thing"
  else aws iot create-thing --thing-name "$thing" >/dev/null; say "$thing: Thing created"
  fi
  if [ "$DRY" -eq 1 ]; then
    say "would create an active certificate, attach it to $POLICY and to $thing, write $out, and record it in the directory"
    return
  fi
  ( umask 077; mkdir -p "$out" )
  arn=$(cd "$out" && umask 077 && aws iot create-keys-and-certificate --set-as-active \
          --certificate-pem-outfile adapter.cert.pem --private-key-outfile adapter.private.key \
          --query certificateArn --output text)
  cert=${arn##*/}
  echo "$arn" > "$out/.cert-arn"
  echo "$thing" > "$out/thing-name"
  aws iot attach-policy --policy-name "$POLICY" --target "$arn"
  aws iot attach-thing-principal --thing-name "$thing" --principal "$arn"
  curl -fsS -o "$out/cacert.pem"        https://www.amazontrust.com/repository/SFSRootCAG2.pem
  curl -fsS -o "$out/AmazonRootCA1.pem" https://www.amazontrust.com/repository/AmazonRootCA1.pem
  chmod 700 "$out" && chmod 600 "$out"/* "$out/.cert-arn"
  directory "{\"op\": \"identity_made\", \"actor\": {\"kind\": \"operator\"}, \"thingName\": \"$thing\", \"certificateId\": \"$cert\"}" >/dev/null
  # Proof: the certificate is active, attached to the Thing, and carries the policy.
  [ "$(aws iot describe-certificate --certificate-id "$cert" --query certificateDescription.status --output text)" = ACTIVE ] &&
    aws iot list-thing-principals --thing-name "$thing" --query principals --output text | grep -qF "$arn" &&
    aws iot list-attached-policies --target "$arn" --query 'policies[].policyName' --output text | grep -qw "$POLICY" ||
    die "$thing: the certificate isn't active, attached and covered by $POLICY -- look at $arn"
  say "$thing: identity made ($cert), recorded in the directory, in $out"
  say "   on $thing's Pi:  set THING_NAME=$thing in /etc/adapter/adapter.env, copy the folder over, then"
  say "                    bash setup/setup-identity.sh restore <the folder>   -- and delete the other copies"
}

revoke_one() {
  local thing=$1 state arns arn
  valid_thing "$thing"
  [ "$thing" != "$SHARED_THING" ] ||
    die "$thing's certificates are shared with the VMS card (VMS-PORT-PLAN.md D1): deactivate one by hand if you mean it"
  state=$(directory_state "$thing")
  [ "${state% *}" = revoked ] || die "$thing isn't revoked in the directory -- revoke it in the System console first (now: ${state% *})"
  arns=$(aws iot list-thing-principals --thing-name "$thing" --query principals --output text 2>/dev/null | tr '\t' '\n' | sed '/^$/d;/^None$/d') || true
  [ -n "$arns" ] || { say "$thing: no certificate attached -- nothing to deactivate"; return; }
  while IFS= read -r arn; do
    if [ "$DRY" -eq 1 ]; then say "would deactivate ${arn##*/}"; continue; fi
    aws iot update-certificate --certificate-id "${arn##*/}" --new-status INACTIVE
    [ "$(aws iot describe-certificate --certificate-id "${arn##*/}" --query certificateDescription.status --output text)" = INACTIVE ] ||
      die "${arn##*/} isn't INACTIVE afterwards"
    say "$thing: certificate ${arn##*/} deactivated"
  done <<<"$arns"
}

case "${1:-}" in
  pending) preflight; pending ;;
  create)  shift; [ $# -gt 0 ] || die "name the adapters: create adapter-02 [adapter-03 ...]"; preflight
           for t in "$@"; do create_one "$t"; done
           [ "$DRY" -eq 0 ] || say "dry run: nothing was changed" ;;
  revoke)  [ $# -eq 2 ] || die "revoke one adapter: revoke adapter-02"; preflight; revoke_one "$2"
           [ "$DRY" -eq 0 ] || say "dry run: nothing was changed" ;;
  *) sed -n '2,27p' "$0"; exit 1 ;;
esac
