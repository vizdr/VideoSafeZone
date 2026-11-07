// Hash routes (#/live, #/settings/cam-03/camera) so CloudFront never has to route: a deep path
// would get S3's 403 under OAC (WEB-PANEL-PLAN.md D2).
//
// Routes carry the roles that see them (DEALER-PANEL-PLAN.md D6, D7); a role that doesn't is sent
// to its own home. The System console and the dealer portal are loaded only when opened, so a
// customer's first load doesn't carry them. The API checks every call again -- this is convenience.
import { createRouter, createWebHashHistory, type RouteRecordRaw } from "vue-router";
import type { Role } from "./api/types";
import { CAMERA_ROLES, DEALER_ROLES, SYSTEM_ROLES, homeFor } from "./auth/roles";
import LoginView from "./views/LoginView.vue";
import ShellLayout from "./views/ShellLayout.vue";
import LiveView from "./views/LiveView.vue";
import EventsView from "./views/EventsView.vue";
import SettingsView from "./views/SettingsView.vue";
import LiveViewSettings from "./views/LiveViewSettings.vue";
import AccountView from "./views/AccountView.vue";

const cameras = { roles: CAMERA_ROLES };

export function makeRoutes(role: () => Role | null): RouteRecordRaw[] {
  return [
    { path: "/login", name: "login", component: LoginView, meta: { public: true } },
    {
      path: "/",
      component: ShellLayout,
      children: [
        { path: "", name: "home", redirect: () => homeFor(role()) },
        { path: "live/:cameraId?", name: "live", component: LiveView, meta: cameras },
        { path: "events/:cameraId?", name: "events", component: EventsView, meta: cameras },
        // Before the camera route, which would otherwise take "live-view" for a camera ID.
        { path: "settings/live-view", name: "settings-live", component: LiveViewSettings, meta: cameras },
        { path: "settings/:cameraId?/:section?", name: "settings", component: SettingsView, meta: cameras },
        { path: "account", name: "account", component: AccountView },   // DEALER-PANEL-PLAN.md D18
        { path: "dealer", name: "dealer", component: () => import("./views/DealerHome.vue"),
          meta: { roles: DEALER_ROLES } },
        {
          path: "system",
          component: () => import("./views/system/SystemView.vue"),
          meta: { roles: SYSTEM_ROLES },
          children: [
            { path: "", name: "system", redirect: { name: "system-overview" } },
            { path: "overview", name: "system-overview", component: () => import("./views/system/SystemOverview.vue") },
            { path: "dealers", name: "system-dealers", component: () => import("./views/system/SystemDealers.vue") },
            { path: "customers", name: "system-customers", component: () => import("./views/system/SystemCustomers.vue") },
            { path: "users", name: "system-users", component: () => import("./views/system/SystemUsers.vue") },
            { path: "adapters", name: "system-adapters", component: () => import("./views/system/SystemAdapters.vue") },
            { path: "pricing", name: "system-pricing", component: () => import("./views/system/SystemPricing.vue") },
          ],
        },
      ],
    },
    { path: "/:rest(.*)*", redirect: "/" },
  ];
}

/** The routes for a session without a role (tests, and the old pool before the switch). */
export const routes = makeRoutes(() => null);

export function makeRouter(isSignedIn: () => boolean, role: () => Role | null = () => null) {
  const router = createRouter({ history: createWebHashHistory(), routes: makeRoutes(role) });
  router.beforeEach(to => {
    if (!to.meta.public && !isSignedIn()) return { name: "login", query: { next: to.fullPath } };
    if (to.name === "login" && isSignedIn()) return homeFor(role());
    const allowed = to.matched.map(r => r.meta.roles as ReadonlyArray<Role | null> | undefined).filter(Boolean);
    if (allowed.some(roles => !roles!.includes(role()))) return homeFor(role());
    return true;
  });
  return router;
}
