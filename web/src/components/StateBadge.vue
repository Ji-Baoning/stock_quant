<!-- web/src/components/StateBadge.vue -->
<script lang="ts">
export type BadgeKind = "acceptance" | "conclusion" | "job";
export type BadgeTone = "pass" | "block" | "pending" | "neutral";
</script>

<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  kind: BadgeKind;
  value: string;
  title?: string;
}>();

/** 语义映射表：只映射项目自己的状态词；未知值回落 neutral + 原文（展示纪律）。 */
const TONES: Record<BadgeKind, Record<string, BadgeTone>> = {
  acceptance: {
    ACCEPTED: "pass",
    REJECTED: "block",
    PENDING_CONFIRMATION: "pending",
    UNVERIFIED: "neutral",
  },
  conclusion: {
    STABLE: "pass",
    UNSTABLE: "block",
    INCONCLUSIVE: "pending",
  },
  job: {
    SUCCEEDED: "pass",
    FAILED: "block",
    QUEUED: "pending",
    RUNNING: "pending",
    CANCELLED_BY_SHUTDOWN: "neutral",
  },
};

const tone = computed<BadgeTone>(() => TONES[props.kind][props.value] ?? "neutral");
</script>

<template>
  <span
    class="badge-state"
    :class="`badge-state--${tone}`"
    :title="props.title ?? props.value"
    data-testid="state-badge"
  >
    {{ props.value }}
  </span>
</template>
