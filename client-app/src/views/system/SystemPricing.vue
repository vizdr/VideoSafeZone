<script setup lang="ts">
// The camera price list (DEALER-PANEL-PLAN.md §5, D10): one monthly price per camera for each
// resolution x recording x storage duration, saved whole -- the server refuses a list with a gap.
// Billing is a fake for now (D9); these prices are what plans and the fake invoices are made of.
// A dealer reads the list; owners and administrators change it.
import { computed, ref, watch } from "vue";
import Button from "openvue/button";
import InputText from "openvue/inputtext";
import Message from "openvue/message";
import { endpoints } from "../../api";
import { errorText } from "../../api/client";
import type { Price, Pricing, Role } from "../../api/types";
import { useLoad } from "../../composables/useLoad";
import { useAuthStore } from "../../stores/auth";
import { SYSTEM_ROLES } from "../../auth/roles";

const RESOLUTIONS: Price["resolution"][] = ["2mp", "4mp", "8mp"];
const RECORDINGS: Price["recording"][] = ["motion", "continuous"];
const key = (r: string, c: string, d: number) => `${r}|${c}|${d}`;

const auth = useAuthStore();
const canEdit = computed(() => SYSTEM_ROLES.includes(auth.role as Role | null));
const pricing = useLoad<Pricing>(() => endpoints.getPricing());

const daysText = ref(""), discount = ref(""), trial = ref("");
const cells = ref<Record<string, string>>({});
const message = ref(""), error = ref(""), busy = ref(false);

watch(() => pricing.data.value, p => {
  if (!p) return;
  daysText.value = p.settings.retention_days.join(", ");
  discount.value = String(p.settings.annual_discount_percent);
  trial.value = String(p.settings.trial_days);
  cells.value = Object.fromEntries(p.prices.map(x => [key(x.resolution, x.recording, x.retentionDays), (x.centsMonth / 100).toFixed(2)]));
}, { immediate: true });

// The storage durations as typed: whole days, in order, each once; null while that isn't so.
const days = computed<number[] | null>(() => {
  const parts = daysText.value.split(/[\s,]+/).filter(Boolean);
  const out = parts.map(Number);
  if (!out.length || out.length > 12 || out.some(d => !Number.isInteger(d) || d < 1 || d > 3650)) return null;
  const sorted = [...new Set(out)].sort((a, b) => a - b);
  return sorted.length === out.length ? sorted : null;
});

function cents(text: string | undefined): number | null {
  if (text === undefined || !/^\s*\d+([.,]\d{1,2})?\s*$/.test(text)) return null;
  return Math.round(parseFloat(text.replace(",", ".")) * 100);
}
const whole = (text: string, top: number): number | null => {
  const n = Number(text);
  return /^\s*\d+\s*$/.test(text) && n <= top ? n : null;
};

async function save(): Promise<void> {
  error.value = message.value = "";
  const d = days.value;
  if (!d) { error.value = "Storage durations: whole days from 1 to 3650, each once, at most 12."; return; }
  const disc = whole(discount.value, 50), tri = whole(trial.value, 90);
  if (disc === null) { error.value = "Annual discount: a whole percent from 0 to 50."; return; }
  if (tri === null) { error.value = "Free trial: whole days from 0 to 90."; return; }
  const prices: Price[] = [];
  for (const r of RESOLUTIONS) for (const c of RECORDINGS) for (const n of d) {
    const v = cents(cells.value[key(r, c, n)]);
    if (v === null) { error.value = `Give a price for ${r.toUpperCase()} ${c}, ${n} days.`; return; }
    prices.push({ resolution: r, recording: c, retentionDays: n, centsMonth: v });
  }
  busy.value = true;
  try {
    const res = await endpoints.putPricing(prices, { retention_days: d, annual_discount_percent: disc, trial_days: tri });
    if (!res.ok) { error.value = errorText(res); return; }
    message.value = `Saved: ${prices.length} prices.`;
    await pricing.reload();
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <article class="console-card">
    <h2>Pricing<span class="dot">.</span></h2>
    <Message v-if="pricing.data.value?.settings.placeholder_prices" severity="warn" size="small" variant="simple">
      These are placeholder prices from the first migration: nobody has set the real ones yet.</Message>
    <p v-if="pricing.error.value" role="alert">{{ pricing.error.value }}</p>
    <template v-if="pricing.data.value">
      <div class="console-form">
        <label>Storage durations, days<InputText v-model="daysText" :disabled="!canEdit" data-test="days" /></label>
        <label>Annual discount, %<InputText v-model="discount" :disabled="!canEdit" inputmode="numeric" /></label>
        <label>Free trial, days<InputText v-model="trial" :disabled="!canEdit" inputmode="numeric" /></label>
      </div>
      <p class="muted">Per camera per month, in {{ pricing.data.value.settings.currency }}.</p>
      <table v-if="days" class="console-table pricing">
        <thead><tr><th>Camera</th><th>Recording</th><th v-for="n in days" :key="n" class="num">{{ n }} days</th></tr></thead>
        <tbody>
          <template v-for="r in RESOLUTIONS" :key="r">
            <tr v-for="c in RECORDINGS" :key="c">
              <td>{{ r.toUpperCase() }}</td><td>{{ c }}</td>
              <td v-for="n in days" :key="n" class="num">
                <InputText v-model="cells[key(r, c, n)]" :disabled="!canEdit" size="small" inputmode="decimal"
                           :invalid="cells[key(r, c, n)] !== undefined && cents(cells[key(r, c, n)]) === null"
                           :data-test="`price-${key(r, c, n)}`" />
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <p v-else role="alert">Storage durations: whole days from 1 to 3650, each once, at most 12.</p>
      <Message v-if="message" severity="success" size="small" variant="simple">{{ message }}</Message>
      <Message v-if="error" severity="error" size="small" variant="simple" role="alert">{{ error }}</Message>
      <Button v-if="canEdit" label="Save the price list" :loading="busy" @click="save" />
    </template>
  </article>
</template>
