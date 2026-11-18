#!/usr/bin/env bash
# setup-launch.sh -- start the stack and prove it works (LAUNCH.md Parts B and C).
#
#   bash setup/setup-launch.sh
#
# Needs the earlier steps: setup-system.sh, setup-user.sh, setup-identity.sh (the SDK only for
# the producers, which this script never starts). Then:
#   1. enables and starts every user unit -- MediaMTX, the cam-01 publisher, agent, admin GUI,
#      event watcher, outage supervisor and uploader, the rematch, schedule and thumbnail timers, netwatch. Enabled means
#      they come back by themselves after every reboot (lingering is on), so this runs once.
#   2. gives every camera registered from another SD card -- or before this card existed -- its
#      producer: a kvs-cam@ instance via provision-camera.sh, *not* started and *not* enabled
#      (cost rule, FoundAndFixed #81). Without it, Start would do nothing for those cameras
#      (#37's trap, VMS-PORT-PLAN.md D1). cam-01 and cam-02 have their own units.
#   3. proves the result the way CLAUDE.md asks for: not `is-active`, but video actually flowing
#      into MediaMTX, the agent online with AWS, the admin GUI answering, and no producer
#      enabled.
#
# Producers (the units that cost money) are never started here: that is Start, in either GUI.
source "$(dirname "$(readlink -f "$0")")/lib.sh"
[ "$(id -u)" -ne 0 ] || die "run this one as your normal user, without sudo"
[ -s /etc/adapter/adapter.env ] || die "no /etc/adapter/adapter.env -- run setup-system.sh first"
[ -s "$VMS_HOME/certs/adapter.private.key" ] || die "no device identity -- run setup-identity.sh first"
[ -x "$VENV/bin/python3" ] || die "no venv -- run setup-user.sh first"

USER_UNITS=(kvs-mediamtx kvs-agent onvif-admin kvs-event-watcher kvs-outage-buffer kvs-outage-uploader netwatch)
# The USB camera's capture runs only on an adapter whose registry has a USB camera (mode transcode) --
# today adapter-01's cam-01: a USB camera on another adapter has no ID scheme yet (DEALER-PANEL-PLAN.md
# A6), and its units would only fail (FoundAndFixed.md #99). Read through the index, its own rows only;
# a registry that can't be read keeps them, so the first Pi never loses its camera.
USB_UNITS=(kvs-camera-init kvs-camera-publish)
HAS_USB=$(env -u VMS_HOME SETUP_VMS_HOME="$VMS_HOME" "$VENV/bin/python3" - 2>/dev/null <<'PY' || echo unknown
import os, sys
sys.path.insert(0, os.path.join(os.environ["SETUP_VMS_HOME"], "adapter"))
import registry_reads
print("yes" if any(r.get("mode") == "transcode" for r in registry_reads.own_rows(["mode"], secrets=False)) else "no")
PY
)
if [ "$HAS_USB" = no ]; then
  note "no USB camera in this adapter's registry: kvs-camera-init and kvs-camera-publish stay off (A6)"
else
  USER_UNITS=("${USB_UNITS[@]}" "${USER_UNITS[@]}")
fi

step "1. user units: enable (start at every boot) and start now"
systemctl --user daemon-reload
for u in "${USER_UNITS[@]}"; do
  systemctl --user enable --now "$u.service" >/dev/null 2>&1 || warn "$u didn't start -- journalctl --user -u $u"
done
systemctl --user enable --now kvs-camera-rematch.timer >/dev/null 2>&1
systemctl --user enable --now kvs-schedule.timer >/dev/null 2>&1
systemctl --user enable --now kvs-thumbnails.timer >/dev/null 2>&1
ok "$(for u in "${USER_UNITS[@]}"; do printf '%s=%s ' "$u" "$(systemctl --user is-active "$u")"; done)"

step "2. producers for cameras registered elsewhere (provisioned, not started)"
# This adapter's rows, read through its own identity by the adapterId-index -- the only read of the
# table its narrowed role allows (DEALER-PANEL-PLAN.md Phase 10.2c, f): every passthrough camera it
# acts on, except the two with dedicated units. A missing channel file means no producer here. A read
# that fails is a failure, never "every camera has its producer" (FoundAndFixed.md #98).
TODO=()
if TODO_OUT=$(env -u VMS_HOME SETUP_VMS_HOME="$VMS_HOME" "$VENV/bin/python3" - <<'PY'
import os, sys
sys.path.insert(0, os.path.join(os.environ["SETUP_VMS_HOME"], "adapter"))
import camera_control
import registry_reads
for row in registry_reads.own_rows(["mode"], secrets=False):
    cid = row["cameraId"]
    if row.get("mode") != "passthrough" or cid in ("cam-01", "cam-02") or not camera_control.acts_on(row):
        continue
    path = camera_control.mediamtx_path_name(cid)
    if not os.path.exists(os.path.join(camera_control.CHANNELS_DIR, f"{path}.env")):
        print(cid, path)
PY
); then
  [ -z "$TODO_OUT" ] || mapfile -t TODO <<<"$TODO_OUT"
  [ "${#TODO[@]}" -gt 0 ] || ok "every registered camera already has its producer"
else
  proof "this adapter's cameras read from the registry (AWS reachable? its identity, A7?)" false
fi
for line in "${TODO[@]}"; do
  set -- $line
  # The sudo rule allows exactly this form (setup-system.sh): no password needed.
  if sudo -n "$VMS_HOME/adapter/bin/provision-camera.sh" "$1" "$2" no-start >/dev/null; then
    ok "$1: kvs-cam@$2 provisioned (Start in either GUI runs it)"
  else
    warn "$1: provisioning failed -- Re-register it in the admin GUI (LAUNCH.md E3)"
  fi
done

step "3. proofs (Part C)"
note "waiting up to 40 s for the cameras' video to reach MediaMTX ..."
FLOW=$(timeout 60 env SETUP_VMS_HOME="$VMS_HOME" "$VENV/bin/python3" - <<'PY'
import os, sys, time
sys.path.insert(0, os.path.join(os.environ["SETUP_VMS_HOME"], "adapter"))
import mediamtx_api, requests
deadline = time.monotonic() + 40
names, flowing = set(), set()
while time.monotonic() < deadline:
    try:
        names = {p["name"] for p in requests.get(f"{mediamtx_api.API}/v3/paths/list", timeout=5).json()["items"]}
    except Exception:
        names = set()
    flowing = mediamtx_api.flowing_paths()
    if names and flowing >= names:
        break
    time.sleep(3)
print(" ".join(sorted(flowing)), "|", " ".join(sorted(names - flowing)))
PY
) || FLOW=" | (MediaMTX not answering)"
ok "video flowing: ${FLOW%%|*}"
[ -z "$(echo "${FLOW#*|}" | tr -d ' ')" ] || warn "no video yet from:${FLOW#*|} (camera off, or still connecting -- re-run this script)"
for u in "${USER_UNITS[@]}"; do proof "$u active" systemctl --user is-active --quiet "$u"; done
if [ "$HAS_USB" != no ]; then
  proof "cam01 video reaches MediaMTX (bytesReceived grows, #50)" bash -c "grep -qw cam01 <<<'${FLOW%%|*}'"
fi
proof "agent online with AWS IoT"            env SETUP_VMS_HOME="$VMS_HOME" "$VENV/bin/python3" -c "
import os, sys; sys.path.insert(0, os.path.join(os.environ['SETUP_VMS_HOME'], 'adapter')); import aws_state
s = aws_state.read_state(); assert s.get('online') and not s.get('stale')"
proof "admin GUI answers on :8080"           curl -fsS -o /dev/null --max-time 10 http://127.0.0.1:8080/
proof "no producer enabled (cost rule, #81)" bash -c "! ls /etc/systemd/system/multi-user.target.wants/ | grep -q '^kvs-'"
verdict "setup-launch.sh"
note "admin GUI: http://$(hostname -I | awk '{print $1}'):8080    cloud page: https://dugyd3kkt36pw.cloudfront.net"
