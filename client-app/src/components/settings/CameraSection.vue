<script setup lang="ts">
import { computed, ref } from "vue";
import Button from "openvue/button";
import ToggleSwitch from "openvue/toggleswitch";
import { endpoints } from "../../api";
import { errorText } from "../../api/client";
import type { Camera, IrMode } from "../../api/types";
import { cloudCodec, codecLabel } from "../../cameras/list";
import { IR_MODES, audioHint, audioSaved } from "../../cameras/settings";
import { useSavedSetting, type SettingStatus } from "../../composables/useSavedSetting";
import { hevcSupport, mseSupported } from "../../player/hevc";
import { canManage, changeRegistration } from "../../cameras/registration";
import { useAuthStore } from "../../stores/auth";
import { useCamerasStore } from "../../stores/cameras";
import { usePlayersStore } from "../../stores/players";
import SettingCard from "./SettingCard.vue";
import SettingRow from "./SettingRow.vue";
import QualityCard from "./QualityCard.vue";
import TagsCard from "./TagsCard.vue";

const props = defineProps<{ camera: Camera }>();
const cameras = useCamerasStore();
const players = usePlayersStore();
const id = computed(() => props.camera.id);

// Video: display only. The codec is set on the camera, in the admin GUI (guide §21.3); here it
// matters for whether this browser can play the camera at all.
const canPlayHevc = hevcSupport(mseSupported()).live;
const recordedHevcWarning = computed(() => cloudCodec(props.camera) === "h265" && !canPlayHevc);
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

// Unregister (DEALER-PANEL-PLAN.md D16; the user, 2026-11-08): whoever controls the camera on an
// account asks its adapter to unregister it, the soft mark -- any camera, the USB camera too. It
// leaves Live and the camera list (this page then points to Cloud adapters → Cameras, the one place
// that lists it); its recordings and settings stay, and Re-register there brings it back as it was.
const auth = useAuthStore();
const canRemove = computed(() => canManage(props.camera, auth.role));
const confirmRemove = ref(false), removing = ref(false);
const removeStatus = ref<SettingStatus | null>(null);
async function removeCamera(): Promise<void> {
  removing.value = true;
  removeStatus.value = { text: "Asked the adapter…", tone: "info" };
  try {
    const problem = await changeRegistration(props.camera, false);
    removeStatus.value = problem ? { text: problem, tone: "error" } : { text: "Unregistered.", tone: "ok" };
  } finally {
    removing.value = false;
    confirmRemove.value = false;
  }
}
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
  <TagsCard :camera="camera" />

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

  <QualityCard :camera="camera" />

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

  <SettingCard v-if="canRemove" icon="pi-trash">
    <SettingRow title="Unregister camera" :status="removeStatus">
      It leaves Live and the camera list. Its recordings and settings stay, and Re-register under
      Cloud adapters → Cameras brings it back as it was.
      <template #control>
        <div class="remove-buttons">
          <Button v-if="!confirmRemove" size="small" severity="danger" variant="outlined" label="Unregister"
                  data-test="remove-camera" @click="confirmRemove = true" />
          <template v-else>
            <Button size="small" severity="danger" label="Yes, unregister it" :loading="removing" data-test="confirm-remove-camera"
                    @click="removeCamera" />
            <Button size="small" severity="secondary" variant="text" label="Keep it" @click="confirmRemove = false" />
          </template>
        </div>
      </template>
    </SettingRow>
  </SettingCard>
</template>

<style scoped>
.remove-buttons { display: flex; gap: 0.35rem; flex-wrap: wrap; }
.warn { color: var(--p-amber-400); margin: 0.4rem 0 0; }
.ir-buttons { display: flex; gap: 0.35rem; flex-wrap: wrap; }
</style>
