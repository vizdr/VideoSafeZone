<script setup lang="ts">
import { computed, ref } from "vue";
import Select from "openvue/select";
import { cameraName } from "../cameras/list";
import LayoutPicker from "../components/live/LayoutPicker.vue";
import SettingCard from "../components/settings/SettingCard.vue";
import SettingRow from "../components/settings/SettingRow.vue";
import type { SettingStatus } from "../composables/useSavedSetting";
import { largeCount, type LayoutId } from "../layouts/grid";
import { useCamerasStore } from "../stores/cameras";
import { useUserSettingsStore } from "../stores/userSettings";

// Settings → Live view (yours): the account's own Live layout and large-cell cameras
// (WEB-PANEL-PLAN.md D3, D4). Not a camera setting, so it sits above the camera list.
const cameras = useCamerasStore();
const settings = useUserSettingsStore();
const busy = ref(false);
const layoutStatus = ref<SettingStatus | null>(null);
const cellsStatus = ref<SettingStatus | null>(null);

const slots = computed(() => largeCount(settings.liveLayout));
// "" is stored for "the next camera"; the dropdown needs a non-empty value to show it as chosen.
const NEXT = "next";
const options = computed(() => [
  { value: NEXT, label: "The next camera in the list" },
  ...cameras.cameras.map(c => ({ value: c.id, label: c.displayName ? `${cameraName(c)} (${c.id})` : c.id })),
]);
// A pick for a camera that is gone shows as "next camera": it is dropped when the grid is built.
const pick = (i: number) => {
  const id = settings.largeCells[i] ?? "";
  return cameras.byId(id) ? id : NEXT;
};

async function saveLayout(id: LayoutId): Promise<void> {
  busy.value = true;
  layoutStatus.value = { text: "saving…", tone: "info" };
  layoutStatus.value = await settings.save({ liveLayout: id });
  busy.value = false;
}

async function saveCell(i: number, id: string): Promise<void> {
  const next = Array.from({ length: slots.value }, (_, k) => pick(k));
  next[i] = id;
  const stored = next.map(v => (v === NEXT ? "" : v));
  busy.value = true;
  cellsStatus.value = { text: "saving…", tone: "info" };
  cellsStatus.value = await settings.save({ largeCells: stored });
  busy.value = false;
}
</script>

<template>
  <section class="view">
    <h1>Settings</h1>
    <h2 class="sub">Live view <span class="muted">(yours)</span></h2>
    <p class="muted intro">These belong to your account, not to a camera: they follow you to any browser
      you sign in from.</p>
    <p v-if="settings.problem" class="problem">{{ settings.problem }}</p>

    <div class="cards">
      <SettingCard icon="pi-th-large">
        <SettingRow title="Layout" :status="layoutStatus">
          How the Live tab arranges the cameras. Large cells show the cameras chosen below.
          <div class="picker"><LayoutPicker :model-value="settings.liveLayout" :disabled="busy" @update:model-value="saveLayout" /></div>
        </SettingRow>
      </SettingCard>

      <SettingCard icon="pi-star">
        <SettingRow title="Large cells" :status="cellsStatus">
          <template v-if="slots">Which camera each large cell shows. A click on a small tile in Live
            brings that camera into the first large cell until you leave the tab.</template>
          <template v-else>This layout has no large cell: every camera gets the same size.</template>
          <div v-for="i in slots" :key="i" class="cell-row">
            <label :for="`large-${i}`">Large cell {{ i }}</label>
            <Select :input-id="`large-${i}`" :model-value="pick(i - 1)" :options="options" option-label="label"
                    option-value="value" :disabled="busy" class="cell-select"
                    @update:model-value="(v: string) => saveCell(i - 1, v)" />
          </div>
        </SettingRow>
      </SettingCard>
    </div>
  </section>
</template>

<style scoped>
.sub { margin: 0 0 0.25rem; font-size: 1.15rem; }
.intro { margin: 0 0 1rem; font-size: 0.9rem; }
.problem { color: var(--p-amber-400); font-size: 0.9rem; }
.cards { max-width: 52rem; }
.picker { margin-top: 0.75rem; }
.cell-row { display: flex; align-items: center; gap: 0.75rem; margin-top: 0.75rem; flex-wrap: wrap; }
.cell-row label { min-width: 6.5rem; color: var(--p-text-color); }
.cell-select { min-width: 16rem; }
</style>
