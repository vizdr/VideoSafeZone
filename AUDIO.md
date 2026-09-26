# Optional audio recording — what was planned, what was built, what it cost

Per-camera, user-selectable audio alongside video, **off by default**, for both `cam-01`
(AVerMedia PW310 USB webcam) and `cam-02` (ONVIF IPC). Delivered in five phases A0–A4,
all complete and live.

Compiled 2026-09-19. **`Demo-AWS-Video-revCosts4.md` §18 is the canonical reference** —
it was rewritten from this work and carries the operational detail. This document is the
record of *how the design was arrived at*: the rules its two silent bugs left behind
(their stories are `FoundAndFixed.md` #15 and #16) and the claims that had to be withdrawn. `COSTS-1.4.md` §7.3a is authoritative for cost.

**Every row is marked measured or predicted.** Two predictions in this work turned out
wrong in opposite directions (§7), which is why the distinction is kept.

| Status | Meaning |
|---|---|
| **measured** | observed on the live system, number recorded |
| *predicted* | derived from source or arithmetic; not exercised |

---

## 1. Status

| Phase | Scope | State |
|---|---|---|
| **A0** | Verify capability before touching a pipeline | done |
| **A1** | `cam-02` audio end to end, hardcoded | done |
| **A2** | `cam-01` audio end to end, hardcoded | done |
| **A3** | Registry flag + Lambda + route + both GUIs | done |
| **A4** | Docs, cost model, corrections | done |

Live state: `set-camera-audio` Lambda **Active**, `POST /cameras/audio` deployed to
`prod`, both cameras `audioCapable=true` / `audioEnabled=false`.

| | `cam-01` | `cam-02` |
|---|---|---|
| Source | PW310 mic (detected on the same USB device; here `hw:CARD=Webcam,DEV=0`) | camera's own G.711 A-law in RTSP |
| Over RTSP to MediaMTX | **LPCM** 16 kHz mono | G.711 A-law (untouched) |
| AAC encode happens | at the producer | at the producer |
| Delivered | 16 kHz AAC-LC, 32 kb/s | 8 kHz AAC-LC, ~28 kb/s |
| KVS frame rejects | **0** | **0** |
| CPU added | ~+10 pts (publisher +5, producer +7.5) | ~+3 pts |
| Browser-confirmed | yes | yes |

---

## 2. The constraint that shapes everything

**KVS's ingest and playback paths accept different codecs, and ingest is the permissive
one.** Every design decision below follows from this.

`kvssink`'s pad template accepts `audio/mpeg` (AAC), `audio/x-alaw` and `audio/x-mulaw`.
So G.711 goes in without complaint. But `GetHLSStreamingSessionURL` and `GetClip` both
require *"codec private data in the AAC format"*, and reject anything else with
`UnsupportedStreamMediaTypeException` — the documented expectation is codec ID `A_AAC` on
track 2.

G.711 therefore ingests happily, costs `PutMedia`, and fails only when someone tries to
watch it. **Audio is always transcoded to AAC before `kvssink`, on both cameras. There is
no passthrough option even for `cam-02`**, whose audio is already compressed.

This trap fired **three times** in this work, each time in a different disguise:

1. G.711 would ingest and not play (caught by reading the docs first — §3.4)
2. 48 kHz AAC ingested and lost half its frames silently (§4.1, FoundAndFixed.md #15)
3. LATM-wrapped AAC ingested perfectly and then refused to serve (§4.2, FoundAndFixed.md #16)

It is also why `ffprobe` accepting a stream proves nothing about the cloud path, and why
the cloud path ingesting proves nothing about playback.

---

## 3. A0 — findings before any pipeline change

The whole point of A0 was to close unknowns cheaply. Three of the five findings changed
the drafted pipelines before a line was written.

### 3.1 `cam-02` is A-law, not μ-law — and ONVIF cannot tell you

**measured.** MediaMTX reports `{"codec":"G711","codecProps":{"muLaw":false,...}}` and
`ffprobe` says `pcm_alaw, 8000 Hz, mono, 64 kb/s`.

ONVIF's enum is just `"G711"` with no law distinction, so the depayloader choice
(`rtppcmadepay`/`alawdec` vs `rtppcmudepay`/`mulawdec`) **cannot** be derived from ONVIF.
The initial draft used the μ-law elements and would have failed to negotiate.

### 3.2 The PW310 microphone is stereo-only

**measured**, from `arecord --dump-hw-params`:

```
CHANNELS: 2                 # a fixed value, NOT a range
RATE: [8000 48000]
FORMAT: S16_LE S24_3LE
```

`arecord -c 1` fails outright with `Channels count non available`, and so does an
`alsasrc` caps filter asking for mono. Capture stereo, downmix in `audioconvert`.

Signal confirmed live rather than assumed — a dead mic still produces a valid WAV:
peak −8.7 dBFS, RMS −27.9 dB, noise floor −36.5 dB, flat factor 0.00.

### 3.3 Both AAC encoders were already installed

**measured.** `voaacenc` (gst-plugins-bad 1.26.2) and `avenc_aac` (gst-libav 1.26.2) are
both present. Guide §18.2's `sudo apt install -y gstreamer1.0-libav` was unnecessary —
see §7.

`voaacenc` is used: lighter on ARM, and its 1024-sample frame size is the fact the whole
sample-rate decision turns on (§4.1).

### 3.4 `kvssink` has audio pads — and wants `stream-format=raw`

**measured**, from `gst-inspect-1.0 kvssink`:

```
SINK template: 'audio_%u'
  audio/mpeg    mpegversion: {2,4}   stream-format: raw
  audio/x-alaw, audio/x-mulaw
```

Two things fall out: the ingest/playback split in §2, and that the pad accepts **only
`raw`** — not the `adts` `aacparse` produces by default. That caps filter does not
negotiate if omitted, and it is invisible until you try.

### 3.5 `cam-02`'s microphone was clipping, and ONVIF cannot fix it

**measured** on raw PCM, before any encoding:

| | at factory gain | after owner reduced gain in the vendor web UI |
|---|---|---|
| Samples pinned at full scale | **723** | **2** |
| Flat factor | 42.27 | 5.11 |
| Peak | −0.14 dBFS | −5.24 dBFS |
| RMS | −14.8 dB | −28.0 dB |

The giveaway is flat factor and peak count, not peak level alone.

**ONVIF has no gain control.** `AudioSourceConfiguration` carries only `Name`, `UseCount`
and `SourceToken` — the schema has no gain field at all. Same shape as the `ActiveCells`
finding (`Camera-Features.md` §4.1): the camera has the control, ONVIF simply does not
expose it, so the vendor web UI is the only route.

Honest caveat on the comparison: RMS fell 13.2 dB but peak only 5.2 dB. A pure gain
reduction moves both equally, so the room was also quieter at the second measurement. The
clipping verdict is robust regardless — peak-count and flat-factor are what decide it —
but the 13 dB cannot all be attributed to the gain change.

`cam-01` has the opposite problem: RMS −40 dB through the cloud path against `cam-02`'s
−15 dB. Genuine signal, just quiet.

### 3.6 A firmware quirk that breaks capability detection

**measured.** `GetAudioEncoderConfigurations()` returns a configuration the camera is not
using:

| Source | Token | UseCount |
|---|---|---|
| `GetAudioEncoderConfigurations()` | `G711A` | **0** |
| Both profiles (MainStream, SubStream) | `G711` | 2 |

Read the audio config **from the profile**. The same object reports its multicast
`IPv4Address` as `http://192.168.178.67:80/onvif/services`, which is not an IPv4 address —
treat this firmware's ONVIF fields as unreliable generally.

---

## 4. The two bugs that shaped the design

Both were silent, and both passed every check short of the specific one that caught them.
Their full write-ups — symptom, cause from source, measurements — are in
`FoundAndFixed.md`; what they left behind is two design rules.

### 4.1 Sample rate is set by the video frame rate, not by quality (FoundAndFixed.md #15)

`kvssink` synthesises audio DTS from a counter shared with the video track, so
**audio frame duration must exceed the video frame interval**. `voaacenc` emits
1024-sample frames, so at 15 fps the rate must stay below ~15.4 kHz. `cam-02` runs 8 kHz,
`cam-01` 16 kHz (64 ms frames, marginally inside the limit, measured zero rejects). **If a
camera's video frame rate changes, recount the rejects:**

```bash
journalctl -u kvs-cam01.service --since "-60 s" | grep -c 0x30000005   # want 0
```

### 4.2 AAC is encoded at the producer, never sent over RTSP (FoundAndFixed.md #16)

`rtspclientsink` turns AAC into LATM, which KVS ingests and then refuses to serve. So
`publish-cam01.sh` sends **LPCM** into MediaMTX (loopback only) and `stream-cam01.sh`
encodes (`rtpL16depay ! audioconvert ! voaacenc ! aacparse`), so `voaacenc`'s own
`codec_data` reaches `kvssink`. Encoding at the producer also keeps both tracks on one RTSP
timeline — capturing ALSA directly in the producer would make audio lead video by
`rtspsrc`'s ~200 ms latency.

---

## 5. The pipelines as built

Both split RTP pads by `application/x-rtp,media=...` — linking the branches bare lets
audio mislink into the video depayloader. Both feed `kvssink`'s audio pad
`stream-format=raw` (§3.4).

**`cam-02`** — `adapter/bin/stream-cam02.sh`:
```
src. ! application/x-rtp,media=audio ! queue
  ! rtppcmadepay ! alawdec ! audioconvert
  ! audio/x-raw,rate=8000,channels=1
  ! voaacenc bitrate=32000 ! aacparse
  ! audio/mpeg,mpegversion=4,stream-format=raw ! queue ! kvs.audio_0
```

**`cam-01`** — split across two scripts:
- `publish-cam01.sh`: `alsasrc ! audioconvert ! audioresample !
  audio/x-raw,rate=16000,channels=1,format=S16BE` → MediaMTX
- `stream-cam01.sh`: `rtpL16depay ! audioconvert ! voaacenc bitrate=32000 ! aacparse !
  audio/mpeg,mpegversion=4,stream-format=raw` → `kvs.audio_0`

**With audio off, both pipelines send exactly the pre-audio video.** The one addition is
a `fakesink` branch that swallows an audio track the source may still carry — without it
the unlinked pad intermittently killed the producer at startup (FoundAndFixed.md #43).
Turning the setting off is a true revert of the video path, not a second one — which matters,
because `cam-01`'s video chain took the `profile=high` bug to get right (FoundAndFixed.md #13).

Guide §18.2 previously advised bypassing the RTSP hop entirely and going straight to
`kvssink`. That predates MediaMTX becoming the hub; following it now would cost the local
preview, the Start/Stop layer and the single-producer-per-camera model.

---

## 6. A3 — the user control

Registry-driven, following the `recordingMode` pattern exactly: the `cameras` table is the
single source of truth, both GUIs write the same row.

| Attribute | Meaning |
|---|---|
| `audioCapable` | hardware fact, written at registration |
| `audioEnabled` | the user's choice, **default false** |
| `audioDevice` | optional override. Unset, `publish-cam01.sh` uses the microphone `detect-hw.sh` finds on the webcam's own USB device (LAUNCH A9); `cam-01`'s row still carries `hw:CARD=Webcam,DEV=0` from before detection existed, which overrides it |
| `audioCodec` | `PCM` / `PCMA` — picks the depayloader |

`adapter/bin/camera-audio.py` prints shell-sourceable env; the pipeline scripts `eval` it
**once at startup**. Failure is deliberately soft — an unreachable registry prints
`AUDIO=off` and exits 0, so a network blip degrades to video-only rather than leaving a
camera with no producer at all.

### 6.1 Why it applies on the next Start

Not a limitation — a requirement. AWS documents, for `GetClip` *and* HLS:

> Track changes aren't supported. […] An error is returned if the fragments in the stream
> change from having only video to having both audio and video.

Applying this to a running producer would break `GetClip` across the boundary — precisely
the clips the setting exists to improve. Hence read-once-at-launch, and both GUIs say
"from the next Start".

### 6.2 Why both APIs refuse it server-side

`set_camera_audio.py` and the admin route both reject `audioEnabled` on a camera whose
`audioCapable` is false, rather than relying on the UI hiding the control. That is not
defensive padding: **`kvssink` collects across its pads**, so an audio pad that never
delivers stalls the *video* too. A 400 is a far better failure than a stalled stream.

### 6.3 Two controls in the UI, not one

They are different things, and merging them misleads:

| | Record the microphone | Play sound in this browser |
|---|---|---|
| Scope | the stream — everyone, every clip | your speakers only |
| When | next Start | instant |
| Cost | bandwidth + CPU | none |

The listen control stays disabled until hls.js reports an audio track in the manifest it
is **actually playing** — read from `MANIFEST_PARSED`, not inferred from the registry
flag, which may have changed since the producer started. The player is created muted
because browsers block unmuted autoplay; unmuting from the checkbox's own handler is
permitted, because the click is the user gesture.

### 6.4 A CSS bug worth recording

The checkboxes first rendered detached from their labels: `client/index.html`'s global
`input { display: block; width: 100%; … }` rule, written for the login form, matches every
input (FoundAndFixed.md #17). **It still applies to any checkbox or radio added anywhere
else in this client**; scoping it to `#login input` is the open follow-up.

---

## 7. Corrections and withdrawn claims

Five claims in this work were wrong. Recording them is the point.

| Claim | Status | Correction |
|---|---|---|
| Guide §18.3: *"AAC is required; KVS will not accept raw PCM or Opus"* | **refined** | Conclusion right, reasoning incomplete. `kvssink` *does* accept A-law/μ-law **at ingest**; it is the reader APIs that require AAC. Ingest and playback are separate questions. |
| Guide §18.3: *"Audio adds ~64 kbps — negligible against 1.5 Mbps of video"* | **withdrawn** | Both halves wrong. 32 kbps suffices, and "negligible" depends entirely on the stream — see §8. |
| Guide §18.2: `apt install -y gstreamer1.0-libav` | **withdrawn** | Both encoders were already present (§3.3). |
| Draft pipeline: `rtppcmudepay ! mulawdec` for `cam-02` | **withdrawn** | It is A-law (§3.1). Would not have negotiated. |
| Prediction: 16 kHz would be marginal for `cam-01`, ~0.6 rejects/s | **withdrawn** | **measured zero.** The prediction was wrong in the safe direction; 64 ms against a 66.7 ms interval still holds, but the margin is thin — see §4.1. |

One further correction, made in `OUTAGE.md` §3.1 but caused by this work's constraint:
the claim that MediaMTX could not record either camera's audio. fMP4 carries LPCM and
G.711 fine; it was MPEG-TS that could not.

---

## 8. Cost

`COSTS-1.4.md` §7.3a is authoritative. Summary:

Audio is **constant bitrate** — unlike every other term in the cost model, it does not
fall away when the scene is static. So it costs most, proportionally, exactly where video
is cheapest:

| Stream | Video 24/7 est. | +32 kbps | Overhead |
|---|---|---|---|
| `cam-02` sub 640×360 | 0.052 Mbps | 0.084 | **+62 %** |
| `cam-01` 720p, daylight only | 0.241 | 0.273 | +13 % |
| `cam-01` 720p 24/7 | 0.623 | 0.655 | +5 % |
| `cam-02` main 2560×1440 | 1.211 | 1.243 | +3 % |

Absolute cost is unremarkable: 10.4 GB/camera-month, ~$0.10/camera-month at 24/7.

**The `cam-02` sub row matters beyond its own cost.** §6.2 of the cost doc puts the
KVS/S3 crossover at **0.104 Mbps** and that stream sits at 0.052 — comfortably where KVS
wins. Audio moves it to 0.084: still below the crossover, but most of the margin gone.
Audio on a sub-stream is not a rounding error in that argument.

CPU is not free either, and is asymmetric:

| | before | after | delta |
|---|---|---|---|
| `cam-02` producer (was pure passthrough) | ~0 % | 2.5–4 % | +3 pts |
| `cam-01` publisher | ~10 % | ~15 % | +5 pts |
| `cam-01` producer | — | ~7.5 % | +7 pts |

`cam-02`'s producer stops being a pure passthrough the moment audio is enabled — the
property guide §16.3(b) exists to protect.

---

## 9. Known limitations

**The local admin preview never has audio, and that is correct.** It plays MediaMTX's own
HLS, and neither track it receives can enter an HLS manifest — `cam-01` publishes LPCM,
`cam-02` publishes G.711. MediaMTX drops them from the playlist (`CODECS="avc1.640028"`,
video only) rather than producing something broken. **measured**, and a frame decoded to
confirm it was not merely a 200. This is why there is no listen control in the admin GUI:
there would be nothing to listen to.

**`cam-01`'s microphone is quiet** — RMS −40 dB through the cloud path against `cam-02`'s
−15 dB. Real signal, not silence, but the opposite problem from §3.5.

**A `GetClip` window straddling a toggle will fail** (§6.1). `LIVE` HLS self-heals within
a few fragments.

**Two-way audio is out of scope.** `cam-02` exposes audio *outputs* (`AudioMainToken`);
that is a separate feature.

---

## 10. Before enabling this on a real space

Recording the **non-public spoken word** in Germany is **`§201 StGB` — a criminal
offence**, and a stricter regime than the GDPR questions the video already raises.

This is why the default is off, per camera, and opt-in, and why that default is enforced
in the registry rather than only in the UI.

---

## 11. Verification

Every failure mode above was silent at the level of "no pipeline errors". Do not stop
there.

```bash
# 1. tracks reach MediaMTX
curl -s http://127.0.0.1:9997/v3/paths/get/cam01 | python3 -m json.tool | grep -A3 tracks2

# 2. no frames being rejected  (want 0 -- anything above is the shared-DTS trap)
journalctl -u kvs-cam01.service --since "-60 s" | grep -c 0x30000005

# 3. the cloud can actually SERVE it -- the step that catches CPD errors
EP=$(aws kinesisvideo get-data-endpoint --stream-name cam-01 \
       --api-name GET_HLS_STREAMING_SESSION_URL --query DataEndpoint --output text)
URL=$(aws kinesis-video-archived-media get-hls-streaming-session-url \
       --endpoint-url "$EP" --stream-name cam-01 --playback-mode LIVE \
       --query HLSStreamingSessionURL --output text)
ffprobe "$URL"                       # expect BOTH streams

# 4. the audio is real, and none of it was dropped
ffmpeg -i "$URL" -t 20 -c copy -y /tmp/s.mp4
ffmpeg -i /tmp/s.mp4 -vn -af astats=metadata=1 -f null - 2>&1 | grep -E 'RMS|Flat'
#    delivered kb/s should match the configured bitrate; about half means the DTS trap

# 5. finally, a BROWSER -- MSE is stricter than ffmpeg and has caught two
#    regressions here that ffmpeg passed (FoundAndFixed.md #13, #16)
```

Results at delivery: `cam-01` 16 kHz AAC at exactly 32 kb/s with a 45.88 s `GetClip`;
`cam-02` 8 kHz AAC at 27.8 kb/s with a 44.93 s `GetClip`; zero rejects on both; both
confirmed audible in the browser by the owner.
