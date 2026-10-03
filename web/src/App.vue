<script setup lang="ts">
import { createApiClient, provideApiClient, type ApiClient } from "./api/client";
import AppShell from "./components/AppShell.vue";

const props = defineProps<{ client?: ApiClient }>();
provideApiClient(props.client ?? createApiClient());
</script>

<template>
  <AppShell />
</template>
