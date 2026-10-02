<script setup lang="ts">
import AppTopBar from "./components/AppTopBar.vue";
</script>

<template>
  <div class="app">
    <AppTopBar />
    <main class="page"><RouterView /></main>
  </div>
</template>
