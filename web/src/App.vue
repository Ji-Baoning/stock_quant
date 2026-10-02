<script setup lang="ts">
import { createApiClient, provideApiClient, type ApiClient } from "./api/client";
import AppTopBar from "./components/AppTopBar.vue";

const props = defineProps<{ client?: ApiClient }>();
provideApiClient(props.client ?? createApiClient());
</script>

<template>
  <div class="app">
    <AppTopBar />
    <main class="page"><RouterView /></main>
  </div>
</template>
