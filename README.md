# Cloud Video Adapter

A Raspberry Pi 4B pulls video from local cameras, pushes it to AWS, takes commands back,
and serves a browser client from anywhere — with **zero inbound ports opened** on the home
router.

Every arrow crosses the router outbound-initiated. There is no port forward, no VPN, no
dynamic-DNS, and no inbound firewall rule anywhere in the system. That constraint is the
point: it is what makes a device deployable in a building whose network you do not
control.

This is a working build, not a sketch. Where a number appears below, it was measured on
the running system; where something does not work, it says so.

---

## Contents

- [Architecture](#architecture) · [Features](#features) · [Measured results](#measured-results)
- [Hardware](#hardware) · [Install](#install-on-a-raspberry-pi-4b-4-gb) · [Daily use](#daily-use)
- [Cost](#cost) · [Repository map](#repository-map) · [Documentation](#documentation)
- [Non-goals](#deliberate-non-goals) · [Rebuilding elsewhere](#rebuilding-on-a-different-aws-account)

---

## Architecture

```
  LAN (no inbound ports)                    │  every arrow outbound-initiated
                                            │
  cam-01  USB webcam ──MJPEG──┐             │
  (AVerMedia PW310)           │             │
                              ▼             │
                      ┌───────────────┐     │      ┌──────────────────────┐
                      │   MediaMTX    │─RTSP┼─────►│ Kinesis Video Streams│
  cam-02  ONVIF IPC ──┤  local hub    │     │      │  live HLS + clips    │
  (RTSP, H.264)       │  :8554 RTSP   │     │      └──────────────────────┘
                      │  :8888 HLS    │     │
                      │  :9997 API    │     │      ┌──────────────────────┐
                      └───────┬───────┘     │      │  IoT Core  MQTT:443  │
                              │             │◄────►│  start/stop/IR       │
                    ┌─────────▼─────────┐   │      └──────────────────────┘
                    │  USB outage buffer│   │
                    │  /mnt/vms-buffer  │───┼─────►┌──────────────────────┐
                    │  rolling 2 min    │backfill  │  S3 evidence clips   │
                    └───────────────────┘   │      │  + DynamoDB registry │
                                            │      └──────────────────────┘
                                            │                 ▲
                                            │      ┌──────────┴───────────┐
                                            │      │ API Gateway + Lambda │
                                            │      │ Cognito · CloudFront │
                                            │      └──────────┬───────────┘
                                            │                 │
                                                     browser client (anywhere)
```

**MediaMTX is the local hub.** Both cameras publish into it; everything downstream reads
from it. That is what lets the cloud producer, the local preview and the outage buffer be
independent of each other and of the cameras.

**Cost is a first-order constraint.** The KVS producer is *not* a background service — it
runs only when started, because that is what incurs `PutMedia` charges. Start/Stop in
either GUI controls exactly that process.

---

## Features

| | |
|---|---|
| **Multi-camera** | USB (transcoded) and ONVIF/RTSP (passthrough) side by side. Cameras come from a DynamoDB registry, not a hardcoded list — adding one works everywhere immediately |
| **Hardware encoding** | Pi 4's JPEG-decode, ISP-convert and H.264-encode blocks, ~2× CPU reduction vs software |
| **Live view** | Cloud HLS via KVS, Cognito-authenticated, served over HTTPS through CloudFront. **Live** is separate from recording: it shows the camera's sub stream while someone watches, and the Pi stops it by itself a few minutes after the page closes |
| **Evidence clips** | Manual recording (while Start runs), plus detection-triggered clips; browse (ten per page, newest first), play, re-tier (Standard / IA / Deep Archive) and delete from the browser |
| **ONVIF detection, per camera** | Motion, cell-grid motion and human detection, each offered only where the camera's own event list publishes it; **Test triggers** listens while you walk past and records what fired |
| **ONVIF device control** | IR-cut filter (day/night) from either GUI; WS-Discovery and one-click registration — including cameras without a password — from a local admin app; which stream is recorded (main/sub) and at 10 or 15 fps; display names |
| **Unregister / Re-register** | A camera can be hidden everywhere and stopped while its clips and settings are kept; Discover offers it back. A camera follows its WS-Discovery identity to a new IP address by itself |
| **Optional audio** | Per-camera, off by default, for both cameras — [`AUDIO.md`](AUDIO.md) |
| **H.264 or H.265, per camera** | Wherever the camera's own hardware encodes it; set on the camera from the local admin GUI, shown read-only in the cloud client. H.264 stays the default, and a viewer whose browser can't decode H.265 is told how to fix it — guide §21 |
| **Durable outage buffering** | Records to a USB stick while AWS is unreachable and backfills on recovery — [`OUTAGE.md`](OUTAGE.md) |
| **Remote control** | Outbound MQTT over 443 (ALPN), so it traverses HTTPS-only firewalls |
| **Device identity** | One X.509 certificate and an IoT role alias — **no static AWS keys on the Pi** |

### Two GUIs, deliberately not one

**Cloud client** (`client/index.html`) — static, S3-hosted behind CloudFront,
Cognito-authenticated, reachable from anywhere. Live view, recording, clips, per-camera
settings.

**Local admin** (`adapter/onvif-admin/`) — a small Flask app on the Pi, LAN-only. It
exists because **WS-Discovery is UDP multicast and only works from the camera's own LAN
segment** — the cloud client and Lambda structurally cannot reach it.

---

## Measured results

The numbers this project exists to produce. Method and caveats in the linked docs.

| | Result |
|---|---|
| **Outage resilience** | A 5-minute WAN outage lost **72.6 %** of its footage before durable buffering, **0.2 %** after — gap-fill 27.4 % → **99.8 %** ([`measurements/reconnect_timeline.md`](measurements/reconnect_timeline.md)) |
| **Outage detection** | 4 s, against a 120 s pre-roll |
| **CPU, ONVIF passthrough** | ~3.5 % (no decode/encode stage at all) |
| **CPU, USB transcode** | 28 % → **13 %** by using the Pi's hardware V4L2 blocks instead of software elements |
| **Bitrate is driven by light, not motion** | Illumination moves bitrate **2.4–4.2×**; motion ~34 % ([`COSTS-1.4.md`](COSTS-1.4.md) §3) |
| **Duty cycle is the largest lever** | Measured 0.28 % overnight vs 12.8 % on a busy afternoon — ~20× |
| **Detection false positives** | Zero across 9.01 h of heartbeat-backed silence |

Three bugs worth knowing about, because they were all silent and none was caught by
"it ran without errors" — all 83 found so far are in [`FoundAndFixed.md`](FoundAndFixed.md):

- H.264 negotiated as **Baseline**: `ffmpeg` played it, browsers rendered black (#13).
- The wrong audio sample rate silently loses half the audio, because `kvssink` shares one
  DTS counter between tracks (#15).
- AAC over RTSP becomes LATM: KVS ingests it, then refuses to play it back (#16).

The pattern behind all three: **KVS's ingest path is more permissive than its playback
path**, and `ffmpeg` is more permissive than a browser's MSE decoder. Decode a frame and
open a browser; "no errors" proves nothing.

---

## Hardware

| | |
|---|---|
| Raspberry Pi 4B | **4 GB** (what this was built on; 8 GB makes the SDK build easier) |
| Storage | SD card for the OS, plus a **USB stick** if you want outage buffering (57 GB here) |
| `cam-01` | AVerMedia PW310 USB webcam — MJPG at 720p (YUYV only at 8 fps), so it must be transcoded |
| `cam-02`… | Any ONVIF/RTSP camera that emits H.264 or H.265. Tested: an OEM rebrand of mixed Hikvision/Dahua lineage ([`Camera-Features.md`](Camera-Features.md)), an H4-52POX-S, and a 5831-F-Q PTZ camera that has no password |
| Network | Wired or Wi-Fi; **no router configuration at all** |
| OS | Raspberry Pi OS (Debian trixie), 64-bit |

An AWS account is required. The demo runs well under $5/month; see [Cost](#cost).

---

## Install on a Raspberry Pi 4B (4 GB)

A fresh SD card to a running adapter is scripted, in [`setup/`](setup/). Each script is
idempotent (re-running it changes nothing that's already right), explains its steps in its
comments, and ends with PASS/FAIL proofs. Detail and reasons: [`LAUNCH.md`](LAUNCH.md) Part A.

```bash
# Raspberry Pi OS (64-bit, Debian trixie), your user, Wi-Fi/SSH -- then:
git clone https://github.com/vizdr/VideoSafeZone.git ~/Projects/VideoSafeZone
bash ~/Projects/VideoSafeZone/setup/setup-all.sh      # as yourself: it asks for sudo once
bash ~/Projects/VideoSafeZone/setup/setup-check.sh    # every line "ok"
```

| Script | What it does |
|---|---|
| `setup-system.sh` | **Stability first** — on 4 GB the SDK build kills the Pi without it: earlyoom tuned to kill compilers first, a 3 GB swapfile, a persistent journal, Wi-Fi power save off. Then the packages, `/etc/adapter/adapter.env`, the system units (installed, never enabled — they cost money), a narrow sudo rule, and the USB stick if one labelled `vms-buffer` is plugged in |
| `setup-user.sh` | Python venv, the pinned MediaMTX binary, the AWS CLI (signature-checked), the user units |
| `setup-sdk.sh` | The KVS Producer SDK with its five patches, built single-threaded in the background (**1.5–2.5 hours**) — or restored in seconds from the stick, where every finished build caches itself |
| `setup-identity.sh` | The device certificate: restored from the stick's backup, or — the first time on an account — created with `aws login --remote` and `setup-identity.sh create`, then `backup` |
| `setup-launch.sh` | Starts everything (from then on at every boot) and proves video flows |

The certificate is the only credential on the device and is **gitignored**; everything else
is vended short-lived through an IoT role alias. The AWS resources themselves (KVS, IAM role
and alias, IoT Thing and policy, DynamoDB, Cognito, Lambdas, API Gateway, S3 + CloudFront)
are created once per account — commands in the guide's §3/§6/§8, policy documents in
[`cloud/`](cloud/) — and the cloud code is deployed with `cloud/deploy.sh` (`LAUNCH.md` Part H).

**The USB stick** (optional, for outage recording; it also carries the SDK cache and the
identity backup across fresh cards) is ext4 labelled `vms-buffer`. The scripts never format
anything: a new stick is `sudo mkfs.ext4 -m 0 -L vms-buffer /dev/sdX1` by hand, after
checking the name twice (`LAUNCH.md` A10).

**A partial launch fails silently, not obviously** — every unit can report `active` while the
stream is dead. That is why `setup-launch.sh` checks that video actually flows, and why
`LAUNCH.md` Part C exists.

---

## Daily use

```bash
# does media actually flow?  (`is-active` is not proof of anything)
ffprobe -rtsp_transport tcp rtsp://127.0.0.1:8554/cam01

# cloud path
ffprobe "$(aws kinesis-video-archived-media get-hls-streaming-session-url ... )"
```

- **Browser client** — the CloudFront URL. Sign in. **Live** to watch a camera (it stops by
  itself after you leave), **Start** to record it to the cloud, browse and play clips, set
  audio, triggers and outage buffering per camera.
- **Local admin** — `http://<pi-ip>:8080`. Discover, register, re-register and unregister
  ONVIF cameras; codec, stream and frame rate; test triggers; IR control; local preview;
  live `systemctl`-backed status.
- **Stop when finished.** Stop in either GUI, or `sudo systemctl stop 'kvs-cam*'` — every
  producer, including GUI-registered `kvs-cam@camNN` instances; these are what cost money.
- **The Pi seems gone from SSH?** On a weak Wi-Fi link it can stall for up to 10 minutes and
  then reconnect by itself (`FoundAndFixed.md` #79); wait before pulling the plug.

Two systemd managers are in play and they cannot see each other: **system** units
(`kvs-cam0N.service`, the paid producers, need `sudo`) and **user** units (everything
else, `systemctl --user`). A unit in one cannot `Requires=` a unit in the other.

---

## Cost

[`COSTS-1.4.md`](COSTS-1.4.md) is authoritative. Headline, at 24 h retention:

| | one camera, 1 month |
|---|---|
| `cam-02` sub-stream (0.052 Mbps) | **~$0.16** |
| `cam-01` 720p15 (0.623 Mbps est.) | **~$1.87** |
| `cam-02` main 4 MP (1.211 Mbps est.) | **~$3.64** |
| Weekend testing, 6 h | **~$0.02** |

Keep a $10 monthly budget alarm. **The realistic failure mode is a producer left running
for a week, not a design error** — which is exactly why Start/Stop is an explicit action.

---

## Repository map

```
setup/              fresh-card setup: setup-all.sh and its five steps, setup-check.sh
adapter/            on-device Python and pipelines
  agent.py            MQTT control agent (start/stop/IR, live view)
  camera_control.py   shared camera logic; unit-name and path-name helpers live here
  config.py           deployment identity from /etc/adapter/adapter.env
  aws_device_creds.py short-lived AWS credentials from the device certificate
  event_watcher.py    ONVIF detection → clip triggers
  outage_buffer.py    outage supervisor (arming, capture, retention)
  outage_uploader.py  merge + backfill to S3
  mediamtx_api.py     MediaMTX control-API helper
  sync_mediamtx_paths.py  re-adds camera paths after every MediaMTX start
  rematch_cameras.py  follows ONVIF cameras to a new IP (timer)
  onvif_discovery.py  WS-Discovery scan + ONVIF enrichment (microphone, streams, web page)
  onvif_events.py     per-camera recording triggers, and ONVIF subscriptions that clean up
  onvif_media2.py     ONVIF Media2: codec, stream and frame rate of a camera's encoders
  live_view.py        the live view on the sub stream (path, registry field, KVS stream)
  codec_caps.py       which codecs each camera can deliver, and the default
  onvif-admin/        local Flask GUI
  bin/                GStreamer pipelines, detect-hw.sh, operator tools
config/             templates for /etc/adapter/ (adapter.env, cameras/*.env)
client/index.html   the cloud browser client (single file)
cloud/deploy.sh     deploys the Lambdas and the page, with the shared-account checks
cloud/lambda/       one file per Lambda
cloud/iam/          one policy document per role
cloud/iot/          IoT rules and thing policy
mediamtx/           MediaMTX binary and config
measurements/       recorded results, not prose
tests/              pytest suite: offline, AWS and the Pi faked (PYTEST.md)
requirements.txt    what venv-adapter needs; requirements-test.txt adds pytest
```

**A naming quirk worth knowing before reading the code:** the camera identifier is
hyphenated (`cam-01`) everywhere except MediaMTX path names and systemd unit suffixes
(`cam01`, `kvs-cam01.service`, or `kvs-cam@cam03.service` for GUI-registered cameras).
`camera_control.py`'s `mediamtx_path_name()` / `unit_name()` are the single conversion
point — getting it wrong twice made Start/Stop silently do nothing (`FoundAndFixed.md`
#7, #37).

---

## Documentation

| File | What it is |
|---|---|
| [`Demo-AWS-Video-revCosts4.md`](Demo-AWS-Video-revCosts4.md) | **The canonical build guide.** A narrative log of the real build and the design decisions. When in doubt about *why* something is built a certain way, read this |
| [`FoundAndFixed.md`](FoundAndFixed.md) | Every defect found so far — symptom, cause, fix — numbered; other docs cite them as `#N` |
| [`LAUNCH.md`](LAUNCH.md) | Operational runbook — fresh-card setup (scripted), launch, verification, deploys |
| [`START.md`](START.md) | Where the system starts: first install, every boot (which services, in which order), and what a Start press runs, down to the producer |
| [`PYTEST.md`](PYTEST.md) | How the pytest suite was introduced, step by step: its safety guards, what each test covers, CI, and the phases still to come |
| [`VMS-PORT-PLAN.md`](VMS-PORT-PLAN.md) | The round that ported the base project's fixes and features (unregister, triggers, stream/fps, live view), with its decisions and status |
| [`COSTS-1.4.md`](COSTS-1.4.md) | The cost model. Authoritative for any bitrate or dollar figure |
| [`Camera-Features.md`](Camera-Features.md) | What the ONVIF camera actually does, marked **verified** vs *advertised* |
| [`CAM02-CAPABILITIES.md`](CAM02-CAPABILITIES.md) | The ONVIF camera's raw capability replies, service by service — what it *advertises* |
| [`AUDIO.md`](AUDIO.md) | Optional audio: design, the rules its two silent bugs left, withdrawn claims |
| [`OUTAGE.md`](OUTAGE.md) | Durable outage buffering: design, measurements, open questions |
| [`OUTBOUND-CLOUD.md`](OUTBOUND-CLOUD.md) | The outbound-only architectural thesis |
| [`NETWORK.md`](NETWORK.md) | MediaMTX's role and ports, how discovery was built, the codec choice as built (canonical: guide §21), and one open option: an isolated camera segment on `eth0` |
| [`measurements/`](measurements/) | Raw recorded results |

`COSTS-1.3.md` and `Demo-AWS-Video-MCh-15.md` are earlier material, kept for the history
of what changed and why; they may be stale against the current guide.

---

## Deliberate non-goals

Scoping discipline, stated explicitly:

- **AI analytics.** Rekognition or a Greengrass inference component would demonstrate
  integration, not engineering.
- **Two-way audio (talkdown).** Needs a reverse audio path and the ONVIF backchannel; it
  argues for a WebRTC redesign rather than an addition.
- **Fleet OTA via IoT Jobs.** Scoped and specified (guide §16.3d), not built. Rollback is
  the part that matters, and half of it is worse than none.
- **NDAA compliance, enclosure, certification.** Product concerns, not architecture.

---

## Rebuilding on a different AWS account

Every AWS identifier in `LAUNCH.md` — account number, API Gateway ID, Cognito pool,
CloudFront distribution, bucket names, IoT endpoint — is specific to the account this was
built in. The commands as written will fail with "already exists" against that account and
will not work against another.

To rebuild: follow the guide's §3 (KVS + IAM + IoT), §6 (Lambda + API Gateway + Cognito)
and §8 (client + CloudFront), then put the new account's region, Thing, endpoints and
bucket into `/etc/adapter/adapter.env` (template `config/adapter.env.example`, its header
has the lookup commands — `LAUNCH.md` A7) and replace the constants at the top of
`client/index.html`. The adapter code itself holds none. The policy documents in
[`cloud/iam/`](cloud/iam/) and [`cloud/iot/`](cloud/iot/) are reusable as-is apart from
the account number.
