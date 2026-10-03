<script setup lang="ts">
const props = defineProps<{
  title: string;
  description?: string;
  actionLabel?: string;
  actionTo?: string;
}>();
</script>

<template>
  <div class="empty-state" data-testid="empty-state">
    <p class="empty-state-title">{{ props.title }}</p>
    <p v-if="props.description" class="empty-state-description">{{ props.description }}</p>
    <RouterLink
      v-if="props.actionLabel !== undefined && props.actionTo !== undefined"
      :to="props.actionTo"
      data-testid="empty-state-action"
    >
      {{ props.actionLabel }}
    </RouterLink>
  </div>
</template>
