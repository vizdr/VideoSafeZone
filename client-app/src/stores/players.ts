import { defineStore } from "pinia";
import { markRaw, shallowRef } from "vue";
import { endpoints } from "../api";
import type { Camera } from "../api/types";
import { CameraController, type PlayerState } from "../player/camera-controller";

/** One CameraController per camera, made when the camera list arrives. Each runs the archive
 *  check from the start, as the old page did at sign-in: it feeds the list's status dots and the
 *  Cloud recording switch. The controllers stay outside Vue's reactivity (markRaw); their state
 *  snapshots are what the views read. */
export const usePlayersStore = defineStore("players", () => {
  const controllers = markRaw(new Map<string, CameraController>());
  const states = shallowRef<Record<string, PlayerState>>({});

  function sync(cameras: Camera[]): void {
    const ids = new Set(cameras.map(c => c.id));
    for (const [id, c] of controllers) {
      if (!ids.has(id)) { c.dispose(); controllers.delete(id); }
    }
    for (const cam of cameras) {
      const existing = controllers.get(cam.id);
      if (existing) { existing.setCamera(cam); continue; }
      const c = markRaw(new CameraController(cam, { api: { hls: endpoints.hls, cmd: endpoints.cmd } }));
      controllers.set(cam.id, c);
      c.subscribe(s => { states.value = { ...states.value, [cam.id]: s }; });
      states.value = { ...states.value, [cam.id]: c.state };
      c.startArchive();
    }
    states.value = Object.fromEntries(Object.entries(states.value).filter(([id]) => ids.has(id)));
  }

  function get(id: string): CameraController | undefined {
    return controllers.get(id);
  }

  /** Signing out: every controller goes, ending any Live view (dispose sends live-stop). */
  function clear(): void {
    for (const c of controllers.values()) c.dispose();
    controllers.clear();
    states.value = {};
  }

  // The old page's two document listeners, once for the app.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") for (const c of controllers.values()) c.onVisible();
  });
  window.addEventListener("pagehide", () => {
    for (const c of controllers.values()) c.onPageHide();
  });

  return { states, sync, get, clear };
});
