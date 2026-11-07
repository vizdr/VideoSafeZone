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
#   bash cloud/provision.sh pg-layer                  the vsz-pg Lambda layer (pg8000 + RDS CA bundle,
#                                                     cloud/layers/vsz-pg), published when this tree's
#                                                     build differs from the newest version
#   bash cloud/provision.sh directory-db              the directory database (DEALER-PANEL-PLAN.md
#                                                     Phase 1): pg-layer, security group vsz-db-client,
#                                                     role and function vsz-db-admin in the VPC; then the
#                                                     database's bootstrap (once), migrations, status
#   bash cloud/provision.sh identity                  sign-in (DEALER-PANEL-PLAN.md Phase 2): function
#                                                     vsz-directory in the VPC, user pool vsz-users with
#                                                     it as the pre-token trigger, app client vsz-web
#   bash cloud/provision.sh directory-seed EMAIL      the system administrator (EMAIL, invited by Cognito)
#                                                     and the demo directory and accounts; then signs in
#                                                     as each demo account and shows its claims
#   bash cloud/provision.sh directory                 the directory API (DEALER-PANEL-PLAN.md Phase 4):
#                                                     function vsz-directory-api, /directory/{proxy+} (ANY,
#                                                     Cognito authorizer) and its CORS preflight, stage
#   bash cloud/provision.sh portal                    the dealer portal's camera inventory (DEALER-PANEL-PLAN.md
#                                                     Phase 5): vsz-directory-api reads the cameras table and
#                                                     the thumbnails' times; checked as the demo dealer
#   bash cloud/provision.sh adapters                  adapters' activation (DEALER-PANEL-PLAN.md Phase 6): the
#                                                     Cloud ID key, the API's config publish and camera release,
#                                                     the IoT rule for last seen, every adapter's config sent
#   bash cloud/provision.sh cameras                   adding cameras from the web page (DEALER-PANEL-PLAN.md
#                                                     Phase 7): tables adapter-discovery and camera-requests, the
#                                                     IoT rules filling them, Lambda adapter-cameras on /adapters
#   bash cloud/provision.sh tenancy                   the tenant check's rollout, step 1 (DEALER-PANEL-PLAN.md
#                                                     3.3): camera-row read for the clip Lambdas, TENANCY=report
#                                                     on every function with the check, vsz-users beside
#                                                     kvs-demo-users on kvs-demo-api's authorizer
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
#   - One exception to "nothing that exists is modified", said aloud when it happens: directory-db
#     adds one inbound rule to the database's existing security group rds-lambda-2 (tcp 5432 from
#     vsz-db-client). Adding a rule removes nothing; the other project's rules stay as they are.
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
VPC_EXECUTION=arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole
DB_INSTANCE=database-coursera-iot            # shared with another project (DEALER-PANEL-PLAN.md §2)
DB_NAME=videosafezone
LAYER_DIR="$VMS_HOME/cloud/layers/vsz-pg"
COGNITO_DIR="$VMS_HOME/cloud/cognito"
POOL_NAME=vsz-users
CLIENT_NAME=vsz-web
DEMO_FILE="${XDG_STATE_HOME:-$HOME/.local/state}/videosafezone/demo-accounts.txt"

AWS="$(command -v aws || echo "$HOME/.local/bin/aws")"
aws() { "$AWS" --region "$REGION" "$@"; }
say()  { printf '%s\n' "$*"; }
warn() { printf '!! %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

DRY=0 ARGS=()
for a in "$@"; do case "$a" in --dry-run) DRY=1 ;; *) ARGS+=("$a") ;; esac; done
set -- "${ARGS[@]+"${ARGS[@]}"}"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
# shellcheck source=cloud/package.sh
. "$VMS_HOME/cloud/package.sh"
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

ensure_role() {    # ensure_role ROLE INLINE_POLICY_NAME POLICY_FILE [MANAGED_POLICY_ARN]
  local role=$1 pname=$2 file="$IAM_DIR/$3" managed=${4:-$BASIC_EXECUTION}
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
  create "  its CloudWatch Logs access (${managed##*/})" aws iam attach-role-policy \
    --role-name "$role" --policy-arn "$managed"
  create "  its inline policy $pname (cloud/iam/$3)" aws iam put-role-policy --role-name "$role" \
    --policy-name "$pname" --policy-document "file://$file"
}

ensure_function() {   # ensure_function NAME ROLE [MEMORY TIMEOUT] -- python3.13, arm64, 128 MB, 10 s by default
  local name=$1 role=$2 memory=${3:-128} timeout=${4:-10} py; no_pir "$name"
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
         --memory-size "$memory" --timeout "$timeout" --zip-file "fileb://$TMP/$name.zip" >/dev/null 2>"$TMP/err"; then
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
  # ANY on a {proxy+} resource: API Gateway invokes with the real method and path, so the
  # permission names any method and any path under the resource's parent.
  local arn_method=$method arn_path=$path
  [ "$method" != ANY ] || arn_method='*'
  arn_path=${arn_path//\{proxy+\}/*}
  aws lambda add-permission --function-name "$fn" --statement-id "apigw-$method-${path//[^A-Za-z0-9]/-}" \
    --action lambda:InvokeFunction --principal apigateway.amazonaws.com \
    --source-arn "arn:aws:execute-api:$REGION:$ACCOUNT:$API/*/$arn_method$arn_path" >/dev/null
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

# --- the directory database (DEALER-PANEL-PLAN.md Phase 1, D2, D3) ---------------------------------

# This tree's vsz-pg layer as $TMP/vsz-pg.zip, the same bytes on every build of the same files (so
# "already published" can be told by its hash); sets LAYER_SHA as Lambda reports it (base64 sha256).
build_pg_layer() {
  local dir="$TMP/layer" py url sum
  py="$VMS_HOME/venv-adapter/bin/python"; [ -x "$py" ] || py=python3
  mkdir -p "$dir/python" "$dir/rds-ca"
  (cd "$TMP" && "$py" -I -m pip install --quiet --disable-pip-version-check --no-deps --only-binary=:all: \
     --require-hashes --no-compile --target "$dir/python" -r "$LAYER_DIR/requirements.txt") ||
    die "pip couldn't fetch the layer's packages with their pinned hashes"
  url=$(awk '$1 == "url" {print $2}' "$LAYER_DIR/rds-ca.txt")
  sum=$(awk '$1 == "sha256" {print $2}' "$LAYER_DIR/rds-ca.txt")
  curl -fsS -o "$dir/rds-ca/bundle.pem" "$url" || die "couldn't download the RDS CA bundle: $url"
  [ "$(sha256sum < "$dir/rds-ca/bundle.pem" | cut -d' ' -f1)" = "$sum" ] ||
    die "the RDS CA bundle at $url isn't the one in cloud/layers/vsz-pg/rds-ca.txt -- look at its roots, then update the sha256 there"
  python3 - "$dir" "$TMP/vsz-pg.zip" <<'PY'
import os, sys, zipfile
root, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, "w") as z:          # sorted, fixed dates and modes: same files, same bytes
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            path = os.path.join(base, name)
            info = zipfile.ZipInfo(os.path.relpath(path, root), date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr, info.compress_type = 0o644 << 16, zipfile.ZIP_DEFLATED
            with open(path, "rb") as f:
                z.writestr(info, f.read())
PY
  LAYER_SHA=$(openssl dgst -sha256 -binary "$TMP/vsz-pg.zip" | base64)
}

ensure_pg_layer() {   # sets LAYER_ARN: the newest vsz-pg version, published first if this build differs
  local latest live
  build_pg_layer
  latest=$(aws lambda list-layer-versions --layer-name vsz-pg --query 'LayerVersions[0].LayerVersionArn' --output text 2>/dev/null | sed 's/^None$//')
  if [ -n "$latest" ]; then
    live=$(aws lambda get-layer-version-by-arn --arn "$latest" --query Content.CodeSha256 --output text)
    if [ "$live" = "$LAYER_SHA" ]; then say "layer vsz-pg: $latest is this tree's build"; LAYER_ARN=$latest; return; fi
    say "layer vsz-pg: $latest differs from this tree's build"
  fi
  if [ "$DRY" -eq 1 ]; then say "would publish layer vsz-pg (pg8000 and its packages, the RDS CA bundle; sha256 $LAYER_SHA)"; LAYER_ARN="(new)"; return; fi
  LAYER_ARN=$(aws lambda publish-layer-version --layer-name vsz-pg \
    --description "pg8000 + RDS CA bundle eu-central-1 (cloud/layers/vsz-pg)" \
    --zip-file "fileb://$TMP/vsz-pg.zip" --compatible-runtimes python3.13 --compatible-architectures arm64 \
    --query LayerVersionArn --output text)
  say "published layer $LAYER_ARN"
}

ensure_sg() {         # ensure_sg NAME VPC DESCRIPTION -- prints its id (messages go to stderr)
  local id
  id=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$1" "Name=vpc-id,Values=$2" \
    --query 'SecurityGroups[0].GroupId' --output text | sed 's/^None$//')
  if [ -n "$id" ]; then say "security group $1: exists ($id), left as is" >&2; echo "$id"; return; fi
  if [ "$DRY" -eq 1 ]; then say "would create security group $1 in $2 (no inbound rules)" >&2; echo "(new)"; return; fi
  id=$(aws ec2 create-security-group --group-name "$1" --vpc-id "$2" --description "$3" --query GroupId --output text)
  say "created security group $1 ($id): no inbound rules" >&2
  echo "$id"
}

ensure_db_ingress() { # ensure_db_ingress DB_SG CLIENT_SG -- the one rule added to an existing group
  local has
  if [ "$2" != "(new)" ]; then
    has=$(aws ec2 describe-security-groups --group-ids "$1" \
      --query 'SecurityGroups[0].IpPermissions[?FromPort==`5432`].UserIdGroupPairs[].GroupId' --output text)
    if grep -qw -- "$2" <<<"$has"; then say "rds-lambda-2 ($1): tcp 5432 from vsz-db-client is there"; return; fi
  fi
  if [ "$DRY" -eq 1 ]; then
    say "would ADD to rds-lambda-2 ($1), the database's existing group, one inbound rule: tcp 5432 from vsz-db-client (nothing else changes)"
    return
  fi
  aws ec2 authorize-security-group-ingress --group-id "$1" --ip-permissions \
    "IpProtocol=tcp,FromPort=5432,ToPort=5432,UserIdGroupPairs=[{GroupId=$2,Description=VideoSafeZone directory functions}]" >/dev/null
  say "added to rds-lambda-2 ($1): tcp 5432 from vsz-db-client ($2)"
}

ensure_vpc_function() {   # NAME ROLE MEMORY TIMEOUT -- in the VPC, with the layer (db_network sets the rest)
  local name=$1 role=$2 memory=$3 timeout=$4 conf i
  if conf=$(aws lambda get-function-configuration --function-name "$name" --output json 2>/dev/null); then
    say "function $name: exists, left as is (bash cloud/deploy.sh lambda $name updates its code)"
    python3 - "$conf" "$LAYER_ARN" "$DB_SG" <<'PY'
import json, sys
conf, layer, sg = json.loads(sys.argv[1]), sys.argv[2], sys.argv[3]
name = conf["FunctionName"]
layers = [l["Arn"] for l in conf.get("Layers", [])]
if layer != "(new)" and layer not in layers:
    print(f"!! {name} uses {layers or 'no layer'}, not {layer} -- to switch: aws lambda "
          f"update-function-configuration --function-name {name} --layers {layer}", file=sys.stderr)
if sg != "(new)" and sg not in conf.get("VpcConfig", {}).get("SecurityGroupIds", []):
    print(f"!! {name} isn't in the security group {sg}", file=sys.stderr)
PY
    return
  fi
  if [ "$DRY" -eq 1 ]; then
    say "would create function $name: in the VPC (vsz-db-client; subnets $DB_SUBNETS), layer vsz-pg, $memory MB, $timeout s"
    return
  fi
  package_lambda "$name" "$TMP/$name.zip"
  say "creating function $name in the VPC (a minute or two: Lambda attaches it to the subnets)"
  for i in $(seq 1 12); do      # a new role takes a few seconds before Lambda may assume it and use EC2
    if aws lambda create-function --function-name "$name" --runtime python3.13 --architectures arm64 \
         --handler "${name//-/_}.lambda_handler" --role "arn:aws:iam::$ACCOUNT:role/$role" \
         --memory-size "$memory" --timeout "$timeout" --zip-file "fileb://$TMP/$name.zip" --layers "$LAYER_ARN" \
         --vpc-config "SubnetIds=$DB_SUBNETS,SecurityGroupIds=$DB_SG" \
         --environment "Variables={DB_HOST=$DB_HOST,DB_PORT=$DB_PORT,DB_NAME=$DB_NAME}" >/dev/null 2>"$TMP/err"; then
      break
    fi
    grep -qE "cannot be assumed|does not have permissions|CreateNetworkInterface" "$TMP/err" && [ "$i" -lt 12 ] ||
      die "create-function: $(cat "$TMP/err")"
    sleep 5
  done
  aws lambda wait function-active-v2 --function-name "$name"
  # No reserved concurrency: the account's total is 10 and AWS keeps all 10 unreserved
  # (DEALER-PANEL-PLAN.md D3, §9).
}

# The database's facts and the network every directory function shares: sets DB_HOST, DB_PORT,
# DB_VPC, DB_SUBNETS, DB_RDS_SG; then the layer (LAYER_ARN), vsz-db-client (DB_SG) and its rule.
db_network() {
  local facts dbi iam dbsgs
  facts=$(aws rds describe-db-instances --db-instance-identifier "$DB_INSTANCE" --query \
    'DBInstances[0].[Endpoint.Address, Endpoint.Port, DBSubnetGroup.VpcId, DbiResourceId, IAMDatabaseAuthenticationEnabled]' \
    --output text) || die "no RDS instance $DB_INSTANCE"
  read -r DB_HOST DB_PORT DB_VPC dbi iam <<<"$facts"
  DB_SUBNETS=$(aws rds describe-db-instances --db-instance-identifier "$DB_INSTANCE" \
    --query 'DBInstances[0].DBSubnetGroup.Subnets[].SubnetIdentifier' --output text | tr '\t' ',')
  dbsgs=$(aws rds describe-db-instances --db-instance-identifier "$DB_INSTANCE" \
    --query 'DBInstances[0].VpcSecurityGroups[].VpcSecurityGroupId' --output text)
  [ "$iam" = True ] || die "IAM database authentication is off on $DB_INSTANCE -- DEALER-PANEL-PLAN.md Phase 0.3 first"
  local f
  for f in vsz-db-admin-policy.json vsz-directory-policy.json; do
    grep -q "dbuser:$dbi/" "$IAM_DIR/$f" || die "cloud/iam/$f doesn't name $dbi, this instance's resource id"
  done
  DB_RDS_SG=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=rds-lambda-2" "Name=vpc-id,Values=$DB_VPC" \
    --query 'SecurityGroups[0].GroupId' --output text)
  grep -qw -- "$DB_RDS_SG" <<<"$dbsgs" || die "the security group rds-lambda-2 ($DB_RDS_SG) isn't attached to $DB_INSTANCE"
  say "database: $DB_INSTANCE ($DB_HOST:$DB_PORT) in $DB_VPC; its group for Lambdas: rds-lambda-2 ($DB_RDS_SG)"
  ensure_pg_layer
  DB_SG=$(ensure_sg vsz-db-client "$DB_VPC" "VideoSafeZone functions that reach the directory database")
  ensure_db_ingress "$DB_RDS_SG" "$DB_SG"
}

# Status first; only a database that isn't set up yet gets the bootstrap -- and only then is the
# master password read, from Secrets Manager here on the operator's machine (the function can't).
db_bootstrap_if_needed() {
  local secret
  printf '{"action": "status"}\n' > "$TMP/status-event.json"
  if invoke_db_admin "$TMP/status-event.json" > "$TMP/status-answer" 2>&1; then
    say "database: set up already -- bootstrap skipped"; return
  fi
  grep -q '"bootstrapped": false' "$TMP/status-answer" ||
    { cat "$TMP/status-answer"; die "vsz-db-admin can't use the database -- see above"; }
  secret=$(aws secretsmanager list-secrets \
    --query "SecretList[?starts_with(Name, 'rds-db-credentials/$DB_INSTANCE/')].Name" --output text)
  [ -n "$secret" ] && [ "$(wc -w <<<"$secret")" -eq 1 ] ||
    die "expected exactly one secret rds-db-credentials/$DB_INSTANCE/..., found: ${secret:-none}"
  say "bootstrap: the master user's credentials from $secret go to vsz-db-admin once, in a 0600 file"
  ( umask 077
    aws secretsmanager get-secret-value --secret-id "$secret" --query SecretString --output text > "$TMP/master.json"
    python3 -c 'import json, sys
s = json.load(open(sys.argv[1]))
json.dump({"action": "bootstrap", "masterUser": s["username"], "masterPassword": s["password"]}, open(sys.argv[2], "w"))' \
      "$TMP/master.json" "$TMP/bootstrap-event.json" )
  rm -f -- "$TMP/master.json"
  if ! invoke_db_admin "$TMP/bootstrap-event.json"; then
    rm -f -- "$TMP/bootstrap-event.json"
    die "the bootstrap failed -- see above (it never changes the master user)"
  fi
  rm -f -- "$TMP/bootstrap-event.json"
}

provision_directory_db() {
  db_network
  ensure_role vsz-db-admin-role vsz-db-admin vsz-db-admin-policy.json "$VPC_EXECUTION"
  ensure_vpc_function vsz-db-admin vsz-db-admin-role 256 60
  if [ "$DRY" -eq 1 ]; then
    say "would then: set the database up if it isn't (the master password from Secrets Manager, sent once), apply the migrations, show the status"
    say "dry run: nothing was changed"; return
  fi
  db_bootstrap_if_needed
  printf '{"action": "migrate"}\n' > "$TMP/migrate-event.json"
  invoke_db_admin "$TMP/migrate-event.json" || die "the migrations failed -- see above"
  printf '{"action": "status"}\n' > "$TMP/status-event.json"
  invoke_db_admin "$TMP/status-event.json" || die "status failed -- see above"
  say "verified: vsz-db-admin reaches the database over verified TLS as vsz_migrator with an IAM token, and the migrations are applied"
}

# --- identity: the user pool, its client and its trigger (DEALER-PANEL-PLAN.md Phase 2, D1, D4) ------

find_pool() {         # sets POOL_ID ("" when there is none) and POOL_ARN
  POOL_ID=$(aws cognito-idp list-user-pools --max-results 60 --query "UserPools[?Name=='$POOL_NAME'].Id" \
    --output text | sed 's/^None$//')
  [ "$(wc -w <<<"$POOL_ID")" -le 1 ] || die "several user pools named $POOL_NAME: $POOL_ID -- won't guess"
  POOL_ARN="arn:aws:cognito-idp:$REGION:$ACCOUNT:userpool/$POOL_ID"
}

ensure_user_pool() {  # from cloud/cognito/vsz-users-pool.json; an existing pool is left as it is
  local want live
  want=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["LambdaConfig"]["PreTokenGenerationConfig"]["LambdaArn"])' \
    "$COGNITO_DIR/vsz-users-pool.json")
  [ "$want" = "arn:aws:lambda:$REGION:$ACCOUNT:function:vsz-directory" ] ||
    die "cloud/cognito/vsz-users-pool.json names another trigger: $want"
  find_pool
  if [ -n "$POOL_ID" ]; then
    say "user pool $POOL_NAME: exists ($POOL_ID), left as is"
    live=$(aws cognito-idp describe-user-pool --user-pool-id "$POOL_ID" \
      --query 'UserPool.LambdaConfig.PreTokenGenerationConfig.LambdaArn' --output text)
    [ "$live" = "$want" ] || warn "$POOL_NAME's pre-token trigger is $live, not vsz-directory"
    return
  fi
  if [ "$DRY" -eq 1 ]; then
    say "would create user pool $POOL_NAME (cloud/cognito/vsz-users-pool.json): sign-in with the email address, accounts by invitation only, trigger vsz-directory, deletion protection"
    POOL_ID="(new)"; return
  fi
  POOL_ID=$(aws cognito-idp create-user-pool --cli-input-json "file://$COGNITO_DIR/vsz-users-pool.json" \
    --query UserPool.Id --output text)
  POOL_ARN="arn:aws:cognito-idp:$REGION:$ACCOUNT:userpool/$POOL_ID"
  say "created user pool $POOL_NAME ($POOL_ID)"
}

ensure_trigger_permission() {   # Cognito may invoke vsz-directory -- for this pool only
  local policy
  policy=$(aws lambda get-policy --function-name vsz-directory --query Policy --output text 2>/dev/null || true)
  if [ "$POOL_ID" != "(new)" ] && grep -qF "$POOL_ARN" <<<"$policy"; then
    say "vsz-directory: Cognito may invoke it for $POOL_NAME"; return
  fi
  if [ "$DRY" -eq 1 ]; then say "would let Cognito invoke vsz-directory, for $POOL_NAME only"; return; fi
  aws lambda add-permission --function-name vsz-directory --statement-id "cognito-$POOL_NAME" \
    --action lambda:InvokeFunction --principal cognito-idp.amazonaws.com \
    --source-arn "$POOL_ARN" --source-account "$ACCOUNT" >/dev/null
  say "let Cognito invoke vsz-directory, for $POOL_NAME ($POOL_ID) only"
}

ensure_pool_client() {          # sets CLIENT_ID; from cloud/cognito/vsz-web-client.json
  CLIENT_ID=""
  if [ "$POOL_ID" != "(new)" ]; then
    CLIENT_ID=$(aws cognito-idp list-user-pool-clients --user-pool-id "$POOL_ID" \
      --query "UserPoolClients[?ClientName=='$CLIENT_NAME'].ClientId" --output text | sed 's/^None$//')
  fi
  if [ -n "$CLIENT_ID" ]; then say "app client $CLIENT_NAME: exists ($CLIENT_ID), left as is"; return; fi
  if [ "$DRY" -eq 1 ]; then
    say "would create app client $CLIENT_NAME (cloud/cognito/vsz-web-client.json): no secret, 5-minute tokens"; return
  fi
  python3 -c 'import json, sys
d = json.load(open(sys.argv[1])); d["UserPoolId"] = sys.argv[2]; json.dump(d, open(sys.argv[3], "w"))' \
    "$COGNITO_DIR/vsz-web-client.json" "$POOL_ID" "$TMP/client.json"
  CLIENT_ID=$(aws cognito-idp create-user-pool-client --cli-input-json "file://$TMP/client.json" \
    --query UserPoolClient.ClientId --output text)
  say "created app client $CLIENT_NAME ($CLIENT_ID)"
}

provision_identity() {
  db_network
  ensure_role vsz-directory-role vsz-directory vsz-directory-policy.json "$VPC_EXECUTION"
  # Cognito waits 5 s for its trigger, cold start included. At 256 MB (a seventh of a vCPU) the
  # first one took 4.26 s; memory buys CPU, so 1024 MB (FoundAndFixed.md #89).
  ensure_vpc_function vsz-directory vsz-directory-role 1024 5
  ensure_user_pool
  ensure_trigger_permission
  ensure_pool_client
  if [ "$DRY" -eq 1 ]; then say "would then: ping the database through vsz-directory"; say "dry run: nothing was changed"; return; fi
  printf '{"op": "ping", "actor": {"kind": "operator"}}\n' > "$TMP/ping-event.json"
  invoke_lambda vsz-directory "$TMP/ping-event.json" || die "vsz-directory can't use the database -- see above"
  say "user pool $POOL_NAME: $POOL_ID -- app client $CLIENT_NAME: $CLIENT_ID (client-app/src/config.ts takes them at Phase 3's switch)"
  say "verified: vsz-directory reaches the database as vsz_app over verified TLS with an IAM token; Cognito may call it as $POOL_NAME's trigger"
}

# --- the demo directory (Phase 2.3) ---------------------------------------------------------------
# The administrator is a real person: Cognito emails a temporary password. The demo accounts are
# example.com addresses that receive nothing; each gets a random password, kept in DEMO_FILE
# (0600) for sign-in-check.sh. demo-disabled and demo-suspended exist to be refused.
DEMO_ACCOUNTS="demo-owner demo-dealer demo-holder demo-superadmin demo-user demo-disabled demo-suspended"

ensure_cognito_user() {         # ensure_cognito_user EMAIL invite|demo -- prints the user's sub
  local email=$1 mode=$2 sub
  sub=$(aws cognito-idp admin-get-user --user-pool-id "$POOL_ID" --username "$email" \
    --query "UserAttributes[?Name=='sub'].Value" --output text 2>/dev/null | sed 's/^None$//') || sub=""
  if [ -n "$sub" ]; then say "  $email: in the pool already" >&2; echo "$sub"; return; fi
  if [ "$mode" = invite ]; then
    sub=$(aws cognito-idp admin-create-user --user-pool-id "$POOL_ID" --username "$email" \
      --user-attributes "Name=email,Value=$email" Name=email_verified,Value=true --desired-delivery-mediums EMAIL \
      --query "User.Attributes[?Name=='sub'].Value" --output text)
    say "  $email: created -- Cognito emails a temporary password" >&2
  else
    sub=$(aws cognito-idp admin-create-user --user-pool-id "$POOL_ID" --username "$email" \
      --user-attributes "Name=email,Value=$email" Name=email_verified,Value=true --message-action SUPPRESS \
      --query "User.Attributes[?Name=='sub'].Value" --output text)
    ( umask 077
      python3 - "$POOL_ID" "$email" "$TMP/set-password.json" "$DEMO_FILE" <<'PY'
import json, secrets, string, sys
pool, email, request, demo = sys.argv[1:5]
chars = string.ascii_letters + string.digits
pw = [secrets.choice(string.ascii_uppercase), secrets.choice(string.ascii_lowercase), secrets.choice(string.digits)]
pw += [secrets.choice(chars) for _ in range(13)]
secrets.SystemRandom().shuffle(pw)
pw = "".join(pw)
json.dump({"UserPoolId": pool, "Username": email, "Password": pw, "Permanent": True}, open(request, "w"))
lines = [l for l in open(demo).read().splitlines() if l and not l.startswith(email + " ")]
open(demo, "w").write("\n".join(lines + [f"{email} {pw}"]) + "\n")
PY
    )
    aws cognito-idp admin-set-user-password --cli-input-json "file://$TMP/set-password.json"
    rm -f -- "$TMP/set-password.json"
    say "  $email: created, its password in $DEMO_FILE" >&2
  fi
  echo "$sub"
}

provision_directory_seed() {
  local admin=${1:-} name subs="{}" sub
  [[ "$admin" == ?*@?*.?* ]] ||
    die "usage: bash cloud/provision.sh directory-seed YOUR_EMAIL -- the system administrator; Cognito emails it a temporary password"
  find_pool
  [ -n "$POOL_ID" ] || die "no user pool $POOL_NAME: bash cloud/provision.sh identity first"
  aws lambda get-function-configuration --function-name vsz-directory >/dev/null 2>&1 ||
    die "no function vsz-directory: bash cloud/provision.sh identity first"
  if [ "$DRY" -eq 1 ]; then
    say "would create in $POOL_NAME: $admin (system administrator; Cognito emails a temporary password)"
    say "would create the demo accounts, $(sed 's/\([a-z-]*\)/\1@example.com/g' <<<"$DEMO_ACCOUNTS"), passwords in $DEMO_FILE"
    say "would then: seed the demo directory through vsz-directory, sign in as each demo account"
    say "dry run: nothing was changed"; return
  fi
  mkdir -p "$(dirname "$DEMO_FILE")"; chmod 700 "$(dirname "$DEMO_FILE")"
  touch "$DEMO_FILE"; chmod 600 "$DEMO_FILE"
  say "accounts in $POOL_NAME:"
  sub=$(ensure_cognito_user "$admin" invite)
  subs=$(python3 -c 'import json, sys; d = json.loads(sys.argv[1]); d[sys.argv[2]] = sys.argv[3]; print(json.dumps(d))' "$subs" "$admin" "$sub")
  for name in $DEMO_ACCOUNTS; do
    sub=$(ensure_cognito_user "$name@example.com" demo)
    subs=$(python3 -c 'import json, sys; d = json.loads(sys.argv[1]); d[sys.argv[2]] = sys.argv[3]; print(json.dumps(d))' "$subs" "$name@example.com" "$sub")
  done
  python3 - "$admin" "$subs" "$TMP/seed-event.json" <<'PY'
import json, sys
admin, subs, out = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3]
d = lambda name: f"{name}@example.com"
spec = {
    "users": [{"email": e, "sub": s, "status": "disabled" if e == d("demo-disabled") else "active"}
              for e, s in subs.items()],
    "platform": {"members": [{"email": admin, "role": "system_admin"},
                             {"email": d("demo-owner"), "role": "system_owner"}]},
    "dealers": [{
        "name": "Demo dealer", "members": [{"email": d("demo-dealer"), "role": "dealer"}],
        "customers": [
            {"name": "Demo customer", "reference": "DEMO-1",
             "members": [{"email": d("demo-holder"), "role": "customer"},
                         {"email": d("demo-superadmin"), "role": "customer_admin"},
                         {"email": d("demo-user"), "role": "customer_user"},
                         {"email": d("demo-disabled"), "role": "customer_user"}],
             "sites": [{"name": "Home", "timezone": "Europe/Berlin",
                        "adapters": [{"thing": "adapter-01", "name": "Hall", "label": "The first Pi"}]}],
             "shares": [{"email": d("demo-user"), "camera": "cam-03", "thing": "adapter-01"}]},
            {"name": "Suspended demo customer", "status": "suspended",
             "members": [{"email": d("demo-suspended"), "role": "customer"}]},
        ]}],
}
json.dump({"op": "operator_seed", "actor": {"kind": "operator"}, "spec": spec}, open(out, "w"))
PY
  say "the demo directory:"
  invoke_lambda vsz-directory "$TMP/seed-event.json" || die "the seed failed -- see above"
  say "signing in as each demo account (demo-disabled and demo-suspended must be refused):"
  for name in $DEMO_ACCOUNTS; do
    bash "$VMS_HOME/cloud/sign-in-check.sh" "$name@example.com" || true
  done
  say "the administrator ($admin) signs in with the emailed temporary password and chooses a new one (the web page, Phase 3)"
}

# --- the tenant check's rollout, step 1 (DEALER-PANEL-PLAN.md 3.3, D4) -------------------------------

tenancy_functions() { grep -l '^def tenancy_refusal' "$LAMBDA_DIR"/*.py | xargs -n1 basename | sed 's/\.py$//; s/_/-/g'; }

authorizer_pools() { aws apigateway get-authorizer --rest-api-id "$API" --authorizer-id "$AUTHORIZER" \
  --query providerARNs --output text | tr '\t' '\n'; }

ensure_authorizer_pool() {      # vsz-users beside the old pool on kvs-demo-api's authorizer: added, nothing removed
  local before after
  before=$(authorizer_pools)
  if grep -qxF "$POOL_ARN" <<<"$before"; then say "$AUTHORIZER_NAME: accepts $POOL_NAME already"; return; fi
  if [ "$DRY" -eq 1 ]; then
    say "would ADD $POOL_NAME to $AUTHORIZER_NAME, beside $(tr '\n' ' ' <<<"$before")(nothing removed), and deploy stage $STAGE"
    return
  fi
  aws apigateway update-authorizer --rest-api-id "$API" --authorizer-id "$AUTHORIZER" \
    --patch-operations "op=add,path=/providerARNs,value=$POOL_ARN" >/dev/null
  after=$(authorizer_pools)
  # Read back: the new pool there, and every pool that was there still there.
  grep -qxF "$POOL_ARN" <<<"$after" || die "$AUTHORIZER_NAME still lacks $POOL_NAME after the patch: $after"
  while IFS= read -r arn; do
    [ -z "$arn" ] || grep -qxF "$arn" <<<"$after" ||
      die "$AUTHORIZER_NAME lost $arn -- put it back at once: aws apigateway update-authorizer --rest-api-id $API --authorizer-id $AUTHORIZER --patch-operations op=add,path=/providerARNs,value=$arn"
  done <<<"$before"
  say "added $POOL_NAME to $AUTHORIZER_NAME; it now accepts: $(tr '\n' ' ' <<<"$after")"
  CHANGED=1
}

provision_tenancy() {
  local fn role
  find_pool
  [ -n "$POOL_ID" ] || die "no user pool $POOL_NAME: bash cloud/provision.sh identity first"
  # The clip Lambdas read the camera row now, to tell whose clip it is (cloud/iam/, as the others).
  for fn in play-clip delete-clip set-clip-tier; do
    role=$(aws lambda get-function-configuration --function-name "$fn" --query Role --output text)
    ensure_inline_policy "${role##*/}" CamerasRegistryRead cameras-registry-read-policy.json
  done
  # Report first: the check logs what it would refuse and refuses nothing. Before the code is
  # deployed -- deploy.sh won't deploy a Lambda with the check to a function without TENANCY,
  # since unset means enforce.
  for fn in $(tenancy_functions); do ensure_env "$fn" TENANCY report; done
  ensure_authorizer_pool
  deploy_stage tenancy
  if [ "$DRY" -eq 1 ]; then say "dry run: nothing was changed"; return; fi
  for fn in $(tenancy_functions); do
    [ "$(aws lambda get-function-configuration --function-name "$fn" --query Environment.Variables.TENANCY --output text)" != None ] ||
      die "$fn has no TENANCY setting afterwards"
  done
  grep -qxF "$POOL_ARN" <<<"$(authorizer_pools)" || die "$AUTHORIZER_NAME doesn't accept $POOL_NAME afterwards"
  say "verified: every function with the check has a TENANCY setting; $AUTHORIZER_NAME accepts both pools"
  say "next: bash cloud/deploy.sh lambdas (the check, in report mode), then deploy.sh page (the page on $POOL_NAME)"
}

# --- the directory API (DEALER-PANEL-PLAN.md Phase 4, D8) -------------------------------------------

provision_directory() {
  local rid url
  find_pool
  [ -n "$POOL_ID" ] || die "no user pool $POOL_NAME: bash cloud/provision.sh identity first"
  grep -q "userpool/$POOL_ID\"" "$IAM_DIR/vsz-directory-api-policy.json" ||
    die "cloud/iam/vsz-directory-api-policy.json doesn't name $POOL_NAME ($POOL_ID)"
  ensure_role vsz-directory-api-role vsz-directory-api vsz-directory-api-policy.json
  ensure_function vsz-directory-api vsz-directory-api-role 512 15
  if [ "$DRY" -eq 1 ] && ! aws lambda get-function-configuration --function-name vsz-directory-api >/dev/null 2>&1; then
    say "would set vsz-directory-api's POOL_ID=$POOL_ID"
  else
    ensure_env vsz-directory-api POOL_ID "$POOL_ID"
  fi
  rid=$(ensure_resource "/directory/{proxy+}")
  ensure_method "$rid" "/directory/{proxy+}" ANY vsz-directory-api
  ensure_cors "$rid" "/directory/{proxy+}" "GET,POST,PUT,PATCH,OPTIONS"
  deploy_stage directory
  if [ "$DRY" -eq 1 ]; then say "dry run: nothing was changed"; return; fi
  # Done means verified through API Gateway: the preflight, the authorizer, and a real call as the
  # demo owner, whose password provision.sh directory-seed keeps.
  url="https://$API.execute-api.$REGION.amazonaws.com/$STAGE/directory/me"
  local i
  for i in 1 2 3 4 5 6; do
    curl -s -o /dev/null -D "$TMP/h" -X OPTIONS "$url" -H "Origin: https://example.org" \
      -H "Access-Control-Request-Method: GET" && grep -qi '^access-control-allow-origin: \*' "$TMP/h" && break
    sleep 5                        # a new deployment takes a few seconds to reach the edge
  done
  grep -qi '^access-control-allow-origin: \*' "$TMP/h" || die "OPTIONS /directory/me: no CORS answer"
  [ "$(curl -s -o /dev/null -w '%{http_code}' "$url")" = 401 ] || die "GET /directory/me without a token: expected 401"
  python3 - "$url" "$DEMO_FILE" "$REGION" "$(aws cognito-idp list-user-pool-clients --user-pool-id "$POOL_ID" \
      --query "UserPoolClients[?ClientName=='$CLIENT_NAME'].ClientId" --output text)" <<'PY' || die "the directory API didn't answer the demo owner -- is vsz-directory's new code deployed? (bash cloud/deploy.sh lambda vsz-directory)"
import json, sys, urllib.error, urllib.request
url, demo, region, client = sys.argv[1:5]
pw = dict(line.split() for line in open(demo) if line.strip())["demo-owner@example.com"]
auth = urllib.request.Request(f"https://cognito-idp.{region}.amazonaws.com/", data=json.dumps({
    "AuthFlow": "USER_PASSWORD_AUTH", "ClientId": client,
    "AuthParameters": {"USERNAME": "demo-owner@example.com", "PASSWORD": pw}}).encode(),
    headers={"Content-Type": "application/x-amz-json-1.1",
             "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth"})
token = json.load(urllib.request.urlopen(auth, timeout=30))["AuthenticationResult"]["IdToken"]
try:
    me = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": token}), timeout=30))
except urllib.error.HTTPError as e:
    print(f"GET /directory/me as demo-owner: {e.code} {e.read().decode()[:200]}", file=sys.stderr)
    sys.exit(1)
assert me.get("role") == "system_owner", me
print(f"GET /directory/me as demo-owner: role {me['role']}, {me['org']['name']}")
PY
  say "verified: /directory answers (CORS preflight, authorizer, vsz-directory-api, vsz-directory)"
}

# --- the dealer portal's camera inventory (DEALER-PANEL-PLAN.md Phase 5, D5) -----------------------

# The portal's customer list counts each customer's cameras, and its Cameras page lists them, from
# the registry -- read by vsz-directory-api, outside the VPC. Its role gets a read of the cameras
# table (Scan) and the thumbnails' times (s3:GetObject on thumbs/, for HeadObject), and the function
# learns the evidence bucket, as list-cameras did (provision_thumbnails).
provision_portal() {
  local bucket url
  aws lambda get-function-configuration --function-name vsz-directory-api >/dev/null 2>&1 ||
    die "no function vsz-directory-api: bash cloud/provision.sh directory first"
  bucket=$(aws lambda get-function-configuration --function-name record-clip \
    --query 'Environment.Variables.BUCKET' --output text)
  [ -n "$bucket" ] && [ "$bucket" != None ] || die "record-clip has no BUCKET: can't tell the evidence bucket"
  grep -q "arn:aws:s3:::$bucket/thumbs/" "$IAM_DIR/vsz-directory-api-inventory-policy.json" ||
    die "cloud/iam/vsz-directory-api-inventory-policy.json doesn't name $bucket"
  ensure_inline_policy vsz-directory-api-role camera-inventory vsz-directory-api-inventory-policy.json
  ensure_env vsz-directory-api THUMB_BUCKET "$bucket"
  if [ "$DRY" -eq 1 ]; then say "dry run: nothing was changed"; return; fi
  # Done means the demo dealer's customer list carries camera counts through API Gateway. A new
  # inline policy takes a few seconds to apply, so a missing count is asked again for a while.
  url="https://$API.execute-api.$REGION.amazonaws.com/$STAGE/directory/customers"
  python3 - "$url" "$DEMO_FILE" "$REGION" "$(aws cognito-idp list-user-pool-clients --user-pool-id "$POOL_ID" \
      --query "UserPoolClients[?ClientName=='$CLIENT_NAME'].ClientId" --output text)" <<'PY' || die "the demo dealer's customer list has no camera counts -- is vsz-directory-api's new code deployed? (bash cloud/deploy.sh lambda vsz-directory-api)"
import json, sys, time, urllib.error, urllib.request
url, demo, region, client = sys.argv[1:5]
pw = dict(line.split() for line in open(demo) if line.strip())["demo-dealer@example.com"]
auth = urllib.request.Request(f"https://cognito-idp.{region}.amazonaws.com/", data=json.dumps({
    "AuthFlow": "USER_PASSWORD_AUTH", "ClientId": client,
    "AuthParameters": {"USERNAME": "demo-dealer@example.com", "PASSWORD": pw}}).encode(),
    headers={"Content-Type": "application/x-amz-json-1.1",
             "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth"})
token = json.load(urllib.request.urlopen(auth, timeout=30))["AuthenticationResult"]["IdToken"]
for attempt in range(8):
    try:
        rows = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": token}), timeout=30))
    except urllib.error.HTTPError as e:
        print(f"GET /directory/customers as demo-dealer: {e.code} {e.read().decode()[:200]}", file=sys.stderr)
        sys.exit(1)
    if rows and all(r.get("registered") is not None for r in rows):
        for r in rows:
            print(f"  {r['name']}: {r['adapters']} adapter(s), {r['registered']} camera(s) registered, {r['inPlans']} in plans")
        sys.exit(0)
    time.sleep(5)
sys.exit(1)
PY
  say "verified: the portal counts cameras from the registry (vsz-directory-api's camera-inventory policy)"
}

# --- adapters: Cloud IDs, the retained config, last seen (DEALER-PANEL-PLAN.md Phase 6, D15) ----------

PRESENCE_RULE=vsz_adapter_presence

# vsz-directory's key for the Cloud IDs' hashes: made here once, never printed, never changed --
# a new key would make every Cloud ID not yet used worthless.
ensure_cloud_id_key() {
  local has
  has=$(aws lambda get-function-configuration --function-name vsz-directory \
    --query 'Environment.Variables.CLOUD_ID_KEY' --output text 2>/dev/null | sed 's/^None$//')
  if [ -n "$has" ]; then say "vsz-directory: CLOUD_ID_KEY set already (left as is: a new one would void every unused Cloud ID)"; return; fi
  if [ "$DRY" -eq 1 ]; then say "would set vsz-directory's CLOUD_ID_KEY (64 hex characters, made here, never printed)"; return; fi
  aws lambda get-function-configuration --function-name vsz-directory --query 'Environment.Variables' --output json \
    > "$TMP/vars.json"
  ( umask 077; python3 - "$TMP/vars.json" "$TMP/env.json" <<'PY'
import json, secrets, sys
current = json.load(open(sys.argv[1])) or {}
current["CLOUD_ID_KEY"] = secrets.token_hex(32)
json.dump({"Variables": current}, open(sys.argv[2], "w"))
PY
  )
  aws lambda update-function-configuration --function-name vsz-directory --environment "file://$TMP/env.json" >/dev/null
  rm -f "$TMP/env.json"
  aws lambda wait function-updated --function-name vsz-directory
  CHANGED=1
  say "set vsz-directory's CLOUD_ID_KEY (not shown; it lives in the function's environment only)"
}

ensure_presence_rule() {        # IoT lifecycle events of adapter-* -> vsz-directory
  local policy
  if aws iot get-topic-rule --rule-name "$PRESENCE_RULE" >/dev/null 2>&1; then
    say "IoT rule $PRESENCE_RULE: exists, left as is"
  else
    create "IoT rule $PRESENCE_RULE (cloud/iot/vsz-adapter-presence-rule.json)" aws iot create-topic-rule \
      --rule-name "$PRESENCE_RULE" --topic-rule-payload "file://$VMS_HOME/cloud/iot/vsz-adapter-presence-rule.json"
  fi
  policy=$(aws lambda get-policy --function-name vsz-directory --query Policy --output text 2>/dev/null || true)
  if grep -qF "rule/$PRESENCE_RULE" <<<"$policy"; then say "vsz-directory: IoT may invoke it for $PRESENCE_RULE"; return; fi
  create "permission for IoT to invoke vsz-directory, for $PRESENCE_RULE only" aws lambda add-permission \
    --function-name vsz-directory --statement-id "iot-$PRESENCE_RULE" --action lambda:InvokeFunction \
    --principal iot.amazonaws.com --source-arn "arn:aws:iot:$REGION:$ACCOUNT:rule/$PRESENCE_RULE" \
    --source-account "$ACCOUNT"
}

# Every adapter's retained config, as the directory says: the Pi reads it at every connect. Run
# once for the adapters that existed before Phase 6 (adapter-01: activated); afterwards
# vsz-directory-api publishes on each change, and the System console's Sync sends one again.
publish_adapter_configs() {
  local endpoint at
  printf '{"op": "adapters", "actor": {"kind": "operator"}}\n' > "$TMP/adapters-event.json"
  if ! invoke_lambda vsz-directory "$TMP/adapters-event.json" >/dev/null; then
    if [ "$DRY" -eq 1 ]; then say "would publish every adapter's config (listing them needs vsz-directory's Phase 6 code)"; return; fi
    die "vsz-directory didn't list the adapters -- is its Phase 6 code deployed? (deploy.sh lambda vsz-directory)"
  fi
  endpoint=$(aws iot describe-endpoint --endpoint-type iot:Data-ATS --query endpointAddress --output text)
  at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  python3 -c '
import json, sys
for a in json.load(open(sys.argv[1]))["adapters"]:
    print(a["thingName"], "true" if a["status"] == "activated" else "false")' "$TMP/vsz-directory-answer.json" |
  while read -r thing activated; do
    if [ "$DRY" -eq 1 ]; then say "would publish adapter/$thing/config (retained): activated $activated"; continue; fi
    printf '{"activated": %s, "at": "%s"}' "$activated" "$at" > "$TMP/config.json"
    aws iot-data publish --endpoint-url "https://$endpoint" --topic "adapter/$thing/config" --qos 1 --retain \
      --cli-binary-format raw-in-base64-out --payload "fileb://$TMP/config.json"
    say "published adapter/$thing/config (retained): activated $activated"
  done
}

provision_adapters() {
  aws lambda get-function-configuration --function-name vsz-directory >/dev/null 2>&1 ||
    die "no function vsz-directory: bash cloud/provision.sh identity first"
  aws iam get-role --role-name vsz-directory-api-role >/dev/null 2>&1 ||
    die "no role vsz-directory-api-role: bash cloud/provision.sh directory first"
  ensure_cloud_id_key
  ensure_inline_policy vsz-directory-api-role adapters vsz-directory-api-adapters-policy.json
  ensure_presence_rule
  publish_adapter_configs
  if [ "$DRY" -eq 1 ]; then say "dry run: nothing was changed"; return; fi
  [ -n "$(aws lambda get-function-configuration --function-name vsz-directory \
          --query 'Environment.Variables.CLOUD_ID_KEY' --output text | sed 's/^None$//')" ] &&
    aws iam get-role-policy --role-name vsz-directory-api-role --policy-name adapters >/dev/null &&
    aws iot get-topic-rule --rule-name "$PRESENCE_RULE" >/dev/null &&
    aws lambda get-policy --function-name vsz-directory --query Policy --output text | grep -qF "rule/$PRESENCE_RULE" ||
    die "adapters: the key, the API's policy, the rule or IoT's permission isn't there afterwards"
  say "verified: CLOUD_ID_KEY set, vsz-directory-api may publish adapter configs and release cameras, adapters' presence reaches vsz-directory"
  say "on each adapter's Pi, once: systemctl --user restart kvs-agent onvif-admin  (the agent then reads its config)"
}

# --- adding cameras from the web page (DEALER-PANEL-PLAN.md Phase 7.3, D16) --------------------------

ensure_ttl() {      # ensure_ttl TABLE ATTRIBUTE -- DynamoDB deletes items whose ATTRIBUTE (epoch s) has passed
  local now
  now=$(aws dynamodb describe-time-to-live --table-name "$1" --query 'TimeToLiveDescription.TimeToLiveStatus' \
        --output text 2>/dev/null || echo NONE)
  if [ "$now" = ENABLED ] || [ "$now" = ENABLING ]; then say "table $1: items expire by $2 already"; return; fi
  create "expiry of $1's items by $2" aws dynamodb update-time-to-live --table-name "$1" \
    --time-to-live-specification "Enabled=true,AttributeName=$2"
}

ensure_iot_tables_role() {   # the role IoT rules use to write what adapters report into DynamoDB
  if aws iam get-role --role-name vsz-iot-tables-role >/dev/null 2>&1; then
    say "role vsz-iot-tables-role: exists, left as is"
    ensure_inline_policy vsz-iot-tables-role tables vsz-iot-tables-policy.json
    return
  fi
  create "role vsz-iot-tables-role (trust: cloud/iam/iot-tables-trust.json)" aws iam create-role \
    --role-name vsz-iot-tables-role --assume-role-policy-document "file://$IAM_DIR/iot-tables-trust.json"
  create "  its inline policy tables (cloud/iam/vsz-iot-tables-policy.json)" aws iam put-role-policy \
    --role-name vsz-iot-tables-role --policy-name tables --policy-document "file://$IAM_DIR/vsz-iot-tables-policy.json"
  [ "$DRY" -eq 1 ] || sleep 10            # a new role takes a moment before IoT may assume it
}

ensure_topic_rule() {        # ensure_topic_rule NAME FILE -- an IoT rule from cloud/iot/, made once
  local name=$1 file="$VMS_HOME/cloud/iot/$2" i
  [ -e "$file" ] || die "no $file"
  if aws iot get-topic-rule --rule-name "$name" >/dev/null 2>&1; then say "IoT rule $name: exists, left as is"; return; fi
  if [ "$DRY" -eq 1 ]; then say "would create IoT rule $name (cloud/iot/$2)"; return; fi
  for i in 1 2 3 4 5 6; do
    aws iot create-topic-rule --rule-name "$name" --topic-rule-payload "file://$file" 2>"$TMP/rule.err" && break
    sleep 5
  done
  aws iot get-topic-rule --rule-name "$name" >/dev/null 2>&1 || die "IoT rule $name: $(cat "$TMP/rule.err")"
  CHANGED=1
  say "created IoT rule $name (cloud/iot/$2)"
}

provision_cameras() {
  local rid url cmd_role
  aws iam get-role --role-name KVSAdapterRole >/dev/null 2>&1 || die "no role KVSAdapterRole (the adapters' role)"
  cmd_role=$(aws lambda get-function-configuration --function-name publish-cmd --query Role --output text)
  cmd_role=${cmd_role##*/}
  ensure_table adapter-discovery thing
  if [ "$DRY" -eq 1 ] && ! aws dynamodb describe-table --table-name adapter-discovery >/dev/null 2>&1; then
    say "would make adapter-discovery's items expire by ttl"
  else ensure_ttl adapter-discovery ttl; fi
  ensure_table camera-requests requestId
  if [ "$DRY" -eq 1 ] && ! aws dynamodb describe-table --table-name camera-requests >/dev/null 2>&1; then
    say "would make camera-requests's items expire by ttl"
  else ensure_ttl camera-requests ttl; fi
  ensure_iot_tables_role
  ensure_topic_rule vsz_adapter_discovery vsz-adapter-discovery-rule.json
  ensure_topic_rule vsz_camera_registration vsz-camera-registration-rule.json
  ensure_inline_policy KVSAdapterRole camera-requests kvs-adapter-camera-requests-policy.json
  ensure_inline_policy "$cmd_role" any-adapter-cmd publish-cmd-any-adapter-policy.json
  ensure_role adapter-cameras-role adapter-cameras adapter-cameras-policy.json
  ensure_function adapter-cameras adapter-cameras-role 256 10
  rid=$(ensure_resource "/adapters/{proxy+}")
  ensure_method "$rid" "/adapters/{proxy+}" ANY adapter-cameras
  ensure_cors "$rid" "/adapters/{proxy+}" "GET,POST,OPTIONS"
  deploy_stage cameras
  if [ "$DRY" -eq 1 ]; then say "dry run: nothing was changed"; return; fi
  # Done means verified through API Gateway: the preflight, the authorizer, the function and its
  # table read as the demo holder, and "no such adapter" for the demo dealer.
  url="https://$API.execute-api.$REGION.amazonaws.com/$STAGE/adapters/adapter-01/discovery"
  local i
  for i in 1 2 3 4 5 6; do
    curl -s -o /dev/null -D "$TMP/h" -X OPTIONS "$url" -H "Origin: https://example.org" \
      -H "Access-Control-Request-Method: GET" && grep -qi '^access-control-allow-origin: \*' "$TMP/h" && break
    sleep 5
  done
  grep -qi '^access-control-allow-origin: \*' "$TMP/h" || die "OPTIONS /adapters/...: no CORS answer"
  [ "$(curl -s -o /dev/null -w '%{http_code}' "$url")" = 401 ] || die "GET /adapters/... without a token: expected 401"
  find_pool
  python3 - "$url" "$DEMO_FILE" "$REGION" "$(aws cognito-idp list-user-pool-clients --user-pool-id "$POOL_ID" \
      --query "UserPoolClients[?ClientName=='$CLIENT_NAME'].ClientId" --output text)" <<'PY' || die "adapter-cameras didn't answer as it should -- see above"
import json, sys, urllib.error, urllib.request
url, demo, region, client = sys.argv[1:5]
passwords = dict(line.split() for line in open(demo) if line.strip())

def token(who):
    req = urllib.request.Request(f"https://cognito-idp.{region}.amazonaws.com/", data=json.dumps({
        "AuthFlow": "USER_PASSWORD_AUTH", "ClientId": client,
        "AuthParameters": {"USERNAME": who, "PASSWORD": passwords[who]}}).encode(),
        headers={"Content-Type": "application/x-amz-json-1.1",
                 "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth"})
    return json.load(urllib.request.urlopen(req, timeout=30))["AuthenticationResult"]["IdToken"]

def ask(who):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": token(who)}), timeout=30)
        return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")

holder = ask("demo-holder@example.com")
dealer = ask("demo-dealer@example.com")
print(f"  demo-holder: {holder[0]} {holder[1]}")
print(f"  demo-dealer: {dealer[0]} {dealer[1]}")
ok = holder[0] == 200 or holder == (404, {"error": "no scan yet: ask for one"})
sys.exit(0 if ok and dealer == (404, {"error": "no such adapter"}) else 1)
PY
  say "verified: /adapters answers its adapter's holder and no one else (CORS, authorizer, adapter-cameras, adapter-discovery)"
  say "on each adapter's Pi, once: systemctl --user restart kvs-agent  (it then answers discover, register, unregister)"
}

case "${1:-}" in
  user-settings)   preflight; provision_user_settings ;;
  camera-settings) preflight; provision_camera_settings ;;
  thumbnails)      preflight; provision_thumbnails ;;
  pg-layer)        preflight; ensure_pg_layer; [ "$DRY" -eq 0 ] || say "dry run: nothing was changed" ;;
  directory-db)    preflight; provision_directory_db ;;
  identity)        preflight; provision_identity ;;
  directory-seed)  preflight; shift; provision_directory_seed "${1:-}" ;;
  tenancy)         preflight; provision_tenancy ;;
  directory)       preflight; provision_directory ;;
  portal)          preflight; find_pool; provision_portal ;;
  adapters)        preflight; provision_adapters ;;
  cameras)         preflight; provision_cameras ;;
  *) sed -n '2,39p' "$0"; exit 1 ;;
esac
