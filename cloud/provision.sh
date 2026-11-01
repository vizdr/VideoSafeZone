#!/usr/bin/env bash
# cloud/provision.sh -- create, once, the AWS resources a new cloud feature needs. (LAUNCH.md Part H)
#
#   bash cloud/provision.sh user-settings --dry-run   say what would be created, change nothing
#   bash cloud/provision.sh user-settings             GET/PUT /me/settings (WEB-PANEL-PLAN.md 2.1):
#                                                     table, role, function, routes, CORS, stage
#   bash cloud/provision.sh camera-settings           POST /cameras/settings (WEB-PANEL-PLAN.md
#                                                     Phase 4): role, function, route, CORS, stage
#   bash cloud/provision.sh thumbnails                camera thumbnails (Phase 5.3): a policy for the
#                                                     Pi's role and for list-cameras', its BUCKET
#
# deploy.sh replaces the code of functions that exist; this creates what doesn't exist yet -- and
# only that. Every step looks first and leaves an existing resource as it is, so a second run
# changes nothing and a run that stopped halfway can simply be repeated. The rules, as deploy.sh's:
#   - The account is shared with VMS and others: never a pir-* name, and nothing that exists is
#     modified. A role whose inline policy differs from the file in cloud/iam/ is reported, not
#     overwritten -- the files must match what is deployed (FoundAndFixed #41), so look first.
#   - The policy that goes up is the file in cloud/iam/, nothing typed here.
#   - Done means verified: the new route answers through API Gateway (CORS preflight, the Cognito
#     authorizer, and the function behind it via test-invoke).
#
# Needs the operator's AWS CLI, logged in to the account below (aws login --remote).
set -euo pipefail
VMS_HOME="${VMS_HOME:-$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)}"

ACCOUNT=596633517506
REGION=eu-central-1
API_NAME=kvs-demo-api
STAGE=prod
AUTHORIZER_NAME=CognitoAuthorizer
LAMBDA_DIR="$VMS_HOME/cloud/lambda"
IAM_DIR="$VMS_HOME/cloud/iam"
BASIC_EXECUTION=arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

AWS="$(command -v aws || echo "$HOME/.local/bin/aws")"
aws() { "$AWS" --region "$REGION" "$@"; }
say()  { printf '%s\n' "$*"; }
warn() { printf '!! %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

DRY=0 ARGS=()
for a in "$@"; do case "$a" in --dry-run) DRY=1 ;; *) ARGS+=("$a") ;; esac; done
set -- "${ARGS[@]+"${ARGS[@]}"}"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
CHANGED=0                                   # anything created: the stage needs a new deployment

preflight() {
  [ -x "$AWS" ] || die "no AWS CLI (bash setup/setup-user.sh installs it)"
  local acct; acct=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) ||
    die "the AWS CLI isn't logged in -- aws login --remote"
  [ "$acct" = "$ACCOUNT" ] || die "logged in to account $acct, not $ACCOUNT"
  API=$(aws apigateway get-rest-apis --query "items[?name=='$API_NAME'].id" --output text)
  [ -n "$API" ] && [ "$API" != None ] || die "no REST API named $API_NAME"
  AUTHORIZER=$(aws apigateway get-authorizers --rest-api-id "$API" --query "items[?name=='$AUTHORIZER_NAME'].id" --output text)
  [ -n "$AUTHORIZER" ] && [ "$AUTHORIZER" != None ] || die "no authorizer $AUTHORIZER_NAME on $API_NAME"
}
no_pir() { [[ "$1" != pir-* ]] || die "$1 is VMS's (pir-*): this repository never creates it"; }
# create: run "$@" unless --dry-run, after saying what it creates
create() { local what=$1; shift; if [ "$DRY" -eq 1 ]; then say "would create $what"; else say "creating $what"; "$@" >/dev/null; CHANGED=1; fi; }

ensure_table() {   # ensure_table NAME HASH_KEY
  no_pir "$1"
  if aws dynamodb describe-table --table-name "$1" >/dev/null 2>&1; then say "table $1: exists, left as is"; return; fi
  create "table $1 (key $2, on-demand)" aws dynamodb create-table --table-name "$1" \
    --attribute-definitions "AttributeName=$2,AttributeType=S" --key-schema "AttributeName=$2,KeyType=HASH" \
    --billing-mode PAY_PER_REQUEST
  [ "$DRY" -eq 1 ] || aws dynamodb wait table-exists --table-name "$1"
}

ensure_role() {    # ensure_role ROLE INLINE_POLICY_NAME POLICY_FILE
  local role=$1 pname=$2 file="$IAM_DIR/$3"
  [ -e "$file" ] || die "no $file"
  if aws iam get-role --role-name "$role" >/dev/null 2>&1; then
    say "role $role: exists, left as is"
    local live; live=$(aws iam get-role-policy --role-name "$role" --policy-name "$pname" --query PolicyDocument --output json 2>/dev/null) || {
      warn "role $role has no inline policy $pname -- look before adding it"; return; }
    python3 -c 'import json,sys; sys.exit(json.loads(sys.argv[1]) != json.load(open(sys.argv[2])))' "$live" "$file" ||
      warn "role $role: inline policy $pname differs from cloud/iam/$3 -- left as is; reconcile them (FoundAndFixed #41)"
    return
  fi
  create "role $role (trust: cloud/iam/lambda-trust.json)" aws iam create-role --role-name "$role" \
    --assume-role-policy-document "file://$IAM_DIR/lambda-trust.json"
  create "  its CloudWatch Logs access (AWSLambdaBasicExecutionRole)" aws iam attach-role-policy \
    --role-name "$role" --policy-arn "$BASIC_EXECUTION"
  create "  its inline policy $pname (cloud/iam/$3)" aws iam put-role-policy --role-name "$role" \
    --policy-name "$pname" --policy-document "file://$file"
}

ensure_function() {   # ensure_function NAME ROLE -- python3.13, arm64, 128 MB, 10 s, as the others
  local name=$1 role=$2 py; no_pir "$name"
  py="$LAMBDA_DIR/${name//-/_}.py"; [ -e "$py" ] || die "no $py"
  if aws lambda get-function-configuration --function-name "$name" >/dev/null 2>&1; then
    say "function $name: exists, left as is (bash cloud/deploy.sh lambda $name updates its code)"; return
  fi
  if [ "$DRY" -eq 1 ]; then say "would create function $name from $(basename "$py")"; return; fi
  (cd "$TMP" && cp "$py" . && zip -qX "$name.zip" "$(basename "$py")")
  say "creating function $name from $(basename "$py")"
  local i
  for i in 1 2 3 4 5 6 7 8; do       # a new role takes a few seconds before Lambda may assume it
    if aws lambda create-function --function-name "$name" --runtime python3.13 --architectures arm64 \
         --handler "${name//-/_}.lambda_handler" --role "arn:aws:iam::$ACCOUNT:role/$role" \
         --memory-size 128 --timeout 10 --zip-file "fileb://$TMP/$name.zip" >/dev/null 2>"$TMP/err"; then
      break
    fi
    grep -q "cannot be assumed" "$TMP/err" && [ "$i" -lt 8 ] || die "create-function: $(cat "$TMP/err")"
    sleep 5
  done
  aws lambda wait function-active-v2 --function-name "$name"
  CHANGED=1
}

ensure_inline_policy() {   # ensure_inline_policy ROLE NAME FILE -- adds a policy, never changes one
  local role=$1 pname=$2 file="$IAM_DIR/$3" live
  [ -e "$file" ] || die "no $file"
  aws iam get-role --role-name "$role" >/dev/null 2>&1 || die "no role $role"
  if live=$(aws iam get-role-policy --role-name "$role" --policy-name "$pname" --query PolicyDocument --output json 2>/dev/null); then
    if python3 -c 'import json,sys; sys.exit(json.loads(sys.argv[1]) != json.load(open(sys.argv[2])))' "$live" "$file"; then
      say "policy $pname on $role: exists, as cloud/iam/$3"
    else
      warn "policy $pname on $role differs from cloud/iam/$3 -- left as is; reconcile them (FoundAndFixed #41)"
    fi
    return
  fi
  create "policy $pname on $role (cloud/iam/$3)" aws iam put-role-policy --role-name "$role" \
    --policy-name "$pname" --policy-document "file://$file"
}

ensure_env() {   # ensure_env FUNCTION KEY VALUE -- adds one environment variable, never changes one
  local fn=$1 key=$2 value=$3 vars current merged
  vars=$(aws lambda get-function-configuration --function-name "$fn" --query 'Environment.Variables' --output json)
  current=$(python3 -c 'import json,sys; print((json.loads(sys.argv[1]) or {}).get(sys.argv[2], ""))' "$vars" "$key")
  if [ "$current" = "$value" ]; then say "$fn: $key=$value already"; return; fi
  if [ -n "$current" ]; then warn "$fn: $key is '$current', not '$value' -- left as is"; return; fi
  merged=$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]) or {}; d[sys.argv[2]]=sys.argv[3]; print(json.dumps({"Variables": d}))' "$vars" "$key" "$value")
  if [ "$DRY" -eq 1 ]; then say "would set $fn's $key=$value"; return; fi
  say "setting $fn's $key=$value"
  aws lambda update-function-configuration --function-name "$fn" --environment "$merged" >/dev/null
  aws lambda wait function-updated --function-name "$fn"
}

resource_id() {    # resource_id /me/settings -> its id, or empty
  aws apigateway get-resources --rest-api-id "$API" --limit 500 --query "items[?path=='$1'].id" --output text | sed 's/None//'
}

ensure_resource() {   # ensure_resource /me/settings -- each missing level is created; prints the id
  local path=$1 parent part id built=""
  parent=$(resource_id /)
  for part in $(tr '/' ' ' <<<"$path"); do
    built="$built/$part"
    id=$(resource_id "$built")
    if [ -z "$id" ]; then
      if [ "$DRY" -eq 1 ]; then
        say "would create resource $built$([ "$built" = "$path" ] || echo " and under it, up to $path")" >&2
        echo "(new)"; return
      fi
      say "creating resource $built" >&2
      id=$(aws apigateway create-resource --rest-api-id "$API" --parent-id "$parent" --path-part "$part" --query id --output text)
      CHANGED=1
    fi
    parent=$id
  done
  echo "$parent"
}

ensure_method() {     # ensure_method RESOURCE_ID PATH METHOD FUNCTION -- Cognito-authorized proxy
  local rid=$1 path=$2 method=$3 fn=$4
  local uri="arn:aws:apigateway:$REGION:lambda:path/2015-03-31/functions/arn:aws:lambda:$REGION:$ACCOUNT:function:$fn/invocations"
  if [ "$rid" != "(new)" ] && aws apigateway get-method --rest-api-id "$API" --resource-id "$rid" --http-method "$method" >/dev/null 2>&1; then
    say "route $method $path: exists, left as is"; return
  fi
  if [ "$DRY" -eq 1 ]; then say "would create route $method $path -> $fn (Cognito authorizer)"; return; fi
  create "route $method $path -> $fn (Cognito authorizer)" aws apigateway put-method --rest-api-id "$API" \
    --resource-id "$rid" --http-method "$method" --authorization-type COGNITO_USER_POOLS --authorizer-id "$AUTHORIZER"
  aws apigateway put-integration --rest-api-id "$API" --resource-id "$rid" --http-method "$method" \
    --type AWS_PROXY --integration-http-method POST --uri "$uri" >/dev/null
  # Only this API, this method, this path may invoke the function -- as for every other route.
  aws lambda add-permission --function-name "$fn" --statement-id "apigw-$method-${path//\//-}" \
    --action lambda:InvokeFunction --principal apigateway.amazonaws.com \
    --source-arn "arn:aws:execute-api:$REGION:$ACCOUNT:$API/*/$method$path" >/dev/null
}

ensure_cors() {       # ensure_cors RESOURCE_ID PATH "GET,PUT,OPTIONS" -- the mock OPTIONS every route has
  local rid=$1 path=$2 methods=$3
  if [ "$rid" != "(new)" ] && aws apigateway get-method --rest-api-id "$API" --resource-id "$rid" --http-method OPTIONS >/dev/null 2>&1; then
    say "CORS preflight for $path: exists, left as is"; return
  fi
  if [ "$DRY" -eq 1 ]; then say "would create CORS preflight OPTIONS $path ($methods)"; return; fi
  local h=method.response.header
  create "CORS preflight OPTIONS $path ($methods)" aws apigateway put-method --rest-api-id "$API" \
    --resource-id "$rid" --http-method OPTIONS --authorization-type NONE
  aws apigateway put-method-response --rest-api-id "$API" --resource-id "$rid" --http-method OPTIONS \
    --status-code 200 --response-parameters "$h.Access-Control-Allow-Headers=true,$h.Access-Control-Allow-Methods=true,$h.Access-Control-Allow-Origin=true" >/dev/null
  aws apigateway put-integration --rest-api-id "$API" --resource-id "$rid" --http-method OPTIONS \
    --type MOCK --request-templates '{"application/json": "{\"statusCode\": 200}"}' >/dev/null
  aws apigateway put-integration-response --rest-api-id "$API" --resource-id "$rid" --http-method OPTIONS \
    --status-code 200 --response-parameters "{\"$h.Access-Control-Allow-Headers\": \"'Content-Type,Authorization'\", \"$h.Access-Control-Allow-Methods\": \"'$methods'\", \"$h.Access-Control-Allow-Origin\": \"'*'\"}" >/dev/null
}

deploy_stage() {
  if [ "$DRY" -eq 1 ] || [ "$CHANGED" -eq 0 ]; then return; fi
  say "deploying stage $STAGE of $API_NAME"
  aws apigateway create-deployment --rest-api-id "$API" --stage-name "$STAGE" --description "provision.sh $1" >/dev/null
}

# The route answers through API Gateway: the preflight with CORS, a call without a token is turned
# away by the authorizer (an unknown route would say "Missing Authentication Token" instead), and
# test-invoke -- which skips the authorizer and sends no body -- reaches the function, whose
# answer to that empty call contains PHRASE.
verify_route() {      # verify_route PATH PHRASE METHOD...
  local path=$1 phrase=$2; shift 2
  local url="https://$API.execute-api.$REGION.amazonaws.com/$STAGE$path" m rid out i
  for i in 1 2 3 4 5 6; do
    curl -s -o /dev/null -D "$TMP/h" -X OPTIONS "$url" -H "Origin: https://example.org" \
      -H "Access-Control-Request-Method: ${1}" && grep -qi '^access-control-allow-origin: \*' "$TMP/h" && break
    sleep 5                        # a new deployment takes a few seconds to reach the edge
  done
  grep -qi '^access-control-allow-origin: \*' "$TMP/h" || die "OPTIONS $path: no CORS answer"
  for m in "$@"; do
    [ "$(curl -s -o /dev/null -w '%{http_code}' -X "$m" "$url")" = 401 ] || die "$m $path without a token: expected 401 from the authorizer"
  done
  rid=$(resource_id "$path")
  out=$(aws apigateway test-invoke-method --rest-api-id "$API" --resource-id "$rid" --http-method "$1" --query '[status, body]' --output text)
  grep -q "$phrase" <<<"$out" || die "test-invoke $1 $path didn't reach the function: $out"
  say "verified: $path answers (CORS preflight, authorizer, function)"
}

provision_user_settings() {
  ensure_table user-settings userId
  ensure_role UserSettingsLambdaRole user-settings-rw user-settings-policy.json
  ensure_function user-settings UserSettingsLambdaRole
  local rid; rid=$(ensure_resource /me/settings)
  ensure_method "$rid" /me/settings GET user-settings
  ensure_method "$rid" /me/settings PUT user-settings
  ensure_cors "$rid" /me/settings "GET,PUT,OPTIONS"
  deploy_stage user-settings
  if [ "$DRY" -eq 1 ]; then say "dry run: nothing was changed"; return; fi
  verify_route /me/settings "no signed-in user" GET PUT
}

provision_camera_settings() {
  ensure_role SetCameraSettingsLambdaRole cameras-rw set-camera-settings-policy.json
  ensure_function set-camera-settings SetCameraSettingsLambdaRole
  local rid; rid=$(ensure_resource /cameras/settings)
  ensure_method "$rid" /cameras/settings POST set-camera-settings
  ensure_cors "$rid" /cameras/settings "POST,OPTIONS"
  deploy_stage camera-settings
  if [ "$DRY" -eq 1 ]; then say "dry run: nothing was changed"; return; fi
  verify_route /cameras/settings "give cameraId" POST
}

provision_thumbnails() {
  # The evidence bucket, as the clip Lambdas know it -- not spelled out here a second time.
  local bucket; bucket=$(aws lambda get-function-configuration --function-name record-clip \
    --query 'Environment.Variables.BUCKET' --output text)
  [ -n "$bucket" ] && [ "$bucket" != None ] || die "record-clip has no BUCKET: can't tell the evidence bucket"
  grep -q "arn:aws:s3:::$bucket/thumbs/" "$IAM_DIR/thumbnails-policy.json" "$IAM_DIR/list-cameras-thumbnails-policy.json" ||
    die "the policies in cloud/iam/ don't name $bucket"
  ensure_inline_policy KVSAdapterRole Thumbnails thumbnails-policy.json
  ensure_inline_policy ListCamerasLambdaRole thumbnails-read list-cameras-thumbnails-policy.json
  ensure_env list-cameras BUCKET "$bucket"
  if [ "$DRY" -eq 1 ]; then say "dry run: nothing was changed"; return; fi
  aws iam get-role-policy --role-name KVSAdapterRole --policy-name Thumbnails >/dev/null &&
    aws iam get-role-policy --role-name ListCamerasLambdaRole --policy-name thumbnails-read >/dev/null &&
    [ "$(aws lambda get-function-configuration --function-name list-cameras --query 'Environment.Variables.BUCKET' --output text)" = "$bucket" ] ||
    die "thumbnails: a policy or list-cameras' BUCKET isn't there afterwards"
  say "verified: both policies attached, list-cameras knows the bucket"
}

case "${1:-}" in
  user-settings)   preflight; provision_user_settings ;;
  camera-settings) preflight; provision_camera_settings ;;
  thumbnails)      preflight; provision_thumbnails ;;
  *) sed -n '2,11p' "$0"; exit 1 ;;
esac
