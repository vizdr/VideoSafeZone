<script setup lang="ts">
// Invoices (DEALER-PANEL-PLAN.md Phase 8, D9, D19): one per customer per month, with a line per adapter
// plan it pays for, at the price stored with the plan. A row opens to its lines.
import { computed, ref } from "vue";
import Button from "openvue/button";
import type { Invoice } from "../../api/types";
import { lineAdapter, lineText, monthLabel } from "../../dealer/billing";
import { money } from "../../pricing";

const props = withDefaults(defineProps<{ invoices: Invoice[]; showCustomer?: boolean; showDealer?: boolean; truncated?: boolean }>(),
                           { showCustomer: true, showDealer: false, truncated: false });
const columns = computed(() => 5 + Number(props.showCustomer) + Number(props.showDealer));
const open = ref(new Set<string>());

function toggle(id: string): void {
  const next = new Set(open.value);
  if (!next.delete(id)) next.add(id);
  open.value = next;
}
</script>

<template>
  <p v-if="!invoices.length" class="muted" data-test="no-invoices">No invoices yet.</p>
  <table v-else class="console-table">
    <thead><tr><th>Month</th><th v-if="showCustomer">Customer</th><th v-if="showDealer">Dealer</th>
      <th class="num">Adapters</th><th class="num">Amount</th><th>Status</th><th></th></tr></thead>
    <tbody>
      <template v-for="inv in invoices" :key="inv.id">
        <tr data-test="invoice">
          <td>{{ monthLabel(inv.periodStart) }}</td>
          <td v-if="showCustomer">
            <RouterLink :to="{ name: 'dealer-invoices', params: { customerId: inv.customer.id } }">{{ inv.customer.name }}</RouterLink>
          </td>
          <td v-if="showDealer">{{ inv.dealer.name }}</td>
          <td class="num">{{ inv.lines.length }}</td>
          <td class="num">{{ money(inv.cents, inv.currency) }}</td>
          <td>{{ inv.status }}<span v-if="inv.provider === 'fake'" class="muted"> · demo</span></td>
          <td><Button size="small" severity="secondary" variant="text" :aria-expanded="open.has(inv.id)"
                      :label="open.has(inv.id) ? 'Hide lines' : 'Lines'" @click="toggle(inv.id)" /></td>
        </tr>
        <tr v-if="open.has(inv.id)" data-test="invoice-lines">
          <td :colspan="columns">
            <ul class="lines">
              <li v-for="l in inv.lines" :key="l.planId">
                <span>{{ lineAdapter(l) }}</span>
                <span class="muted">{{ lineText(l) }}</span>
                <span class="amount">{{ money(l.cents, l.currency) }}</span>
              </li>
            </ul>
          </td>
        </tr>
      </template>
    </tbody>
  </table>
  <p v-if="truncated" class="muted" data-test="truncated">Only the newest invoices are shown.</p>
</template>

<style scoped>
.lines { list-style: none; margin: 0; padding: 0 0 0 1rem; display: grid; gap: 0.3rem; }
.lines li { display: grid; grid-template-columns: minmax(12rem, 1fr) 2fr auto; gap: 0.75rem; }
.lines .amount { text-align: right; }
@media (max-width: 700px) { .lines li { grid-template-columns: 1fr auto; } .lines .muted { grid-column: 1 / -1; } }
</style>
