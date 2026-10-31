<script setup lang="ts">
import { computed, ref } from "vue";
import Button from "openvue/button";
import ToggleSwitch from "openvue/toggleswitch";
import { endpoints } from "../../api";
import { errorText } from "../../api/client";
import type { Camera, IrMode } from "../../api/types";
import { codecLabel } from "../../cameras/list";
import { IR_MODES, audioHint, audioSaved } from "../../cameras/settings";
import { useSavedSetting, type SettingStatus } from "../../composables/useSavedSetting";
import { hevcSupport, mseSupported } from "../../player/hevc";
import { useCamerasStore } from "../../stores/cameras";
import { usePlayersStore } from "../../stores/players";
import SettingCard from "./SettingCard.vue";
import SettingRow from "./SettingRow.vue";

const props = defineProps<{ camera: Camera }>();
const cameras = useCamerasStore();
const players = usePlayersStore();
const id = computed(() => props.camera.id);

// Video: display only. The codec is set on the camera, in the admin GUI (guide §21.3); here it
// matters for whether this browser can play the camera at all.
const canPlayHevc = hevcSupport(mseSupported()).live;
const recordedHevcWarning = computed(() => props.camera.videoCodec === "h265" && !canPlayHevc);
const liveHevcWarning = computed(() => props.camera.liveView === "sub"
  && props.camera.liveVideoCodec === "h265" && props.camera.videoCodec !== "h265" && !canPlayHevc);

// "Record microphone": whether audio exists in the cloud stream at all -- for everyone, in Live and
// in every clip, from the next Start. ("Play sound here", this browser only, is the Live player's.)
const audio = useSavedSetting<boolean>({
  read: () => props.camera.audioEnabled,
  apply: v => cameras.patch(id.value, { audioEnabled: v }),
  send: v => endpoints.setAudio(id.value, v),
  saved: v => audioSaved(v, !!players.states[id.value]?.archiving),
});
// No microphone: the description already says so.
const audioStatus = computed<SettingStatus | null>(() => audio.status.value
  ?? (props.camera.audioCapable ? { text: audioHint(props.camera.audioEnabled, true), tone: "info" } : null));

// IR: a command to the Pi, not a stored setting -- the registry doesn't hold the current mode, so
// none is shown as selected.
const irBusy = ref(false);
const irStatus = ref<SettingStatus | null>(null);
async function setIr(mode: IrMode, label: string): Promise<void> {
  irBusy.value = true;
  irStatus.value = { text: `sending ${label}…`, tone: "info" };
  try {
    const r = await endpoints.setIrMode(id.value, mode);
    irStatus.value = r.ok
      ? { text: `Sent to the Pi: ${label}.`, tone: "ok" }
      : { text: "Not sent: " + errorText(r), tone: "error" };
  } catch (e) {
    irStatus.value = { text: "Not sent: " + (e as Error).message, tone: "error" };
  } finally {
    irBusy.value = false;
  }
}
</script>

<template>
  <SettingCard icon="pi-video">
    <SettingRow title="Video">
      {{ codecLabel(camera) }} · set on the camera, in the admin GUI.
      <p v-if="recordedHevcWarning" class="warn">
        This browser can't play H.265 video — live view and clips of this camera won't show here.
        See the note at the top of the page.
      </p>
      <p v-if="liveHevcWarning" class="warn">
        This camera's live view (its sub stream) is H.265, which this browser can't play — Live
        won't show here, recordings will.
      </p>
    </SettingRow>
  </SettingCard>

  <SettingCard icon="pi-volume-up">
    <SettingRow title="Record microphone" :label-for="`audio-${camera.id}`" :status="audioStatus">
      <template v-if="camera.audioCapable">
        Into the live view and clips, for everyone. Applies on the next Start.
      </template>
      <template v-else>This camera has no microphone.</template>
      <template #control>
        <ToggleSwitch :input-id="`audio-${camera.id}`" :model-value="camera.audioEnabled"
                      :disabled="!camera.audioCapable || audio.busy.value"
                      @update:model-value="audio.save" />
      </template>
    </SettingRow>
  </SettingCard>

  <SettingCard v-if="camera.hasIrControl" icon="pi-sun">
    <SettingRow title="IR illumination" :status="irStatus">
      Day keeps colour with the IR lights off; Night switches to black-and-white with IR on.
      <template #control>
        <div class="ir-buttons" role="group" aria-label="IR illumination">
          <Button v-for="m in IR_MODES" :key="m.value" :label="m.label" :title="m.title"
                  size="small" severity="secondary" :disabled="irBusy"
                  @click="setIr(m.value, m.label)" />
        </div>
      </template>
    </SettingRow>
  </SettingCard>
</template>

<style scoped>
.warn { color: var(--p-amber-400); margin: 0.4rem 0 0; }
.ir-buttons { display: flex; gap: 0.35rem; flex-wrap: wrap; }
</style>
