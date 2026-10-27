#!/usr/bin/env bash
# Generic passthrough launcher for any GUI-registered camera -- reads CAMERA_ID and
# MEDIAMTX_PATH from the environment (set per-instance by
# /etc/adapter/channels/<mediamtx-path>.env via kvs-cam@.service's EnvironmentFile=)
# instead of being duplicated per camera the way stream-cam01.sh/stream-cam02.sh were.
# Passthrough-only (§16.2.1/§16.3: WS-Discovery only ever finds networked ONVIF cameras,
# which this project already treats as pure passthrough, no transcode stage).
#
# The same script is a camera's live-view producer: kvs-live@cam03.service sets LIVE_OF=cam03
# (its instance name), and it then pushes the camera's live-view path (cam03_live, the sub stream)
# -- or the recorded stream's path for a camera with no sub stream -- to the KVS stream
# cam-03-live, until the deadline the agent keeps moving forward passes (agent.py set_live,
# producer-lib.sh). It needs no per-camera env file, so Live also works for a camera
# registered from the other SD card. (VMS's form -- an env file with MEDIAMTX_PATH=cam03_live,
# KVS_STREAM and LIVE=1 -- still works.)
set -e
if [ -n "${LIVE_OF:-}" ]; then
  [[ "$LIVE_OF" =~ ^cam[0-9]{2}$ ]] || { echo "stream-channel: invalid LIVE_OF '$LIVE_OF'" >&2; exit 1; }
  CAMERA_ID="cam-${LIVE_OF#cam}"
  MEDIAMTX_PATH="${LIVE_OF}_live"          # replaced below by the path stream-codec.py reads
  KVS_STREAM="${CAMERA_ID}-live"
  LIVE=1
  export LIVE_DEADLINE_FILE="/run/user/$(id -u)/vms-live/${LIVE_OF}"
fi
: "${CAMERA_ID:?CAMERA_ID must be set}"
: "${MEDIAMTX_PATH:?MEDIAMTX_PATH must be set}"
KVS_STREAM="${KVS_STREAM:-${CAMERA_ID}}"
CODEC_ARGS=()
[ "${LIVE:-0}" = 1 ] && CODEC_ARGS=(--live)
# The watchdog FIFO is named after the label, and every kvs-cam@ instance shares
# /run/vms-producer: one label per instance, or a camera's recording and live producers,
# started together, could open each other's pipe.
LABEL="stream-channel-${MEDIAMTX_PATH}"
[ -n "${LIVE_OF:-}" ] && LABEL="stream-live-${LIVE_OF}"   # its own label even on the recorded path
# $VMS_HOME if exported (interactive shells, via ~/.bashrc); otherwise the repo root
# this script lives in (systemd units do not read ~/.bashrc).
VMS_HOME="${VMS_HOME:-$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)}"

source "${VMS_HOME}/adapter/bin/adapter-config.sh"   # AWS_REGION, THING_NAME, IOT_* (/etc/adapter/adapter.env)
source "${VMS_HOME}/adapter/bin/producer-lib.sh"     # producer_run
CERTS="${VMS_HOME}/certs"

# Resolved from what MediaMTX actually receives, not assumed: a GUI-registered camera is
# whatever the operator plugged in, and H.265 is common on exactly these cameras. See
# stream-cam02.sh for why a missing source is a deliberate failure here. The same call reports
# the audio track (AUDIO_TRACK), whose G.711 law ONVIF can't tell and a camera's own audio page can
# change (cam-02 went A-law -> mu-law, FoundAndFixed.md #71); cam-03 is mu-law, cam-04 A-law.
VIDEO_ENV="$(${VMS_HOME}/venv-adapter/bin/python3 \
             ${VMS_HOME}/adapter/bin/stream-codec.py "${CODEC_ARGS[@]}" "${CAMERA_ID}")" \
  || { echo "stream-channel(${KVS_STREAM}): no H.264/H.265 video on ${MEDIAMTX_PATH} from MediaMTX -- exiting for systemd to retry" >&2; exit 1; }
eval "${VIDEO_ENV}"
VIDEO_CHAIN="$(video_depay_chain "${VIDEO_CODEC}")"
[ -n "${LIVE_OF:-}" ] && MEDIAMTX_PATH="${VIDEO_PATH}"
if live_deadline_passed; then
  # Started (or restarted after a failure) with no viewer left: nothing to push.
  echo "stream-channel(${KVS_STREAM}): live-view deadline already passed -- not starting" >&2
  exit 0
fi

# Whether to record audio: the registry's audioCapable (found at discovery) and audioEnabled
# (the user's switch), read once at start like stream-cam02.sh -- see camera-audio.py for why a
# change waits for the next Start. `|| true`: an unreadable registry means video-only, never no
# producer.
AUDIO_ENV="$(${VMS_HOME}/venv-adapter/bin/python3 \
             ${VMS_HOME}/adapter/bin/camera-audio.py "${CAMERA_ID}" || true)"
eval "${AUDIO_ENV}"

case "${AUDIO_TRACK:-none}" in
  pcma) AUDIO_DEPAY="rtppcmadepay ! alawdec" ;;
  pcmu) AUDIO_DEPAY="rtppcmudepay ! mulawdec" ;;
  *)    AUDIO_DEPAY="" ;;
esac
if [ "${AUDIO:-off}" = "on" ] && [ -z "${AUDIO_DEPAY}" ]; then
  # Never start an audio branch that can't deliver: kvssink collects across its pads, so an
  # audio pad that never gets a buffer stalls the video as well (guide §18).
  echo "stream-channel(${KVS_STREAM}): audio is on, but MediaMTX receives ${AUDIO_TRACK:-no audio}, not G.711 -- video only" >&2
  AUDIO=off
fi

KVSSINK="kvssink name=kvs stream-name=${KVS_STREAM} aws-region=${AWS_REGION} \
  iot-certificate=iot-certificate,endpoint=${IOT_CRED_ENDPOINT},cert-path=${CERTS}/adapter.cert.pem,key-path=${CERTS}/adapter.private.key,ca-path=${CERTS}/cacert.pem,role-aliases=${IOT_ROLE_ALIAS},iot-thing-name=${THING_NAME}"

if [ "${AUDIO:-off}" = "on" ]; then
  # stream-cam02.sh's chain, with the depayloader chosen by law; its comments carry the reasons
  # (AAC because KVS playback refuses G.711, raw not adts, no resampling up). 8 kHz AAC frames
  # are 128 ms, longer than one video frame at 10-25 fps, which is guide §18.3's rule. Measured
  # with zero rejects at 15 fps (cam-02) and 20 fps (cam-03, FoundAndFixed.md #59); 25 fps with
  # audio is unmeasured -- count 0x30000005 rejects there.
  echo "stream-channel(${KVS_STREAM}): audio ENABLED (${AUDIO_TRACK} -> AAC 8kHz), video ${VIDEO_CODEC} from ${MEDIAMTX_PATH}"
  producer_run "${LABEL}" -v \
    rtspsrc location="rtsp://127.0.0.1:8554/${MEDIAMTX_PATH}" protocols=tcp latency=200 name=src \
    src. ! application/x-rtp,media=video ! queue \
    ! ${VIDEO_CHAIN} ! queue ! kvs.video_0 \
    src. ! application/x-rtp,media=audio ! queue \
    ! ${AUDIO_DEPAY} ! audioconvert \
    ! audio/x-raw,rate=8000,channels=1 \
    ! ${AAC_ENC} ! aacparse \
    ! audio/mpeg,mpegversion=4,stream-format=raw ! queue ! kvs.audio_0 \
    ${KVSSINK}
fi

# Video-only, but the audio pad is still linked -- to a fakesink. A GUI-registered ONVIF
# camera very often has a G.711 track that MediaMTX re-serves, and an rtspsrc pad nobody
# links can abort the whole pipeline with "streaming stopped, reason not-linked (-1)"
# depending on which pad is exposed first (FoundAndFixed.md #43). The branch is inert if the
# source has no audio track.
echo "stream-channel(${KVS_STREAM}): audio disabled (video only), video ${VIDEO_CODEC} from ${MEDIAMTX_PATH}"
producer_run "${LABEL}" -v \
  rtspsrc location="rtsp://127.0.0.1:8554/${MEDIAMTX_PATH}" protocols=tcp latency=200 name=src \
  src. ! application/x-rtp,media=video ! queue \
  ! ${VIDEO_CHAIN} \
  ! ${KVSSINK} \
  src. ! application/x-rtp,media=audio ! queue ! fakesink sync=false async=false
