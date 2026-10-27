"""Live view on an ONVIF camera's sub stream, whatever stream is recorded (ported from VMS).

Recording takes the stream chosen in the admin GUI (`rtspUrl`, MediaMTX path `cam03`, KVS
stream `cam-03`). Live view -- the admin GUI's local preview and the cloud page's Live player --
shows the sub stream when the camera has one: far less data to watch, and a browser decodes
800x448 H.265 more readily than 2560x1440.

When the recorded stream *is* the sub stream, or the camera has only one, live view uses the
recording path and stream. Only a camera recording its main stream gets a separate live view,
with derived names (camera_control.live_*):
  - registry `liveRtspUrl`: the sub stream's URI, with the registered URI's credentials. Its
    presence is what every reader goes by (sync_mediamtx_paths.py, rematch_cameras.py, the admin
    GUI's preview, get-hls-url's view=live). The registry row is shared with VMS, which reads the
    field the same way;
  - MediaMTX path `cam03_live`, pulling continuously like any camera path: the live producer's
    stream-codec.py needs the path's tracks before anyone reads it, so not on demand. Local,
    so it costs nothing in AWS;
  - KVS stream `cam-03-live`, pushed only while someone watches: kvs-live@cam03.service, started
    by the cloud page's Live control through agent.py and stopped by its own deadline
    (stream-channel.sh LIVE_OF). Unlike VMS, it does not follow the recording producer, so no
    per-camera unit is provisioned here.

Setting it up writes the registry first, then MediaMTX, the order a MediaMTX restart rebuilds
things in; removing goes the other way. Rows change field by field.
"""
import subprocess

import camera_control
import mediamtx_api
import onvif_media2

# KVS serves HLS only from a stream that keeps data (retention 0 is ingest-only). One hour is
# the minimum and plenty: nothing but the live player reads this stream.
LIVE_RETENTION_HOURS = 1


def live_uri(streams: list, rtsp_url: str) -> str | None:
    """The URI live view should pull, or None when it should use the recorded stream: only a
    camera recording its main stream, and having a sub stream, gets one. `streams` is
    onvif_media2.streams(), biggest picture first."""
    current = next((s for s in streams if onvif_media2.same_stream(s["streamUri"], rtsp_url)), None)
    sub = next((s for s in streams if s["name"] == "sub"), None)
    if current is None or current["name"] != "main" or sub is None:
        return None
    return onvif_media2.with_credentials(sub["streamUri"], rtsp_url)


def _ensure_kvs_stream(kv, camera_id: str) -> str:
    name = camera_control.live_stream_name(camera_id)
    try:
        return kv.describe_stream(StreamName=name)["StreamInfo"]["StreamARN"]
    except kv.exceptions.ResourceNotFoundException:
        # video/h264 as at registration: stream-codec.py --live corrects MediaType at the
        # first start.
        return kv.create_stream(StreamName=name, DataRetentionInHours=LIVE_RETENTION_HOURS,
                                MediaType="video/h264")["StreamARN"]


def apply(camera_id: str, item: dict, uri: str | None, table, kv) -> dict:
    """Make the camera's live view match `uri` (live_uri()), and say what changed.

    `item` is the camera's registry row as it is now; `table` the cameras table and `kv` a
    kinesisvideo client, both through the device identity. Idempotent: an unchanged camera costs
    one KVS describe, one MediaMTX read and no write anywhere."""
    path = camera_control.live_path_name(camera_id)
    if uri:
        stream_arn = _ensure_kvs_stream(kv, camera_id)
        if item.get("liveRtspUrl") != uri:
            table.update_item(Key={"cameraId": camera_id}, UpdateExpression="SET liveRtspUrl = :u",
                              ExpressionAttributeValues={":u": uri})
        want = {"source": uri, "rtspTransport": "tcp"}
        conf = mediamtx_api.get_path_conf(path)
        if conf is None:
            mediamtx_api.add_path(path, want)
        elif conf.get("source") != uri:
            mediamtx_api.patch_path(path, want)       # reconnects only the live view's readers
        return {"live": "sub", "path": path, "kvsStream": camera_control.live_stream_name(camera_id),
                "kvsStreamArn": stream_arn}

    # The KVS stream stays: it costs nothing idle, and a later switch back to main reuses it.
    # A live producer still running would pull a path that is about to go: stop it now rather
    # than at its deadline.
    subprocess.run(["sudo", "systemctl", "stop", camera_control.live_unit_name(camera_id)],
                   check=False, capture_output=True)
    mediamtx_api.delete_path(path)
    if item.get("liveRtspUrl"):
        table.update_item(Key={"cameraId": camera_id}, UpdateExpression="REMOVE liveRtspUrl")
    return {"live": "recorded stream", "path": camera_control.mediamtx_path_name(camera_id)}
