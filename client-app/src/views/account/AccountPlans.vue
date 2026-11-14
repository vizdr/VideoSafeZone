<script setup lang="ts">
// Plan & Billing (DEALER-PANEL-PLAN.md D18, D19), for an account's holder: each adapter's plan, read-only
// -- the cameras by class, storage, payment, price -- with its history; the total; this month so far and
// the invoices (D9: fake, and the banner says so). The dealer changes plans, so their contact is here.
import { computed, ref, watch } from "vue";
import { endpoints } from "../../api";
import { errorText } from "../../api/client";
import type { AccountPlans, Me, Plan } from "../../api/types";
import DemoBanner from "../../components/dealer/DemoBanner.vue";
import InvoiceList from "../../components/dealer/InvoiceList.vue";
import NextInvoiceEstimate from "../../components/dealer/NextInvoiceEstimate.vue";
import { adapterLabel } from "../../dealer/customer";
import { STATUS_LABELS, camerasText, intervalText, planChanges } from "../../dealer/plans";
import { money } from "../../pricing";
import AccountPicker from "./AccountPicker.vue";

const props = defineProps<{ accounts: Me["accounts"] }>();
const chosen = ref(props.accounts[0]?.id ?? "");
watch(() => props.accounts.map(a => a.id).join(), () => {
  if (!props.accounts.some(a => a.id === chosen.value)) chosen.value = props.accounts[0]?.id ?? "";
});

const data = ref<AccountPlans | null>(null);
const error = ref("");
watch(chosen, async id => {
  data.value = null;
  error.value = "";
  if (!id) return;
  try {
    const res = await endpoints.accountPlans(id);
    if (id !== chosen.value) return;                    // another account was picked meanwhile
    error.value = res.ok ? "" : errorText(res);
    data.value = res.ok ? res.data : null;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}, { immediate: true });

const date = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString() : "");
const per = (p: Plan) => (p.interval === "annual" ? "year" : "month");
const price = (p: Plan) => (p.status === "cancelled" || p.status === "ended" ? "no charge" : `${money(p.centsPeriod, p.currency)} a ${per(p)}`);

function stateText(p: Plan | null): string {
  if (!p) return "No plan yet: your dealer sets one.";
  const state = p.effectiveStatus === "trial" ? `Trial until ${date(p.trialEndsAt)}` : STATUS_LABELS[p.effectiveStatus] ?? p.effectiveStatus;
  return p.effectiveStatus === "cancelled" || p.effectiveStatus === "ended" ? state : `${state} · ${price(p)}`;
}
const contact = computed(() => [data.value?.dealer.email, data.value?.dealer.phone].filter(Boolean).join(", "));
const historyOf = (plans: Plan[]) => plans.map((p, i) => ({ p, changes: planChanges(plans[i + 1] ?? null, p) }));
</script>

<template>
  <article class="console-card">
    <h2>Plan &amp; Billing<span class="dot">.</span></h2>
    <DemoBanner />
    <AccountPicker v-model="chosen" :accounts="accounts" />
    <p v-if="error" role="alert" data-test="plans-error">{{ error }}</p>
    <template v-if="data">
      <p class="muted" data-test="dealer">Your plans are set by your dealer, {{ data.dealer.name }}<template v-if="contact">
        ({{ contact }})</template>: ask them for a change.</p>
      <p v-if="!data.adapters.length" class="muted">No cloud adapter on this account yet.</p>
      <section v-for="a in data.adapters" :key="a.id" class="adapter" data-test="adapter-plan">
        <h3>{{ adapterLabel(a) }}</h3>
        <p data-test="plan-state">{{ stateText(a.plan) }}</p>
        <dl v-if="a.plan" class="plan">
          <dt>Cameras</dt><dd>{{ camerasText(a.plan.lines) }}</dd>
          <dt>Cloud storage</dt><dd>{{ a.plan.retentionDays }} days</dd>
          <dt>Payment</dt><dd>{{ intervalText(a.plan.interval) }}</dd>
        </dl>
        <details v-if="a.history.length" class="history">
          <summary>Plan history ({{ a.history.length }} version{{ a.history.length === 1 ? "" : "s" }})</summary>
          <ol>
            <li v-for="{ p, changes } in historyOf(a.history)" :key="p.id" :data-test="`version-${p.version}`">
              <strong>Version {{ p.version }}</strong>, {{ date(p.createdAt) }}: {{ changes.join("; ") }}
              <span class="muted"> — {{ price(p) }}</span>
            </li>
          </ol>
        </details>
      </section>
      <p class="total" data-test="total">Total: {{ data.total.adapters }} adapter{{ data.total.adapters === 1 ? "" : "s" }},
        {{ money(data.total.centsMonth, data.total.currency) }} a month
        <span class="muted">(trial and active plans; an annual plan as a twelfth of its year)</span></p>
      <NextInvoiceEstimate :next="data.next" />
      <h3>Invoices</h3>
      <InvoiceList :invoices="data.invoices" :show-customer="false" :truncated="data.truncated" />
    </template>
  </article>
</template>

<style scoped>
.adapter { padding: 0.75rem 0; border-bottom: 1px solid var(--p-surface-700); }
.adapter h3 { margin: 0 0 0.35rem; font-size: 1.05rem; }
.plan { display: grid; grid-template-columns: max-content 1fr; gap: 0.2rem 1rem; margin: 0.5rem 0; }
.plan dt { color: var(--p-text-muted-color); }
.plan dd { margin: 0; }
.history ol { margin: 0.5rem 0 0; padding-left: 1.25rem; display: grid; gap: 0.25rem; }
.total { margin: 1rem 0; }
</style>
