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
import NextInvoiceEstimate from "../../components/dealer/NextInvoiceEstimate.vue";
import { CUSTOMER } from "../../dealer/customer";

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
    <NextInvoiceEstimate v-if="next" :next="next" />
    <h3>Billed</h3>
    <InvoiceList v-if="data" :invoices="data.invoices" :show-customer="false" :truncated="data.truncated" />
  </article>
</template>

