#!/usr/bin/env bash
# cloud/spike-d13.sh -- the D13 spike: can AWS keep one adapter's credentials off another adapter's
# resources? (DEALER-PANEL-PLAN.md Phase 10.1)
#
#   bash cloud/spike-d13.sh setup [--dry-run]      throwaway test resources, all named vsz-spike*: a Thing
#                                                  vsz-spike-a with a certificate and an IoT policy, a role
#                                                  and role alias with D13's policy, four KVS test streams,
#                                                  two SSM parameters, a DynamoDB table with an adapter
#                                                  index, an S3 bucket
#   bash cloud/spike-d13.sh run                    on this Pi, as the spike Thing only: ~30 checks of what
#                                                  it may reach, its own and vsz-spike-b's (cloud/spike_d13.py)
#   bash cloud/spike-d13.sh verify                 with your login: which test streams received video
#   bash cloud/spike-d13.sh teardown [--dry-run]   deletes everything setup made, and the local folder
#
# What it tries, each against a resource of the spike Thing and one of an imaginary second adapter:
# ${credentials-iot:ThingName} in a role policy (SSM paths, S3 keys, topics, stream names), KVS streams
# allowed by their adapterId tag (describe, PutMedia through kvssink, create only tagged as one's own,
# no re-tagging another's), and dynamodb:LeadingKeys on a global secondary index. Nothing shared is
# touched: not KVSAdapterRole, not the Thing policy, not adapter-01, not the cameras table. Cost: a
# minute of test video into streams that keep it an hour, and a few requests.
#
# setup and teardown need your AWS CLI, logged in (aws login --remote); run needs only the spike folder
# setup writes: ~/.local/state/videosafezone/spike-d13/ (0700) -- the spike Thing's certificate and key.
set -euo pipefail
VMS_HOME="${VMS_HOME:-$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)}"

ACCOUNT=596633517506
REGION=eu-central-1
THING=vsz-spike-a OTHER=vsz-spike-b
IOT_POLICY=VszSpikeD13ThingPolicy
ROLE=vsz-spike-d13-role
ALIAS=VszSpikeD13RoleAlias
TABLE=vsz-spike-cameras INDEX=adapterId-index
BUCKET=vsz-spike-$ACCOUNT
PARAMS=/vsz-spike
STREAMS=("vsz-spike-tag-a:$THING" "vsz-spike-tag-b:$OTHER" "$THING.name:" "$OTHER.name:")
FOLDER="${XDG_STATE_HOME:-$HOME/.local/state}/videosafezone/spike-d13"

AWS="$(command -v aws || echo "$HOME/.local/bin/aws")"
aws() { "$AWS" --region "$REGION" "$@"; }
export AWS_PAGER=""
say()  { printf '%s\n' "$*"; }
warn() { printf '!! %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

DRY=0 ARGS=()
for a in "$@"; do case "$a" in --dry-run) DRY=1 ;; *) ARGS+=("$a") ;; esac; done
set -- "${ARGS[@]+"${ARGS[@]}"}"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

do_() {   # do_ "what" command... -- runs it, or says it would
  local what=$1; shift
  if [ "$DRY" -eq 1 ]; then say "would: $what"; return; fi
  "$@" >/dev/null
  say "done: $what"
}

preflight() {
  [ -x "$AWS" ] || die "no AWS CLI (bash setup/setup-user.sh installs it)"
  local acct; acct=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) ||
    die "the AWS CLI isn't logged in -- aws login --remote"
  [ "$acct" = "$ACCOUNT" ] || die "logged in to account $acct, not $ACCOUNT"
}

# --- setup ---------------------------------------------------------------------------------------------

setup() {
  local arn cert stream tag
  # The Thing and its certificate -- the spike's own identity, never adapter-01's.
  if aws iot describe-thing --thing-name "$THING" >/dev/null 2>&1; then say "Thing $THING: exists"
  else do_ "Thing $THING" aws iot create-thing --thing-name "$THING"; fi
  if aws iot get-policy --policy-name "$IOT_POLICY" >/dev/null 2>&1; then say "IoT policy $IOT_POLICY: exists"
  else do_ "IoT policy $IOT_POLICY (cloud/iot/vsz-spike-d13-thing-policy.json: the spike role alias only)" \
    aws iot create-policy --policy-name "$IOT_POLICY" --policy-document "file://$VMS_HOME/cloud/iot/vsz-spike-d13-thing-policy.json"; fi
  if [ -e "$FOLDER/adapter.cert.pem" ]; then say "certificate: in $FOLDER already"
  elif [ "$DRY" -eq 1 ]; then say "would: an active certificate for $THING, attached to $IOT_POLICY, written to $FOLDER"
  else
    ( umask 077; mkdir -p "$FOLDER" )
    arn=$(cd "$FOLDER" && umask 077 && aws iot create-keys-and-certificate --set-as-active \
            --certificate-pem-outfile adapter.cert.pem --private-key-outfile adapter.private.key \
            --query certificateArn --output text)
    echo "$arn" > "$FOLDER/.cert-arn"
    aws iot attach-policy --policy-name "$IOT_POLICY" --target "$arn"
    aws iot attach-thing-principal --thing-name "$THING" --principal "$arn"
    curl -fsS -o "$FOLDER/cacert.pem" https://www.amazontrust.com/repository/SFSRootCAG2.pem
    chmod 700 "$FOLDER" && chmod 600 "$FOLDER"/* "$FOLDER/.cert-arn"
    say "done: certificate ${arn##*/} for $THING, in $FOLDER"
  fi

  # The role and its alias: what the credentials endpoint hands the spike Thing.
  if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then say "role $ROLE: exists"
  else do_ "role $ROLE (trust: credentials.iot.amazonaws.com)" aws iam create-role --role-name "$ROLE" \
    --assume-role-policy-document "file://$VMS_HOME/cloud/iam/kvs-role-trust.json"; fi
  do_ "its policy (cloud/iam/vsz-spike-d13-policy.json: D13's shape, on test resources)" aws iam put-role-policy \
    --role-name "$ROLE" --policy-name spike --policy-document "file://$VMS_HOME/cloud/iam/vsz-spike-d13-policy.json"
  if aws iot describe-role-alias --role-alias "$ALIAS" >/dev/null 2>&1; then say "role alias $ALIAS: exists"
  else
    [ "$DRY" -eq 1 ] || sleep 10                    # a new role takes a few seconds before IoT may use it
    do_ "role alias $ALIAS -> $ROLE" aws iot create-role-alias --role-alias "$ALIAS" \
      --role-arn "arn:aws:iam::$ACCOUNT:role/$ROLE" --credential-duration-seconds 3600
  fi

  # The resources of two adapters: the spike Thing's own, and an imaginary vsz-spike-b's.
  for spec in "${STREAMS[@]}"; do
    stream=${spec%%:*} tag=${spec#*:}
    if aws kinesisvideo describe-stream --stream-name "$stream" >/dev/null 2>&1; then say "stream $stream: exists"
    elif [ -n "$tag" ]; then do_ "stream $stream, tagged adapterId=$tag" aws kinesisvideo create-stream \
      --stream-name "$stream" --data-retention-in-hours 1 --media-type video/h264 --tags "adapterId=$tag"
    else do_ "stream $stream (named for its adapter, no tag)" aws kinesisvideo create-stream \
      --stream-name "$stream" --data-retention-in-hours 1 --media-type video/h264; fi
  done
  do_ "SSM $PARAMS/adapters/$THING/cameras/cam-90" aws ssm put-parameter --overwrite --type SecureString \
    --name "$PARAMS/adapters/$THING/cameras/cam-90" --value "spike secret of $THING"
  do_ "SSM $PARAMS/adapters/$OTHER/cameras/cam-91" aws ssm put-parameter --overwrite --type SecureString \
    --name "$PARAMS/adapters/$OTHER/cameras/cam-91" --value "spike secret of $OTHER"
  if aws dynamodb describe-table --table-name "$TABLE" >/dev/null 2>&1; then say "table $TABLE: exists"
  else
    do_ "table $TABLE (key cameraId) with the index $INDEX (key adapterId, every attribute)" aws dynamodb create-table \
      --table-name "$TABLE" --billing-mode PAY_PER_REQUEST \
      --attribute-definitions AttributeName=cameraId,AttributeType=S AttributeName=adapterId,AttributeType=S \
      --key-schema AttributeName=cameraId,KeyType=HASH \
      --global-secondary-indexes "IndexName=$INDEX,KeySchema=[{AttributeName=adapterId,KeyType=HASH}],Projection={ProjectionType=ALL}"
    [ "$DRY" -eq 1 ] || aws dynamodb wait table-exists --table-name "$TABLE"
  fi
  do_ "row cam-90 of $THING" aws dynamodb put-item --table-name "$TABLE" \
    --item "{\"cameraId\": {\"S\": \"cam-90\"}, \"adapterId\": {\"S\": \"$THING\"}}"
  do_ "row cam-91 of $OTHER" aws dynamodb put-item --table-name "$TABLE" \
    --item "{\"cameraId\": {\"S\": \"cam-91\"}, \"adapterId\": {\"S\": \"$OTHER\"}}"
  if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then say "bucket $BUCKET: exists"
  else do_ "bucket $BUCKET (private)" aws s3api create-bucket --bucket "$BUCKET" \
    --create-bucket-configuration "LocationConstraint=$REGION"; fi
  [ "$DRY" -eq 0 ] || { say "dry run: nothing was changed"; return; }
  say "setup done. Next, on this Pi:  bash cloud/spike-d13.sh run"
  say "(a new role can take a minute before the credentials endpoint hands it out: if run's first check fails, wait and run again)"
}

# --- run, verify --------------------------------------------------------------------------------------

run() {
  local py="$VMS_HOME/venv-adapter/bin/python3"; [ -x "$py" ] || py=python3
  "$py" "$VMS_HOME/cloud/spike_d13.py"
}

verify() {
  local spec stream ep count since now
  since=$(date -u -d '-1 hour' +%Y-%m-%dT%H:%M:%SZ) now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  say "fragments each test stream received in the last hour (kept 1 hour):"
  for spec in "${STREAMS[@]}"; do
    stream=${spec%%:*}
    ep=$(aws kinesisvideo get-data-endpoint --stream-name "$stream" --api-name LIST_FRAGMENTS \
           --query DataEndpoint --output text 2>/dev/null) || { say "  $stream: no such stream"; continue; }
    count=$(aws kinesis-video-archived-media list-fragments --endpoint-url "$ep" --stream-name "$stream" \
              --fragment-selector "FragmentSelectorType=SERVER_TIMESTAMP,TimestampRange={StartTimestamp=$since,EndTimestamp=$now}" \
              --query 'length(Fragments)' --output text 2>/dev/null) || count="? (list-fragments failed)"
    say "  $stream: $count"
  done
  say "expected: vsz-spike-tag-a and $THING.name received some; vsz-spike-tag-b and $OTHER.name none"
}

# --- teardown -------------------------------------------------------------------------------------------

teardown() {
  local arn names name stream
  if aws iot describe-role-alias --role-alias "$ALIAS" >/dev/null 2>&1; then
    do_ "role alias $ALIAS deleted" aws iot delete-role-alias --role-alias "$ALIAS"; fi
  if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
    do_ "role $ROLE's policy deleted" aws iam delete-role-policy --role-name "$ROLE" --policy-name spike
    do_ "role $ROLE deleted" aws iam delete-role --role-name "$ROLE"
  fi
  for arn in $(aws iot list-thing-principals --thing-name "$THING" --query principals --output text 2>/dev/null | sed 's/^None$//'); do
    do_ "certificate ${arn##*/}: policy detached" aws iot detach-policy --policy-name "$IOT_POLICY" --target "$arn"
    do_ "certificate ${arn##*/}: detached from $THING" aws iot detach-thing-principal --thing-name "$THING" --principal "$arn"
    do_ "certificate ${arn##*/}: deactivated" aws iot update-certificate --certificate-id "${arn##*/}" --new-status INACTIVE
    do_ "certificate ${arn##*/}: deleted" aws iot delete-certificate --certificate-id "${arn##*/}"
  done
  if aws iot describe-thing --thing-name "$THING" >/dev/null 2>&1; then
    [ "$DRY" -eq 1 ] || sleep 5                       # detaching a principal settles before the Thing may go
    do_ "Thing $THING deleted" aws iot delete-thing --thing-name "$THING"; fi
  if aws iot get-policy --policy-name "$IOT_POLICY" >/dev/null 2>&1; then
    do_ "IoT policy $IOT_POLICY deleted" aws iot delete-policy --policy-name "$IOT_POLICY"; fi
  for stream in $(aws kinesisvideo list-streams --stream-name-condition ComparisonOperator=BEGINS_WITH,ComparisonValue=vsz-spike \
                    --query 'StreamInfoList[].StreamARN' --output text | sed 's/^None$//'); do
    do_ "stream ${stream#*:stream/} deleted" aws kinesisvideo delete-stream --stream-arn "$stream"
  done
  names=$(aws ssm get-parameters-by-path --path "$PARAMS" --recursive --query 'Parameters[].Name' --output text | sed 's/^None$//')
  for name in $names; do do_ "SSM $name deleted" aws ssm delete-parameter --name "$name"; done
  if aws dynamodb describe-table --table-name "$TABLE" >/dev/null 2>&1; then
    do_ "table $TABLE deleted" aws dynamodb delete-table --table-name "$TABLE"; fi
  if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
    do_ "bucket $BUCKET emptied" aws s3 rm "s3://$BUCKET" --recursive
    do_ "bucket $BUCKET deleted" aws s3api delete-bucket --bucket "$BUCKET"
  fi
  if [ -d "$FOLDER" ]; then
    if [ "$DRY" -eq 1 ]; then say "would: delete $FOLDER (the spike certificate, its key, the results)"
    else
      if cp "$FOLDER/results.json" "${FOLDER%/*}/spike-d13-results.json" 2>/dev/null; then
        say "kept the run's results: ${FOLDER%/*}/spike-d13-results.json"; fi
      rm -rf -- "$FOLDER"; say "done: $FOLDER deleted"
    fi
  fi
  [ "$DRY" -eq 0 ] || say "dry run: nothing was changed"
}

case "${1:-}" in
  setup)    preflight; setup ;;
  run)      run ;;
  verify)   preflight; verify ;;
  teardown) preflight; teardown ;;
  *) sed -n '2,25p' "$0"; exit 1 ;;
esac
