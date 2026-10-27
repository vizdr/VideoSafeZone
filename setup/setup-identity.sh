#!/usr/bin/env bash
# setup-identity.sh -- the adapter's AWS identity: the device certificate in certs/ (LAUNCH.md A7).
#
#   bash setup/setup-identity.sh            # certs/ present: prove it works. Else restore the
#                                           # stick's backup if there is one. Else say what to do.
#   bash setup/setup-identity.sh create     # a NEW certificate for the Thing (needs `aws login`)
#   bash setup/setup-identity.sh backup     # copy certs/ to the stick, for the next fresh card
#   bash setup/setup-identity.sh restore [DIR]   # copy a backup into certs/ (default: the stick)
#   bash setup/setup-identity.sh check      # the four proofs only
#
# Four files must end up in certs/ (gitignored, so a fresh clone has none):
#   adapter.cert.pem     the device certificate of Thing $THING_NAME
#   adapter.private.key  its private key -- AWS never shows it again after `create`
#   cacert.pem           Starfield root: the credentials endpoint (kvssink, aws_device_creds.py)
#   AmazonRootCA1.pem    Amazon root CA 1: the MQTT data endpoint (agent.py). The two CAs are not
#                        interchangeable; the wrong one fails like a permission problem (#29).
#
# Why a backup on the stick: with frequent fresh cards, `create` each time would pile up ACTIVE
# certificates on the Thing. Restoring the same identity needs no AWS login at all. The price:
# whoever holds the stick holds the adapter's identity (it can push to KVS and read the camera
# registry as the adapter). The stick lives in the Pi, next to the SD card that holds the same
# key -- but don't hand the stick to anyone. `backup` is therefore something you run, never
# something the other scripts do on their own.
#
# Never retired automatically: the Thing is shared with the VMS card (another SD card in the same
# board, VMS-PORT-PLAN.md D1), whose certificate must stay ACTIVE. `create` lists the others.
source "$(dirname "$(readlink -f "$0")")/lib.sh"
[ "$(id -u)" -ne 0 ] || die "run this one as your normal user, without sudo"
CERTS="$VMS_HOME/certs"
BACKUP="$SETUP_CACHE/adapter-identity"
FILES=(adapter.cert.pem adapter.private.key cacert.pem AmazonRootCA1.pem)
AWS="$HOME/.local/bin/aws"
[ -e /etc/adapter/adapter.env ] || die "no /etc/adapter/adapter.env -- run setup-system.sh first"
source "$VMS_HOME/adapter/bin/adapter-config.sh"   # THING_NAME, IOT_* endpoints, role alias

complete() { local f; for f in "${FILES[@]}"; do [ -s "$1/$f" ] || return 1; done; }

fetch_cas() {
  curl -fsS -o "$CERTS/cacert.pem"        https://www.amazontrust.com/repository/SFSRootCAG2.pem
  curl -fsS -o "$CERTS/AmazonRootCA1.pem" https://www.amazontrust.com/repository/AmazonRootCA1.pem
}

lock_down() { chmod 700 "$1" && chmod 600 "$1"/*; }

check() {
  step "proofs ($CERTS)"
  proof "all four files present" complete "$CERTS"
  # a) the certificate belongs to this key
  proof "certificate matches the private key" bash -c \
    "diff <(openssl x509 -in '$CERTS/adapter.cert.pem' -noout -pubkey) <(openssl pkey -in '$CERTS/adapter.private.key' -pubout)"
  # b) the one that matters most: cert ACTIVE, attached to the Thing, allowed to assume the role
  proof "credentials endpoint vends AWS credentials (kvssink, boto3)" bash -c \
    "curl -fsS --cert '$CERTS/adapter.cert.pem' --key '$CERTS/adapter.private.key' --cacert '$CERTS/cacert.pem' \
       -H 'x-amzn-iot-thingname: $THING_NAME' 'https://$IOT_CRED_ENDPOINT/role-aliases/$IOT_ROLE_ALIAS/credentials' \
       | grep -q accessKeyId"
  # c) the MQTT endpoint, with the other CA -- TLS handshake only
  proof "MQTT endpoint accepts the certificate (agent.py)" bash -c \
    "openssl s_client -connect '$IOT_DATA_ENDPOINT:8443' -CAfile '$CERTS/AmazonRootCA1.pem' \
       -cert '$CERTS/adapter.cert.pem' -key '$CERTS/adapter.private.key' </dev/null 2>/dev/null \
       | grep -q 'Verify return code: 0'"
  # d) end to end through the project's own code, VMS_HOME unset as under systemd
  proof "project code assumes the adapter's role" env -u VMS_HOME "$VENV/bin/python3" -c "
import sys; sys.path.insert(0, '$VMS_HOME/adapter'); from aws_device_creds import get_session
assert 'assumed-role' in get_session().client('sts').get_caller_identity()['Arn']"
  verdict "setup-identity.sh"
}

restore() {
  local src="${1:-$BACKUP}"
  complete "$src" || die "no complete identity backup in $src"
  if complete "$CERTS" && cmp -s "$src/adapter.private.key" "$CERTS/adapter.private.key"; then
    ok "certs/ already holds this identity"; return 0
  fi
  [ ! -e "$CERTS/adapter.private.key" ] || die "certs/ already holds a different key -- move it away first; nothing overwritten"
  mkdir -p "$CERTS"
  cp "${FILES[@]/#/$src/}" "$CERTS/"
  if [ -e "$src/.cert-arn" ]; then cp "$src/.cert-arn" "$CERTS/"; fi
  lock_down "$CERTS"
  ok "restored from $src"
}

create() {
  [ ! -e "$CERTS/adapter.private.key" ] || die "certs/ already holds a key -- this would replace the adapter's identity. Move certs/ away first if you mean it."
  "$AWS" sts get-caller-identity >/dev/null 2>&1 || die "the AWS CLI isn't logged in: run  aws login --remote  first"
  "$AWS" iot describe-thing --thing-name "$THING_NAME" >/dev/null || die "Thing $THING_NAME not found in this account"
  step "a new certificate for $THING_NAME"
  ( umask 077; mkdir -p "$CERTS" )
  local arn
  arn=$(cd "$CERTS" && umask 077 && "$AWS" iot create-keys-and-certificate --set-as-active \
          --certificate-pem-outfile adapter.cert.pem --public-key-outfile adapter.public.key \
          --private-key-outfile adapter.private.key --query certificateArn --output text)
  echo "$arn" > "$CERTS/.cert-arn"
  "$AWS" iot attach-policy --policy-name KVSAdapterThingPolicy --target "$arn"
  "$AWS" iot attach-thing-principal --thing-name "$THING_NAME" --principal "$arn"
  fetch_cas
  lock_down "$CERTS"
  ok "created and attached: $arn"
  step "the Thing's other certificates (left ACTIVE -- one may be the VMS card's)"
  "$AWS" iot list-thing-principals --thing-name "$THING_NAME" --query principals --output text | tr '\t' '\n' |
    grep -v "^$arn$" | while read -r other; do
      "$AWS" iot describe-certificate --certificate-id "${other##*/}" \
        --query 'certificateDescription.[creationDate,status]' --output text | sed "s|^|   ${other##*/}  |"
    done
  note "retire one you know is gone:  aws iot update-certificate --certificate-id <id> --new-status INACTIVE"
  note "keep this identity for the next fresh card:  bash setup/setup-identity.sh backup"
  sleep 5   # a just-attached principal takes a moment to reach the credentials endpoint
}

backup() {
  complete "$CERTS" || die "certs/ isn't complete -- nothing to back up"
  buffer_ready || die "the stick isn't mounted (setup-system.sh, A10)"
  mkdir -p "$BACKUP"
  cp "${FILES[@]/#/$CERTS/}" "$BACKUP/"
  if [ -e "$CERTS/.cert-arn" ]; then cp "$CERTS/.cert-arn" "$BACKUP/"; fi
  lock_down "$BACKUP"
  ok "identity copied to $BACKUP (mode 700/600). Whoever holds the stick holds this identity."
}

case "${1:-auto}" in
  auto)
    step "device identity for $THING_NAME"
    if complete "$CERTS"; then ok "certs/ present"
    elif buffer_ready && complete "$BACKUP"; then restore
    else
      warn "no identity yet. Either:"
      note "  copy certs/ from the previous card, or"
      note "  aws login --remote   and then   bash setup/setup-identity.sh create   (and then  backup)"
      exit 1
    fi
    check ;;
  check)   check ;;
  restore) restore "${2:-}"; check ;;
  create)  create; check ;;
  backup)  backup ;;
  *) die "usage: setup-identity.sh [check|restore [DIR]|create|backup]" ;;
esac
