import { createRouter, createWebHashHistory, type RouteRecordRaw } from "vue-router";
import DecisionConsolePage from "./pages/DecisionConsolePage.vue";
import VersionsPage from "./pages/VersionsPage.vue";
import VersionDetailPage from "./pages/VersionDetailPage.vue";
import DataPreviewPage from "./pages/DataPreviewPage.vue";
import CoverageEvidencePage from "./pages/CoverageEvidencePage.vue";
import UpdateJobsPage from "./pages/UpdateJobsPage.vue";
import StrategiesPage from "./pages/StrategiesPage.vue";
import StrategyDetailPage from "./pages/StrategyDetailPage.vue";
import RegisterPage from "./pages/RegisterPage.vue";

export interface NavItem {
  label: string;
  path: string;
}

/** v3 规格 §4：侧边栏分组导航；label 为 null 表示独立项（不进任何组）。 */
export interface NavGroup {
  label: string | null;
  items: readonly NavItem[];
}

/** 报告独立项已下线（裁定 7：迁入策略详情页）。 */
export const NAV_GROUPS: readonly NavGroup[] = [
  {
    label: null,
    items: [{ label: "决策台", path: "/" }],
  },
  {
    label: "策略",
    items: [
      { label: "策略列表", path: "/strategies" },
      { label: "注册台", path: "/strategies/register" },
    ],
  },
  {
    label: "数据",
    items: [
      { label: "版本面板", path: "/versions" },
      { label: "数据预览", path: "/preview" },
      { label: "质量/覆盖证据", path: "/evidence" },
    ],
  },
  {
    label: "运维",
    items: [{ label: "更新任务", path: "/jobs" }],
  },
];

/** 扁平导航顺序（既有测试与外部引用的口径不变）。 */
export const NAV_ITEMS: readonly NavItem[] = NAV_GROUPS.flatMap((group) => group.items);

const routes: RouteRecordRaw[] = [
  { path: "/", name: "console", component: DecisionConsolePage },
  { path: "/strategies", name: "strategies", component: StrategiesPage },
  // 静态路径放在参数路由前，register 不会被 :experimentId 吞掉。
  { path: "/strategies/register", name: "strategy-register", component: RegisterPage },
  { path: "/strategies/:experimentId", name: "strategy-detail", component: StrategyDetailPage },
  { path: "/versions", name: "versions", component: VersionsPage },
  { path: "/versions/:version", name: "version-detail", component: VersionDetailPage },
  { path: "/preview", name: "preview", component: DataPreviewPage },
  { path: "/evidence", name: "evidence", component: CoverageEvidencePage },
  { path: "/jobs", name: "jobs", component: UpdateJobsPage },
];

export function createPortalRouter() {
  return createRouter({ history: createWebHashHistory(), routes });
}

export const router = createPortalRouter();
