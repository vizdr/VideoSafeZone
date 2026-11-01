<script setup lang="ts">
import { computed, ref } from "vue";
import Button from "openvue/button";
import InputText from "openvue/inputtext";
import { endpoints } from "../../api";
import type { Camera } from "../../api/types";
import { normalizeTag, tagProblem } from "../../cameras/tags";
import { useSavedSetting } from "../../composables/useSavedSetting";
import { useCamerasStore } from "../../stores/cameras";
import SettingCard from "./SettingCard.vue";
import SettingRow from "./SettingRow.vue";

// Tags group the camera list on the left by their first tag (WEB-PANEL-PLAN.md §4.1). The whole
// list is saved on each change; the old list comes back if the server refuses.
const props = defineProps<{ camera: Camera }>();
const cameras = useCamerasStore();
const tags = computed(() => props.camera.tags ?? []);
const typed = ref("");
const problem = ref<string | null>(null);

const save = useSavedSetting<string[]>({
  read: () => tags.value,
  apply: v => cameras.patch(props.camera.id, { tags: v }),
  send: v => endpoints.setCameraSettings(props.camera.id, { tags: v }),
  saved: () => "Saved. The camera list groups cameras by their first tag.",
});

function add(): void {
  const tag = normalizeTag(typed.value);
  problem.value = tagProblem(tag, tags.value);
  if (problem.value) return;
  typed.value = "";
  void save.save([...tags.value, tag]);
}
function remove(tag: string): void {
  void save.save(tags.value.filter(t => t !== tag));
}
</script>

<template>
  <SettingCard icon="pi-tag">
    <SettingRow title="Tags" :label-for="`tag-${camera.id}`" :status="problem ? { text: problem, tone: 'error' } : save.status.value">
      Labels such as a place or a building; the camera list on the left is grouped by the first one.
      <div class="chips">
        <span v-for="t in tags" :key="t" class="chip">
          {{ t }}
          <button type="button" :aria-label="`Remove tag ${t}`" :disabled="save.busy.value" @click="remove(t)">×</button>
        </span>
        <span v-if="!tags.length" class="muted">No tags yet.</span>
      </div>
      <form class="add" @submit.prevent="add">
        <InputText :id="`tag-${camera.id}`" v-model="typed" size="small" placeholder="New tag" maxlength="40"
                   :disabled="save.busy.value" />
        <Button type="submit" label="Add" size="small" severity="secondary" :disabled="save.busy.value" />
      </form>
    </SettingRow>
  </SettingCard>
</template>

<style scoped>
.chips { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.6rem; }
.chip {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.15rem 0.3rem 0.15rem 0.6rem;
  border-radius: 1rem;
  background: var(--p-surface-900);
  color: var(--p-text-color);
  font-size: 0.82rem;
}
.chip button {
  border: 0;
  background: none;
  color: var(--p-text-muted-color);
  font-size: 1rem;
  line-height: 1;
  cursor: pointer;
  padding: 0 0.2rem;
}
.chip button:hover { color: var(--p-text-color); }
.add { display: flex; gap: 0.4rem; margin-top: 0.6rem; }
</style>
