<script setup lang="ts">
import AppTopBar from "./AppTopBar.vue";
import { NAV_GROUPS } from "../router";
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <p class="brand">Stock Quant 数据门户</p>
      <nav class="side-nav" data-testid="main-nav">
        <template v-for="group in NAV_GROUPS" :key="group.label ?? '__standalone__'">
          <p v-if="group.label !== null" class="side-group-label">{{ group.label }}</p>
          <RouterLink v-for="item in group.items" :key="item.path" :to="item.path">
            {{ item.label }}
          </RouterLink>
        </template>
      </nav>
    </aside>
    <div class="shell-main">
      <AppTopBar />
      <main class="page"><RouterView /></main>
    </div>
  </div>
</template>
