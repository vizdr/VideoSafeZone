#!/usr/bin/env python3
"""Follow ONVIF cameras to a new IP address.

A camera without a DHCP reservation can come back from a power cut or lease renewal on a
different address. Its registry row (`onvifHost`, `rtspUrl`) then points at nothing, the
MediaMTX path goes dead and so does everything downstream -- until someone noticed,
rescanned and clicked Re-register (guide §16's "what's still genuinely missing").

Identity is the WS-Discovery endpoint reference (`urn:uuid:...`, often MAC-derived) that
every ONVIF device announces: stable across address changes, unlike the address. It is kept
as `onvifEndpointRef` on the registry row -- stored by the admin GUI at registration, and
learned here for cameras registered before that, the first time the camera answers a scan
at its registered address.

Each run (kvs-camera-rematch.timer, LAUNCH.md A8) does one multicast scan, then for each
passthrough camera:
  * no endpoint ref yet, device answers at its onvifHost -> learn the ref
  * ref found at a different address -> move it: onvifHost/onvifPort, and the host part of
    rtspUrl and of liveRtspUrl, the live view's sub stream (credentials, RTSP port and path
    unchanged) -- registry first, then MediaMTX, both paths
  * ref not found -> nothing. The camera may just be off; absence proves nothing.
Before that, on adapter-01 only (ADOPT_UNSTAMPED_ROWS=yes), rows without an adapterId whose
MediaMTX path this Pi serves get this Thing's name stamped (adopt_unstamped, DEALER-PANEL-PLAN.md
D4, D12). Unregistered cameras are not in the list at all (sync_mediamtx_paths.load_cameras leaves
them out), so their deleted paths are never recreated. It refuses to guess: a ref on two rows, or a new address another camera already has, is
logged and skipped.

When the registry can't be written (AWS down), the MediaMTX path and the local cache are
still moved, so video keeps flowing into the outage buffer and a MediaMTX restart (which
falls back to the cache while AWS is down) keeps the new address. The row still shows the
old address, so the next run retries the registry write.

Usage:  rematch_cameras.py [--dry-run] [--timeout SECONDS]
"""
import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import onvif_discovery
import sync_mediamtx_paths as paths


def log(msg):
    print(f"rematch: {msg}", flush=True)


def move_url(url, old_host, new_host):
    """rtspUrl with only its host replaced, or None if its host isn't old_host (then the
    URL was edited by hand to point elsewhere, and rewriting it would be a guess)."""
    parts = urlsplit(url)
    if parts.hostname != old_host:
        return None
    creds, _, hostport = parts.netloc.rpartition("@")
    netloc = (creds + "@" if creds else "") + new_host + hostport[len(old_host):]
    return urlunsplit(parts._replace(netloc=netloc))


def registry_update(camera_id, fields, expect_host, require_no_ref=False):
    """Conditional write: only if the row still shows the address we read, so a Re-register
    in the GUI between our read and our write is never overwritten."""
    from aws_device_creds import get_session   # also loads /etc/adapter/adapter.env
    fields = {**fields, "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    names = {f"#{k}": k for k in fields}
    values = {f":{k}": v for k, v in fields.items()} | {":expect": expect_host}
    condition = "onvifHost = :expect"
    if require_no_ref:
        condition += " AND attribute_not_exists(onvifEndpointRef)"
    get_session().resource("dynamodb").Table("cameras").update_item(
        Key={"cameraId": camera_id},
        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
        ConditionExpression=condition,
        ExpressionAttributeNames=names, ExpressionAttributeValues=values)


def adopt_unstamped(dry_run=False):
    """Stamp this Pi's Thing name as `adapterId` on registry rows that have none and whose
    MediaMTX path this Pi serves (DEALER-PANEL-PLAN.md D4, D12). The camera API's tenant check
    shows a row without adapterId to system administrators only, so until it is stamped a
    customer doesn't see the camera. Only on a Pi whose adapter.env says ADOPT_UNSTAMPED_ROWS=yes
    -- adapter-01, which served every camera registered before adapterId existed, and serves
    those a VMS card registers; a second Pi never claims a row by itself. Each write is
    conditional, so two Pis can't both stamp one row. Returns the camera IDs stamped."""
    import config
    if not config.ADOPT_UNSTAMPED_ROWS:
        return []
    import camera_control
    import mediamtx_api
    from aws_device_creds import get_session
    table = get_session().resource("dynamodb").Table("cameras")
    rows, kwargs = [], {"ProjectionExpression": "cameraId, adapterId, unregisteredAt, releasedFrom"}
    while True:
        page = table.scan(**kwargs)
        rows += page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    stamped = []
    for row in sorted(rows, key=lambda r: r["cameraId"]):
        if row.get("adapterId") or row.get("releasedFrom") or camera_control.is_unregistered(row):
            continue
        path = camera_control.mediamtx_path_name(row["cameraId"])
        try:
            served = (mediamtx_api.get_path_conf(path) is not None
                      or mediamtx_api.get_path_state(path) is not None)
        except Exception:  # noqa: BLE001 -- MediaMTX not answering: not provably ours, next run
            served = False
        if not served:
            continue
        if not dry_run:
            try:
                table.update_item(Key={"cameraId": row["cameraId"]}, UpdateExpression="SET adapterId = :a",
                                  ConditionExpression="attribute_not_exists(adapterId)",
                                  ExpressionAttributeValues={":a": config.THING_NAME})
            except Exception as e:  # noqa: BLE001
                if getattr(e, "response", {}).get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                    raise
                log(f"{row['cameraId']}: stamped by someone else meanwhile -- left as is")
                continue
        log(f"{row['cameraId']}: {'would stamp' if dry_run else 'stamped'} adapterId {config.THING_NAME}")
        stamped.append(row["cameraId"])
    return stamped


def rematch(cameras, found, dry_run=False):
    """Apply the rules above. Returns (cache_changed, cameras whose MediaMTX path must move)."""
    at_host = {host: epr for epr, (host, _) in found.items()}
    ref_count = Counter(c["onvifEndpointRef"] for c in cameras if c.get("onvifEndpointRef"))
    hosts_in_use = {c.get("onvifHost") for c in cameras}
    changed, moved = False, []
    suffix = " (dry run)" if dry_run else ""

    for cam in sorted(cameras, key=lambda c: c["cameraId"]):
        camera_id, host = cam["cameraId"], cam.get("onvifHost")
        if cam.get("mode") != "passthrough" or not host:
            continue
        ref = cam.get("onvifEndpointRef")

        if not ref:
            if host in at_host:
                log(f"{camera_id}: learned identity {at_host[host]} at {host}{suffix}")
                if not dry_run:
                    try:
                        registry_update(camera_id, {"onvifEndpointRef": at_host[host]}, host,
                                        require_no_ref=True)
                    except Exception as e:  # noqa: BLE001 -- retried next run
                        log(f"{camera_id}: registry write failed ({type(e).__name__}: {e}); will retry")
                    cam["onvifEndpointRef"] = at_host[host]
                    changed = True
            continue

        if ref_count[ref] > 1:
            log(f"{camera_id}: identity {ref} is on {ref_count[ref]} registry rows -- skipped, fix the registry")
            continue
        if ref not in found:
            continue
        new_host, new_port = found[ref]
        if new_host == host:
            continue
        if new_host in hosts_in_use:
            log(f"{camera_id}: found at {new_host}, but another camera is registered there -- skipped")
            continue
        new_url = move_url(cam.get("rtspUrl", ""), host, new_host)
        if new_url is None:
            log(f"{camera_id}: found at {new_host}, but its rtspUrl host isn't {host} -- "
                f"not rewriting a hand-edited URL; Re-register it (LAUNCH.md E3)")
            continue

        fields = {"onvifHost": new_host, "onvifPort": new_port, "rtspUrl": new_url}
        if cam.get("liveRtspUrl"):
            new_live = move_url(cam["liveRtspUrl"], host, new_host)
            if new_live is None:
                log(f"{camera_id}: found at {new_host}, but its liveRtspUrl host isn't {host} -- "
                    f"not rewriting a hand-edited URL; Re-register it (LAUNCH.md E3)")
                continue
            fields["liveRtspUrl"] = new_live

        log(f"{camera_id}: moved {host} -> {new_host}; rtspUrl -> {paths.redact(new_url)}{suffix}")
        if dry_run:
            continue
        try:
            registry_update(camera_id, fields, host)
        except Exception as e:  # noqa: BLE001 -- local move still happens; registry retried next run
            log(f"{camera_id}: registry write failed ({type(e).__name__}: {e}); "
                f"moving the MediaMTX path anyway, registry retried next run")
        cam.update(fields)
        hosts_in_use.add(new_host)
        changed = True
        moved.append(cam)
    return changed, moved


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report what would change, change nothing")
    ap.add_argument("--timeout", type=int, default=5, help="WS-Discovery scan time, seconds")
    args = ap.parse_args()

    try:
        adopt_unstamped(args.dry_run)
    except Exception as e:  # noqa: BLE001 -- AWS or MediaMTX unreachable: the next run tries again
        log(f"adopting unstamped rows skipped ({type(e).__name__}: {e})")

    cameras = paths.load_cameras(args.dry_run)       # registry, or the local cache when offline
    if cameras is None:
        return 0
    found = onvif_discovery.locate(args.timeout)
    log(f"scan: {len(found)} ONVIF device(s) answered")
    changed, moved = rematch(cameras, found, args.dry_run)
    if changed:
        paths.save_cache(cameras)
    if moved:
        if not paths.wait_for_api():
            log("MediaMTX API not answering -- paths move at its next start (from registry/cache)")
            return 1
        paths.sync(moved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
