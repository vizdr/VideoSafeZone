# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A buildable, portfolio-grade "Cloud Adapter" demo: a Raspberry Pi 4B pulls video from
local cameras and pushes it outbound to AWS (Kinesis Video Streams), takes commands over
an outbound MQTT session to AWS IoT Core, and serves a browser client through API
Gateway — with **zero inbound ports opened** on the home router. The architectural thesis
(every arrow crosses the router outbound-initiated) is written up in
`OUTBOUND-CLOUD.md`.

`README.md` is the front door — project overview, feature list, and a Pi 4B install guide
written for someone arriving cold. It summarises; it is never the source of truth.

**`Demo-AWS-Video-revCosts4.md` is the canonical, actively-maintained build guide and the
single source of truth for *why* things are built the way they are** — a narrative log
of the real build and its design decisions, updated continuously as the system evolves.
**`FoundAndFixed.md` holds every defect found in this project** — symptom, cause,
diagnosis, fix — as permanently numbered entries; the other docs keep only the rule a bug
left behind plus a `FoundAndFixed.md #N` reference. `LAUNCH.md` is the short operational runbook (what to
actually run, in order, assuming the guide has already been followed once) — check it
first for "how do I start/stop/verify this." Its Part A is also the fresh-Pi setup
checklist (SDK build with its patches, venv, MediaMTX binary, AWS CLI, device
certificate, systemd unit files, optional USB outage-buffer stick), each step with a command
that proves it worked. `AUDIO.md` records how optional per-camera audio was designed and built (guide §18 is the
canonical reference; `AUDIO.md` keeps the reasoning, the rules its two silent bugs left
(FoundAndFixed.md #15, #16), and the claims that were withdrawn). `OUTAGE.md` is the working record for durable outage
buffering (guide §16.3c) — design, measurements and open questions — and folds into
§16.3c when that work completes; it is authoritative for that feature in the meantime.
`COSTS-1.4.md` is the cost model and authoritative for any bitrate or dollar figure;
`Camera-Features.md` is the verified-vs-advertised inventory of the ONVIF camera (`cam-02`).
`NETWORK.md` holds the networking notes (MediaMTX's role and ports, how discovery was
built, the still-open camera-segment isolation option, and a summary of the codec choice),
reviewed against the system 2026-09-26. `measurements/codec-phase0.md` is the evidence
behind per-camera H.264/H.265 selection (guide §21): hardware, camera, KVS and browser
measurements, and the decisions they led to. `Demo-AWS-Video-MCh-15.md` and `COSTS-1.3.md` are earlier material and
may be stale relative to the current guide; `SafeZone_Group-cloud_EN-rev_1.md` is the original product-requirements sketch this
demo is modeled on. **When in doubt about current architecture or "why is it done this
way," read `Demo-AWS-Video-revCosts4.md` (or grep it for the relevant §-number) before
guessing from code alone** — most non-obvious decisions are explained there, and the
real failure that motivated one is the `FoundAndFixed.md` entry it cites.

**When you find or fix a bug, record it in `FoundAndFixed.md`:** append a new entry
(never renumber or reuse a number), add it to the overview table, and cite it as
`FoundAndFixed.md #N` wherever the resulting rule is documented — don't retell the story
in the guide, LAUNCH, README or the feature docs.

There is no build system, package manifest, or automated test suite. Verification is
done live, against the running Pi and AWS account — see "Verifying changes" below.

## Commands

### Adapter-side Python (agent, ONVIF admin, discovery)

All of `adapter/*.py` and `adapter/onvif-admin/` run under one venv:
```bash
source venv-adapter/bin/activate   # or call venv-adapter/bin/python3 directly
```

Run the local ONVIF admin GUI directly (normally managed by `onvif-admin.service`):
```bash
cd adapter/onvif-admin && python3 app.py   # http://<pi-ip>:8080
```

Run WS-Discovery from the CLI:
```bash
python3 adapter/bin/discover-onvif.py --user admin --password *** --timeout 5
```

Watch the MQTT control-plane state topic live (debug aid):
```bash
python3 adapter/observe_state.py
```

### Deploying a Lambda

Every function in `cloud/lambda/` is deployed the same way — zip the single file, push it:
```bash
cd cloud/lambda && zip -q <name>.zip <name>.py && \
  aws lambda update-function-code --function-name <name> --zip-file fileb://<name>.zip
```
Function names use hyphens (`get-hls-url`), files use underscores (`get_hls_url.py`).
IAM policy documents for each function's role live in `cloud/iam/`.

### Deploying the browser client

```bash
aws s3 cp client/index.html s3://vms-demo-client-596633517506/index.html \
  --content-type text/html --cache-control "no-cache, must-revalidate"
```
Served at **https://dugyd3kkt36pw.cloudfront.net** (CloudFront + OAC, guide §8.5.1). The
`--cache-control` flag is required and now does double duty: it prevents stale clients
after a deploy (FoundAndFixed.md #11), and it makes CloudFront revalidate rather than
serve a cached copy, so an upload is live immediately (`x-cache: RefreshHit`). Drop the
header and you also need `aws cloudfront create-invalidation --distribution-id
E1B12167KKII6B --paths '/*'`.

The plain-HTTP S3 website URL is still live pending §8.5.1's final cutover (OAC-only
bucket policy → Block Public Access → `delete-bucket-website`).

### systemd — two separate managers, easy to mix up

**System units** (root, `/etc/systemd/system/`, need `sudo systemctl`): `kvs-cam01.service`,
`kvs-cam02.service`, `kvs-cam@.service` (template for GUI-provisioned cameras, §16.6) — the
actual KVS producers that cost money while running.

**User units** (`~/.config/systemd/user/`, `systemctl --user`, no sudo): `kvs-mediamtx`,
`kvs-camera-init`, `kvs-camera-publish`, `kvs-agent`, `onvif-admin`,
`kvs-event-watcher` (ONVIF detection → clips), `kvs-outage-buffer` +
`kvs-outage-uploader` (durable outage buffering, `OUTAGE.md`), and the
`kvs-camera-rematch.timer` oneshot (follows ONVIF cameras to a new IP).

A unit in one manager **cannot** `Requires=`/`After=` a unit in the other — they're
independent systemd instances; a cross-manager `Requires=` fails with "Unit not found"
(FoundAndFixed.md #12).

Unit files are **not in git**. `LAUNCH.md` A8 generates all of them, writing this clone's
literal absolute paths into `ExecStart=`/`WorkingDirectory=`/`Environment=`. systemd
never reads `.bashrc` and doesn't expand `$VMS_HOME` or `~`. When the clone moves, re-run A8.

One-time launch (enables everything to start at boot), order and startup gotchas: `LAUNCH.md` Part B; after a reboot just verify, Part C.

### Paths: never hardcode the clone location

The repo has lived at more than one path (`~/MyProjects/VMS`, now
`~/Projects/VideoSafeZone`), and hardcoded `/home/...` paths broke on the move
(FoundAndFixed.md #25). Code in
`adapter/` resolves the root as `$VMS_HOME` if set, else from its own location. Python
modules in `adapter/` use
`os.environ.get("VMS_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`.
Scripts in `adapter/bin/` use
`VMS_HOME="${VMS_HOME:-$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)}"`.
Derive the venv's `pythonX.Y` directory from `sys.version_info` rather than spelling it
out. Follow the same pattern in new code; don't rely on `VMS_HOME` being exported, since
systemd units don't have it.

The same goes for **deployment identity** (AWS region, IoT Thing name, role alias, IoT
credential/data endpoints, evidence bucket, and MQTT topics derived from the Thing name).
It lives only in `/etc/adapter/adapter.env` (template `config/adapter.env.example`,
installed by `LAUNCH.md` A7). Python code reads it via `import config`
(`config.AWS_REGION`, `config.TOPIC_PREFIX`, …); bash code does
`source "${VMS_HOME}/adapter/bin/adapter-config.sh"`. Environment variables override the
file, and a missing key is a hard error, never a silent default — except where a script
is designed to degrade (e.g. `camera-audio.py` falls back to `AUDIO=off`), in which case
the config load belongs inside its `try`. Never write an endpoint, region or Thing name
into code again. Cloud-side code (`cloud/lambda/`, `client/index.html`) is configured
separately, through Lambda environment variables and the client's own constants.

**Local hardware is detected, not named.** No `/dev/video*`, `/dev/v4l/by-id/<model>`,
`hw:CARD=<name>` or mountpoint literals in scripts. `adapter/bin/detect-hw.sh` provides
`camera_setup <mediamtx-path>` (sets `CAM`), `alsa_card_for_video` (the mic on the same
USB device, via sysfs), `buffer_mount` (by `LABEL=vms-buffer`) and `isolated_cpus`.
Detection must never guess: zero or several matches is an error. Per-camera tuning and
selectors (`CAM_MATCH`, `CAM_DEVICE`, `CAPS`, `V4L2_*_CTRLS`) go in
`/etc/adapter/cameras/<path>.env` (template `config/cameras/cam01.env.example`), with
the current tuning as defaults in the script. Hardware choice must stay local, never in
the DynamoDB registry: cam-01 has to start while AWS is unreachable.
`detect-hw.sh --print` shows what would be used.

### Verifying changes

**`systemctl ... is-active` is not proof anything is actually working** — a producer unit
can report `active` while crash-looping silently against a dead upstream RTSP source.
Always verify media is actually flowing:
```bash
ffprobe -rtsp_transport tcp rtsp://127.0.0.1:8554/cam01      # local capture proof
ffprobe "http://127.0.0.1:8888/cam01/index.m3u8"             # MediaMTX's own local HLS
# cloud path: GetHLSStreamingSessionURL -> ffprobe the returned URL (LAUNCH.md Part C)
```
When changing anything in a GStreamer pipeline (`adapter/bin/*.sh`), don't stop at "no
pipeline errors and CPU/logs look right" — decode an actual frame and look at it
(`ffmpeg -i <url> -frames:v 1 out.png`, then view it). A wrong H.264 profile that rendered
black only in a browser was caught this way (FoundAndFixed.md #13), and so was the
reverse: a probe that looked corrupted but was only a probing artifact. CLI tools like `ffmpeg`/`ffprobe` are far more tolerant of malformed streams than a
browser's MSE decoder, so "ffmpeg played it" is necessary but not sufficient evidence.

## Architecture

### Two independent camera pipelines converge on MediaMTX

`cam-01` is a USB webcam that must be transcoded (it delivers MJPG; YUYV is too slow at
720p over USB 2.0); `cam-02` (and any camera
added later) is a real ONVIF/RTSP IP camera that passes through untouched. Both publish
into **MediaMTX**, which is the local hub for everything downstream: it re-serves RTSP,
exposes its own local HLS output (port 8888, LAN-reachable — used only by the ONVIF admin
GUI's browser-side preview, never the cloud path), and exposes a control API (port 9997,
localhost-only) used to add camera paths live without a config-file rewrite + restart
(which would otherwise drop every other camera's connection). MediaMTX doesn't persist
API changes, so `mediamtx.yml` deliberately has **no camera paths**:
`adapter/sync_mediamtx_paths.py` runs as `ExecStartPost=` of `kvs-mediamtx.service` and
re-adds every passthrough camera's path from its registry `rtspUrl` after each start. When
AWS is unreachable it uses a 0600 local cache. It never deletes a path, and it leaves a
path alone when the source is unchanged, because patching `source` reconnects readers.
Never put a camera address or credential back into `mediamtx.yml` (FoundAndFixed.md #31, #32). `cam-01`'s pipeline (`adapter/bin/publish-cam01.sh`)
uses the Pi 4's hardware JPEG-decode/ISP-convert/H.264-encode blocks (`v4l2jpegdec`,
`v4l2convert`, `v4l2h264enc` — all separate V4L2 M2M devices under `bcm2835-codec`) rather
than software elements, for a ~2x CPU reduction; the encoder must be told `profile=high`
explicitly, since GStreamer's `v4l2h264enc` wrapper otherwise negotiates Baseline even
though the hardware control's own default is High, and Baseline broke browser (but not
`ffmpeg`) playback (FoundAndFixed.md #13).

**Audio is optional, per-camera, and off by default** (guide §18). Two registry flags gate
it — `audioCapable` (hardware fact, set at registration) and `audioEnabled` (user choice,
set from either GUI) — and the producer scripts read them once at startup via
`adapter/bin/camera-audio.py`. The setting therefore applies on the camera's **next
Start**, which is deliberate: KVS rejects a stream whose fragments change from video-only
to audio+video partway through, so applying it live would break `GetClip` across the
boundary. With audio off, every pipeline sends exactly the pre-audio video — but an audio
track the source still carries must be consumed by a `fakesink`, never left unlinked:
an unlinked `rtspsrc` pad intermittently kills the producer at startup (FoundAndFixed.md #43).

**Video codec is per camera — H.264 or H.265 — and is set on the camera itself** (guide
§21). Only a codec the hardware encodes is offered: the Pi 4 has no HEVC encoder and
software x265 can't hold 720p15, so `cam-01` is H.264-only; an ONVIF camera offers what
its encoder does. The admin GUI switches the camera's encoder over ONVIF Media2
(`adapter/onvif_media2.py`, every write read back), so the preview, outage buffer and
cloud stream all follow; there is deliberately no cloud route to change it. Capabilities
and the hardware-first default live in the registry (`adapter/codec_caps.py`), for the
GUIs only. **Producers never ask the registry which codec to expect**:
`adapter/bin/stream-codec.py` asks MediaMTX what is arriving, and `producer-lib.sh`
builds the chain. H.265 caps must pin `stream-format=hvc1`, or KVS ingests fine and can't
play it back (kvssink sends CPD only from `codec_data`). KVS also refuses a clip or
ON_DEMAND session spanning a codec switch, so the clip Lambdas split windows at
`videoCodecActiveSince`. H.264 stays the default because browser H.265 support depends
on browser *and* machine; both GUIs probe the viewer's browser at runtime.

The constraint that shapes all of it: **KVS's ingest and playback paths accept different
codecs, and ingest is the permissive one.** `kvssink` takes G.711 and malformed AAC
codec-private-data without complaint; `GetHLSStreamingSessionURL`/`GetClip` then refuse to
serve them. So audio is always transcoded to AAC at the producer (never encoded earlier
and passed through RTSP — `rtspclientsink` payloads AAC as LATM, which mangles the CPD;
FoundAndFixed.md #16),
and the sample rate is chosen against the *video frame rate* rather than for fidelity,
because `kvssink` synthesises the DTS that GStreamer audio buffers lack from a counter
shared with the video track. Guide §18.3 has the arithmetic; the short version is that
audio frame duration must exceed the video frame interval, and getting it wrong silently
loses half the audio (FoundAndFixed.md #15).

**Durable outage buffering** (`OUTAGE.md`, guide §16.3c) is also MediaMTX's job, not a
pipeline change: it records a rolling 2-minute window to a USB stick
(`/mnt/vms-buffer`) for any camera whose registry row asks for it *and* whose producer is
running, keeps everything once AWS goes unreachable, and backfills merged clips into the
existing evidence-clip list on recovery. Per-camera, **off by default**. Two things make
it work and are easy to undo by accident: `recordDeleteAfter` is `0s` **permanently** (the
supervisor owns retention — handing it to MediaMTX's cleaner would let one raced tick
delete the captured outage), and **no MediaMTX API call happens at outage onset**, because
patching any record field rebuilds the recorder and puts a keyframe seam exactly at T0.
Measured effect on a 5-minute outage: gap-fill 27.4% → 99.8%.

A separate KVS producer process per camera (`kvs-cam01.service` / `kvs-cam02.service` /
GUI-registered `kvs-cam@<path>.service` instances, named by MediaMTX path, e.g.
`kvs-cam@cam03` — not by camera ID) pulls from MediaMTX's RTSP and pushes to its own
Kinesis Video Stream. Each runs its pipeline through `producer_run`
(`adapter/bin/producer-lib.sh`), which exits non-zero whenever the pipeline ends: a
session MediaMTX closes (camera dropped, codec switched) is otherwise a clean exit that
`Restart=on-failure` ignores, leaving the stream down while the unit looks stopped
(FoundAndFixed.md #44). **This is the layer Start/Stop buttons (in either GUI) actually
control** — toggling it does not affect MediaMTX or the camera's own feed, which keep
running regardless. This is a common point of confusion: the "local preview" (MediaMTX
HLS) and the "KVS push" (cloud) are independent signals.

### The camera registry is the single source of truth — not a hardcoded list

A DynamoDB table `cameras` (PK `cameraId`, e.g. `"cam-01"`) holds each camera's mode
(`transcode`/`passthrough`), ONVIF credentials, RTSP URL, IR-control capability, KVS
stream ARN, and its video-codec capabilities and choice (`videoCodecCaps`, `videoCodec`,
`videoCodecActive`; guide §21.2). Every Lambda that needs to validate a camera ID, and `agent.py`'s MQTT
handler, read this table directly — there is no hardcoded allow-list anywhere. Adding a
camera through the ONVIF admin GUI (below) makes it work everywhere (cloud client,
MQTT control, all API routes) immediately, with no code change. IAM for
camera-ARN-scoped actions (`kinesisvideo:*`, per-Lambda) uses a `stream/cam-*/*` wildcard
rather than enumerated ARNs specifically so this stays true without an IAM edit per
camera — a deliberate least-privilege tradeoff, not an oversight.

Credential storage in that table is phased: currently a plain `onvifPassword` attribute
(Phase 1); a planned Phase 2 moves it to an SSM Parameter Store `SecureString` referenced
by a `credentialRef`, decrypted only via the adapter's own device identity. Not yet built.

### Device identity: one X.509 cert, no static keys on the Pi

The adapter authenticates to AWS as IoT Thing `adapter-01` via an X.509 certificate
(`certs/`, gitignored) and an IoT **role alias** (`KVSAdapterRoleAlias`), which vends
short-lived AWS credentials over HTTPS given the cert for mTLS. `kvssink` uses this
directly for KVS `PutMedia`. `adapter/aws_device_creds.py` wraps the same
credentials-endpoint call for arbitrary boto3 use — `agent.py` and
`adapter/onvif-admin/app.py` both call it (fresh per request; the underlying token has a
3600s TTL, so caching a session at process start would silently break a long-running
daemon after an hour). This is why the role's IAM policy, not a second credential, is
what's widened whenever the adapter needs a new AWS permission (e.g. `CreateStream`,
DynamoDB access on `cameras`) — see `cloud/iam/kvs-producer-policy.json`. The files in
`cloud/iam/` must match what is deployed: when you change a policy in AWS, export it back
(`aws iam get-role-policy … --query PolicyDocument`) in the same change
(FoundAndFixed.md #41).

### Two GUIs, deliberately not one

`client/index.html` — the **cloud** client. Static, S3-hosted, Cognito-authenticated,
reachable from anywhere. Talks only to API Gateway/Lambda/MQTT; has no LAN access.
Renders one panel per camera fetched from `GET /cameras` (not hardcoded), with live view
(cloud HLS via KVS), manual recording, evidence-clip browsing/tiering, per-camera IR
control where applicable, and each camera's codec shown read-only.

`adapter/onvif-admin/` — a small local Flask app, LAN-only, no login, run directly on the
Pi. It exists because **WS-Discovery is UDP multicast and only works from a process on
the camera's own LAN segment** — the cloud client and Lambda structurally cannot reach it.
It does discovery (`adapter/onvif_discovery.py`, shared with the `discover-onvif.py` CLI),
one-click registration (adds the MediaMTX path live, provisions a `kvs-cam@.service`
instance via the input-validated `adapter/bin/provision-camera.sh`, creates the KVS
stream, writes the `cameras` row), re-registration for an already-known camera (updates
MediaMTX's path + the registry row, but deliberately never touches systemd for `cam-01`/
`cam-02` — they predate the `kvs-cam@` template, and re-provisioning them would start a
second, conflicting producer), and local control (Start/Stop, IR mode, the camera's video codec, a live
`systemctl is-active`-backed status column, and the local-HLS preview mentioned above).

A camera's identity is its WS-Discovery endpoint reference (`onvifEndpointRef`,
`urn:uuid:…`), not its IP address. The GUI stores it at registration and matches scan
results by it first. `adapter/rematch_cameras.py` (`kvs-camera-rematch.timer`, every
5 min) learns it for older rows and follows a camera to a new address: it rewrites
`onvifHost` and the host in `rtspUrl` with a conditional registry write, then updates the
MediaMTX path. It must never guess: duplicate identities and address clashes are skipped.
MediaMTX's API deletes paths with HTTP `DELETE`; `POST` to the delete route is a 404
(FoundAndFixed.md #36).

Naming quirk both share: the camera identifier is hyphenated (`cam-01`, used for the KVS
stream name, DynamoDB key, S3 key prefix, and every API field) but the MediaMTX path name
and systemd unit suffix are not (`cam01`, `kvs-cam01.service`). `camera_control.py`'s
`mediamtx_path_name()`/`unit_name()` are the one place this conversion happens, and
`unit_name()` also knows the two unit families: `kvs-cam01.service` for cam-01/cam-02,
`kvs-cam@cam03.service` for GUI-registered cameras (it checks
`/etc/adapter/channels/<path>.env`). Both families were once missed, and `systemctl`
silently no-op'd each time (FoundAndFixed.md #7, #37). Route every unit-name computation
through these helpers.

### Cost is a first-order design constraint, not an afterthought

The guide's §1.2 cost model treats "never leave `kvs-cam0N.service` running unattended"
as a hard rule (it's what incurs `PutMedia` charges) — this shows up throughout the code
as the reason Start/Stop exists as an explicit action rather than the producer just
running continuously, and why "stop the stream" is step one of the teardown/shutdown
sequence in `LAUNCH.md` Part F.
