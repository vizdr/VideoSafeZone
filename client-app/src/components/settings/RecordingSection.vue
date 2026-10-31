<script setup lang="ts">
import { computed } from "vue";
import Select from "openvue/select";
import { endpoints } from "../../api";
import type { Camera, RecordingMode } from "../../api/types";
import {
  MODE_OPTIONS, OUTAGE_LIMITS, modeUnavailable, outageHint, recModeHint, triggerNote,
} from "../../cameras/settings";
import { useSavedSetting, type SettingStatus } from "../../composables/useSavedSetting";
import { useCamerasStore } from "../../stores/cameras";
import SettingCard from "./SettingCard.vue";
import SettingRow from "./SettingRow.vue";

const props = defineProps<{ camera: Camera }>();
const cameras = useCamerasStore();
const id = computed(() => props.camera.id);

// Recording mode: the event watcher on the Pi re-reads the registry and re-subscribes within ~60 s,
// so a change here really does change what gets recorded.
const modeOptions = computed(() => MODE_OPTIONS.map(o => ({
  ...o, reason: modeUnavailable(props.camera, o.value), disabled: !!modeUnavailable(props.camera, o.value),
})));
const mode = useSavedSetting<RecordingMode>({
  read: () => props.camera.recordingMode,
  apply: v => cameras.patch(id.value, { recordingMode: v }),
  send: v => endpoints.setRecordingMode(id.value, v),
  saved: v => "Saved — " + recModeHint(v, true),
});
const modeStatus = computed<SettingStatus>(() =>
  mode.status.value ?? { text: recModeHint(props.camera.recordingMode, props.camera.supportsDetection), tone: "info" });
const note = computed(() => triggerNote(props.camera));

// Outage buffer: no restart needed -- the supervisor on the Pi re-reads the registry every 60 s and
// arms or disarms recording in place. It can't buffer a camera that isn't streaming: with no
// producer there is no cloud stream to protect.
const outage = useSavedSetting<number>({
  read: () => props.camera.outageBufferSec,
  apply: v => cameras.patch(id.value, { outageBufferSec: v }),
  send: v => endpoints.setOutageBuffer(id.value, v),
  saved: v => `Saved — ${outageHint(v)}. Takes effect within a minute.`,
});
const outageStatus = computed<SettingStatus>(() =>
  outage.status.value ?? { text: outageHint(props.camera.outageBufferSec), tone: "info" });
</script>

<template>
  <SettingCard icon="pi-bolt">
    <SettingRow title="Recording mode" :label-for="`mode-${camera.id}`" :status="modeStatus">
      What makes a clip: only Record clip, or a detection the camera reports.
      <p v-if="camera.supportsDetection" class="note">
        Some triggers fire only after they are switched on in the camera's own settings<template
          v-if="note.webUrl"> (<a :href="note.webUrl" target="_blank" rel="noopener">open</a>, home network
          only)</template>. Test them with “Test triggers” in the admin GUI.
        <template v-if="note.lastTests.length"> Last test: {{ note.lastTests.join(" · ") }}.</template>
      </p>
      <template #control>
        <Select :input-id="`mode-${camera.id}`" :model-value="camera.recordingMode" :options="modeOptions"
                option-label="label" option-value="value" option-disabled="disabled"
                :disabled="!camera.supportsDetection || mode.busy.value" class="wide"
                @update:model-value="mode.save">
          <template #option="{ option }">
            <span>{{ option.label }}<small v-if="option.reason" class="reason"> — {{ option.reason }}</small></span>
          </template>
        </Select>
      </template>
    </SettingRow>
  </SettingCard>

  <SettingCard icon="pi-wifi">
    <SettingRow title="If the internet drops" :label-for="`outage-${camera.id}`" :status="outageStatus">
      Keeps recording to the USB drive while AWS is unreachable, then uploads it to Events when the
      connection returns. The 2 minutes before the drop are always included on top of the time
      you choose. Only runs while Cloud recording is on.
      <template #control>
        <Select :input-id="`outage-${camera.id}`" :model-value="camera.outageBufferSec" :options="OUTAGE_LIMITS"
                option-label="label" option-value="sec" :disabled="outage.busy.value" class="wide"
                @update:model-value="outage.save" />
      </template>
    </SettingRow>
  </SettingCard>
</template>

<style scoped>
.note { margin: 0.4rem 0 0; }
.reason { color: var(--p-text-muted-color); }
.wide { min-width: 13rem; }
</style>
