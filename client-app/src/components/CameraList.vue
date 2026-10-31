<script setup lang="ts">
import { computed, ref } from "vue";
import Button from "openvue/button";
import IconField from "openvue/iconfield";
import InputIcon from "openvue/inputicon";
import InputText from "openvue/inputtext";
import Message from "openvue/message";
import { cameraName, groupCameras, matchesQuery } from "../cameras/list";
import { useCamerasStore } from "../stores/cameras";
import { usePlayersStore } from "../stores/players";

const props = defineProps<{ selected?: string }>();
const emit = defineEmits<{ select: [id: string] }>();

const store = useCamerasStore();
const players = usePlayersStore();

// The dot is the archive check's answer (FoundAndFixed.md #83): footage reaching the cloud or not.
function dot(id: string): { cls: string; label: string } {
  const s = players.states[id];
  if (s?.archiving) return { cls: "on", label: "Recording to the cloud" };
  if (s?.archivePending) return { cls: "pending", label: "Starting cloud recording" };
  return { cls: "off", label: "Not recording to the cloud" };
}
const query = ref("");
const groups = computed(() => groupCameras(store.cameras.filter(c => matchesQuery(c, query.value))));
const shown = computed(() => groups.value.reduce((n, g) => n + g.cameras.length, 0));
</script>

<template>
  <section class="camera-list" aria-label="Cameras">
    <h2>Cameras <span class="count">({{ store.cameras.length }})</span></h2>
    <IconField>
      <InputIcon class="pi pi-search" />
      <InputText v-model="query" placeholder="Search" aria-label="Search cameras" size="small" fluid />
    </IconField>

    <p v-if="store.loading && !store.loaded" class="muted">Loading cameras…</p>
    <div v-else-if="store.error" class="list-error">
      <Message severity="error" size="small" variant="simple">{{ store.error }}</Message>
      <Button label="Try again" size="small" text @click="store.load()" />
    </div>
    <p v-else-if="store.loaded && !store.cameras.length" class="muted">
      No cameras registered yet. Add one in the admin GUI on the Pi.
    </p>
    <p v-else-if="store.loaded && !shown" class="muted">No camera matches “{{ query }}”.</p>

    <template v-for="g in groups" :key="g.tag ?? ''">
      <h3 v-if="g.tag !== null || groups.length > 1" class="group">{{ g.tag ?? "Untagged" }}</h3>
      <ul class="cams">
        <li v-for="c in g.cameras" :key="c.id">
          <button type="button" class="cam" :class="{ selected: c.id === props.selected }"
                  :aria-current="c.id === props.selected ? 'true' : undefined"
                  @click="emit('select', c.id)">
            <span class="dot" :class="dot(c.id).cls" role="img" :aria-label="dot(c.id).label"
                  :title="dot(c.id).label" />
            <span class="name">{{ cameraName(c) }}</span>
            <span v-if="c.displayName" class="id">{{ c.id }}</span>
          </button>
        </li>
      </ul>
    </template>
  </section>
</template>

<style scoped>
h2 { margin: 0 0 0.75rem; font-size: 1.05rem; }
.count { color: var(--p-text-muted-color); font-weight: normal; }
.group {
  margin: 1rem 0 0.25rem;
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--p-text-muted-color);
}
.cams { list-style: none; margin: 0.5rem 0 0; padding: 0; }
.cam {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  width: 100%;
  padding: 0.5rem 0.6rem;
  border: 0;
  border-radius: 0.4rem;
  background: none;
  color: var(--p-text-color);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.cam:hover { background: var(--p-surface-800); }
.cam.selected { background: var(--p-highlight-background); color: var(--p-highlight-color); }
.cam:focus-visible { outline: 2px solid var(--p-primary-color); }
.dot {
  flex: none;
  width: 0.55rem;
  height: 0.55rem;
  border-radius: 50%;
  align-self: center;
  background: var(--p-surface-500);
}
.dot.on { background: var(--p-green-400); }
.dot.pending { background: var(--p-amber-400); animation: pulse 1.2s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: 0.35; } }
@media (prefers-reduced-motion: reduce) { .dot.pending { animation: none; } }
.name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.id { color: var(--p-text-muted-color); font-size: 0.8rem; }
.list-error { margin-top: 0.75rem; }
</style>
