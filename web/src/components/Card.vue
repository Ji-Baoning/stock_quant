<!-- web/src/components/Card.vue -->
<script setup lang="ts">
const props = defineProps<{ title?: string; testid?: string }>();
</script>

<template>
  <section class="card" :data-testid="props.testid ?? 'card'">
    <h2 v-if="props.title" class="card-title">{{ props.title }}</h2>
    <slot />
  </section>
</template>
