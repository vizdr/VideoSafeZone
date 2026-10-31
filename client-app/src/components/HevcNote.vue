<script setup lang="ts">
import { computed } from "vue";
import Message from "openvue/message";
import { HEVC_STORE_URL, hevcOnWindows, hevcSupport, mseSupported } from "../player/hevc";
import { useCamerasStore } from "../stores/cameras";
import { usePlayersStore } from "../stores/players";

// The old page's note at the top: shown up front, before the viewer meets a black player, when a
// camera records in H.265 and this browser can't decode it -- or when a player found that out.
// Amber, not red: nothing is wrong with the camera or the cloud; the fix is on the viewer's machine.
const cameras = useCamerasStore();
const players = usePlayersStore();
const canPlay = hevcSupport(mseSupported()).live;
const windows = hevcOnWindows();
const show = computed(() =>
  (!canPlay && cameras.cameras.some(c => c.videoCodec === "h265"))
  || Object.values(players.states).some(s => s.hevcHelp));
</script>

<template>
  <Message v-if="show" severity="warn" class="hevc-note">
    <strong>This browser can't play H.265 video.</strong>
    <template v-if="windows">
      On Windows, install Microsoft's
      <a :href="HEVC_STORE_URL" target="_blank" rel="noopener">HEVC Video Extensions</a>
      (German Windows: „HEVC-Videoerweiterungen“) from the Microsoft Store, then restart the browser —
      Firefox and Edge need it to decode H.265. Cameras recording in H.264 are not affected.
    </template>
    <template v-else>
      Open this page in a browser or on a device that can decode H.265 (HEVC). Cameras recording in
      H.264 are not affected.
    </template>
  </Message>
</template>

<style scoped>
.hevc-note { margin-bottom: 1.25rem; }
.hevc-note strong { display: block; margin-bottom: 0.2rem; }
</style>
