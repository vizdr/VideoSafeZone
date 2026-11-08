<script setup lang="ts">
// An unregistered camera's Settings (the user, 2026-11-08): it stays in the camera list, out of
// Live, recording nothing; its recordings stay in Events. Re-register brings it back as it was --
// the USB camera too -- from what the registry holds.
import { computed, onUnmounted, ref } from "vue";
import Button from "openvue/button";
import type { Camera } from "../../api/types";
import { canManage, changeRegistration } from "../../cameras/registration";
import type { SettingStatus } from "../../composables/useSavedSetting";
import { useAuthStore } from "../../stores/auth";
import SettingCard from "./SettingCard.vue";
import SettingRow from "./SettingRow.vue";

const props = defineProps<{ camera: Camera }>();
const auth = useAuthStore();
const allowed = computed(() => canManage(props.camera, auth.role));
const busy = ref(false), status = ref<SettingStatus | null>(null);
let alive = true;
onUnmounted(() => { alive = false; });
const since = computed(() => (props.camera.unregisteredAt ? new Date(props.camera.unregisteredAt).toLocaleString() : ""));

async function reregister(): Promise<void> {
  busy.value = true;
  status.value = { text: "Asked the adapter: it comes back in a moment…", tone: "info" };
  try {
    const problem = await changeRegistration(props.camera, true, () => !alive);
    status.value = problem ? { text: problem, tone: "error" } : { text: "Back. Press Start to record it again.", tone: "ok" };
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <SettingCard icon="pi-eye-slash">
    <SettingRow title="Unregistered" :status="status">
      Since {{ since }}. It isn't in Live and records nothing; its recordings stay in Events, and its
      settings are kept for when it comes back.
      <template #control>
        <Button v-if="allowed" size="small" label="Re-register" :loading="busy" data-test="reregister" @click="reregister" />
      </template>
    </SettingRow>
  </SettingCard>
</template>
