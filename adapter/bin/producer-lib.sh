# Shared by the KVS producer scripts (stream-cam01.sh, stream-cam02.sh, stream-channel.sh).
#
#   source "${VMS_HOME}/adapter/bin/producer-lib.sh"
#   VIDEO_CHAIN="$(video_depay_chain "$VIDEO_CODEC")"
#   producer_run stream-cam02 -v rtspsrc ... ! kvssink ...

# Depayload/parse chain for a passthrough producer's video track, chosen by the codec
# MediaMTX is actually receiving (adapter/bin/stream-codec.py). cam-01 is H.264-only and
# keeps its own chain (measurements/codec-phase0.md §6).
#
# Both chains end in caps that make the parser emit codec_data, because kvssink sends codec
# private data only from the caps' codec_data field (gstkvssink.cpp). Its H.265 pad template
# pins no stream-format, so without `stream-format=hvc1` an Annex-B H.265 stream would
# ingest fine and never play back (MissingCodecPrivateData) -- the same ingest-vs-playback
# trap as FoundAndFixed.md #16. The h264 chain is token for token what every producer ran
# before H.265 existed.
video_depay_chain() {
  case "$1" in
    h264) echo "rtph264depay ! h264parse config-interval=-1 ! video/x-h264,stream-format=avc,alignment=au" ;;
    h265) echo "rtph265depay ! h265parse config-interval=-1 ! video/x-h265,stream-format=hvc1,alignment=au" ;;
    *)    echo "producer-lib: unsupported video codec '$1'" >&2; return 1 ;;
  esac
}

# Run the producer pipeline; never return success. A producer must not end on its own:
# when MediaMTX closes the session -- the camera dropped off the network, its codec was
# switched, the path's source changed, cam-01's publisher restarted -- rtspsrc reports a
# clean end-of-stream and gst-launch exits 0. Under `exec` that was the unit's exit status,
# and Restart=on-failure takes 0 for a deliberate stop: the cloud stream stayed down with
# the unit merely "inactive", indistinguishable from the user pressing Stop
# (FoundAndFixed.md #44). A real Stop never gets here -- systemd signals the whole unit --
# so any return is a failure, reported non-zero for systemd to restart. (With `set -e`, a
# non-zero gst-launch exit ends the script at once with its own status; same outcome.)
producer_run() {
  local label="$1"; shift
  gst-launch-1.0 "$@"
  echo "${label}: pipeline ended (source closed the session) -- exiting non-zero so systemd restarts it" >&2
  exit 1
}
