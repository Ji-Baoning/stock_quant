import { createRouter, createWebHashHistory, type RouteRecordRaw } from "vue-router";
import VersionsPage from "./pages/VersionsPage.vue";
import VersionDetailPage from "./pages/VersionDetailPage.vue";
import DataPreviewPage from "./pages/DataPreviewPage.vue";
import CoverageEvidencePage from "./pages/CoverageEvidencePage.vue";
import UpdateJobsPage from "./pages/UpdateJobsPage.vue";
import ReportsPage from "./pages/ReportsPage.vue";

export interface NavItem {
  label: string;
  path: string;
}

/** v3 规格 §4：侧边栏分组导航；label 为 null 表示独立项（不进任何组）。 */
export interface NavGroup {
  label: string | null;
  items: readonly NavItem[];
}

/** 报告项保守保留（裁定 7：S1 下线并迁入策略详情页）。 */
export const NAV_GROUPS: readonly NavGroup[] = [
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
  {
    label: null,
    items: [{ label: "报告", path: "/reports" }],
  },
];

/** 扁平导航顺序（既有测试与外部引用的口径不变）。 */
export const NAV_ITEMS: readonly NavItem[] = NAV_GROUPS.flatMap((group) => group.items);

const routes: RouteRecordRaw[] = [
  { path: "/", redirect: "/versions" },
  { path: "/versions", name: "versions", component: VersionsPage },
  { path: "/versions/:version", name: "version-detail", component: VersionDetailPage },
  { path: "/preview", name: "preview", component: DataPreviewPage },
  { path: "/evidence", name: "evidence", component: CoverageEvidencePage },
  { path: "/jobs", name: "jobs", component: UpdateJobsPage },
  { path: "/reports", name: "reports", component: ReportsPage },
];

export function createPortalRouter() {
  return createRouter({ history: createWebHashHistory(), routes });
}

export const router = createPortalRouter();
