<script setup lang="ts">
// Billing (DEALER-PANEL-PLAN.md §7.1, D9): each customer's total over its adapters' plans, and their
// invoices. Data real, money fake: no payment is taken, and there are no card or bank fields at all.
// Invoices are made when an owner or administrator runs a billing period (System → Billing, Phase 8).
import { computed } from "vue";
import { endpoints } from "../../api";
import type { CustomerSummary, InvoiceList as Invoices } from "../../api/types";
import DemoBanner from "../../components/dealer/DemoBanner.vue";
import InvoiceList from "../../components/dealer/InvoiceList.vue";
import { useLoad } from "../../composables/useLoad";
import { money } from "../../pricing";
import { useAuthStore } from "../../stores/auth";

const auth = useAuthStore();
const list = useLoad<CustomerSummary[]>(() => endpoints.listCustomers());
const invoices = useLoad<Invoices>(() => endpoints.listInvoices());
const billed = computed(() => (list.data.value ?? []).filter(c => c.adapters > 0));
const sum = computed(() => billed.value.reduce((n, c) => n + c.centsMonth, 0));
const currency = computed(() => list.data.value?.[0]?.currency ?? "EUR");
</script>

<template>
  <article class="console-card">
    <h2>Billing<span class="dot">.</span></h2>
    <DemoBanner />
    <p v-if="list.error.value" role="alert">{{ list.error.value }}</p>
    <table class="console-table">
      <thead><tr><th>Customer</th><th v-if="auth.role !== 'dealer'">Dealer</th><th class="num">Adapters</th>
        <th class="num">Cameras in plans</th><th class="num">A month</th></tr></thead>
      <tbody>
        <tr v-for="c in billed" :key="c.id">
          <td><RouterLink :to="{ name: 'dealer-plan', params: { customerId: c.id } }">{{ c.name }}</RouterLink></td>
          <td v-if="auth.role !== 'dealer'">{{ c.dealer.name }}</td>
          <td class="num">{{ c.adapters }}</td><td class="num">{{ c.inPlans }}</td>
          <td class="num">{{ money(c.centsMonth, c.currency) }}</td>
        </tr>
      </tbody>
      <tfoot><tr><th>Total</th><th v-if="auth.role !== 'dealer'"></th><th></th><th></th>
        <th class="num" data-test="sum">{{ money(sum, currency) }}</th></tr></tfoot>
    </table>
    <p class="muted">Trial and active plans, at the price saved with each; an annual plan as a twelfth of its year.
      An invoice bills only what is paid for: a plan on trial adds nothing until its trial ends.</p>

    <h3>Invoices</h3>
    <p v-if="invoices.error.value" role="alert" data-test="invoices-error">{{ invoices.error.value }}</p>
    <InvoiceList v-else-if="invoices.data.value" :invoices="invoices.data.value.invoices"
                 :show-dealer="auth.role !== 'dealer'" :truncated="invoices.data.value.truncated" />
  </article>
</template>
