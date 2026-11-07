<script setup lang="ts">
import { computed } from "vue";
import { endpoints } from "../../api";
import type { Overview } from "../../api/types";
import { ROLE_LABELS } from "../../auth/roles";
import { useLoad } from "../../composables/useLoad";

const o = useLoad<Overview>(() => endpoints.overview());
const sum = (r: Record<string, number> | undefined) => Object.values(r ?? {}).reduce((a, b) => a + b, 0);
const money = computed(() => {
  const p = o.data.value?.plans;
  return p ? `${(p.monthlyCents / 100).toFixed(2)} ${p.currency ?? ""}`.trim() : "";
});
</script>

<template>
  <article class="console-card">
    <h2>Overview<span class="dot">.</span></h2>
    <p v-if="o.error.value" role="alert">{{ o.error.value }}</p>
    <div v-else-if="o.data.value" class="tiles">
      <div class="tile"><span class="n">{{ o.data.value.dealers.active ?? 0 }}</span> dealers
        <small v-if="o.data.value.dealers.suspended">{{ o.data.value.dealers.suspended }} suspended</small></div>
      <div class="tile"><span class="n">{{ o.data.value.customers.active ?? 0 }}</span> customers
        <small v-if="o.data.value.customers.suspended">{{ o.data.value.customers.suspended }} suspended</small></div>
      <div class="tile"><span class="n">{{ sum(o.data.value.users) }}</span> memberships
        <small>{{ o.data.value.invited }} invited, not signed in yet</small></div>
      <div class="tile"><span class="n">{{ sum(o.data.value.adapters) }}</span> adapters
        <small>{{ Object.entries(o.data.value.adapters).map(([k, v]) => `${v} ${k}`).join(", ") || "none yet" }}</small></div>
      <div class="tile"><span class="n">{{ o.data.value.plans.active }}</span> paid plans
        <small>{{ o.data.value.plans.trial }} on trial · {{ money }} a month</small></div>
    </div>
    <table v-if="o.data.value" class="console-table roles">
      <thead><tr><th>Role</th><th class="num">Memberships</th></tr></thead>
      <tbody>
        <tr v-for="(n, role) in o.data.value.users" :key="role"><td>{{ ROLE_LABELS[role] ?? role }}</td><td class="num">{{ n }}</td></tr>
      </tbody>
    </table>
  </article>
</template>

<style scoped>
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr)); gap: 0.75rem; margin-bottom: 1.25rem; }
.tile { display: flex; flex-direction: column; gap: 0.2rem; padding: 0.9rem; background: var(--p-surface-800); border-radius: 0.5rem; }
.tile .n { font-size: 1.8rem; font-weight: 700; }
.tile small { color: var(--p-text-muted-color); }
.roles { max-width: 24rem; }
</style>
