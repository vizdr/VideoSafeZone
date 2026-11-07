<script setup lang="ts">
// Every adapter (DEALER-PANEL-PLAN.md §7.2, D15). Generating new ones with their Cloud IDs,
// allocating them to dealers, releasing and revoking them come in Phase 6.
import { endpoints } from "../../api";
import type { AdapterSummary } from "../../api/types";
import { useLoad } from "../../composables/useLoad";

const list = useLoad<AdapterSummary[]>(() => endpoints.listAdapters());
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");
</script>

<template>
  <article class="console-card">
    <h2>Adapters<span class="dot">.</span></h2>
    <p class="muted">New adapters, each with its Cloud ID, come in Phase 6.</p>
    <p v-if="list.error.value" role="alert">{{ list.error.value }}</p>
    <table class="console-table">
      <thead><tr><th>Adapter</th><th>Dealer</th><th>Status</th><th>Activated at</th><th>Last seen</th></tr></thead>
      <tbody>
        <tr v-for="a in list.data.value ?? []" :key="a.id">
          <td>{{ a.thingName }}<div v-if="a.label" class="muted">{{ a.label }}</div></td>
          <td>{{ a.dealer?.name ?? "—" }}</td>
          <td><span class="status" :class="a.status">{{ a.status }}</span></td>
          <td>
            <template v-if="a.activation">{{ a.activation.customer.name }} · {{ a.activation.site }}
              <span v-if="a.activation.name" class="muted">({{ a.activation.name }})</span></template>
            <span v-else class="muted">not activated</span>
          </td>
          <td class="muted">{{ when(a.lastSeenAt) }}</td>
        </tr>
      </tbody>
    </table>
  </article>
</template>
