<script setup lang="ts">
// Users across the system (DEALER-PANEL-PLAN.md §7.2, D6). An administrator invites anyone; an
// owner a dealer's staff or a customer's holder, and manages everyone but the system's staff --
// what vsz_directory.may_give / may_manage allow, which decide again. Disable signs every session
// out; a person who hasn't signed in yet gets the invitation again, one who has a password reset.
import { computed, ref } from "vue";
import Button from "openvue/button";
import InputText from "openvue/inputtext";
import Message from "openvue/message";
import { endpoints } from "../../api";
import { errorText } from "../../api/client";
import type { CustomerSummary, Dealer, DirectoryUser, Me, MemberRole, UserAction } from "../../api/types";
import { ROLE_LABELS } from "../../auth/roles";
import { useLoad } from "../../composables/useLoad";

const users = useLoad<DirectoryUser[]>(() => endpoints.listUsers());
const me = useLoad<Me>(() => endpoints.me());
const dealers = useLoad<Dealer[]>(() => endpoints.listDealers());
const customers = useLoad<CustomerSummary[]>(() => endpoints.listCustomers());

const filter = ref("");
const shown = computed(() => {
  const words = filter.value.toLowerCase().split(/\s+/).filter(Boolean);
  return (users.data.value ?? []).filter(u => {
    const text = [u.email, u.name ?? "", ...u.memberships.map(m => `${m.org} ${m.role}`)].join(" ").toLowerCase();
    return words.every(w => text.includes(w));
  });
});

type Org = { id: string; label: string; kind: "platform" | "dealer" | "customer" };
const orgs = computed<Org[]>(() => [
  ...(me.data.value?.org?.kind === "platform" ? [{ id: me.data.value.org.id, label: `${me.data.value.org.name} (platform)`, kind: "platform" as const }] : []),
  ...(dealers.data.value ?? []).map(d => ({ id: d.id, label: `${d.name} (dealer)`, kind: "dealer" as const })),
  ...(customers.data.value ?? []).map(c => ({ id: c.id, label: `${c.name} (customer of ${c.dealer.name})`, kind: "customer" as const })),
]);
const ROLES_BY_KIND: Record<Org["kind"], MemberRole[]> = {
  platform: ["system_owner", "system_admin"], dealer: ["dealer"], customer: ["customer", "customer_admin", "customer_user"],
};
const email = ref(""), name = ref(""), orgId = ref(""), role = ref<MemberRole | "">("");
const roles = computed(() => {
  const kind = orgs.value.find(o => o.id === orgId.value)?.kind;
  const all = kind ? ROLES_BY_KIND[kind] : [];
  return isAdmin.value ? all : all.filter(r => r === "dealer" || r === "customer");
});
const isAdmin = computed(() => me.data.value?.role === "system_admin");
const SYSTEM_STAFF: MemberRole[] = ["system_owner", "system_admin"];
const canManage = (u: DirectoryUser) =>
  !isMe(u) && (isAdmin.value || !u.memberships.some(m => SYSTEM_STAFF.includes(m.role)));

const message = ref(""), error = ref(""), busy = ref(false);

async function invite(): Promise<void> {
  if (!role.value) { error.value = "Choose a role."; return; }
  busy.value = true;
  error.value = message.value = "";
  try {
    const e = email.value.trim();
    const res = await endpoints.inviteUser({ email: e, name: name.value.trim() || undefined, orgId: orgId.value, role: role.value });
    if (!res.ok) { error.value = errorText(res); return; }
    message.value = `Invitation sent to ${e}: Cognito emails a temporary password.`;
    email.value = name.value = "";
    await users.reload();
  } finally {
    busy.value = false;
  }
}

const ACTION_DONE: Record<UserAction, string> = {
  "disable": "disabled -- signed out everywhere", "enable": "enabled",
  "reset-password": "sent a code to reset the password", "resend-invite": "sent the invitation again",
};
async function act(u: DirectoryUser, action: UserAction): Promise<void> {
  busy.value = true;
  error.value = message.value = "";
  try {
    const res = await endpoints.userAction(u.id, action);
    if (!res.ok) { error.value = errorText(res); return; }
    message.value = `${u.email}: ${ACTION_DONE[action]}.`;
    await users.reload();
  } finally {
    busy.value = false;
  }
}
const isMe = (u: DirectoryUser) => u.email.toLowerCase() === me.data.value?.email.toLowerCase();
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "never");
</script>

<template>
  <article class="console-card">
    <h2>Invite someone<span class="dot">.</span></h2>
    <form class="console-form" @submit.prevent="invite">
      <label>Email<InputText v-model="email" type="email" required data-test="user-email" /></label>
      <label>Name<InputText v-model="name" /></label>
      <label>Organisation
        <select v-model="orgId" class="p-inputtext" required data-test="user-org" @change="role = ''">
          <option value="" disabled>choose…</option>
          <option v-for="o in orgs" :key="o.id" :value="o.id">{{ o.label }}</option>
        </select>
      </label>
      <label>Role
        <select v-model="role" class="p-inputtext" required data-test="user-role">
          <option value="" disabled>choose…</option>
          <option v-for="r in roles" :key="r" :value="r">{{ ROLE_LABELS[r] }}</option>
        </select>
      </label>
      <Button type="submit" label="Send invitation" :loading="busy" />
    </form>
  </article>

  <article class="console-card">
    <h2>Users<span class="dot">.</span></h2>
    <div class="console-form"><label>Search<InputText v-model="filter" placeholder="email, organisation, role" /></label></div>
    <Message v-if="message" severity="success" size="small" variant="simple">{{ message }}</Message>
    <Message v-if="error || users.error.value" severity="error" size="small" variant="simple" role="alert">
      {{ error || users.error.value }}</Message>
    <table class="console-table">
      <thead><tr><th>Email</th><th>Where, as what</th><th>Status</th><th>Last sign-in</th><th></th></tr></thead>
      <tbody>
        <tr v-for="u in shown" :key="u.id" :data-test="`user-${u.email}`">
          <td>{{ u.email }}<div v-if="u.name" class="muted">{{ u.name }}</div></td>
          <td><div v-for="m in u.memberships" :key="m.orgId">{{ ROLE_LABELS[m.role] ?? m.role }} · {{ m.org }}</div></td>
          <td><span class="status" :class="u.status">{{ u.status }}</span></td>
          <td class="muted">{{ when(u.lastSignInAt) }}</td>
          <td class="console-actions">
            <template v-if="canManage(u)">
              <Button v-if="u.status !== 'disabled'" size="small" severity="danger" label="Disable" @click="act(u, 'disable')" />
              <Button v-else size="small" label="Enable" @click="act(u, 'enable')" />
              <Button v-if="u.status === 'active'" size="small" severity="secondary" label="Reset password" @click="act(u, 'reset-password')" />
              <Button v-if="u.status === 'invited'" size="small" severity="secondary" label="Resend invitation" @click="act(u, 'resend-invite')" />
            </template>
            <span v-else-if="isMe(u)" class="muted">you</span>
          </td>
        </tr>
      </tbody>
    </table>
  </article>
</template>
