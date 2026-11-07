<script setup lang="ts">
// The customer list (DEALER-PANEL-PLAN.md §7.1): search, status, and per customer its holder, its
// adapters, cameras in plans against cameras registered, and its plans' monthly total (D19). "New
// customer" makes one with its first site and, optionally, invites its holder. A dealer sees its
// own; owners and administrators every dealer's, with a filter, and choose the dealer for a new one.
import { computed, ref } from "vue";
import Button from "openvue/button";
import InputText from "openvue/inputtext";
import Message from "openvue/message";
import { endpoints } from "../../api";
import { errorText } from "../../api/client";
import type { CustomerSummary, Dealer } from "../../api/types";
import { useLoad } from "../../composables/useLoad";
import { browserZone, timeZones } from "../../dealer/zones";
import { money } from "../../pricing";
import { useAuthStore } from "../../stores/auth";

const auth = useAuthStore();
const staff = computed(() => auth.role !== "dealer");
const dealerFilter = ref("");
const list = useLoad<CustomerSummary[]>(() => endpoints.listCustomers(dealerFilter.value || undefined));
const dealers = useLoad<Dealer[]>(() => (staff.value ? endpoints.listDealers() : Promise.resolve({ ok: true, status: 200, data: [] })));

const search = ref(""), status = ref("");
const shown = computed(() => {
  const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
  return (list.data.value ?? []).filter(c => {
    const text = [c.name, c.reference ?? "", c.holder?.email ?? "", c.dealer.name].join(" ").toLowerCase();
    return (!status.value || c.status === status.value) && words.every(w => text.includes(w));
  });
});
const camerasText = (c: CustomerSummary) => `${c.inPlans} / ${c.registered ?? "—"}`;

// --- New customer ---
const zones = timeZones();
const creating = ref(false);
const form = ref({ name: "", reference: "", email: "", phone: "", siteName: "Main site", timezone: browserZone(),
                   holderEmail: "", holderName: "", dealerId: "" });
const message = ref(""), warning = ref(""), error = ref(""), busy = ref(false);

function openForm(): void {
  creating.value = !creating.value;
  if (!form.value.dealerId) form.value.dealerId = dealers.data.value?.find(d => d.name === "Direct")?.id ?? "";
}

async function create(): Promise<void> {
  const f = form.value;
  busy.value = true;
  message.value = warning.value = error.value = "";
  try {
    const res = await endpoints.createCustomer({
      name: f.name.trim(), reference: f.reference.trim() || undefined, email: f.email.trim() || undefined,
      phone: f.phone.trim() || undefined, site: { name: f.siteName.trim(), timezone: f.timezone },
      ...(staff.value ? { dealerId: f.dealerId } : {}),
      ...(f.holderEmail.trim() ? { holder: { email: f.holderEmail.trim(), name: f.holderName.trim() || undefined } } : {}),
    });
    if (!res.ok) { error.value = errorText(res); return; }
    message.value = `Customer ${f.name.trim()} created.`
      + (res.data.holder ? ` Invitation sent to ${res.data.holder.invited}.` : "");
    if (res.data.holderError) warning.value = `The holder wasn't invited: ${res.data.holderError}. Invite them from the customer's Users page.`;
    form.value = { ...form.value, name: "", reference: "", email: "", phone: "", holderEmail: "", holderName: "" };
    creating.value = false;
    await list.reload();
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <article class="console-card">
    <h2>Customers<span class="dot">.</span></h2>
    <div class="console-form">
      <label>Search<InputText v-model="search" placeholder="name, reference, holder" data-test="search" /></label>
      <label>Status
        <select v-model="status" class="p-inputtext" data-test="status-filter">
          <option value="">any</option><option value="active">active</option><option value="suspended">suspended</option>
        </select>
      </label>
      <label v-if="staff">Dealer
        <select v-model="dealerFilter" class="p-inputtext" data-test="dealer-filter" @change="list.reload()">
          <option value="">every dealer</option>
          <option v-for="d in dealers.data.value ?? []" :key="d.id" :value="d.id">{{ d.name }}</option>
        </select>
      </label>
      <Button :label="creating ? 'Close' : 'New customer'" :severity="creating ? 'secondary' : undefined" @click="openForm" />
    </div>

    <form v-if="creating" class="console-form new-customer" data-test="new-customer" @submit.prevent="create">
      <label v-if="staff">Dealer
        <select v-model="form.dealerId" class="p-inputtext" required data-test="new-dealer">
          <option value="" disabled>choose…</option>
          <option v-for="d in dealers.data.value ?? []" :key="d.id" :value="d.id">{{ d.name }}</option>
        </select>
      </label>
      <label>Name<InputText v-model="form.name" required data-test="new-name" /></label>
      <label>Reference<InputText v-model="form.reference" data-test="new-reference" /></label>
      <label>Email<InputText v-model="form.email" type="email" /></label>
      <label>Phone<InputText v-model="form.phone" /></label>
      <label>Site<InputText v-model="form.siteName" required data-test="new-site" /></label>
      <label>Time zone
        <select v-model="form.timezone" class="p-inputtext" required data-test="new-timezone">
          <option v-for="z in zones" :key="z" :value="z">{{ z }}</option>
        </select>
      </label>
      <label>Holder's email (invited)<InputText v-model="form.holderEmail" type="email" data-test="new-holder" /></label>
      <label>Holder's name<InputText v-model="form.holderName" /></label>
      <Button type="submit" label="Create customer" :loading="busy" />
    </form>

    <Message v-if="message" severity="success" size="small" variant="simple">{{ message }}</Message>
    <Message v-if="warning" severity="warn" size="small" variant="simple" data-test="warning">{{ warning }}</Message>
    <Message v-if="error || list.error.value" severity="error" size="small" variant="simple" data-test="error">
      {{ error || list.error.value }}</Message>

    <table class="console-table">
      <thead><tr><th>Customer</th><th v-if="staff">Dealer</th><th>Holder</th><th class="num">Adapters</th>
        <th class="num" title="in plans / registered">Cameras</th><th class="num">A month</th><th>Since</th></tr></thead>
      <tbody>
        <tr v-for="c in shown" :key="c.id" :data-test="`customer-${c.name}`">
          <td>
            <RouterLink :to="{ name: 'dealer-plan', params: { customerId: c.id } }">{{ c.name }}</RouterLink>
            <span v-if="c.reference" class="muted"> · {{ c.reference }}</span>
            <span v-if="c.status !== 'active'" class="status" :class="c.status">{{ c.status }}</span>
          </td>
          <td v-if="staff">{{ c.dealer.name }}</td>
          <td><template v-if="c.holder">{{ c.holder.email }}
            <span v-if="c.holder.status !== 'active'" class="status" :class="c.holder.status">{{ c.holder.status }}</span></template>
            <span v-else class="muted">none yet</span></td>
          <td class="num">{{ c.adapters }}</td>
          <td class="num">{{ camerasText(c) }}</td>
          <td class="num">{{ money(c.centsMonth, c.currency) }}</td>
          <td class="muted">{{ new Date(c.createdAt).toLocaleDateString() }}</td>
        </tr>
        <tr v-if="list.data.value && !shown.length"><td :colspan="staff ? 7 : 6" class="muted">No customer matches.</td></tr>
      </tbody>
    </table>
  </article>
</template>

<style scoped>
.new-customer { margin: 1rem 0; padding: 1rem; border: 1px solid var(--p-content-border-color); border-radius: 0.5rem; }
.status { margin-left: 0.4rem; }
</style>
