import { createApp } from "vue";
import { createPinia } from "pinia";
import OpenVue from "openvue/config";
import "primeicons/primeicons.css";
import "./styles.css";
import App from "./App.vue";
import { preset } from "./theme";
import { makeRouter } from "./router";
import { useAuthStore } from "./stores/auth";

async function start(): Promise<void> {
  const app = createApp(App);
  app.use(createPinia());
  // Dark always: <html class="app-dark"> in index.html.
  app.use(OpenVue, { theme: { preset, options: { darkModeSelector: ".app-dark" } } });
  const auth = useAuthStore();
  await auth.restore();          // a refresh token in sessionStorage signs a reloaded tab back in
  app.use(makeRouter(() => auth.signedIn));
  app.mount("#app");
}

void start();
