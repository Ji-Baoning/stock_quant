<!-- web/src/components/KpiCard.vue -->
<script lang="ts">
export type KpiTone = "neutral" | "up" | "down" | "pending" | "block";
</script>

<script setup lang="ts">
const props = defineProps<{
  label: string;
  value: string;
  compare?: string;
  tone?: KpiTone;
  testid?: string;
}>();
</script>

<template>
  <div class="kpi" :data-testid="props.testid ?? 'kpi-card'">
    <span class="kpi-label">{{ props.label }}</span>
    <span class="kpi-value kpi-num" :class="props.tone ? `kpi-value--${props.tone}` : undefined">
      {{ props.value }}
    </span>
    <span v-if="props.compare" class="kpi-compare">{{ props.compare }}</span>
  </div>
</template>
