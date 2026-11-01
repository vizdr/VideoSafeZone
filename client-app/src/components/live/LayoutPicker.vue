<script setup lang="ts">
import { LAYOUTS, LAYOUT_IDS, type LayoutId } from "../../layouts/grid";
import LayoutIcon from "./LayoutIcon.vue";

// The three layouts as buttons with their picture. Used in Settings → Live view and in the Live
// toolbar; both save the same account setting.
defineProps<{ modelValue: LayoutId; disabled?: boolean; compact?: boolean }>();
const emit = defineEmits<{ "update:modelValue": [id: LayoutId] }>();
</script>

<template>
  <div class="layout-picker" :class="{ compact }" role="group" aria-label="Layout">
    <button v-for="id in LAYOUT_IDS" :key="id" type="button" class="choice"
            :class="{ on: id === modelValue }" :aria-pressed="id === modelValue" :disabled="disabled"
            :title="`Layout ${LAYOUTS[id].label}`" @click="id !== modelValue && emit('update:modelValue', id)">
      <LayoutIcon :id="id" />
      <span class="label">{{ LAYOUTS[id].label }}</span>
    </button>
  </div>
</template>

<style scoped>
.layout-picker { display: flex; gap: 0.5rem; flex-wrap: wrap; }
.choice {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.35rem;
  padding: 0.55rem 0.8rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: 0.5rem;
  background: var(--p-surface-800);
  color: var(--p-text-muted-color);
  font: inherit;
  font-size: 0.82rem;
  cursor: pointer;
}
.choice:hover:not(:disabled) { color: var(--p-text-color); }
.choice.on { border-color: var(--p-primary-color); color: var(--p-primary-color); background: var(--p-surface-900); }
.choice:focus-visible { outline: 2px solid var(--p-primary-color); outline-offset: 2px; }
.choice:disabled { opacity: 0.6; cursor: default; }
.compact .choice { flex-direction: row; padding: 0.35rem 0.6rem; }
</style>
