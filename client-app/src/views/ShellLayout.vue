<script setup lang="ts">
import { computed, onMounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { Role } from "../api/types";
import { CAMERA_ROLES } from "../auth/roles";
import AppRail from "../components/AppRail.vue";
import CameraList from "../components/CameraList.vue";
import HevcNote from "../components/HevcNote.vue";
import { useAuthStore } from "../stores/auth";
import { useCamerasStore } from "../stores/cameras";
import { usePlayersStore } from "../stores/players";
import { useUserSettingsStore } from "../stores/userSettings";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const cameras = useCamerasStore();
const players = usePlayersStore();
const userSettings = useUserSettingsStore();

const cameraId = computed(() =>
  typeof route.params.cameraId === "string" ? route.params.cameraId : undefined);

// Cameras and the Live layout only for whoever sees cameras (DEALER-PANEL-PLAN.md D5).
onMounted(() => {
  if (!CAMERA_ROLES.includes(auth.role as Role | null)) return;
  if (!cameras.loaded && !cameras.loading) void cameras.load();
  if (!userSettings.loaded) void userSettings.load();
});

const onSettings = computed(() => route.name === "settings" || route.name === "settings-live");
// The camera list belongs beside the camera pages only -- not beside Account, the dealer portal or
// the System console.
const showCameras = computed(() =>
  typeof route.name === "string" && ["live", "events", "settings"].some(n => (route.name as string).startsWith(n)));

// One controller per camera, following the list (stores/players.ts).
watch(() => cameras.cameras, list => players.sync(list), { immediate: true });

// Signed out from anywhere -- Exit, or a refresh Cognito refused: everything goes (any Live view
// is ended), then back to sign-in.
watch(() => auth.signedIn, signedIn => {
  if (signedIn) return;
  players.clear();
  cameras.clear();
  userSettings.clear();
  void router.replace({ name: "login" });
});

function select(id: string): void {
  // From Settings → Live view, a camera opens that camera's settings.
  const name = route.name === "settings-live" ? "settings" : (route.name ?? "live");
  void router.push({ name, params: { ...route.params, cameraId: id } });
}
</script>

<template>
  <div class="shell" :class="{ 'no-cameras': !showCameras }">
    <AppRail />
    <aside v-if="showCameras" class="cameras-col">
      <RouterLink v-if="onSettings" :to="{ name: 'settings-live' }" class="yours"
                  :class="{ active: route.name === 'settings-live' }">
        <i class="pi pi-th-large" aria-hidden="true" /> Live view <span>(yours)</span>
      </RouterLink>
      <CameraList :selected="cameraId" @select="select" />
    </aside>
    <main class="content">
      <HevcNote />
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.yours {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 1rem;
  padding: 0.6rem 0.75rem;
  border-radius: 0.5rem;
  background: var(--p-surface-800);
  color: var(--p-text-color);
  text-decoration: none;
  font-weight: 600;
}
.yours span { color: var(--p-text-muted-color); font-weight: normal; }
.yours.active { background: var(--p-highlight-background); color: var(--p-highlight-color); }
.yours:focus-visible { outline: 2px solid var(--p-primary-color); }
</style>
