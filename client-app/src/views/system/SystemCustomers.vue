<script setup lang="ts">
// Every customer, by dealer (DEALER-PANEL-PLAN.md §7.2). Plans, cameras and users per customer are
// the dealer portal's pages (Phase 5), which the System console will open for any dealer.
import { ref } from "vue";
import { endpoints } from "../../api";
import type { CustomerSummary, Dealer } from "../../api/types";
import { useLoad } from "../../composables/useLoad";

const dealer = ref("");
const dealers = useLoad<Dealer[]>(() => endpoints.listDealers());
const list = useLoad<CustomerSummary[]>(() => endpoints.listCustomers(dealer.value || undefined));
</script>

<template>
  <article class="console-card">
    <h2>Customers<span class="dot">.</span></h2>
    <div class="console-form">
      <label>Dealer
        <select v-model="dealer" class="p-inputtext" data-test="dealer-filter" @change="list.reload()">
          <option value="">every dealer</option>
          <option v-for="d in dealers.data.value ?? []" :key="d.id" :value="d.id">{{ d.name }}</option>
        </select>
      </label>
    </div>
    <p v-if="list.error.value" role="alert">{{ list.error.value }}</p>
    <table class="console-table">
      <thead><tr><th>Customer</th><th>Reference</th><th>Dealer</th><th>Status</th>
        <th class="num">Adapters</th><th class="num">Users</th></tr></thead>
      <tbody>
        <tr v-for="c in list.data.value ?? []" :key="c.id">
          <td>{{ c.name }}</td><td class="muted">{{ c.reference }}</td><td>{{ c.dealer.name }}</td>
          <td><span class="status" :class="c.status">{{ c.status }}</span></td>
          <td class="num">{{ c.adapters }}</td><td class="num">{{ c.users }}</td>
        </tr>
      </tbody>
    </table>
  </article>
</template>
