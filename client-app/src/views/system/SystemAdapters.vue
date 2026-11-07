<script setup lang="ts">
// Adapters (DEALER-PANEL-PLAN.md §7.1, §7.2, D15). Owners and administrators make new ones -- each
// with its Cloud ID, shown here once, and a printable label -- allocate them to dealers, issue a new
// code, release, revoke and sync. Each adapter's AWS identity is the owner's script's
// (cloud/adapter-identity.sh, Phase 6 amendment): this page says what to run. On the dealer portal
// (Cloud adapters) the same list, read-only: a dealer activates one for a customer from the
// customer's Plan page.
import { computed, ref } from "vue";
import Button from "openvue/button";
import InputText from "openvue/inputtext";
import Message from "openvue/message";
import { endpoints } from "../../api";
import { errorText, type ApiResult } from "../../api/client";
import type { AdapterChange, AdapterSummary, Dealer, NewAdapters, Role } from "../../api/types";
import { SYSTEM_ROLES } from "../../auth/roles";
import { useLoad } from "../../composables/useLoad";
import { useAuthStore } from "../../stores/auth";

type Action = "new-code" | "release" | "revoke" | "sync";

const auth = useAuthStore();
const system = computed(() => SYSTEM_ROLES.includes(auth.role as Role | null));
const list = useLoad<AdapterSummary[]>(() => endpoints.listAdapters());
const dealers = useLoad<Dealer[]>(() => (system.value ? endpoints.listDealers()
  : Promise.resolve({ ok: true, status: 200, data: [] as Dealer[] })));
const error = ref(""), busy = ref(false);

// --- New adapters -------------------------------------------------------------------------------
const count = ref(1), label = ref(""), dealerId = ref("");
const made = ref<NewAdapters | null>(null);

async function generate(): Promise<void> {
  busy.value = true;
  error.value = "";
  try {
    const res = await endpoints.generateAdapters(Number(count.value), label.value.trim() || undefined, dealerId.value || undefined);
    if (!res.ok) { error.value = errorText(res); return; }
    made.value = res.data;
    await list.reload();
  } finally {
    busy.value = false;
  }
}
const printLabels = () => window.print();

// --- one adapter's actions -----------------------------------------------------------------------
interface Outcome { title: string; cloudId?: string; command?: string; notes: string[] }
const outcome = ref<Outcome | null>(null);
const confirming = ref<{ id: string; action: Action } | null>(null);
const allocating = ref<string | null>(null);
const allocateTo = ref("");

const TITLES: Record<Action, (thing: string) => string> = {
  "new-code": t => `A new Cloud ID for ${t}`, "release": t => `${t} is released`,
  "revoke": t => `${t} is revoked`, "sync": t => `${t}: its config is sent again`,
};

function notesOf(d: AdapterChange): string[] {
  return [
    ...(d.camerasReleased ? [`${d.camerasReleased} camera${d.camerasReleased === 1 ? "" : "s"} taken out of its customer's view.`] : []),
    ...(d.registryError ? [d.registryError] : []),
    ...(d.configUnsent ? [d.configUnsent] : []),
  ];
}

async function act(work: () => Promise<ApiResult<AdapterChange>>, title: (d: AdapterChange) => string): Promise<void> {
  busy.value = true;
  error.value = "";
  outcome.value = null;
  try {
    const res = await work();
    if (!res.ok) { error.value = errorText(res); return; }
    const d = res.data;
    confirming.value = null;
    allocating.value = null;
    outcome.value = { title: title(d), cloudId: d.cloudId, command: d.identityCommand, notes: notesOf(d) };
    await list.reload();
  } finally {
    busy.value = false;
  }
}
const run = (a: AdapterSummary, action: Action) =>
  act(() => endpoints.adapterAction(a.id, action), () => TITLES[action](a.thingName));
const allocate = (a: AdapterSummary) =>
  act(() => endpoints.allocateAdapter(a.id, allocateTo.value || null),
      () => `${a.thingName} is ${allocateTo.value ? `in ${dealers.data.value?.find(d => d.id === allocateTo.value)?.name}'s stock` : "in nobody's stock"}`);

function openAllocate(a: AdapterSummary): void {
  allocating.value = allocating.value === a.id ? null : a.id;
  allocateTo.value = a.dealer?.id ?? "";
}
const waiting = (a: AdapterSummary) => a.status === "provisioned" || a.status === "released";
const askedFor = (a: AdapterSummary, action: Action) => confirming.value?.id === a.id && confirming.value.action === action;
const connection = (a: AdapterSummary) => (a.connected ? "online"
  : a.lastSeenAt ? `last seen ${new Date(a.lastSeenAt).toLocaleString()}` : "not seen yet");
</script>

<template>
  <article v-if="system" class="console-card">
    <h2>New adapters<span class="dot">.</span></h2>
    <form class="console-form" @submit.prevent="generate">
      <label>How many<input v-model.number="count" type="number" min="1" max="20" class="p-inputtext count" data-test="count" /></label>
      <label>Label<InputText v-model="label" placeholder="Batch 2026-11" data-test="label" /></label>
      <label>For a dealer's stock
        <select v-model="dealerId" class="p-inputtext" data-test="stock">
          <option value="">nobody's yet</option>
          <option v-for="d in dealers.data.value ?? []" :key="d.id" :value="d.id">{{ d.name }}</option>
        </select>
      </label>
      <Button type="submit" label="Make them" :loading="busy" />
    </form>

    <div v-if="made" class="made" data-test="made">
      <Message severity="warn" size="small" variant="simple">
        Shown this once: write the Cloud IDs down or print the labels now. Nobody can show them again.</Message>
      <table class="console-table">
        <thead><tr><th>Adapter</th><th>Cloud ID</th></tr></thead>
        <tbody>
          <tr v-for="m in made.adapters" :key="m.id"><td>{{ m.thingName }}</td><td class="code">{{ m.cloudId }}</td></tr>
        </tbody>
      </table>
      <p>Then make their AWS identities, with your own AWS login:</p>
      <pre class="command" data-test="identity-command">{{ made.identityCommand }}</pre>
      <p class="muted">Each makes a folder for that adapter's Pi: <code>bash setup/setup-identity.sh restore &lt;folder&gt;</code> there.</p>
      <Message v-if="made.configUnsent" severity="warn" size="small" variant="simple">{{ made.configUnsent }}</Message>
      <div class="console-actions">
        <Button label="Print labels" icon="pi pi-print" severity="secondary" @click="printLabels" />
        <Button label="Done" severity="secondary" variant="text" @click="made = null" />
      </div>
      <div class="print-labels" aria-hidden="true">
        <div v-for="m in made.adapters" :key="m.id" class="label">
          <span class="brand">VideoSafeZone</span>
          <span class="what">Cloud ID</span>
          <span class="code">{{ m.cloudId }}</span>
          <span class="thing">{{ m.thingName }}</span>
        </div>
      </div>
    </div>
  </article>

  <article class="console-card">
    <h2>{{ system ? "Adapters" : "Cloud adapters" }}<span class="dot">.</span></h2>
    <p v-if="!system" class="muted">To activate one for a customer: the customer's Plan page, with the Cloud ID from its label.</p>
    <p v-if="list.error.value" role="alert">{{ list.error.value }}</p>
    <Message v-if="error" severity="error" size="small" variant="simple" data-test="error">{{ error }}</Message>
    <div v-if="outcome" class="outcome" data-test="outcome">
      <strong>{{ outcome.title }}</strong>
      <p v-if="outcome.cloudId">Its new Cloud ID, shown this once: <span class="code" data-test="new-code">{{ outcome.cloudId }}</span></p>
      <template v-if="outcome.command"><p>Then deactivate its certificate, with your own AWS login:</p>
        <pre class="command" data-test="outcome-command">{{ outcome.command }}</pre></template>
      <p v-for="n in outcome.notes" :key="n" class="muted">{{ n }}</p>
      <Button size="small" label="Done" severity="secondary" variant="text" @click="outcome = null" />
    </div>
    <table class="console-table">
      <thead><tr><th>Adapter</th><th v-if="system">Dealer</th><th>Status</th><th>At</th><th>Connection</th>
        <th v-if="system">Code · identity</th><th v-if="system"></th></tr></thead>
      <tbody>
        <template v-for="a in list.data.value ?? []" :key="a.id">
          <tr :data-test="`adapter-${a.thingName}`">
            <td>{{ a.thingName }}<div v-if="a.label" class="muted">{{ a.label }}</div></td>
            <td v-if="system">{{ a.dealer?.name ?? "—" }}</td>
            <td><span class="status" :class="a.status">{{ a.status }}</span></td>
            <td>
              <template v-if="a.activation">{{ a.activation.customer.name }} · {{ a.activation.site }}
                <span v-if="a.activation.name" class="muted">({{ a.activation.name }})</span></template>
              <span v-else class="muted">not activated</span>
            </td>
            <td><span class="status" :class="{ active: a.connected }">{{ connection(a) }}</span></td>
            <td v-if="system" class="muted">{{ a.hasCode ? "code waiting" : "no code" }} ·
              {{ a.identity ? "identity made" : "no identity yet" }}</td>
            <td v-if="system" class="console-actions">
              <template v-if="waiting(a)">
                <Button size="small" severity="secondary" label="Allocate" @click="openAllocate(a)" />
                <Button size="small" severity="secondary" label="New code" @click="run(a, 'new-code')" />
              </template>
              <template v-if="a.status === 'activated'">
                <Button v-if="!askedFor(a, 'release')" size="small" severity="secondary" label="Release"
                        @click="confirming = { id: a.id, action: 'release' }" />
                <template v-else>
                  <span class="muted">It leaves {{ a.activation?.customer.name }}, with its cameras' recordings.</span>
                  <Button size="small" severity="danger" label="Yes, release it" :loading="busy" data-test="confirm-release"
                          @click="run(a, 'release')" />
                  <Button size="small" severity="secondary" variant="text" label="Keep it" @click="confirming = null" />
                </template>
              </template>
              <template v-if="a.status !== 'revoked'">
                <Button v-if="!askedFor(a, 'revoke')" size="small" severity="danger" variant="text" label="Revoke"
                        @click="confirming = { id: a.id, action: 'revoke' }" />
                <template v-else>
                  <span class="muted">Lost or stolen? It never comes back.</span>
                  <Button size="small" severity="danger" label="Yes, revoke it" :loading="busy" data-test="confirm-revoke"
                          @click="run(a, 'revoke')" />
                  <Button size="small" severity="secondary" variant="text" label="Keep it" @click="confirming = null" />
                </template>
              </template>
              <Button size="small" severity="secondary" variant="text" label="Sync" @click="run(a, 'sync')" />
            </td>
          </tr>
          <tr v-if="allocating === a.id">
            <td colspan="7">
              <form class="console-form" @submit.prevent="allocate(a)">
                <label>Into the stock of
                  <select v-model="allocateTo" class="p-inputtext" data-test="allocate-to">
                    <option value="">nobody</option>
                    <option v-for="d in dealers.data.value ?? []" :key="d.id" :value="d.id">{{ d.name }}</option>
                  </select>
                </label>
                <Button type="submit" size="small" label="Allocate" :loading="busy" data-test="allocate" />
              </form>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </article>
</template>

<style scoped>
.count { width: 5rem; }
.made, .outcome { margin-top: 1rem; padding: 1rem; border: 1px solid var(--p-content-border-color); border-radius: 0.5rem; }
.outcome { margin: 0 0 1rem; }
.code { font-family: ui-monospace, monospace; font-size: 1.1rem; letter-spacing: 0.08em; }
.command { padding: 0.6rem 0.8rem; background: var(--p-surface-800); border-radius: 0.4rem; overflow-x: auto; }
</style>
