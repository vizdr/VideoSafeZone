<script setup lang="ts">
// A card in the Videoloft layout: an icon column on the left, then one or more SettingRows.
defineProps<{ icon: string }>();
</script>

<template>
  <section class="setting-card">
    <div class="icon" aria-hidden="true"><i :class="['pi', icon]" /></div>
    <div class="rows"><slot /></div>
  </section>
</template>

<style scoped>
.setting-card {
  display: flex;
  background: var(--p-surface-700);
  border-radius: 0.5rem;
  overflow: hidden;
  margin-bottom: 1rem;
}
.icon {
  flex: none;
  width: 3.5rem;
  display: flex;
  justify-content: center;
  padding-top: 1.1rem;
  background: var(--p-surface-900);
  font-size: 1.3rem;
  color: var(--p-text-color);
}
.rows { flex: 1; min-width: 0; padding: 0.4rem 1rem; }
</style>
