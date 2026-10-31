<script setup lang="ts">
import { computed, ref } from "vue";
import ToggleSwitch from "openvue/toggleswitch";
import type { Camera } from "../../api/types";
import { CLOUD_RECORDING_HINT } from "../../cameras/settings";
import type { SettingStatus } from "../../composables/useSavedSetting";
import { usePlayersStore } from "../../stores/players";
import SettingRow from "./SettingRow.vue";

// Start / Stop as a switch. What it shows is KVS's own answer -- footage reaching the archive
// (the archive check, FoundAndFixed.md #83) -- not what was last clicked, so a Start or Stop from
// the admin GUI or another browser shows up here within 30 s.
const props = defineProps<{ camera: Camera }>();
const players = usePlayersStore();
const state = computed(() => players.states[props.camera.id]);
const busy = ref(false);
const requested = ref(false);
const refused = ref<string | null>(null);

const on = computed(() => busy.value
  ? requested.value
  : !!state.value && (state.value.archiving || state.value.archivePending));

async function toggle(v: boolean): Promise<void> {
  const c = players.get(props.camera.id);
  if (!c || busy.value) return;
  busy.value = true;
  requested.value = v;
  refused.value = null;
  const ok = await c.command(v ? "start" : "stop");
  busy.value = false;
  if (!ok) refused.value = c.state.status.replace(/^error: /, "");
}

const status = computed<SettingStatus>(() => {
  const s = state.value;
  if (busy.value) return { text: requested.value ? "sending Start…" : "sending Stop…", tone: "info" };
  if (refused.value) return { text: "Not sent: " + refused.value, tone: "error" };
  if (!s) return { text: "", tone: "info" };
  if (s.archivePending) return { text: "Starting — waiting for the first footage to reach the cloud (up to 45 s).", tone: "info" };
  if (s.archiving) return { text: "On — footage is reaching the cloud archive.", tone: "ok" };
  if (s.archiveFailed) {
    return { text: "No footage reached the cloud within 45 s. The Pi didn't answer: it may be offline " +
                   "from AWS, or the camera unreachable. Try again in a minute.", tone: "error" };
  }
  return { text: "Off — nothing is sent to the cloud.", tone: "info" };
});
</script>

<template>
  <div class="cloud-recording">
    <SettingRow title="Cloud recording" :label-for="`cloud-${camera.id}`" :status="status">
      {{ CLOUD_RECORDING_HINT }}
      <template #control>
        <ToggleSwitch :input-id="`cloud-${camera.id}`" :model-value="on" :disabled="busy || !state"
                      @update:model-value="toggle" />
      </template>
    </SettingRow>
  </div>
</template>

<style scoped>
.cloud-recording {
  background: var(--p-surface-700);
  border-radius: 0.5rem;
  padding: 0.3rem 1rem;
  margin: 1rem 0;
}
</style>
