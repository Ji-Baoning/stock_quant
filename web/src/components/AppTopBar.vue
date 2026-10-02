<script setup lang="ts">
import { NAV_ITEMS } from "../router";
</script>

<template>
  <header class="top-bar" data-testid="top-bar">
    <span class="brand">Stock Quant 数据门户</span>
    <nav class="nav" data-testid="main-nav">
      <RouterLink v-for="item in NAV_ITEMS" :key="item.path" :to="item.path">{{ item.label }}</RouterLink>
    </nav>
  </header>
</template>
