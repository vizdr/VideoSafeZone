#!/usr/bin/env bash
# setup-check.sh -- what this Pi still lacks, and which script provides it. Read-only, quick, no
# sudo, no network. (LAUNCH.md Part A)
#
#   bash setup/setup-check.sh
#
# Every line is "ok" or "MISSING <script>". All ok means Part A is done; the stack's *health*
# is setup-launch.sh's proofs (video flowing, agent online), which this deliberately doesn't do.
source "$(dirname "$(readlink -f "$0")")/lib.sh"
set +e
MISSING=0
item() {   # item SCRIPT "what" test-command...
  local by="$1" what="$2"; shift 2
  if "$@" >/dev/null 2>&1; then printf '   %sok%s        %s\n' "$_CG" "$_CN" "$what"
  else printf '   %sMISSING%s   %-44s -> bash setup/%s\n' "$_CR" "$_CN" "$what" "$by"; MISSING=$((MISSING + 1)); fi
}
UU="$SETUP_HOME/.config/systemd/user"
step "setup check for $SETUP_USER, repo $VMS_HOME"
item setup-system.sh   "earlyoom running (A1)"                systemctl is-active --quiet earlyoom
item setup-system.sh   "persistent journal (A1)"              test -e /etc/systemd/journald.conf.d/90-vms-persistent.conf
item setup-system.sh   "GStreamer RTSP + timestampers (A3)"   bash -c "gst-inspect-1.0 rtspclientsink && gst-inspect-1.0 h265timestamper"
item setup-system.sh   "build tools (A3)"                     command -v cmake
item setup-system.sh   "lingering (A3)"                       bash -c "[ \"\$(loginctl show-user $SETUP_USER -p Linger --value)\" = yes ]"
item setup-system.sh   "/etc/adapter/adapter.env (A7)"        test -s /etc/adapter/adapter.env
item setup-system.sh   "system units incl. kvs-live@ (A8)"    test -e /etc/systemd/system/kvs-live@.service
item setup-system.sh   "sudo rule (A8)"                       test -e /etc/sudoers.d/020_vms-adapter
item setup-system.sh   "outage-buffer stick mounted (A10, optional)" buffer_ready
item setup-user.sh     "~/.bashrc block (A2)"                 grep -q 'VideoSafeZone (setup/setup-user.sh)' "$SETUP_HOME/.bashrc"
item setup-user.sh     "venv (A4)"                            test -x "$VENV/bin/python3"
item setup-user.sh     "MediaMTX $MEDIAMTX_VERSION (A5)"      bash -c "'$VMS_HOME/mediamtx/mediamtx' --version | grep -qx $MEDIAMTX_VERSION"
item setup-user.sh     "AWS CLI (A6)"                         test -x "$SETUP_HOME/.local/bin/aws"
item setup-user.sh     "user unit files (A8)"                 test -e "$UU/netwatch.service" -a -e "$UU/kvs-agent.service"
item setup-sdk.sh      "KVS SDK + patched kvssink (A3)"       bash -c "test -e '$KVS_SDK/build/libgstkvssink.so' && [ \$(grep -c 'VMS patch [45]' '$KVS_SDK/src/gstreamer/gstkvssink.cpp') -eq 2 ]"
item setup-identity.sh "device certificate in certs/ (A7)"    test -s "$VMS_HOME/certs/adapter.private.key"
item setup-launch.sh   "user units enabled (Part B)"          bash -c "[ \"\$(systemctl --user is-enabled kvs-agent kvs-mediamtx 2>/dev/null | sort -u)\" = enabled ]"
if [ "$MISSING" -eq 0 ]; then
  printf '\n%sPart A is complete.%s Health: bash setup/setup-launch.sh (its proofs).\n' "$_CG" "$_CN"
else
  printf '\n%s%d item(s) missing.%s  bash setup/setup-all.sh  does them all, in order.\n' "$_CR" "$MISSING" "$_CN"
  exit 1
fi
