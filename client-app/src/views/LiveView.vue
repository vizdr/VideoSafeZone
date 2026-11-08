<script setup lang="ts">
import { computed, ref, watch, watchEffect } from "vue";
import { useRoute, useRouter } from "vue-router";
import Button from "openvue/button";
import LayoutPicker from "../components/live/LayoutPicker.vue";
import LiveTile from "../components/live/LiveTile.vue";
import type { SettingStatus } from "../composables/useSavedSetting";
import { LAYOUTS, bringToLarge, cameraOrder, pageOf, type Cell, type LayoutId } from "../layouts/grid";
import { useAuthStore } from "../stores/auth";
import { useCamerasStore } from "../stores/cameras";
import { useUserSettingsStore } from "../stores/userSettings";

// WEB-PANEL-PLAN.md §4.3: the cameras on a 4×4 grid in the account's layout (D3, D4). Each tile on
// the page plays its camera's live view while this tab shows it; leaving the tab, turning the page
// or changing the layout ends the tiles that go (LiveTile).
const route = useRoute();
const router = useRouter();
const cameras = useCamerasStore();
const auth = useAuthStore();
const settings = useUserSettingsStore();

const layoutId = computed(() => settings.liveLayout);
// The order the grid fills; clicks on small tiles change it for this visit only.
const order = ref<string[]>([]);
const selected = ref<string | null>(null);
// A new order from the settings (they arrive after the first layout, or were changed) starts the
// selection over at the first tile, or the camera picked in the list.
watch(() => cameraOrder(cameras.active, settings.largeCells, layoutId.value),
      o => { order.value = o; selected.value = null; }, { immediate: true });
const page = ref(0);
const view = computed(() => pageOf(order.value, layoutId.value, page.value));
watch(() => view.value.pages, n => { if (page.value > n - 1) page.value = n - 1; });
const tiles = computed(() =>
  LAYOUTS[layoutId.value].cells.map((cell, i) => ({ cell, cameraId: view.value.tiles[i] })));

const routeCamera = computed(() =>
  typeof route.params.cameraId === "string" ? route.params.cameraId : undefined);

// A camera picked in the list on the left: turn to its page and bring it into the first large cell.
watch(routeCamera, id => {
  if (!id || !order.value.includes(id)) return;
  const n = LAYOUTS[layoutId.value].cells.length;
  page.value = Math.floor(order.value.indexOf(id) / n);
  order.value = bringToLarge(order.value, layoutId.value, page.value, id);
  selected.value = id;
}, { immediate: true });

// Something is always selected: the routed camera if it's on this page, else the first tile.
watchEffect(() => {
  const onPage = view.value.tiles.filter((t): t is string => !!t);
  if (selected.value && onPage.includes(selected.value)) return;
  selected.value = routeCamera.value && onPage.includes(routeCamera.value) ? routeCamera.value : onPage[0] ?? null;
});

function select(id: string, cell: Cell): void {
  if (cell.span === 1) order.value = bringToLarge(order.value, layoutId.value, page.value, id);
  selected.value = id;
  if (routeCamera.value !== id) void router.replace({ name: "live", params: { cameraId: id } });
}

const layoutBusy = ref(false);
const layoutStatus = ref<SettingStatus | null>(null);
async function setLayout(id: LayoutId): Promise<void> {
  layoutBusy.value = true;
  layoutStatus.value = await settings.save({ liveLayout: id });
  layoutBusy.value = false;
}
</script>

<template>
  <section class="view live">
    <div class="toolbar">
      <h1>Live</h1>
      <LayoutPicker compact :model-value="layoutId" :disabled="layoutBusy" @update:model-value="setLayout" />
      <div v-if="view.pages > 1" class="pager">
        <Button icon="pi pi-angle-left" text rounded aria-label="Previous page" :disabled="page === 0" @click="page--" />
        <span>Page {{ page + 1 }} of {{ view.pages }}</span>
        <Button icon="pi pi-angle-right" text rounded aria-label="Next page" :disabled="page >= view.pages - 1" @click="page++" />
      </div>
      <span class="note muted">Tiles play while this tab is open and stop when you leave it.</span>
    </div>
    <p v-if="layoutStatus && layoutStatus.tone !== 'ok'" class="layout-status" :class="layoutStatus.tone">
      {{ layoutStatus.text }}</p>

    <p v-if="cameras.loaded && !cameras.active.length" class="muted" data-test="no-cameras">No cameras registered yet.
      <template v-if="auth.role === 'customer'">
        <RouterLink :to="{ name: 'add-cameras' }" data-test="add-cameras">Add cameras</RouterLink> ·
        <RouterLink :to="{ name: 'add-adapter' }">Add your Cloud Adapter</RouterLink></template></p>
    <div v-else class="grid">
      <LiveTile v-for="(t, i) in tiles" :key="t.cameraId ?? `empty-${i}`" :camera="cameras.byId(t.cameraId ?? undefined)"
                :cell="t.cell" :selected="!!t.cameraId && t.cameraId === selected"
                @select="t.cameraId && select(t.cameraId, t.cell)" />
    </div>
  </section>
</template>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
.toolbar h1 { margin: 0; }
.pager { display: flex; align-items: center; gap: 0.25rem; font-size: 0.9rem; }
.note { font-size: 0.82rem; margin-left: auto; }
.layout-status { font-size: 0.85rem; margin: -0.25rem 0 0.75rem; }
.layout-status.error { color: var(--p-red-400); }
.layout-status.info { color: var(--p-amber-400); }
/* 16:9 overall, so each of the 4×4 cells is 16:9 too; as large as the window allows. */
.grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  grid-template-rows: repeat(4, minmax(0, 1fr));
  gap: 4px;
  width: 100%;
  max-width: calc((100vh - 8.5rem) * 16 / 9);
  aspect-ratio: 16 / 9;
}
@media (max-width: 800px) {
  .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: none; aspect-ratio: auto; max-width: none; }
  .note { margin-left: 0; }
}
</style>
