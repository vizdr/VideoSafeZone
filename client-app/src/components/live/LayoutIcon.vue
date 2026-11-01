<script setup lang="ts">
import { computed } from "vue";
import { LAYOUTS, type LayoutId } from "../../layouts/grid";

// A small picture of a layout: its tiles on the 4×4 grid, large cells filled.
const props = defineProps<{ id: LayoutId }>();
const cells = computed(() => LAYOUTS[props.id].cells);
</script>

<template>
  <span class="layout-icon" aria-hidden="true">
    <span v-for="(c, i) in cells" :key="i" :class="{ large: c.span === 2 }"
          :style="{ gridRow: `${c.row} / span ${c.span}`, gridColumn: `${c.col} / span ${c.span}` }" />
  </span>
</template>

<style scoped>
.layout-icon {
  display: inline-grid;
  grid-template: repeat(4, 1fr) / repeat(4, 1fr);
  gap: 2px;
  width: 2.4rem;
  aspect-ratio: 16 / 9;
}
.layout-icon span { background: currentColor; opacity: 0.45; border-radius: 1px; }
.layout-icon span.large { opacity: 0.9; }
</style>
