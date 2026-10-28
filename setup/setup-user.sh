#!/usr/bin/env bash
# setup-user.sh -- the setup steps that need no root (LAUNCH.md Part A). Run as your user:
#
#   bash setup/setup-user.sh
#
#   A2  environment in ~/.bashrc (VMS_HOME, KVS_SDK, GStreamer paths) for interactive shells --
#       systemd units never read it, which is why every unit below carries literal paths.
#   A4  the Python venv (venv-adapter) with every module adapter/ imports (FoundAndFixed #27).
#   A5  the MediaMTX binary, extracted by name so the project's mediamtx.yml survives (#26).
#   A6  the AWS CLI, per user, after checking AWS's GPG signature. It is the *operator's* tool
#       (deploys, creating a device certificate); the adapter itself never uses it. Logging in
#       is interactive and left to you:  aws login --remote  (only when you need it).
#   A8  the user units: MediaMTX, the cam-01 publisher, agent, admin GUI, event watcher, outage
#       supervisor and uploader, rematch timer, and netwatch. Written, not enabled: that is
#       setup-launch.sh, once the certificate exists. This script is the authoritative source of
#       the unit files (they are not in git).
#
# Idempotent: the venv is updated in place, a binary of the right version isn't fetched twice,
# unit files are rewritten only when their text changes.
source "$(dirname "$(readlink -f "$0")")/lib.sh"
[ "$(id -u)" -ne 0 ] || die "run this one as your normal user, without sudo"
UNITS="$HOME/.config/systemd/user"

# ---------------------------------------------------------------- A2 ~/.bashrc
step "A2  environment block in ~/.bashrc"
BEGIN='# >>> VideoSafeZone (setup/setup-user.sh) >>>'
END='# <<< VideoSafeZone <<<'
BLOCK="$BEGIN
export VMS_HOME=$VMS_HOME
export KVS_SDK=\$VMS_HOME/vendor/amazon-kinesis-video-streams-producer-sdk-cpp
export GST_PLUGIN_PATH=\$KVS_SDK/build
export LD_LIBRARY_PATH=\$KVS_SDK/open-source/local/lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}
export AWS_REGION=\$(sed -n 's/^AWS_REGION=//p' /etc/adapter/adapter.env 2>/dev/null)
export THING_NAME=\$(sed -n 's/^THING_NAME=//p' /etc/adapter/adapter.env 2>/dev/null)
# ~/.local/bin (per-user AWS CLI): ~/.profile adds it only if it existed at login
case \":\$PATH:\" in *\":\$HOME/.local/bin:\"*) ;; *) export PATH=\"\$HOME/.local/bin:\$PATH\" ;; esac
$END"
# Replaced as a whole on every run, so a moved clone updates it. AWS_REGION and THING_NAME come
# from adapter.env, so a manual run and the services can't talk to different things (A7).
if grep -qF "$BEGIN" ~/.bashrc 2>/dev/null; then
  python3 - "$BEGIN" "$END" "$BLOCK" <<'PY'
import os, sys
b, e, block = sys.argv[1:4]
p = os.path.expanduser("~/.bashrc"); s = open(p).read()
i, j = s.index(b), s.index(e) + len(e)
open(p, "w").write(s[:i] + block + s[j:])
PY
else
  printf '\n%s\n' "$BLOCK" >> ~/.bashrc
fi
ok "~/.bashrc carries the block (new terminals see it; this one: source ~/.bashrc)"

# ---------------------------------------------------------------- A4 venv
step "A4  Python venv"
[ -x "$VENV/bin/python3" ] || python3 -m venv "$VENV"
# requirements.txt is the one list of what adapter/ imports (CI installs the same, #27);
# requirements-test.txt adds pytest (PYTEST.md).
"$VENV/bin/pip" install -q -r "$VMS_HOME/requirements.txt" -r "$VMS_HOME/requirements-test.txt"
ok "$("$VENV/bin/python3" --version) in $VENV"

# ---------------------------------------------------------------- A5 MediaMTX
step "A5  MediaMTX $MEDIAMTX_VERSION"
if ! "$VMS_HOME/mediamtx/mediamtx" --version 2>/dev/null | grep -qx "$MEDIAMTX_VERSION"; then
  T=$(mktemp -d)
  # -f: a 404 must fail loudly, not leave a 9-byte "Not Found" tarball (guide §2.5).
  curl -fsSL -o "$T/m.tar.gz" \
    "https://github.com/bluenviron/mediamtx/releases/download/$MEDIAMTX_VERSION/mediamtx_${MEDIAMTX_VERSION}_linux_arm64.tar.gz"
  # The binary by name only: the tarball also carries a default mediamtx.yml, which a plain
  # `tar xzf` here would write over the project's (FoundAndFixed #26).
  tar -xzf "$T/m.tar.gz" -C "$T" mediamtx
  install -m 755 "$T/mediamtx" "$VMS_HOME/mediamtx/mediamtx"
  rm -rf "$T"
fi
ok "$("$VMS_HOME/mediamtx/mediamtx" --version)"

# ---------------------------------------------------------------- A6 AWS CLI
step "A6  AWS CLI (operator tool, per user)"
if ! "$HOME/.local/bin/aws" --version >/dev/null 2>&1; then
  T=$(mktemp -d)
  curl -fsSL -o "$T/awscliv2.zip" https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip
  curl -fsSL -o "$T/awscliv2.sig" https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip.sig
  # 70 MB of code that will hold your AWS credentials: check AWS's signature before running it.
  # "[expired]" next to the key is normal (AWS extends it; keyservers lag); only "Good signature"
  # with this fingerprint counts.
  export GNUPGHOME="$T/gnupg"; mkdir -m 700 "$GNUPGHOME"
  gpg -q --keyserver hkps://keyserver.ubuntu.com --recv-keys FB5DB77FD5C118B80511ADA8A6310ACC4672475C
  gpg --verify "$T/awscliv2.sig" "$T/awscliv2.zip" 2>&1 | grep -q 'Good signature' || die "AWS CLI signature check failed -- not installed"
  unset GNUPGHOME
  (cd "$T" && unzip -q awscliv2.zip && ./aws/install -i "$HOME/.local/aws-cli" -b "$HOME/.local/bin" >/dev/null)
  rm -rf "$T"
fi
ok "$("$HOME/.local/bin/aws" --version 2>&1 | cut -d' ' -f1)"
if "$HOME/.local/bin/aws" sts get-caller-identity >/dev/null 2>&1; then
  ok "AWS CLI is logged in"
else
  note "AWS CLI not logged in -- only needed to create a certificate or deploy: aws login --remote"
fi

# ---------------------------------------------------------------- A8 user units
step "A8  user units in $UNITS"
mkdir -p "$UNITS"
CHANGED=0
uunit() {  # uunit FILE -- text on stdin; rewritten only when it changed
  local f="$UNITS/$1" tmp; tmp=$(mktemp)
  cat > "$tmp"
  if ! cmp -s "$tmp" "$f" 2>/dev/null; then mv "$tmp" "$f"; CHANGED=$((CHANGED + 1)); note "wrote $1"; else rm -f "$tmp"; fi
}
uunit kvs-camera-init.service <<EOF
[Unit]
Description=Lock the USB camera's exposure/WB/focus before streaming starts
Before=kvs-mediamtx.service

[Service]
Type=oneshot
ExecStart=$VMS_HOME/adapter/bin/camera-init.sh
RemainAfterExit=yes
# retry if the camera wasn't there yet (detection itself also waits 30 s, FoundAndFixed #42)
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
uunit kvs-mediamtx.service <<EOF
[Unit]
Description=MediaMTX RTSP/HLS server for camera capture
After=network.target

[Service]
Type=simple
WorkingDirectory=$VMS_HOME/mediamtx
ExecStart=$VMS_HOME/mediamtx/mediamtx
# Network cameras' paths come from the registry after every start (mediamtx.yml has none,
# FoundAndFixed #31, #32). '-': no registry and no cache must not stop MediaMTX itself.
ExecStartPost=-$VENV/bin/python3 $VMS_HOME/adapter/sync_mediamtx_paths.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
uunit kvs-camera-publish.service <<EOF
[Unit]
Description=USB camera capture/encode -> publish to MediaMTX (rtsp://127.0.0.1:8554/cam01)
After=kvs-camera-init.service kvs-mediamtx.service
# Wants, not Requires, on init: a failed exposure lock must not block the video for good
# -- a dependency failure is never retried (FoundAndFixed #42).
Wants=kvs-camera-init.service
Requires=kvs-mediamtx.service

[Service]
Type=simple
WorkingDirectory=$VMS_HOME/adapter/bin
ExecStart=$VMS_HOME/adapter/bin/publish-cam01.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
py_unit() {  # py_unit NAME DESCRIPTION WORKDIR SCRIPT [extra [Unit] line]
uunit "$1.service" <<EOF
[Unit]
Description=$2
${5:-}

[Service]
Type=simple
WorkingDirectory=$3
ExecStart=$VENV/bin/python3 -u $4
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
}
py_unit kvs-agent           "MQTT control agent"                            "$VMS_HOME/adapter"             "$VMS_HOME/adapter/agent.py"
py_unit onvif-admin         "Local ONVIF admin GUI (port 8080)"             "$VMS_HOME/adapter/onvif-admin" "$VMS_HOME/adapter/onvif-admin/app.py"
py_unit kvs-event-watcher   "ONVIF detections -> evidence clips"            "$VMS_HOME/adapter"             "$VMS_HOME/adapter/event_watcher.py"
py_unit kvs-outage-buffer   "Durable outage buffering supervisor (OUTAGE.md)" "$VMS_HOME/adapter"           "$VMS_HOME/adapter/outage_buffer.py" "After=kvs-mediamtx.service"
py_unit kvs-outage-uploader "Backfill buffered outage footage to S3 (OUTAGE.md)" "$VMS_HOME/adapter"        "$VMS_HOME/adapter/outage_uploader.py"
uunit kvs-camera-rematch.service <<EOF
[Unit]
Description=Follow ONVIF cameras to a changed IP address (registry + MediaMTX)
After=kvs-mediamtx.service

[Service]
Type=oneshot
WorkingDirectory=$VMS_HOME/adapter
ExecStart=$VENV/bin/python3 -u $VMS_HOME/adapter/rematch_cameras.py
EOF
uunit kvs-camera-rematch.timer <<EOF
[Unit]
Description=Rescan for moved ONVIF cameras every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF
uunit netwatch.service <<EOF
[Unit]
Description=Log network health every 10 s (FoundAndFixed #79)

[Service]
ExecStart=$VMS_HOME/adapter/bin/netwatch.sh
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
ok "$CHANGED unit file(s) written; all reloaded"

# ---------------------------------------------------------------- proofs
step "proofs"
proof "venv imports every module adapter/ needs" \
  "$VENV/bin/python3" -c "import boto3, awsiot, awscrt, flask, requests, lxml, onvif, wsdiscovery"
proof "pytest in the venv (PYTEST.md)"         "$VENV/bin/python3" -m pytest --version
proof "ONVIF WSDLs found with VMS_HOME unset (as under systemd)" \
  env -u VMS_HOME "$VENV/bin/python3" -c "import sys, os; sys.path.insert(0, '$VMS_HOME/adapter'); import camera_control as c; assert os.path.isdir(c.WSDL_DIR)"
proof "MediaMTX $MEDIAMTX_VERSION"            bash -c "'$VMS_HOME/mediamtx/mediamtx' --version | grep -qx $MEDIAMTX_VERSION"
proof "project mediamtx.yml untouched"        git -C "$VMS_HOME" diff --quiet -- mediamtx/mediamtx.yml
proof "AWS CLI installed"                     "$HOME/.local/bin/aws" --version
proof "user units valid"                      systemd-analyze --user verify "$UNITS/kvs-agent.service" "$UNITS/kvs-mediamtx.service" "$UNITS/netwatch.service"
proof "no unit path still says \$VMS_HOME or ~" bash -c "! grep -hE 'ExecStart|WorkingDirectory' $UNITS/*.service | grep -qE '\\\$|~'"
verdict "setup-user.sh"
