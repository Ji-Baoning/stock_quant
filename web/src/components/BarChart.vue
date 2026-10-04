<!-- web/src/components/BarChart.vue —— ECharts 柱状薄封装（Task 5）。
     不做任何金融计算；红涨绿跌只按调用方给的数值正负着色（colorBySign）。 -->
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts/core";
import { BarChart as EChartsBar } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

// 按需注册，模块作用域执行一次；换页不重复注册。
echarts.use([EChartsBar, GridComponent, TooltipComponent, CanvasRenderer]);

// ECharts canvas 渲染器不解析 `var(--…)`（zrender 颜色解析对 var() 返回
// undefined，fillStyle 赋值会被忽略）——所以渲染时从 computed style 取
// token 实际值；token 读不到时（happy-dom 单测未加载 tokens.css）回退到
// tokens.css 的同名字面值。hex 只出现在 JS 回退表，CSS 文件仍零 hex。
type ColorToken = "--color-up" | "--color-down" | "--color-primary";
const TOKEN_FALLBACKS: Record<ColorToken, string> = {
  "--color-up": "#c23a2b",
  "--color-down": "#1c7c4a",
  "--color-primary": "#2459a8",
};

function tokenColor(name: ColorToken): string {
  const value = window
    .getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return value !== "" ? value : TOKEN_FALLBACKS[name];
}

const props = withDefaults(
  defineProps<{
    labels: string[];
    series: Array<{ name: string; data: Array<number | null> }>;
    colorBySign?: boolean;
    testid?: string;
  }>(),
  {},
);

const container = ref<HTMLDivElement | null>(null);
let chart: echarts.ECharts | null = null;

// happy-dom 单测环境没有真实 canvas（getContext 返回 null）：此时不创建
// zrender 实例，避免动画帧在 null 上下文上抛未捕获异常；单测只断言容器与
// props。浏览器端探测通过，正常出图。
function canvasSupported(): boolean {
  try {
    return document.createElement("canvas").getContext("2d") !== null;
  } catch {
    return false;
  }
}

function itemColor(value: number | null): string {
  if (props.colorBySign !== true || value === null) return tokenColor("--color-primary");
  return value >= 0 ? tokenColor("--color-up") : tokenColor("--color-down");
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
    yAxis: { type: "value" },
    series: props.series.map((bars) => ({
      name: bars.name,
      type: "bar" as const,
      data: props.colorBySign
        ? bars.data.map((value) => ({
            value,
            itemStyle: { color: itemColor(value) },
          }))
        : bars.data,
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
  <div ref="container" class="chart-canvas" :data-testid="props.testid ?? 'bar-chart'" />
</template>
