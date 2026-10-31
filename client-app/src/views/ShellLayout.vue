<script setup lang="ts">
import { computed, onMounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppRail from "../components/AppRail.vue";
import CameraList from "../components/CameraList.vue";
import { useAuthStore } from "../stores/auth";
import { useCamerasStore } from "../stores/cameras";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const cameras = useCamerasStore();

const cameraId = computed(() =>
  typeof route.params.cameraId === "string" ? route.params.cameraId : undefined);

onMounted(() => {
  if (!cameras.loaded && !cameras.loading) void cameras.load();
});

// Signed out from anywhere -- Exit, or a refresh Cognito refused: back to sign-in.
watch(() => auth.signedIn, signedIn => {
  if (signedIn) return;
  cameras.clear();
  void router.replace({ name: "login" });
});

function select(id: string): void {
  void router.push({ name: route.name ?? "live", params: { ...route.params, cameraId: id } });
}
</script>

<template>
  <div class="shell">
    <AppRail />
    <aside class="cameras-col">
      <CameraList :selected="cameraId" @select="select" />
    </aside>
    <main class="content">
      <RouterView />
    </main>
  </div>
</template>
