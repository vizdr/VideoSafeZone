<script setup lang="ts">
import { computed, watchEffect } from "vue";
import { useRoute, useRouter } from "vue-router";
import { cameraName } from "../cameras/list";
import ClipList from "../components/events/ClipList.vue";
import { useCamerasStore } from "../stores/cameras";

// WEB-PANEL-PLAN.md §4.4: the evidence clips of the camera picked on the left (the first until
// another is picked). A merged list across cameras needs a list-clips change: later.
const route = useRoute();
const router = useRouter();
const cameras = useCamerasStore();
const requested = computed(() =>
  typeof route.params.cameraId === "string" ? route.params.cameraId : undefined);
const cam = computed(() => cameras.byId(requested.value));

watchEffect(() => {
  if (!requested.value && cameras.active.length) {
    void router.replace({ name: "events", params: { cameraId: cameras.active[0].id } });
  }
});
</script>

<template>
  <section class="view">
    <h1>Events</h1>
    <template v-if="cam">
      <h2 class="cam-heading">
        Evidence clips · {{ cameraName(cam) }}<span v-if="cam.displayName" class="cam-id">{{ cam.id }}</span>
      </h2>
      <!-- Keyed by camera: another camera's list starts fresh, with no page or status left over. -->
      <ClipList :key="cam.id" :camera="cam" />
    </template>
    <p v-else-if="requested && cameras.loaded">There is no camera “{{ requested }}”.</p>
    <p v-else-if="cameras.loaded" class="muted">No cameras registered yet.</p>
  </section>
</template>

<style scoped>
.cam-heading { margin: 0 0 0.75rem; font-size: 1.1rem; }
.cam-id { color: var(--p-text-muted-color); font-size: 0.85rem; font-weight: normal; margin-left: 0.45rem; }
</style>
