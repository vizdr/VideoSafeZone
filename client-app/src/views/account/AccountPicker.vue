<script setup lang="ts">
// Which account a tab of the Account page is about (DEALER-PANEL-PLAN.md D18): a picker when the person
// has more than one account the tab applies to; nothing otherwise.
defineProps<{ accounts: Array<{ id: string; name: string }> }>();
const chosen = defineModel<string>({ required: true });
</script>

<template>
  <div v-if="accounts.length > 1" class="console-form">
    <label>Account
      <select v-model="chosen" class="p-inputtext" data-test="account-picker">
        <option v-for="a in accounts" :key="a.id" :value="a.id">{{ a.name }}</option>
      </select>
    </label>
  </div>
</template>
