<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import { useAuthStore } from "../stores/auth";

// Live, Events, Settings; Exit at the bottom (WEB-PANEL-PLAN.md §4.1). Audit logs and the other
// Videoloft entries are left out on purpose (D8).
const items = [
  { name: "live", label: "Live", icon: "pi-video" },
  { name: "events", label: "Events", icon: "pi-history" },
  { name: "settings", label: "Settings", icon: "pi-cog" },
] as const;

const route = useRoute();
const auth = useAuthStore();

// Switching tabs keeps the selected camera.
const cameraParam = computed(() =>
  typeof route.params.cameraId === "string" ? { cameraId: route.params.cameraId } : {});

// The shell sees the sign-out, ends any Live view, clears the stores and goes to sign-in.
function exit(): void {
  auth.signOut();
}
</script>

<template>
  <nav class="rail" aria-label="Main">
    <RouterLink v-for="item in items" :key="item.name" :to="{ name: item.name, params: cameraParam }"
                class="rail-item" :class="{ active: route.name === item.name }"
                :aria-current="route.name === item.name ? 'page' : undefined">
      <i :class="['pi', item.icon]" aria-hidden="true" />
      <span>{{ item.label }}</span>
    </RouterLink>
    <div class="spacer" />
    <button type="button" class="rail-item" @click="exit">
      <i class="pi pi-sign-out" aria-hidden="true" />
      <span>Exit</span>
    </button>
  </nav>
</template>

<style scoped>
.rail {
  display: flex;
  flex-direction: column;
  background: var(--p-surface-950);
  padding: 0.75rem 0;
}
.spacer { flex: 1; }
.rail-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.3rem;
  padding: 0.8rem 0.25rem;
  color: var(--p-text-muted-color);
  text-decoration: none;
  font: inherit;
  font-size: 0.8rem;
  background: none;
  border: 0;
  cursor: pointer;
}
.rail-item .pi { font-size: 1.3rem; }
.rail-item:hover { color: var(--p-text-color); }
.rail-item.active {
  color: var(--p-primary-contrast-color);
  background: var(--p-primary-color);
}
.rail-item:focus-visible { outline: 2px solid var(--p-primary-color); outline-offset: -2px; }

@media (max-width: 800px) {
  .rail { flex-direction: row; padding: 0 0.5rem; }
  .rail-item { flex-direction: row; padding: 0.7rem 0.8rem; }
}
</style>
