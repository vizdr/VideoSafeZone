# FoundAndFixed.md — every defect found in this project, and what fixed it

The single place where this project's bugs are written up: what broke, how it was found,
why it happened, and what fixed it. Other documents keep only the rule or decision a bug
produced, plus a reference to it here — **`FoundAndFixed.md #N`**. Numbers are permanent:
new entries are appended, and an entry is never renumbered or reused.

**In scope:** defects in this project's own code, configuration, build and setup, and
documentation (an instruction that fails when followed is a defect too). **Not here,
deliberately:** quirks of third-party components, which this project works around but
cannot fix (the camera firmware in `Camera-Features.md` and `AUDIO.md` §3, the `kvssink`
buffer behaviour in guide §16.3c), withdrawn *predictions* (`AUDIO.md` §7), and generic
advice (guide §15 "Known traps").

---

## Overview

85 entries, from the first build (2026-08-20) through the move onto a second Pi
(2026-09-24/25) and the codec-selection work (2026-09-26) to the fixes ported from the base
project `VMS` (2026-09-27; numbers kept identical to VMS's, see #49). #52–#75 are VMS's #51–#74, ported on
2026-10-25 (this number = VMS's + 1); #76–#82 were found on a freshly re-imaged card that day. #84 and #85 were
found writing the pytest suite (2026-10-28).

| # | Defect | Area | Found | Status |
|---|---|---|---|---|
| 1 | The Pi crashed outright during the SDK build — three mechanisms | build / platform | 2026-08-20 | fixed |
| 2 | Nested `--parallel` in the SDK ignores `-j1` / `PARALLEL_BUILD` | build | 2026-08-20, again 2026-09-24 | fixed (patch 1) |
| 3 | OpenSSL's test submodules crashed the Pi during clone | build | 2026-08-20 | fixed (patch 2) |
| 4 | GCC 14 rejects the SDK's implicit `pthread_getname_np` | build | 2026-08-20 | fixed (patch 3) |
| 5 | The camera chain was never persisted — producer crash-looped silently | systemd | 2026-08-20 | fixed |
| 6 | A retained Last Will gets the MQTT CONNECT rejected | control plane | 2026-08-20 | fixed |
| 7 | `agent.py` built `kvs-cam-02.service`, silently did nothing for cam-02 | control plane | 2026-08-20 | fixed |
| 8 | A Lambda exception reached the browser as a bare `NetworkError` | cloud client | 2026-08-20 | fixed |
| 9 | `mediaSourceRequiresReset` on every player reload | cloud client | 2026-08-20 | fixed |
| 10 | Player retried forever after Stop, with no "stopped" state | cloud client | 2026-08-20 | fixed |
| 11 | Browsers kept serving a stale client after deploys | cloud client | not recorded | fixed |
| 12 | A system unit declared a dependency on a user unit | systemd | not recorded | fixed |
| 13 | `v4l2h264enc` negotiated Baseline — black video in browsers | media pipeline | not recorded | fixed |
| 14 | HLS sessions died after exactly five minutes | cloud client / Lambda | 2026-09-06 | fixed |
| 15 | 48 kHz audio silently lost more than half its frames (shared DTS) | audio | audio work | fixed |
| 16 | AAC over RTSP became LATM — ingested, then refused at playback | audio | audio work | fixed |
| 17 | A global `input` CSS rule broke the audio checkboxes | cloud client | audio work | fixed locally; root rule open |
| 18 | The guide's outage test (§10.2) could not detect anything | docs / test | outage work | fixed (`awsblock.sh`) |
| 19 | The §17/M1 spec's MPEG-TS recording would have been mute | docs / spec | outage work | avoided (fMP4) |
| 20 | Segment names parsed as UTC — rolling buffer grew without bound | outage buffer | outage work | fixed |
| 21 | Supervisor blocked 45 min on AWS — during the outage it watches for | outage buffer | outage work | fixed |
| 22 | Recovery on one probe made the supervisor flap | outage buffer | outage work | fixed |
| 23 | The reachability probe cost twice its timeout | outage buffer | outage work | fixed |
| 24 | LAUNCH A3's patches were not actionable; patch 3 impossible up front | docs / build | 2026-09-24 | fixed |
| 25 | Absolute `/home/vladimir/MyProjects/VMS` paths broke on the move | code / docs | 2026-09-24 | fixed |
| 26 | The MediaMTX tarball overwrites the project's `mediamtx.yml` | docs / setup | 2026-09-25 | fixed |
| 27 | The documented venv package list was incomplete | docs / setup | 2026-09-25 | fixed |
| 28 | Troubleshooting advised `-j2` for the OpenSSL build crash | docs | 2026-09-25 | fixed |
| 29 | The guide never downloaded `AmazonRootCA1.pem` | docs / setup | 2026-09-25 | fixed |
| 30 | Six unit files existed only on the old Pi; `kvs-cam@` sketch incomplete | docs / systemd | 2026-09-25 | fixed |
| 31 | cam-02's RTSP credentials committed in `mediamtx.yml` | security | 2026-09-25 | removed; **password rotation pending** |
| 32 | API-added MediaMTX paths vanished on every MediaMTX restart | MediaMTX | 2026-09-25 | fixed |
| 33 | Journal lived in RAM only; the first fix didn't take on Pi OS | platform / docs | 2026-09-25 | fixed |
| 34 | Passwordless sudo assumed; the check was fooled by a cached password | platform / docs | 2026-09-25 | fixed |
| 35 | MediaMTX's generated `auto.crt`/`auto.key` were not git-ignored | repo hygiene | 2026-09-25 | fixed |
| 36 | Admin GUI rollback deleted a path with `POST` — a 404, silently | admin GUI | 2026-09-25 | fixed |
| 37 | Start/Stop and status silently no-op'd for GUI-registered cameras | control plane | 2026-09-25 | fixed |
| 38 | Instructions that failed when followed literally (docs consistency review) | docs | 2026-09-25 | fixed |
| 39 | Outage supervisor crash-looped on a Pi without the USB stick | outage buffer | 2026-09-25 | fixed |
| 40 | cam-01 never published: `gstreamer1.0-rtsp` missing from the setup lists | docs / setup | 2026-09-25 | fixed |
| 41 | `cloud/iam/` policy files had drifted from the deployed policies | IaC / docs | 2026-09-26 | fixed |
| 42 | cam-01 dead after a reboot: camera not yet enumerated, and nothing retried | systemd / detection | 2026-09-26 | fixed |
| 43 | cam-02 played only after several Start/Stop/Reload rounds | pipeline / client | 2026-09-26 | fixed |
| 44 | A producer stopped for good whenever MediaMTX closed its session | pipeline / systemd | 2026-09-26 | fixed |
| 45 | Manual recording without footage returned a raw AWS 500, not the intended 503 | Lambda | 2026-09-26 | fixed |
| 46 | The producer could hang `active` on EOS, uploading nothing | pipeline / systemd | 2026-09-27 | fixed (ported from VMS) |
| 47 | Admin GUI timed out in the browser; cam-02 dropped out while uploading (Wi-Fi power save, shared air) | network / Wi-Fi | 2026-09-27 | fixed |
| 48 | Deploys from two repositories into one AWS account overwrote each other | process / deployment | 2026-09-27 | fixed (process) |
| 49 | *(VMS only — feature not in this repository)* | — | 2026-09-27 | not applicable |
| 50 | MediaMTX reported a path `ready` while no media flowed | verification | 2026-09-27 | fixed |
| 51 | Clips older than the newest 50 could not be reached from the browser | Lambda / cloud client | 2026-09-28 | fixed (paging) |
| 52 | The cam-01 publisher hung silently and stayed `active` (VMS #51) | publisher | 2026-10-25 (VMS 10-03) | ported; restart verified |
| 53 | The FRITZ!Box drops the Pi's Wi-Fi after group-rekey bursts (VMS #52) | network | 2026-10-25 (VMS 10-03) | **open** — environmental |
| 54 | H.265 detection clips listed as 45 s held 9 s (VMS #53) | clip Lambdas | 2026-10-25 | fixed, deployed 2026-10-25 |
| 55 | The AWS block missed the IoT endpoint's new range (VMS #54) | test tooling | 2026-10-25 | fixed |
| 56 | A DNS failure froze the admin page's status polls (VMS #55) | admin GUI | 2026-10-25 | fixed, verified |
| 57 | *(hardware note, VMS #56)* USB 3 stick vs 2.4 GHz radio | hardware | — | not applicable |
| 58 | kvssink dropped every H.265 keyframe: no DTS (VMS #57) | pipeline | 2026-10-25 | fixed, verified |
| 59 | H.265 + audio: video clock 1000 h ahead (VMS #58) | pipeline | 2026-10-25 | fixed, verified |
| 60 | GUI-registered cameras could never record audio (VMS #59) | discovery / producer | 2026-10-25 | fixed, verified |
| 61 | Start lost during an MQTT drop; page said only "not available" (VMS #60) | control plane / page | 2026-10-25 | **open**; page message done |
| 62 | cam-01 audio on: Start didn't restart the publisher (VMS #61) | producer | 2026-10-25 | fixed, verified |
| 63 | Audio frames lost: ms tie in kvssink, invented DTS on cam-01 (VMS #62) | kvssink / producer | 2026-10-25 | fixed, verified |
| 64 | IR buttons had ONVIF's ON/OFF backwards (VMS #63) | GUIs | 2026-10-25 | fixed in both pages |
| 65 | Detection never fired on cam-03; watcher leaked subscriptions (VMS #64) | event watcher / GUIs | 2026-10-25 | ported; hygiene verified, a fired trigger pending |
| 66 | The cloud player spun forever (VMS #65) | cloud page / Lambda | 2026-10-25 | fixed, deployed 2026-10-25 |
| 67 | No-password camera couldn't register; microphone only in SDP (VMS #66) | registration / discovery | 2026-10-25 | fixed, verified |
| 68 | cam-04's Motion never fired: event field ≠ declared (VMS #67) | event watcher | 2026-10-25 | ported; verification pending |
| 69 | Camera already on H.265 labelled H.264 (VMS #68) | admin GUI / codec caps | 2026-10-25 | ported; verification pending |
| 70 | Passthrough H.264 + audio lost audio frames (VMS #69) | kvssink | 2026-10-25 | fixed, verified |
| 71 | cam-02's hard-coded A-law (VMS #70) | producer | 2026-10-25 | fixed, verified |
| 72 | Connected producer feeding kvssink nothing looked healthy (VMS #71) | producer | 2026-10-25 | fixed, verified |
| 73 | cam-02 lost 1 AAC frame in 6: paired RTP timestamps (VMS #72) | producer | 2026-10-25 | fixed, verified |
| 74 | Discovery showed "G.711 8000 kHz" (VMS #73) | discovery | 2026-10-25 | fixed |
| 75 | cam-01 publisher sent audio, no video, unnoticed (VMS #74) | publisher | 2026-10-25 | fixed, verified |
| 76 | LAUNCH A3's patches 1–2 had nothing to patch on a fresh clone | docs / build | 2026-10-25 | fixed |
| 77 | Patch 2 did nothing on CMake 3.31: 700 MB of OpenSSL submodules | build | 2026-10-25 | fixed (patch 2b) |
| 78 | Two LAUNCH A3 proofs couldn't tell success from failure | docs | 2026-10-25 | fixed |
| 79 | Wi-Fi uplink stalls while "connected", until the router's next rekey (≤10 min) | network / platform | 2026-10-25 | **open** — environmental (#53) |
| 80 | Ported Unregister would fail silently under the narrow sudo rule | sudo / systemd | 2026-10-25 | fixed |
| 81 | GUI-registered producers started at boot, pushing to KVS unattended | provisioning / cost | 2026-10-25 | fixed |
| 82 | The new `list-cameras` answered 500: a Decimal it couldn't serialise | Lambda | 2026-10-25 | fixed before deploy |
| 83 | Start Recording could run with nothing recording; it failed only at Stop Recording | cloud page | 2026-10-25 | fixed, page not yet deployed |
| 84 | MediaMTX's duration parser reads `500ms` as 500 minutes | outage buffer | 2026-10-28 | fixed 2026-10-28 (latent; no live decision changed) |
| 85 | An unknown IR mode got a 500 from the admin GUI, a 400 from the cloud | admin GUI | 2026-10-28 | fixed 2026-10-28 |

### What they have in common

- **Most were silent.** #5, #7, #13, #15, #16, #18, #20, #21, #32, #36, #37, #39, #42, #44, #46,
  #47, #50 and #51 all passed "it ran without errors". The recurring cause is a tool that reports success on
  the wrong question. `systemctl is-active` says `inactive` for a unit that doesn't exist
  (#7, #37). `check=False` swallows a failure (#7). `ffmpeg` plays what a browser won't
  (#13). KVS ingest accepts what KVS playback refuses (#16). `sudo -n` passes on a cached
  password (#34). CLAUDE.md "Verifying changes" is the countermeasure.
- **Ingest is more permissive than playback** (#13, #15, #16, #19): verify at the
  consuming end — a decoded frame, a browser, `GetHLSStreamingSessionURL` — not at
  ingest.
- **Two copies of one fact drift** (#7, #25, #30, #31, #37, #41): unit names, paths, endpoints
  and camera addresses now each have one source (CLAUDE.md "Paths", the registry,
  `/etc/adapter/`).
- **Instructions are code too.** #24–#30, #33–#34, #38 and #40 were wrong or missing
  documentation. They surfaced only when the setup was followed literally on a fresh Pi.
- **A 4 GB Pi building C/C++** (#1–#4) needs memory discipline at every level of a
  nested build, not just the top one.

---

## Build and platform

### #1 — The Pi crashed outright during the SDK build — three mechanisms

**Found:** 2026-08-20, over five failed build attempts. **Referenced from:** guide §1.4, §4.

A Pi 4B running VS Code Remote-SSH's server (1.3+ GB of Node processes) plus a
from-source C++ build is oversubscribed on 4 GB. The KVS SDK build did not just fail — it
took the whole Pi down, by three distinct mechanisms:

- **Kernel OOM with no early intervention.** An `earlyoom` setting of `-s 50` (act only
  once swap is *also* below 50 % free), tried to rescue large compiles, let available
  memory crater from 64 % to 12 % in one 60-second window before it acted — a full crash.
- **Swap thrashing on the SD card.** Once, mid-build: no OOM-killer log, no panic, a
  silent cutoff correlated with `brcmfmac` (WiFi) SDIO timeouts just before it.
- **Build load on the interrupt cores.** This kernel isolates cores 1–2
  (`isolcpus=1,2 irqaffinity=0,3`); an unpinned build competed with WiFi interrupt
  servicing. `systemd-run --property=AllowedCPUs=` silently did nothing (cpuset not
  delegated to user cgroups).

Firmware was also found over a year out of date (EEPROM 2025-05-08 vs 2026-05-17).

**Fix:** guide §1.4's four hardening steps — `earlyoom -m 20 -s 95` preferring compiler
processes; a low-priority disk swapfile with `vm.swappiness=10`; EEPROM update; `taskset
-c 1,2` for the build. The final build ran with zero crashes and zero `earlyoom`
interventions. (On the second Pi, `isolcpus` is not set — `detect-hw.sh --print`
reports it — so the pinning protects nothing there.)

### #2 — Nested `--parallel` in the SDK ignores `-j1` and `PARALLEL_BUILD`

**Found:** 2026-08-20; recurred 2026-09-24 on the second Pi. **Referenced from:** guide
§4.2, LAUNCH.md A3, README §3.

`CMake/Utilities.cmake`'s `build_dependency()` builds each dependency with `cmake --build
. --parallel`, no job count — a bare `--parallel` becomes `-j$(nproc)` and overrides any
inherited `MAKEFLAGS`. `-DPARALLEL_BUILD=OFF` fixes the top-level copy (log4cplus dropped
from **370 → 14** concurrent tasks). But OpenSSL is built by a **nested** vendored copy,
`dependency/libkvscproducer/kvscproducer-src/CMake/Utilities.cmake`, which hardcodes
`--parallel` with no option at all: OpenSSL still spawned **398 tasks** and crashed the Pi.

**Recurrence, 2026-09-24:** on the second Pi the patch was not applied (#24), so OpenSSL
again compiled one job per core; `earlyoom` (configured by #1 to prefer compilers) sent
SIGTERM to every `cc1` at once — `build.log` showed a burst of `cc: fatal error:
Terminated signal terminated program cc1` and `EXIT_CODE=1`. Nothing was wrong with the
code.

**Fix:** patch 1 — `sed -i 's/--build \. --parallel/--build ./'` on the nested file
(LAUNCH.md A3). With it the 2026-09-25 build ran at 14 tasks, zero `earlyoom` kills.

### #3 — OpenSSL's test submodules crashed the Pi during clone

**Found:** 2026-08-20. **Referenced from:** guide §4.2, LAUNCH.md A3.

OpenSSL's `ExternalProject_Add` also fetched four large optional submodules (`boringssl`,
`krb5`, `pyca-cryptography`, `wycheproof` — fuzzing and test vectors, not needed for
`make install_sw`). Cloning `boringssl` alone caused repeated system crashes during
clone/checkout — most likely sustained SD-card I/O pressure, the same failure mode as
#1's swap thrashing.

**Fix:** patch 2 — `GIT_SUBMODULES ""` in the `project_libopenssl` block.

### #4 — GCC 14 rejects the SDK's implicit `pthread_getname_np`

**Found:** 2026-08-20. **Referenced from:** guide §4.2, LAUNCH.md A3.

`Thread.c` in kvspic uses `pthread_getname_np`, a GNU extension that needs `_GNU_SOURCE`
defined before `<pthread.h>`. Older GCC only warned about the implicit declaration;
**GCC 14 made it a hard error**, so the build fails outright on Debian trixie.

**Fix:** patch 3 — `add_definitions(-D_GNU_SOURCE)` (under `UNIX AND NOT APPLE`) in
kvspic's `CMakeLists.txt`, once, globally. Its file only exists after the configure step
has downloaded kvspic (#24).

### #33 — The journal lived in RAM only, and the first fix didn't take on Pi OS

**Found:** 2026-09-25. **Referenced from:** LAUNCH.md A1.

The second Pi kept the journal in `/run/log/journal` only. Every reboot lost all logs —
so the first failed build (#2) left no trace across the reboot — and `journalctl --user
-u <unit>` printed "No journal files were found" for **every** user unit, which every
`--user -u` check in LAUNCH.md depends on.

The first fix (`mkdir /var/log/journal`) did nothing: Raspberry Pi OS ships
`/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf` with
`Storage=volatile` (to spare the SD card), which makes journald ignore the directory.
(A related misreading: an empty `/var/log/journal` was first reported as missing.)

**Fix:** a later drop-in, `/etc/systemd/journald.conf.d/90-vms-persistent.conf`, with
`Storage=persistent`, `SystemMaxUse=200M`, `SystemMaxFileSize=20M`, then `journalctl
--flush`. Verified: `system.journal` and `user-1000.journal` on disk, the pre-switch boot
still listed, `journalctl --user -u` works.

### #34 — Passwordless sudo assumed; the check was fooled by a cached password

**Found:** 2026-09-25. **Referenced from:** LAUNCH.md A8.

`agent.py`/the admin GUI run `sudo systemctl start|stop` and `sudo provision-camera.sh`
with no terminal. The code and docs assumed Raspberry Pi OS grants passwordless sudo; the
current image grants `(ALL : ALL) ALL` with a password. Worse, LAUNCH A8's check `sudo -n
true` **passed**: this image sets `Defaults timestamp_type=global`, so a password typed
anywhere in the last ~15 minutes satisfies `sudo -n` in every session — services
included. Start/Stop would have worked just after any `sudo`, and failed otherwise.

**Fix:** `/etc/sudoers.d/020_vms-adapter`, NOPASSWD for exactly `systemctl
start|stop kvs-cam(@cam)?NN.service` and `provision-camera.sh cam-NN camNN` (regex
arguments, sudo ≥ 1.9.10), validated by `visudo` before install. The check is now `sudo
-k -n` (ignores the cache). Verified allowed and refused cases, including a smuggled
second unit.

---

## Media pipeline and audio

### #5 — The camera chain was never persisted; the producer crash-looped silently

**Found:** 2026-08-20. **Referenced from:** guide §2.8, §7.1, LAUNCH.md Part B.

Hours into later phases, the stream was dead — not downstream: `camera-init.sh` →
MediaMTX → `publish-cam01.sh` had only ever been run by hand, never made into units. The
cloud-facing pieces (`kvs-cam01.service`, the agent) were persisted and auto-recovering,
so `kvs-cam01.service` crash-looped (`Restart=on-failure`) against a 404 on
`rtsp://127.0.0.1:8554/cam01`, with nothing to show for it. A later version of LAUNCH
Part B repeated it: `kvs-camera-publish` was missing from the launch list and everything
else came up "active".

**Fix:** user units for all three pieces (`kvs-camera-init`, `kvs-mediamtx`,
`kvs-camera-publish`), in the launch list, and the rule that `is-active` is not proof —
verify media (LAUNCH.md Part C).

### #13 — `v4l2h264enc` negotiated Baseline; browsers rendered black

**Found:** date not recorded. **Referenced from:** README "Results", CLAUDE.md, guide
§2.6, `AUDIO.md` §5, `OUTAGE.md` §4.1.

`/dev/video11`'s `h264_profile` control defaults to High, but GStreamer's `v4l2h264enc`
negotiated **Baseline** — nothing in the pipeline asked for it. `ffmpeg`/`ffprobe` played
the stream; browsers (MSE) rendered **black**. The guide's Checkpoint 1 even recorded
`Video: h264 (Baseline)` and called Baseline "the safer choice" — the note that let the
bug pass.

**Fix:** `profile=(string)high` in the encoder caps in `publish-cam01.sh`, and the rule
to finish every pipeline check in a browser with a decoded frame (CLAUDE.md "Verifying
changes"). Re-verified 2026-09-25 on the second Pi: `H.264 High, level 4.0`.

### #15 — 48 kHz audio silently lost more than half its frames (shared DTS)

**Found:** during the audio work (`AUDIO.md`). **Referenced from:** guide §18.3,
`AUDIO.md` §2/§4.1, README, CLAUDE.md.

*Symptom:* cam-02 audio at 48 kHz — fragments persisted, both tracks in the HLS
manifest, `ffprobe` happy — and over half the audio missing: **15 kb/s delivered of
32 kb/s sent**, `kvssink` logging `0x30000005` at 1.65/s.

*Cause* (`gstkvssink.cpp`, `gst_kvs_sink_handle_buffer`): GStreamer audio buffers carry no
DTS, and `kvssink` synthesises one as `last_dts + 40 ms` from a counter **shared with the
video track**. With more than one audio frame between two video frames, the synthesised
timestamps overrun the next video DTS, go backwards, and are rejected
(`STATUS_CONTENT_VIEW_INVALID_TIMESTAMP`). `voaacenc` emits 1024-sample frames, so frame
duration is `1024/rate`: 21 ms at 48 kHz against a 66.7 ms video frame.

*Diagnosed* by A/B-ing reject rates against a prediction derived from the source —
`identity silent=false` is a no-op in this GStreamer build and `python3-gi` is not
installed, so DTS values could not be read directly.

| Rate | Frame duration | Rejects | Delivered (of 32 kb/s) |
|---|---|---|---|
| 48 kHz | 21 ms | **1.65 /s** | 15 kb/s — over half lost |
| 8 kHz (native) | 128 ms | **0** | 27.8 kb/s |

**Fix:** the rule *audio frame duration must exceed the video frame interval* — below
~15.4 kHz at 15 fps. cam-02 runs 8 kHz, cam-01 16 kHz (64 ms: marginally inside, measured
zero rejects). Re-count rejects whenever a camera's frame rate changes (guide §18.3).

### #16 — AAC over RTSP became LATM: ingested, then refused at playback

**Found:** during the audio work. **Referenced from:** `AUDIO.md` §2/§4.2, README,
CLAUDE.md, guide §18.4 (the rule; `stream-cam01.sh`/`publish-cam01.sh` comments retell the story).

*Symptom:* cam-01 with AAC encoded in the publisher and passed through at the producer —
the obvious design, one encode. `kvssink` ingested it (0 rejects, 30 fragments/min), then
playback failed: `InvalidCodecPrivateDataException: AAC CPD must be of length 2 or 5, but
was 4`.

*Cause:* `rtspclientsink` payloads AAC as MPEG-4 **LATM**, and the LATM round-trip
re-wraps the AudioSpecificConfig into the 4-byte `14081fe0` (`channelConfiguration=0` plus
trailing bits) instead of the canonical 2 bytes. The payloader is a per-pad property, not
settable from `gst-launch`; a caps filter after `aacparse` didn't change it (MediaMTX still
reported `MPEG-4 Audio LATM`).

**Fix:** never send AAC over RTSP. `publish-cam01.sh` sends LPCM (16 kHz mono S16BE,
loopback only); `stream-cam01.sh` encodes (`rtpL16depay ! audioconvert ! voaacenc !
aacparse`) so `voaacenc`'s own `codec_data` reaches `kvssink`. Encoding at the producer
also keeps both tracks on one RTSP timeline for A/V sync.

---

## Control plane and systemd

### #6 — A retained Last Will gets the MQTT CONNECT rejected

**Found:** 2026-08-20. **Referenced from:** guide §7.2.

`retain=True` on the agent's Last Will made AWS IoT drop the connection at CONNECT —
`AWS_ERROR_MQTT_UNEXPECTED_HANGUP`, no CONNACK error code — while the same certificate,
policy and topic worked for a normal `publish()`. IoT Core evidently authorizes retained
LWTs more strictly than regular publishes.

*Isolated* by stripping the connection to nothing (no Will, no subscribe), then adding
pieces back one at a time until the failing one was obvious; `awscrt.io.init_logging(
awscrt.io.LogLevel.Debug, 'stderr')` is what surfaces the hang-up's context.

Found alongside: `conn.publish(...)` returns a **tuple** `(future, packet_id)` in
`awsiotsdk` 1.31.0 / `awscrt` 0.36.1 — `.result()` without `[0]` silently swallowed
publish errors.

**Fix:** `retain=False` on the Will (enough — subscribers still see it fire), and `[0]`
before `.result()`.

### #7 — `agent.py` built `kvs-cam-02.service` and silently did nothing for cam-02

**Found:** 2026-08-20. **Referenced from:** guide §16.3(a), CLAUDE.md,
`camera_control.py`.

`agent.py` built the unit name as `f"kvs-{camera}.service"`, which for `cam-02` gave
`kvs-cam-02.service` — a unit that doesn't exist (the unit is `kvs-cam02.service`).
`systemctl` exited non-zero, but it was called with `check=False`, so the failure was
swallowed and the API reported success. Caught only by reading `journalctl` and seeing
the wrong unit name in the logged `sudo` command.

**Fix:** `camera_control.mediamtx_path_name()`/`unit_name()` as the one place the
`cam-02` → `cam02` conversion happens. (Its template-unit blind spot is #37.)

### #12 — A system unit declared a dependency on a user unit

**Found:** date not recorded. **Referenced from:** CLAUDE.md.

A templated system unit (`kvs-cam@.service`) declared `Requires=kvs-mediamtx.service`,
which is a **user** unit. System and user units belong to independent systemd instances,
so the system manager could not find it: "Unit not found".

**Fix:** drop the cross-manager dependency. A unit in one manager can never
`Requires=`/`After=` a unit in the other.

### #30 — Six unit files existed only on the old Pi; the `kvs-cam@` sketch was incomplete

**Found:** 2026-09-25. **Referenced from:** LAUNCH.md A8, guide §16.6.

LAUNCH Part B enabled units that nothing in the docs created. Only five units were
written out in the guide; `kvs-cam02`, `kvs-agent`, `onvif-admin`, `kvs-event-watcher`,
`kvs-outage-buffer` and `kvs-outage-uploader` existed only as files on the old Pi. The
guide's `kvs-cam@.service` sketch also lacked `User=` and the
`GST_PLUGIN_PATH`/`LD_LIBRARY_PATH` lines, so every GUI-provisioned producer would have
failed with `No such element "kvssink"`.

**Fix:** LAUNCH.md A8 generates all 13 units (the missing six reconstructed from the code)
with this clone's literal paths, plus checks. Verified with `systemd-analyze verify`.

### #36 — Admin GUI rollback deleted a MediaMTX path with `POST` — a 404, silently

**Found:** 2026-09-25, while testing camera re-matching. **Referenced from:** CLAUDE.md.

When provisioning failed during registration, `app.py` rolled back the MediaMTX path it
had just added with `requests.post(".../v3/config/paths/delete/...")`. MediaMTX's delete
route only accepts `DELETE` and answers `POST` with `404 page not found`; the response
was never checked, so a failed registration always left an orphan path.

**Fix:** `requests.delete(...)`. Verified: the path is gone after a simulated
provisioning failure, and nothing is written to the registry.

### #37 — Start/Stop and status silently no-op'd for every GUI-registered camera

**Found:** 2026-09-25, while writing the sudo rule (#34). **Referenced from:**
`camera_control.py`, CLAUDE.md.

`camera_control.unit_name()` always returned `kvs-camNN.service`, but cameras registered
through the admin GUI run as template instances, `kvs-cam@camNN.service`
(`provision-camera.sh` enables that name). Start/Stop (agent and both GUIs), the status
column and the outage buffer's "is the producer running" check all targeted a unit that
doesn't exist. `systemctl is-active` printed `inactive` for it — the same word as for a
real stopped unit — so nothing looked wrong. Only cam-01/cam-02, which have their own
units, worked. It is #7's pattern again.

**Fix:** `unit_name()` returns `kvs-cam@<path>.service` when
`/etc/adapter/channels/<path>.env` exists — the file `provision-camera.sh` writes for
exactly those cameras — and `kvs-<path>.service` otherwise. Verified against systemd's
`LoadState` (`not-found` before, `loaded` after); the sudo rule (#34) already allowed both
forms.

---

## Cloud client and Lambdas

### #8 — A Lambda exception reached the browser as a bare `NetworkError`

**Found:** 2026-08-20. **Referenced from:** guide §8.6.

Pressing Start or reloading sometimes showed `NetworkError when attempting to fetch
resource`. `get_hls_url.py` attached its CORS header only on its own `return`; when
`get_hls_streaming_session_url` raised `ResourceNotFoundException` (producer not running),
the exception escaped, API Gateway returned its own 502 **without CORS headers**, and the
browser could not read the cross-origin response at all — `fetch()` threw instead of
resolving. `curl` cannot reproduce this (it ignores CORS), so backend tests passed.

**Fix:** wrap each handler in `try`/`except` and return every path — 200, 503 "stream is
not currently live — press Start", 500 — through the code that attaches CORS headers.
Applied to both Lambdas.

### #9 — `mediaSourceRequiresReset` on every player reload

**Found:** 2026-08-20. **Referenced from:** guide §8.6.

The client's `load()` created a fresh `new Hls()` per call and attached it to the same
`<video>` without releasing the previous instance's `MediaSource` — two overlapping
`MediaSource` objects on one element, which is exactly what the error means.

**Fix:** keep the instance in a module-level variable; `hls.destroy()` before creating
the next.

### #10 — The player retried forever after Stop, with no "stopped" state

**Found:** 2026-08-20. **Referenced from:** guide §8.6.

hls.js's recommended fatal-error recovery (`hls.startLoad()` on `NETWORK_ERROR`) is right
for a live stream's transient blips, but after Stop the stream had ended permanently and
the retry never finished. Even once retries stopped, the viewer saw a frozen last frame
under a spinner — indistinguishable from "still loading".

**Fix:** a `userStopped` flag checked before any retry; Stop tears the player down at
once (`hls.destroy()`, clear `src`); a state-driven overlay ("Stream stopped. Press Start
to watch again." / "Starting stream…"), cleared on `Hls.Events.FRAG_BUFFERED`. The video
wrapper needs `aspect-ratio: 16 / 9`, or the overlay collapses before first load.

### #11 — Browsers kept serving a stale client after deploys

**Found:** date not recorded. **Referenced from:** guide §8.5.1, CLAUDE.md.

After `aws s3 cp` of a new `client/index.html`, browsers kept serving the old page from
cache (S3 static website), so a fix appeared not to work.

**Fix:** every client upload sets `--cache-control "no-cache, must-revalidate"`. Behind
CloudFront the same header makes it revalidate (`x-cache: RefreshHit`), so an upload is
live immediately without `create-invalidation`.

### #14 — HLS sessions died after exactly five minutes

**Found:** 2026-09-06. **Referenced from:** guide §8.6.

A few minutes into watching, the browser's buffering ring appeared and never went away,
while the backend was entirely healthy. Three links in a chain:

1. `get_hls_url.py` requested `Expires=300` — the KVS minimum — so every session URL was
   dead after five minutes regardless of stream health.
2. The client received `expires_in` and never used it.
3. The fatal-`NETWORK_ERROR` branch called `hls.startLoad()`, which re-requests the
   **same** expired URL — a loop that cannot succeed — and showed no overlay, so it looked
   exactly like normal buffering.

*Proved, not inferred:* poll the master playlist every 30 s — `200` at t=+270 s, `403` at
t=+300 s, to the second; the fixed Lambda's URL still `200` at t=+385 s.

It surfaced on cam-02 first only because cam-02's fragments are 2.93 s against cam-01's
1.93 s (the camera's keyframe interval), leaving less headroom at the live edge — "only on
camera X" meant "camera X is the most sensitive detector".

**Fix:** `Expires=3600`; the client refreshes at 80 % of `expires_in`; the error handler
allows two `startLoad()` retries for real blips, then fetches a **new** session behind a
"Reconnecting…" overlay.

### #17 — A global `input` CSS rule broke the audio checkboxes

**Found:** during the audio work. **Referenced from:** `AUDIO.md` §6.4.

The audio checkboxes rendered detached from their labels and overflowing the panel. Not
the new markup: `client/index.html`'s pre-existing global rule `input { display: block;
width: 100%; … }`, written for the login form, matches every `<input>`; on a checkbox,
`width: 100%` spans the panel and pushes the label out. A `flex: none` attempt did nothing
(it touches neither `width` nor `display`). The working fix came only after rendering the
layout headlessly in Chromium instead of reasoning about it.

**Status:** fixed for the audio controls. **Still open:** the rule itself should be scoped
to `#login input`; not done, because it changes the login form's styling as a side effect.

---

## Outage buffering

### #18 — The guide's outage test (§10.2) could not detect anything

**Found:** first executed while building durable outage buffering. **Referenced from:**
guide §10.2, `OUTAGE.md` §1.2/§7.

Three independent faults, each enough on its own:

1. The `iptables` snippet is IPv4-only. This LAN is dual-stack and AWS resolves to
   `2a05:d014:…`, so every "blocked" connection went over IPv6 and returned HTTP 200 —
   with the rules apparently applied and packet counters even incrementing.
2. 120 s is exactly `kvssink`'s own `DEFAULT_BUFFER_DURATION_SECONDS`, so KVS lost nothing
   and the test proved nothing about buffering.
3. The suggested grep (`retry|reconnect|error`) matches none of the lines that show a
   buffer filling (`droppedFrame`, `storage overflow`, `Overall storage byte size`).
   The test had also never been run: its results file did not exist.

Found while fixing it: **blocking a resolved IP is useless** — the IoT endpoint rotated
through `18.196.251.80`, `3.69.141.146`, `18.185.210.34` and `18.153.244.214` within
minutes, so only blocking AWS *ranges* (`3/8, 18/8, 35/8, 52/8, 54/8`, `2a05::/16`) works.

**Fix:** `adapter/bin/awsblock.sh` (IPv4 + IPv6 ranges, verifies the block landed), outages
well past 120 s, and the method recorded in `measurements/reconnect_timeline.md`.

### #19 — The §17/M1 spec's MPEG-TS recording would have been mute

**Found:** during the outage-buffer design. **Referenced from:** `OUTAGE.md` §1.3/§3.1.

One of two earlier outage-recording specs recorded to MPEG-TS. MediaMTX's MPEG-TS
recorder cannot carry LPCM or G.711 — cam-01's and cam-02's audio on the MediaMTX leg —
so it would have recorded silently mute footage (the ingest-permissive/playback-strict
pattern again).

**Fix:** never built that way; `recordFormat: fmp4` is mandatory (`mediamtx_api.py`).

### #20 — Segment names parsed as UTC: the rolling buffer grew without bound

**Found:** outage-buffer testing (B1). **Referenced from:** `OUTAGE.md` §5.2.

`recordPath`'s `%Y-%m-%d_%H-%M-%S` is formatted in the machine's local zone; parsing it as
UTC put every segment two hours in the future on a CEST box, so the retention cutoff never
matched. Measured 9 segments where 4–5 were expected — it would have filled the stick
silently.

**Fix:** parse with `astimezone()`, with an mtime fallback so an unparseable name can
never become un-prunable.

### #21 — The supervisor blocked 45 minutes on AWS, during the outage it watches for

**Found:** outage-buffer testing (B1). **Referenced from:** `OUTAGE.md` §5.2.

`load_registry()` called DynamoDB on the tick path with boto3's defaults (60 s connect /
60 s read, with retries). With AWS unreachable the process sat in `poll_schedule_timeout`
for **45 minutes**, never reaching the connectivity check — never detecting the outage.

**Fix:** all AWS access moved to a background thread (the tick loop makes no network call),
and the scan pinned to `connect_timeout=3, read_timeout=5, max_attempts=1`.

### #22 — Recovery on a single probe made the supervisor flap

**Found:** outage-buffer testing (B1). **Referenced from:** `OUTAGE.md` §5.2 (`outage_buffer.py`'s
comment retells it).

The IoT endpoint's DNS rotates across AWS ranges; one rotation briefly landed on a
reachable address and the supervisor declared recovery — finalising the capture and
opening another. Measured 3 finalise/reopen cycles within one outage, fragmenting it into
separate clips.

**Fix:** asymmetric thresholds — 2 consecutive failures to declare an outage, 3
consecutive successes to declare recovery. Acting early on failure is cheap; acting early
on recovery stops recording.

### #23 — The reachability probe cost twice its timeout

**Found:** outage-buffer testing (B2). **Referenced from:** `OUTAGE.md` §5.3.

`socket.create_connection` applies its timeout per resolved address, and the IoT endpoint
has A and AAAA records, so `PROBE_TIMEOUT = 4` cost 8 s per probe. Two probes made
detection 86 s against a 120 s pre-roll — working, on a third of the intended margin, and
at the mercy of how many addresses DNS returns.

**Fix:** an explicit resolve-then-try loop under a total `PROBE_BUDGET_SEC = 4`. Measured
0.02 s reachable, hard-capped 4 s unreachable.

---

## Setup, documentation and repository (second Pi, 2026-09-24/25)

### #24 — LAUNCH A3's patches were not actionable, and patch 3 can't be applied up front

**Found:** 2026-09-24, when the build failed (#2 recurrence). **Referenced from:**
LAUNCH.md A3, README §3, guide §4.2.

LAUNCH A3 listed the three SDK patches only as comments inside a code block, so the build
was started without them. README showed a one-command `cmake … && make` build. And patch
3's target file (`kvspic-src/CMakeLists.txt`) does not exist until the configure step has
downloaded kvspic, while `Thread.c` compiles only later in `make` — so "apply all three
patches first" was impossible as written.

**Fix:** A3 as runnable commands with a proof after each step, and two stages: patches
1–2 → `cmake` configure → patch 3 → `make -j1`. The 2026-09-25 build ran this way in 16
minutes with zero `earlyoom` kills.

### #25 — Absolute `/home/vladimir/MyProjects/VMS` paths broke when the clone moved

**Found:** 2026-09-24. **Referenced from:** CLAUDE.md "Paths", guide §1.3, LAUNCH.md A2.

Seventeen hardcoded paths across 11 files in `adapter/` (certificates, venv, WSDL
directory, helper scripts), plus the docs' `VMS_HOME` exports and systemd examples, still
named the old Pi's clone location. Nothing could find its files in
`~/Projects/VideoSafeZone`. The venv's `python3.13` directory was spelled out too.

**Fix:** code resolves `$VMS_HOME` if set, else the repo root from its own location;
the venv's `pythonX.Y` comes from `sys.version_info`. Units get literal paths generated
per clone (#30). Deployment identity moved to `/etc/adapter/adapter.env` in the same
spirit.

### #26 — The MediaMTX release tarball overwrites the project's `mediamtx.yml`

**Found:** 2026-09-25. **Referenced from:** guide §2.5, LAUNCH.md A5.

The documented `tar xzf mediamtx.tar.gz` extracts the release's default `mediamtx.yml`
over the tracked, customised one — silently: MediaMTX starts fine without the project's
settings. LAUNCH A5's proof ("`git status` must not list `mediamtx.yml` as modified")
also gave a false failure whenever the file had legitimate uncommitted edits.

**Fix:** `tar xzf mediamtx.tar.gz mediamtx` (binary only) and `curl -f`; the proof is a
checksum taken before extraction and checked after.

### #27 — The documented venv package list was incomplete

**Found:** 2026-09-25. **Referenced from:** README §4, LAUNCH.md A4.

README's `pip install` omitted `WSDiscovery` (imported by `onvif_discovery.py`) and `lxml`,
so the admin GUI and discovery could not import.

**Fix:** `boto3 awsiotsdk onvif-zeep-async WSDiscovery flask requests lxml`; verified by
importing all 17 adapter modules against the venv.

### #28 — Troubleshooting advised `-j2` for the OpenSSL build crash

**Found:** 2026-09-25. **Referenced from:** guide §15.

The troubleshooting row "Build dies around OpenSSL/curl → `make -j4` on 4 GB — use `-j2`"
contradicted §4.2's own finding: the outer `-j` never reaches the nested build (#2).

**Fix:** the row now points at patch 1 and names the `Terminated signal … cc1` symptom.

### #29 — The guide never downloaded `AmazonRootCA1.pem`

**Found:** 2026-09-25. **Referenced from:** guide §6.4.

`agent.py`'s MQTT connection uses `AmazonRootCA1.pem`, but the guide only downloaded
`SFSRootCAG2.pem` (the credentials endpoint's CA). A fresh setup had no MQTT CA.

**Fix:** both downloads in guide §6.4 and LAUNCH.md A7, with a proof for each endpoint.

### #31 — cam-02's RTSP credentials were committed in `mediamtx.yml`

**Found:** 2026-09-25. **Referenced from:** LAUNCH.md E3, CLAUDE.md, guide §16.2.1.

cam-02 was kept alive across MediaMTX restarts (#32) by hardcoding its path in the
tracked `mediamtx.yml` — camera IP, user and password in the `source:` URL — and the file
was pushed to GitHub (commit `aaf04bd`).

**Fix:** `mediamtx.yml` has no camera paths; addresses and credentials live only in the
registry (`rtspUrl`) and a 0600 local cache (#32). **Still required:** change the
camera's password — it remains in git history — then Re-register cam-02 (LAUNCH E3).

### #32 — API-added MediaMTX paths vanished on every MediaMTX restart

**Found:** 2026-09-25. **Referenced from:** guide §16.2.1, LAUNCH.md E2/E3, CLAUDE.md.

The admin GUI adds a registered camera's path through MediaMTX's API, and MediaMTX never
writes API changes back to `mediamtx.yml`. Every MediaMTX restart therefore dropped every
GUI-registered camera until someone clicked Re-register — the reason for #31.

**Fix:** `adapter/sync_mediamtx_paths.py` as `ExecStartPost=` of `kvs-mediamtx.service`:
after every start (automatic crash restarts included — a separate unit ordered after
MediaMTX would miss those) it adds each passthrough camera's path from the registry, or
from a 0600 cache when AWS is unreachable; it never deletes a path and leaves unchanged
ones alone. Verified by `kill -9` on MediaMTX: the path was back after the automatic
restart.

### #35 — MediaMTX's generated `auto.crt`/`auto.key` were not git-ignored

**Found:** 2026-09-25. **Referenced from:** LAUNCH.md A5, `.gitignore`.

MediaMTX v1.20 generates a self-signed certificate and private key (`CN=mediamtx`) in
`mediamtx/` on its first start. `auto.key` was ignored only by the generic `*.key` rule;
`auto.crt` not at all — it would have gone into the next commit.

**Fix:** both named explicitly in `.gitignore`.

### #38 — Instructions that failed when followed literally

**Found:** 2026-09-25, reading every current document against the system and against each
other. **Referenced from:** guide §2.8, §4.1, §7.3, §16.6, §17, LAUNCH.md Part B/C/F,
README.

Most of what the review found was drift — statements the system had outgrown, fixed in
place without an entry. These are the ones that *break something* when a reader follows
them as written:

- **Guide §4.1 installed `gstreamer1.0-omx-generic`**, which does not exist on Debian
  trixie, so the whole `apt install` failed. LAUNCH A3 and README had dropped it; the guide
  had not.
- **Guide §7.3 restarted the agent with `sudo systemctl restart kvs-agent.service`**, but
  `kvs-agent` is a *user* unit: the system manager has no such unit, so the proof step
  restarted nothing (the very next line already used `journalctl --user`).
- **`adapter/bin/awsblock.sh`, the outage simulator LAUNCH Part C prescribes, needs
  `iptables`/`ip6tables`**, which a fresh Raspberry Pi OS image doesn't have (it ships
  `nft` only). Nothing said to install it; the old Pi had it from an earlier session.
- **Guide §2.8's `kvs-mediamtx` unit lacked the `ExecStartPost=` path sync**, so a reader
  building units from the guide rather than LAUNCH A8 got MediaMTX with no camera paths
  (#32). §16.6 enabled template instances by camera ID (`kvs-cam@cam-01`) where the real
  ones are named by path (`kvs-cam@cam01`) — #37's mix-up, in the docs.
- **LAUNCH Part B never enabled `kvs-outage-buffer`/`kvs-outage-uploader`**, while Part C
  checked that both were running and README's start list enabled them. Part F and README
  stopped only `kvs-cam01`/`kvs-cam02`, leaving GUI-registered producers billing.
- **Twelve references to a `COSTS.md` that doesn't exist**, with v1.3's section numbers
  and figures ($4.13/month, "4.7×, near 700 cameras") that COSTS-1.4 had revised.

**Fix:** each corrected where it appears — package dropped, `systemctl --user`, an
`apt install iptables` step in LAUNCH Part C, pointers from the guide's historical units
and code listings to LAUNCH A8 and the current code, the outage units in Part B, `stop
'kvs-cam*'` in Part F and README, and every `COSTS.md` reference remapped to
`COSTS-1.4.md`'s sections and figures.

### #39 — The outage supervisor crash-looped on a Pi without the USB stick

**Found:** 2026-09-25, verifying LAUNCH Part B on the second Pi. **Referenced from:**
`outage_buffer.py`.

`kvs-outage-buffer` showed `activating`, not `active` — 80 restarts. At startup,
before its loop ever called `buffer_ready()`, `main()` created `OUTAGE_DIR` to scan for
orphan captures. With the stick not set up (LAUNCH.md A10), `/mnt/vms-buffer` didn't exist,
and creating it needs root: `PermissionError`, exit, `Restart=on-failure`, repeat. The
design says a missing stick means *idle, disarmed* — the sentinel check exists for exactly
that — and the docs promise the outage units idle unless enabled; the one unguarded line
ran first. `is-active` alone would not have shown it: the state word was `activating`,
and only the restart counter and the journal told the story.

**Fix:** nothing touches the buffer until `buffer_ready()` passes; the orphan scan runs
once, on the first ready tick, and readiness changes are logged ("buffer unavailable (not
mounted) -- idle …"). Verified on the Pi without a stick (active, no restarts) and with a
simulated stick appearing mid-run (ready logged, one orphan capture reported once).
(`outage_uploader.py` was already gated on `buffer_ready()`.)

### #40 — cam-01 never published: `gstreamer1.0-rtsp` missing from the setup lists

**Found:** 2026-09-25, "cam01 does not provide video" on the second Pi. **Referenced
from:** LAUNCH.md A3, README §3, guide §4.1.

`kvs-camera-publish` had restarted 167 times: `WARNING: erroneous pipeline: no element
"rtspclientsink"`. Camera detection worked; the element that publishes into MediaMTX did
not exist, so `rtsp://127.0.0.1:8554/cam01` was a 404 and nothing downstream had a source.
`rtspclientsink` ships in `gstreamer1.0-rtsp`. Only guide §2.4 installed it; LAUNCH A3's
package list — the one a fresh Pi is set up from — and README §3 did not. (`rtspsrc`, what
the producers use to *read* from MediaMTX, is in `plugins-good`, so they were unaffected.)

It survived the Phase 2 hardware test because that test ran `publish-cam01.sh`'s exact
pipeline with the sink swapped for a local file (MediaMTX wasn't installed yet): the one
element missing was the one the test replaced. A test that substitutes a component proves
nothing about that component.

**Fix:** `gstreamer1.0-rtsp` (and `v4l-utils`) in LAUNCH A3 and README §3, and
`gst-inspect-1.0 rtspclientsink` in A3's proof. Verified: publisher `active` with a stable
restart count, MediaMTX `cam01` ready, H.264 High 1280×720, decoded frame correct.

### #41 — `cloud/iam/` policy files had drifted from the deployed policies

**Found:** 2026-09-26, checking NETWORK.md's claim that recreating a KVS stream needs IAM
edits against `CLAUDE.md`'s claim that it doesn't. **Referenced from:** guide §8.1,
NETWORK.md §4, `cloud/iam/`.

The two claims disagreed because the repo and AWS did. Every policy document in
`cloud/iam/`, the IoT policy and the IoT rule were compared with what is deployed:

| File | Repo | Deployed |
|---|---|---|
| `kvs-producer-policy.json` (`KVSAdapterRole`) | `stream/cam-01/*`, `stream/cam-02/*` only | `stream/cam-*/*`, **plus** `CreateStream`, DynamoDB `cameras` read/write, `iot:Publish` on `adapter/adapter-01/event` |
| `get-hls-url-policy.json` | cam-01's and cam-02's exact stream ARNs | `stream/cam-*/*` |
| `clip-to-s3-policy.json` | the same two exact ARNs | `stream/cam-*/*` |
| — | missing | `CamerasRegistryRead` (`GetItem` on `cameras`) on four Lambda roles |
| — | missing | `ListCamerasAccess` (`Scan` on `cameras`) |

The account itself was right — policies had been widened in place as cameras, the
registry, GUI registration and event publishing were added — but the files were not
updated. A rebuild from `cloud/iam/` (README "Rebuilding on a different AWS account")
would have produced an adapter that could not register a camera, read the registry or
publish a detection event, and Lambdas that served no camera after `cam-02`. Guide §8.1's
setup block still wrote the first single-stream version over the file.

The other ten IAM files, the IoT thing policy and the IoT rule matched.
`client-bucket-oac-policy.json` differs from the live bucket policy by design: it is the
target of §8.5.1's pending CloudFront cutover.

**Fix:** the three drifted files rewritten from the deployed documents, the two missing
ones added (`cameras-registry-read-policy.json`, `list-cameras-policy.json`), each
re-verified identical to AWS; a warning in guide §8.1 not to re-run its `cat >` lines. When
a policy is changed in AWS, export it back into `cloud/iam/` in the same change.

### #42 — cam-01 dead after a reboot: camera not yet enumerated, and nothing retried

**Found:** 2026-09-26, "I cannot watch cam01 in the web GUI" after the Pi had rebooted.
**Referenced from:** `detect-hw.sh`, LAUNCH.md A8 (`kvs-camera-init`, `kvs-camera-publish`).

Start in the GUI worked — the producer launched and sat at "Connecting to
rtsp://127.0.0.1:8554/cam01" — but MediaMTX had no `cam01`: `kvs-camera-publish` was
`inactive (dead)`. The persistent journal (#33) showed why:

- **10:01:17** the kernel enumerated the PW310 on USB;
- **10:01:26** the user manager started `kvs-camera-init`, whose detection found **no**
  `/dev/v4l/by-id/` link yet — the driver and udev were still working through boot;
- `kvs-camera-init` is a oneshot with no retry, so it stayed `failed`;
- `kvs-camera-publish` had `Requires=kvs-camera-init`, so its start job failed with
  `result 'dependency'` — and a dependency failure is **never** retried, whatever the
  unit's own `Restart=on-failure` says.

One missed look at boot therefore killed cam-01 until someone restarted it by hand. The
hardcoded device path before Phase 2 would have failed the same way; this boot was simply
the first where the link lost the race.

**Fix, three layers:** `camera_setup` waits up to `CAMERA_WAIT_SEC` (default 30 s) for a
camera to *appear* — "several cameras" still fails at once, since waiting can't fix
ambiguity, and `--print` never waits; `kvs-camera-init` has `Restart=on-failure`;
`kvs-camera-publish` only `Wants=` the init unit (it still `Requires=` MediaMTX), so a
failed exposure lock can't block the video for good. Verified: the late-appearing camera
found after 3 s in a simulated race; both units active after restart; and end to end
through the cloud — producer started, KVS HLS decoded (H.264 High 1280×720, the frame
showing the wall clock at the capture time), producer stopped again.

### #43 — cam-02 played only after several Start/Stop/Reload rounds

**Found:** 2026-09-26, "cam-02 starts to stream only after several presses of Start,
followed by Stop and Reload Player", right after cam-01 was fixed (#42).
**Referenced from:** `stream-cam02.sh`, `stream-cam01.sh`, `stream-channel.sh`,
`client/index.html` (`cmd()`/`load()`), AUDIO.md, Camera-Features.md, CLAUDE.md.

Two independent faults, either one enough to leave the player on "stream is not live":

1. **The client looked once, too early.** `cmd('start')` called `load()` a single time,
   8 s after the API returned. KVS serves LIVE HLS only once a fragment is complete, and
   a fragment closes on the *next* keyframe. cam-02 sends one every 3 s (camera-side GOP,
   not ours), so after connect (~2 s), waiting for the first keyframe and closing the
   fragment, the first `PERSISTED` ack came **9.3–10.1 s** after Start (journal:
   10:46:56.7 → 10:47:06.8, 10:57:09.1 → 10:57:18.4; re-measured after the fix: 10.9 s
   until `GetHLSStreamingSessionURL` succeeded). The 8 s look got a 503 and never
   retried; "Press Start to begin" invited exactly the Stop/Start/Reload dance. cam-01
   escaped because its encoder's GOP is short and under our control.
2. **The video-only pipeline sometimes died on its own.** The camera always sends a G.711
   track, and MediaMTX re-serves it whether or not audio is enabled. `rtspsrc ! rtph264depay`
   links only the video pad; the audio pad stays unlinked. In 2 of the 5 Starts that
   morning the audio pad appeared first (10:57:03.766 vs video at .826) and rtspsrc
   stopped the pipeline 15 ms after the video linked: `Internal data stream error …
   streaming stopped, reason not-linked (-1)`. systemd restarted it 5 s later, which
   pushed the first fragment out to ~20 s — far past the client's single look. It is a
   timing race: 16 runs (8 with `fakesink`, 8 with `kvssink`) did not reproduce it, which
   is also why it looked random.

**Fix:** the video-only branches of `stream-cam02.sh`, `stream-cam01.sh` and the template's
`stream-channel.sh` link `application/x-rtp,media=audio` to `fakesink sync=false
async=false` (inert when there is no audio track — checked against cam01, which has
none), so the audio stream can never report `not-linked`. The client now treats 503 after
Start as "not yet": it first looks at 5 s and re-polls every 3 s for up to 45 s, with a
"waiting for the first video" overlay; Stop clears the window. Verified: four real
`kvs-cam02` Start/Stop cycles with zero `not-linked` and zero restarts, cam-02 frame
decoded from cloud HLS (H.264 High 640×360, on-screen clock matching capture time), client
deployed and served by CloudFront.

---

## Codec selection (2026-09-26)

### #44 — A producer stopped for good whenever MediaMTX closed its session

**Found:** 2026-09-26, live test for H.264/H.265 selection (`measurements/codec-phase0.md`):
switching `cam-02`'s encoder to H.265 while `kvs-cam02` was streaming left the unit
`inactive`, not `failed`, and nothing restarted it.
**Referenced from:** `adapter/bin/producer-lib.sh` (`producer_run`), `stream-cam01.sh`,
`stream-cam02.sh`, `stream-channel.sh`.

Every producer ended in `exec gst-launch-1.0 …`, so gst-launch's exit status was the
unit's. A camera-side codec switch makes MediaMTX restart the path, and a restarting path
closes its readers. rtspsrc reports that as a clean end of stream: journal `The server
closed the connection.` → `Got EOS from element "pipeline0"` → exit 0 → systemd
`Deactivated successfully`. `Restart=on-failure` does not restart a clean exit, so the
cloud stream stayed down while the unit looked exactly like a user Stop (`Result=success`,
`NRestarts=0`, GUI status "inactive").

Not specific to codec switching: MediaMTX closes a path's readers whenever its source goes
away or changes. That covers a camera dropping off the network (`cam-02` did, for 5 min,
the same day), a re-registration that moves the source URI, and a restart of `cam-01`'s
publisher. It was silent because the failure looked like success at every layer.

**Fix:** `producer_run` in `adapter/bin/producer-lib.sh` replaces `exec`. It runs the
pipeline and treats any return as a failure: it logs `pipeline ended (source closed the
session)` and exits 1, and the existing `Restart=on-failure` restarts it 5 s later. A real
Stop never reaches that exit, because systemd signals the whole unit. The gst-launch
argument lists are unchanged token for token (checked by running old and new scripts
against a stub `gst-launch-1.0`). Verified live on `cam-02`: H.264 → H.265 → H.264 camera
switches while streaming, each followed by a restart within ~10 s on the new codec; cloud
HLS `avc1` → `hvc1` → `avc1` with decoded frames; Stop still leaves the unit `inactive`.

### #45 — Manual recording without footage returned a raw AWS 500, not the intended 503

**Found:** 2026-09-26, while making clips survive a codec switch (codec selection Phase 5).
**Referenced from:** `cloud/lambda/record_clip.py`, `cloud/lambda/clip_to_s3.py`
(`no_fragments`); `get_hls_url.py` carries the same lesson in a comment.

`record_clip.py` meant to answer "no footage found for that time range -- was the stream
live throughout?" with a 503 when KVS had nothing for the window. It caught
`kv.exceptions.ResourceNotFoundException`, but `GetClip` is called on the
`kinesis-video-archived-media` client, and boto3 generates a separate exception class per
client, so the handler never matched. Every such request fell through to the generic
handler: HTTP 500 carrying the raw AWS text. Reproduced by calling the unfixed handler for
a window with no footage: `500 {'error': 'An error occurred (ResourceNotFoundException)
when calling the GetClip operation: No fragments found in the stream for the clip
request.'}`. `get_hls_url.py` once had the identical fault; it was fixed there with an
explanatory comment but never recorded, so the pattern survived next door.

**Fix:** both clip Lambdas match on the error *code* (`no_fragments()`:
`ClientError` with `Code == "ResourceNotFoundException"`), as `get_hls_url.py` does.
The codec-switch split needs exactly that test anyway: one side of a split may
legitimately have no footage. Verified on the deployed `record-clip`: the same empty window
now returns `503 {'error': 'no footage found for that time range -- was the stream live
throughout?'}`.

---

## Ported from the base project, and shared air (2026-09-27)

This repository and its base project `VMS` (github.com/vizdr/VMS) share an AWS account, a
Pi and, until now, their defect numbers: #44 and #45 are the same bugs in both. VMS found
#46–#50 on 2026-09-26/27; they are recorded here under the same numbers, each with what was
checked *on this Pi*, so a `#N` means the same thing in either repository.

### #46 — The producer could hang `active` on EOS, uploading nothing

**Found:** in VMS (its #46), verifying #44's fix. **Referenced from:**
`adapter/bin/producer-lib.sh` (`producer_run`), LAUNCH.md A8.

#44's fix assumes gst-launch *returns* when MediaMTX closes its session, and turns that
return into a failure. VMS measured that it sometimes does not return at all: kvssink takes
the EOS, its `PutMedia` connection starves (`Operation too slow. Less than 30 bytes/sec`,
then `exited without triggering end-of-stream. Service call result: 599`), and the process
stays alive — `active (running)`, `NRestarts=0`, zero `PERSISTED` acks, for 4+ minutes.
That is worse than #44: the unit reports itself as streaming in both GUIs.

**On this Pi** the hang did not reproduce on the one try made: a MediaMTX kick of
`kvs-cam02`'s reader session (`POST /v3/rtspsessions/kick/<id>`, the trigger VMS used) with
the old code ended in `Got EOS` → `pipeline ended` → restart. It is intermittent, and the
cost of missing it is a producer that looks healthy while uploading nothing, so the fix was
ported rather than waited for.

**Fix:** VMS's `producer_run`. gst-launch's output goes through a named pipe the function
reads line by line — forwarded to the journal and matched against
`PRODUCER_FATAL_PATTERNS` (`The server closed the connection.`, `EOS Event received in
sink`, `Got EOS from element`). On a match it `kill -9`s the pipeline (SIGTERM would wait
on the wedged teardown), stops reading instead of waiting for EOF, and exits 1. The FIFO
lives in `$RUNTIME_DIRECTORY` (`RuntimeDirectory=vms-producer`, added to LAUNCH.md A8's
units), falling back to one fixed-name FIFO per camera in `/tmp` when the unit lacks it —
as the installed units here do until A8 is re-run. VMS's entry records three mechanisms
that look right and are not (`| while read`, both forms of `coproc`).

**Verified here:** stub pipelines that exit 0, exit 3, or hang after printing a fatal line
all end in exit 1, the hanging ones within 1 s. Live on `kvs-cam02` (FIFO in `/tmp`): kick
→ `'The server closed the connection.' -- the source closed the session; killing the
pipeline` → `status 137, killed by the watchdog` → restart 6 s later on H.265, exactly one
`gst-launch` for cam-02 afterwards (no orphan).

### #47 — Admin GUI timed out in the browser; cam-02 dropped out while uploading

**Found:** in VMS (its #47: "Local Admin can not be launched — `Netzwerk-Zeitüberschreitung`"),
and reported here the same day: the admin GUI would not open and neither camera could be
watched after opening the cloud client. **Referenced from:** LAUNCH.md A1, README (install).

The Pi is on Wi-Fi only (`eth0` has no carrier), at −68 dBm, and on this Pi **`iw dev wlan0
get power_save` said `on`** — VMS's fix had been made on its own installation, not here.
With power save on the radio sleeps between beacons: traffic the Pi starts works, but a
connection coming *in* — the admin GUI on :8080, the only inbound service in this
outbound-only design — times out in the browser while every check on the Pi says healthy.

This Pi showed a second, larger symptom of the same shared radio. **cam-02's RTSP source
into MediaMTX broke only while a producer was uploading:** `TCP timeout`, `connection reset
by peer`, `EOF`, `218 RTP packets lost`, every 10–40 s.

| When | Upload to AWS | cam-02 source errors |
|---|---|---|
| 2026-09-26 19:00–20:59 | `kvs-cam01` running (18:43–20:33) | 36, 38 per hour |
| 2026-09-26 21:00 | stopped 20:35 | 4 |
| overnight to 15:00 | none | 0 |
| 2026-09-27 15:00 hour | `cam01` from 15:14, `cam02` from 15:18 | 70 (first at 15:16) |

One radio carries cam-02's stream *in* and the cloud upload *out*, on a weak link, with
power save on. The producers restarted correctly after every break (#44) — which is exactly
why nothing could be watched: the stream never stayed up. The same starved link made
kvssink's credential fetch fail on name resolution (`Resolving timed out after 3000
milliseconds`, 4 times in 3 h; the SDK allows DNS 3 s), which is how `kvs-cam01` failed to
start at 15:18.

**Fix:** power save off, persisted in NetworkManager (LAUNCH.md A1 has the commands and the
proof). The NetworkManager profile needs single quotes around a name like `FRITZ!Box …` —
inside double quotes bash treats `!` as history expansion (`event not found`).

**Verified** the same afternoon: `iw` reads `Power save: off`, the profile reads `disable`,
and with **both** producers uploading for 8 minutes — the load that broke cam-02 before —
cam-02 logged 0 source errors, 0 producer restarts and 0 DNS timeouts, while KVS acknowledged
238 cam-01 and 160 cam-02 fragments, exactly one per GOP (2 s and 3 s). The signal is still
weak (−70 dBm); if the dropouts return, the next step is a cable — the camera on the Pi's
`eth0` (`NETWORK.md` §3's isolated segment) takes its stream off the air entirely.

### #48 — Deploys from two repositories into one AWS account overwrote each other

**Found:** in VMS (its #48), investigating "streaming does not work at all on both cameras".
**Referenced from:** CLAUDE.md ("Deploying a Lambda", "Deploying the browser client").

VMS and this repository deploy the same function names and the same client object into
account 596633517506, and neither deploy command names a repository. On 2026-09-26 VMS
deployed `record-clip` (20:00 UTC) and `index.html` (20:03 UTC) over this repository's
H.265-era versions — losing the codec-split clip windows, the MP4 codec sniffing and the
client's HEVC detection — silently: the commands are idempotent and nothing errors. The
client bucket keeps no versions. VMS restored everything from this tree on 2026-09-27 and
synced its own copies, so both trees now hold the same client and Lambdas.

**Checked here:** the live client (CloudFront) is byte-identical to this repository. The
Lambdas could not be compared — the AWS CLI session had expired; rerun the comparison in
CLAUDE.md "Deploying a Lambda" after `aws login`.

**Fix (process):** CLAUDE.md's two deploy sections now say the account is shared, and how
to check what is live, and from where, before deploying.

### #49 — *(VMS only)*: automatic rollback read its backup list too late

Recorded so the numbers stay aligned with VMS. The defect is in VMS's camera-configuration
apply/rollback (`_apply_and_verify`, `configure-camera.sh`), which this repository does not
have — nothing to fix here. The lesson it shares with #50 — test the safety net, don't read
it — applies generally.

### #50 — MediaMTX reported a path `ready` while no media flowed

**Found:** in VMS (its #50): `GET /v3/paths/get/cam01` said `"ready": true` with
`"tracks": ["H264"]` while `ffprobe` got `width=0 height=0`. **Referenced from:**
`adapter/mediamtx_api.py` (`flowing_paths`), `adapter/bin/stream-codec.py`,
`adapter/onvif-admin/` (`on-wire`, codec status).

`ready` describes a handshake — a source connected and declared its tracks — not media
arriving. This repository used it three ways as if it meant "video is here":
`stream-codec.py` started a producer as soon as the path was ready; the admin GUI's codec
switch confirmed "on the wire: H.265" on it; and the table's status line said the same.

**Reproduced here:** an ffmpeg publisher on a throwaway path, frozen with SIGSTOP. For the
9 s observed, MediaMTX kept reporting `ready=True`, `tracks=['H264']` and a detectable codec,
while `bytesReceived` stood still at 121630.

**Fix:** `mediamtx_api.flowing_paths()` / `path_is_flowing()` — a path counts as live only
when `bytesReceived` grows across a ~1 s sample. `stream-codec.py` now waits for flow, not
readiness, before a producer starts; the admin GUI's `on-wire` returns `flowing`, the switch
confirms only once video flows, and the status line distinguishes "on the wire" from
"declared, but no video is flowing". Verified: the frozen publisher reads `flowing=False`
from the first sample; both live cameras read `True`; a missing path exits `stream-codec.py`
with "no H.264/H.265 video flowing".

### #51 — Clips older than the newest 50 could not be reached from the browser

**Found:** 2026-09-28, reading `list_clips.py` while planning clip-list paging.
**Referenced from:** guide §9.3, `cloud/lambda/list_clips.py`.

`list-clips` queried DynamoDB with `Limit=50` and returned that one page, with no way to
ask for the next. The client rendered whatever came back. With more than 50 clips on a
camera, the older ones disappeared from the browser: they couldn't be played, re-tiered
or deleted there, and nothing said so. Detection mode can easily produce 50 clips on one
camera, and the outage backfill adds more. The comment beside the loop ("fine at demo
scale (tens of clips)") covered the per-clip `head_object` cost, not the cut-off.

**Fix:** paging (guide §9.3). The route takes `limit` and `page` (a number or `last`) and
returns `page`, `pages` and `total`. The client shows ten clips per page, with numbered
pages 1..N, Newest / Newer / Older / Oldest, and the clip total. All of them are redrawn on
every load, so they follow deletes and new clips. A caller without `limit` still gets the
newest 50, so VMS, which shares the function (#48), is unaffected. (The first version,
live for a few hours on 2026-09-28, used a `before`/`next` cursor. Cursors can't give a
page count or jump to the last page, so it was replaced.)
Verified locally: the real Lambda over a moto DynamoDB/S3 pages lists of 0, 1, 9, 10, 11,
20, 23 and 61 clips with no gap, overlap or empty last page. It also counts keys past
DynamoDB's 1 MB response page, clamps a page past the end, and renumbers after deletes and
additions. The page in headless Chromium was driven through numbered jumps, Oldest,
windowed numbers on a 95-clip camera, deletes that remove a page, new clips, a tier
change, a recording and a failed load, on both a USB and an ONVIF camera.

**Live, 2026-09-28**, through API Gateway `test-invoke-method` (it skips the Cognito
authorizer), after deploying the page-number version:
- cam-02's 35 clips page 10/10/10/5 as 4 pages, total 35, in the same order as the
  unpaged response. cam-01's two clips fit one page.
- `page=last` and `page=99` both return page 4.
- A request without `limit`/`page` returns the pre-paging `{"clips": [...]}` shape,
  unchanged.
- Bad `page`/`limit` values get 400.

When checking this way with a value that holds a `+`, pass it decoded:
`test-invoke-method` hands the query string to the Lambda without decoding it, unlike a
real request.

---

## Ported from VMS (2026-10-25 round, `VMS-PORT-PLAN.md`)

Entries #52–#75 are VMS's #51–#74, in the same order, so **this number = VMS's number + 1**.
Each says what broke and what fixed it in one paragraph; VMS's `FoundAndFixed.md` has the
full diagnosis. The code was ported on 2026-10-25 onto a freshly re-imaged card (#76);
"verified here" names what was proven on this Pi, "pending" what still has to be.

### #52 — The cam-01 publisher hung silently and stayed `active`, dead

**Found in VMS as #51** (2026-10-03): `gst-launch` stopped producing without exiting, at
boot and once after an hour, with no error and no EOS, so `kvs-camera-publish` stayed
`active` while MediaMTX received nothing. `publish-cam01.sh` ran the pipeline under `exec`,
so nothing watched it. **Fix:** `publish_run` — MediaMTX's `bytesReceived` for `cam01` is
read every 10 s and the pipeline is SIGKILLed after 30 s without growth; any end of the
pipeline exits 1 so `Restart=on-failure` brings it back. Only this function was ported, not
VMS's `cam01.env`-driven pipeline (R2). **Verified here** (2026-10-25): a pipeline end exits 1 and systemd restarts the publisher (seen when #75's watchdog fired); the 30 s byte check itself has not fired yet.
### #53 — The FRITZ!Box drops the Pi's Wi-Fi after bursts of group rekeying

**Found in VMS as #52** (2026-10-03, counted 2026-10-17): after a burst of four group rekeys
one second apart, the access point disconnects the Pi (`reason=2`) and every AWS client
loses ~40 s to 3 min. Environmental: same router, same board. **Status:** open; Ethernet is
the fix VMS recommends. See #79 for the related silent stall found on this card.

### #54 — H.265 detection clips listed as 45 s held 9 s

**Found in VMS as #53** (open there, in this repository's `clip-to-s3`): `durationSec` was
the requested window, not what `GetClip` returned. **Reproduced here** from the evidence
bucket: cam-02's clip of 2026-09-28 09:47 is listed 45 s and holds 9 s (ffprobe); a manual
clip listed 37 s holds 32 s. **Fix:** `mp4_duration_sec()` in `clip_to_s3.py` and
`record_clip.py` reads `moov/mvhd` (GetClip's MP4 is not fragmented); the window length is
only the fallback for an unreadable file. Verified on three real clips: 9, 44 and 32 s, as
ffprobe. `clip-to-s3` is still VMS's too; the change is additive (same field, truer value).
**Deployed** 2026-10-25 (`clip-to-s3`, `record-clip`). Why cam-02 returned only 9 s is still open.

### #55 — The AWS block missed the IoT endpoint's new address

**Found in VMS as #54**: since 2026-10 the IoT data endpoint also resolves into
`63.176.0.0/12`, which `awsblock.sh` didn't block, so a test outage "recovered" after 7 s.
**Fix:** the range added to `awsblock.sh`; OUTAGE.md's rule: check the endpoint with
`getent ahosts <IOT_DATA_ENDPOINT>` before believing an outage test. **Verified here:** pending.

### #56 — A DNS failure froze the whole admin page

**Found in VMS as #55**: every camera-status poll looked the camera up in DynamoDB; with DNS
failing each lookup hung ~36 s, and six of them filled the browser's connection pool. **Fix:**
`GET /api/cameras/<id>/status` checks the ID against the outage supervisor's local registry
cache (DynamoDB only without one); the codec, stream and detection routes run once per table
load on short timeouts, never from a poll. **Verified here** (2026-10-25): with DynamoDB pointed at an address that never answers, every status poll answered from the cache in ~50 ms (an unknown camera: 404 in 2 ms), while the old per-poll lookup was still hanging after 20 s.
### #57 — *(hardware note)* A USB 3 stick next to a 2.4 GHz radio deafens it

**Found in VMS as #56** with the Pico 2 W, which this repository doesn't have. Recorded so the
numbers stay aligned; the stick is in a USB 3 port here too, and the Pi's own radio is on
5 GHz, so it is not known to matter. Nothing to fix.

### #58 — kvssink dropped every keyframe of an H.265 camera

**Found in VMS as #57**: cam-03's H.265 leaves `h265parse` with `dts: none`; kvssink drops any
HEADER buffer without a DTS, i.e. every keyframe, so no fragment ever starts and the unit stays
`active`. **Fix:** `h265timestamper` at the end of the H.265 chain (`producer-lib.sh
video_depay_chain`). **Verified here** (2026-10-25): cam-03 on H.265, fragments persisted every 4 s; the only drop was the stream's first (non-key) frame; live HLS `hvc1.1.6.L90`, 800×448, a clean frame from KVS.
### #59 — H.265 with audio: video ran on a clock 1000 h ahead of the audio

**Found in VMS as #58** (its diagnosis corrected by its #62): the timestamper shifts every
timestamp by 1000 h, harmless alone; beside an audio track KVS refused audio frames in
bursts (`0x30000005`). **Fix:** `identity single-segment=true` after the timestamper.
**Verified here** (2026-10-25): cam-03 with audio, 4½ min: 63 fragments, zero rejected frames, AAC in the live HLS. (cam-03's own G.711 track is silent at the source, −91 dB, so its sound was proven on cam-02 and cam-04.)
### #60 — A GUI-registered camera could never record audio

**Found in VMS as #59**: registration wrote no `audioCapable`, and `stream-channel.sh` sent the
audio track to a fakesink only. **Fix:** discovery reads the microphone from the profile
(`onvif_discovery._audio`) or, where the profile names none, from the stream's SDP (#67);
registration writes `audioCapable` (G.711 only); `stream-codec.py` prints `AUDIO_TRACK`
(`pcma`/`pcmu` from MediaMTX's `muLaw`) and `stream-channel.sh` gained the G.711 → AAC 8 kHz
branch, depayloader by law. **Verified here** (2026-10-25): discovery shows the microphone on all three ONVIF cameras; cam-02 (mu-law), cam-03 (mu-law) and cam-04 (A-law) recorded G.711 → AAC with zero rejects; cloud audio at room level (cam-02 −38…−43 dB, cam-04 −37 dB).
### #61 — Start during an MQTT drop was lost, and the page only said "not available"

**Found in VMS as #60.** **Status:** open (the command is fire-and-forget over the shared MQTT
session). The page half is done: when no video arrives in the 45 s start window, Start and
Live now say "The Pi didn't answer: it may be offline from AWS" instead of "not available".

### #62 — cam-01 with audio on: Start never restarted the publisher

**Found in VMS as #61**: the publisher reads `audioEnabled` once at its own start; Start
restarts only the producer, so a flag switched since left the producer waiting on an audio
pad that never came, and kvssink stalled the video too. **Fix:** `stream-cam01.sh` compares
the flag with the tracks MediaMTX carries for `cam01` and restarts `kvs-camera-publish` when
they disagree, both ways; still no microphone track: video only. **Verified here** (2026-10-25): both directions — audio off: Start restarted the publisher and MediaMTX dropped the LPCM track; on: restarted again and the microphone returned. First fragment ~40 s after Start when the publisher has to restart.
### #63 — Audio cameras lost audio frames: a millisecond tie in kvssink, invented DTS on cam-01

**Found in VMS as #62**: kvssink's track merge mishandled audio and video timestamps that tie
within a millisecond, and cam-01's encoder frames had no DTS, so kvssink invented
`last_dts + 40 ms`. **Fix:** SDK patch 4 (LAUNCH.md A3, whole-millisecond timestamps) and
`h264timestamper ! identity single-segment=true` on cam-01's video in its audio branch.
**Verified here** (2026-10-25): patch 4 built; cam-01 with audio, 3 min: 86 fragments, zero rejects.
### #64 — IR buttons had ONVIF's ON/OFF backwards

**Found in VMS as #63**: ONVIF's `IrCutFilter` ON means the filter is *in* (day, colour, IR
off); both GUIs labelled ON "Force Night"/"IR On". **Fix:** both pages: Day = `ON`,
Night = `OFF`, and the status line names the effect. cam-03 ignores the setting (VMS #63).

### #65 — Detection never fired on cam-03, and the watcher leaked subscriptions

**Found in VMS as #64**: modes mapped to cam-02's topic names, which cam-03 doesn't publish;
`event_watcher.py` re-subscribed every ~100 s without renewing or unsubscribing, filled
cam-03's 10 subscription slots, and then nothing could subscribe — VMS on the other card
included, since the cameras are shared (VMS-PORT-PLAN.md D1). **Fix:** `onvif_events.py`
(per-camera topics from `GetEventProperties`, `PullSubscription` renewing every 20 s and
always unsubscribing); the watcher, both GUIs (unsupported modes greyed out, "Test
triggers") and both mode routes (`set-camera-mode` now refuses them too, 409) use it.
`replay-gate.py`: identical results before and after on both recorded logs. **Verified
here:** live pending; `kvs-event-watcher` stays disabled until then (plan 0.5). **Verified here** (2026-10-25): three back-to-back trigger tests on cam-03 each subscribed, received the opening state and unsubscribed, with no error or leaked session; the watcher is enabled again and idles while no camera is in a detection mode. A test that *sees* a trigger needs someone walking past (pending).

### #66 — The cloud player spun forever

**Found in VMS as #65**: after a quick Stop → Start, `LIVE` built the session from the old
run's last fragment; a failed playlist load was retried with `startLoad()`, which has no
playlist to resume; nothing caught a frozen player; hls.js was `@latest`. **Fix:** `since`
(LIVE_REPLAY from the button press) in `get-hls-url`, a stall watchdog (new session after
20 s without progress, a message after three), playlist failures go straight to a new
session, hls.js pinned to 1.7.3 (both pages). **Deployed** 2026-10-25 (`get-hls-url` and the page); the page passed a stubbed run in headless Firefox before the upload.

### #67 — A camera with no password couldn't be registered; its microphone wasn't found

**Found in VMS as #66** (cam-04: user `admin`, empty password): registration required a
password, the CLI's `None` crashed the digest, the RTSP URI from GetStreamUri carried no login
(every pull a 401, MediaMTX refuses an empty RTSP password), and its G.711 track appears only
in the stream's SDP. **Fix:** empty passwords accepted and stored as `""`; `rtsp_login()`
puts the login (placeholder `x` when empty) into the URI after one checking DESCRIBE;
`stream_audio()` reads the SDP. **Verified here** (2026-10-25): `discover-onvif.py --user admin` with no password enriched all three cameras; cam-04 re-registered with an empty password (its stored `admin:x@` URI kept), producer provisioned.
### #68 — cam-04's Motion could never fire: its events use another field than declared

**Found in VMS as #67**: cam-04 declares `MotionAlarm` with `State` and sends `IsMotion`; its
human detection never reaches ONVIF. **Fix:** `onvif_events.state_value()` — the declared
field first, else the first known state field present; the trigger test counts a `Changed`
message and keeps `Initialized` for the opening window, because cam-04 sends both for every
detection. **Verified here:** pending (needs someone walking past cam-04).

### #69 — A camera already sending H.265 at registration was labelled H.264

**Found in VMS as #68**: the registry learnt the codec only from a switch, so `list-cameras`
fell back. In this repository the same trap sat in `codec_caps.probe`: at registration the
new MediaMTX path isn't flowing yet, so `videoCodecDefault` came from the camera's *claim*
(cam-03 claims H264, sends H.265). **Fix:** `GET /api/cameras/<id>/codec` records the wire's
codec when the registry differs (once), and registration waits up to 15 s for the new path's
video before probing. **Verified here:** pending.

### #70 — Passthrough H.264 with audio lost audio frames; `h264timestamper` made it worse

**Found in VMS as #69**: kvssink invented `last_dts + 40 ms` for `h264parse`'s `dts: none`;
`h264timestamper` assumed 4 reorder frames on cam-04 (no B-frames) and KVS refused the video.
**Fix:** SDK patch 5 (a missing DTS becomes the PTS); the passthrough H.264 chain gets no
timestamper. **Verified here** (2026-10-25): patch 5 built; cam-04 H.264 1080p with A-law audio, 5 min: 145 fragments, zero rejects (VMS saw 45 before the patch); cloud plays `avc1.4d0029,mp4a.40.2`.
### #71 — cam-02's hard-coded A-law garbled mu-law speech, then stalled the stream

**Found in VMS as #70**: `stream-cam02.sh` hard-coded `rtppcmadepay ! alawdec`; cam-02 sends
mu-law since a change in its own audio page. **Fix:** the law from the wire (`AUDIO_TRACK`),
in `stream-cam02.sh` and `stream-channel.sh`; no G.711 track: video only. **Verified here** (2026-10-25): cam-02's track reads `pcmu` from the wire and records with zero rejects; the old A-law constant would have stalled it.
### #72 — A producer connected but feeding kvssink nothing looked healthy

**Found in VMS as #71.** **Fix:** `producer_run`'s second watchdog — 45 s without kvssink's
"client and stream metrics" line (logged per key frame put, so it continues through an AWS
outage) kills the pipeline; enforced only once DEBUG lines are seen. **Verified here** with a
fake `gst-launch-1.0` on `PATH`: a silent pipeline killed at the limit (exit 1), a steady one
untouched; live pending. **Verified here** (2026-10-25) with a fake `gst-launch-1.0` on `PATH`: a silent pipeline killed at the limit (exit 1), a steady one untouched; no false kill in today's 3–5 min live runs.

### #73 — cam-02 lost one AAC frame in six: paired RTP audio timestamps

**Found in VMS as #72**: cam-02 sends its 40 ms audio packets in pairs with one timestamp;
voaacenc resynced on each step back and flagged ~1 frame in 6 DISCONT, which kvssink drops.
**Fix:** `AAC_ENC` in `producer-lib.sh` (`perfect-timestamp=true tolerance=100000000`), used by
both passthrough producers. **Verified here** (2026-10-25): cam-02, 5 min with audio: **one** dropped AAC frame (the stream's first), not the ~300 of before.
### #74 — Discovery showed "G.711 8000 kHz"

**Found in VMS as #73**: cam-02 reports Media1 audio in Hz and bit/s. **Fix:**
`onvif_discovery._per_kilo`. **Verified here:** cam-02 now shows 8.0 kHz.

### #75 — cam-01's publisher sent audio, no video, for over an hour

**Found in VMS as #74**: with audio on, the microphone's LPCM kept `bytesReceived` growing, so
#52's byte check couldn't see the video stop. **Fix:** the video chain ends in
`watchdog timeout=10000`: no video buffer for 10 s is a pipeline error. **Verified here** (2026-10-25): on the first start of this card's stack the PW310's video stopped ~5 s in while its audio kept flowing; the watchdog fired after 10 s and the restarted publisher has run since without a trigger (45 min). Exactly the failure it exists for.

---

## Found on this card (2026-10-25)

### #76 — LAUNCH A3's patches 1–2 had nothing to patch on a fresh clone

**Found:** 2026-10-25, setting up a re-imaged card (the plan assumed the September setup,
VMS-PORT-PLAN.md D1). **Referenced from:** LAUNCH.md A3.

A3 said to apply patches 1 and 2 "before anything is built", but the files they edit belong
to `amazon-kinesis-video-streams-producer-c`, which the SDK only downloads *during* its
configure step (`fetch_repo(kvscproducer)`); on a fresh clone both `sed`s fail with "No such
file". The September card had them from an earlier attempt. Cloning that repository by hand
doesn't work either: ExternalProject would wipe and re-clone it. **Fix:** A3 runs the fetch
step alone first — exactly what `fetch_repo` does — then patches, then configures; `setup/setup-sdk.sh` does it that way.

### #77 — Patch 2 did nothing on CMake 3.31: 700 MB of OpenSSL test submodules came down

**Found:** 2026-10-25, while diagnosing why the network died during the SDK build.
**Referenced from:** LAUNCH.md A3 (patch 2b), #3.

`GIT_SUBMODULES ""` means "no submodules" only under policy CMP0097 (CMake 3.16+). The file it
is in declares `cmake_minimum_required(VERSION 3.6.3)`, so CMake 3.31 applies the old
behaviour — empty means *all* — and the configure cloned `boringssl`, `wycheproof` & co.:
701 MB in `open-source/local/libopenssl`, the download #3 records crashing the Pi in August.
Both lost sessions that afternoon happened during it (#79). **Fix:** patch 2b,
`cmake_policy(SET CMP0097 NEW)` after that `cmake_minimum_required`. Verified: the
re-configure fetched OpenSSL alone (161 MB, no `Submodule` line), and the build completed. `setup/setup-sdk.sh` applies 2b and warns if a `Submodule` line appears anyway.

### #78 — Two LAUNCH A3 proofs that couldn't tell success from failure

**Found:** 2026-10-25. **Referenced from:** LAUNCH.md A3.
- "`grep -c 'sending SIGTERM'` must be 0" counted earlyoom's own startup banner
  (`sending SIGTERM when mem avail <= 20.00% …`), so it was ≥1 on any day earlyoom started.
  A kill reads `sending SIGTERM to process …`.
- Patch 3's proof, `grep -A1 'UNIX AND NOT APPLE'`, matches two unrelated blocks the file
  already has, so it "passes" unpatched. An idempotence guard built on the same string
  skipped the patch here. The patch's own `-D_GNU_SOURCE` is the thing to look for.

**Fix:** both proofs now grep for what only success can produce (LAUNCH.md A3, `setup/setup-sdk.sh`).

### #79 — The Pi's Wi-Fi stops passing data while still "connected" — for up to 10 minutes

**Found:** 2026-10-25, three times in one afternoon: VS Code lost the Pi and a power cycle
"brought it back". **Status:** open — environmental; it is #53 (VMS #52), seen from the inside.
**Referenced from:** LAUNCH.md A1.

A watcher logging every 10 s with an fsync (`adapter/bin/netwatch.sh`, user unit `netwatch`)
caught five stalls. In each, router, internet and DNS all became unreachable at once while the
driver kept reporting the access point with fresh signal readings (−62 to −74 dBm), the SSH send
queue grew unsent, and the user's PC got "destination host unreachable" for the Pi. Power save
was off for the last three; memory, load and downloads made no difference (two hit an idle Pi).
Neither `brcmfmac` nor wpa_supplicant logs anything *during* a stall.

The two that weren't cut short by a restart show how one ends. 14:44:50 → 14:47:15: at 14:47:10–13
four group rekeys one second apart, then `CTRL-EVENT-DISCONNECTED reason=2`, a reconnect, and
traffic flows again — exactly VMS #52's sequence. So the Pi's *transmissions* stop reaching the
router (an uplink failure: it still hears the router's beacons, hence "connected" and a signal
reading), the router notices only at its next group rekey (every 10 min, here at `:x7:09`),
kicks the Pi off, and the Pi reconnects within seconds. A stall therefore lasts **until the
next rekey — up to 10 minutes**; the 14:31 one would have ended at 14:37:09, and the restart came
a minute earlier. (The 15:05 one, 45 s, ended without any log line.) A weak 5 GHz link
(−70 dBm, DFS channel 108) whose weak direction is the Pi's own transmit power fits all of it.

Two things made it look worse than it was: Wi-Fi power save was still on at first (#47's setting,
never applied on this card), and **journald writes info-level lines to disk only every
5 minutes**, so every power cut erased exactly the minutes before the drop.

**What to do when SSH drops:** wait up to 10 minutes before restarting; the Pi comes back by
itself at the router's next rekey. **Options for the cause:** Ethernet (eth0 has no cable yet);
the router's 2.4 GHz band (more range in the Pi's weak transmit direction); a shorter WPA
group-key interval on the FRITZ!Box would shorten the stalls but not prevent them.

### #80 — Ported Unregister would have failed silently: the sudo rule allowed only start/stop

**Found:** 2026-10-25, porting VMS's Unregister. **Referenced from:** LAUNCH.md A8.

VMS's `set_boot_start` runs `sudo systemctl enable|disable`, and its Re-register provisions
producers; VMS's card grants broad sudo. This card's narrow rule (#34) allowed only
`start|stop`, and `set_boot_start` runs with `check=False`: Unregister would have "worked"
while leaving the producer enabled to start at boot. **Fix:** the rule allows
`start|stop|enable|disable` on `kvs-cam…` units, `start|stop` on the new `kvs-live@` units,
and `provision-camera.sh … no-start`; still nothing else.

### #81 — Every GUI-registered camera's producer started at boot, pushing to KVS unattended

**Found:** 2026-10-25, verifying Re-register (VMS-PORT-PLAN.md 2.4, which asked for "enabled,
not started"). **Referenced from:** `adapter/bin/provision-camera.sh`, LAUNCH.md E3/E7.

`provision-camera.sh` ran `systemctl enable --now kvs-cam@<path>`, so each camera registered
in the admin GUI had its producer enabled: after every reboot it started by itself and pushed
to KVS until someone pressed Stop — the one thing the cost rule forbids (guide §1.2), and what
`kvs-cam01`/`kvs-cam02` were always kept from doing ("deliberately not enabled", LAUNCH.md
Part B). The guide's methodology sketch had the same `enable --now`. Nothing showed it: the
status column says `active` either way. **Fix:** provisioning starts (registration) or does
nothing (Re-register's `no-start`), and never enables; Re-register no longer re-enables an
unregistered camera. Unregister still disables, for instances provisioned before. cam-03's
instance, enabled by this session's first Re-register, was disabled the same minute.

### #82 — The new `list-cameras` answered 500 to every request: a Decimal it couldn't serialise

**Found:** 2026-10-25, running the changed function locally against the real `cameras` table
before deploying it. **Referenced from:** `cloud/lambda/list_cameras.py`, LAUNCH.md Part H.

The VMS port made `GET /cameras` return `detectionTests`, whose `events` counts are numbers.
DynamoDB returns every number as a `Decimal`, which `json.dumps` refuses, and the handler's
catch-all turned that into a 500 — for the whole camera list, so the cloud page would have
shown no cameras at all. The earlier fields never tripped it because `outageBufferSec` was
converted with `int()` by hand. Nothing in the code review showed it; one local run did.
**Fix:** `plain()` converts every value in the response, nested ones included. **The rule:**
before deploying a changed Lambda, run its handler locally against the real table (the
operator's AWS CLI credentials, `importlib` on the file) — the code a deploy can't take back
is cheap to run first.

### #83 — Start Recording could run with nothing recording; it failed only at Stop Recording

**Found:** 2026-10-25, when the user asked whether Start had to be pressed before Start
Recording, once Live had been added. **Referenced from:** `client/index.html`
(`setArchiving`, `checkArchive`), guide §22.5.

The cloud page's Start Recording sends no video. It notes the time, and Stop Recording asks
`record-clip` to cut that span out of the camera's KVS archive, which has footage only while
Start runs. The button was always enabled, so a recording made with the stream stopped ran
its whole length and only then failed with "no footage found for that time range". Live made
this easy to hit: with the picture on screen the camera looks as if it is recording, but Live
pushes to its own stream, `<camera>-live`, which clips never read.

**Fix:** the button is on only while KVS reports footage arriving on the recorded stream:
`/hls` answers 200 (a fragment within the last 30 s) rather than 503. The page checks:
- at sign-in;
- every 30 s while the page is visible;
- on returning to the tab;
- every 5 s for 45 s after Start, counting from Start's moment (`since=`, #66).

After this page's own Stop the button goes off at once, because KVS keeps calling the stream
live for 30 s. A recording in progress always stays stoppable. A Stop pressed somewhere else
can leave the button on for up to a minute; Stop Recording then saves whatever the span holds.

**The rule:** a control that needs footage in the cloud is gated by what KVS reports, not by
what the page last asked for. The producer may have been started from the admin GUI, or may
never have started at all.

### #84 — MediaMTX's duration parser reads `500ms` as 500 minutes

**Found:** 2026-10-28, while writing the first pytest suite (`PYTEST.md`, step 5).
**Referenced from:** `adapter/mediamtx_api.py` (`_dur_secs`),
`tests/adapter/test_mediamtx_api.py` (`test_ff84_milliseconds_are_not_minutes`).

`mediamtx_api._dur_secs()` turns the durations MediaMTX reports into seconds, so the outage
supervisor's reconcile check compares values instead of strings ("2m" comes back as "2m0s").
It read one character at a time: the `m` of `ms` multiplied by 60, and the `s` that followed
added nothing. So `500ms` read as 30 000 s. The same approach had further faults:
- a microsecond or nanosecond value read as 0;
- a number with no unit (`30`) read as 0, where Go refuses it;
- `1.2.3s` raised `ValueError` instead of returning None;
- MediaMTX's own day unit read as unreadable. Its path defaults report
  `"recordDeleteAfter": "1d"`, and the global config reports `hlsPartDuration: "200ms"`,
  which the old parser read as 12 000 s.

**Why it was latent:** only `recordDeleteAfter` and `recordSegmentDuration` are compared.
For an armed path, those are always `0s` (or `""`) and `30s`. An unarmed path is decided by
the `record` flag before either duration is read. Had a sub-second value been compared, the
check would never have matched, and the reconcile loop would have re-patched the path every
5 s. That is the symptom the parsed comparison was written to stop.

**Fix:** parse term by term, as Go's `time.ParseDuration` does. The units are `ns`, `us`/`µs`,
`ms`, `s`, `m`, `h`, plus MediaMTX's `d`, and `ms` is tried before `m`. The terms are summed in
whole nanoseconds, so two spellings of one value (`1500ms`, `1.5s`) compare exactly equal.
Anything else is None.

**Verified** (2026-10-28):
- 52 unit tests pass, 18 of which fail against the old parser.
- Read-only against the live MediaMTX v1.20.1: every duration it reports (`200ms` … `1d`)
  parses correctly.
- `recording_conf_matches` gives the same answer from the old and new parser for every path
  (cam01–cam04, `all_others`), whether off or armed is wanted. So the running supervisor's
  decisions don't change; it loads the fix at its next restart.

**The rule:** parse a duration by unit token, the way the program that wrote it does, and
check it against the values that program actually reports.

### #85 — An unknown IR mode got a 500 from the admin GUI, a 400 from the cloud

**Found:** 2026-10-28, writing the admin GUI's tests (`PYTEST.md`, Phase 3), by comparing the
GUI's routes with the Lambdas they mirror. **Referenced from:** `adapter/onvif-admin/app.py`
(`set_ir`), `tests/admin/test_admin_routes.py` (`test_ff85_an_unknown_ir_mode_is_a_400_as_in_the_cloud`).

`POST /api/cameras/<id>/ir` passes the mode straight to `camera_control.set_ir_mode()`. That
function refuses anything but `AUTO`, `ON` and `OFF` with a `ValueError`, before any camera
call. The route's catch-all then answers **500** with the message, so a bad request is reported
as a server failure. The cloud's `publish-cmd` checks the mode itself and answers **400**
("mode must be 'AUTO', 'ON', or 'OFF'").

**Why it is minor:** both pages send only the three valid modes, and the camera is never
touched; a test pins that too (`test_an_unknown_ir_mode_never_reaches_the_camera`). Only a
direct API call sees the wrong status.

**Fix:** the route checks the mode against `camera_control.ALLOWED_IR_MODES` itself and
answers 400 with `publish-cmd`'s message, at the same point in the same order of checks
(unknown camera, unregistered, no IR control, then the mode).

**Verified** (2026-10-28): the test sends four bad modes (`night`, `auto`, none, a missing
field) to both the GUI route and `publish-cmd`. It requires the same status and body from
each, and that nothing reaches the camera. The four cases fail against the previous `app.py`
and pass with the fix. The running admin GUI (`onvif-admin.service`) loads the fix at its next
restart.

**The rule:** a route that mirrors a Lambda validates its input the same way, and answers a bad
request with a 4xx, never through a catch-all 500.
