<script setup lang="ts">
// Add cameras (DEALER-PANEL-PLAN.md §7.3, D16): the adapter (picked for you when there is one)
// looks for cameras on its own network; the ones already added are marked; per camera a name and
// its ONVIF login; then progress until it is registered, and it shows in Live and Settings. The
// page never reaches the customer's LAN: everything goes through the adapter's outbound MQTT
// session. The password is sent once, with the request, and not kept here.
import { computed, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import Button from "openvue/button";
import InputText from "openvue/inputtext";
import Message from "openvue/message";
import Password from "openvue/password";
import { endpoints } from "../api";
import { errorText } from "../api/client";
import type { AdapterSummary, CameraRequest, CameraScan, FoundDevice } from "../api/types";
import { pollUntil } from "../cameras/poll";
import { useLoad } from "../composables/useLoad";
import { useCamerasStore } from "../stores/cameras";

const SCAN_WAIT_MS = 60_000, ADD_WAIT_MS = 180_000, EVERY_MS = 2_000;
const PROGRESS: Record<CameraRequest["status"], string> = {
  sent: "Asking the adapter…",
  registering: "Registering: the adapter connects to the camera and makes its cloud stream (up to a minute)…",
  done: "Added.", failed: "Not added.",
};

const route = useRoute();
const cameras = useCamerasStore();
const adapters = useLoad<AdapterSummary[]>(() => endpoints.listAdapters());
const mine = computed(() => (adapters.data.value ?? []).filter(a => a.status === "activated"));
const thing = ref(typeof route.params.thing === "string" ? route.params.thing : "");
watch(mine, list => { if (!thing.value && list.length) thing.value = list[0].thingName; }, { immediate: true });
const adapter = computed(() => mine.value.find(a => a.thingName === thing.value));
const adapterName = (a: AdapterSummary) => a.activation?.name || a.thingName;

type Phase = "idle" | "scanning" | "found" | "form" | "adding" | "done";
const phase = ref<Phase>("idle");
const scan = ref<CameraScan | null>(null);
const chosen = ref<FoundDevice | null>(null);
const form = ref({ name: "", user: "admin", password: "" });
const progress = ref(""), error = ref(""), added = ref<CameraRequest | null>(null);
let alive = true;
onUnmounted(() => { alive = false; });

async function lookForCameras(): Promise<void> {
  const a = adapter.value;
  if (!a) return;
  phase.value = "scanning";
  error.value = "";
  scan.value = null;
  const res = await endpoints.scanForCameras(a.thingName);
  if (!res.ok) { error.value = errorText(res); phase.value = "idle"; return; }
  const wanted = res.data.requestId;
  const out = await pollUntil(async () => {
    const r = await endpoints.cameraScan(a.thingName);
    return r.ok ? r.data : null;
  }, s => s.requestId === wanted, { every: EVERY_MS, within: SCAN_WAIT_MS, stopped: () => !alive });
  if (out === "stopped") return;
  if (out === "timeout") {
    error.value = `${adapterName(a)} didn't answer within a minute. Is it switched on and online?`
      + (a.connected === false && a.lastSeenAt ? ` It was last seen ${new Date(a.lastSeenAt).toLocaleString()}.` : "");
    phase.value = "idle";
    return;
  }
  scan.value = out;
  if (out.error) error.value = `The adapter couldn't look: ${out.error}`;
  phase.value = "found";
}

function choose(d: FoundDevice): void {
  chosen.value = d;
  form.value = { name: d.displayName || d.name || "", user: "admin", password: "" };
  error.value = "";
  phase.value = "form";
}

async function add(): Promise<void> {
  const a = adapter.value, d = chosen.value;
  if (!a || !d?.xaddr) return;
  const req = { endpointRef: d.endpointRef, xaddr: d.xaddr, user: form.value.user.trim(), password: form.value.password,
                displayName: form.value.name.trim() || undefined };
  form.value.password = "";                                       // sent once, not kept
  phase.value = "adding";
  error.value = "";
  progress.value = PROGRESS.sent;
  const res = await endpoints.addCamera(a.thingName, req);
  if (!res.ok) { error.value = errorText(res); phase.value = "form"; return; }
  const id = res.data.requestId;
  const out = await pollUntil(async () => {
    const r = await endpoints.cameraRequest(a.thingName, id);
    if (r.ok) progress.value = PROGRESS[r.data.status];
    return r.ok ? r.data : null;
  }, r => r.status === "done" || r.status === "failed", { every: EVERY_MS, within: ADD_WAIT_MS, stopped: () => !alive });
  if (out === "stopped") return;
  if (out === "timeout") {
    error.value = "The adapter hasn't finished within three minutes. Look in Live in a while, or try again.";
    phase.value = "form";
    return;
  }
  if (out.status === "failed") { error.value = out.error ?? "The camera wasn't added."; phase.value = "form"; return; }
  added.value = out;
  phase.value = "done";
  await cameras.load();
}

function another(): void {
  added.value = chosen.value = null;
  phase.value = "idle";
  void lookForCameras();
}

const STATE_TEXT: Record<string, string> = { added: "Added", removed: "Removed: add it back", elsewhere: "On another adapter" };
const label = (d: FoundDevice) => d.displayName || d.name || d.hardware || d.host || d.endpointRef;
</script>

<template>
  <section class="view">
    <h1>Add cameras</h1>
    <article class="console-card">
      <h2>Cameras on the adapter's network<span class="dot">.</span></h2>
      <p v-if="adapters.error.value" role="alert">{{ adapters.error.value }}</p>
      <p v-else-if="adapters.data.value && !mine.length" class="muted" data-test="no-adapter">
        No activated adapter on your account. <RouterLink :to="{ name: 'add-adapter' }">Add your Cloud Adapter</RouterLink> first.</p>
      <template v-else-if="adapter">
        <div class="console-form">
          <label v-if="mine.length > 1">Adapter
            <select v-model="thing" class="p-inputtext" data-test="adapter" :disabled="phase === 'scanning' || phase === 'adding'">
              <option v-for="a in mine" :key="a.thingName" :value="a.thingName">{{ adapterName(a) }} · {{ a.activation?.site }}</option>
            </select>
          </label>
          <Button v-if="phase === 'idle' || phase === 'found'" :label="phase === 'found' ? 'Look again' : 'Look for cameras'"
                  icon="pi pi-search" data-test="scan" @click="lookForCameras" />
        </div>
        <p v-if="phase === 'scanning'" class="muted" data-test="scanning">
          <i class="pi pi-spin pi-spinner" aria-hidden="true" /> Looking for cameras… (asking {{ adapterName(adapter) }})</p>
        <Message v-if="error" severity="error" size="small" variant="simple" data-test="error">{{ error }}</Message>

        <table v-if="phase === 'found' && scan" class="console-table">
          <thead><tr><th>Camera</th><th>Address</th><th></th></tr></thead>
          <tbody>
            <tr v-for="d in scan.devices" :key="d.endpointRef" :data-test="`device-${d.host}`">
              <td>{{ label(d) }}<div v-if="d.hardware && label(d) !== d.hardware" class="muted">{{ d.hardware }}</div></td>
              <td class="muted">{{ d.host }}</td>
              <td class="console-actions">
                <span v-if="d.state === 'added' || d.state === 'elsewhere'" class="muted">
                  {{ STATE_TEXT[d.state] }}{{ d.state === "added" ? ` as ${d.displayName || d.cameraId}` : "" }}</span>
                <Button v-else size="small" :label="d.state === 'removed' ? 'Add it back' : 'Add'" :disabled="!d.xaddr"
                        @click="choose(d)" />
              </td>
            </tr>
            <tr v-if="!scan.devices.length"><td colspan="3" class="muted">No camera answered on the adapter's network.</td></tr>
          </tbody>
        </table>

        <form v-if="(phase === 'form' || phase === 'adding') && chosen" class="console-form add-form" data-test="add-form"
              @submit.prevent="add">
          <p class="who">{{ label(chosen) }} <span class="muted">· {{ chosen.host }}</span></p>
          <label>Name<InputText v-model="form.name" placeholder="Front door" data-test="name" :disabled="phase === 'adding'" /></label>
          <label>The camera's ONVIF user<InputText v-model="form.user" required autocomplete="off" data-test="user"
                                                   :disabled="phase === 'adding'" /></label>
          <label>Its password<Password v-model="form.password" :feedback="false" toggle-mask input-id="camera-password"
                                       autocomplete="new-password" :disabled="phase === 'adding'" /></label>
          <Button type="submit" label="Add" :loading="phase === 'adding'" data-test="add" />
          <Button v-if="phase === 'form'" label="Back" severity="secondary" variant="text" @click="phase = 'found'" />
        </form>
        <p v-if="phase === 'adding'" class="muted" data-test="progress">{{ progress }}</p>

        <template v-if="phase === 'done' && added">
          <Message severity="success" size="small" variant="simple" data-test="added">
            {{ added.reactivated ? "Back again" : "Added" }} as {{ added.cameraId }}. Its picture appears in Live once it
            has started streaming.</Message>
          <div class="console-actions">
            <RouterLink :to="{ name: 'live', params: { cameraId: added.cameraId } }" class="p-button">See it in Live</RouterLink>
            <Button label="Add another" severity="secondary" @click="another" />
          </div>
        </template>
      </template>
    </article>
  </section>
</template>

<style scoped>
.add-form { margin-top: 1rem; padding: 1rem; border: 1px solid var(--p-content-border-color); border-radius: 0.5rem; }
.who { flex-basis: 100%; margin: 0 0 0.25rem; font-weight: 600; }
.p-button { padding: 0.5rem 1rem; text-decoration: none; }
</style>
