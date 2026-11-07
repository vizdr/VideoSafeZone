<script setup lang="ts">
// The customer's cameras (DEALER-PANEL-PLAN.md §7.1, D5), by adapter, from the registry: name,
// codec, picture size and its class, recording mode, when the last thumbnail was taken. No pictures
// and no video: those are the customer's. Plan vs. actual per adapter, as a hint.
import { inject } from "vue";
import Message from "openvue/message";
import type { CustomerAdapter } from "../../api/types";
import { CUSTOMER, adapterLabel } from "../../dealer/customer";
import { RESOLUTION_LABELS } from "../../dealer/plans";

const ctx = inject(CUSTOMER)!;
const codec = (c: string) => c.toUpperCase().replace(/^H(\d)/, "H.$1");
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");
const summary = (a: CustomerAdapter) => (a.plan ? `${a.plan.cameras} in plan` : "no plan")
  + (a.actual ? `, ${a.actual.total} on this adapter` : "")
  + (a.actual?.unclassified ? ` (${a.actual.unclassified} of unknown picture size)` : "");
</script>

<template>
  <article class="console-card">
    <h2>Cameras<span class="dot">.</span></h2>
    <Message v-if="ctx.adapters.value?.registryError" severity="warn" size="small" variant="simple" data-test="registry-error">
      {{ ctx.adapters.value.registryError }}</Message>
    <p v-if="ctx.adapters.value && !ctx.adapters.value.adapters.length" class="muted">No adapter is activated for this customer yet.</p>
    <section v-for="a in ctx.adapters.value?.adapters ?? []" :key="a.id" class="adapter" :data-test="`adapter-${a.thingName}`">
      <h3>{{ adapterLabel(a) }}</h3>
      <p class="muted">{{ summary(a) }}</p>
      <table v-if="a.cameras?.length" class="console-table">
        <thead><tr><th>Camera</th><th>Codec</th><th>Picture</th><th>Recording</th><th>Last thumbnail</th></tr></thead>
        <tbody>
          <tr v-for="c in a.cameras" :key="c.id">
            <td>{{ c.name ?? c.id }}<span v-if="c.name" class="muted"> · {{ c.id }}</span></td>
            <td>{{ codec(c.codec) }}</td>
            <td>{{ c.class ? RESOLUTION_LABELS[c.class] : "unknown" }}<span v-if="c.size" class="muted"> · {{ c.size }}</span></td>
            <td>{{ c.recording }}<span class="muted"> · {{ c.recordingMode }}</span></td>
            <td class="muted">{{ when(c.thumbnailAt) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else-if="a.cameras" class="muted">No camera on this adapter yet.</p>
    </section>
  </article>
</template>

<style scoped>
.adapter + .adapter { margin-top: 1.5rem; }
.adapter h3 { margin: 0 0 0.25rem; font-size: 1.05rem; }
</style>
