<script setup lang="ts">
import { ref, watch } from "vue";
import Dialog from "openvue/dialog";
import { endpoints } from "../../api";
import { errorText } from "../../api/client";
import { HEVC_STORE_URL, hevcOnWindows, hevcSupport, mseSupported } from "../../player/hevc";

// One player for saved clips, in a dialog -- only one clip is ever reviewed at a time. Ported from
// the old page's playClip(), including what Deep Archive and an undecodable H.265 clip say.
const props = defineProps<{ s3Key: string | null; title: string }>();
const emit = defineEmits<{ close: [] }>();

const video = ref<HTMLVideoElement | null>(null);
const status = ref("");
const hevcHelp = ref(false);
const visible = ref(false);

function unload(): void {
  const v = video.value;
  if (!v) return;
  v.onerror = null;
  v.pause();
  v.removeAttribute("src");
  v.load();
}

async function play(key: string): Promise<void> {
  visible.value = true;
  hevcHelp.value = false;
  status.value = "Loading clip…";
  unload();
  try {
    const r = await endpoints.playClip(key);
    if (props.s3Key !== key) return;                   // another clip was picked meanwhile
    if (!r.ok && r.status !== 202) { status.value = "Error: " + errorText(r); return; }
    const d = r.data;
    if (d.status === "ready" && d.url) {
      const v = video.value!;
      // A browser without an H.265 decoder fails an H.265 clip with an unreadable error (Firefox:
      // NS_ERROR_DOM_MEDIA_METADATA_ERR; Edge: "no supported source"), so explain it when this
      // browser lacks H.265 rather than repeat the browser's message.
      v.onerror = () => {
        if (!hevcSupport(mseSupported()).clips) {
          status.value = "This clip could not be played. If this camera records in H.265, this browser can't decode it.";
          hevcHelp.value = true;
        } else {
          status.value = `This clip could not be played (media error ${v.error ? v.error.code : "?"}).`;
        }
      };
      v.src = d.url;
      status.value = "";
      v.play().catch((e: Error) => {
        // Only an autoplay refusal means "fine, just press play"; anything else is onerror's.
        if (e.name === "NotAllowedError") status.value = "Loaded — press the player's play button to watch.";
      });
    } else if (d.status === "restore_requested") {
      status.value = `This clip is in Deep Archive and wasn't readable directly. A restore has been requested — ` +
        `check back in about ${d.eta_hours} hours and press Play again.`;
    } else if (d.status === "restoring") {
      status.value = `Restore already in progress — check back in about ${d.eta_hours} hours and press Play again.`;
    }
  } catch (e) {
    status.value = "Network error: " + (e as Error).message;
  }
}

watch(() => props.s3Key, key => { if (key) void play(key); else { unload(); visible.value = false; } });

function onHide(): void {
  unload();
  status.value = "";
  emit("close");
}
</script>

<template>
  <Dialog v-model:visible="visible" modal :header="title" :style="{ width: 'min(60rem, 95vw)' }" @hide="onHide">
    <video ref="video" class="clip-video" controls muted playsinline />
    <p v-if="status" class="clip-status" role="status">{{ status }}</p>
    <p v-if="hevcHelp" class="clip-status">
      <template v-if="hevcOnWindows()">On Windows, install Microsoft's
        <a :href="HEVC_STORE_URL" target="_blank" rel="noopener">HEVC Video Extensions</a>, then restart the browser.</template>
      <template v-else>Open it in a browser or on a device that can decode H.265 (HEVC).</template>
    </p>
  </Dialog>
</template>

<style scoped>
.clip-video { width: 100%; background: #000; display: block; aspect-ratio: 16 / 9; }
.clip-status { margin: 0.6rem 0 0; font-size: 0.9rem; color: var(--p-text-muted-color); }
</style>
