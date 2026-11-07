<script setup lang="ts">
// A customer's details (DEALER-PANEL-PLAN.md §7.1): name, reference, contact; suspend (its people
// can't sign in, their cameras keep recording) or reactivate; its sites.
import { inject, ref, watch } from "vue";
import Button from "openvue/button";
import InputText from "openvue/inputtext";
import Message from "openvue/message";
import { endpoints } from "../../api";
import { errorText } from "../../api/client";
import { CUSTOMER } from "../../dealer/customer";

const ctx = inject(CUSTOMER)!;
const FIELDS = ["name", "reference", "email", "phone"] as const;
type Field = typeof FIELDS[number];
const form = ref<Record<Field, string>>({ name: "", reference: "", email: "", phone: "" });
watch(ctx.detail, d => {
  if (d) form.value = { name: d.name, reference: d.reference ?? "", email: d.email ?? "", phone: d.phone ?? "" };
}, { immediate: true });

const message = ref(""), error = ref(""), busy = ref(false);

async function update(change: Record<string, string>, done: string): Promise<void> {
  busy.value = true;
  message.value = error.value = "";
  try {
    const res = await endpoints.updateCustomer(ctx.customerId.value, change);
    if (!res.ok) { error.value = errorText(res); return; }
    message.value = done;
    await ctx.reload();
  } finally {
    busy.value = false;
  }
}

function save(): void {
  const d = ctx.detail.value;
  if (!d) return;
  const change = Object.fromEntries(FIELDS.filter(f => form.value[f].trim() !== ((d[f] as string | null) ?? ""))
    .map(f => [f, form.value[f].trim()]));
  if (!Object.keys(change).length) { message.value = "Nothing changed."; return; }
  void update(change, "Saved.");
}
</script>

<template>
  <article v-if="ctx.detail.value" class="console-card">
    <h2>Details<span class="dot">.</span></h2>
    <form class="console-form" @submit.prevent="save">
      <label>Name<InputText v-model="form.name" required data-test="name" /></label>
      <label>Reference<InputText v-model="form.reference" data-test="reference" /></label>
      <label>Email<InputText v-model="form.email" type="email" /></label>
      <label>Phone<InputText v-model="form.phone" /></label>
      <Button type="submit" label="Save" :loading="busy" />
    </form>
    <Message v-if="message" severity="success" size="small" variant="simple">{{ message }}</Message>
    <Message v-if="error" severity="error" size="small" variant="simple" data-test="error">{{ error }}</Message>

    <h3>Sites</h3>
    <ul class="sites">
      <li v-for="s in ctx.detail.value.sites" :key="s.id">{{ s.name }} <span class="muted">· {{ s.timezone }}</span></li>
    </ul>
    <p class="muted">Customer since {{ new Date(ctx.detail.value.createdAt).toLocaleDateString() }} · dealer {{ ctx.detail.value.dealer.name }}</p>

    <div class="console-actions">
      <Button v-if="ctx.detail.value.status === 'active'" severity="danger" variant="outlined" label="Suspend the customer"
              data-test="suspend" @click="update({ status: 'suspended' }, 'Suspended: its people can\'t sign in.')" />
      <Button v-else label="Reactivate the customer" data-test="reactivate" @click="update({ status: 'active' }, 'Active again.')" />
    </div>
  </article>
</template>

<style scoped>
h3 { margin: 1.25rem 0 0.4rem; font-size: 1rem; }
.sites { margin: 0 0 0.75rem; padding-left: 1.2rem; }
</style>
