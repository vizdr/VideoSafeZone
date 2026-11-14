<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import Button from "openvue/button";
import IconField from "openvue/iconfield";
import InputIcon from "openvue/inputicon";
import InputText from "openvue/inputtext";
import Message from "openvue/message";
import ToggleSwitch from "openvue/toggleswitch";
import { cameraName, groupByPlace, matchesQuery } from "../cameras/list";
import { useAuthStore } from "../stores/auth";
import { useCamerasStore } from "../stores/cameras";
import { useDirectoryStore } from "../stores/directory";
import { usePlayersStore } from "../stores/players";

const props = defineProps<{ selected?: string }>();
const emit = defineEmits<{ select: [id: string] }>();

const store = useCamerasStore();
const players = usePlayersStore();
const directory = useDirectoryStore();
const auth = useAuthStore();
// Where a customer's cameras are -- their accounts' sites and adapters -- for the groups below. Asked
// once per sign-in (the store keeps it); staff have no accounts, so their list is grouped by tag only.
onMounted(() => { if (auth.role === "customer") void directory.load(); });

// The dot is the archive check's answer (FoundAndFixed.md #83): footage reaching the cloud or not.
function dot(id: string): { cls: string; label: string } {
  const s = players.states[id];
  if (s?.archiving) return { cls: "on", label: "Recording to the cloud" };
  if (s?.archivePending) return { cls: "pending", label: "Starting cloud recording" };
  return { cls: "off", label: "Not recording to the cloud" };
}
const query = ref("");

// "Preview": the Pi's latest frame of each camera beside its name (thumbnails.py, every 10 min).
// A choice for this browser only, remembered where storage allows it.
const PREVIEW_KEY = "vsz.preview";
const preview = ref((() => { try { return localStorage.getItem(PREVIEW_KEY) === "1"; } catch { return false; } })());
function setPreview(on: boolean): void {
  preview.value = on;
  try { localStorage.setItem(PREVIEW_KEY, on ? "1" : "0"); } catch { /* storage blocked */ }
}
const failed = ref(new Set<string>());
// Cameras in use only: an unregistered one is listed under Cloud adapters → Cameras alone (the user,
// 2026-11-08), with Re-register. Grouped by place -- account, site, adapter -- when there is more than
// one (DEALER-PANEL-PLAN.md D18, D19), then by tag.
const places = computed(() => groupByPlace(store.active.filter(c => matchesQuery(c, query.value)),
                                           directory.me?.accounts ?? []));
const shown = computed(() => places.value.reduce((n, p) => n + p.groups.reduce((m, g) => m + g.cameras.length, 0), 0));
</script>

<template>
  <section class="camera-list" aria-label="Cameras">
    <div class="head">
      <h2>Cameras <span class="count">({{ store.active.length }})</span></h2>
      <label class="preview">Preview
        <ToggleSwitch :model-value="preview" aria-label="Show camera pictures" @update:model-value="setPreview" />
      </label>
    </div>
    <IconField>
      <InputIcon class="pi pi-search" />
      <InputText v-model="query" placeholder="Search" aria-label="Search cameras" size="small" fluid />
    </IconField>

    <p v-if="store.loading && !store.loaded" class="muted">Loading cameras…</p>
    <div v-else-if="store.error" class="list-error">
      <Message severity="error" size="small" variant="simple">{{ store.error }}</Message>
      <Button label="Try again" size="small" text @click="store.load()" />
    </div>
    <p v-else-if="store.loaded && !store.active.length" class="muted">
      No cameras registered yet. Add one in the admin GUI on the Pi.
    </p>
    <p v-else-if="store.loaded && !shown" class="muted">No camera matches “{{ query }}”.</p>

    <template v-for="place in places" :key="place.key">
      <h3 v-if="place.label" class="place" data-test="place">{{ place.label }}</h3>
      <template v-for="g in place.groups" :key="g.tag ?? ''">
      <component :is="place.label ? 'h4' : 'h3'" v-if="g.tag !== null || place.groups.length > 1" class="group">
        {{ g.tag ?? "Untagged" }}</component>
      <ul class="cams">
        <li v-for="c in g.cameras" :key="c.id">
          <button type="button" class="cam" :class="{ selected: c.id === props.selected }"
                  :aria-current="c.id === props.selected ? 'true' : undefined"
                  @click="emit('select', c.id)">
            <span class="dot" :class="dot(c.id).cls" role="img" :aria-label="dot(c.id).label"
                  :title="dot(c.id).label" />
            <span v-if="preview" class="mini">
              <img v-if="c.thumbnailUrl && !failed.has(c.id)" :src="c.thumbnailUrl" alt="" @error="failed.add(c.id)" />
            </span>
            <span class="name">{{ cameraName(c) }}</span>
            <span v-if="c.displayName" class="id">{{ c.id }}</span>
          </button>
        </li>
      </ul>
      </template>
    </template>
  </section>
</template>

<style scoped>
h2 { margin: 0; font-size: 1.05rem; }
.head { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; margin-bottom: 0.75rem; }
.preview { display: flex; align-items: center; gap: 0.4rem; font-size: 0.8rem; color: var(--p-text-muted-color); }
.mini { flex: none; width: 3.6rem; aspect-ratio: 16 / 9; border-radius: 0.2rem; overflow: hidden;
        background: var(--p-surface-950); align-self: center; }
.mini img { width: 100%; height: 100%; object-fit: cover; display: block; }
.count { color: var(--p-text-muted-color); font-weight: normal; }
.place { margin: 1.1rem 0 0.2rem; font-size: 0.9rem; font-weight: 600; }
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
