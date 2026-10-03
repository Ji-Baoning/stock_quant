<script setup lang="ts">
const props = withDefaults(defineProps<{ rows?: number }>(), { rows: 3 });
</script>

<template>
  <div class="skeleton" data-testid="skeleton" aria-busy="true">
    <div v-for="row in props.rows" :key="row" class="skeleton-row" />
  </div>
</template>
