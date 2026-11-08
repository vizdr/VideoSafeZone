<script setup lang="ts">
// Settings → Cloud adapters (DEALER-PANEL-PLAN.md §7.3, D15): the account's adapters, where each is,
// whether it is online; Add adapter; Remove from the account -- a release: activating it again
// takes a new Cloud ID from the system owner, and its cameras and recordings stay behind.
import { ref } from "vue";
import Button from "openvue/button";
import Message from "openvue/message";
import { endpoints } from "../api";
import { errorText } from "../api/client";
import type { AdapterSummary } from "../api/types";
import { useLoad } from "../composables/useLoad";
import { useAuthStore } from "../stores/auth";
import { useCamerasStore } from "../stores/cameras";

const auth = useAuthStore();
const cameras = useCamerasStore();
const list = useLoad<AdapterSummary[]>(() => endpoints.listAdapters());
const confirming = ref<string | null>(null);
const message = ref(""), error = ref(""), busy = ref(false);

const label = (a: AdapterSummary) => a.activation?.name || a.thingName;
const connection = (a: AdapterSummary) => (a.connected ? "online"
  : a.lastSeenAt ? `last seen ${new Date(a.lastSeenAt).toLocaleString()}` : "not seen yet");

async function remove(a: AdapterSummary): Promise<void> {
  busy.value = true;
  message.value = error.value = "";
  try {
    const res = await endpoints.adapterAction(a.id, "remove");
    if (!res.ok) { error.value = errorText(res); return; }
    confirming.value = null;
    message.value = `${label(a)} is removed from your account.`;
    await auth.refresh().catch(() => undefined);
    await Promise.all([cameras.load(), list.reload()]);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="view">
    <h1>Cloud adapters</h1>
    <article class="console-card">
      <h2>Your adapters<span class="dot">.</span></h2>
      <p v-if="list.error.value" role="alert">{{ list.error.value }}</p>
      <Message v-if="message" severity="success" size="small" variant="simple">{{ message }}</Message>
      <Message v-if="error" severity="error" size="small" variant="simple" data-test="error">{{ error }}</Message>
      <table v-if="list.data.value?.length" class="console-table">
        <thead><tr><th>Adapter</th><th>Location</th><th>Connection</th><th>Since</th><th></th></tr></thead>
        <tbody>
          <tr v-for="a in list.data.value" :key="a.id" :data-test="`adapter-${a.thingName}`">
            <td>{{ label(a) }}<span v-if="a.activation?.name" class="muted"> · {{ a.thingName }}</span></td>
            <td>{{ a.activation?.site }}</td>
            <td><span class="status" :class="{ active: a.connected }">{{ connection(a) }}</span></td>
            <td class="muted">{{ a.activation ? new Date(a.activation.activatedAt).toLocaleDateString() : "" }}</td>
            <td class="console-actions">
              <RouterLink :to="{ name: 'add-cameras', params: { thing: a.thingName } }" class="p-button p-button-sm"
                          data-test="add-cameras" title="Add, unregister and re-register its cameras">Cameras</RouterLink>
              <Button v-if="confirming !== a.id" size="small" severity="danger" variant="text" label="Remove adapter"
                      data-test="remove-adapter" @click="confirming = a.id" />
              <template v-else>
                <span class="muted">Remove it from your account? Its cameras stay behind.</span>
                <Button size="small" severity="danger" label="Yes, remove it" :loading="busy" data-test="confirm-remove" @click="remove(a)" />
                <Button size="small" severity="secondary" label="Keep it" @click="confirming = null" />
              </template>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else-if="list.data.value" class="muted">No adapter on your account yet.</p>
      <p class="muted hint">An adapter's <em>Cameras</em>: add new ones, unregister one (out of Live, recordings kept) or re-register it.</p>
      <RouterLink :to="{ name: 'add-adapter' }" class="p-button add" data-test="add-adapter">Add adapter</RouterLink>
    </article>
  </section>
</template>

<style scoped>
.add { display: inline-block; margin-top: 1rem; padding: 0.5rem 1rem; text-decoration: none; }
.hint { margin: 1rem 0 0; }
</style>
