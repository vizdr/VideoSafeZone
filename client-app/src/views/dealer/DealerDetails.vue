<script setup lang="ts">
// Dealer details (DEALER-PANEL-PLAN.md §7.1, D6): the dealer's own company name, contact and address.
import { ref, watch } from "vue";
import Button from "openvue/button";
import InputText from "openvue/inputtext";
import Message from "openvue/message";
import { endpoints } from "../../api";
import { errorText } from "../../api/client";
import type { Address, DealerDetails } from "../../api/types";
import { useLoad } from "../../composables/useLoad";

const details = useLoad<DealerDetails>(() => endpoints.getDealer());
const LINES: Array<[keyof Address, string]> = [["line1", "Address"], ["line2", "Address, line 2"], ["city", "City"],
                                               ["postcode", "Postcode"], ["country", "Country"]];
const form = ref({ name: "", email: "", phone: "", address: {} as Record<keyof Address, string> });
watch(() => details.data.value, d => {
  if (!d) return;
  form.value = { name: d.name, email: d.email ?? "", phone: d.phone ?? "",
                 address: Object.fromEntries(LINES.map(([k]) => [k, d.address?.[k] ?? ""])) as Record<keyof Address, string> };
}, { immediate: true });

const message = ref(""), error = ref(""), busy = ref(false);

async function save(): Promise<void> {
  const d = details.data.value;
  if (!d) return;
  const f = form.value;
  const change: Record<string, unknown> = {};
  for (const k of ["name", "email", "phone"] as const) {
    if (f[k].trim() !== (d[k] ?? "")) change[k] = f[k].trim();
  }
  const address = Object.fromEntries(LINES.map(([k]) => [k, f.address[k].trim()]).filter(([, v]) => v));
  if (JSON.stringify(address) !== JSON.stringify(Object.fromEntries(LINES.map(([k]) => [k, d.address?.[k] ?? ""]).filter(([, v]) => v)))) {
    change.address = Object.keys(address).length ? address : null;
  }
  if (!Object.keys(change).length) { message.value = "Nothing changed."; return; }
  busy.value = true;
  message.value = error.value = "";
  try {
    const res = await endpoints.updateOwnDealer(change);
    if (!res.ok) { error.value = errorText(res); return; }
    message.value = "Saved.";
    await details.reload();
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <article class="console-card">
    <h2>Dealer details<span class="dot">.</span></h2>
    <p v-if="details.error.value" role="alert">{{ details.error.value }}</p>
    <form v-if="details.data.value" class="console-form" @submit.prevent="save">
      <label>Company name<InputText v-model="form.name" required data-test="name" /></label>
      <label>Email<InputText v-model="form.email" type="email" /></label>
      <label>Phone<InputText v-model="form.phone" data-test="phone" /></label>
      <label v-for="[k, label] in LINES" :key="k">{{ label }}<InputText v-model="form.address[k]" :data-test="`address-${k}`" /></label>
      <Button type="submit" label="Save" :loading="busy" />
    </form>
    <Message v-if="message" severity="success" size="small" variant="simple">{{ message }}</Message>
    <Message v-if="error" severity="error" size="small" variant="simple" data-test="error">{{ error }}</Message>
  </article>
</template>
