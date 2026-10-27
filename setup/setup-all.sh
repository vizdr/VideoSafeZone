#!/usr/bin/env bash
# setup-all.sh -- a fresh Raspberry Pi OS card to a running adapter, in order. (LAUNCH.md Part A)
#
#   git clone https://github.com/vizdr/VideoSafeZone.git ~/Projects/VideoSafeZone
#   bash ~/Projects/VideoSafeZone/setup/setup-all.sh
#
# Runs, stopping at the first failure (every step is safe to re-run, so just run this again):
#   1. setup-system.sh    root steps; asks for your password once
#   2. setup-user.sh      venv, MediaMTX, AWS CLI, unit files
#   3. setup-sdk.sh       the KVS SDK: restored from the stick's cache in seconds, or built in the
#                         background (1.5-2.5 h) while the rest goes on
#   4. setup-identity.sh  the device certificate: kept, or restored from the stick's backup. With
#                         neither it stops and tells you the two ways to get one.
#   5. setup-launch.sh    everything started and proved; producers stay stopped (cost rule)
#
# What it can't do for you, once per account rather than per card:
#   - an identity: the first time, `aws login --remote` and `setup-identity.sh create`, then
#     `setup-identity.sh backup` so every later fresh card restores it from the stick;
#   - formatting a new stick (it would erase it): see setup-system.sh's A10 message.
source "$(dirname "$(readlink -f "$0")")/lib.sh"
[ "$(id -u)" -ne 0 ] || die "run this as your normal user (it asks for sudo itself):  bash $0"
cd "$VMS_HOME"
START=$SECONDS
run() { printf '\n%s######## %s%s\n' "$_CB" "$*" "$_CN"; bash "$SETUP_DIR/$1" "${@:2}" || die "$1 failed -- fix what it says, then run setup-all.sh again"; }
run setup-system.sh
run setup-user.sh
run setup-sdk.sh
run setup-identity.sh
run setup-launch.sh
printf '\n%s######## setup finished in %d min%s\n' "$_CB" "$(( (SECONDS - START) / 60 ))" "$_CN"
if ! bash "$SETUP_DIR/setup-sdk.sh" status | grep -q ' built'; then
  note "the KVS SDK is still building in the background: the stack runs, but Start needs the plugin."
  note "when it is done ('bash setup/setup-sdk.sh wait' blocks until then), nothing else is needed."
fi
bash "$SETUP_DIR/setup-check.sh" || true
