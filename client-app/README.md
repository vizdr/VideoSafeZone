# client-app — the web panel

The cloud client rebuilt as a Vue app with panels (Live, Events, Settings), replacing
`client/index.html` once it does everything that page does. Plan, decisions and progress:
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

Deploy with `bash cloud/deploy.sh app` from the repository root: it builds fresh from the lockfile
and publishes at `https://dugyd3kkt36pw.cloudfront.net/app/index.html`, beside the old page.

## Layout

| Path | What |
|---|---|
| `src/config.ts` | the API and Cognito constants (the same as the old page's) |
| `src/auth/cognito.ts` | sign-in over Cognito's JSON API, renewing the token before it expires |
| `src/api/` | one client for API Gateway; one function per route; the response types |
| `src/player/camera-controller.ts` | the old page's player, Live, Start/Stop and archive check, framework-free |
| `src/cameras/list.ts` | the camera list's search, grouping and labels |
| `src/stores/`, `src/views/`, `src/components/` | Pinia stores and the Vue screens |
| `src/theme.ts` | the OpenVue preset: Aura, sky accent, slate dark surfaces |
| `tests/` | Vitest; `camera-controller.spec.ts` keeps the FoundAndFixed fixes the player carries |

The player logic in `camera-controller.ts` was ported from `client/index.html` without changing
its behaviour; each fix in it cites its `FoundAndFixed.md` entry. Change it there with a test.
