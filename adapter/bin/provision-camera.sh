#!/usr/bin/env bash
# Provisions ONE passthrough camera's local systemd wiring: an env file for
# kvs-cam@.service's templated unit (§16.6), then daemon-reload, and a start unless told not to.
#
#   provision-camera.sh cam-03 cam03            # registration: started now
#   provision-camera.sh cam-03 cam03 no-start   # Re-register on a Pi that has no producer for
#                                               # it yet: provisioned, not started
#
# Never `enable`: a producer costs money while it runs, so none starts at boot -- the rule
# cam-01/cam-02's units always followed (LAUNCH.md Part B). The instance used to be enabled,
# so every GUI-registered camera pushed to KVS after each reboot, unattended
# (FoundAndFixed.md #81).
#
# The second form exists because the registry is shared with VMS, which runs on the same board
# from another SD card: a camera registered there has a row and a MediaMTX path here, but no
# producer, until Re-register provisions one (VMS-PORT-PLAN.md D1, step 2.4).
# Invoked from the ONVIF admin GUI (adapter/onvif-admin/app.py), which runs as the same
# unprivileged user and shells out to this script via sudo rather than writing under
# /etc itself -- this keeps "what root-level file gets written and how" as one
# reviewable, input-validated script instead of scattered across the Flask app. The sudo
# rule in LAUNCH.md A8 allows exactly these argument forms. It is input hygiene and a single
# source of truth for the templating, not a hard privilege boundary: the script lives in the
# user's own writable checkout (LAUNCH.md A8 says as much).
set -euo pipefail

CAMERA_ID="${1:-}"       # e.g. cam-03
MEDIAMTX_PATH="${2:-}"   # e.g. cam03 -- same id, hyphen stripped
START="${3:-start}"      # start | no-start

if [[ ! "$CAMERA_ID" =~ ^cam-[0-9]{2}$ ]]; then
  echo "invalid camera id: $CAMERA_ID" >&2
  exit 1
fi
if [[ ! "$MEDIAMTX_PATH" =~ ^cam[0-9]{2}$ || "$MEDIAMTX_PATH" != "${CAMERA_ID//-/}" ]]; then
  echo "invalid mediamtx path for $CAMERA_ID: $MEDIAMTX_PATH" >&2
  exit 1
fi
if [[ ! "$START" =~ ^(start|no-start)$ ]]; then
  echo "invalid start mode: $START (start | no-start)" >&2
  exit 1
fi

ENV_FILE="/etc/adapter/channels/${MEDIAMTX_PATH}.env"
if [[ -e "$ENV_FILE" ]]; then
  echo "already provisioned: $ENV_FILE exists" >&2
  exit 1
fi

mkdir -p /etc/adapter/channels
cat > "$ENV_FILE" <<EOF
CAMERA_ID=${CAMERA_ID}
MEDIAMTX_PATH=${MEDIAMTX_PATH}
EOF

systemctl daemon-reload
if [ "$START" = start ]; then
  systemctl start "kvs-cam@${MEDIAMTX_PATH}.service"
fi
