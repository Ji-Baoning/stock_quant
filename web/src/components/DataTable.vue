<script lang="ts">
export interface DataTableColumn {
  key: string;
  label: string;
  sortable?: boolean;
  align?: "left" | "right";
  mono?: boolean;
}
</script>

<script setup lang="ts">
import { computed, ref } from "vue";

type Row = Record<string, unknown>;

const props = withDefaults(
  defineProps<{
    columns: DataTableColumn[];
    rows: Row[];
    rowKey?: string;
    rowTestid?: string;
    emptyText?: string;
    testid?: string;
  }>(),
  { emptyText: "无数据" },
);

const sortKey = ref<string | null>(null);
const sortAsc = ref(true);

function toggleSort(column: DataTableColumn) {
  if (!column.sortable) return;
  if (sortKey.value === column.key) {
    sortAsc.value = !sortAsc.value;
  } else {
    sortKey.value = column.key;
    sortAsc.value = true;
  }
}

/** 只是 UI 稳定排序，不是指标计算（零金融计算纪律）。 */
const sortedRows = computed<Row[]>(() => {
  if (sortKey.value === null) return props.rows;
  const key = sortKey.value;
  const factor = sortAsc.value ? 1 : -1;
  return [...props.rows].sort((a, b) => {
    const left = a[key];
    const right = b[key];
    if (typeof left === "number" && typeof right === "number") {
      return (left - right) * factor;
    }
    return String(left ?? "").localeCompare(String(right ?? ""), "zh-Hans-CN") * factor;
  });
});

function cellText(row: Row, column: DataTableColumn): string {
  const value = row[column.key];
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

/** 行键：rowKey 字段缺失的行回退索引键，避免所有缺行共用 "undefined" 重复键。 */
function keyFor(row: Row, index: number): string | number {
  if (props.rowKey !== undefined && row[props.rowKey] !== undefined) {
    return String(row[props.rowKey]);
  }
  return index;
}
</script>

<template>
  <table class="data-table" :data-testid="props.testid ?? 'data-table'">
    <thead>
      <tr>
        <th
          v-for="column in props.columns"
          :key="column.key"
          :class="[
            column.align === 'right' ? 'cell-right' : '',
            column.sortable ? 'th-sortable' : '',
          ]"
          :aria-sort="
            sortKey === column.key ? (sortAsc ? 'ascending' : 'descending') : undefined
          "
          :tabindex="column.sortable ? 0 : undefined"
          @click="toggleSort(column)"
          @keydown.enter.prevent="toggleSort(column)"
          @keydown.space.prevent="toggleSort(column)"
        >
          {{ column.label }}
        </th>
      </tr>
    </thead>
    <tbody v-if="sortedRows.length > 0">
      <tr
        v-for="(row, index) in sortedRows"
        :key="keyFor(row, index)"
        :data-testid="props.rowTestid"
      >
        <td
          v-for="column in props.columns"
          :key="column.key"
          :class="[column.align === 'right' ? 'cell-right' : '', column.mono ? 'cell-mono' : '']"
        >
          <slot
            :name="column.key"
            :row="row"
            :value="row[column.key]"
          >{{ cellText(row, column) }}</slot>
        </td>
      </tr>
    </tbody>
    <tbody v-else>
      <tr>
        <td :colspan="props.columns.length" class="data-table-empty" data-testid="data-table-empty">
          {{ props.emptyText }}
        </td>
      </tr>
    </tbody>
  </table>
</template>
