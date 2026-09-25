#!/usr/bin/env bash
# Local hardware detection -- finds the Pi's USB camera, the microphone that belongs to it,
# the outage-buffer stick and the isolated CPU cores at every start, instead of scripts
# carrying one Pi's device names. Two uses:
#
#   source "${VMS_HOME}/adapter/bin/detect-hw.sh"   # functions, for the camera scripts
#   adapter/bin/detect-hw.sh --print [cam01]        # report what would be used (LAUNCH.md A9)
#
# Rules, so detection can't quietly pick the wrong device: exactly one match is used;
# none is an error; several is an error that lists them and asks for CAM_MATCH -- never a
# guess. Per-camera overrides live in /etc/adapter/cameras/<mediamtx-path>.env (template:
# config/cameras/cam01.env.example); every choice is logged to stderr, i.e. the journal.

source "$(dirname "${BASH_SOURCE[0]}")/env-file.sh"

ADAPTER_CAMERAS_DIR="${ADAPTER_CAMERAS_DIR:-/etc/adapter/cameras}"
V4L_BYID_DIR="${V4L_BYID_DIR:-/dev/v4l/by-id}"   # overridable only so the ambiguity path can be tested

# [match] -> the persistent /dev/v4l/by-id path of the one MJPG-capable capture node.
# Filtering on MJPG also drops UVC metadata nodes (the PW310's -video-index1 lists no
# formats at all), so the right node is found without assuming "index0".
find_uvc_camera() {
  local match="${1:-}" dev
  local found=()
  for dev in "$V4L_BYID_DIR"/*-video-index*; do
    [ -e "$dev" ] || continue
    [ -n "$match" ] && [[ "$(basename "$dev")" != *"$match"* ]] && continue
    v4l2-ctl -d "$dev" --list-formats 2>/dev/null | grep -q "'MJPG'" || continue
    found+=("$dev")
  done
  case ${#found[@]} in
    1) echo "${found[0]}" ;;
    0) echo "detect-hw: no MJPG-capable USB camera${match:+ matching '$match'} under $V4L_BYID_DIR" >&2
       return 1 ;;
    *) echo "detect-hw: ${#found[@]} MJPG-capable cameras -- set CAM_MATCH to choose one:" >&2
       printf '  %s\n' "${found[@]}" >&2
       return 1 ;;
  esac
}

# <video device> -> hw:CARD=<id>,DEV=0 of the sound card on the SAME USB device. Matching
# by sysfs parent, not by card name: ALSA calls many webcams' mics just "Webcam", and a
# second one becomes "Webcam_1" in plug-in order (guide 18.1: never hw:N either).
alsa_card_for_video() {
  local node usb card
  node="$(basename "$(readlink -f "$1")")"
  usb="$(dirname "$(readlink -f "/sys/class/video4linux/$node/device")")"
  [[ "$usb" == */usb* ]] || return 1       # only USB cameras carry their own microphone
  for card in /sys/class/sound/card*; do
    [ "$(dirname "$(readlink -f "$card/device")")" = "$usb" ] || continue
    echo "hw:CARD=$(cat "$card/id"),DEV=0"
    return 0
  done
  return 1
}

# Mountpoint of the outage-buffer stick, found by the filesystem label README §6 gives it.
buffer_mount() {
  findmnt -n -o TARGET -S LABEL=vms-buffer 2>/dev/null
}

# Cores reserved from the scheduler (isolcpus, guide §1.4); empty when none are.
isolated_cpus() {
  cat /sys/devices/system/cpu/isolated 2>/dev/null
}

# <mediamtx-path>: load that camera's overrides and set CAM to its video device --
# CAM_DEVICE if the file pins one, otherwise the one camera matching CAM_MATCH.
camera_setup() {
  load_env_file "$ADAPTER_CAMERAS_DIR/$1.env"
  if [ -n "${CAM_DEVICE:-}" ]; then
    CAM="$CAM_DEVICE"
    [ -e "$CAM" ] || { echo "detect-hw: $1: CAM_DEVICE=$CAM does not exist" >&2; return 1; }
  else
    CAM="$(find_uvc_camera "${CAM_MATCH:-}")" || return 1
  fi
  echo "detect-hw: $1 video device: $CAM" >&2
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  [ "${1:-}" = "--print" ] || { echo "usage: $0 --print [mediamtx-path, default cam01]" >&2; exit 2; }
  path="${2:-cam01}"
  echo "config file   : $ADAPTER_CAMERAS_DIR/$path.env $([ -r "$ADAPTER_CAMERAS_DIR/$path.env" ] && echo '(present)' || echo '(absent -- defaults)')"
  if camera_setup "$path" 2>/dev/null; then
    echo "video device  : $CAM -> $(readlink -f "$CAM")"
    echo "audio device  : $(alsa_card_for_video "$CAM" || echo 'none (no microphone on this camera)')"
  else
    camera_setup "$path"                   # again, to show the reason
    echo "video device  : NOT FOUND"; rc=1
  fi
  buf="$(buffer_mount)"
  echo "buffer mount  : ${buf:-not mounted (no filesystem LABEL=vms-buffer mounted)}"
  cpus="$(isolated_cpus)"
  echo "isolated CPUs : ${cpus:-none (no isolcpus -- pinning protects nothing)}"
  exit "${rc:-0}"
fi
