<script setup lang="ts">
// System → Billing (DEALER-PANEL-PLAN.md §7.2, Phase 8, D9): the fake provider's billing run and every
// invoice. A run bills a calendar month (UTC): one invoice per customer with something to pay, a line
// per adapter plan at the price stored with it; a customer billed for that month already keeps its
// invoice. No payment is taken, and there are no card or bank fields anywhere.
import { computed, ref } from "vue";
import Button from "openvue/button";
import Message from "openvue/message";
import { endpoints } from "../../api";
import { errorText } from "../../api/client";
import type { BillingRun, Dealer, InvoiceList as Invoices } from "../../api/types";
import DemoBanner from "../../components/dealer/DemoBanner.vue";
import InvoiceList from "../../components/dealer/InvoiceList.vue";
import { useLoad } from "../../composables/useLoad";
import { currentPeriod, monthLabel } from "../../dealer/billing";
import { money } from "../../pricing";

const dealerFilter = ref("");
const invoices = useLoad<Invoices>(() => endpoints.listInvoices({ dealerId: dealerFilter.value || undefined }));
const dealers = useLoad<Dealer[]>(() => endpoints.listDealers());

const latest = currentPeriod();
const period = ref(latest);
const busy = ref(false), error = ref("");
const run = ref<BillingRun | null>(null);

const summary = computed(() => {
  const r = run.value;
  if (!r) return "";
  const total = r.invoices.reduce((n, i) => n + i.cents, 0);
  const made = r.invoices.length
    ? `${r.invoices.length} invoice${r.invoices.length === 1 ? "" : "s"}, ${money(total, r.invoices[0].currency)}`
    : "no new invoice";
  const rest = [r.alreadyInvoiced && `${r.alreadyInvoiced} invoiced already`,
                r.nothingToBill && `${r.nothingToBill} with nothing to pay`].filter(Boolean).join(", ");
  return `${monthLabel(r.period)}: ${made}${rest ? ` (${rest})` : ""}.`;
});

async function runPeriod(): Promise<void> {
  error.value = "";
  run.value = null;
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(period.value) || period.value > latest) {
    error.value = `Choose a month up to ${monthLabel(latest)}.`;
    return;
  }
  busy.value = true;
  try {
    const res = await endpoints.runBillingPeriod(period.value);
    if (!res.ok) { error.value = errorText(res); return; }
    run.value = res.data;
    await invoices.reload();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <article class="console-card">
    <h2>Billing<span class="dot">.</span></h2>
    <DemoBanner />
    <form class="console-form run" @submit.prevent="runPeriod">
      <label>Month<input v-model="period" type="month" :max="latest" class="p-inputtext" required data-test="period" /></label>
      <Button type="submit" label="Run billing period (fake)" :loading="busy" data-test="run" />
    </form>
    <p class="muted">Each customer with something to pay gets one invoice for the month, with a line per adapter plan
      as it stood at the month's end, at the price saved with it. A plan on trial adds nothing until its trial ends;
      an annual plan is billed a twelfth of its year. A customer invoiced for the month already keeps its invoice.</p>
    <Message v-if="summary" severity="success" size="small" variant="simple" data-test="run-result">{{ summary }}</Message>
    <Message v-for="s in run?.skipped ?? []" :key="s.customer.id" severity="warn" size="small" variant="simple">
      {{ s.customer.name }} wasn't billed: {{ s.reason }}.</Message>
    <Message v-if="error" severity="error" size="small" variant="simple" role="alert" data-test="run-error">{{ error }}</Message>
  </article>

  <article class="console-card">
    <h2>Invoices<span class="dot">.</span></h2>
    <div class="console-form">
      <label>Dealer
        <select v-model="dealerFilter" class="p-inputtext" data-test="dealer-filter" @change="invoices.reload()">
          <option value="">Every dealer</option>
          <option v-for="d in dealers.data.value ?? []" :key="d.id" :value="d.id">{{ d.name }}</option>
        </select>
      </label>
    </div>
    <p v-if="invoices.error.value" role="alert" data-test="invoices-error">{{ invoices.error.value }}</p>
    <InvoiceList v-else-if="invoices.data.value" :invoices="invoices.data.value.invoices" show-dealer
                 :truncated="invoices.data.value.truncated" />
  </article>
</template>

<style scoped>
.run { margin: 0.75rem 0 0.5rem; }
</style>
