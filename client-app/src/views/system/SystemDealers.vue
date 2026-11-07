<script setup lang="ts">
// Dealers (DEALER-PANEL-PLAN.md §7.2, D6): owners and administrators create, suspend and reactivate
// them, and invite each dealer's users -- Cognito emails the temporary password. A suspended dealer's
// staff can't sign in; its customers carry on.
import { ref } from "vue";
import Button from "openvue/button";
import InputText from "openvue/inputtext";
import Message from "openvue/message";
import { endpoints } from "../../api";
import { errorText, type ApiResult } from "../../api/client";
import type { Dealer } from "../../api/types";
import { useLoad } from "../../composables/useLoad";

const list = useLoad<Dealer[]>(() => endpoints.listDealers());
const name = ref(""), email = ref(""), phone = ref("");
const inviting = ref<string | null>(null);
const inviteEmail = ref(""), inviteName = ref("");
const message = ref(""), error = ref(""), busy = ref(false);

async function act(work: () => Promise<ApiResult<unknown>>, done: string): Promise<boolean> {
  busy.value = true;
  error.value = message.value = "";
  try {
    const res = await work();
    if (!res.ok) { error.value = errorText(res); return false; }
    message.value = done;
    await list.reload();
    return true;
  } finally {
    busy.value = false;
  }
}

async function create(): Promise<void> {
  const n = name.value.trim();
  if (await act(() => endpoints.createDealer({ name: n, email: email.value.trim() || undefined,
                                               phone: phone.value.trim() || undefined }), `Dealer ${n} created.`)) {
    name.value = email.value = phone.value = "";
  }
}

const setStatus = (d: Dealer, status: "active" | "suspended") =>
  act(() => endpoints.updateDealer(d.id, { status }), `${d.name} is ${status} now.`);

function openInvite(d: Dealer): void {
  inviting.value = inviting.value === d.id ? null : d.id;
  inviteEmail.value = inviteName.value = "";
}

async function invite(d: Dealer): Promise<void> {
  const e = inviteEmail.value.trim();
  if (await act(() => endpoints.inviteUser({ email: e, name: inviteName.value.trim() || undefined, orgId: d.id, role: "dealer" }),
                `Invitation sent to ${e}: Cognito emails a temporary password.`)) {
    inviting.value = null;
  }
}
</script>

<template>
  <article class="console-card">
    <h2>New dealer<span class="dot">.</span></h2>
    <form class="console-form" @submit.prevent="create">
      <label>Name<InputText v-model="name" required data-test="dealer-name" /></label>
      <label>Email<InputText v-model="email" type="email" /></label>
      <label>Phone<InputText v-model="phone" /></label>
      <Button type="submit" label="Create" :loading="busy" />
    </form>
  </article>

  <article class="console-card">
    <h2>Dealers<span class="dot">.</span></h2>
    <Message v-if="message" severity="success" size="small" variant="simple">{{ message }}</Message>
    <Message v-if="error || list.error.value" severity="error" size="small" variant="simple" role="alert">
      {{ error || list.error.value }}</Message>
    <table class="console-table">
      <thead><tr><th>Dealer</th><th>Contact</th><th>Status</th><th class="num">Customers</th>
        <th class="num">Users</th><th class="num">Adapters</th><th></th></tr></thead>
      <tbody>
        <template v-for="d in list.data.value ?? []" :key="d.id">
          <tr>
            <td>{{ d.name }}</td>
            <td class="muted">{{ [d.email, d.phone].filter(Boolean).join(" · ") }}</td>
            <td><span class="status" :class="d.status">{{ d.status }}</span></td>
            <td class="num">{{ d.customers }}</td><td class="num">{{ d.users }}</td><td class="num">{{ d.adapters }}</td>
            <td class="console-actions">
              <Button size="small" severity="secondary" label="Invite user" @click="openInvite(d)" />
              <Button v-if="d.status === 'active'" size="small" severity="danger" label="Suspend" @click="setStatus(d, 'suspended')" />
              <Button v-else size="small" label="Activate" @click="setStatus(d, 'active')" />
            </td>
          </tr>
          <tr v-if="inviting === d.id">
            <td colspan="7">
              <form class="console-form" @submit.prevent="invite(d)">
                <label>Their email<InputText v-model="inviteEmail" type="email" required data-test="invite-email" /></label>
                <label>Name<InputText v-model="inviteName" /></label>
                <Button type="submit" label="Send invitation" :loading="busy" />
              </form>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </article>
</template>
