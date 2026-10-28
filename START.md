# START.md — where the system starts

There is no single `main()`. The adapter is a set of systemd services on the Pi, plus Lambdas and
a page in AWS. So the starting point depends on which start you mean:

1. **First install**: a fresh SD card becomes a running adapter, once.
2. **Every boot**: the always-on stack comes up by itself. It costs nothing, and nothing goes to
   the cloud.
3. **Start**: someone presses Start (or Live), and a camera begins sending to Kinesis Video
   Streams (KVS). This is the part that is billed.

The unit files aren't in git. The setup scripts write them, with this clone's absolute paths
(CLAUDE.md, "systemd").

---

## 1. First install: `setup/setup-all.sh`

```bash
git clone https://github.com/vizdr/VideoSafeZone.git ~/Projects/VideoSafeZone
bash ~/Projects/VideoSafeZone/setup/setup-all.sh
```

It runs five scripts in order and stops at the first failure. Each one is safe to run again
(LAUNCH.md Part A):

| Step | Script | What it does for starting up |
|---|---|---|
| 1 | [`setup-system.sh`](setup/setup-system.sh) | Root steps. Turns on **lingering** (A3), so user services start at boot with nobody logged in. Writes the **system units** (A8): the producers `kvs-cam01`/`kvs-cam02`, the `kvs-cam@` template for cameras registered in the admin GUI, and the `kvs-live@` template for live view. Writes the **sudo rule** that lets the stack start and stop exactly those units |
| 2 | [`setup-user.sh`](setup/setup-user.sh) | The venv (`requirements.txt`), MediaMTX, the AWS CLI, and every **user unit** |
| 3 | `setup-sdk.sh` | The KVS producer SDK (`kvssink`), restored from the USB stick's cache or built (1.5–2.5 h) |
| 4 | `setup-identity.sh` | The device certificate the adapter signs in to AWS with (`certs/`) |
| 5 | [`setup-launch.sh`](setup/setup-launch.sh) | **Enables** the user units, so they start on every boot, and starts them now. Gives a camera registered from another card its producer, but doesn't start it. Then checks that video is actually flowing |

This happens once. After that, a reboot needs nothing; check it with LAUNCH.md Part C.

## 2. Every boot: systemd's user manager

Because lingering is on, the user-level systemd (`systemctl --user`) starts the enabled units.
The order comes from their `After=`/`Requires=` lines in `setup-user.sh`:

```
kvs-camera-init       adapter/bin/camera-init.sh
                      locks the USB camera's exposure, white balance and focus
kvs-mediamtx          mediamtx/mediamtx: the local hub everything below depends on
  └ ExecStartPost     adapter/sync_mediamtx_paths.py
                      re-adds each ONVIF camera's path from the registry (or its local cache
                      when AWS is unreachable), because MediaMTX keeps no API changes
kvs-camera-publish    adapter/bin/publish-cam01.sh            (after camera-init + MediaMTX)
                      USB camera → hardware H.264 → rtsp://127.0.0.1:8554/cam01
kvs-agent             adapter/agent.py
                      outbound MQTT session to AWS IoT Core; waits for commands
onvif-admin           adapter/onvif-admin/app.py: the local admin GUI, http://<pi>:8080
kvs-event-watcher     adapter/event_watcher.py: ONVIF detections → evidence clips
kvs-outage-buffer     adapter/outage_buffer.py: records to the USB stick if AWS goes away
                      (after MediaMTX)
kvs-outage-uploader   adapter/outage_uploader.py: backfills that footage once AWS is back
netwatch              adapter/bin/netwatch.sh: logs network health every 10 s
kvs-camera-rematch.timer → adapter/rematch_cameras.py
                      2 min after boot, then every 5 min: follows a camera to a new address
```

The ONVIF cameras need no service of their own here: MediaMTX pulls them over RTSP once
`sync_mediamtx_paths.py` has given it their paths. cam-01, the USB webcam, is pushed into
MediaMTX by `kvs-camera-publish`.

**After boot, every camera's video flows into MediaMTX on the Pi, and nothing is sent to KVS.**
The local preview in the admin GUI works, and costs nothing.

The producers (the system units from step 1) are installed but **never enabled**. An enabled
producer would push to KVS after every reboot, unattended and billed (FoundAndFixed.md #81).
Starting one is always a person's decision: the next section.

## 3. Start: a camera begins sending to the cloud

From the cloud page, anywhere:

```
client/index.html                                   Start button
  → API Gateway → publish-cmd Lambda                cloud/lambda/publish_cmd.py
  → MQTT topic adapter/<thing>/cmd                  AWS IoT Core, outbound session
  → agent.py on_message()                           the control plane's entry point on the Pi
  → camera_control.set_stream()                     the one place a camera ID becomes a unit name
  → sudo systemctl start kvs-cam01.service          cam-01 and cam-02
                     or  kvs-cam@cam03.service      cameras registered in the admin GUI
  → adapter/bin/stream-cam01.sh / stream-channel.sh
  → producer-lib.sh producer_run → kvssink → Kinesis Video Streams
```

From the admin GUI, on the camera's LAN, the cloud part is skipped. `POST
/api/cameras/<id>/start` in `app.py` calls the same `camera_control.set_stream()`.

Stop runs the same path with `systemctl stop`. Start/Stop control only the push to KVS: MediaMTX
and the camera's own feed keep running either way.

**Live** ("watch", not "record") takes the same route to the agent. There `set_live()` writes a
deadline (now + 5 min) and starts `kvs-live@<path>.service`. That unit pushes the camera's sub
stream to its own KVS stream, `<camera>-live`, and stops by itself once the deadline passes. The
page renews the deadline every 2 minutes while someone watches.

---

## Where to start reading

- **Control:** [`adapter/agent.py`](adapter/agent.py), `on_message()`, then
  [`adapter/camera_control.py`](adapter/camera_control.py) for how `cam-03` becomes `cam03`,
  `kvs-cam@cam03.service` or `kvs-live@cam03.service`.
- **Media:** [`adapter/bin/publish-cam01.sh`](adapter/bin/publish-cam01.sh) for cam-01 into
  MediaMTX, and `adapter/bin/producer-lib.sh` for MediaMTX into KVS.
- **Why it is built this way:** `Demo-AWS-Video-revCosts4.md`. Every arrow above crosses the
  router outbound-initiated (`OUTBOUND-CLOUD.md`).
- **Checking it runs:** LAUNCH.md Part C. `systemctl is-active` is not proof; check that video
  actually flows (CLAUDE.md, "Verifying changes").
