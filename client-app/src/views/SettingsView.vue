<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import { cameraName, codecLabel, modeLabel } from "../cameras/list";
import { OLD_PAGE } from "../config";
import { useCamerasStore } from "../stores/cameras";

const route = useRoute();
const cameras = useCamerasStore();
const requested = computed(() =>
  typeof route.params.cameraId === "string" ? route.params.cameraId : undefined);
const cam = computed(() => cameras.byId(requested.value));
</script>

<template>
  <section class="view">
    <h1>Settings</h1>
    <template v-if="cam">
      <h2 class="cam-heading">
        {{ cameraName(cam) }} <span v-if="cam.displayName" class="cam-id">{{ cam.id }}</span>
      </h2>
      <p class="muted">{{ modeLabel(cam) }} · {{ codecLabel(cam) }}{{ cam.hasIrControl ? " · IR control" : "" }}</p>
      <p>This camera's settings move here next. Until then, change them on the
        <a :href="OLD_PAGE">current page</a>.</p>
    </template>
    <p v-else-if="requested && cameras.loaded">There is no camera “{{ requested }}”.</p>
    <p v-else class="muted">Pick a camera on the left.</p>
  </section>
</template>

<style scoped>
.cam-heading { margin: 0 0 0.25rem; font-size: 1.2rem; }
.cam-id { color: var(--p-text-muted-color); font-size: 0.9rem; font-weight: normal; margin-left: 0.4rem; }
</style>
