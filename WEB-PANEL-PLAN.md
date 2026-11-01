# Web panel: the cloud client as a Vue app with panels

**Written:** 2026-10-31, against `master` at `84992c2`.
**For:** the sessions that build it. Decisions D1–D8 were settled with the user on 2026-10-31.
Update this file as phases complete; when the work is done, fold the outcome into the guide
(§8, the browser client) the way `OUTAGE.md` folds into §16.3c, and keep this file as the record
of why.

**Progress (2026-10-31):** Phase 0 is **deployed** at
`https://dugyd3kkt36pw.cloudfront.net/app/index.html` (commit `029c0bc`, both CI jobs green); its
last *Verify* step is the user's: sign in there, and an action still works after 70 minutes.
**Phase 1** (Settings on today's API) is **deployed** too (commit `32d6c78`, both CI jobs green,
deployed by the user with `bash cloud/deploy.sh app`); its *Verify* steps are the user's, signed in
at `/app/index.html`.
**Phase 2** (Live grid, Events, per-user settings) is committed (`125a802`, both CI jobs green) and
deployed; its backend was created by the user with `bash cloud/provision.sh user-settings`
(2026-11-01: `/me/settings` answers 401 without a token, and its CORS preflight allows GET, PUT).
**Phase 3 — the cutover — is done (2026-11-01):** the user checked Phases 0–2 at `/app/` and ran
`bash cloud/deploy.sh page`; **the site's root serves the web panel**, the old page is in the deploy
backup (byte-identical to the committed file) and `client/index.html` has left the tree.
**Phase 4** (camera settings: tags, recorded quality, schedule) is **deployed** (2026-11-01, by the
user): `set-camera-settings` created by `provision.sh camera-settings` (`POST /cameras/settings`
answers 401 without a token), `list-cameras` deployed, the admin GUI restarted, `deploy.sh page`
done; `deploy.sh status` shows every function as this tree. Not committed yet.
**Phase 5** (the Pi side: schedule runner, sub-stream step, thumbnails) is built and tested locally
(2026-11-01) — not committed, not installed or deployed: it needs the two new timers on the Pi,
`bash cloud/provision.sh thumbnails`, `bash cloud/deploy.sh lambda list-cameras` and
`deploy.sh page`.

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

*Built 2026-10-31* (62 Vitest tests, 880 pytest):
- **Settings screen** (§4.2): the middle column holds name, ID, a preview placeholder (hidden when
  the layout stacks), **Cloud recording** and the links **Camera** (Video, Record microphone, IR)
  and **Recording** (Recording mode with the trigger note, If the internet drops). Settings always
  shows a camera: the first until another is picked. Every control saves on the viewer's change
  only (`useSavedSetting`), is disabled while its save is out, and puts the old value back when
  the server refuses — now Recording mode too, which the old page left showing. Wording and value
  lists ported in `src/cameras/settings.ts`.
- **Cloud recording** is Start/Stop as a switch showing KVS's answer (the archive check, #83), not
  the last click. The controller gained `archivePending` (a Start waiting for its first footage)
  and `archiveFailed` (its 45 s window closed with none — the Pi didn't answer, #61), and
  `command()` now says whether the API accepted it. One controller per camera lives in
  `stores/players.ts`, running the archive check from sign-in as the old page did; it also feeds
  the camera list's **status dot** (green = footage arriving, amber = starting).
- **H.265:** the page-level note (`HevcNote.vue`) and the per-camera warnings in the Video card.
- **Cross-checks:** `tests/test_value_lists.py` (pytest) reads the outage durations, recording
  modes and IR modes out of every copy — Lambdas, adapter, admin GUI route and page, old page, web
  panel — and fails when one drifts (checked by breaking the web list on purpose).
- **hls.js is loaded with the first player**, not with the page: the first load is 124 kB
  compressed instead of 302 kB.
- **Theme:** the dark switch track is surface-950 — Aura's surface-700 was the cards' own colour,
  so an "off" switch vanished into its card. Rendered offline (fake Cognito and API injected) at
  1400 px and phone width.
- **Left for Phase 2:** the "new clips" polling after a detection mode is chosen (it belongs to
  Events); "Play sound here" (the Live player).

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

*Built 2026-10-31* (103 Vitest tests, 899 pytest):
- **2.1** `cloud/lambda/user_settings.py` (GET/PUT, user = the token's `sub`, a whitelist of
  `liveLayout`/`largeCells`, field-by-field writes, 4 kB cap; 17 pytest through `invoke()`, which
  now also passes `httpMethod` and the authorizer's claims) and `cloud/iam/user-settings-policy.json`.
  **Created by `cloud/provision.sh user-settings`**, not by hand as §5.3 first said: it makes the
  table, role, function, `/me/settings` with GET, PUT and the CORS preflight, deploys the stage and
  verifies through API Gateway; it only creates what is missing and never changes what exists
  (`--dry-run` lists what it would create). `deploy.sh lambdas` skips a function not created yet.
  Picks for cameras that are gone are dropped by the page, so the Lambda needs no registry access.
- **2.2** `src/layouts/grid.ts`: layouts as data, `cameraOrder` (large cell i shows pick i; "" or
  a gone camera = the next one), `pageOf`, `bringToLarge`. The tests found that `Object.keys` put
  "16" first (integer-like keys come first in JavaScript), so the order is spelled out.
- **2.3** Live: `LiveTile` plays its camera while mounted and sends `live-stop` when it goes (page,
  layout, tab); a live view this browser can't decode (H.265) isn't started. The selected tile has
  Live / End live, Start / Stop, Record clip (ported into the controller, #83), Play sound here,
  Reload and full screen. A click on a small tile swaps it into the first large cell for the visit;
  a camera picked in the list opens its page. The controller gained `startLive`/`stopLive` and a
  guard: a Live ended or restarted while its `live` was on the way left a second keep-alive timer
  running in the old code — easy to trigger in a grid (tests fail without the guard). Settings →
  Live view (yours): layout picker and large-cell pickers; the same picker in the Live toolbar.
  Before `/me/settings` exists the page shows the default and keeps a change for the visit.
- **2.4** Events: the old clip list per camera (paging, tier, delete with confirmation, play in a
  dialog, Deep Archive restore messages, the "new clips" notice while a detection mode is set).
- **Cross-checks:** `tests/test_value_lists.py` now also compares the layouts with
  `user_settings.py` and the storage tiers with `set_clip_tier.py`.
- Rendered offline (fake API) at 1400 px and phone width. Not measured yet: Pi load with every
  camera live (§7) — that needs the deploy and real cameras.

### Phase 3: Cutover

Parity checklist against the old page — audited 2026-11-01: every function of `client/index.html`
has its place in the app, and each item below is pinned by tests. "Live" is the user's check of
the same thing on the deployed `/app/`.

| Old page | Web panel | Tested by | Live |
|---|---|---|---|
| Live / End live; keep-alive every 2 min; `live-stop` on leaving | `CameraController.toggleLive`, `LiveTile` (also on leaving the tab), `stores/players` (pagehide) | `camera-controller.spec` (Live), `live.spec` | [x] |
| Start / Stop; Start window; "no video arrived within 45 s" (#43, #61, #66) | `command`, `load`; Settings → Cloud recording; tile control | `camera-controller.spec` (Start), `settings.spec` | [x] |
| Stall reload; "arrives but doesn't play"; session refresh; Reload player (#66) | `watchStalls`, `scheduleSessionRefresh`, `reload` | `camera-controller.spec` | [x] |
| Record clip (#83); Play sound here | `startClip`/`stopClip`; `setListen`; tile controls | `camera-controller.spec`, `live.spec` | [x] |
| IR; recording mode (#65); sound; outage buffer | Settings → Camera / Recording | `settings.spec`, `test_value_lists.py` | [x] |
| Clips: paging, tier, delete, play, restore messages, new-clips notice | Events (`ClipList`, `ClipPlayer`) | `events.spec`, `test_value_lists.py` | [x] |
| H.265 notes, page and per camera | `HevcNote`, the Video card, the tile message, the clip line | `settings.spec`, `live.spec` | [x] |

*Live* ticked on the user's word (2026-11-01: "points 1, 2, 3 of the cutover done").

Different by design (D3): the Live grid plays every camera on the page, where the old page played
one camera per press; Start/Stop also sits in Settings as a switch.

*Built for the cutover (2026-11-01):* `deploy.sh page` now publishes the web panel at the root —
the same fresh build as `app`, which stays as a preview at `/app/`. At the root it recognises the
old page by looking the live file up among the committed versions of `client/index.html`
(`git log --find-object`), so the first `page` deploy isn't refused as someone else's; it backs up
only the root's `index.html` (and `version.json`, when there is one), never the whole bucket, and
`rollback page` puts them back — the old page included. Rehearsed with a recording stand-in for the
AWS CLI against the real CloudFront: the dry run names the old page (committed in `df70e8f`);
assets go up first and immutable, `version.json` and `index.html` last with `no-cache`; the backup
is byte-identical to the committed page; `rollback page` restores it and removes the root's
`version.json`. `status` shows what each location serves. LAUNCH Part H and CLAUDE.md describe it.

The cutover, in order:
1. The user checks Phases 0–2 at `/app/index.html` (the *Live* column above), after
   `bash cloud/provision.sh user-settings` so the layout is saved with the account.
2. `bash cloud/deploy.sh page --dry-run`, then `bash cloud/deploy.sh page` — the root serves the
   web panel; the old page is in the backup (`rollback page` brings it back).
3. Then `client/index.html` leaves the tree (git history keeps it), with what still reads it:
   `tests/test_value_lists.py`'s old-page copies, `adapter/onvif-admin/app.py`'s comment; and the
   docs are updated: CLAUDE.md ("Two GUIs", the "no build system" line), README, START.md, guide §8.

*Done 2026-11-01.* Checked after the user's steps: `/me/settings` answers 401 without a token
(the authorizer) and its preflight allows GET, PUT, OPTIONS; the root's `version.json` and
`index.html` are the web panel, its assets load; the deploy backup `20261101T091604Z/index.html`
is byte-identical to the last committed `client/index.html`; `page --dry-run` and `app --dry-run`
both say "already live, unchanged". Then `client/index.html` was removed (`git rm`);
`tests/test_value_lists.py` now compares the admin GUI's mode list instead of the old page's;
CLAUDE.md, README, START.md, PYTEST.md, LAUNCH Part H, `client-app/README.md` and the guide (§8.7,
new; §12; §16) describe the web panel. Historical records that cite the old file (FoundAndFixed,
OUTAGE.md, AUDIO.md, the guide's §8.4–8.6) are left as they were written.

### Phase 4: Camera settings backend

`set-camera-settings` Lambda, route and IAM policy: validators per §5.1, 409 for an unregistered
camera, `unregistered_response` copied identically (`tests/cloud/test_lambda_twins.py` checks).
`list-cameras` returns the new fields; the admin GUI writes the ceiling fields. Settings: Tags,
Recorded quality (ceiling shown, choice saved), Schedule (stored, not yet run — the card says
so).

*Verify:* pytest; tags and schedule round-trip; a choice above the ceiling → 400; lowering the
ceiling in the admin GUI clears the cloud choice.

*Built 2026-11-01* (117 Vitest tests, 940 pytest):
- **`set-camera-settings`** (`cloud/lambda/set_camera_settings.py`, policy
  `cloud/iam/set-camera-settings-policy.json`): tags, schedule and `recordQuality`, each checked
  and written on its own; an emptied one is REMOVEd rather than stored. Schedule rules: days
  `mon`…`sun`, `HH:MM`, actions `start`/`stop`/`mode:<recordingMode>` with set-camera-mode's own
  rules (#65), at most 50 entries, no two changes of one kind at the same moment, a zone name by
  shape (the Pi checks it against its zone database in Phase 5 — Lambda's isn't relied on).
  `recordQuality`: null or the ceiling clears it; `{"stream": "sub"}` only when the ceiling is
  main and a sub stream exists; anything else 400. 30 pytest. Created by
  `bash cloud/provision.sh camera-settings`, whose route check now takes the phrase the function
  answers an empty call with.
- **`list-cameras`** returns the §5.1 fields and `recordQualityOptions` (the ceiling, plus "sub"
  when it is below; none for the USB camera or before a ceiling is known).
- **The admin GUI writes the ceiling** (`_record_ceiling`): at registration, after a stream or
  frame-rate switch, and on `GET …/stream` when the row differs (learnt on read, like the codec,
  #69); a switch away from main clears a "sub" choice. Found while testing it: the camera's
  read-back rate is a float (10.0), which boto3 refuses for DynamoDB — the fake table didn't, so a
  test now checks the stored type (it fails without the conversion). 5 admin tests.
- **Settings:** Tags (chips; the camera list groups by the first tag), Recorded quality (the
  ceiling as text, the choice as a dropdown when there is one; "not applied yet" until Phase 5),
  Camera schedule (day buttons, time, action limited to the camera's modes, a week view of the
  recording windows with mode marks, hours per week, a warning while a Start has no Stop within
  24 h, entries with delete; the zone is the schedule's own, or this browser's for a new one; "not
  run yet" until Phase 5). `src/cameras/schedule.ts` holds the rules: edge-triggered windows from
  each Start to the next Stop, wrapping Sunday into Monday. A missing route now reads "the server
  doesn't have this route yet" instead of "Missing Authentication Token".
- **Cross-checks:** the schedule's days and the copy of the recording modes in the new Lambda
  (`tests/test_value_lists.py`).

*Deployed 2026-11-01.* The admin GUI recorded the ceilings when the cameras' stream settings were
opened: cam-02 (640x360), cam-03 and cam-04 (800x448) all record their **sub** stream at 15 fps, so
no lower step exists for any of them today — the Recorded-quality card says so, and the dropdown
appears once a camera records main. cam-01 is fixed on the Pi, as designed. The build's plugin-
timing report (Rolldown's `bundlerTimings`, noise in `deploy.sh page` output) is switched off in
`vite.config.ts`.

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

*Built 2026-11-01* (119 Vitest tests, 971 pytest):
- **5.1 `adapter/schedule_runner.py`**, run by `kvs-schedule.timer` on every minute (oneshot;
  `Persistent=` off on purpose — the runner catches up by itself). Edge-triggered with a last-run
  file; entries missed by up to 10 min still act, older ones are logged and skipped; the first run
  replays nothing; times in the schedule's zone with `zoneinfo` (a skipped DST time acts once after
  the jump, a repeated one once). Actions: start/stop through `camera_control.set_stream` (a camera
  with no producer on this Pi is skipped), a mode through a conditional registry write that refuses
  an unregistered camera; a mode change and a Start at one moment run mode first. **Deviation:** the
  outage supervisor's cache holds only buffer seconds, so the runner keeps its own 0600 cache of the
  schedules (no credentials), refreshed at most every 5 min and used while AWS is unreachable.
  Found by its tests before it shipped: a small clock step back (NTP) made an entry act twice — the
  later last-run time is now kept, and a last run recorded far ahead (a Pi with no battery clock)
  starts over from now. 15 tests. Run once against the real registry: reads it with the device's
  identity, finds no schedules, does nothing.
- **5.2 the stream step:** `stream-codec.py` reads `recordQuality` once at start (6 s budget; an
  unreachable registry means the camera's own stream) and, for "sub" below a main ceiling with a
  `_live` path in MediaMTX, the recording producer pulls that path (`VIDEO_PATH`, now used by
  `stream-channel.sh` and `stream-cam02.sh` for recording too). It records `recordQualityActive`
  (`{stream, size}`) and stamps `videoCodecActiveSince` on a change of picture as well as of codec:
  KVS refuses a clip whose codec private data changes, and the size is part of it — so a clip
  across a main/sub switch is split, not lost. That also covers a stream switched in an admin GUI
  with the codec unchanged, which nothing split before (unmeasured, §7). 10 tests (the first for
  `stream-codec.py`). The page's H.265 warnings use the codec the cloud stream carries.
- **5.3 thumbnails:** `adapter/thumbnails.py`, run by `kvs-thumbnails.timer` every 10 min: one frame
  per camera whose path is flowing (#50), from the sub stream when there is one, scaled to ≤640 px,
  uploaded as `thumbs/<cameraId>.jpg`. Looking at the first real frames found cam-02/03/04 grey with
  only moving edges — a P-frame decoded without its keyframe; `-skip_frame nokey` fixed it (real
  pictures in 2–3 s; a test pins the flag). `list-cameras` HEADs each thumbnail and returns a 1-hour
  link with the time taken. AWS side by `bash cloud/provision.sh thumbnails`: inline policy
  `Thumbnails` on `KVSAdapterRole` (`cloud/iam/thumbnails-policy.json`), `thumbnails-read` on
  `ListCamerasLambdaRole` (`cloud/iam/list-cameras-thumbnails-policy.json`), and list-cameras'
  `BUCKET` taken from record-clip's — adding only, never changing (dry run checked against the
  account). The page shows the picture in Settings, and small ones in the camera list behind a
  "Preview" switch (remembered per browser). **Deviation:** no on-demand grab when Settings opens
  (it would need a new MQTT command); every 10 minutes is the only cadence.

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
- [x] Phase 0: sign-in and the 70-minute refresh checked (the user, before the cutover)
- [x] Phase 1: Settings on today's API, deployed at `/app/` (2026-10-31)
- [x] Phase 1: the *Verify* steps, signed in (the user, before the cutover)
- [x] Phase 2: `user-settings` backend, layouts, Live, Events — committed, provisioned, deployed
- [ ] Phase 2: Pi CPU and memory with every camera live at once (§7), before 16 tiles are called usable
- [x] Phase 3: parity audit; cutover tooling (`deploy.sh page` = web panel at the root), rehearsed
- [x] Phase 3: the user's checks at `/app/`; `deploy.sh page` (the cutover); `client/index.html` out, docs (2026-11-01)
- [x] Phase 4: `set-camera-settings`, new registry fields, admin GUI ceiling — provisioned and deployed (2026-11-01)
- [x] Phase 5: schedule runner, stream step, thumbnails (built; timers, provision, deploy open)
- [ ] Measurements of §7 recorded here
