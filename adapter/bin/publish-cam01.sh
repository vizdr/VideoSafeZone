#!/usr/bin/env bash
# $VMS_HOME if exported (interactive shells, via ~/.bashrc); otherwise the repo root
# this script lives in (systemd units do not read ~/.bashrc).
VMS_HOME="${VMS_HOME:-$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)}"
source "${VMS_HOME}/adapter/bin/detect-hw.sh"
camera_setup cam01 || exit 1            # sets CAM (detected, or pinned in /etc/adapter/cameras/cam01.env)
: "${CAPS:=image/jpeg,width=1280,height=720,framerate=30/1}"

# Audio goes into MediaMTX as LPCM, deliberately uncompressed. Encoding it to AAC here
# instead is the obvious move and it is a trap: rtspclientsink payloads AAC as MPEG-4
# LATM, and the round-trip mangles the codec private data into a form KVS will ingest but
# refuses to play back. stream-cam01.sh carries the full account. LPCM at 16 kHz mono is
# 256 kbps over loopback, which costs nothing here and never leaves the Pi.
#
# Guide 18.2 suggests bypassing MediaMTX entirely for A/V; that advice predates MediaMTX
# becoming the hub, and following it now would cost the local preview, the Start/Stop
# layer, and the single-producer-per-camera model. MediaMTX carries two tracks fine.
#
# The microphone is the sound card on the same USB device as $CAM, found through sysfs
# (detect-hw.sh), and addressed by name, never hw:N -- card numbers move on reboot
# (guide 18.1). Matching by USB parent rather than by card name matters: ALSA calls many
# webcams' mics plain "Webcam", so a second camera would make the name ambiguous.
# The PW310 capture device is STEREO-ONLY (ALSA reports CHANNELS: 2, a fixed value not a
# range), so `-c 1` is rejected outright; audioconvert does the downmix instead.
#
# 16 kHz, not 48 kHz, and the reason is not audio quality -- see stream-cam02.sh for the
# full account. Short version: kvssink synthesises the DTS that GStreamer audio buffers
# lack, using a counter SHARED with the video track, so an audio frame rate higher than
# the video frame rate makes the synthesised timestamps run backwards and KVS drops the
# frames. voaacenc emits 1024-sample frames, so frame duration is 1024/rate: at 48 kHz
# that is 21ms against a 66.7ms video frame, which fails; 16 kHz gives 64ms. Verified by
# measurement below, not by theory -- if you change the rate, recount the rejects.
AUDIO_DEV="$(alsa_card_for_video "$CAM" || true)"

# Read once at startup; see adapter/bin/camera-audio.py for why this is never re-read.
# `|| true` so an unreachable registry degrades to video-only instead of leaving cam-01
# with no feed at all.
AUDIO_ENV="$(${VMS_HOME}/venv-adapter/bin/python3 \
             ${VMS_HOME}/adapter/bin/camera-audio.py cam-01 || true)"
eval "${AUDIO_ENV}"
# A pinned audioDevice is an OVERRIDE, not an instruction to fail, and it is honoured only if
# that card is present. The registry row is shared with VMS, whose USB scan writes this pin
# (VMS guide §22), and ALSA card ids are not stable identities: many webcams' mics are called
# plain "Webcam", and a second one becomes "Webcam_1" in plug-in order. Video and audio share
# ONE pipeline here, so alsasrc failing to open a stale device would take the *video* down too.
# When the pin does not resolve, discovery wins and the reason is logged.
if [ -n "${AUDIO_DEVICE:-}" ]; then
  pin_card="${AUDIO_DEVICE#hw:CARD=}"; pin_card="${pin_card%%,*}"
  if [ -d "/proc/asound/${pin_card}" ]; then
    AUDIO_DEV="${AUDIO_DEVICE}"
  else
    echo "publish-cam01: registry pins audioDevice=${AUDIO_DEVICE} but ALSA card '${pin_card}'" \
         "is not present -- ignoring the pin and using ${AUDIO_DEV:-no microphone}" >&2
  fi
fi
if [ "${AUDIO:-off}" = "on" ] && [ -z "${AUDIO_DEV}" ]; then
  # A stale audioEnabled on a camera with no microphone must not become a pipeline that
  # waits forever for audio (the same reason camera-audio.py gates on audioCapable).
  echo "publish-cam01: audio requested but $CAM has no microphone -- video only" >&2
  AUDIO=off
fi

# Run the publisher, and never let it sit there doing nothing (FoundAndFixed.md #52).
#
# gst-launch has stopped producing without exiting twice in VMS -- a second after boot, and
# after an hour of normal running -- with no error, no EOS, and the unit `active`. Nothing in
# its own output marks the moment, so the watchdog reads the one signal that does: MediaMTX's
# bytesReceived for the path (#50's rule). No advance for PUBLISH_STALL_SEC kills the
# pipeline. Any end of the pipeline, a clean EOS included, exits non-zero so
# Restart=on-failure brings it back -- the clean-exit gap that #44 closed for the producers.
# An unreachable API is "unknown", not "stalled", and never kills.
#
# Not `exec`, so this shell stays the unit's main process; a Stop signals the whole cgroup,
# which takes gst-launch with it (the same reasoning as producer_run in producer-lib.sh).
#
# bytesReceived is the whole path, so with audio on it can't see the *video* stopping: in
# VMS the video hung for over an hour while the microphone's LPCM (32 kB/s) kept the count
# moving, and cam-01's producer, with no video to start a fragment, uploaded nothing
# (FoundAndFixed.md #75). So the video chain ends in a GStreamer `watchdog`: no video buffer
# for PUBLISH_VIDEO_WATCHDOG_MS is a pipeline error, gst-launch exits, and the unit restarts.
# Should a wedged element keep gst-launch from exiting, the error has still stopped the RTSP
# sender, so the byte check below catches it 30 s later.
PUBLISH_VIDEO_WATCHDOG_MS=10000
PUBLISH_CHECK_SEC=10
PUBLISH_STALL_SEC=30
publish_run() {
  gst-launch-1.0 "$@" &
  local pid=$! last= now stalled=0
  while sleep "${PUBLISH_CHECK_SEC}"; do
    kill -0 "$pid" 2>/dev/null || break
    now="$(curl -s --max-time 3 http://127.0.0.1:9997/v3/paths/get/cam01 \
           | sed -n 's/.*"bytesReceived":\([0-9]*\).*/\1/p')"
    [ -n "$now" ] || continue
    if [ "$now" = "$last" ]; then
      stalled=$(( stalled + PUBLISH_CHECK_SEC ))
      if [ "$stalled" -ge "$PUBLISH_STALL_SEC" ]; then
        echo "publish-cam01: MediaMTX bytesReceived stuck at ${now} for ${stalled}s --" \
             "the pipeline hung (FoundAndFixed.md #52); killing it" >&2
        # SIGKILL: a pipeline wedged this way has no reason to honour a clean shutdown.
        kill -9 "$pid" 2>/dev/null
        break
      fi
    else
      stalled=0
    fi
    last="$now"
  done
  local rc=0
  wait "$pid" 2>/dev/null || rc=$?
  echo "publish-cam01: pipeline ended (status ${rc}) -- exiting non-zero so systemd restarts it" >&2
  exit 1
}

VIDEO_CHAIN="v4l2src device=$CAM ! \
  ${CAPS} ! \
  v4l2jpegdec ! videorate drop-only=true ! video/x-raw,framerate=15/1 ! \
  v4l2convert ! video/x-raw,format=I420 ! \
  v4l2h264enc extra-controls=controls,video_bitrate=1000000,h264_i_frame_period=30,repeat_sequence_header=1 ! \
  video/x-h264,level=(string)4,profile=(string)high ! \
  h264parse config-interval=-1 ! watchdog timeout=${PUBLISH_VIDEO_WATCHDOG_MS}"

if [ "${AUDIO:-off}" = "on" ]; then
  echo "publish-cam01: audio ENABLED (${AUDIO_DEV} -> LPCM 16kHz mono)"
  publish_run -v \
    ${VIDEO_CHAIN} ! queue ! rtsp.sink_0 \
    alsasrc device="${AUDIO_DEV}" provide-clock=false do-timestamp=true \
    ! audioconvert ! audioresample \
    ! audio/x-raw,rate=16000,channels=1,format=S16BE ! queue ! rtsp.sink_1 \
    rtspclientsink name=rtsp location=rtsp://127.0.0.1:8554/cam01 protocols=tcp
fi

# Video-only: byte-for-byte the pipeline that ran before audio existed, plus the video
# watchdog (#75).
echo "publish-cam01: audio disabled (video only)"
publish_run -v \
  v4l2src device="$CAM" ! \
  "$CAPS" ! \
  v4l2jpegdec ! videorate drop-only=true ! video/x-raw,framerate=15/1 ! \
  v4l2convert ! video/x-raw,format=I420 ! \
  v4l2h264enc extra-controls="controls,video_bitrate=1000000,h264_i_frame_period=30,repeat_sequence_header=1" ! \
  "video/x-h264,level=(string)4,profile=(string)high" ! \
  h264parse config-interval=-1 ! watchdog timeout=${PUBLISH_VIDEO_WATCHDOG_MS} ! \
  rtspclientsink location=rtsp://127.0.0.1:8554/cam01 protocols=tcp
