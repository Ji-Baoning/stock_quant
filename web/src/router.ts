import { createRouter, createWebHashHistory, type RouteRecordRaw } from "vue-router";
import VersionsPage from "./pages/VersionsPage.vue";
import VersionDetailPage from "./pages/VersionDetailPage.vue";
import DataPreviewPage from "./pages/DataPreviewPage.vue";
import CoverageEvidencePage from "./pages/CoverageEvidencePage.vue";
import UpdateJobsPage from "./pages/UpdateJobsPage.vue";
import ReportsPage from "./pages/ReportsPage.vue";

/** §10.3 页面地图：导航固定为 版本面板 → 数据预览 → 质量/覆盖证据 → 更新任务 → 报告。 */
export const NAV_ITEMS = [
  { label: "版本面板", path: "/versions" },
  { label: "数据预览", path: "/preview" },
  { label: "质量/覆盖证据", path: "/evidence" },
  { label: "更新任务", path: "/jobs" },
  { label: "报告", path: "/reports" },
] as const;

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
