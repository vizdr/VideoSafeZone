<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import Button from "openvue/button";
import type { Camera } from "../../api/types";
import { cameraName } from "../../cameras/list";
import type { Cell } from "../../layouts/grid";
import { hevcSupport, mseSupported } from "../../player/hevc";
import { usePlayersStore } from "../../stores/players";

// One cell of the Live grid (WEB-PANEL-PLAN.md §4.3). While mounted it plays the camera's live view
// -- the `live` command and its keep-alive, all in the camera's controller -- and when it goes (page
// turned, layout changed, tab left) it ends it at once with `live-stop`; the Pi's 5-minute deadline
// is the guarantee if that message is lost. The selected tile carries the old page's controls.
const props = defineProps<{ camera?: Camera; cell: Cell; selected: boolean }>();
const emit = defineEmits<{ select: [] }>();

const players = usePlayersStore();
const root = ref<HTMLElement | null>(null);
const video = ref<HTMLVideoElement | null>(null);
const controller = computed(() => (props.camera ? players.get(props.camera.id) : undefined));
const state = computed(() => (props.camera ? players.states[props.camera.id] : undefined));

// A live view this browser can't decode would cost a push that shows nothing: don't start it.
const liveCodec = computed(() =>
  props.camera?.liveView === "sub" ? props.camera.liveVideoCodec : props.camera?.videoCodec);
const cannotDecode = computed(() => liveCodec.value === "h265" && !hevcSupport(mseSupported()).live);

let connected: ReturnType<typeof players.get>;
function connect(): void {
  const c = controller.value;
  if (!c || !video.value || connected === c) return;
  connected = c;
  c.attach(video.value);
  if (!cannotDecode.value) void c.startLive();
}
onMounted(connect);
watch(controller, connect);                       // the controller arrives after the tile: connect then
onBeforeUnmount(() => {
  if (!connected) return;
  connected.stopLive();
  connected.detach();
});
// Moved to another cell (a swap, a new layout): moving a <video> in the page pauses it.
watch(() => props.cell, () => { video.value?.play().catch(() => {}); });

const overlay = computed(() => {
  if (cannotDecode.value && state.value?.view !== "live") {
    return "This browser can't play this camera's H.265 live view.";    // the page's note says why
  }
  return state.value?.overlay ?? null;
});

// Record clip's running time, ticking while it records.
const now = ref(Date.now());
let ticker: ReturnType<typeof setInterval> | undefined;
watch(() => state.value?.recording, rec => {
  clearInterval(ticker);
  if (rec) ticker = setInterval(() => { now.value = Date.now(); }, 1000);
}, { immediate: true });
onBeforeUnmount(() => clearInterval(ticker));
const recordingFor = computed(() => {
  const since = state.value?.recordingSince;
  return since ? Math.max(0, Math.floor((now.value - since) / 1000)) : 0;
});

const commanding = ref(false);
async function cloud(action: "start" | "stop"): Promise<void> {
  if (!controller.value) return;
  commanding.value = true;
  await controller.value.command(action);
  commanding.value = false;
}
function clip(): void {
  const c = controller.value;
  if (!c) return;
  if (state.value?.recording) void c.stopClip();
  else c.startClip();
}
function fullscreen(): void {
  if (document.fullscreenElement) void document.exitFullscreen();
  else root.value?.requestFullscreen?.().catch(() => {});
}
</script>

<template>
  <div ref="root" class="tile" :class="{ large: cell.span === 2, selected, empty: !camera }"
       :style="{ '--row': cell.row, '--col': cell.col, '--span': cell.span }">
    <template v-if="camera">
      <button type="button" class="surface" :aria-label="`Select ${cameraName(camera)}`" :aria-pressed="selected"
              @click="emit('select')">
        <video ref="video" muted playsinline autoplay />
        <span v-if="overlay" class="overlay">{{ overlay }}</span>
      </button>
      <div class="label">
        <span class="dot" :class="{ on: state?.archiving, rec: state?.recording }"
              :title="state?.archiving ? 'Recording to the cloud' : 'Not recording to the cloud'" />
        {{ cameraName(camera) }}
      </div>

      <div v-if="selected && state" class="controls" @click.stop>
        <Button size="small" text rounded :icon="state.view === 'live' ? 'pi pi-stop' : 'pi pi-play'"
                :aria-label="state.view === 'live' ? 'End live' : 'Live'"
                :title="state.view === 'live' ? 'End live (stops sending this camera)' : 'Watch live, without recording'"
                @click="controller?.toggleLive()" />
        <Button size="small" :text="!state.archiving" rounded icon="pi pi-cloud-upload"
                :severity="state.archiving ? 'danger' : 'secondary'" :disabled="commanding"
                :aria-label="state.archiving ? 'Stop cloud recording' : 'Start cloud recording'"
                :title="state.archiving ? 'Stop recording to the cloud' : 'Record to the cloud -- costs while it runs'"
                @click="cloud(state.archiving || state.archivePending ? 'stop' : 'start')" />
        <Button size="small" rounded :text="!state.recording" icon="pi pi-circle-fill"
                :severity="state.recording ? 'danger' : 'secondary'"
                :label="state.recording ? `${recordingFor} s` : undefined"
                :disabled="!state.archiving && !state.recording"
                :aria-label="state.recording ? 'Stop recording the clip' : 'Record clip'"
                :title="state.archiving || state.recording ? 'Record clip' : 'Record clip needs cloud recording on'"
                @click="clip" />
        <Button size="small" text rounded :icon="state.listen ? 'pi pi-volume-up' : 'pi pi-volume-off'"
                :disabled="!state.listenAvailable"
                :aria-label="state.listen ? 'Mute here' : 'Play sound here'"
                :title="state.listenAvailable ? 'Sound in this browser only' : 'No sound in this stream'"
                @click="controller?.setListen(!state.listen)" />
        <Button size="small" text rounded icon="pi pi-refresh" aria-label="Reload player" title="Reload player"
                @click="controller?.reload()" />
        <Button size="small" text rounded icon="pi pi-window-maximize" aria-label="Full screen" title="Full screen"
                @click="fullscreen" />
        <span class="status">{{ state.clipStatus || state.status }}</span>
      </div>
    </template>
    <span v-else class="none">No camera</span>
  </div>
</template>

<style scoped>
.tile {
  position: relative;
  grid-row: var(--row) / span var(--span);
  grid-column: var(--col) / span var(--span);
  min-width: 0;
  min-height: 0;
  background: #000;
  border-radius: 0.35rem;
  overflow: hidden;
  outline: 2px solid transparent;
}
.tile.selected { outline-color: var(--p-primary-color); }
.tile.empty { background: var(--p-surface-900); display: grid; place-items: center; }
.none { color: var(--p-text-muted-color); font-size: 0.8rem; }
.surface { all: unset; position: absolute; inset: 0; cursor: pointer; }
.surface:focus-visible { outline: 2px solid var(--p-primary-color); outline-offset: -2px; }
video { width: 100%; height: 100%; object-fit: contain; display: block; }
.overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0.75rem;
  text-align: center;
  white-space: pre-line;
  color: #ddd;
  font-size: 0.8rem;
  background: rgba(0, 0, 0, 0.75);
}
.large .overlay { font-size: 0.95rem; }
.label {
  position: absolute;
  left: 0.4rem;
  top: 0.35rem;
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.1rem 0.45rem;
  border-radius: 0.3rem;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 0.75rem;
  pointer-events: none;
}
.dot { width: 0.5rem; height: 0.5rem; border-radius: 50%; background: var(--p-surface-500); }
.dot.on { background: var(--p-green-400); }
.dot.rec { background: var(--p-red-500); }
.controls {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  align-items: center;
  gap: 0.15rem;
  padding: 0.2rem 0.4rem;
  background: linear-gradient(transparent, rgba(0, 0, 0, 0.85));
  color: #fff;
}
.controls :deep(.p-button) { color: #fff; }
.status { margin-left: 0.4rem; font-size: 0.72rem; opacity: 0.85; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.tile:fullscreen { border-radius: 0; outline: none; }

@media (max-width: 800px) {
  .tile { grid-row: auto; grid-column: span 1; aspect-ratio: 16 / 9; }
  .tile.large { grid-column: span 2; }
}
</style>
