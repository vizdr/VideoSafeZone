<script setup lang="ts">
// The dealer portal's landing (DEALER-PANEL-PLAN.md D7). Its tabs -- Customers, Cloud adapters,
// Billing, Pricing, Dealer details -- come in Phase 5; until then a dealer sees whose portal it is.
import { endpoints } from "../api";
import type { Me } from "../api/types";
import { useLoad } from "../composables/useLoad";

const me = useLoad<Me>(() => endpoints.me());
</script>

<template>
  <section class="view">
    <h1>Dealer portal</h1>
    <article class="console-card">
      <h2>{{ me.data.value?.org?.name ?? "…" }}<span class="dot">.</span></h2>
      <p v-if="me.error.value" class="muted" role="alert">{{ me.error.value }}</p>
      <p v-else>Your customers, their plans and your cloud adapters will be here.</p>
    </article>
  </section>
</template>
