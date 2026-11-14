<script setup lang="ts">
// The Account page (DEALER-PANEL-PLAN.md D18), after Videoloft's: Account details for everyone; Plan &
// Billing for an account's holder; Additional users for its holder and Super Admins -- each with an
// account picker when the person has more than one such account. Opt-ins and the "Convert to SSO" card
// are left out (D17, D18). The tabs only hide: the directory decides every call again.
import { computed, onMounted } from "vue";
import { useRoute } from "vue-router";
import { useAuthStore } from "../stores/auth";
import { useDirectoryStore } from "../stores/directory";
import AccountDetails from "./account/AccountDetails.vue";
import AccountPlans from "./account/AccountPlans.vue";
import AccountUsers from "./account/AccountUsers.vue";

const route = useRoute();
const auth = useAuthStore();
const directory = useDirectoryStore();
onMounted(() => { if (auth.role === "customer") void directory.load(); });

const accounts = computed(() => directory.me?.accounts ?? []);
const held = computed(() => accounts.value.filter(a => a.role === "customer"));
const administered = computed(() => accounts.value.filter(a => a.role === "customer" || a.role === "customer_admin"));
const tabs = computed(() => [
  { tab: "details", label: "Account details" },
  ...(held.value.length ? [{ tab: "plans", label: "Plan & Billing" }] : []),
  ...(administered.value.length ? [{ tab: "users", label: "Additional users" }] : []),
]);
const tab = computed(() => {
  const asked = typeof route.params.tab === "string" ? route.params.tab : "details";
  return tabs.value.some(t => t.tab === asked) ? asked : "details";
});
</script>

<template>
  <section class="view account">
    <nav class="tabs" aria-label="Account">
      <RouterLink v-for="t in tabs" :key="t.tab" :to="{ name: 'account', params: t.tab === 'details' ? {} : { tab: t.tab } }"
                  class="tab" :class="{ active: t.tab === tab }" :aria-current="t.tab === tab ? 'page' : undefined">
        {{ t.label }}</RouterLink>
    </nav>
    <AccountPlans v-if="tab === 'plans'" :accounts="held" />
    <AccountUsers v-else-if="tab === 'users'" :accounts="administered" @changed="directory.load(true)" />
    <AccountDetails v-else />
  </section>
</template>

<style scoped>
.account { max-width: 60rem; }
.tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 1.25rem;
  padding: 0.35rem;
  background: var(--p-surface-900);
  border-radius: 0.6rem;
}
.tab { padding: 0.45rem 1rem; border-radius: 0.45rem; color: var(--p-text-muted-color); text-decoration: none; }
.tab.active { background: var(--p-primary-color); color: var(--p-primary-contrast-color); }
</style>
