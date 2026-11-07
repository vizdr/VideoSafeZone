<script setup lang="ts">
// The customer's people (DEALER-PANEL-PLAN.md §7.1, D6, D18): the holder, Super Admins and users,
// with what the caller may do to each -- disable, enable, reset a password, resend an invitation.
// A customer without a holder gets one invited here; Super Admins and users are the holder's to
// add (Account → Additional users, Phase 9).
import { inject, ref, watch } from "vue";
import Button from "openvue/button";
import InputText from "openvue/inputtext";
import Message from "openvue/message";
import { endpoints } from "../../api";
import { errorText } from "../../api/client";
import type { CustomerPerson, UserAction } from "../../api/types";
import { ROLE_LABELS } from "../../auth/roles";
import { CUSTOMER } from "../../dealer/customer";

const ctx = inject(CUSTOMER)!;
const people = ref<CustomerPerson[] | null>(null);
const message = ref(""), error = ref(""), busy = ref(false);
const email = ref(""), name = ref("");

async function load(): Promise<void> {
  const res = await endpoints.customerUsers(ctx.customerId.value);
  if (res.ok) people.value = res.data;
  else error.value = errorText(res);
}
watch(ctx.customerId, () => { people.value = null; void load(); }, { immediate: true });

async function run(work: () => Promise<{ ok: boolean; status: number; data: { error?: string; message?: string } }>,
                   done: string): Promise<boolean> {
  busy.value = true;
  message.value = error.value = "";
  try {
    const res = await work();
    if (!res.ok) { error.value = errorText(res as never); return false; }
    message.value = done;
    await Promise.all([load(), ctx.reload()]);
    return true;
  } finally {
    busy.value = false;
  }
}

async function inviteHolder(): Promise<void> {
  const e = email.value.trim();
  if (await run(() => endpoints.inviteUser({ email: e, name: name.value.trim() || undefined,
                                             orgId: ctx.customerId.value, role: "customer" }),
                `Invitation sent to ${e}: Cognito emails a temporary password.`)) {
    email.value = name.value = "";
  }
}

const DONE: Record<UserAction, string> = {
  "disable": "disabled -- signed out everywhere", "enable": "enabled",
  "reset-password": "sent a code to reset the password", "resend-invite": "sent the invitation again",
};
const act = (p: CustomerPerson, action: UserAction) =>
  run(() => endpoints.userAction(p.id, action), `${p.email}: ${DONE[action]}.`);
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "never");
</script>

<template>
  <article class="console-card">
    <h2>Users<span class="dot">.</span></h2>
    <form v-if="ctx.detail.value && !ctx.detail.value.holder" class="console-form" data-test="invite-holder" @submit.prevent="inviteHolder">
      <label>The account holder's email<InputText v-model="email" type="email" required data-test="holder-email" /></label>
      <label>Name<InputText v-model="name" /></label>
      <Button type="submit" label="Invite the holder" :loading="busy" />
    </form>
    <Message v-if="message" severity="success" size="small" variant="simple">{{ message }}</Message>
    <Message v-if="error" severity="error" size="small" variant="simple" data-test="error">{{ error }}</Message>
    <table v-if="people" class="console-table">
      <thead><tr><th>Email</th><th>Role</th><th>Status</th><th>Last sign-in</th><th></th></tr></thead>
      <tbody>
        <tr v-for="p in people" :key="p.id" :data-test="`person-${p.email}`">
          <td>{{ p.email }}<div v-if="p.name" class="muted">{{ p.name }}</div></td>
          <td>{{ ROLE_LABELS[p.role] ?? p.role }}</td>
          <td><span class="status" :class="p.status">{{ p.status }}</span></td>
          <td class="muted">{{ when(p.lastSignInAt) }}</td>
          <td class="console-actions">
            <template v-if="p.manageable">
              <Button v-if="p.status !== 'disabled'" size="small" severity="danger" label="Disable" @click="act(p, 'disable')" />
              <Button v-else size="small" label="Enable" @click="act(p, 'enable')" />
              <Button v-if="p.status === 'active'" size="small" severity="secondary" label="Reset password" @click="act(p, 'reset-password')" />
              <Button v-if="p.status === 'invited'" size="small" severity="secondary" label="Resend invitation" @click="act(p, 'resend-invite')" />
            </template>
          </td>
        </tr>
        <tr v-if="!people.length"><td colspan="5" class="muted">Nobody yet.</td></tr>
      </tbody>
    </table>
    <p class="muted">Super Admins and users are added by the account holder, under Account → Additional users.</p>
  </article>
</template>
