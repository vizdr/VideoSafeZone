<script setup lang="ts">
import { computed, ref, watch, watchEffect } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { Role } from "../api/types";
import { CUSTOMER_ROLES } from "../auth/roles";
import { cameraName, modeLabel } from "../cameras/list";
import CameraSection from "../components/settings/CameraSection.vue";
import CloudRecording from "../components/settings/CloudRecording.vue";
import RecordingSection from "../components/settings/RecordingSection.vue";
import { useAuthStore } from "../stores/auth";
import { useCamerasStore } from "../stores/cameras";

// WEB-PANEL-PLAN.md §4.2: the camera's name, thumbnail, Cloud recording and the section links in
// the middle; the selected section's cards on the right.
const SECTIONS = [
  { name: "camera", label: "Camera", icon: "pi-video" },
  { name: "recording", label: "Recording", icon: "pi-bolt" },
] as const;

const route = useRoute();
const router = useRouter();
const cameras = useCamerasStore();
const auth = useAuthStore();
const requested = computed(() =>
  typeof route.params.cameraId === "string" ? route.params.cameraId : undefined);
// Cameras in use only. An unregistered one -- an old link -- is pointed to Cloud adapters → Cameras,
// the one place that lists it, with Re-register (the user, 2026-11-08).
const found = computed(() => cameras.byId(requested.value));
const cam = computed(() => (found.value?.unregisteredAt ? undefined : found.value));
const unregistered = computed(() => (found.value?.unregisteredAt ? found.value : undefined));
const customer = computed(() => CUSTOMER_ROLES.includes(auth.role as Role | null));
const section = computed(() => (route.params.section === "recording" ? "recording" : "camera"));

// The Pi's latest frame (thumbnails.py, every 10 minutes). Its link lasts an hour: an expired or
// missing one falls back to the placeholder.
const thumbFailed = ref(false);
watch(() => cam.value?.thumbnailUrl, () => { thumbFailed.value = false; });
const takenAt = computed(() => cam.value?.thumbnailAt
  ? `taken ${new Date(cam.value.thumbnailAt).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" })}` : null);

// Settings always shows a camera: the first one until another is picked.
watchEffect(() => {
  if (!requested.value && cameras.active.length) {
    void router.replace({ name: "settings", params: { cameraId: cameras.active[0].id } });
  }
});
</script>

<template>
  <section class="view settings">
    <h1>Settings</h1>
    <!-- Keyed by camera: switching cameras starts each card fresh, with no status left over. -->
    <div v-if="cam" :key="cam.id" class="settings-grid">
      <div class="side">
        <h2 class="cam-heading">
          {{ cameraName(cam) }}<span v-if="cam.displayName" class="cam-id">{{ cam.id }}</span>
        </h2>
        <p class="muted small">{{ modeLabel(cam) }}{{ cam.hasIrControl ? " · IR control" : "" }}</p>
        <figure class="thumb">
          <img v-if="cam.thumbnailUrl && !thumbFailed" :src="cam.thumbnailUrl" :alt="`Latest frame of ${cameraName(cam)}`"
               @error="thumbFailed = true" />
          <template v-else><i class="pi pi-image" aria-hidden="true" /><span>No picture yet</span></template>
          <figcaption v-if="cam.thumbnailUrl && !thumbFailed && takenAt">{{ takenAt }}</figcaption>
        </figure>
        <CloudRecording :camera="cam" />
        <nav class="sections" aria-label="Settings sections">
          <RouterLink v-for="s in SECTIONS" :key="s.name"
                      :to="{ name: 'settings', params: { cameraId: cam.id, section: s.name } }"
                      :class="{ active: section === s.name }"
                      :aria-current="section === s.name ? 'page' : undefined">
            <i :class="['pi', s.icon]" aria-hidden="true" />
            <span>{{ s.label }}</span>
            <i class="pi pi-angle-right" aria-hidden="true" />
          </RouterLink>
        </nav>
      </div>
      <div class="cards">
        <CameraSection v-if="section === 'camera'" :camera="cam" />
        <RecordingSection v-else :camera="cam" />
      </div>
    </div>
    <p v-else-if="unregistered" data-test="unregistered">
      {{ cameraName(unregistered) }} is unregistered.
      <template v-if="customer && unregistered.adapterId">Re-register it under
        <RouterLink :to="{ name: 'add-cameras', params: { thing: unregistered.adapterId } }">Cloud adapters → Cameras</RouterLink>.
      </template>
    </p>
    <p v-else-if="requested && cameras.loaded">There is no camera “{{ requested }}”.</p>
    <p v-else-if="cameras.loaded" class="muted">No cameras registered yet.</p>
  </section>
</template>

<style scoped>
.settings-grid {
  display: grid;
  grid-template-columns: 18rem minmax(0, 1fr);
  gap: 1.5rem;
  align-items: start;
}
.cam-heading { margin: 0 0 0.2rem; font-size: 1.15rem; }
.cam-id { color: var(--p-text-muted-color); font-size: 0.85rem; font-weight: normal; margin-left: 0.45rem; }
.small { font-size: 0.85rem; margin: 0 0 0.75rem; }
.thumb {
  aspect-ratio: 16 / 9;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.4rem;
  background: var(--p-surface-950);
  border-radius: 0.5rem;
  color: var(--p-text-muted-color);
  font-size: 0.8rem;
}
.thumb .pi { font-size: 1.6rem; }
.thumb { position: relative; overflow: hidden; margin: 0; }
.thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.thumb figcaption {
  position: absolute; right: 0.4rem; bottom: 0.35rem; padding: 0.1rem 0.4rem; border-radius: 0.3rem;
  background: rgba(0, 0, 0, 0.6); color: #fff; font-size: 0.72rem;
}
.sections { display: flex; flex-direction: column; gap: 0.5rem; }
.sections a {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.75rem 0.9rem;
  border-radius: 0.5rem;
  background: var(--p-surface-700);
  color: var(--p-text-color);
  text-decoration: none;
  font-weight: 600;
}
.sections a span { flex: 1; }
.sections a:hover { background: var(--p-surface-600); }
.sections a.active { background: var(--p-primary-color); color: var(--p-primary-contrast-color); }
.sections a:focus-visible { outline: 2px solid var(--p-primary-color); outline-offset: 2px; }

@media (max-width: 1100px) {
  .settings-grid { grid-template-columns: minmax(0, 1fr); }
  .thumb { display: none; }          /* a placeholder until Phase 5's thumbnails: not worth the height */
  .sections { flex-direction: row; }
  .sections a { flex: 1; }
}
</style>
