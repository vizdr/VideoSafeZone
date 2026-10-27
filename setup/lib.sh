# Shared by every setup/*.sh script -- sourced, never run. (LAUNCH.md Part A)
#
# What it gives the scripts:
#   - VMS_HOME, the repo root, found from this file's location (never hardcoded, FoundAndFixed #25);
#   - SETUP_USER / SETUP_HOME: the person the stack runs as, also when a script is started with
#     `sudo` in front (then $USER and $HOME are root's, and a unit file written with them would
#     run as root from /root -- what the first run of the old setup script did);
#   - as_root CMD...: sudo when needed, CMD directly when already root;
#   - as_user CMD...: CMD as SETUP_USER, also from a root shell;
#   - step / ok / note / warn / die: one output style for all scripts;
#   - proof DESCRIPTION CMD...: runs CMD, prints PASS or FAIL with the description, and counts
#     failures in PROOF_FAILS, so a script can end with a one-line verdict;
#   - the versions every script must agree on.
#
# Every script built on this is idempotent: it checks before it changes, and running it again
# on a finished Pi changes nothing.

set -euo pipefail

VMS_HOME="${VMS_HOME:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)}"
SETUP_DIR="$VMS_HOME/setup"

if [ "$(id -u)" -eq 0 ]; then
  if [ -z "${SUDO_USER:-}" ] || [ "$SUDO_USER" = root ]; then
    echo "Run this as your normal user (it asks for sudo itself):  bash $0" >&2
    exit 1
  fi
  SETUP_USER=$SUDO_USER
  SETUP_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
  SETUP_USER=$(id -un)
  SETUP_HOME=$HOME
fi
SETUP_UID=$(id -u "$SETUP_USER")

# --- versions and places (change them here, on purpose, never by accident) ----------------
MEDIAMTX_VERSION=v1.20.1        # what mediamtx/mediamtx.yml was written against (LAUNCH A5)
KVS_SDK_TAG=v3.6.0              # the SDK release patches 1-5 were made and verified against
KVS_SDK="$VMS_HOME/vendor/amazon-kinesis-video-streams-producer-sdk-cpp"
VENV="$VMS_HOME/venv-adapter"
BUFFER=/mnt/vms-buffer          # the USB stick, found by its label (LAUNCH A10)
BUFFER_LABEL=vms-buffer
# Survives a fresh SD card because the stick stays in the Pi: the SDK build cache and, when
# you choose to keep it there, a backup of the device identity (setup-identity.sh backup).
SETUP_CACHE="$BUFFER/.setup-cache"

# --- output -----------------------------------------------------------------------------
if [ -t 1 ]; then _CB=$'\e[1m' _CG=$'\e[32m' _CY=$'\e[33m' _CR=$'\e[31m' _CN=$'\e[0m'
else _CB= _CG= _CY= _CR= _CN=; fi
step() { printf '\n%s== %s%s\n' "$_CB" "$*" "$_CN"; }
ok()   { printf '   %sok%s   %s\n' "$_CG" "$_CN" "$*"; }
note() { printf '   ..   %s\n' "$*"; }
warn() { printf '   %s!!%s   %s\n' "$_CY" "$_CN" "$*" >&2; }
die()  { printf '%sERROR:%s %s\n' "$_CR" "$_CN" "$*" >&2; exit 1; }

PROOF_FAILS=0
proof() {   # proof "what is being proved" cmd args...
  local what="$1"; shift
  if "$@" >/dev/null 2>&1; then
    printf '   %sPASS%s %s\n' "$_CG" "$_CN" "$what"
  else
    printf '   %sFAIL%s %s\n' "$_CR" "$_CN" "$what"
    PROOF_FAILS=$((PROOF_FAILS + 1))
  fi
}
not() { ! "$@"; }   # for proofs of a refusal:  proof "..." not CMD
verdict() {  # verdict "script name": the last line of a script
  if [ "$PROOF_FAILS" -eq 0 ]; then
    printf '\n%s%s: done, every proof passed.%s\n' "$_CG" "$1" "$_CN"
  else
    printf '\n%s%s: %d proof(s) failed -- see FAIL above. Re-running is safe.%s\n' "$_CR" "$1" "$PROOF_FAILS" "$_CN"
    return 1
  fi
}

# --- privilege helpers ----------------------------------------------------------------------
as_root() { if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi; }
as_user() {
  if [ "$(id -u)" -eq 0 ]; then
    runuser -u "$SETUP_USER" -- env HOME="$SETUP_HOME" XDG_RUNTIME_DIR="/run/user/$SETUP_UID" "$@"
  else
    "$@"
  fi
}
# systemctl --user for SETUP_USER, from either kind of shell (needs lingering, setup-system.sh).
user_systemctl() { as_user systemctl --user "$@"; }

# The stick, mounted and carrying its sentinel -- the only state in which anything may write
# to it (an unplugged stick leaves an empty mountpoint on the SD card, OUTAGE.md / #39).
buffer_ready() { mountpoint -q "$BUFFER" && [ -e "$BUFFER/.vms-buffer-ok" ] && [ -w "$BUFFER" ]; }
