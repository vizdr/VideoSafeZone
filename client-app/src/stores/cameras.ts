import { defineStore } from "pinia";
import { ref } from "vue";
import { endpoints } from "../api";
import { errorText } from "../api/client";
import type { Camera } from "../api/types";

/** The camera list from GET /cameras (the registry), fetched once after sign-in. */
export const useCamerasStore = defineStore("cameras", () => {
  const cameras = ref<Camera[]>([]);
  const loaded = ref(false);
  const loading = ref(false);
  const error = ref<string | null>(null);

  async function load(): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      const r = await endpoints.listCameras();
      if (!r.ok) { error.value = "Could not load the camera list: " + errorText(r); return; }
      cameras.value = r.data.cameras;
      loaded.value = true;
    } catch (e) {
      error.value = "Network error: " + (e as Error).message;
    } finally {
      loading.value = false;
    }
  }

  function byId(id: string | undefined): Camera | undefined {
    return id ? cameras.value.find(c => c.id === id) : undefined;
  }

  function clear(): void {
    cameras.value = [];
    loaded.value = false;
    error.value = null;
  }

  return { cameras, loaded, loading, error, load, byId, clear };
});
