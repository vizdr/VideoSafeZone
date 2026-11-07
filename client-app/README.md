# client-app — the web panel

The cloud client rebuilt as a Vue app with panels (Live, Events, Settings), replacing
`client/index.html` — which it did at the cutover on 2026-11-01. Plan, decisions and progress:
[`WEB-PANEL-PLAN.md`](../WEB-PANEL-PLAN.md).

Vue 3 + TypeScript + Vite, OpenVue (the MIT fork of PrimeVue 4.5.5, WEB-PANEL-PLAN.md D1),
Pinia, vue-router in hash mode, hls.js, Vitest. Node 24 builds it (LAUNCH.md Part H).

```bash
npm ci              # install exactly what package-lock.json says
npm run dev         # local dev server; sign-in and the API are the real ones
npm test            # Vitest: Cognito, the API client, the player logic, the shell
npm run typecheck   # vue-tsc, including the tests
npm run build       # into dist/ (not in git)
```

Deploy from the repository root: `bash cloud/deploy.sh page` builds fresh from the lockfile and
publishes at `https://dugyd3kkt36pw.cloudfront.net/`; `bash cloud/deploy.sh app` publishes the same
build at `/app/index.html`, a preview to look at first.

## Layout

| Path | What |
|---|---|
| `src/config.ts` | the API and Cognito constants (the same as the old page's) |
| `src/auth/cognito.ts` | sign-in over Cognito's JSON API, renewing the token before it expires |
| `src/api/` | one client for API Gateway; one function per route; the response types |
| `src/player/camera-controller.ts` | the old page's player, Live, Start/Stop and archive check, framework-free |
| `src/cameras/list.ts` | the camera list's search, grouping and labels |
| `src/cameras/settings.ts` | Settings' choices and wording, ported from the old page |
| `src/stores/players.ts` | one `CameraController` per camera: the archive check, status dots, Cloud recording |
| `src/composables/useSavedSetting.ts` | save on change, put the old value back when the server refuses |
| `src/components/settings/` | the Settings cards: Cloud recording, Camera and Recording sections |
| `src/layouts/grid.ts` | the Live layouts as data; which camera goes in which cell, paging, swaps |
| `src/components/live/` | the Live grid's tile (plays while mounted) and the layout picker |
| `src/stores/userSettings.ts` | the account's own settings (GET/PUT `/me/settings`) |
| `src/clips/format.ts`, `src/components/events/` | Events: the clip list and its player |
| `src/stores/`, `src/views/`, `src/components/` | Pinia stores and the Vue screens |
| `src/auth/roles.ts`, `src/views/system/`, `src/composables/useLoad.ts` | what each role sees and where it lands; the System console (`/directory/*`, DEALER-PANEL-PLAN.md Phase 4) |
| `src/theme.ts` | the OpenVue preset: Aura, sky accent, slate dark surfaces |
| `tests/` | Vitest; `camera-controller.spec.ts` keeps the FoundAndFixed fixes the player carries |

That the value lists offered here (outage durations, recording and IR modes) match what the
Lambdas accept is checked from the pytest side, `tests/test_value_lists.py` in the repository.

The player logic in `camera-controller.ts` was ported from `client/index.html` (now in git history) without changing
its behaviour; each fix in it cites its `FoundAndFixed.md` entry. Change it there with a test.
