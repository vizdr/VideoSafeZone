<script setup lang="ts">
// The month under way, so far (DEALER-PANEL-PLAN.md Phase 8, D9): the billing provider's quote -- what a
// run would bill now -- or that the month is invoiced already, or that there is nothing to pay. Shown on
// a customer's Invoices page in the portal and on the holder's Plan & Billing (Phase 9).
import type { NextInvoice } from "../../api/types";
import { lineAdapter, lineText, monthLabel } from "../../dealer/billing";
import { money } from "../../pricing";

defineProps<{ next: NextInvoice }>();
</script>

<template>
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

<style scoped>
.estimate { list-style: none; margin: 0 0 0.5rem; padding: 0; display: grid; gap: 0.3rem; }
.estimate li { display: grid; grid-template-columns: minmax(12rem, 1fr) 2fr auto; gap: 0.75rem; }
.estimate .amount { text-align: right; }
@media (max-width: 700px) { .estimate li { grid-template-columns: 1fr auto; } .estimate .muted { grid-column: 1 / -1; } }
</style>
