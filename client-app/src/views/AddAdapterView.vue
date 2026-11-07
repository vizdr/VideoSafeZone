<script setup lang="ts">
// Add adapter (DEALER-PANEL-PLAN.md §7.3, D15): a holder or Super Admin links an adapter to their
// account with its Cloud ID. Afterwards the token is renewed, so the adapter's cameras appear at
// once (D4: the claims are made at every renewal). It starts on a trial until the dealer sets a plan.
import { computed, ref, watch } from "vue";
import Message from "openvue/message";
import { endpoints } from "../api";
import type { Activation, Me } from "../api/types";
import ActivateAdapter from "../components/adapters/ActivateAdapter.vue";
import { useLoad } from "../composables/useLoad";
import { useAuthStore } from "../stores/auth";
import { useCamerasStore } from "../stores/cameras";

const auth = useAuthStore();
const cameras = useCamerasStore();
const me = useLoad<Me>(() => endpoints.me());
const accounts = computed(() => (me.data.value?.accounts ?? []).filter(a => a.role === "customer" || a.role === "customer_admin"));
const accountId = ref("");
watch(accounts, list => { if (!accountId.value && list.length) accountId.value = list[0].id; }, { immediate: true });
const account = computed(() => accounts.value.find(a => a.id === accountId.value));

const done = ref<Activation | null>(null);
const note = ref("");

async function activated(a: Activation): Promise<void> {
  done.value = a;
  try {
    await auth.refresh();
  } catch {
    note.value = "Sign out and in again to see its cameras.";
  }
  await cameras.load();
}
</script>

<template>
  <section class="view">
    <h1>Add adapter</h1>
    <article class="console-card">
      <h2>Enter Cloud ID<span class="dot">.</span></h2>
      <p v-if="me.error.value" role="alert">{{ me.error.value }}</p>
      <p v-else-if="me.data.value && !accounts.length" class="muted" data-test="not-yours">
        Adapters are added by the account's holder or a Super Admin.</p>
      <template v-else-if="account && !done">
        <div v-if="accounts.length > 1" class="console-form">
          <label>For the account
            <select v-model="accountId" class="p-inputtext" data-test="account">
              <option v-for="a in accounts" :key="a.id" :value="a.id">{{ a.name }}</option>
            </select>
          </label>
        </div>
        <ActivateAdapter :key="accountId" :sites="account.sites" :account-id="accountId" @activated="activated" />
      </template>
      <template v-if="done">
        <Message severity="success" size="small" variant="simple" data-test="activated">
          {{ done.thingName }} is activated. It starts on a trial plan until your dealer sets one.</Message>
        <Message v-if="note" severity="warn" size="small" variant="simple">{{ note }}</Message>
        <p class="muted">Add its cameras on the adapter's own page on your network for now; adding them from here comes next.</p>
        <p><RouterLink :to="{ name: 'live' }">Go to Live</RouterLink> ·
          <RouterLink :to="{ name: 'settings-adapters' }">Your cloud adapters</RouterLink></p>
      </template>
    </article>
  </section>
</template>
