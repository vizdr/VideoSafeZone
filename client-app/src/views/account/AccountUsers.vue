<script setup lang="ts">
// Additional users (DEALER-PANEL-PLAN.md D18), after Videoloft's, for an account's holder and Super
// Admins: Add user -- an address with an account gets access at once, a new one an invitation -- Users
// (Super Admin, or the cameras shared; Edit, Remove) and Pending invites (Resend, Cancel); the holder can
// hand the account over. A change reaches the person's token within about 3 minutes (D1). Nobody changes
// the holder here, or their own access: the directory refuses it, and the page doesn't offer it.
import { computed, ref, watch } from "vue";
import Button from "openvue/button";
import InputText from "openvue/inputtext";
import Message from "openvue/message";
import ToggleSwitch from "openvue/toggleswitch";
import { endpoints } from "../../api";
import { errorText, type ApiResult } from "../../api/client";
import type { AccountPerson, AccountUsers, Me } from "../../api/types";
import AccountPicker from "./AccountPicker.vue";

const props = defineProps<{ accounts: Me["accounts"] }>();
const emit = defineEmits<{ changed: [] }>();
const chosen = ref(props.accounts[0]?.id ?? "");
watch(() => props.accounts.map(a => a.id).join(), () => {
  if (!props.accounts.some(a => a.id === chosen.value)) chosen.value = props.accounts[0]?.id ?? "";
});

const data = ref<AccountUsers | null>(null);
const error = ref(""), message = ref(""), warning = ref(""), busy = ref(false);

async function load(): Promise<void> {
  const id = chosen.value;
  if (!id) return;
  try {
    const res = await endpoints.accountUsers(id);
    if (id !== chosen.value) return;                    // another account was picked meanwhile
    error.value = res.ok ? "" : errorText(res);
    data.value = res.ok ? res.data : null;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}
watch(chosen, () => { data.value = null; message.value = warning.value = ""; void load(); }, { immediate: true });

const holder = computed(() => data.value?.people.find(p => p.role === "customer") ?? null);
const users = computed(() => (data.value?.people ?? []).filter(p => p.role !== "customer" && p.status !== "invited"));
const pending = computed(() => (data.value?.people ?? []).filter(p => p.role !== "customer" && p.status === "invited"));
const cameras = computed(() => data.value?.cameras ?? []);
const nameOf = (id: string) => cameras.value.find(c => c.id === id)?.name || id;
function accessText(p: AccountPerson): string {
  if (p.role === "customer_admin") return "Super Admin";
  if (!p.cameras.length) return "No cameras shared";
  return `${p.cameras.length} camera${p.cameras.length === 1 ? "" : "s"} shared: ${p.cameras.map(nameOf).join(", ")}`;
}
const dateOf = (iso: string) => new Date(iso).toLocaleDateString();

/** One change: on success the message, the list reloaded, the page told (the viewer's own role may move). */
async function act(work: () => Promise<ApiResult<unknown>>, done: string, after?: (data: unknown) => void): Promise<boolean> {
  busy.value = true;
  message.value = warning.value = error.value = "";
  try {
    const res = await work();
    if (!res.ok) { error.value = errorText(res); return false; }
    message.value = done;
    after?.(res.data);
    await load();
    emit("changed");
    return true;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    return false;
  } finally {
    busy.value = false;
  }
}

// --- Add user ---
const email = ref(""), superAdmin = ref(false), picked = ref<string[]>([]);
function resetAdd(): void { email.value = ""; superAdmin.value = false; picked.value = []; }
async function add(): Promise<void> {
  const address = email.value.trim();
  let note = "";
  const ok = await act(
    () => endpoints.addAccountUser(chosen.value, { email: address, superAdmin: superAdmin.value,
                                                   cameras: superAdmin.value ? [] : picked.value }),
    "", d => {
      note = (d as { new: boolean }).new ? `An invitation is on its way to ${address}: they're under Pending invites until they sign in.`
        : `${address} has access now: they'll see it within about 3 minutes.`;
    });
  if (ok) { message.value = note; resetAdd(); }
}

// --- Edit ---
const editing = ref<string | null>(null), editAdmin = ref(false), editPicked = ref<string[]>([]);
function edit(p: AccountPerson): void {
  editing.value = p.id;
  editAdmin.value = p.role === "customer_admin";
  editPicked.value = [...p.cameras];
}
async function saveEdit(p: AccountPerson): Promise<void> {
  const ok = await act(() => endpoints.updateAccountUser(chosen.value, p.id,
                                                         { superAdmin: editAdmin.value, cameras: editAdmin.value ? [] : editPicked.value }),
                       `${p.email}'s access is changed: within about 3 minutes it holds.`);
  if (ok) editing.value = null;
}

// --- Remove, Resend, Cancel ---
const removing = ref<string | null>(null);
async function remove(p: AccountPerson, invite: boolean): Promise<void> {
  const work = invite ? () => endpoints.accountInvite(chosen.value, p.id, "cancel") : () => endpoints.removeAccountUser(chosen.value, p.id);
  await act(work, invite ? `The invitation to ${p.email} is cancelled.` : `${p.email} no longer has access to this account.`,
            d => { const err = (d as { cognitoError?: string }).cognitoError; if (err) warning.value = `Done, but ${err}.`; });
  removing.value = null;
}
const resend = (p: AccountPerson) => act(() => endpoints.accountInvite(chosen.value, p.id, "resend"),
                                         `The invitation was sent to ${p.email} again.`);

// --- Hand over (the holder) ---
const heir = ref(""), handing = ref(false);
const heirs = computed(() => users.value.filter(p => p.status === "active"));
async function handOver(): Promise<void> {
  const to = heirs.value.find(p => p.id === heir.value);
  if (!to) return;
  const ok = await act(() => endpoints.handOverAccount(chosen.value, to.id),
                       `${to.email} holds this account now; you stay on as a Super Admin.`);
  if (ok) { handing.value = false; heir.value = ""; }
}
</script>

<template>
  <article class="console-card">
    <h2>Additional users<span class="dot">.</span></h2>
    <AccountPicker v-model="chosen" :accounts="accounts" />
    <Message v-if="message" severity="success" size="small" variant="simple" data-test="message">{{ message }}</Message>
    <Message v-if="warning" severity="warn" size="small" variant="simple" data-test="warning">{{ warning }}</Message>
    <p v-if="error" role="alert" data-test="users-error">{{ error }}</p>

    <template v-if="data">
      <section class="block" aria-labelledby="add-user">
        <h3 id="add-user">Add user<span class="dot">.</span></h3>
        <form class="console-form add" @submit.prevent="add">
          <label>Email
            <InputText v-model="email" type="email" required autocomplete="off" data-test="new-email" />
          </label>
          <p class="hint">If the person already has a VideoSafeZone account, they get access straight away; if not,
            they get an email asking them to set a password.</p>
          <label class="switch">Super Admin
            <ToggleSwitch v-model="superAdmin" aria-label="Super Admin" data-test="new-super-admin" />
          </label>
          <p class="hint">Full access to all cameras of the account; can also add and remove cameras and cloud
            adapters, and manage users.</p>
          <fieldset v-if="!superAdmin" class="cameras" data-test="new-cameras">
            <legend>Cameras to share</legend>
            <p v-if="data.cameras === null" class="muted">{{ data.registryError }}</p>
            <p v-else-if="!cameras.length" class="muted">No cameras on this account yet.</p>
            <label v-for="c in cameras" :key="c.id" class="camera">
              <input v-model="picked" type="checkbox" :value="c.id" /> {{ c.name || c.id }}
              <span v-if="c.name" class="muted">{{ c.id }}</span>
            </label>
          </fieldset>
          <div class="actions">
            <Button type="submit" label="Save" :loading="busy" />
            <Button type="button" label="Cancel" severity="secondary" @click="resetAdd" />
          </div>
          <p class="hint">Changes take effect within about 3 minutes.</p>
        </form>
      </section>

      <section class="block" aria-labelledby="users">
        <h3 id="users">Users<span class="dot">.</span></h3>
        <p v-if="!users.length" class="muted" data-test="no-users">You haven't shared cameras with anyone yet.</p>
        <table v-else class="console-table">
          <tbody>
            <template v-for="p in users" :key="p.id">
              <tr data-test="user">
                <td>{{ p.email }}<span v-if="p.you" class="muted"> (you)</span>
                  <span v-if="p.status === 'disabled'" class="muted"> · disabled</span></td>
                <td>{{ accessText(p) }}</td>
                <td class="row-actions">
                  <template v-if="!p.you">
                    <template v-if="removing === p.id">
                      <span>Remove {{ p.email }}?</span>
                      <Button size="small" severity="danger" label="Remove" :loading="busy" @click="remove(p, false)" />
                      <Button size="small" severity="secondary" label="Keep" @click="removing = null" />
                    </template>
                    <template v-else>
                      <Button size="small" severity="secondary" label="Edit" @click="edit(p)" />
                      <Button size="small" severity="danger" variant="text" label="Remove" @click="removing = p.id" />
                    </template>
                  </template>
                </td>
              </tr>
              <tr v-if="editing === p.id" data-test="editing">
                <td colspan="3">
                  <div class="console-form">
                    <label class="switch">Super Admin <ToggleSwitch v-model="editAdmin" aria-label="Super Admin" /></label>
                    <fieldset v-if="!editAdmin" class="cameras">
                      <legend>Cameras to share</legend>
                      <label v-for="c in cameras" :key="c.id" class="camera">
                        <input v-model="editPicked" type="checkbox" :value="c.id" /> {{ c.name || c.id }}
                      </label>
                    </fieldset>
                    <div class="actions">
                      <Button size="small" label="Save" :loading="busy" @click="saveEdit(p)" />
                      <Button size="small" severity="secondary" label="Cancel" @click="editing = null" />
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </section>

      <section class="block" aria-labelledby="pending">
        <h3 id="pending">Pending invites<span class="dot">.</span></h3>
        <p v-if="!pending.length" class="muted" data-test="no-pending">No pending invites.</p>
        <table v-else class="console-table">
          <tbody>
            <tr v-for="p in pending" :key="p.id" data-test="pending">
              <td>{{ p.email }}</td>
              <td class="muted">invited {{ dateOf(p.addedAt) }}</td>
              <td>{{ accessText(p) }}</td>
              <td class="row-actions">
                <template v-if="removing === p.id">
                  <span>Cancel the invitation?</span>
                  <Button size="small" severity="danger" label="Cancel invitation" :loading="busy" @click="remove(p, true)" />
                  <Button size="small" severity="secondary" label="Keep" @click="removing = null" />
                </template>
                <template v-else>
                  <Button size="small" severity="secondary" label="Resend" :loading="busy" @click="resend(p)" />
                  <Button size="small" severity="danger" variant="text" label="Cancel" @click="removing = p.id" />
                </template>
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="holder" class="block" aria-labelledby="holder" data-test="holder">
        <h3 id="holder">Account holder<span class="dot">.</span></h3>
        <p>{{ holder.email }}<span v-if="holder.you" class="muted"> (you)</span> — sees Plan &amp; Billing; nobody can
          change the holder's access.</p>
        <template v-if="data.role === 'customer'">
          <p v-if="!heirs.length" class="muted">To hand the account over, add the person first; they take it over once
            they have signed in.</p>
          <Button v-else-if="!handing" size="small" severity="secondary" label="Hand the account over…" @click="handing = true" />
          <div v-else class="console-form">
            <label>To
              <select v-model="heir" class="p-inputtext" data-test="heir">
                <option value="" disabled>Choose someone</option>
                <option v-for="p in heirs" :key="p.id" :value="p.id">{{ p.email }}</option>
              </select>
            </label>
            <p class="hint">You stay on as a Super Admin and no longer see Plan &amp; Billing.</p>
            <div class="actions">
              <Button size="small" label="Hand over" :disabled="!heir" :loading="busy" @click="handOver" />
              <Button size="small" severity="secondary" label="Cancel" @click="handing = false" />
            </div>
          </div>
        </template>
      </section>
    </template>
  </article>
</template>

<style scoped>
.block { padding: 0.75rem 0 1rem; border-bottom: 1px solid var(--p-surface-700); }
.block:last-child { border-bottom: 0; }
.block h3 { margin: 0 0 0.6rem; }
.dot { color: var(--p-primary-color); }
.add { max-width: 34rem; }
.hint { margin: 0; font-size: 0.82rem; color: var(--p-text-muted-color); }
.switch { flex-direction: row; align-items: center; gap: 0.6rem; }
.cameras { border: 1px solid var(--p-surface-700); border-radius: 0.4rem; padding: 0.5rem 0.75rem; display: grid; gap: 0.3rem; }
.camera { display: flex; flex-direction: row; align-items: center; gap: 0.45rem; }
.actions { display: flex; gap: 0.5rem; flex-wrap: wrap; }
.row-actions { display: flex; gap: 0.4rem; align-items: center; justify-content: flex-end; flex-wrap: wrap; }
</style>
