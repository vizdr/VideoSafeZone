<script setup lang="ts">
// One customer's pages (DEALER-PANEL-PLAN.md §7.1): Plan, Plan history, Cameras, Users, Details.
// The customer and its adapters are loaded here once and shared (dealer/customer.ts).
import { computed, provide, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { endpoints } from "../../api";
import { errorText } from "../../api/client";
import type { CustomerAdapters, CustomerDetail } from "../../api/types";
import { CUSTOMER } from "../../dealer/customer";
import { useAuthStore } from "../../stores/auth";

const route = useRoute();
const auth = useAuthStore();
const customerId = computed(() => String(route.params.customerId ?? ""));
const detail = ref<CustomerDetail | null>(null);
const adapters = ref<CustomerAdapters | null>(null);
const error = ref("");

async function reload(): Promise<void> {
  const id = customerId.value;
  if (!id) return;
  try {
    const [d, a] = await Promise.all([endpoints.getCustomer(id), endpoints.customerAdapters(id)]);
    if (id !== customerId.value) return;                 // another customer was opened meanwhile
    error.value = !d.ok ? errorText(d) : !a.ok ? errorText(a) : "";
    detail.value = d.ok ? d.data : null;
    adapters.value = a.ok ? a.data : null;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}
watch(customerId, () => { detail.value = adapters.value = null; void reload(); }, { immediate: true });
provide(CUSTOMER, { customerId, detail, adapters, error, reload });

const menu = [
  { name: "dealer-plan", label: "Plan" }, { name: "dealer-history", label: "Plan history" },
  { name: "dealer-cameras", label: "Cameras" }, { name: "dealer-users", label: "Users" },
  { name: "dealer-customer-details", label: "Details" },
];
</script>

<template>
  <div class="portal-customer">
    <nav class="side-menu" aria-label="Customer">
      <RouterLink :to="{ name: 'dealer-customers' }" class="back">‹ Customer list</RouterLink>
      <RouterLink v-for="m in menu" :key="m.name" :to="{ name: m.name, params: { customerId } }"
                  :class="{ active: route.name === m.name }">{{ m.label }}</RouterLink>
    </nav>
    <div>
      <header class="customer-head">
        <h1>{{ detail?.name ?? "…" }}<span v-if="detail?.reference" class="muted"> — {{ detail.reference }}</span></h1>
        <span v-if="detail && detail.status !== 'active'" class="status" :class="detail.status">{{ detail.status }}</span>
        <span v-if="detail && auth.role !== 'dealer'" class="muted">dealer: {{ detail.dealer.name }}</span>
      </header>
      <p v-if="error" role="alert" data-test="customer-error">{{ error }}</p>
      <RouterView />
    </div>
  </div>
</template>

<style scoped>
.portal-customer { display: grid; grid-template-columns: 12rem 1fr; gap: 1.25rem; align-items: start; }
.side-menu { display: flex; flex-direction: column; gap: 0.2rem; padding: 0.5rem; background: var(--p-surface-900); border-radius: 0.6rem; }
.side-menu a { padding: 0.5rem 0.75rem; border-radius: 0.4rem; color: var(--p-text-color); text-decoration: none; }
.side-menu a.active { background: var(--p-primary-color); color: var(--p-primary-contrast-color); }
.side-menu a.back { color: var(--p-text-muted-color); font-size: 0.9rem; }
.customer-head { display: flex; align-items: baseline; gap: 0.75rem; flex-wrap: wrap; }
.customer-head h1 { margin: 0 0 1rem; font-size: 1.5rem; }
@media (max-width: 800px) { .portal-customer { grid-template-columns: 1fr; } .side-menu { flex-direction: row; flex-wrap: wrap; } }
</style>
