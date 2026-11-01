<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import Button from "openvue/button";
import Message from "openvue/message";
import Select from "openvue/select";
import { endpoints } from "../../api";
import { errorText } from "../../api/client";
import type { Camera, Clip, StorageTier } from "../../api/types";
import { cameraName } from "../../cameras/list";
import {
  CLIPS_PAGE_SIZE, TIER_CHOICES, TIER_LABEL, clipCodecName, clipPageNumbers, formatDuration, targetPage,
  tierChoice, type PageRequest,
} from "../../clips/format";
import { hevcSupport, mseSupported } from "../../player/hevc";
import ClipPlayer from "./ClipPlayer.vue";

// One camera's evidence clips (WEB-PANEL-PLAN.md §4.4), ported from the old page's loadClips():
// a page at a time, newest first, with tier, delete, play and the "new clips" notice.
const props = defineProps<{ camera: Camera }>();

const clips = ref<Clip[]>([]);
const page = ref(1);
const pages = ref(1);
const total = ref(0);
const loading = ref(false);
const error = ref<string | null>(null);
const rowStatus = ref<Record<string, string>>({});
const tierPick = ref<Record<string, StorageTier>>({});
const playing = ref<{ key: string; title: string } | null>(null);
const newClips = ref(false);
let renderedLatest: string | null = null;
let req = 0;
const canPlayHevc = hevcSupport(mseSupported()).clips;

// Only the latest request may render: a slow response must not overwrite a newer page.
async function load(p: PageRequest = "current"): Promise<void> {
  const my = ++req;
  loading.value = true;
  try {
    const r = await endpoints.listClips(props.camera.id, CLIPS_PAGE_SIZE, targetPage(p, page.value));
    if (my !== req) return;
    if (!r.ok) { error.value = "Error: " + errorText(r); return; }
    // Taken from the response only, so a failed load leaves the viewer where they were.
    page.value = r.data.page || 1;
    pages.value = r.data.pages || 1;
    total.value = r.data.total ?? r.data.clips.length;
    clips.value = r.data.clips.slice(0, CLIPS_PAGE_SIZE);
    tierPick.value = Object.fromEntries(clips.value.map(c => [c.s3Key, tierChoice(c.tier)]));
    rowStatus.value = {};
    error.value = null;
    if (page.value === 1) {
      // Remember the newest clip, so the poll below can spot arrivals; the newest page is on screen.
      renderedLatest = clips.value.length ? clips.value[0].startTs : null;
      newClips.value = false;
    }
  } catch (e) {
    if (my === req) error.value = "Error: " + (e as Error).message;
  } finally {
    if (my === req) loading.value = false;
  }
}
onMounted(() => void load("first"));

// Detection-made clips arrive without the page doing anything, so poll -- only to *notice* them and
// say so, never reloading the list underneath the viewer (a tier choice or a scroll would reset).
let poll: ReturnType<typeof setInterval> | undefined;
async function checkForNewClips(): Promise<void> {
  try {
    const r = await endpoints.listClips(props.camera.id, 1, 1);    // only the newest matters
    const newest = r.ok && r.data.clips.length ? r.data.clips[0].startTs : null;
    if (newest && newest !== renderedLatest) newClips.value = true;
  } catch { /* transient; the next tick asks again */ }
}
watch(() => props.camera.recordingMode, mode => {
  clearInterval(poll);
  if (mode && mode !== "manual") poll = setInterval(() => void checkForNewClips(), 30_000);
}, { immediate: true });
onBeforeUnmount(() => clearInterval(poll));

const when = (c: Clip) => new Date(c.startTs).toLocaleString();

async function applyTier(c: Clip): Promise<void> {
  rowStatus.value = { ...rowStatus.value, [c.s3Key]: "Applying…" };
  try {
    const r = await endpoints.setClipTier(c.s3Key, tierPick.value[c.s3Key]);
    if (!r.ok) { rowStatus.value = { ...rowStatus.value, [c.s3Key]: errorText(r) }; return; }
    await load("current");                           // stay on the page being viewed
  } catch (e) {
    rowStatus.value = { ...rowStatus.value, [c.s3Key]: "Network error: " + (e as Error).message };
  }
}

async function remove(c: Clip): Promise<void> {
  if (!window.confirm(`Delete the clip from ${when(c)}?\n\nThis cannot be undone.`)) return;
  rowStatus.value = { ...rowStatus.value, [c.s3Key]: "Deleting…" };
  try {
    const r = await endpoints.deleteClip(c.s3Key, c.startTs);
    if (!r.ok) { rowStatus.value = { ...rowStatus.value, [c.s3Key]: "Error: " + errorText(r) }; return; }
    if (playing.value?.key === c.s3Key) playing.value = null;
    // The page refills from older clips; a page that no longer exists comes back as the last one.
    await load("current");
  } catch (e) {
    rowStatus.value = { ...rowStatus.value, [c.s3Key]: "Network error: " + (e as Error).message };
  }
}

const numbers = computed(() => clipPageNumbers(page.value, pages.value));
</script>

<template>
  <div class="clip-list">
    <div class="list-head">
      <span class="muted">{{ total }} clip{{ total === 1 ? "" : "s" }}</span>
      <Button label="Refresh" icon="pi pi-refresh" size="small" text :disabled="loading" @click="load('first')" />
    </div>
    <Message v-if="newClips" severity="info" class="notice">
      New clips are available.
      <Button label="Show them" size="small" text @click="load('first')" />
    </Message>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-else-if="loading && !clips.length" class="muted">Loading…</p>
    <p v-else-if="!clips.length" class="muted">No clips yet.</p>

    <ul class="clips">
      <li v-for="c in clips" :key="c.s3Key">
        <span class="tier" :class="`tier-${c.tier}`">{{ TIER_LABEL[c.tier] || c.tier }}{{ c.restoring ? " (restoring…)" : "" }}</span>
        <span class="meta">
          <span class="when">{{ when(c) }}</span>
          <span class="muted"> · {{ formatDuration(c.durationSec) }}</span>
          <template v-if="clipCodecName(c)">
            <span v-if="c.videoCodec === 'h265' && !canPlayHevc" class="codec-warn"
                  title="This browser can't decode H.265 — see the note at the top of the page"> · H.265</span>
            <span v-else class="muted"> · {{ clipCodecName(c) }}</span>
          </template>
          <span v-if="c.labels?.length" class="labels">{{ c.labels.join(", ") }}</span>
        </span>
        <span class="actions">
          <Select v-model="tierPick[c.s3Key]" :options="TIER_CHOICES" option-label="label" option-value="value"
                  size="small" :aria-label="`Storage tier of the clip from ${when(c)}`" />
          <Button label="Apply" size="small" severity="secondary" @click="applyTier(c)" />
          <Button label="Play" icon="pi pi-play" size="small" @click="playing = { key: c.s3Key, title: `${cameraName(camera)} · ${when(c)}` }" />
          <Button label="Delete" icon="pi pi-trash" size="small" severity="danger" text @click="remove(c)" />
        </span>
        <span v-if="rowStatus[c.s3Key]" class="row-status" role="status">{{ rowStatus[c.s3Key] }}</span>
      </li>
    </ul>

    <nav v-if="pages > 1" class="pager" aria-label="Clip pages">
      <Button label="« Newest" size="small" text :disabled="loading || page === 1" @click="load('first')" />
      <Button label="‹ Newer" size="small" text :disabled="loading || page === 1" @click="load('newer')" />
      <template v-for="(n, i) in numbers" :key="i">
        <span v-if="n === null" class="gap">…</span>
        <Button v-else :label="String(n)" size="small" :text="n !== page" :disabled="loading || n === page"
                :aria-current="n === page ? 'page' : undefined" @click="load(n)" />
      </template>
      <Button label="Older ›" size="small" text :disabled="loading || page === pages" @click="load('older')" />
      <Button label="Oldest »" size="small" text :disabled="loading || page === pages" @click="load('last')" />
    </nav>

    <ClipPlayer :s3-key="playing?.key ?? null" :title="playing?.title ?? ''" @close="playing = null" />
  </div>
</template>

<style scoped>
.list-head { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.5rem; }
.notice { margin-bottom: 0.75rem; }
.error { color: var(--p-red-400); }
.clips { list-style: none; margin: 0; padding: 0; }
.clips li {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
  padding: 0.6rem 0.75rem;
  margin-bottom: 0.4rem;
  border-radius: 0.5rem;
  background: var(--p-surface-700);
}
.tier { font-size: 0.75rem; padding: 0.15rem 0.55rem; border-radius: 1rem; color: #fff; white-space: nowrap; background: #777; }
.tier-STANDARD { background: #2a7d2a; }
.tier-STANDARD_IA { background: #b8860b; }
.tier-DEEP_ARCHIVE, .tier-GLACIER { background: #444; }
.meta { flex: 1; min-width: 12rem; font-size: 0.9rem; }
.labels { display: block; color: var(--p-text-muted-color); font-size: 0.8rem; }
.codec-warn { color: var(--p-amber-400); font-weight: 600; cursor: help; }
.actions { display: flex; align-items: center; gap: 0.35rem; flex-wrap: wrap; }
.row-status { flex-basis: 100%; font-size: 0.82rem; color: var(--p-text-muted-color); }
.pager { display: flex; align-items: center; flex-wrap: wrap; gap: 0.2rem; margin-top: 0.75rem; }
.gap { color: var(--p-text-muted-color); padding: 0 0.2rem; }
</style>
