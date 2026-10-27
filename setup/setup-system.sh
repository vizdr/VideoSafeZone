#!/usr/bin/env bash
# setup-system.sh -- every step of a fresh Pi's setup that needs root (LAUNCH.md Part A).
#
#   bash setup/setup-system.sh        # as your normal user; asks for your password once
#
# What it does, each step checked before it changes anything, so a second run is a no-op:
#   A1   stability: earlyoom tuned to kill compilers first, a 3 GB swapfile at low priority,
#        persistent journal, Wi-Fi power save off, an EEPROM update *reported* (never applied:
#        applying it needs a reboot you should choose the moment for).
#   A3   the apt packages: build tools for the KVS SDK, GStreamer and its RTSP plugin, jq.
#        (The SDK build itself is setup-sdk.sh: it runs for hours without root.)
#   A3   lingering, so user units start at boot and survive a lost SSH session.
#   A7   /etc/adapter/adapter.env from config/adapter.env.example, if absent.
#   A8   the system units (kvs-cam01/02, the kvs-cam@ template, the kvs-live@ template),
#        installed and never enabled, and the narrow sudo rule the services rely on.
#   A10  the USB outage-buffer stick, if one labelled vms-buffer is plugged in: fstab by UUID,
#        mounted, handed to you, sentinel. Never formatted: that stays a manual decision.
#
# It ends with proofs; every one must say PASS.
source "$(dirname "$(readlink -f "$0")")/lib.sh"

KVS_SDK_DIR="$KVS_SDK"
step "setup-system.sh for user '$SETUP_USER', repo $VMS_HOME"
[ -e "$VMS_HOME/config/adapter.env.example" ] || die "no repo at $VMS_HOME"
[ "$(id -u)" -eq 0 ] || sudo -v || die "sudo is needed for this script"

# ---------------------------------------------------------------- A1 stability (#1, #33, #47)
step "A1  earlyoom: kill compilers before the Pi locks up (FoundAndFixed #1)"
dpkg -s earlyoom >/dev/null 2>&1 || as_root apt-get install -y earlyoom
WANT='EARLYOOM_ARGS="-r 60 -m 20 -s 95 --avoid '"'"'(^|/)(sshd|systemd|systemd-.*|init)$'"'"' --prefer '"'"'(^|/)(cc1plus|cc1|g\+\+|gcc|cpp|as|ld|make|cmake)$'"'"'"'
if ! grep -qxF "$WANT" /etc/default/earlyoom 2>/dev/null; then
  printf '%s\n' "$WANT" | as_root tee /etc/default/earlyoom >/dev/null
  as_root systemctl restart earlyoom
fi
as_root systemctl enable --now earlyoom >/dev/null 2>&1
ok "earlyoom $(systemctl is-active earlyoom)"

step "A1  3 GB swapfile at low priority, swappiness 10"
if [ ! -e /swapfile ]; then
  as_root fallocate -l 3G /swapfile && as_root chmod 600 /swapfile && as_root mkswap /swapfile >/dev/null
fi
swapon --show=NAME --noheadings | grep -qx /swapfile || as_root swapon -p 10 /swapfile
grep -q '^/swapfile ' /etc/fstab || echo "/swapfile none swap sw,pri=10 0 0" | as_root tee -a /etc/fstab >/dev/null
echo "vm.swappiness=10" | as_root tee /etc/sysctl.d/99-low-swappiness.conf >/dev/null
as_root sysctl -q -p /etc/sysctl.d/99-low-swappiness.conf
ok "swap: $(swapon --show=NAME --noheadings | tr '\n' ' ')"

step "A1  persistent journal (FoundAndFixed #33)"
# Pi OS ships Storage=volatile in its own drop-in; a later /etc drop-in overrides it.
if [ ! -e /etc/systemd/journald.conf.d/90-vms-persistent.conf ]; then
  as_root mkdir -p /etc/systemd/journald.conf.d
  printf '[Journal]\nStorage=persistent\nSystemMaxUse=200M\nSystemMaxFileSize=20M\n' |
    as_root tee /etc/systemd/journald.conf.d/90-vms-persistent.conf >/dev/null
  as_root systemctl restart systemd-journald
  as_root journalctl --flush
fi
ok "journal storage: $(systemd-analyze cat-config systemd/journald.conf | grep '^Storage=' | tail -1)"
# Note: journald still writes info-level lines to disk only every 5 minutes, so a power cut
# loses the last minutes. The netwatch unit (setup-user.sh) logs the network with an fsync for
# that reason (FoundAndFixed #79).

step "A1  Wi-Fi power save off (FoundAndFixed #47)"
CONN=$(nmcli -t -f NAME,TYPE connection show --active 2>/dev/null | awk -F: '$2=="802-11-wireless"{print $1; exit}')
if [ -n "$CONN" ]; then
  [ "$(nmcli -g 802-11-wireless.powersave connection show "$CONN")" = disable ] ||
    as_root nmcli connection modify "$CONN" 802-11-wireless.powersave 2
  as_root iw dev wlan0 set power_save off
  ok "'$CONN': $(iw dev wlan0 get power_save)"
else
  note "no active Wi-Fi connection (Ethernet?) -- nothing to do"
fi

step "A1  EEPROM (reported only)"
EE=$(as_root rpi-eeprom-update 2>/dev/null || true)
if grep -q 'UPDATE AVAILABLE' <<<"$EE"; then
  warn "an EEPROM update is available: run 'sudo rpi-eeprom-update -a && sudo reboot' when convenient"
else
  ok "EEPROM up to date"
fi

# ---------------------------------------------------------------- A3 packages and lingering
step "A3  packages"
PKGS=(cmake m4 git build-essential pkg-config libssl-dev libcurl4-openssl-dev liblog4cplus-dev
      gstreamer1.0-plugins-base-apps gstreamer1.0-plugins-bad gstreamer1.0-plugins-good
      gstreamer1.0-plugins-ugly gstreamer1.0-tools libgstreamer1.0-dev
      libgstreamer-plugins-base1.0-dev
      gstreamer1.0-rtsp   # rtspclientsink: without it cam-01 never publishes (FoundAndFixed #40)
      v4l-utils jq curl unzip gpg ffmpeg)
MISSING=()
for p in "${PKGS[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || MISSING+=("$p"); done
if [ "${#MISSING[@]}" -gt 0 ]; then
  note "installing: ${MISSING[*]}"
  as_root apt-get update -qq
  as_root apt-get install -y "${MISSING[@]}"
fi
ok "all ${#PKGS[@]} packages installed"

step "A3  lingering for $SETUP_USER"
[ "$(loginctl show-user "$SETUP_USER" -p Linger --value 2>/dev/null)" = yes ] || as_root loginctl enable-linger "$SETUP_USER"
ok "Linger=$(loginctl show-user "$SETUP_USER" -p Linger --value)"

# ---------------------------------------------------------------- A7 deployment config
step "A7  /etc/adapter/adapter.env"
if [ ! -e /etc/adapter/adapter.env ]; then
  as_root install -D -m 644 "$VMS_HOME/config/adapter.env.example" /etc/adapter/adapter.env
  ok "installed from config/adapter.env.example (edit it for a different AWS account or Thing)"
else
  ok "present, left as it is"
fi

# ---------------------------------------------------------------- A8 system units + sudo rule
step "A8  system units (installed, never enabled: producers cost money while they run, §1.2)"
# Literal paths, written fresh each run: systemd expands no $VMS_HOME or ~ (FoundAndFixed #25,
# #30), so a moved clone just needs this script again.
unit() {  # unit NAME  -- the unit text on stdin
  local f="/etc/systemd/system/$1" tmp; tmp=$(mktemp)
  cat > "$tmp"
  if ! cmp -s "$tmp" "$f" 2>/dev/null; then as_root install -m 644 "$tmp" "$f"; note "wrote $f"; fi
  rm -f "$tmp"
}
for cam in 01 02; do
unit "kvs-cam${cam}.service" <<EOF
[Unit]
Description=KVS producer for cam-${cam}
After=network-online.target

[Service]
Type=simple
User=${SETUP_USER}
Environment=GST_PLUGIN_PATH=${KVS_SDK_DIR}/build
Environment=LD_LIBRARY_PATH=${KVS_SDK_DIR}/open-source/local/lib
ExecStart=${VMS_HOME}/adapter/bin/stream-cam${cam}.sh
Restart=on-failure
RestartSec=5
# The EOS watchdog's pipe (producer-lib.sh, FoundAndFixed #46).
RuntimeDirectory=vms-producer

[Install]
WantedBy=multi-user.target
EOF
done
# One instance per GUI-registered camera (kvs-cam@cam03), configured by the env file
# provision-camera.sh writes. Started by Start, never enabled (FoundAndFixed #81).
unit "kvs-cam@.service" <<EOF
[Unit]
Description=KVS producer for %i
After=network-online.target

[Service]
Type=simple
User=${SETUP_USER}
EnvironmentFile=/etc/adapter/channels/%i.env
Environment=GST_PLUGIN_PATH=${KVS_SDK_DIR}/build
Environment=LD_LIBRARY_PATH=${KVS_SDK_DIR}/open-source/local/lib
ExecStart=${VMS_HOME}/adapter/bin/stream-channel.sh
Restart=on-failure
RestartSec=5
RuntimeDirectory=vms-producer
CPUAccounting=true
MemoryAccounting=true

[Install]
WantedBy=multi-user.target
EOF
# The cloud page's Live view, on demand: agent.py starts it on `live`; it stops itself, exit 0,
# when the deadline the page keeps renewing has passed. No [Install]: never enabled.
unit "kvs-live@.service" <<EOF
[Unit]
Description=KVS live view for %i (on demand, stops at its deadline)
After=network-online.target

[Service]
Type=simple
User=${SETUP_USER}
Environment=LIVE_OF=%i
Environment=GST_PLUGIN_PATH=${KVS_SDK_DIR}/build
Environment=LD_LIBRARY_PATH=${KVS_SDK_DIR}/open-source/local/lib
ExecStart=${VMS_HOME}/adapter/bin/stream-channel.sh
Restart=on-failure
RestartSec=5
RuntimeDirectory=vms-live-%i
EOF
as_root systemctl daemon-reload
ok "kvs-cam01, kvs-cam02, kvs-cam@, kvs-live@ in /etc/systemd/system"

step "A8  sudo rule: exactly what the services run without a terminal (FoundAndFixed #34, #80)"
TMP=$(mktemp)
cat > "$TMP" <<EOF
# VideoSafeZone adapter (setup/setup-system.sh, LAUNCH.md A8): exactly the privileged actions its
# services run non-interactively -- producer Start/Stop and Unregister's disable (camera_control.py),
# live views (agent.py, live_view.py), and camera provisioning (admin GUI -> provision-camera.sh).
# Regex arguments need sudo >= 1.9.10.
${SETUP_USER} ALL=(root) NOPASSWD: /usr/bin/systemctl ^(start|stop|enable|disable) kvs-cam(@cam)?[0-9]{2}\\.service\$, \\
    /usr/bin/systemctl ^(start|stop) kvs-live@cam[0-9]{2}\\.service\$, \\
    ${VMS_HOME}/adapter/bin/provision-camera.sh ^cam-[0-9]{2} cam[0-9]{2}( no-start)?\$
EOF
/usr/sbin/visudo -qcf "$TMP" || die "the generated sudo rule doesn't parse -- not installed"
if ! as_root cmp -s "$TMP" /etc/sudoers.d/020_vms-adapter; then
  as_root install -m 0440 -o root -g root "$TMP" /etc/sudoers.d/020_vms-adapter
  note "installed /etc/sudoers.d/020_vms-adapter"
fi
rm -f "$TMP"
as_root visudo -qc || die "sudo configuration broken -- check /etc/sudoers.d"
ok "sudo rule installed and the whole configuration parses"

# ---------------------------------------------------------------- A10 USB stick
step "A10 outage-buffer stick (optional)"
DEV=$(lsblk -nrpo NAME,LABEL | awk -v l="$BUFFER_LABEL" '$2==l {print $1; exit}')
if [ -z "$DEV" ]; then
  note "no partition labelled '$BUFFER_LABEL' -- skipped (outage buffering then idles, #39)."
  note "to prepare a NEW stick (erases it):  sudo mkfs.ext4 -m 0 -L $BUFFER_LABEL /dev/sdX1"
else
  UUID=$(lsblk -nro UUID "$DEV")
  [ "$(lsblk -nro FSTYPE "$DEV")" = ext4 ] || die "$DEV is labelled $BUFFER_LABEL but isn't ext4"
  as_root mkdir -p "$BUFFER"
  grep -q "UUID=$UUID" /etc/fstab ||
    echo "UUID=$UUID  $BUFFER  ext4  defaults,noatime,nofail,x-systemd.device-timeout=10  0  2" | as_root tee -a /etc/fstab >/dev/null
  as_root systemctl daemon-reload
  mountpoint -q "$BUFFER" || as_root mount "$BUFFER"
  as_root chown "$SETUP_USER:$SETUP_USER" "$BUFFER"
  as_user mkdir -p "$BUFFER/live" "$BUFFER/outage"
  [ -e "$BUFFER/.vms-buffer-ok" ] || as_user touch "$BUFFER/.vms-buffer-ok"
  ok "$DEV ($UUID) mounted at $BUFFER"
  # A stick moved from another card may hold captures that still need uploading (D1).
  PENDING=$(grep -l pending-upload "$BUFFER"/outage/*/state.json 2>/dev/null | wc -l)
  [ "$PENDING" -eq 0 ] || note "$PENDING outage capture(s) waiting for upload; the uploader takes them once it runs"
fi

# ---------------------------------------------------------------- proofs
# Whether sudo lets the user run CMD with no password: sudo's own answer, not the command's
# exit status (stopping a unit that doesn't exist fails even when sudo allowed it). -k ignores
# a recently typed password, which would otherwise fake a pass (FoundAndFixed #34).
sudo_allowed() {   # output captured first: under pipefail, `sudo | grep` would report sudo's failure
  local out; out=$(as_user sudo -k -n "$@" 2>&1) || true
  ! grep -qi 'password is required' <<<"$out"
}
step "proofs"
proof "earlyoom active"                         systemctl is-active --quiet earlyoom
proof "swapfile in use"                         bash -c "swapon --show=NAME --noheadings | grep -qx /swapfile"
proof "journal persistent"                      bash -c "systemd-analyze cat-config systemd/journald.conf | grep '^Storage=' | tail -1 | grep -q persistent"
[ -n "$CONN" ] && proof "Wi-Fi power save off"  bash -c "iw dev wlan0 get power_save | grep -q off"
proof "GStreamer has rtspclientsink"            gst-inspect-1.0 rtspclientsink
proof "GStreamer has h264/h265timestamper"      bash -c "gst-inspect-1.0 h264timestamper && gst-inspect-1.0 h265timestamper"
proof "lingering on"                            bash -c "[ \"\$(loginctl show-user $SETUP_USER -p Linger --value)\" = yes ]"
proof "/etc/adapter/adapter.env"                test -s /etc/adapter/adapter.env
proof "system units known to systemd"           bash -c "systemctl cat kvs-cam01.service kvs-cam02.service kvs-cam@.service kvs-live@.service"
proof "no producer enabled (cost rule)"         bash -c "! ls /etc/systemd/system/multi-user.target.wants/ | grep -q '^kvs-'"
proof "sudo rule allows a producer stop"        sudo_allowed systemctl stop kvs-cam99.service
proof "sudo rule allows a live-view stop"       sudo_allowed systemctl stop kvs-live@cam99.service
proof "sudo rule refuses anything else"         not sudo_allowed true
[ -n "$DEV" ] && proof "stick mounted with sentinel" buffer_ready
verdict "setup-system.sh"
