<script setup lang="ts">
// The System console for owners and administrators (DEALER-PANEL-PLAN.md §7.2). Billing joins in
// Phase 8; generating adapters with their Cloud IDs in Phase 6.
const tabs = [
  { name: "system-overview", label: "Overview" },
  { name: "system-dealers", label: "Dealers" },
  { name: "system-customers", label: "Customers" },
  { name: "system-users", label: "Users" },
  { name: "system-adapters", label: "Adapters" },
  { name: "system-pricing", label: "Pricing" },
];
</script>

<template>
  <section class="view">
    <nav class="console-tabs" aria-label="System">
      <RouterLink v-for="t in tabs" :key="t.name" :to="{ name: t.name }">{{ t.label }}</RouterLink>
    </nav>
    <RouterView />
  </section>
</template>
