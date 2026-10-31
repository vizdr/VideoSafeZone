<script setup lang="ts">
import type { SettingStatus } from "../../composables/useSavedSetting";

// Title and description on the left, the control on the right, and a status line under both:
// "saving…", what was saved, or why it wasn't.
defineProps<{ title: string; labelFor?: string; status?: SettingStatus | null }>();
</script>

<template>
  <div class="setting-row">
    <div class="text">
      <label v-if="labelFor" :for="labelFor" class="title">{{ title }}</label>
      <h3 v-else class="title">{{ title }}</h3>
      <div class="description"><slot /></div>
    </div>
    <div class="control"><slot name="control" /></div>
    <p v-if="status && status.text" class="status" :class="status.tone" role="status">{{ status.text }}</p>
  </div>
</template>

<style scoped>
.setting-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  column-gap: 1rem;
  align-items: start;
  padding: 0.7rem 0;
}
.setting-row + .setting-row { border-top: 1px solid var(--p-content-border-color); }
.title { display: block; margin: 0; font-size: 0.95rem; font-weight: 600; }
.description { color: var(--p-text-muted-color); font-size: 0.82rem; line-height: 1.4; margin-top: 0.15rem; }
.control { display: flex; align-items: center; gap: 0.5rem; padding-top: 0.1rem; }
.status { grid-column: 1 / -1; margin: 0.45rem 0 0; font-size: 0.82rem; color: var(--p-text-muted-color); }
.status.ok { color: var(--p-green-400); }
.status.error { color: var(--p-red-400); }

@media (max-width: 560px) {
  .setting-row { grid-template-columns: minmax(0, 1fr); }
  .control { padding-top: 0.5rem; }
}
</style>
