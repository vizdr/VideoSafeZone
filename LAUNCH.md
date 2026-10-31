# LAUNCH.md — Cloud Adapter operational runbook

Companion to `Demo-AWS-Video-revCosts4.md` (the narrative build guide). This file is the
short version: what to run to actually get the system up, after everything in the guide
has already been built once. A fresh SD card is `setup/setup-all.sh` (Part A); deploying the
cloud side is `cloud/deploy.sh` (Part H). If something here doesn't work, the guide explains why it
is built this way (search it for the section number), and `FoundAndFixed.md` has every
bug found so far, cited here as `#N`.

**Current live values for this deployment** (Account `596633517506`, region
`eu-central-1`) are baked into the commands below. If you rebuild this from scratch on a
different AWS account, every ID here changes — see the guide's §1–§8 for how each one is
created.

---

## Part A — One-time setup: a fresh SD card

Fresh cards are routine on this board — it also runs VMS from another card
(`VMS-PORT-PLAN.md` D1), and a re-imaged card has none of what follows — so Part A is
scripted. `setup/` holds one script per group of steps. Each is idempotent (it checks before
it changes anything, so a second run changes nothing) and ends with PASS/FAIL proofs. The
scripts carry the step-by-step reasons as comments; this part says what each step does and
why, and keeps the A1–A10 numbers that other documents cite.

### The short way

1. Flash Raspberry Pi OS (64-bit, Debian 13 "trixie") with your user, Wi-Fi and SSH. Boot it.
2. Clone the repo and run everything:

   ```bash
   git clone https://github.com/vizdr/VideoSafeZone.git ~/Projects/VideoSafeZone
   bash ~/Projects/VideoSafeZone/setup/setup-all.sh     # as yourself, not with sudo: it asks once
   ```

3. **Only the first time on an AWS account** (no identity backup on the stick yet), it stops at
   the device certificate and says so. Then, from `~/Projects/VideoSafeZone`:

   ```bash
   aws login --remote                       # prints a URL: sign in from any browser
   bash setup/setup-identity.sh create      # a new certificate for Thing adapter-01
   bash setup/setup-identity.sh backup      # kept on the stick: every later card restores it
   bash setup/setup-all.sh                  # carries on where it stopped
   ```

4. `bash setup/setup-check.sh` — every line `ok`.

With the SDK cache on the stick a fresh card is ready in about 10–15 minutes, mostly apt and
pip. Without it, the KVS SDK builds in the background for 1.5–2.5 hours. The stack runs
meanwhile, and only Start needs the plugin; `bash setup/setup-sdk.sh wait` blocks until the
build is done, and the finished build caches itself on the stick.

| Script | Does | Root | Takes |
|---|---|---|---|
| `setup/setup-system.sh` | A1, A3 packages and lingering, A7 `adapter.env`, A8 system units and sudo rule, A10 stick | asks once | 5–10 min |
| `setup/setup-user.sh` | A2 `~/.bashrc`, A4 venv, A5 MediaMTX, A6 AWS CLI, A8 user units | — | 3–5 min |
| `setup/setup-sdk.sh` | A3 the KVS SDK with patches 1–5, in the background; the stick's cache | — | seconds, or 1.5–2.5 h |
| `setup/setup-identity.sh` | A7 the device certificate: keep, restore, create, back up | — | seconds |
| `setup/setup-launch.sh` | Part B: start everything, give cameras registered elsewhere their producer, Part C proofs | — | ~1 min |
| `setup/setup-all.sh` | the five above in order, stopping at the first failure | — | |
| `setup/setup-check.sh` | read-only: what is missing, and which script provides it | — | seconds |

**What stays manual, on purpose:** logging in to AWS; the first certificate on an account, and
retiring old ones (never automatic — the VMS card shares the Thing); formatting a new stick
(it would erase it); an EEPROM update and its reboot (`setup-system.sh` only reports one).

**A moved clone** (another folder, user or Pi): run `setup-system.sh` and `setup-user.sh` again
— unit files and the sudo rule hold literal paths (FoundAndFixed.md #25, #30) — then
`bash setup/setup-launch.sh`. The SDK cache doesn't match a different path, so the SDK rebuilds.

### A1. Stability hardening (§1.4) — `setup-system.sh`

earlyoom told to kill compilers first (`-m 20 -s 95 --prefer cc1plus|…`), a 3 GB swapfile at
low priority with swappiness 10, and a **persistent journal** — Pi OS keeps it in RAM, so every
`journalctl --user -u` check in this file found nothing until a later `/etc` drop-in
overrode its `Storage=volatile` (FoundAndFixed.md #33). These are what stopped the SDK build
from crashing the Pi (#1). The script also turns **Wi-Fi power save off** (#47): with it on,
connections *into* the Pi — SSH, the admin GUI — time out while every check on the Pi says
it is healthy. An EEPROM update is reported, never applied: it needs a reboot you choose.

**Wi-Fi on this board's network is fragile even so.** The Pi's uplink can stall while it still
reports itself connected, until the router's next group rekey (every 10 minutes) kicks it off
and it reconnects (FoundAndFixed.md #79, #53). When SSH drops, **wait up to 10 minutes** before
restarting; ping the Pi from the PC meanwhile — "destination host unreachable" is this stall.
An Ethernet cable avoids all of it. The `netwatch` user unit logs the link every 10 s with an
fsync, because journald writes to disk only every 5 minutes and a restart erases exactly the
minutes that matter: `tail -f ~/.local/state/netwatch.log` (`gw=FAIL`: the link itself died).

**Proof:** `setup-system.sh`'s own; and after a reboot, `journalctl --list-boots | tail -2`
lists two boots with the older one still readable.

### A2. Environment in `~/.bashrc` — `setup-user.sh`

A marked block exports `VMS_HOME`, `KVS_SDK`, `GST_PLUGIN_PATH`, `LD_LIBRARY_PATH`, and
`AWS_REGION`/`THING_NAME` read from `/etc/adapter/adapter.env`, so a manual run and the
services can't talk to different things. A rerun replaces the block, so a moved clone updates
it. **Interactive shells only:** systemd never reads `.bashrc`, which is why every unit carries
literal paths, and why the adapter's scripts and modules find the repo root from their own
location when `VMS_HOME` isn't set (FoundAndFixed.md #25).

### A3. The KVS Producer SDK (§4) — `setup-sdk.sh`

`setup-system.sh` installs the build packages, including `gstreamer1.0-rtsp`, without which
cam-01 never publishes (FoundAndFixed.md #40). `setup-sdk.sh` then builds the SDK at a fixed
tag (`v3.6.0`, in `setup/lib.sh`) with the patches it needs. Each patch is a fix for a
failure; the script's header explains every one:

| Patch | What | Why |
|---|---|---|
| 1 | no `--parallel` in nested dependency builds | OpenSSL on all cores starves the Pi; earlyoom kills every compiler (#2) |
| 2, 2b | no OpenSSL test submodules; 2b is the CMake policy (`CMP0097`) without which CMake ≥ 3.16 reads 2's empty list as "all" | ~700 MB of `boringssl` & co. crashed the Pi (#3, #77) |
| 3 | `_GNU_SOURCE` for GCC 14 in kvspic | `pthread_getname_np` is a hard error (#4) |
| 4, 5 | kvssink timestamps: whole milliseconds; a missing DTS taken from the PTS (`setup/patches/kvssink-vms-patches-4-5.patch`) | audio frames rejected with `0x30000005` (#63, #70) |

Patches 1–2b edit `kvscproducer`, a second repository that the SDK only downloads *during*
its configure step, so the script fetches it on its own first (#76). Patch 3's file exists only
after configure. The compile runs one job pinned to cores 1–2.

**The cache.** A finished build is saved to `/mnt/vms-buffer/.setup-cache/` (~60 MB) and
restored in seconds on a fresh card. It is used only when the SDK tag, the patches, the
Debian release, the GStreamer version, the CPU and the clone's path all match — the plugin's
`RUNPATH` holds absolute paths into the clone. Anything else rebuilds.

```bash
bash setup/setup-sdk.sh            # built? proofs. cached? restore. else: start the build
bash setup/setup-sdk.sh status     # how far the background build is
bash setup/setup-sdk.sh wait       # block until done, then the proofs
```

A failed build resumes when re-run: finished dependencies in `open-source/local/` are kept.

**Changing a kvssink patch on a built SDK:** `make src/gstreamer/gstkvssink.o` in
`$KVS_SDK/build` while producers run, then stop them and `make gstkvssink` — linking over a
loaded plugin can crash a running producer. Then `bash setup/setup-sdk.sh save-cache`, after
bumping `BUILD_RECIPE` in it so older caches stop matching.

### A4. Python venv (§7.2) — `setup-user.sh`

`venv-adapter` with every third-party module `adapter/` imports, from `requirements.txt` —
the one list of them, which CI installs too (the list was once incomplete,
FoundAndFixed.md #27) — and pytest, from `requirements-test.txt` (`PYTEST.md`). Its proof
also resolves the ONVIF WSDLs with `VMS_HOME` unset, as under systemd.

### A5. MediaMTX (§2.5) — `setup-user.sh`

The binary of the version `mediamtx/mediamtx.yml` was written against (`v1.20.1`, in
`setup/lib.sh`), extracted by name: the release tarball also carries a default `mediamtx.yml`
that a plain `tar xzf` would write over the project's (FoundAndFixed.md #26). Upgrade on
purpose, by changing the version there.

### A6. AWS CLI — the operator's tool, not the adapter's — `setup-user.sh`

Installed per user under `~/.local`, after checking AWS's GPG signature (fingerprint
`FB5D B77F D5C1 18B8 0511 ADA8 A631 0ACC 4672 475C`; `[expired]` next to it is normal). It is
only for you: creating a device certificate (A7), deploying (Part H), and the cloud checks in
Part C. **The adapter's services never use it** — they authenticate with the device
certificate. Logging in is left to you, and only needed for those tasks:

- `aws login --remote` — recommended: your normal console sign-in, short-lived credentials, a
  URL to open on any device (the Pi has no browser). Re-run it when the session expires.
- `aws configure sso` if the account uses IAM Identity Center.
- AWS CloudShell instead, if you'd rather keep no credentials on the Pi at all.

`aws configure` with an IAM user's keys works too, but leaves a static key on the device.

### A7. AWS resources, `adapter.env`, the device certificate

**AWS resources (§3, §6, §8) — once per AWS account,** already created for this one: KVS
streams, IAM role `KVSAdapterRole` and role alias `KVSAdapterRoleAlias`, IoT Thing `adapter-01`
with `KVSAdapterThingPolicy`, DynamoDB tables `cameras` and `clips`, Cognito pool
`kvs-demo-users`, the Lambdas in `cloud/lambda/`, API Gateway `kvs-demo-api`, and the S3 bucket
behind CloudFront. The guide's §3/§6/§8/§9 has the commands; don't re-run them here.

**`/etc/adapter/adapter.env` — once per Pi** (`setup-system.sh`, from
`config/adapter.env.example`): which region, Thing, role alias, endpoints and evidence bucket
this adapter uses. Without it every service exits at startup with `<KEY> is not set`.
Environment variables override it. A different account or Thing: edit the file; the lookup
commands are in the template's header.

**The device certificate — `setup-identity.sh`.** `certs/` is gitignored, so a fresh clone has
none. Four files:

| File | What it is | Used by |
|---|---|---|
| `adapter.cert.pem` | device certificate for Thing `adapter-01` | everything |
| `adapter.private.key` | its private key — **AWS never shows it again** | everything |
| `cacert.pem` | Starfield root (`SFSRootCAG2`) — credentials endpoint | `kvssink`, `aws_device_creds.py` |
| `AmazonRootCA1.pem` | Amazon root CA 1 — MQTT data endpoint (FoundAndFixed.md #29) | `agent.py` |

The two CAs are not interchangeable; the wrong one fails like a permission problem (§6.4).

```bash
bash setup/setup-identity.sh               # keep certs/, or restore the stick's backup, or say what to do
bash setup/setup-identity.sh create        # a new certificate (needs `aws login`); refuses to replace certs/
bash setup/setup-identity.sh backup        # copy certs/ to /mnt/vms-buffer/.setup-cache/adapter-identity
bash setup/setup-identity.sh restore DIR   # from any copy, e.g. the previous card's certs/
bash setup/setup-identity.sh check         # the four proofs only
```

**Why the backup:** with frequent fresh cards, `create` each time would pile up ACTIVE
certificates on the Thing; restoring needs no AWS login at all. The price: whoever holds the
stick holds the adapter's identity — it can push to KVS and read the camera registry. The stick
lives in the Pi, next to the SD card that holds the same key; just don't hand it to anyone.

**Retiring old certificates** is never automatic: the VMS card's certificate is attached to the
same Thing and must stay ACTIVE. `create` lists the others with their dates; retire one you
know is gone with `aws iot update-certificate --certificate-id <id> --new-status INACTIVE`.
Never run `kvs-agent` on two Pis at once: both connect as MQTT client `adapter-01`, and IoT
Core drops the older session each time the other connects.

**The proofs**, in order of importance: (b) the credentials endpoint vends credentials — the
certificate is active, attached to the Thing and allowed to assume the role; (c) the MQTT
endpoint accepts it, with the other CA; (d) the project's own code assumes the role with
`VMS_HOME` unset; (a) the certificate matches the key.

### A8. systemd units and the sudo rule — `setup-system.sh`, `setup-user.sh`

The unit files are not in git: **these two scripts are their authoritative source.** They write
literal absolute paths — systemd expands no `$VMS_HOME` or `~` (FoundAndFixed.md #25, #30) —
and rewrite a file only when its text changes.

| Unit | Manager | Runs | Written by |
|---|---|---|---|
| `kvs-camera-init` | user | `adapter/bin/camera-init.sh` (one-shot) | `setup-user.sh` |
| `kvs-mediamtx` | user | `mediamtx/mediamtx` + the registry path sync (#32) | `setup-user.sh` |
| `kvs-camera-publish` | user | `adapter/bin/publish-cam01.sh` | `setup-user.sh` |
| `kvs-agent` | user | `adapter/agent.py` | `setup-user.sh` |
| `onvif-admin` | user | `adapter/onvif-admin/app.py` | `setup-user.sh` |
| `kvs-event-watcher` | user | `adapter/event_watcher.py` | `setup-user.sh` |
| `kvs-outage-buffer`, `kvs-outage-uploader` | user | `adapter/outage_buffer.py`, `outage_uploader.py` | `setup-user.sh` |
| `kvs-camera-rematch.{service,timer}` | user | `adapter/rematch_cameras.py`, every 5 min (E3) | `setup-user.sh` |
| `netwatch` | user | `adapter/bin/netwatch.sh` (A1, #79) | `setup-user.sh` |
| `kvs-cam01`, `kvs-cam02` | **system** | `adapter/bin/stream-cam01.sh`, `stream-cam02.sh` | `setup-system.sh` |
| `kvs-cam@` | **system** | `adapter/bin/stream-channel.sh`, one instance per GUI-registered camera | `setup-system.sh` |
| `kvs-live@` | **system** | `adapter/bin/stream-channel.sh` with `LIVE_OF`: the cloud page's Live view | `setup-system.sh` |

- **user** → `systemctl --user …`, no sudo, logs in `journalctl --user -u <unit>`; they start
  at boot without a login because of lingering (A3).
- **system** → `sudo systemctl …`, logs in `journalctl -u <unit>`. These are the producers,
  the units that **cost money while running** (guide §1.2): installed, **never enabled**.
  Start/Stop in either GUI drives them; a `kvs-live@` instance stops itself at its deadline.
  An enabled producer once pushed to KVS after every reboot, unattended (#81).
- A unit in one manager can't `Requires=`/`After=` a unit in the other (#12).

**The sudo rule** (`/etc/sudoers.d/020_vms-adapter`). The services run a few root actions with
no terminal for a password, and current Pi OS images grant no passwordless sudo (#34). The
rule allows exactly those: `systemctl start|stop|enable|disable kvs-camNN`/`kvs-cam@camNN` (Start,
Stop, Unregister — #80), `systemctl start|stop kvs-live@camNN`, and
`provision-camera.sh cam-NN camNN [no-start]`. Nothing else. It narrows what the services ask
for; it is not a hard boundary, since `provision-camera.sh` lives in your own writable
checkout. The script validates it with `visudo` before installing; its proofs check with
`sudo -k -n`, which ignores a recently typed password that would otherwise fake a pass (#34).

### A9. Local camera hardware (cam-01, USB webcam) — detected at every start

Nothing to configure while one USB camera is attached. `camera-init.sh` and
`publish-cam01.sh` find it themselves (`adapter/bin/detect-hw.sh`): the one
MJPG-capable `/dev/v4l/by-id/…` capture node, and the microphone on the **same USB
device**, matched through sysfs rather than by ALSA card name (many webcams all call
theirs "Webcam"). Both are logged at start (`journalctl --user -u kvs-camera-publish`).
Detection never guesses: no camera, or more than one, is an error naming the candidates.

**Proof:**

```bash
"$VMS_HOME/adapter/bin/detect-hw.sh" --print
# video device  : /dev/v4l/by-id/usb-…-video-index0 -> /dev/video0
# audio device  : hw:CARD=…,DEV=0
# buffer mount  : …                (A10's stick, found by LABEL=vms-buffer)
# isolated CPUs : …                (empty = no isolcpus; A3's taskset pinning protects nothing)
# HW encoders   : h264             (a Pi 4 has no H.265 encoder — why cam-01 is H.264-only, guide §21.1)
```

**Only if needed** — a second USB camera, a different model, or other lighting — create
`/etc/adapter/cameras/cam01.env` from the template (every key is optional; unset keys keep
the PW310 defaults shown in it):

```bash
sudo install -D -m 644 "$VMS_HOME/config/cameras/cam01.env.example" /etc/adapter/cameras/cam01.env
sudoedit /etc/adapter/cameras/cam01.env    # CAM_MATCH / CAM_DEVICE, CAPS, V4L2_*_CTRLS
"$VMS_HOME/adapter/bin/detect-hw.sh" --print                 # shows "(present)" and the result
systemctl --user restart kvs-camera-init kvs-camera-publish  # settings apply on restart
```

A changed `CAPS` or control set is a pipeline change: verify with a decoded frame, not
just a running unit (Part C, and `CLAUDE.md` "Verifying changes").

### A10. USB outage-buffer stick (optional) — `setup-system.sh`

Only for durable outage buffering (`OUTAGE.md`) — and for the setup cache and the identity
backup (A3, A7), which is why it stays in the Pi across fresh cards. Without it the outage
units just idle (FoundAndFixed.md #39). `setup-system.sh` finds the partition by its filesystem
**label** `vms-buffer`, adds it to `/etc/fstab` by UUID with `nofail` (a missing stick never
blocks boot), mounts it at `/mnt/vms-buffer`, hands it to you, and creates the sentinel
`.vms-buffer-ok`. Nothing arms without the sentinel: an unplugged stick leaves an empty
mountpoint on the SD card, where recording would otherwise land — and fit, which is worse
than failing.

**Formatting is never done by a script.** Only a *new* stick, after checking the name twice
(it erases the partition):

```bash
lsblk -o NAME,SIZE,FSTYPE,LABEL                      # the stick, usually sda1
sudo mkfs.ext4 -m 0 -L vms-buffer /dev/sdX1          # X = the letter lsblk showed
bash setup/setup-system.sh                           # mounts it
```

A stick moved from another card may hold captures that still need uploading; the script
counts them, and the uploader takes them once it runs (Part G). Recording itself stays off
until outage buffering is switched on per camera (either GUI), and arms only while that
camera's producer runs — Part C "If outage buffering is enabled".

---

## Part B — Launch — `setup/setup-launch.sh`

**Once per Pi, or after a moved clone** — after that everything starts at boot by itself.

```bash
bash setup/setup-launch.sh
```

1. Enables and starts every user unit (the table in A8). `enable` makes each one come back at
   every boot, and lingering starts them without anyone logging in.
2. Gives every camera that is in the registry but has no producer on this Pi — one registered
   from the other SD card, or before this card existed — its `kvs-cam@` instance, provisioned
   but **not started** (`provision-camera.sh … no-start`). Without it, Start would do nothing
   for that camera (FoundAndFixed.md #37, VMS-PORT-PLAN.md D1).
3. Proves the result the way Part C asks: video actually flowing into MediaMTX (byte counters
   growing, #50), the agent online with AWS IoT, the admin GUI answering, no producer enabled.

| When | What to do |
|---|---|
| **Once**, on a fresh card or a moved clone | this Part B (`setup-all.sh` includes it) |
| **After every reboot** | nothing to start — verify with Part C if you like |
| **Each time you want cloud video** | Start, in either GUI. Producers cost money while they run (§1.2) — and Part F at the end |

**A partial launch fails silently, not obviously** (FoundAndFixed.md #5): with one unit missing
everything else still reports `active` while producing nothing — which is why the script
checks the flow, not the units.

To start a producer without a GUI:

```bash
sudo systemctl start kvs-cam01.service   # or: aws iot-data publish --topic adapter/adapter-01/cmd \
                                          #     --cli-binary-format raw-in-base64-out \
                                          #     --payload '{"action":"start"}' --region eu-central-1
```

**If `kvs-camera-init` or `kvs-camera-publish` fails**, run `adapter/bin/detect-hw.sh --print`
first: "no MJPG-capable USB camera" or "2 cameras" there is the whole answer (A9).

**If any unit fails to start**, check in this order: `who -b` / `uptime` (did the Pi just
restart? see §1.4 and A1's Wi-Fi note), `journalctl --user -u <unit> -n 50`, `free -h`
(memory pressure), `systemctl is-active earlyoom` (should be `active`), and
`bash setup/setup-check.sh`.

---

## Part C — Verify

**Check in this order — `systemctl ... is-active` alone is not proof of anything.** Every
unit can report `active` while the stream is genuinely dead (FoundAndFixed.md #5). Only
the first two commands below actually prove media is flowing; the systemd check at the
end is a secondary sanity check, not the primary one.

```bash
# 1. camera → RTSP (Checkpoint 1) — the real proof local capture is working
ffprobe -rtsp_transport tcp rtsp://127.0.0.1:8554/cam01

# 2. KVS stream is receiving live media (Checkpoints 4/5) — needs kvs-cam01.service active
EP=$(aws kinesisvideo get-data-endpoint --stream-name cam-01 --region eu-central-1 \
  --api-name GET_HLS_STREAMING_SESSION_URL --query DataEndpoint --output text)
URL=$(aws kinesis-video-archived-media get-hls-streaming-session-url \
  --endpoint-url "$EP" --region eu-central-1 --stream-name cam-01 --playback-mode LIVE \
  --query HLSStreamingSessionURL --output text)
ffprobe "$URL"   # a fresh creation_time in the output is the actual proof, not just HTTP 200

# 3. systemd units — a secondary check, not a substitute for 1 and 2
systemctl --user list-units 'kvs-*' --no-pager
sudo systemctl is-active kvs-cam01.service
```

### If the camera has audio enabled (guide §18)

Audio is off by default. When it is on, two extra checks matter, because both of its
failure modes are **silent** at the level of step 1–3 above — fragments persist, both
tracks appear, and `ffprobe` is happy:

```bash
# 4. frames being rejected? want exactly 0.
#    Anything above zero is the shared-DTS trap (§18.3, FoundAndFixed.md #15): you are losing audio.
journalctl -u kvs-cam01.service --since "-60 s" | grep -c 0x30000005

# 5. is the audio actually all arriving, and is it real?
#    delivered kb/s well below the configured bitrate = frames being dropped;
#    RMS at the noise floor with a high flat factor = a dead or clipping mic.
ffmpeg -i "$URL" -t 20 -c copy -y /tmp/s.mp4
ffprobe /tmp/s.mp4                     # expect BOTH streams
ffmpeg -i /tmp/s.mp4 -vn -af astats=metadata=1 -f null - 2>&1 | grep -E 'RMS|Flat'
```

Note that step 2 is the step that catches codec-private-data errors: a stream can ingest
perfectly and still fail `GetHLSStreamingSessionURL` with
`InvalidCodecPrivateDataException`. And as always, finish in a **browser** — MSE is
stricter than `ffmpeg` and has caught two regressions here that `ffmpeg` passed (#13, #16).

To toggle audio: tick "Record audio with video" in the cloud client, or "with audio" in
the local admin table. It applies on the camera's **next Start**, by design (§18.7).

### If a camera runs H.265 (guide §21)

H.264 is every camera's default; H.265 is switched on per camera in the admin GUI (E6).
The producer picks the codec MediaMTX actually receives, so check that chain end to end:

```bash
curl -s http://127.0.0.1:8080/api/cameras/cam-02/on-wire          # {"onWire": "h265"}
journalctl -u kvs-cam02.service -n 80 | grep -E 'video h26|stream-codec'   # "video h265"
aws kinesisvideo describe-stream --stream-name cam-02 --query StreamInfo.MediaType   # video/h265
# step 2 above, then: the master playlist must say CODECS="hvc1..." -- and a decoded frame:
curl -s "$URL" | grep -o 'CODECS="[^"]*"'
ffmpeg -skip_frame nokey -i "$URL" -frames:v 1 -vsync 0 -y /tmp/h265.png
```

Then a **browser**, and on the machines that will actually watch: whether H.265 plays
depends on browser *and* machine (on Windows, Firefox and Edge need Microsoft's HEVC Video
Extensions). Both GUIs say so when the viewer's browser can't decode it.

### If outage buffering is enabled (OUTAGE.md)

Off by default. When on, footage is buffered to the USB stick while AWS is unreachable and
backfilled into **Evidence clips** on recovery. Two user units do this — both must be up:

```bash
systemctl --user is-active kvs-outage-buffer kvs-outage-uploader

# armed only while that camera's producer runs; check what MediaMTX was actually told:
curl -s http://127.0.0.1:9997/v3/config/paths/get/cam02 | python3 -m json.tool | grep record

# the rolling window should stay BOUNDED (~4 segments = 120s / 30s). Growing without
# limit means retention is broken and the stick will fill silently.
ls /mnt/vms-buffer/live/cam02/*.mp4 | wc -l

# captures waiting to upload (empty in steady state)
ls -d /mnt/vms-buffer/outage/*/ 2>/dev/null
```

**The stick must be mounted or nothing is armed** — the supervisor checks `ismount` plus
the `/mnt/vms-buffer/.vms-buffer-ok` sentinel every tick, because an unplugged stick with
the mountpoint still present would send MediaMTX's writes to the SD card, and with tens of
GB free there a long outage *fits*, which is worse than failing.

To test it, use `adapter/bin/awsblock.sh on|off` — **not** §10.2's `iptables` snippet,
which is IPv4-only and silently ineffective here (#18). `awsblock.sh` needs `iptables` and
`ip6tables`, which a fresh Raspberry Pi OS image does not have (it ships `nft` only):
`sudo apt install -y iptables` first — its output then shows both families blocked. Then
`adapter/bin/gap-fill.py --stream cam-02 --last 600`.

---

## Part D — Access the browser client (§8, Checkpoint 7)

**URL:** https://dugyd3kkt36pw.cloudfront.net  (CloudFront + TLS, §8.5.1)

**Login:** username `demo-viewer`, password `DemoViewer2026!`

The old plain-HTTP S3 website URL
(`http://vms-demo-client-596633517506.s3-website.eu-central-1.amazonaws.com`) still
works today — the bucket is still public pending the cutover in §8.5.1's last step
(swap to the OAC-only bucket policy, enable Block Public Access, `delete-bucket-website`).
Until that runs, there is still an unencrypted way to reach the page. Prefer the HTTPS
URL, and expect some browsers to complain about the HTTP one.

This is a genuinely public URL, reachable from anywhere (no VPN, no router changes, no
geographic restriction — that's the point). Sign in, press **Start** if the stream isn't
already live, wait a few seconds for the first HLS segments to land, then **Reload
player** if it doesn't auto-recover from the initial buffering.

---

## Part E — Add an ONVIF camera (local admin GUI)

**URL:** http://192.168.178.53:8080 — LAN only, no login. If it isn't up:
`systemctl --user enable --now onvif-admin`.

**Why this is a separate local app and not part of the browser client:** WS-Discovery is
UDP multicast. It only works from a process on the same LAN segment as the cameras — the
cloud client is served from S3 and reached over the internet, and Lambda has no route to
your LAN at all. Discovery and registration therefore have to run on the Pi (guide
§16.2.1).

**Prerequisite:** the camera must be on the *same broadcast domain* as the Pi. Multicast
does not cross routers or VLANs by design, so a camera on a different subnet will never
answer a scan no matter how long you wait.

### E1. Discover

1. Enter the camera's **ONVIF username / password** (for the existing camera: `admin`).
2. Press **Scan LAN**.

Each device that answers shows its XAddrs, ONVIF scopes, and — because credentials were
supplied — its manufacturer/model, media profile and a real RTSP URL. A camera already in
the registry is labelled **"Registered as cam-NN"** and its button reads *Re-register*
instead of *Register*.

Same thing from the CLI, useful when the GUI is not running:

```bash
venv-adapter/bin/python3 adapter/bin/discover-onvif.py --user admin --password *** --timeout 5
```

### E2. Register

Press **Register this camera**, check the pre-filled fields, and give it an ID matching
`cam-NN` (e.g. `cam-03`). One click then does all of this:

| Step | What happens |
|---|---|
| MediaMTX path | added **live** via its local API — no config rewrite, no restart, so other cameras keep streaming; re-added from the registry after every MediaMTX restart (E3) |
| systemd | `/etc/adapter/channels/camNN.env` written, then `kvs-cam@camNN.service` started — never enabled, so it doesn't start by itself at boot (templated unit, guide §16.6; FoundAndFixed.md #81) |
| KVS | stream `cam-NN` created, 24 h retention |
| Registry | row written to the `cameras` DynamoDB table |

That registry row is the single source of truth: the cloud client, the MQTT control plane
and every camera-aware Lambda read it, so a camera registered here works **everywhere
immediately, with no code change and no redeploy**.

### E3. Re-register an existing camera

Use this when a camera's credentials changed, to bring back an unregistered camera (E7), or
for a camera registered from the **other SD card** (VMS shares the registry, E8). (A moved IP
is followed automatically — see below.) A discovered device is matched to its row by its
WS-Discovery identity first, then by address. Re-register updates the MediaMTX path source
and the registry row. It never re-provisions `cam-01`/`cam-02`, which predate the `kvs-cam@`
template — a second producer on the same KVS stream would conflict — but a camera with **no
producer on this Pi** (the status column says "no producer on this Pi: Re-register", and
Start is greyed out) gets its `kvs-cam@` instance provisioned, **not** started — and, like
every producer, not enabled: none starts at boot (FoundAndFixed.md #81).

**The registry is where a network camera's address and credentials live** — `rtspUrl` on
its `cameras` row, nowhere in git (FoundAndFixed.md #31, #32). `mediamtx.yml` has no camera paths: after every
MediaMTX start (crash restarts included) `adapter/sync_mediamtx_paths.py` re-adds them
from the registry, or from a local cache (`~/.local/state/vms-adapter/cameras-cache.json`,
mode 600) when AWS is unreachable. So after changing a camera's password *on the camera*,
Re-register it here; that updates the registry, and the path follows immediately and on
every later restart.

**Proof** — what the sync would do right now, and what MediaMTX has:

```bash
"$VMS_HOME/venv-adapter/bin/python3" "$VMS_HOME/adapter/sync_mediamtx_paths.py" --dry-run
# cam-02: cam02 up to date            <- good
# cam-02: passthrough but no rtspUrl  <- this camera was never registered through the GUI:
#                                        Re-register it, or it has no path after a restart
curl -s http://127.0.0.1:9997/v3/config/paths/list | python3 -c \
  'import json,sys; print([p["name"] for p in json.load(sys.stdin)["items"]])'
journalctl --user -u kvs-mediamtx | grep sync-paths    # each start's sync, credentials masked
```

**A moved camera is followed automatically**, so Re-register is only needed for changed
credentials. Every 5 minutes `kvs-camera-rematch.timer` scans the LAN. It recognises each
camera by its WS-Discovery identity (`onvifEndpointRef`, a `urn:uuid:…` that survives
DHCP changes), and when one answers at a new address it rewrites `onvifHost` and the
host in `rtspUrl` (registry, then MediaMTX path). Cameras registered before this existed
get their identity learned automatically the first time they answer at their registered
address. It never guesses: duplicates and address clashes are logged and skipped.

**Proof:**

```bash
"$VMS_HOME/venv-adapter/bin/python3" "$VMS_HOME/adapter/rematch_cameras.py" --dry-run
# rematch: scan: 1 ONVIF device(s) answered
# rematch: cam-02: learned identity urn:uuid:…    <- first run for an older registration
# rematch: cam-02: moved <old> -> <new>; …        <- what a real move looks like
systemctl --user list-timers kvs-camera-rematch.timer     # next and last run
journalctl --user -u kvs-camera-rematch | grep rematch    # what each run did
```

### E4. Control, from the same table

| Column | Does what |
|---|---|
| **Local preview** | live video straight from MediaMTX (port 8888) — no cloud round trip, works before AWS is involved at all |
| **KVS push** | polls `systemctl is-active` for the producer — actual state, not what a button last claimed |
| **Recording** | `manual` / `motion` / `cellMotion` / `human` — consumed by `kvs-event-watcher` |
| **Camera** | the ID, and a name both pages show beside it (letters, digits, space, `- _ .`) |
| **Start/Stop Remote** | starts/stops the KVS producer, i.e. what costs money. Greyed out with "no producer on this Pi" for a camera registered from the other card: Re-register it (E8) |
| **Day/Night Auto, Day, Night** | the IR-cut filter, where the camera supports it: Day = colour, IR off; Night = black-and-white, IR on (the old labels had it backwards, FoundAndFixed.md #64) |
| **video** | the recorded stream's codec, H.264 or H.265, where the camera's encoder offers it — see E6 |
| **stream / fps** | which of the camera's streams is recorded, and 10 or 15 fps — see E9 |
| **Test triggers** | listens for up to a minute while you walk past — see E9 |
| **Unregister** | hides the camera everywhere and stops it, keeping its clips — see E7 |

Motion analytics (sensitivity, cell mask, alarm delays) are shown **read-only** in section
3 of the page. That is not a UI shortcut: `SetVideoAnalyticsConfiguration` is a silent
no-op on this camera — it returns success and changes nothing (verified). Change those in
the camera's own web UI.

### E5. Verify — same rule as Part C

`systemctl is-active` is not proof. After registering:

```bash
ffprobe -rtsp_transport tcp rtsp://127.0.0.1:8554/camNN     # is the camera actually feeding MediaMTX?
systemctl is-active kvs-cam@camNN.service                   # is the producer up?
```

Then Part C's KVS check to confirm media is reaching the cloud.

### E6. Switch a camera's codec (H.264 / H.265)

Only here — the cloud client shows the codec but cannot change it. Pick it in the
**Mode / video** column; the page asks for confirmation, then follows the codec actually
arriving until it reads "on the wire: H.265". What to expect (guide §21):

- **The camera itself switches**, so the local preview, the outage buffer and the cloud
  stream all follow — there is no per-consumer codec.
- **Only codecs the hardware encodes are offered.** `cam-01` shows H.265 as *not
  available* with the reason (it would need software encoding, which this Pi can't sustain).
- **A running cloud stream restarts itself**: ~3 s for the camera to apply the change,
  7–14 s until MediaMTX has it, ~10 s without cloud video. No Stop/Start needed.
- **Clips can't span the switch**: a recording or detection across it is saved as two
  clips, one per codec, labelled `codec-switch`.
- **Viewers need a browser that decodes H.265** — see Part C's H.265 check.

Codec capabilities are read when the admin GUI starts and at (re-)registration. To see what
would be recorded, without writing: `venv-adapter/bin/python3 adapter/codec_caps.py --dry-run`.

### E7. Unregister an ONVIF camera

The **Unregister** button in the camera's row. In order: the registry row gets
`unregisteredAt` (field by field: the row, its KVS stream, clips and settings all stay), the
producer is stopped and disabled, any live view stopped, and the MediaMTX paths (recording and
`_live`) are deleted. Every reader skips the row from then on: both GUIs and VMS's,
`list-cameras`, the agent (everything but Stop), the outage supervisor, the event watcher, the
path sync and the rematch timer. Its clips leave the cloud page until it is re-registered.
To bring it back: Scan LAN, then **Re-register** on the device labelled "Unregistered: was
cam-03" (E3) — its producer stays stopped until Start.

**Proof** (cam-03): `"unregisteredAt"` on the row; `systemctl is-enabled kvs-cam@cam03`
says `disabled`; `curl -s localhost:9997/v3/paths/get/cam03` is a 404 and stays one after
`systemctl --user restart kvs-mediamtx`; `GET /cameras` doesn't list it; a direct
`POST /cameras/cam-03/start` answers 409.

### E8. A camera registered from the other SD card

This board also runs VMS from another SD card (VMS-PORT-PLAN.md D1); both read and write the
one `cameras` table. A camera registered there has a row and a MediaMTX path here (the path
sync takes it from the registry) but **no producer**: Re-register it once (E3). The same holds
the other way round. Card swap rules: **Part G**.

### E9. Recording triggers, stream and frame rate, live view

- **Triggers.** The mode selector offers only what the camera's own ONVIF event list
  publishes (FoundAndFixed.md #65); the link beside it opens the camera's settings page,
  where a detection may first need switching on. **Test triggers** listens for up to a
  minute while someone walks past and records what fired (`detectionTests`, shown in both
  pages).
- **Stream and fps.** `stream` picks which of the camera's streams is *recorded*; `fps` sets
  10 or 15 on it — no other rate is offered, and a camera at another rate shows "choose 10 or
  15" until someone does (VMS-PORT-PLAN.md D5). Both count as done only when the wire shows
  it: MediaMTX reconnecting with video, frames counted on the wire. The keyframe interval
  keeps its length in seconds.
- **Live view.** A camera recording its main stream gets a second MediaMTX path,
  `<path>_live`, on its sub stream: the local preview plays it. The cloud page's **Live**
  button pushes it to `<camera>-live` through `kvs-live@<path>.service` while the page asks,
  without recording, and the unit stops itself 5 minutes after the last request
  (`journalctl -u kvs-live@cam03` ends with "live-view deadline passed").

### Gotchas found the hard way

- **Detection recording needs the producer running.** `clip_to_s3` cuts `ts-12s..ts+33s`
  from KVS, and KVS only returns footage it already ingested. Arming a detection mode
  while the stream is stopped produces triggers with no footage behind them — the clip
  fails and the only trace is a Lambda log. Start the stream first.
- **A clip takes ~40 s to appear** after a detection (38 s post-roll so the full window
  exists, plus Lambda time), then up to 30 s more before the browser client announces it.
  Not a fault — pressing Refresh sooner simply finds nothing.
- **Registration is not idempotent against a half-finished attempt.** If provisioning
  fails the MediaMTX path is rolled back (#36), but check `/etc/adapter/channels/` before
  retrying with the same ID.

---

## Part F — Stop everything / cost control

Per §1.2's cost rule — never leave the producer running unattended:

```bash
sudo systemctl stop 'kvs-cam*' 'kvs-live@*'   # every producer and live view: stops PutMedia billing
systemctl list-units 'kvs-cam*' 'kvs-live@*' --state=active --no-legend   # nothing listed = nothing billing
# camera/MediaMTX/agent can stay running; they cost nothing idle
```

Full teardown (deletes the KVS stream — recreating it takes seconds, see §11):

```bash
"$VMS_HOME/teardown.sh"   # if present; otherwise see guide §11 for the manual steps
```

---

## Part G — Swapping SD cards with VMS

The board is shared with VMS by swapping SD cards (VMS-PORT-PLAN.md D1): only one stack runs
at a time, and nothing on the cards is shared — except the cameras, the AWS account and the
USB stick. Before shutting down for a swap:

```bash
grep -l pending-upload /mnt/vms-buffer/outage/*/state.json   # must print nothing: both
                                                             # uploaders take any capture they find
sudo systemctl stop 'kvs-cam*' 'kvs-live@*'                    # no producer left running
```

After the swap: a camera registered from the other card has no producer here until it is
re-registered (E8). If a camera refuses event subscriptions ("Invalid Hostname" on cam-03),
the other card's watcher may have used up its subscription slots (FoundAndFixed.md #65): send
`Unsubscribe` to `/onvif/event_service/0…9`, or power-cycle the camera.

## Part H — Deploying the cloud side: Lambdas and the browser page

`cloud/deploy.sh` deploys with every check built in. It needs the operator's AWS CLI, logged in
to account `596633517506` (A6). The adapter's device certificate cannot deploy, by design.

```bash
bash cloud/deploy.sh status                     # each function and the page: live = this tree? = HEAD?
bash cloud/deploy.sh all --dry-run              # what a deploy would change, changing nothing
bash cloud/deploy.sh lambda get-hls-url list-cameras    # the named functions
bash cloud/deploy.sh lambdas                    # every function whose live code differs from the tree
bash cloud/deploy.sh page                       # client/index.html
bash cloud/deploy.sh app                        # client-app/, the Vue web panel, at /app/
bash cloud/deploy.sh all                        # lambdas, then the page
```

`status` says, per function and for the page, whether the live code is **this tree**, **git
HEAD** (this tree has undeployed changes), or **neither** — deployed from somewhere else. For the
app it shows the commit it was built from; `app --dry-run` builds and compares.

### The web panel needs Node

The web panel (`client-app/`, `WEB-PANEL-PLAN.md`) is built with Node 24 LTS, which only the
machine that builds and deploys it needs — nothing on the adapter runs it, so `setup/` doesn't
install it. Into `~/.local`, no sudo, checksum-verified:

```bash
V=v24.22.2; F=node-$V-linux-arm64.tar.xz; T=$(mktemp -d); cd "$T"
curl -fsSO https://nodejs.org/dist/$V/$F && curl -fsSO https://nodejs.org/dist/$V/SHASUMS256.txt
grep " $F\$" SHASUMS256.txt | sha256sum -c - && mkdir -p ~/.local/lib/nodejs ~/.local/bin && tar -xJf $F -C ~/.local/lib/nodejs
for b in node npm npx; do ln -sfn ~/.local/lib/nodejs/node-$V-linux-arm64/bin/$b ~/.local/bin/$b; done; cd -
```

`bash cloud/deploy.sh app` then builds a fresh copy from `client-app/package-lock.json` in a
temporary directory and publishes it at `https://dugyd3kkt36pw.cloudfront.net/app/index.html`,
beside the old page, until the cutover (WEB-PANEL-PLAN.md Phase 3). Day-to-day commands are in
`client-app/README.md`.

### The sequence for a change that spans Pi and cloud

1. **The Pi side first, verified live** (Part C, E5): the cloud only talks to it through the
   registry and MQTT, so its new fields and commands have to exist before anything uses them.
2. **The Lambdas, backward compatible.** The page that is live right now must keep working
   with them: new response fields are additive, new commands are new names, and nothing an
   old page sends changes meaning. That is what makes step 3 safe to do a minute — or a day —
   later. Check what changes first: `bash cloud/deploy.sh lambdas --dry-run`.
3. **The page:** `bash cloud/deploy.sh page`. Reload the browser; `no-cache` means the next load
   gets it.
4. **Verify:** `bash cloud/deploy.sh status` says "this tree" everywhere; `GET /cameras` through
   API Gateway (below) answers 200; the page works signed in.
5. **Commit what you deployed.** The page's bucket keeps no versions, so the tree is the only
   record of what is live (the script's backups below are the only other copy).

### The rules the script enforces

- **The account is shared with VMS** (github.com/vizdr/VMS). This repository deploys the
  unprefixed functions, `kvs-demo-api`, the page's bucket and distribution; VMS deploys only
  `pir-*` copies, and `deploy.sh` refuses any `pir-*` name. Live code that equals neither this
  tree nor HEAD was put there by someone else, and isn't overwritten without `--force`: look at
  it first (FoundAndFixed.md #48).
- **`clip-to-s3` is VMS's too:** its event shape and its clip rows must stay backward
  compatible. The script warns whenever it deploys it.
- **A backup before every overwrite:** the live copy goes to
  `~/.local/state/vms-deploy-backups/<UTC time>/` first, which is what `rollback` uses.
- **One file per function,** zipped fresh in a temporary directory. The zips are build output
  and not kept in git.
- **The page goes up with `Cache-Control: no-cache, must-revalidate`:** browsers revalidate
  rather than keep a stale page (FoundAndFixed.md #11), and CloudFront does the same, so an
  upload is live at once (`x-cache: RefreshHit`) with no invalidation.
- **Done means verified:** after each deploy the live code is downloaded again and compared
  with the tree, and the page is fetched back through CloudFront.
- **The app goes up under `/app/` only,** never over the page. Its hashed assets are cached for
  good and go up first; `index.html` goes up last with the page's `no-cache`, so a browser never
  gets an `index.html` whose assets aren't there yet. A `version.json` beside it records what was
  uploaded: a live `/app/` without a matching one wasn't put there by the script, and needs
  `--force`.

### Rolling back

```bash
bash cloud/deploy.sh rollback lambda get-hls-url    # the copy saved before its last deploy
bash cloud/deploy.sh rollback page
bash cloud/deploy.sh rollback app                   # none after a first deploy: aws s3 rm --recursive the prefix
```

Or deploy an older version from git: check it out (`git stash` your changes, or
`git checkout <commit> -- cloud/lambda/<file>.py client/index.html`), run `deploy.sh` with
`--force` (the live code is now "neither"), then restore your tree.

### Checking the API without signing in

`test-invoke-method` calls a route through API Gateway and skips the Cognito authorizer —
read-only routes only:

```bash
API=$(aws apigateway get-rest-apis --query "items[?name=='kvs-demo-api'].id" --output text)
RID=$(aws apigateway get-resources --rest-api-id "$API" --query "items[?path=='/cameras'].id" --output text)
aws apigateway test-invoke-method --rest-api-id "$API" --resource-id "$RID" --http-method GET \
  --query '[status, body]' --output text | head -c 300
```

It hands the query string to the Lambda undecoded, unlike a real request: pass a value that
holds a `+` decoded.

### By hand, if the script can't be used

The same steps as one-liners — keep each command on **one line**: a lost closing quote in a
pasted multi-line command leaves the shell waiting at `>`, and nothing runs.

```bash
F=get_hls_url; T=$(mktemp -d); cp cloud/lambda/$F.py "$T/" && (cd "$T" && zip -qX $F.zip $F.py)
aws lambda update-function-code --function-name ${F//_/-} --zip-file fileb://$T/$F.zip --query LastModified
aws lambda wait function-updated --function-name ${F//_/-}
aws s3 cp client/index.html s3://vms-demo-client-596633517506/index.html --content-type text/html --cache-control "no-cache, must-revalidate"
curl -fsS https://dugyd3kkt36pw.cloudfront.net/index.html | cmp - client/index.html && echo "page live"
```

Before overwriting by hand, look at what is live (`bash cloud/deploy.sh status`, or
`aws lambda get-function --function-name <fn> --query Code.Location` for a download link).

### New functions, routes and permissions

`deploy.sh` only updates existing functions. A new function, its role, its API route and
authorizer, or a widened permission is created once with the commands in the guide (§8, §9).
Its policy document goes into `cloud/iam/`, exported back from AWS in the same change
(`aws iam get-role-policy … --query PolicyDocument`), so the files match what is deployed
(FoundAndFixed.md #41).

## Known traps not obvious from a cold read

- **`kvssink: no element "kvssink"`** — you're in a shell that never sourced `.bashrc`
  (any systemd unit, most non-interactive contexts). Export `GST_PLUGIN_PATH`/
  `LD_LIBRARY_PATH` explicitly (§4.3).
- **`create-stream`/`ListFragments`/`GetHLSStreamingSessionURL` → AccessDenied** —
  you're using `kvs-demo-producer`'s deliberately scoped-down credentials (or the
  adapter's certificate) for something that needs your own admin AWS identity. `unset
  AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY` to fall back to your default profile.
- **`AWS_ERROR_MQTT_UNEXPECTED_HANGUP` connecting the agent** — check nothing in the
  connection declares a Last Will with `retain=True`; IoT Core rejects the CONNECT for a
  retained LWT (#6).
- **`UnrecognizedClientException` / `security token invalid`** — check for a stray
  `AWS_SESSION_TOKEN` left from an earlier, unrelated credential export in the same
  shell; `unset` it.
- **VS Code / SSH loses the Pi, and it looks dead** — usually the Wi-Fi uplink stall of
  FoundAndFixed.md #79: wait up to 10 minutes (the router's next rekey reconnects it) before
  restarting, and read `~/.local/state/netwatch.log` afterwards (A1).
- **A sudden reboot mid-build** — see §1.4 in full (#1); the short version is `earlyoom` +
  swap + firmware update + CPU-pinning the build away from the WiFi IRQ cores. Pinning
  only helps on a kernel with `isolcpus` (the first Pi had `1,2`; check yours with
  `detect-hw.sh --print`, A9).
- **"Is the stream alive?" → no, but every `systemctl` check said `active`** —
  `kvs-cam01.service` crash-loops silently against a 404 if `kvs-camera-publish` isn't
  also running; it has no way to tell "no camera feed" apart from any other transient
  failure. Always verify with Part C's `ffprobe` commands, not unit status alone (#5).
