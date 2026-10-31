# Web panel: the cloud client as a Vue app with panels

**Written:** 2026-10-31, against `master` at `84992c2`.
**For:** the sessions that build it. Decisions D1–D8 were settled with the user on 2026-10-31.
Update this file as phases complete; when the work is done, fold the outcome into the guide
(§8, the browser client) the way `OUTAGE.md` folds into §16.3c, and keep this file as the record
of why.

**Progress (2026-10-31):** Phase 0 is built, tested and **deployed** at
`https://dugyd3kkt36pw.cloudfront.net/app/index.html` — `client-app/` (42 Vitest tests),
`deploy.sh app`, the `web` CI job. Its last *Verify* step is the user's: sign in there, and an
action still works after 70 minutes (the token refresh).

---

## 1. Goal

Replace the single-page cloud client (`client/index.html`: one long page, one panel per camera)
with a web app made of panels, modelled on the structure of the Videoloft web app: an icon rail
(Live, Events, Settings, Exit), a camera list, and setting cards per camera. The reference is
Videoloft's **Settings** tab (screenshot shared 2026-10-30): camera tree with search → the
camera's name, thumbnail and main switches → setting cards (tags, recording mode, quality, sound,
schedule). Copy the structure, not the branding: own name, logo and accent colour.

What stays the same: static hosting on S3 + CloudFront, Cognito sign-in, API Gateway + Lambda,
zero inbound ports on the Pi. The admin GUI (`adapter/onvif-admin/`) stays a separate LAN-only
app; nothing moves out of it.

## 2. Ground rules

- **Parity before cutover.** The old page stays live and untouched at `/index.html` until the new
  app does everything it does (checklist in Phase 3).
- **Port the player logic, don't rewrite it.** `load()`, `watchStalls()`, `liveToggle()`,
  `checkArchive()`, `scheduleSessionRefresh()` and the Start window carry the fixes for
  FoundAndFixed.md #43, #61 and #66. Move them into framework-free TypeScript first, comments and
  `#N` references included, and build the components on top.
- **The registry contract (shared with VMS) holds:** new fields only, written field by field;
  no new value in an existing enum; `list-cameras` never returns `rtspUrl` or `liveRtspUrl`;
  every route that changes a camera answers 409 for an unregistered one; every new reader skips
  unregistered rows.
- **Shared AWS account:** never deploy a `pir-*` resource; new resources get names this
  repository owns; IAM policy documents in `cloud/iam/` match what is deployed (#41).
- **Cost:** nothing the new app adds may leave a producer running unattended unless the user
  chose exactly that (the schedule, D6); live tiles play only while on screen.
- **Bugs** go to `FoundAndFixed.md`; a fix in Python logic gets a `test_ffNN_` regression test
  (CLAUDE.md, `PYTEST.md`).

## 3. Decisions (settled 2026-10-31)

**D1. Vue 3 + TypeScript + Vite + OpenVue** (decided as PrimeVue 4; changed to OpenVue on
2026-10-31, below). With Pinia (state), vue-router in hash mode, Vitest + @vue/test-utils; VueUse
(`useDocumentVisibility`, `useIntervalFn`) joins when Phase 1–2 first need it. hls.js comes from
npm, pinned by the lockfile, which ends the CDN-pinning risk of #66. OpenVue in styled mode with
an own dark preset (`src/theme.ts`). Icons: primeicons **7.0.0**.
Considered: React + Mantine (as good on widgets; Vue won on porting today's HTML templates almost
unchanged, a simpler player lifecycle — no effects run twice in development — and Capacitor as
the path to the mobile app of requirements §6); Svelte; Lit or Preact without a build step.

*Why OpenVue, not PrimeVue* (found while starting Phase 0): PrimeVue 5.0 (2026-07-15) and
primeicons 8 moved to the commercial PrimeUI License — a license key, free for individuals and
small organisations but renewed yearly, paid otherwise, shipped compiled. The MIT line stopped at
PrimeVue 4.5.5 (2026-04-08). OpenVue (`openvue`, github.com/openvi-foundation/openvue, MIT) is a
community fork of 4.5.5 that keeps its API: checked, the same 149 components and the same type
definitions for the controls this panel uses. Switching between the two is a package rename in
either direction (`@openvue/migrate` does it), so if OpenVue stalls, the way back to 4.5.5 is
cheap. Its risk is youth — 1.0 on 2026-09-10, two npm maintainers — not compatibility.
*Tool versions:* Node 24 LTS (Node 22 leaves support in April 2027; 26 was three days into LTS);
TypeScript 6.0 — TypeScript 7 is the native compiler, and vue-tsc's support for it is unproven.

**D2. Built beside the old page, cut over at parity.** Source in `client-app/`; published at
`/app/index.html` on the same bucket and distribution until Phase 3, then at the root. Hash routes
(`#/live`, `#/settings/cam-03/camera`) so CloudFront needs no change: a deep path would get a 403
from S3 under OAC. Vite `base: "./"` so one build works both under `/app/` and at the root.

**D3. Live layouts on a 4×4 grid.**

```
2 + 6 (default)          1 + 12                   16
┌─────────┬─────────┐    ┌─────────┬────┬────┐    ┌────┬────┬────┬────┐
│         │         │    │         │ 2  │ 3  │    │ 1  │ 2  │ 3  │ 4  │
│    1    │    2    │    │    1    ├────┼────┤    ├────┼────┼────┼────┤
│         │         │    │         │ 4  │ 5  │    │ 5  │ 6  │ 7  │ 8  │
├────┬────┼────┬────┤    ├────┬────┼────┼────┤    ├────┼────┼────┼────┤
│ 3  │ 4  │ 5  │ 6  │    │ 6  │ 7  │ 8  │ 9  │    │ 9  │ 10 │ 11 │ 12 │
├────┼────┼────┴────┤    ├────┼────┼────┼────┤    ├────┼────┼────┼────┤
│ 7  │ 8  │  empty  │    │ 10 │ 11 │ 12 │ 13 │    │ 13 │ 14 │ 15 │ 16 │
└────┴────┴─────────┘    └────┴────┴────┴────┘    └────┴────┴────┴────┘
```

- **2 + 6 (default), 8 cameras:** two large cells (2×2 each) across the top, six small ones
  underneath; the last two cells of the bottom row stay blank by design.
- **1 + 12:** one large cell (2×2) top left, twelve small ones — 13 tiles.
- **16:** sixteen equal tiles.

Large cells show the cameras picked in Settings → Live view, else the first cameras in list
order. Clicking a small tile swaps it with the camera in the first large cell, for the session.
More cameras than tiles: pages, and tiles on other pages don't play. Fewer cameras than tiles:
the tiles left over say "No camera" (the two blank cells of 2 + 6 are not tiles and show
nothing). 1 + 12 and 16 are as proposed on 2026-10-30.

**D4. Per-user settings are saved with the account, in DynamoDB.** A table `user-settings`, one
item per user, partition key `userId` = the Cognito `sub`, taken from the authorizer's claims and
never from the request body. One Lambda serves `GET` and `PUT /me/settings`. First contents: the
Live layout and the large-cell cameras. **Agreed by the user for the current state of the
project.**
PostgreSQL was allowed ("if necessary") and isn't needed: one item fetched by key is what
DynamoDB is for, the account already runs on it (`cameras`, `clips`), it bills per request
(≈ $0 at this size) and needs no VPC. An RDS instance bills by the hour even when idle (roughly
$15 a month for the smallest before storage — list price, not part of `COSTS-1.4.md`), and a
Lambda reaching it needs VPC networking or the Data API. **Revisit with multi-tenancy**
(requirements §3: customers, sites, roles admin/dealer/customer), where relational queries could
earn a database server.
Also considered: Cognito custom attributes (no backend at all) — rejected for the 2048-character
limit and because adding one is a schema change to the user pool that can't be undone.

**D5. Recorded quality: the admin GUI sets the ceiling; the cloud page may only go lower, by one
step — main stream → sub stream.** The admin GUI's choice — recorded stream main/sub and 10/15 fps
(guide §21, `VMS-PORT-PLAN.md` Phase 5) — is the maximum, and **the frame rate is set only in the
admin GUI**. The cloud page offers the ceiling and, when the camera records its main stream and
has a sub stream (`liveRtspUrl` present), the sub stream.

- **How the step works — on the Pi only** (Phase 5). The producer pulls the camera's `_live`
  path — the sub stream MediaMTX already pulls continuously for live view — instead of the
  recorded path. Nothing on the camera changes; `rtspUrl` (shared with VMS: "the stream that is
  recorded") is untouched; the local preview and the outage buffer keep the full quality. The
  producer reads the choice once at startup, like `audioEnabled`, so it **applies on the next
  Start**. When `_live` is missing at start, the producer uses the recorded path.
- **Why the frame rate isn't offered** (user, 2026-10-31): passthrough video can't drop frames
  without re-encoding, and the Pi's hardware encoder stops at 1080p, so a lower rate would have
  to be set on the camera's encoder — changing it for everything, including the local preview,
  the outage buffer and VMS's recording when that card is in. It stays a camera setting, in the
  admin GUI.

The USB camera (cam-01) is encoded on the Pi at a fixed 720p15 and offers no lower step; neither
does a camera that already records its sub stream.
Ceiling rules: the Lambda refuses a choice above the ceiling; the producer checks again at start;
a change in the admin GUI sets a new ceiling and clears a cloud choice that is no longer below it.

**D6. The camera schedule runs on the Pi.** A user unit `kvs-schedule.timer` (every minute) runs
`adapter/schedule_runner.py`, which reads schedules from the outage supervisor's registry cache
(`outage_buffer.read_registry_cache()`, so it also works while AWS is unreachable) and skips
unregistered cameras. Not EventBridge Scheduler: that adds resources to the shared account and
loses a command when the Pi is offline at that minute.
**Edge-triggered:** an entry fires once, when its time passes; its last run is kept in a state
file on the Pi. After a reboot, entries missed by up to 10 minutes still fire; older ones are
skipped and logged. So a manual Stop at 20:00 inside a scheduled window stays stopped — a
level-triggered "should be recording now" would restart it a minute later. Times are wall-clock
in a timezone stored with the schedule (IANA name, `zoneinfo`, safe across DST). Actions: start
recording, stop recording, set recording mode.

**D7. New per-camera settings: registry fields and one Lambda.** `set-camera-settings` behind
`POST /cameras/settings`, body `{cameraId, tags?, schedule?, recordQuality?}`: each field
validated and written with its own `SET`. The existing per-setting Lambdas (`set-camera-mode`,
`set-camera-audio`, `set-camera-outage-buffer`) stay as they are.

**D8. Left out.** From the Videoloft screen: Audit logs, two-way audio, codec selection, live
video quality, HDMI output, PTZ (Phase 2 in the requirements), Stills, Analytics add-ons, Smart
motion alerts. The frame rate stays in the admin GUI only (D5). "Camera On" waits until the cloud can read an online/offline status (Dashboard,
later). The codec stays visible, read-only, where it decides whether this browser can play the
camera (the H.265 notes).

## 4. Screens

### 4.1 Shell

- **Icon rail:** Live, Events, Settings; Exit at the bottom (signs out: drops the tokens, sends
  `live-stop` for every live tile).
- **Sign-in:** as today — Cognito `InitiateAuth` with `USER_PASSWORD_AUTH` over plain `fetch` —
  plus **token refresh** (`REFRESH_TOKEN_AUTH`) before the ID token expires. The current page has
  none, so after about an hour every API call returns 401. ID and access tokens in memory; the
  refresh token in `sessionStorage` (survives a reload, gone when the tab closes).
- **Camera list** (Live and Settings): from `GET /cameras`; search on name, ID and tag; grouped by
  first tag, untagged last; a status dot from Phase 1, when the archive check gives it something
  to show. The "Preview" switch shows thumbnails once Phase 5
  provides them.

### 4.2 Settings

```
┌──────────┬─────────────────────┬──────────────────────┬──────────────────────────────────────┐
│ Live     │ Cameras (4)       < │ Settings             │ Tags  [outdoor x] [gate x] [+ tag]   │
│          │ [Search...        ] │ Front gate - cam-03  ├──────────────────────────────────────┤
│ Events   │ Preview       [off] │ +------------------+ │ Recorded quality   [Main stream  v]  │
│          │ Live view (yours)   │ |                  | │   at most Main 15 fps (admin GUI)    │
│ Settings │ v outdoor           │ |    thumbnail     | ├──────────────────────────────────────┤
│          │   * Front gate    < │ |                  | │ Sound: microphone, next Start [off]  │
│          │   * Yard            │ +------------------+ ├──────────────────────────────────────┤
│          │ v indoor            │ Cloud recording [on ]│ IR illumination [Auto | Day | Night] │
│          │   * USB desk cam    │                      │                                      │
│          │                     │ [ Camera         > ] │                                      │
│ Exit     │                     │ [ Recording      > ] │                                      │
└──────────┴─────────────────────┴──────────────────────┴──────────────────────────────────────┘
```

The middle column holds the camera's name and ID, its thumbnail (a placeholder until Phase 5),
the **Cloud recording** switch (Start/Stop), and the section links **Camera** and **Recording**.
**Live view (yours)** above the camera list opens the per-user settings (D4) — they belong to the
account, not to a camera.

| Section | Card | Data | Saved by | Phase |
|---|---|---|---|---|
| (middle) | Cloud recording | archive check (`GET /hls?view=recorded`) | `POST /cmd` start/stop | 1 |
| Camera | Tags | `tags` (new) | `POST /cameras/settings` | 4 |
| Camera | Recorded quality | ceiling `recordStream`/`recordFps`/`recordSize`, choice `recordQuality` (new) | `POST /cameras/settings` | 4 shown, 5 applied |
| Camera | Sound — record microphone, applies on next Start | `audioEnabled`, `audioCapable` | `POST /cameras/audio` | 1 |
| Camera | IR illumination Auto / Day / Night | `hasIrControl` | `POST /cmd` `ir` | 1 |
| Recording | Recording mode, with the trigger note | `recordingMode`, `detectionModes`, `detectionTests` | `POST /cameras/mode` | 1 |
| Recording | Camera schedule | `schedule` (new) | `POST /cameras/settings` | 4 stored, 5 runs |
| Recording | If the internet drops | `outageBufferSec` | `POST /cameras/outage-buffer` | 1 |
| Live view (yours) | Layout; large-cell cameras | `user-settings` | `PUT /me/settings` | 2 |

Every control saves on change and shows "saving… / saved / error" beside it. Saving is driven by
user events only (`@update:modelValue`), never by a `watch` on loaded data — that would send
every setting back to the API on every sign-in.

**Schedule card**, like Videoloft's: days (multi-select), time, action, Add; a week view with one
bar per day showing the recording windows; the entries, each with Delete. It shows the
**recording hours per week** and warns when a Start has no Stop after it within 24 h — a
scheduled Start is unattended `PutMedia`, the guide's §1.2 cost rule.

### 4.3 Live

- Layout from the user settings (D3, D4); a switcher in the toolbar writes the same setting.
- A tile plays the camera's live view (the `live` command, KVS stream `<camera>-live`) only while
  it is on screen and the tab is visible, with today's keep-alive (re-sent every 2 min) and
  `live-stop` on leaving, per tile. Leaving the Live tab stops every tile: no `<KeepAlive>` on
  this view, or the Pi keeps uploading until its 5-minute deadline.
- The large tile that has focus carries today's panel controls: Live / End live, Start / Stop
  (cloud recording), Record clip, Play sound here, Reload player.
- An H.265 tile in a browser without HEVC shows the existing note instead of a black tile.

### 4.4 Events

Today's clip list — per camera, paged, storage tier, delete, play in a dialog, the "new clips"
notice — with a camera filter. A merged list across all cameras needs a `list-clips` change:
later.

## 5. Data and API

### 5.1 Registry fields (all new, this project only; VMS ignores them)

| Field | Written by | Meaning |
|---|---|---|
| `tags` | `set-camera-settings` | list of strings: at most 10, each at most 24 characters of `[A-Za-z0-9 _-]` |
| `schedule` | `set-camera-settings` | `{tz, entries: [{days: ["mon", …], at: "HH:MM", action: "start" \| "stop" \| "mode:<recordingMode>"}]}`, at most 50 entries |
| `recordStream`, `recordFps`, `recordSize` | admin GUI: at registration, at a stream/fps switch, and when it reads the camera's streams | the ceiling (D5) |
| `recordQuality` | `set-camera-settings` | the cloud's choice below the ceiling: `{"stream": "sub"}`, the only lower step (D5); absent = the ceiling |
| `recordQualityActive` | producer, at start | what it actually pulled, for display |

`list-cameras` returns all of these plus `recordQualityOptions` (the steps at or below the
ceiling, computed), and still never a credential.

A stream switch made in VMS's admin GUI doesn't write the ceiling fields, so after a card swap
they can be stale until this admin GUI next reads the camera — the same pattern as the codec
learnt on read (#69). The producer's fallback (D5) keeps that harmless.

### 5.2 Per-user settings

Table `user-settings`, partition key `userId` (string, the Cognito `sub`), on-demand billing.
Item: `{userId, liveLayout: "2+6" | "1+12" | "16", largeCells: ["cam-03", "cam-01"], updatedAt}`.
Lambda `user-settings` (one function, `GET` and `PUT`): a whitelist of keys, written field by
field so a later key isn't overwritten by an older page; a 4 KB size cap; camera IDs that no
longer exist are dropped when read. IAM: `GetItem`, `UpdateItem` on that table only
(`cloud/iam/user-settings-policy.json`).

### 5.3 Routes

API Gateway `kvs-demo-api`, Cognito authorizer, CORS headers on every response including errors
(#8).

| Route | Lambda | Phase |
|---|---|---|
| `GET /me/settings`, `PUT /me/settings` | `user-settings` (new) | 2 |
| `POST /cameras/settings` | `set-camera-settings` (new) | 4 |
| all existing routes | unchanged | — |

Each new route also needs its `OPTIONS` preflight and a stage deployment. `deploy.sh` deploys code
only, so the routes are created with the CLI as in guide §8.3, and the commands are recorded in
the guide.

## 6. Phases

### Phase 0: Scaffold and shell

0.1 `client-app/`: Vite (Vue + TypeScript), OpenVue, Pinia, vue-router (hash), Vitest;
scripts `typecheck`, `test`, `build`. Node 24 LTS is needed only to build the page, so it goes
in LAUNCH Part H, not in `setup/` — the Pi's runtime doesn't need it.
0.2 Framework-free modules: `api/` (fetch with the token, JSON errors), `auth/cognito.ts`
(sign-in, refresh), `player/` (the ported logic, §2).
0.3 Shell: sign-in, rail, camera list with search, empty Live / Events / Settings views, own dark
preset.
0.4 `deploy.sh app`: build; back up the live `/app/` prefix (the bucket keeps no versions);
upload — hashed assets `immutable`, `index.html` with the cache header of #11; verify through
CloudFront. It refuses to touch `/index.html` until Phase 3.
0.5 CI: a GitHub Actions job — `npm ci`, typecheck, Vitest, build.

*Verify:* sign in at `https://dugyd3kkt36pw.cloudfront.net/app/index.html`; the camera list
matches the old page's; leave the page open 70 minutes, then an action still works (refresh).

*Done 2026-10-31, before the deploy:*
- **0.1** Node 24.22.2 in `~/.local` (checksum-verified). Exact pins in `package.json`:
  vue 3.5.44, vue-router 5.4.0, pinia 4.1.0, openvue + @openvue/themes 1.1.0, primeicons 7.0.0,
  hls.js 1.7.3 (the old page's version, so the port is compared like for like), vite 8.3.5,
  TypeScript 6.0.3, vue-tsc 3.3.14, vitest 5.0.5. `npm audit`: no vulnerabilities.
- **0.2** `CameraController` holds the old page's `load()`, `watchStalls()`, `liveToggle()`,
  `cmd()`, `checkArchive()` and session refresh, behaviour unchanged, comments and `#N`
  references kept. Additions only: a guard for a player detached while a request was out, and
  `dispose()` (ends Live, clears every timer) for components that go away. 18 tests pin the
  behaviour (#43, #61, #66, #83); breaking the stop grace or the playlist-error rule on purpose
  turns them red. Cognito: the token is renewed two minutes before it expires, on the call that
  needs it, and a 401 renews and retries once.
- **0.3** Rendered in headless Chromium at 1400 px and phone width (the rail becomes a top bar).
- **0.4** `app --dry-run` built fresh from the lockfile (~20 s) and found `/app/` empty (a first
  deploy); the upload order and headers were checked with a recording stub. Two builds of the
  same tree are byte-identical, which the "already live" check relies on.
- **0.5** `.github/workflows/web.yml`, on changes under `client-app/`.

*Deployed 2026-10-31* (`bash cloud/deploy.sh app`, 50 s; `/app/` was empty in the bucket): 9
files, verified through CloudFront by the script. Checked after: `index.html` and `version.json`
`no-cache, must-revalidate` (`x-cache: RefreshHit`), hashed assets `immutable`, every content
type right (fonts included); the hashes equal a local build in another directory, so "already
live" works; the old page untouched (live = this tree). The Cognito app client allows
`ALLOW_REFRESH_TOKEN_AUTH`, has no secret (none is sent), and keeps the default 60-minute ID
token and a 30-day refresh token. The bare `/app/` answers 403 — S3 has no directory index under
OAC — so the address is `/app/index.html`. Headless Chromium in Claude's sandbox can't load
network pages; the same build rendered from `file://`.

### Phase 1: Settings on today's API

Cards: Cloud recording, Recording mode (with the trigger note), Sound, IR, If the internet drops;
the codec read-only where it matters. The same requests as the old page.

*Verify:* each change made on the new page shows on the old page after a reload, and the other
way round; an unregistered camera's 409 is shown; a detection mode the camera doesn't publish is
greyed out.

### Phase 2: Live grid, Events, per-user settings

2.1 Backend: `user-settings` table, Lambda, routes, IAM policy; pytest through `invoke()` (CORS,
user ID from the claims only, whitelist, size cap).
2.2 Layout geometry as a pure function (`layouts/grid.ts`: tiles with row, column and spans);
Vitest: no overlaps, every tile inside the 4×4 grid, 8 / 13 / 16 tiles, and only 2 + 6's two
bottom-right cells left blank.
2.3 Live per §4.3; Settings → Live view (yours): the layout picker (three mini-grid buttons) and
the large-cell camera pickers.
2.4 Events per §4.4.

*Verify:* the layout follows the account to a second browser; leaving the Live tab stops every
`kvs-live@` unit within the deadline (journal: deadline exit, `inactive`, result `success`); Pi
CPU and memory measured with every camera live at once (§7) before 16 tiles are called usable.

### Phase 3: Cutover

Parity checklist against the old page:

- [ ] Live / End live; live keep-alive; `live-stop` on leaving the page
- [ ] Start / Stop; the Start window and "no video arrived within 45 s"
- [ ] Stall reload and "video arrives but doesn't play"; session refresh about hourly; Reload player
- [ ] Record clip; Play sound here
- [ ] IR; recording mode; sound; outage buffer
- [ ] Clips: paging, tier, delete, play; the new-clips notice
- [ ] H.265 notes (page and per camera)

Then `deploy.sh page` publishes the app at the root (the deploy backs up the old page first);
`client/index.html` leaves the tree (git history keeps it); CLAUDE.md (the "no build system"
line, "Two GUIs", deploying the browser client), README, LAUNCH Part H and guide §8 are updated.

### Phase 4: Camera settings backend

`set-camera-settings` Lambda, route and IAM policy: validators per §5.1, 409 for an unregistered
camera, `unregistered_response` copied identically (`tests/cloud/test_lambda_twins.py` checks).
`list-cameras` returns the new fields; the admin GUI writes the ceiling fields. Settings: Tags,
Recorded quality (ceiling shown, choice saved), Schedule (stored, not yet run — the card says
so).

*Verify:* pytest; tags and schedule round-trip; a choice above the ceiling → 400; lowering the
ceiling in the admin GUI clears the cloud choice.

### Phase 5: The Pi side

5.1 Schedule runner (D6): `adapter/schedule_runner.py`, its unit in `setup/setup-user.sh`; tests
in `tests/adapter` with the `fake_run` and `registry` fixtures: fires once per entry, the 10-minute
catch-up, a DST day, a manual Stop respected, unregistered cameras skipped.
5.2 Stream step (D5): the producer reads `recordQuality` at start, pulls `_live` when asked and
present, writes `recordQualityActive`; `stream-codec.py` is asked about the path actually pulled.
The sub stream can be H.265 under an H.264 main, so the page's codec warning uses the active
codec.
5.3 Thumbnails: every 10 minutes, and when Settings opens a camera, the Pi grabs one frame from
each flowing path (the sub stream when there is one) and uploads it to the evidence bucket as
`thumbs/<cameraId>.jpg`; `list-cameras` returns a presigned URL. IAM: `s3:PutObject` on that
prefix in the role-alias policy (exported back, #41); `s3:GetObject` for `list-cameras`.

*Verify:* a schedule entry two minutes ahead starts and then stops the producer (journal and KVS
fragments); a reboot across an entry fires it within the catch-up window; with "sub" chosen, the
next Start pushes the sub resolution (ffprobe the HLS URL, decode a frame and look at it);
thumbnails appear and refresh.

### Later

Playback with a timeline (archive with a scroll bar, requirements §5); Dashboard (online/offline,
which also enables "Camera On"); Alerts; PTZ (requirements Phase 2); the mobile app with
Capacitor; multi-tenancy (revisit D4).

## 7. To measure before promising

- Pi 4 load with several `kvs-live@` producers at once (CPU, memory, uplink): the system has four
  cameras today, and 10–16 live tiles have never been measured.
- Browser decoding of 10–16 hls.js players at once, H.264 and H.265, in Chrome, Firefox and Safari.
- Whether `GetClip` and HLS span the resolution change a stream step makes at a session boundary.
  If not, the clip Lambdas split windows at a `recordQualityActiveSince`, as they already do at
  `videoCodecActiveSince`.
- Viewing cost of a full grid: the cost per GB watched (`COSTS-1.4.md` §5.2) times the tiles
  playing.

## 8. Session checklist

- [x] Phase 0: scaffold, ported player logic, shell, `deploy.sh app`, CI (built and tested locally)
- [x] Phase 0: first deploy to `/app/` (2026-10-31)
- [ ] Phase 0: sign-in and the 70-minute refresh checked at `/app/index.html` (the user)
- [ ] Phase 1: Settings on today's API
- [ ] Phase 2: `user-settings` backend, layouts, Live, Events
- [ ] Phase 3: parity checklist, cutover, docs
- [ ] Phase 4: `set-camera-settings`, new registry fields, admin GUI ceiling
- [ ] Phase 5: schedule runner, stream step, thumbnails
- [ ] Measurements of §7 recorded here
