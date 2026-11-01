<script setup lang="ts">
import { computed } from "vue";
import Select from "openvue/select";
import { endpoints } from "../../api";
import type { Camera } from "../../api/types";
import { useSavedSetting, type SettingStatus } from "../../composables/useSavedSetting";
import { useCamerasStore } from "../../stores/cameras";
import SettingCard from "./SettingCard.vue";
import SettingRow from "./SettingRow.vue";

// Recorded quality (WEB-PANEL-PLAN.md D5): the admin GUI's choice is the ceiling, and the cloud may
// only go lower -- one step, the sub stream, which the Pi already pulls for live view. The frame
// rate stays in the admin GUI. The producer reads the choice when it starts (stream-codec.py), so
// it applies from the next Start, and records what it pulled (recordQualityActive).
const props = defineProps<{ camera: Camera }>();
const cameras = useCamerasStore();

const fixed = computed(() => props.camera.mode === "transcode" || !props.camera.supportsDetection);
const ceiling = computed(() => props.camera.recordStream ?? null);
const options = computed(() => (props.camera.recordQualityOptions ?? []).map(v => ({ value: v, label: streamLabel(v) })));
const choice = computed(() => props.camera.recordQuality?.stream ?? ceiling.value);

function streamLabel(stream: string): string {
  if (stream === "sub") return "Sub stream (as in Live)";
  const parts = [stream === "main" ? "Main stream" : stream];
  if (stream === ceiling.value) {
    if (props.camera.recordSize) parts.push(props.camera.recordSize);
    if (props.camera.recordFps) parts.push(`${props.camera.recordFps} fps`);
  }
  return parts.join(" · ");
}

const save = useSavedSetting<string | null>({
  read: () => choice.value,
  apply: v => cameras.patch(props.camera.id, { recordQuality: !v || v === ceiling.value ? null : { stream: v } }),
  send: v => endpoints.setCameraSettings(props.camera.id,
                                         { recordQuality: !v || v === ceiling.value ? null : { stream: v } }),
  saved: () => "Saved. Applies from the next Start of cloud recording.",
});
const active = computed(() => props.camera.recordQualityActive);
const activeLabel = computed(() => active.value?.stream
  ? `${active.value.stream === "sub" ? "Sub stream" : active.value.stream === "main" ? "Main stream" : "the camera's stream"}${active.value.size ? ` · ${active.value.size}` : ""}`
  : null);
const status = computed<SettingStatus | null>(() => save.status.value);
</script>

<template>
  <SettingCard icon="pi-sliders-h">
    <SettingRow title="Recorded quality" :label-for="`quality-${camera.id}`" :status="status">
      <template v-if="fixed">Fixed: 720p at 15 fps, encoded on the Pi.</template>
      <template v-else-if="!ceiling">The admin GUI hasn't recorded this camera's stream yet: open its stream
        settings there once, and the choice appears here.</template>
      <template v-else>
        At most {{ streamLabel(ceiling) }}, set in the admin GUI with its frame rate.
        <template v-if="options.length > 1">The sub stream records a smaller picture, for less.
          Applies from the next Start.</template>
        <template v-else>Nothing lower is offered: {{ ceiling === "sub" ? "the camera already records its sub stream" : "the camera has no sub stream" }}.</template>
        <p v-if="activeLabel" class="active">Last cloud recording: {{ activeLabel }}.</p>
      </template>
      <template v-if="options.length > 1" #control>
        <Select :input-id="`quality-${camera.id}`" :model-value="choice" :options="options" option-label="label"
                option-value="value" :disabled="save.busy.value" class="wide" @update:model-value="save.save" />
      </template>
    </SettingRow>
  </SettingCard>
</template>

<style scoped>
.wide { min-width: 13rem; }
.active { margin: 0.35rem 0 0; }
</style>
