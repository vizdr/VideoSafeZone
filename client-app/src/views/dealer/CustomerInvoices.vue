<script setup lang="ts">
// A customer's invoices (DEALER-PANEL-PLAN.md §7.1, Phase 8, D9, D19): what the month under way would
// bill so far -- the billing provider's quote -- and the invoices made by billing runs, one per month
// with a line per adapter plan. Data real, money fake: no payment is taken.
import { computed, inject, ref, watch } from "vue";
import { endpoints } from "../../api";
import { errorText } from "../../api/client";
import type { CustomerInvoices } from "../../api/types";
import DemoBanner from "../../components/dealer/DemoBanner.vue";
import InvoiceList from "../../components/dealer/InvoiceList.vue";
import { CUSTOMER } from "../../dealer/customer";
import { lineAdapter, lineText, monthLabel } from "../../dealer/billing";
import { money } from "../../pricing";

const ctx = inject(CUSTOMER)!;
const data = ref<CustomerInvoices | null>(null);
const error = ref("");

async function load(): Promise<void> {
  const id = ctx.customerId.value;
  if (!id) return;
  try {
    const res = await endpoints.customerInvoices(id);
    if (id !== ctx.customerId.value) return;              // another customer was opened meanwhile
    error.value = res.ok ? "" : errorText(res);
    data.value = res.ok ? res.data : null;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}
watch(ctx.customerId, () => { data.value = null; void load(); }, { immediate: true });

const next = computed(() => data.value?.next ?? null);
</script>

<template>
  <article class="console-card">
    <h2>Invoices<span class="dot">.</span></h2>
    <DemoBanner />
    <p v-if="error" role="alert" data-test="invoices-error">{{ error }}</p>
    <template v-if="next">
      <h3>{{ monthLabel(next.period) }} so far</h3>
      <p v-if="next.invoiceId" class="muted" data-test="invoiced">Invoiced already: its invoice is below.</p>
      <p v-else-if="!next.lines.length" class="muted" data-test="nothing-to-pay">
        Nothing to pay this month: every plan is on trial, cancelled, or there is none.</p>
      <template v-else>
        <ul class="estimate" data-test="estimate">
          <li v-for="l in next.lines" :key="l.planId">
            <span>{{ lineAdapter(l) }}</span><span class="muted">{{ lineText(l) }}</span>
            <span class="amount">{{ money(l.cents, l.currency) }}</span>
          </li>
        </ul>
        <p><strong data-test="estimate-total">{{ money(next.cents, next.currency) }}</strong>
          <span class="muted"> — an estimate: the plans as they are now, invoiced when the month is billed.</span></p>
      </template>
    </template>
    <h3>Billed</h3>
    <InvoiceList v-if="data" :invoices="data.invoices" :show-customer="false" :truncated="data.truncated" />
  </article>
</template>

<style scoped>
.estimate { list-style: none; margin: 0 0 0.5rem; padding: 0; display: grid; gap: 0.3rem; }
.estimate li { display: grid; grid-template-columns: minmax(12rem, 1fr) 2fr auto; gap: 0.75rem; }
.estimate .amount { text-align: right; }
@media (max-width: 700px) { .estimate li { grid-template-columns: 1fr auto; } .estimate .muted { grid-column: 1 / -1; } }
</style>
