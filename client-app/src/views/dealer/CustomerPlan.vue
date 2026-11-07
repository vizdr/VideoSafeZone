<script setup lang="ts">
// Cloud plan (DEALER-PANEL-PLAN.md §7.1, D9, D19): one plan per adapter -- cameras per class,
// storage duration, payment -- with the price as it is edited. The preview is the page's
// (pricing.ts); the server prices the plan again when it is saved, and its figure is the one kept.
// Plans aren't enforced: plan vs. actual is a hint.
import { computed, inject, ref, watch } from "vue";
import Button from "openvue/button";
import Message from "openvue/message";
import { endpoints } from "../../api";
import { errorText } from "../../api/client";
import type { BillingInterval, PlanLine, Pricing } from "../../api/types";
import { useLoad } from "../../composables/useLoad";
import { CUSTOMER, adapterLabel, useAdapterChoice } from "../../dealer/customer";
import { RECORDINGS, RECORDING_LABELS, RESOLUTIONS, RESOLUTION_LABELS, STATUS_LABELS, linesOf } from "../../dealer/plans";
import { money, pricePlan } from "../../pricing";

const ctx = inject(CUSTOMER)!;
const pricing = useLoad<Pricing>(() => endpoints.getPricing());
const { list, selected, choose } = useAdapterChoice(ctx, "dealer-plan");
const plan = computed(() => selected.value?.plan ?? null);

const key = (r: string, c: string) => `${r}/${c}`;
const qty = ref<Record<string, number | string>>({});
const retention = ref(30);
const interval = ref<BillingInterval>("monthly");
const days = computed(() => pricing.data.value?.settings.retention_days ?? []);

function reset(): void {
  qty.value = Object.fromEntries(linesOf(plan.value).map(l => [key(l.resolution, l.recording), l.quantity]));
  retention.value = plan.value?.retentionDays ?? (days.value.includes(30) ? 30 : days.value[0] ?? 30);
  interval.value = plan.value?.interval ?? "monthly";
}
watch(() => [selected.value?.id, plan.value?.version, days.value.join()], reset, { immediate: true });

const lines = computed<PlanLine[]>(() => RESOLUTIONS.flatMap(resolution => RECORDINGS.map(recording => {
  const v = qty.value[key(resolution, recording)];
  return { resolution, recording, quantity: v === "" || v === undefined ? 0 : Number(v) };
})));
const valid = computed(() => lines.value.every(l => Number.isInteger(l.quantity) && l.quantity >= 0 && l.quantity <= 100));
const discount = computed(() => pricing.data.value?.settings.annual_discount_percent ?? 0);
const currency = computed(() => pricing.data.value?.settings.currency ?? "EUR");
const preview = computed(() => (pricing.data.value && valid.value
  ? pricePlan(pricing.data.value.prices, discount.value, lines.value, retention.value, interval.value) : null));
const per = (i: BillingInterval) => (i === "annual" ? "year" : "month");
const inPlan = computed(() => lines.value.reduce((n, l) => n + l.quantity, 0));
const changed = computed(() => !plan.value || plan.value.effectiveStatus === "cancelled"
  || plan.value.retentionDays !== retention.value || plan.value.interval !== interval.value
  || linesOf(plan.value).some((l, i) => l.quantity !== lines.value[i].quantity));

const date = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString() : "");
const statusText = computed(() => {
  const p = plan.value;
  if (!p) return "No plan yet.";
  const state = p.effectiveStatus === "trial" ? `Trial until ${date(p.trialEndsAt)}` : STATUS_LABELS[p.effectiveStatus];
  const price = p.effectiveStatus === "cancelled" ? "" : ` · ${money(p.centsPeriod, p.currency)} a ${per(p.interval)}`;
  return `${state} · version ${p.version}${price}`;
});
const onAdapter = (r: string, c: string) => selected.value?.actual?.byClass[key(r, c)] ?? 0;
const actualText = computed(() => {
  const a = selected.value?.actual;
  if (!a) return "Cameras on this adapter: unknown (the camera registry couldn't be read).";
  return `${inPlan.value} in plan, ${a.total} on this adapter`
    + (a.unclassified ? ` (${a.unclassified} of unknown picture size)` : "") + " — plans aren't enforced.";
});
const total = computed(() => ctx.adapters.value?.total);

const message = ref(""), error = ref(""), busy = ref(false), confirming = ref(false);

async function save(): Promise<void> {
  const a = selected.value;
  if (!a || !valid.value) { error.value = "Each camera count is a whole number from 0 to 100."; return; }
  busy.value = true;
  message.value = error.value = "";
  try {
    const res = await endpoints.putPlan(a.id, { lines: lines.value, retentionDays: retention.value,
                                                interval: interval.value, basedOn: a.plan?.version ?? 0 });
    if (!res.ok) {
      error.value = errorText(res);
      if (res.status === 409) await ctx.reload();
      return;
    }
    const p = res.data.plan;
    message.value = res.data.changed
      ? `Saved: version ${p.version}, ${money(p.centsPeriod, p.currency)} a ${per(p.interval)}.` : "Nothing changed.";
    await ctx.reload();
  } finally {
    busy.value = false;
  }
}

async function cancel(): Promise<void> {
  const a = selected.value;
  if (!a?.plan) return;
  busy.value = true;
  message.value = error.value = "";
  try {
    const res = await endpoints.cancelPlan(a.id, a.plan.version);
    if (!res.ok) { error.value = errorText(res); return; }
    confirming.value = false;
    message.value = `Subscription cancelled (version ${res.data.plan.version}). Saving a plan starts it again.`;
    await ctx.reload();
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <article class="console-card">
    <h2>Cloud plan<span class="dot">.</span></h2>
    <Message v-if="ctx.adapters.value && !list.length" severity="info" size="small" variant="simple" data-test="no-adapter">
      No adapter is activated for this customer yet. Activating one with its Cloud ID comes in Phase 6.</Message>
    <template v-if="selected">
      <div class="console-form">
        <label>Adapter (one plan each)
          <select class="p-inputtext" :value="selected.id" data-test="adapter" @change="choose(($event.target as HTMLSelectElement).value)">
            <option v-for="a in list" :key="a.id" :value="a.id">{{ adapterLabel(a) }}</option>
          </select>
        </label>
      </div>
      <p class="plan-status" data-test="plan-status">{{ statusText }}</p>

      <table class="console-table plan-grid">
        <thead><tr><th>Cloud cameras</th><th v-for="c in RECORDINGS" :key="c">{{ RECORDING_LABELS[c] }}</th></tr></thead>
        <tbody>
          <tr v-for="r in RESOLUTIONS" :key="r">
            <th>{{ RESOLUTION_LABELS[r] }}</th>
            <td v-for="c in RECORDINGS" :key="c">
              <input v-model.number="qty[key(r, c)]" type="number" min="0" max="100" step="1" class="p-inputtext qty"
                     :aria-label="`${RESOLUTION_LABELS[r]}, ${RECORDING_LABELS[c]}`" :data-test="`qty-${r}-${c}`" />
              <small v-if="selected.actual" class="muted">{{ onAdapter(r, c) }} on the adapter</small>
            </td>
          </tr>
        </tbody>
      </table>

      <fieldset class="choice">
        <legend>Cloud storage</legend>
        <label v-for="d in days" :key="d"><input v-model.number="retention" type="radio" name="retention" :value="d"
                                                 :data-test="`days-${d}`" /> {{ d }} days</label>
      </fieldset>
      <fieldset class="choice">
        <legend>Payment</legend>
        <label><input v-model="interval" type="radio" name="interval" value="monthly" data-test="monthly" /> Monthly</label>
        <label><input v-model="interval" type="radio" name="interval" value="annual" data-test="annual" />
          Annual ({{ discount }} % off)</label>
      </fieldset>

      <p class="price">Trade price (ex. tax)
        <strong data-test="price">{{ preview === null ? "—" : money(preview, currency) }}</strong> / {{ per(interval) }}
        <span v-if="pricing.data.value?.settings.placeholder_prices" class="muted">· placeholder prices</span></p>
      <p class="muted" data-test="actual">{{ actualText }}</p>
      <p v-if="total" class="muted" data-test="total">Customer total: {{ total.adapters }} adapter{{ total.adapters === 1 ? "" : "s" }},
        {{ money(total.centsMonth, total.currency) }} a month</p>

      <Message v-if="message" severity="success" size="small" variant="simple">{{ message }}</Message>
      <Message v-if="error || pricing.error.value" severity="error" size="small" variant="simple" data-test="error">
        {{ error || pricing.error.value }}</Message>

      <div class="console-actions">
        <Button label="Save plan" :loading="busy" :disabled="!changed || !valid" data-test="save" @click="save" />
        <template v-if="plan && (plan.effectiveStatus === 'trial' || plan.effectiveStatus === 'active')">
          <Button v-if="!confirming" label="Cancel subscription" severity="danger" variant="text" @click="confirming = true" />
          <template v-else>
            <span class="muted">Cancel this adapter's subscription?</span>
            <Button label="Yes, cancel it" severity="danger" :loading="busy" data-test="confirm-cancel" @click="cancel" />
            <Button label="Keep it" severity="secondary" @click="confirming = false" />
          </template>
        </template>
      </div>
    </template>
  </article>
</template>

<style scoped>
.plan-grid { max-width: 36rem; margin-bottom: 1rem; }
.plan-grid .qty { width: 5rem; }
.plan-grid small { display: block; margin-top: 0.2rem; }
.choice { display: flex; flex-wrap: wrap; gap: 1rem; margin: 0 0 1rem; padding: 0.6rem 0.9rem; border: 1px solid var(--p-content-border-color); border-radius: 0.5rem; }
.choice legend { padding: 0 0.3rem; color: var(--p-text-muted-color); font-size: 0.85rem; }
.price { font-size: 1.1rem; }
.price strong { font-size: 1.4rem; margin: 0 0.3rem; }
.plan-status { margin: 0.5rem 0 1rem; }
</style>
