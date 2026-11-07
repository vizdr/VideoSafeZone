<script setup lang="ts">
// Plan history (DEALER-PANEL-PLAN.md §7.1): every version of an adapter's plan at this customer,
// newest first, with who saved it, when, what changed and the price the server set.
import { computed, inject, ref, watch } from "vue";
import { endpoints } from "../../api";
import { errorText } from "../../api/client";
import type { Plan } from "../../api/types";
import { CUSTOMER, adapterLabel, useAdapterChoice } from "../../dealer/customer";
import { STATUS_LABELS, camerasText, intervalText, planChanges } from "../../dealer/plans";
import { money } from "../../pricing";

const ctx = inject(CUSTOMER)!;
const { list, selected, choose } = useAdapterChoice(ctx, "dealer-history");
const plans = ref<Plan[] | null>(null);
const error = ref("");

watch(() => `${selected.value?.id}:${selected.value?.plan?.version}`, async () => {
  const a = selected.value;
  plans.value = null;
  if (!a) return;
  const res = await endpoints.planHistory(a.id);
  if (a.id !== selected.value?.id) return;
  error.value = res.ok ? "" : errorText(res);
  plans.value = res.ok ? res.data.plans : null;
}, { immediate: true });

const rows = computed(() => (plans.value ?? []).map((p, i, all) => ({ p, changes: planChanges(all[i + 1] ?? null, p) })));
const price = (p: Plan) => (p.status === "cancelled" ? "—" : `${money(p.centsPeriod, p.currency)} / ${p.interval === "annual" ? "year" : "month"}`);
</script>

<template>
  <article class="console-card">
    <h2>Plan history<span class="dot">.</span></h2>
    <p v-if="ctx.adapters.value && !list.length" class="muted">No adapter is activated for this customer yet.</p>
    <div v-if="selected" class="console-form">
      <label>Adapter
        <select class="p-inputtext" :value="selected.id" data-test="adapter" @change="choose(($event.target as HTMLSelectElement).value)">
          <option v-for="a in list" :key="a.id" :value="a.id">{{ adapterLabel(a) }}</option>
        </select>
      </label>
    </div>
    <p v-if="error" role="alert">{{ error }}</p>
    <table v-if="plans" class="console-table">
      <thead><tr><th class="num">Version</th><th>Saved</th><th>Status</th><th>Cameras</th><th>Storage</th>
        <th>Payment</th><th class="num">Price</th><th>Changes</th></tr></thead>
      <tbody>
        <tr v-for="{ p, changes } in rows" :key="p.id" :data-test="`version-${p.version}`">
          <td class="num">{{ p.version }}</td>
          <td>{{ new Date(p.createdAt).toLocaleString() }}<div class="muted">{{ p.createdBy ?? "the system" }}</div></td>
          <td>{{ STATUS_LABELS[p.status] ?? p.status }}</td>
          <td>{{ camerasText(p.lines) }}</td>
          <td>{{ p.retentionDays }} days</td>
          <td>{{ intervalText(p.interval) }}</td>
          <td class="num">{{ price(p) }}</td>
          <td class="changes">{{ changes.join("; ") }}</td>
        </tr>
      </tbody>
    </table>
  </article>
</template>

<style scoped>
.changes { max-width: 22rem; }
</style>
