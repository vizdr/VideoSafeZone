<script setup lang="ts">
// The dealer portal (DEALER-PANEL-PLAN.md §7.1, D7): a dealer's customers, their plans, its cloud
// adapters, billing, the price list and its own details. Owners and administrators open it too,
// across every dealer; a dealer's own details are the dealer's (System → Dealers for the others).
import { computed } from "vue";
import { useRoute } from "vue-router";
import { useAuthStore } from "../../stores/auth";

const auth = useAuthStore();
const route = useRoute();
const tabs = computed(() => [
  { name: "dealer-customers", label: "Customers" },
  { name: "dealer-adapters", label: "Cloud adapters" },
  { name: "dealer-billing", label: "Billing" },
  { name: "dealer-pricing", label: "Pricing" },
  ...(auth.role === "dealer" ? [{ name: "dealer-details", label: "Dealer details" }] : []),
]);
// A customer's own pages are still the Customers tab.
const inCustomers = computed(() => route.path.startsWith("/dealer/customers"));
</script>

<template>
  <section class="view">
    <nav class="console-tabs" aria-label="Dealer portal">
      <RouterLink v-for="t in tabs" :key="t.name" :to="{ name: t.name }"
                  :class="{ 'router-link-active': t.name === 'dealer-customers' && inCustomers }">{{ t.label }}</RouterLink>
    </nav>
    <RouterView />
  </section>
</template>
