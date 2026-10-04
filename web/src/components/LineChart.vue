<!-- web/src/components/LineChart.vue —— ECharts 折线薄封装（Task 5）。
     不做任何金融计算；基准虚线由调用方用 dashed 表达。 -->
<script lang="ts">
// 类型必须导出给消费方（Task 8 的详情页），而 `<script setup>` 内禁止
// ES 模块导出（Vue 编译错误）——所以类型放独立 script 块，与第一批各组件同款式。
export interface LineSeries {
  name: string;
  data: number[];
  dashed?: boolean;
}
</script>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts/core";
import { LineChart as EChartsLine } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

// 按需注册，模块作用域执行一次；换页不重复注册。
echarts.use([EChartsLine, GridComponent, TooltipComponent, CanvasRenderer]);

const props = withDefaults(
  defineProps<{
    labels: string[];
    series: LineSeries[];
    testid?: string;
    fold?: string;
  }>(),
  {},
);

const container = ref<HTMLDivElement | null>(null);
let chart: echarts.ECharts | null = null;

// happy-dom 单测环境没有真实 canvas（getContext 返回 null）：此时不创建
// zrender 实例，避免动画帧在 null 上下文上抛未捕获异常；单测只断言容器与
// data-fold 证据。浏览器端探测通过，正常出图。
function canvasSupported(): boolean {
  try {
    return document.createElement("canvas").getContext("2d") !== null;
  } catch {
    return false;
  }
}

function render() {
  if (container.value === null) return;
  if (chart === null) {
    if (!canvasSupported()) return;
    chart = echarts.init(container.value);
  }
  chart.setOption({
    grid: { left: 48, right: 16, top: 16, bottom: 28 },
    tooltip: { trigger: "axis" },
    xAxis: { type: "category", data: props.labels },
    yAxis: { type: "value", scale: true },
    series: props.series.map((line) => ({
      name: line.name,
      type: "line" as const,
      data: line.data,
      showSymbol: false,
      lineStyle: line.dashed === true ? { type: "dashed" as const } : undefined,
    })),
  });
}

onMounted(render);
watch(() => [props.labels, props.series], render, { deep: true });
onBeforeUnmount(() => {
  chart?.dispose();
  chart = null;
});
</script>

<template>
  <div
    ref="container"
    class="chart-canvas"
    :data-testid="props.testid ?? 'line-chart'"
    :data-fold="props.fold"
  />
</template>
