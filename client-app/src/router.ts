// Hash routes (#/live, #/settings/cam-03/camera) so CloudFront never has to route: a deep path
// would get S3's 403 under OAC (WEB-PANEL-PLAN.md D2).
import { createRouter, createWebHashHistory, type RouteRecordRaw } from "vue-router";
import LoginView from "./views/LoginView.vue";
import ShellLayout from "./views/ShellLayout.vue";
import LiveView from "./views/LiveView.vue";
import EventsView from "./views/EventsView.vue";
import SettingsView from "./views/SettingsView.vue";

export const routes: RouteRecordRaw[] = [
  { path: "/login", name: "login", component: LoginView, meta: { public: true } },
  {
    path: "/",
    component: ShellLayout,
    children: [
      { path: "", redirect: "/live" },
      { path: "live/:cameraId?", name: "live", component: LiveView },
      { path: "events/:cameraId?", name: "events", component: EventsView },
      { path: "settings/:cameraId?/:section?", name: "settings", component: SettingsView },
    ],
  },
  { path: "/:rest(.*)*", redirect: "/live" },
];

export function makeRouter(isSignedIn: () => boolean) {
  const router = createRouter({ history: createWebHashHistory(), routes });
  router.beforeEach(to => {
    if (!to.meta.public && !isSignedIn()) return { name: "login", query: { next: to.fullPath } };
    if (to.name === "login" && isSignedIn()) return "/live";
    return true;
  });
  return router;
}
