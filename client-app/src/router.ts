// Hash routes (#/live, #/settings/cam-03/camera) so CloudFront never has to route: a deep path
// would get S3's 403 under OAC (WEB-PANEL-PLAN.md D2).
//
// Routes carry the roles that see them (DEALER-PANEL-PLAN.md D6, D7); a role that doesn't is sent
// to its own home. The System console and the dealer portal are loaded only when opened, so a
// customer's first load doesn't carry them. The API checks every call again -- this is convenience.
import { createRouter, createWebHashHistory, type RouteRecordRaw } from "vue-router";
import type { Role } from "./api/types";
import { CAMERA_ROLES, CUSTOMER_ROLES, DEALER_ROLES, PORTAL_ROLES, SYSTEM_ROLES, homeFor } from "./auth/roles";
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
        // The account's adapters (DEALER-PANEL-PLAN.md D15): before the camera route too.
        { path: "settings/adapters", name: "settings-adapters", component: () => import("./views/AdaptersSettings.vue"),
          meta: { roles: CUSTOMER_ROLES } },
        { path: "adapters/add", name: "add-adapter", component: () => import("./views/AddAdapterView.vue"),
          meta: { roles: CUSTOMER_ROLES } },
        // Adding cameras through an adapter (DEALER-PANEL-PLAN.md D16).
        { path: "cameras/add/:thing?", name: "add-cameras", component: () => import("./views/AddCamerasView.vue"),
          meta: { roles: CUSTOMER_ROLES } },
        { path: "settings/:cameraId?/:section?", name: "settings", component: SettingsView, meta: cameras },
        { path: "account", name: "account", component: AccountView },   // DEALER-PANEL-PLAN.md D18
        {
          // The dealer portal (DEALER-PANEL-PLAN.md §7.1, D7): dealers, and owners and administrators
          // across every dealer. A dealer's own details are the dealer's.
          path: "dealer",
          component: () => import("./views/dealer/PortalView.vue"),
          meta: { roles: PORTAL_ROLES },
          children: [
            { path: "", name: "dealer", redirect: { name: "dealer-customers" } },
            { path: "customers", name: "dealer-customers", component: () => import("./views/dealer/PortalCustomers.vue") },
            {
              path: "customers/:customerId",
              component: () => import("./views/dealer/CustomerView.vue"),
              children: [
                { path: "", name: "dealer-customer",
                  redirect: to => ({ name: "dealer-plan", params: { customerId: to.params.customerId } }) },
                { path: "plan/:adapterId?", name: "dealer-plan", component: () => import("./views/dealer/CustomerPlan.vue") },
                { path: "history/:adapterId?", name: "dealer-history", component: () => import("./views/dealer/CustomerHistory.vue") },
                { path: "cameras", name: "dealer-cameras", component: () => import("./views/dealer/CustomerCameras.vue") },
                { path: "users", name: "dealer-users", component: () => import("./views/dealer/CustomerUsers.vue") },
                { path: "details", name: "dealer-customer-details", component: () => import("./views/dealer/CustomerDetails.vue") },
              ],
            },
            { path: "adapters", name: "dealer-adapters", component: () => import("./views/system/SystemAdapters.vue") },
            { path: "billing", name: "dealer-billing", component: () => import("./views/dealer/DealerBilling.vue") },
            { path: "pricing", name: "dealer-pricing", component: () => import("./views/system/SystemPricing.vue") },
            { path: "details", name: "dealer-details", component: () => import("./views/dealer/DealerDetails.vue"),
              meta: { roles: DEALER_ROLES } },
          ],
        },
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
