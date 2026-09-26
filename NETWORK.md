# Networking notes: MediaMTX, ONVIF discovery, VLANs

Companion notes to the build guide, covering the local media-relay layer, camera
discovery, network isolation for the camera segment, and the H.265 option. First written
while planning the MVP (as a companion to `Demo-AWS-Video-MCh-15.md`) — kept separate from
the runbook because it's reference material, not a build sequence.

> **Status, reviewed 2026-09-26 against the built system.** §1 (MediaMTX) and §2
> (discovery) now describe what was built, with the planning-era differences noted.
> §3 (camera-segment isolation) and §4 (H.265 on `cam-02`) are still open design options.
> A bare `§` refers to the build guide, `Demo-AWS-Video-revCosts4.md` (numbering unchanged
> from `MCh-15`) — except "§3 below"/"see §4", which are this file's, and cited sections
> of other files, which name the file. `#N` are entries in `FoundAndFixed.md`.

---

## 1. MediaMTX

### What it is

[MediaMTX](https://github.com/bluenviron/mediamtx) (formerly `rtsp-simple-server`) is a
lightweight, dependency-free media server written in Go. It speaks RTSP, RTMP, HLS,
WebRTC and SRT, but this project only uses one role: **a local RTSP relay running on the
Pi**. It has no encode/decode logic of its own — it accepts a stream pushed to it and
re-serves that same stream to anyone who connects and asks for it.

### Why it's in this architecture

From the build guide's §2.5: keeping MediaMTX in the design (even though the
PW310 USB webcam replaced the original synthetic source) is deliberate. It preserves the
**RTSP boundary** that a real IP camera (Hikvision, etc.) would present natively, so
Phases 3–9 of the build stay untouched regardless of what's actually behind the camera
path. A real ONVIF/RTSP camera speaks RTSP directly; the PW310 doesn't, so MediaMTX
absorbs that difference. Everything downstream only ever talks to
`rtsp://127.0.0.1:8554/camXX`.

This is what made the pass-through-vs-transcode comparison (§16.3b) a one-line change:
point MediaMTX's source at a real camera's RTSP URL instead of a local GStreamer
publisher, and nothing downstream needs to know. It has since become the hub for more
than that: MediaMTX's own HLS (port 8888) feeds the admin GUI's local preview, and its
recorder does durable outage buffering (`OUTAGE.md`, §16.3c).

### How it's wired into the pipeline

Two independent GStreamer processes talk to MediaMTX over loopback:

```
v4l2src (PW310) → v4l2jpegdec → v4l2convert → v4l2h264enc → rtspclientsink ──push──▶ MediaMTX :8554/cam01
                                                                          │
                                                                     (relays)
                                                                          │
kvssink pipeline: rtspsrc rtsp://127.0.0.1:8554/cam01 ◀──pull────────────┘
```

- **Publisher** (`rtspclientsink location=rtsp://127.0.0.1:8554/cam01`, from the
  `gstreamer1.0-rtsp` package — without it cam-01 never appears, #40) — an RTSP client
  that pushes (ANNOUNCEs) the encoded stream into MediaMTX under path `cam01`. Decode,
  convert and encode all run on the Pi's hardware blocks (§16.3b), and the encoder is told
  `profile=high`, since the Baseline it otherwise negotiates renders black in browsers
  (#13). `adapter/bin/publish-cam01.sh` is the current version.
- **Consumer** (`rtspsrc location="rtsp://127.0.0.1:8554/cam01"`) — a separate process,
  the KVS producer pipeline (`kvs-cam01.service`), that pulls the same stream into
  `kvssink`. `cam-02` has no publisher at all: MediaMTX pulls the camera's own RTSP.

Because these are decoupled processes, the cloud-facing pipeline can be restarted (e.g.
while iterating on `kvssink` params) without interrupting capture, and `ffprobe`/`ffplay`
can inspect the stream independently for debugging (§2.6) — MediaMTX serves multiple
simultaneous readers of the same path for free.

### Running it

**As built:** the binary is pinned to v1.20.1 and installed per `LAUNCH.md` A5 (extract
the binary only — the tarball's default `mediamtx.yml` would overwrite the project's,
#26), and it runs as the user unit `kvs-mediamtx` (`LAUNCH.md` A8), not with a trailing
`&`. The planning-era command here pointed at `releases/latest/download/…arm64v8.tar.gz`,
an asset name that no longer exists (§2.5's "Known trap").

Notes that still apply:

- **Ports, as configured in the tracked `mediamtx/mediamtx.yml`:** RTSP `:8554` (all
  interfaces), HLS `:8888` (LAN — the admin GUI's preview), control API `127.0.0.1:9997`
  (the path sync, outage buffer and admin GUI use it). RTMP, WebRTC and SRT are enabled but
  unused. Binding RTSP to `127.0.0.1` would still be a sensible hardening: nothing outside
  the Pi reads it.
- **Camera paths are not in `mediamtx.yml`.** MediaMTX doesn't persist API changes, so
  `adapter/sync_mediamtx_paths.py` (`ExecStartPost=` of `kvs-mediamtx`) re-adds every
  network camera's path from the registry after each start (#31, #32).
- **Startup ordering:** `kvs-camera-publish` is `After=`/`Requires=kvs-mediamtx`. The
  producers are *system* units and cannot depend on a user unit (#12), so they rely on
  `Restart=on-failure` until the RTSP path exists.
- **Stopping it:** `systemctl --user stop kvs-mediamtx` — a `pkill -f mediamtx` (as in the
  guide's §11 `teardown.sh`) just gets it restarted by the unit.
- `"Device or resource busy"` on `v4l2src` is never MediaMTX's fault — it never touches
  V4L2 devices, only RTSP bytes. It means something else (ffplay, guvcview) still holds the
  camera node open.

### Alternatives considered

| Project | Language | Notes |
|---|---|---|
| **gst-rtsp-server** | C (Python via PyGObject/`GstRtspServer`) | Strongest fit for this stack — already 100% GStreamer. Lets the capture pipeline serve RTSP directly (`v4l2src ! ... ! rtph264pay name=pay0`), collapsing the publisher+relay into one process instead of two talking over loopback. Worth prototyping as a simplification. |
| **v4l2rtspserver** | C++ (built on live555) | Purpose-built for V4L2/M2M capture on boards like this. Captures and serves RTSP in one binary. Trade-off: likely less granular control over GOP/caps tuning (`h264_i_frame_period`, `repeat_sequence_header`, the `level=(string)4` caps workaround) than a hand-written `gst-launch` pipeline. |
| **live555** (library) | C++ | The toolkit many IP camera firmwares embed. Low-level — significant code to hand-write vs. `gst-rtsp-server`. |
| **live555ProxyServer** | C++ (live555 example binary) | Ready-made RTSP proxy/relay, but expects an RTSP source upstream already — doesn't solve the "GStreamer pushes in" side as cleanly. |
| **ZLMediaKit** | C++ | Production-grade, RTSP/RTMP/HLS/WebRTC/GB28181, widely used in IP-camera/NVR products. More capable than MediaMTX but heavier to configure/cross-compile than a static Go binary. |
| **SRS** | C++ | Similar scope to ZLMediaKit, more oriented toward RTMP/streaming-platform use cases than a simple local relay. |
| ~~ffserver~~ | C | Removed from ffmpeg since 4.0 — dead, don't use. |
| Python pip packages (`rtsp-server`, etc.) | Python | Generally immature/single-maintainer. Not recommended for anything run unattended. |

**Recommendation:** stay on MediaMTX for the MVP (single static binary, zero-config, and
it cleanly preserves the swappable camera-facing boundary needed for §16.3b). Revisit
`gst-rtsp-server` later as an architectural simplification once the pipeline is stable —
it's a legitimate "evaluated and chose X because Y" talking point.

---

## 2. ONVIF WS-Discovery

**Status: built** (§16.2.1). When this was written, discovery was a roadmap row in the
§16.2 gap table ("hardcoded URL", effort **M**) and every RTSP source was a literal
string. Now:

- `adapter/onvif_discovery.py` does the scan and the ONVIF enrichment, shared by the CLI
  (`adapter/bin/discover-onvif.py`) and the local admin GUI (`adapter/onvif-admin/`,
  `LAUNCH.md` Part E), which turns a scan result into a registered camera in one click.
- Cameras live in the DynamoDB `cameras` registry, not a `channels.json`; each network
  camera's RTSP URL is its `rtspUrl`, re-added to MediaMTX at every start (#32).
- `adapter/rematch_cameras.py` (`kvs-camera-rematch.timer`, every 5 min) follows a camera
  to a new IP by its WS-Discovery endpoint reference (`urn:uuid:…`).

### How WS-Discovery works

WS-Discovery ("Web Services Dynamic Discovery") is a generic W3C/OASIS protocol that
ONVIF adopted for device discovery. It only answers "what ONVIF devices exist on this
LAN and what's their control endpoint?" — it does not hand back an RTSP URL directly.

1. **Multicast probe.** The client sends a UDP multicast SOAP message to
   `239.255.255.250:3702` (the well-known WS-Discovery group/port):
   ```xml
   <Probe><Types>dn:NetworkVideoTransmitter</Types></Probe>
   ```
2. **ProbeMatch.** Every matching ONVIF device on the same L2 broadcast domain replies
   with its **endpoint reference** (a stable `urn:uuid:…` identity — what the rematch
   timer keys on), its **device service address** (an HTTP/SOAP endpoint, e.g.
   `http://192.168.178.67/onvif/device_service` for `cam-02`) plus scope URIs (name,
   hardware, location).
3. **Hello/Bye.** Devices also announce on boot (`Hello`) and clean shutdown (`Bye`), so a
   long-running listener can track appearance/disappearance without polling. (Not used
   here: the rematch timer re-probes every 5 minutes instead, which also catches devices
   whose Hello was missed.)

### From ProbeMatch to an actual RTSP URL

Separate ONVIF SOAP calls ("Profile S"), against the endpoint found above:

1. `GetCapabilities` → returns the camera's **Media service** endpoint.
2. `GetProfiles` → returns configured stream profiles (e.g. main/sub stream).
3. `GetStreamUri` for a chosen profile token → the actual `rtsp://.../Streaming/Channels/102`.

Most cameras require ONVIF auth (WS-UsernameToken: digest over username/password/nonce/
timestamp) on the Media/Device calls, even though Probe/ProbeMatch itself is
unauthenticated — credentials still need to come from somewhere (site config, or a
manual pairing step).

### Where it sits (as built)

Not in the agent, as first planned: in the **local admin GUI** (`adapter/onvif-admin/`),
a LAN-only Flask app on the Pi — because WS-Discovery is multicast and only works from a
process on the cameras' own segment, and because registration also has to write local
state (MediaMTX path, a `kvs-cam@<path>` systemd instance). Registration writes the
`cameras` registry row, which the agent, both GUIs and every Lambda read, so nothing
downstream changes per camera. The periodic re-probe runs as its own timer unit.

Implementation, as predicted here: Python, `WSDiscovery` for Probe/ProbeMatch and
`onvif-zeep-async` for the ONVIF SOAP calls (`LAUNCH.md` A4).

### Caveats

- **Multicast doesn't cross subnets/VLANs** without an IGMP-aware switch and, cross-VLAN,
  a multicast-aware router or reflector. Directly relevant to §3 below: once the camera
  segment is isolated, discovery must run from a device attached to that segment (i.e.
  bind the probe to the Pi's camera-facing interface), not from the trusted-LAN side.
- Conformance varies a lot below "Profile S certified" — some budget/OEM cameras
  implement WS-Discovery inconsistently or not at all. A manual-URL fallback stays
  necessary regardless; discovery is additive.
- This inconsistency is why the gap table rated it **M**, not **S**: the discovery
  handshake itself is roughly a day's work; robust profile/auth handling across
  heterogeneous firmware is where the effort actually goes. `cam-02` bore that out — its
  ONVIF fields are partly unreliable (`Camera-Features.md` §3, §4).

---

## 3. VLANs and network isolation for the camera segment

### The problem

An ONVIF camera is one of the weaker-audited device classes on a home network (default
credentials, unpatched firmware, RTSP/ONVIF services with a spotty security record). This
maps to the "network isolation" gap in the main doc's table (§16.2):

| Capability | Cloud Adapter Mini | This prototype | Effort to close |
|---|---|---|---|
| Network isolation | dual NIC (Enterprise) | single LAN | **S** — second interface + routes |

### What a VLAN is

A VLAN (802.1Q tagging) splits one physical switch fabric into multiple logical broadcast
domains. Without it, every device on the same switch chain — including Wi-Fi clients on
the same router — is one broadcast domain: any broadcast or multicast (including a
WS-Discovery probe) reaches every port.

- **Access port** — untagged, belongs to one VLAN; what an end device plugs into.
- **Trunk port** — carries multiple tagged VLANs over one cable; used between switches or
  to a router that needs to see several VLANs.

Critically: **a switch enforces isolation, but only a router (or an L3-capable switch)
moves traffic *between* VLANs.** A pure VLAN switch with no routing isolates VLAN 10 from
VLAN 20 completely, including from the internet, unless the uplink device is also
VLAN/L3-aware.

### Current setup — no VLAN, one flat network

```
FritzBox 7583 (internet, LAN, WLAN) ── LAN/PoE switch ── ONVIF camera
```

The FritzBox's default subnet (`192.168.178.0/24`) is one broadcast domain. The camera,
any PC, any phone on Wi-Fi are all mutually visible — which is also why a WS-Discovery
probe from anywhere on this network already reaches the camera today with zero extra
configuration.

### FritzBox 7583 limitation

Consumer FritzBox routers **don't support configurable 802.1Q VLAN tagging on the LAN
side.** The LAN switch ports are one flat bridge — no per-port VLAN assignment, no trunk
mode. The only VLAN-ish feature is on the WAN side (some ISPs require a tagged VLAN for
DSL/IPTV uplink), unrelated to this use case. "Guest Wi-Fi" gives wireless clients a
logically separate, internet-only network, but that isolation doesn't extend to wired
ports and can't be routed into from a PoE switch.

**Consequence:** even with a fully VLAN-capable managed switch tagging the camera's port
into its own VLAN, the FritzBox cannot route between that VLAN and the trusted LAN, and
cannot give it internet access either — it doesn't understand VLAN tags on its LAN
interface at all.

### Recommended topology — no VLAN switch needed

Matches the doc's "dual NIC" gap-analysis entry directly: **the adapter itself is the
isolation boundary**, using two physical interfaces, rather than relying on the router to
do VLAN routing it can't do.

Given the Pi 4B's Wi-Fi is currently the uplink to the FritzBox, its **built-in Ethernet
port is free** — no USB-to-Ethernet adapter needed:

```
FritzBox 7583 ──(WLAN, trusted)── Pi wlan0   (AWS: MQTT, KVS — outbound)
                                     │
                                     │  (no IP forwarding between interfaces)
                                     │
                                  Pi eth0 (built-in) ──── PoE switch ──── ONVIF camera
                                  (isolated segment, own subnet, no internet)
```

- **No NAT/forwarding required.** The camera never needs outbound internet access; only
  the Pi's own processes (RTSP/ONVIF client) need to reach it, which happens natively as
  a host on that segment. Leave `net.ipv4.ip_forward` at its Linux default (off) and add
  no forwarding rule between `eth0` and `wlan0` — that absence *is* the isolation. Don't
  build anything; just don't accidentally enable routing later.
- **Addressing.** No FritzBox DHCP exists on this segment. Use a distinct private subnet
  (e.g. `192.168.50.0/24`, vs. the FritzBox's `192.168.178.0/24`) to avoid ambiguity.
  Either statically address both `eth0` (e.g. `192.168.50.1/24`) and the camera via its
  own web UI, or statically address `eth0` and run `dnsmasq` on the Pi to DHCP-serve just
  that interface.
- **A cheap unmanaged PoE switch is sufficient** — isolation comes from physical
  interface separation, not VLAN tagging. 802.1Q tagging only becomes the right tool if
  multiple isolated segments need to share one physical switch (several camera groups
  plus trusted devices on the same box) — not needed at the current one-camera scale.
- **Uplink trade-off, not a blocker:** the AWS-facing link (wlan0 → FritzBox) is Wi-Fi,
  inheriting its jitter/dropout characteristics. The outbound-MQTT design (doc §7)
  already tolerates reconnects, and this incidentally gives more realistic data for the
  §10.2 reconnect-behavior measurements than a synthetic block alone (for which use
  `adapter/bin/awsblock.sh`, not §10.2's IPv4-only snippet, #18).
- **Still the state on the second Pi (checked 2026-09-26):** the uplink is `wlan0` and
  `eth0` has no carrier — the isolated segment is not built yet.
- **WS-Discovery interaction:** once this segment exists, bind the discovery probe to
  `eth0` explicitly (not the default route interface) — that's the interface actually
  attached to the camera's broadcast domain.

---

## 4. Codec choice: H.264 vs H.265, per camera

### Current state

Both channels are pinned to H.264 today, confirmed in the actual code, not just the
runbook:

- `adapter/onvif-admin/app.py` (`register_camera`) creates every KVS stream with
  `MediaType="video/h264"`.
- `adapter/bin/stream-cam01.sh` (PW310, transcoded) and `adapter/bin/stream-cam02.sh`
  (real ONVIF camera, genuine passthrough) both use the identical H.264-specific video
  chain: `rtph264depay ! h264parse ! video/x-h264,... ! kvssink` (plus an optional AAC
  audio branch, §18).

`cam-02`'s camera supports H.265 as an alternate profile, but its registered RTSP URL
(`rtspUrl` in the registry, which MediaMTX is given at every start) deliberately points at
its H.264 sub-stream — most ONVIF cameras expose both so an
integrator can pick whichever the downstream system supports. That's a choice, not a
camera limitation.

### Why cam-01 (PW310) has to stay H.264

Not a policy choice — a hardware fact. The Pi 4B's VideoCore VI exposes exactly one H.264
hardware encode block (`v4l2h264enc`, `/dev/video11`). There is a hardware HEVC block on
the BCM2711, but it's **decode-only** (used for 4K video playback), not encode. So `cam-01`
can never produce H.265 without falling back to software `x265enc`, which is considerably
more expensive than the `x264enc` software fallback already noted as a stopgap in the main
doc's §2.5. `cam-01` is architecturally stuck on H.264.

### Why cam-02 (real ONVIF camera, passthrough) is a different case

Initially considered and rejected on a "keep both channels symmetric for clean
measurements" argument — on reconsideration, that argument doesn't hold once the change
is scoped to `cam-02` only:

- The two channels were never actually coupled. Each camera has its own producer unit,
  its own KVS stream and its own registry row — codec is just another per-camera
  property, not something that needs to match across cameras.
- **Passthrough makes the codec free on the adapter.** `stream-cam02.sh` never decodes
  anything — `rtspsrc ! rtph264depay ! h264parse ! kvssink` is a byte-level RTP relay.
  Swapping to `rtph265depay ! h265parse` with `video/x-h265` caps costs the same
  near-zero CPU. Unlike `cam-01`, there's no encode-cost trade-off standing in the way at
  all.
- KVS's HEVC support (ingestion and `GetHLSStreamingSessionURL`/`GetDASHStreamingSessionURL`
  playback) is mature, not bleeding-edge — the friction isn't on the AWS side of the
  pipe.

This is actually a cleaner experiment than the existing transcode-vs-passthrough
comparison: same passthrough architecture, **codec as the only variable**, no
encoder-load confound. It would directly demonstrate H.265's ~40–50% bitrate/bandwidth
saving over H.264 at equal quality — directly relevant to the main doc's §16.6 conclusion
that uplink bandwidth, not the adapter, is the binding constraint at scale.

### The one real remaining constraint: browser playback

Client-side HEVC decode support in the browser HLS path (`hls.js`/MSE) is inconsistent:
reliable on Safari/iOS, generally unsupported on Chrome/Firefox desktop and on most
Android Chrome builds (a licensing gap, not a technical one). `cam-02`'s stream would
likely fail to render in the same generic browser client that plays `cam-01` fine, unless
viewed from an HEVC-capable browser/device. This is a genuine trade-off worth measuring
directly (test Chrome, Firefox, Safari, an Android phone; record what actually happens)
rather than assuming — in keeping with the main doc's "measure, don't assert" approach to
§10.

### Migration steps, if pursued

1. Point `cam-02` at the camera's H.265 profile — find the URI via ONVIF
   `GetProfiles`/`GetStreamUri` on the HEVC profile token, then Re-register `cam-02` with
   it in the admin GUI (`LAUNCH.md` E3), which updates the registry and the MediaMTX path.
2. `stream-cam02.sh`: swap `rtph264depay ! h264parse` → `rtph265depay ! h265parse`, caps
   to `video/x-h265`.
3. `MediaType` is set at KVS stream creation and can't be changed on an existing stream —
   `cam-02`'s current stream (`stream/cam-02/1788026766462`) would need to be deleted and
   recreated with `MediaType="video/h265"`, which gives it a new ARN suffix. No IAM edit
   follows from that: every KVS-scoped policy uses `stream/cam-*/*` (checked against the
   deployed policies 2026-09-26; the `cloud/iam/` files now match them, FoundAndFixed.md
   #41).
4. Verify the producer SDK build actually has HEVC support compiled in before touching
   AWS — `gst-inspect-1.0 kvssink` and check its accepted caps — same discipline as the
   main doc's §2.6 ("verify before touching AWS").
5. Either accept `cam-02` only plays reliably on HEVC-capable clients, or measure that gap
   explicitly across browsers/devices as its own result.

### Cost implication

`COSTS-1.4.md` §7.4 works the dollar side of this (re-based onto the measured 24/7 bitrate): switching `cam-02` to H.265 cuts KVS
ingest and viewing egress by roughly the same 40–50 % as the bitrate reduction, since KVS
recording cost is linear in bitrate. It also shows the saving is larger on KVS than on S3
(S3's PUT/index costs don't scale with bitrate at all), and prices out — qualitatively,
pending measurement — the two ways to close the browser-HEVC gap above: on-demand
transcode at playback time, or a dual H.265-archive/H.264-live stream pair using the Pi's
hardware HEVC decode block feeding the already-proven `v4l2h264enc` encode path.

---

## Open items

- [x] ~~Write `mediamtx.service` systemd unit~~ — done as the user unit `kvs-mediamtx`
      (`LAUNCH.md` A8), with the registry path sync as `ExecStartPost=`.
- [ ] Decide: keep MediaMTX, or prototype `gst-rtsp-server` as a one-process replacement.
      (MediaMTX now also serves local HLS and records the outage buffer, which raises the
      bar for replacing it.)
- [x] ~~Implement WS-Discovery in `agent.py`~~ — built in the local admin GUI instead,
      plus the rematch timer (§2).
- [ ] Wire up the Pi's built-in `eth0` for the isolated camera segment; retire the
      camera's current path through the FritzBox's flat LAN. (Still open, 2026-09-26.)
- [ ] Decide whether to run `cam-02` on H.265 (see §4) — test the ONVIF HEVC profile URI
      and browser-compatibility matrix before recreating the KVS stream.
