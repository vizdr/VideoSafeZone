#!/usr/bin/env bash
# Lock cam-01's exposure / white balance / focus before streaming starts (guide §2.3).
# The camera is found by detection and the control values come from
# /etc/adapter/cameras/cam01.env when present -- defaults below are the PW310 tuning.
set -e
VMS_HOME="${VMS_HOME:-$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)}"
source "${VMS_HOME}/adapter/bin/detect-hw.sh"
camera_setup cam01                      # sets CAM, or exits: no camera, or ambiguous

: "${V4L2_MODE_CTRLS:=auto_exposure=1,exposure_dynamic_framerate=0,white_balance_automatic=0,focus_automatic_continuous=0}"
: "${V4L2_VALUE_CTRLS:=exposure_time_absolute=250,white_balance_temperature=4600,focus_absolute=120}"

# Two passes, modes first: a value like exposure_time_absolute is rejected while its
# automatic mode is still on.
IFS=, read -ra MODES  <<< "$V4L2_MODE_CTRLS"
IFS=, read -ra VALUES <<< "$V4L2_VALUE_CTRLS"
v4l2-ctl -d "$CAM" "${MODES[@]/#/--set-ctrl=}"
v4l2-ctl -d "$CAM" "${VALUES[@]/#/--set-ctrl=}"
