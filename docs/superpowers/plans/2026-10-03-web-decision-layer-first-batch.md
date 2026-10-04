# Web 决策层 · 第一批次（S0a 组件库 + 决策台①③）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 v3 规格 §8 关键路径交付第一批：设计 token 层 + 六个基础组件（StateBadge/KpiCard/Card/EmptyState/Skeleton/DataTable）+ 侧边栏分组导航壳（S0a），以及决策台 `/` 页的块①（数据可信吗）、块③（需要我做什么）与块②的诚实空态（S2①③）——零端点变更、零新增运行时依赖、只消费既有 pin I5/I9/I10 契约。

**Architecture:** 纯前端改造。所有数据仍经既有 `ApiClient`（10 方法，零改动）取自 `/api/v1/datasets`、`/api/v1/experiments`、`/api/v1/update-jobs`；决策台数据组装进一个新 store（`stores/console.ts`），其中 update-jobs 的 **503 = 操作面默认禁用**（pin I9）被单独容忍为正常态而不是错误。导航从顶栏平铺改为侧边栏分组（`NAV_GROUPS`），同时保留 `NAV_ITEMS` 扁平导出与 `main-nav` testid，让既有测试口径平滑迁移。展示纪律沿用 P5：真实状态词、错误只走 `toDisplayError()`、空态诚实、前端零金融计算。

**Tech Stack:** 既有栈零新增：Vue 3.5.43、vue-router 5.3.1、Vite 8.3.1、vitest 5.0.3、@vue/test-utils 2.5.1、happy-dom 20.14.5、TypeScript 5.9.3、vue-tsc 3.3.11、@playwright/test 1.63.0。**`package.json` 的 `dependencies` 在本批次结束时应与开工时逐字节相同**（ECharts 属 S1）。

**Spec:** [docs/superpowers/specs/2026-10-03-web-portal-decision-layer-design.md](../specs/2026-10-03-web-portal-decision-layer-design.md)（v3 已裁定定稿）§4（信息架构）、§5.1（决策台）、§6（视觉系统）、§8（S0a/S2①③ 两行）、§9（不变量对照）、§10（裁定 2/4/7 的边界含义）。

## 规格审定记录（2026-10-03，按 brainstorming 技能四项自审标准）

1. **占位符扫描**：通过。无 TBD/TODO；S1 的"stability_report.json 逐字段对账"是明确的过程步骤且不在本批次；推迟项（S4、挑战区块）均有 owner 裁定背书。
2. **内部一致性**：通过。块③"失败 research run"依赖的端点 §7.2 未定义，但 §5.1 已自洽降级（"若需新端点则降级为可选块"）——本计划按降级执行。
3. **范围检查**：规格跨多阶段（S0a/S2①③/S1/S2②/S3a/S4），按 writing-plans 的 Scope Check 拆分为多个计划，**每计划独立产出可工作可测试的软件**。本计划=第一批（S0a+S2①③）；后续计划触发点见文末。
4. **歧义检查**：两处在计划层消解并在此显式化——(a) 侧边栏的"策略"组在 S1 落地前**不渲染**（不放假入口）；(b) 块②在 S2② 落地前渲染**诚实空态/计数一句话**（只用既有 pin I5 端点，不渲染任何结论指标），这是对规格 §3 原则 6（空态诚实）的执行，不是提前实现 S2②。

**结论：规格通过审定，可进入计划。**

## Global Constraints

- **零端点/零契约变更**：不改 `web/src/api/` 下任何文件（client.ts/types.ts/quality.ts/errors.ts 一概不动）；不改 `web/vite.config.ts` 的两条 proxy 规则（pin I12）；不动 `src/`、`tests/`、配置等 `web/` 之外的一切文件（本计划文档自身除外）。
- **零新增运行时依赖**：`package.json` 的 `dependencies` 与 `devDependencies` 不做任何增删改；不出现 ECharts（属 S1 计划）。
- **既有 testid 全部保留**：`main-nav` 这个 testid 转移到侧边栏 nav 元素上（app-shell 测试与潜在外部引用不断链）；五个页面与顶栏的全部既有 testid 原样保留。
- **展示纪律（P5 §10.2 延续，违反即缺陷）**：
  - 只出现真实状态词："门禁通过 / 门禁阻断（N 项）；质量问题 N 条"、验收四态原文（`ACCEPTED`/`REJECTED`/`PENDING_CONFIRMATION`/`UNVERIFIED`）、"加载中 / 获取失败 / 无待办 / 尚无已发布实验"——不新增翻译，不发明投资建议词汇。
  - CURRENT 是指针标记不是可信等级——徽标保留 `title="CURRENT 是指针标记，不是可信等级"`。
  - 错误只经 `toDisplayError()` 渲染稳定 code + 安全摘要，任何组件不得渲染 `error.stack`。
  - 前端零金融计算：组件是纯展示（DataTable 不格式化数字，KpiCard 不做正负判定——着色由调用方显式给 `tone`）。
  - 块②空态不得出现任何编造的指标、图表占位或"敬请期待"式营销文案。
- **CSS 纪律**：`styles.css` 与组件样式只消费 `tokens.css` 的自定义属性，**不出现硬编码十六进制颜色**（tokens.spec 持续守卫）；暗色只留 `[data-theme="dark"]` 同名变量结构，不做切换入口（裁定 4）。
- **测试全部离线**：vitest 注入 fake `ApiClient`（`web/tests/helpers.ts` 既有模式）；Playwright 用 `page.route` mock `/api/v1/**`，不请求真实后端。mock 数据只用 `"a".repeat(64)` 类假哈希（凭据零容忍）。
- **保护在途 WIP**：开工 Step 先跑 `git status`；除本计划新增/列出文件外，不覆盖、不回退、不暂存、不重排任何文件；每次提交只 `git add` 本任务文件。
- 每任务至少一个独立提交；提交信息英文，结尾 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- 工具链：node v24.0.0、npm 11.3.0；所有命令在 `/home/ji/work/program/stock/web` 下执行（git 命令在仓库根）。

## 开工前必须知道的实现形态

1. **当前文件形态（2026-10-03 实查）**：`src/router.ts` 33 行（`NAV_ITEMS` 平铺 5 项、`/` redirect 到 `/versions`）；`src/App.vue` 14 行（顶栏 + RouterView）；`src/components/AppTopBar.vue` 84 行（品牌/导航/指纹/已解析版本/CURRENT/刷新/新版本提示）；`src/styles.css` 26 行；`src/main.ts` 6 行（只 import `./styles.css`）。
2. **既有测试口径**：`tests/app-shell.spec.ts` 断言 `[data-testid="main-nav"]` 的链接文本与 href 等于 `NAV_ITEMS` 的扁平顺序——本计划把 `NAV_ITEMS` 改为从 `NAV_GROUPS` 派生的扁平导出（顺序：决策台、版本面板、数据预览、质量/覆盖证据、更新任务、报告），该测试断言逻辑保持、期望值随 Task 8 更新；`tests/top-bar.spec.ts` 只按 testid 断言（不钉品牌/导航 DOM），把导航从 AppTopBar 挪走**不会**破坏它。
3. **e2e mock 事实（Task 8 直接引用）**：`e2e/portal.spec.ts` 的 `installMockBackend` 中 `/api/v1/datasets` 的 HASH_A 为 `state: "ACCEPTED"` + `by_severity: {FATAL: 1}` → 块①文案是"门禁通过；质量问题 1 条"（门禁读 `acceptance.state`，P5 语义）；`/api/v1/experiments` 返回 2 条 → 块②是"已发布实验 2 个"分支。
4. **vitest 机制**：CSS `import` 在 vitest（Vite 管道）中被转换、不实际作用于 happy-dom——token 测试用 `?raw` 后缀读源码断言（`import x from "../src/styles/tokens.css?raw"` 是 Vite 原生支持）。
5. **Vue SFC 类型导出**：需要导出类型的组件（StateBadge/DataTable 等）用双 script 块（`<script lang="ts">` 放 `export interface`，`<script setup lang="ts">` 放逻辑）——Vue 3.5 对此一等支持，typecheck 走 vue-tsc。
6. **`UpdateJobStatus` 的值域**（types.ts 既有）：`QUEUED | RUNNING | SUCCEEDED | FAILED | CANCELLED_BY_SHUTDOWN`；活跃=前两者。

## 文件结构（全部在 `web/` 内）

| 文件 | 动作 | 职责 |
| --- | --- | --- |
| `src/styles/tokens.css` | 新建 | 设计 token 唯一权威（§6.1） |
| `src/styles.css` | 重写（Task 1）→ 追加（Task 2–6） | 消费 token 的全局样式 + 组件类 |
| `src/main.ts` | 修改（2 行） | 引入 tokens.css |
| `src/components/StateBadge.vue` | 新建 | 语义状态徽章（acceptance/conclusion/job 三映射 + 诚实回落） |
| `src/components/Card.vue` | 新建 | 卡片容器（标题 + 插槽） |
| `src/components/KpiCard.vue` | 新建 | KPI 大数卡（label/value/compare/tone） |
| `src/components/EmptyState.vue` | 新建 | 空态（标题/原因/下一步动作链接） |
| `src/components/Skeleton.vue` | 新建 | 加载骨架 |
| `src/components/DataTable.vue` | 新建 | 排序 + 粘性表头 + 空态回落的展示表格 |
| `src/components/AppShell.vue` | 新建 | 侧边栏分组导航 + 顶栏 + 主区布局 |
| `src/App.vue` | 修改 | 改用 AppShell |
| `src/components/AppTopBar.vue` | 修改 | 移除品牌与导航（迁至侧边栏），其余语义不动 |
| `src/router.ts` | 修改（Task 6 分组；Task 8 加 `/`） | NAV_GROUPS/NAV_ITEMS、路由 |
| `src/stores/console.ts` | 新建 | 决策台数据组装（datasets/experiments/jobs + 派生） |
| `src/pages/DecisionConsolePage.vue` | 新建 | 决策台三块 |
| `tests/tokens.spec.ts`、`tests/state-badge.spec.ts`、`tests/card-kpi.spec.ts`、`tests/empty-skeleton.spec.ts`、`tests/data-table.spec.ts`、`tests/console-store.spec.ts`、`tests/decision-console-page.spec.ts` | 新建 | 各任务测试 |
| `tests/app-shell.spec.ts` | 修改（Task 6/8） | 分组导航断言 |
| `e2e/portal.spec.ts` | 修改（Task 8） | 新增决策台 e2e |

---

### Task 1: 设计 token 层与全局样式 token 化

**Files:**
- Create: `web/src/styles/tokens.css`
- Modify: `web/src/styles.css`（整文件重写，选择器集合不变）
- Modify: `web/src/main.ts`
- Test: `web/tests/tokens.spec.ts`

**Interfaces:**
- Consumes: 无（最底层）。
- Produces: CSS 自定义属性集合（下方清单）；后续所有组件与样式只消费这些名字。

- [ ] **Step 1: 写失败测试**

```ts
// web/tests/tokens.spec.ts
import { describe, expect, it } from "vitest";
import tokensSource from "../src/styles/tokens.css?raw";
import stylesSource from "../src/styles.css?raw";

const REQUIRED_TOKENS = [
  "--color-bg",
  "--color-surface",
  "--color-surface-muted",
  "--color-border",
  "--color-border-strong",
  "--color-text-secondary",
  "--color-text",
  "--color-primary",
  "--color-pass",
  "--color-block",
  "--color-pending",
  "--color-info",
  "--color-up",
  "--color-down",
  "--color-benchmark",
  "--color-inverse-bg",
  "--color-inverse-text",
  "--color-block-bg",
  "--color-block-border",
  "--color-pending-bg",
  "--color-pending-border",
  "--space-1",
  "--space-4",
  "--radius-card",
  "--shadow-card",
  "--font-size-body",
  "--font-size-kpi",
  "--content-max-width",
  "--sidebar-width",
];

describe("设计 token 层（规格 §6.1）", () => {
  it("tokens.css 定义全部必需 token", () => {
    for (const token of REQUIRED_TOKENS) {
      expect(tokensSource, `缺少 ${token}`).toContain(`${token}:`);
    }
  });

  it("dark 主题只预留同名变量结构（裁定 4：无切换入口）", () => {
    expect(tokensSource).toContain('[data-theme="dark"]');
    // 结构性预留：dark 块里没有切换/媒体查询逻辑。
    expect(tokensSource).not.toContain("prefers-color-scheme");
  });

  it("全局样式不硬编码十六进制颜色（只消费 token）", () => {
    const hexColors = stylesSource.match(/#[0-9a-fA-F]{3,8}\b/g) ?? [];
    expect(hexColors, `styles.css 残留硬编码颜色：${hexColors.join(",")}`).toEqual([]);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npm test -- tokens.spec`
Expected: FAIL——`tokens.css` 不存在（Vite `?raw` 导入解析失败/文件为空导致断言失败）。

- [ ] **Step 3: 创建 tokens.css 并重写 styles.css**

```css
/* web/src/styles/tokens.css —— 设计 token 唯一权威（v3 规格 §6.1）。
   light 是默认主题；dark 只预留同名变量结构，无切换入口（裁定 4）。 */
:root {
  /* 中性色 8 级（bg → text） */
  --color-bg: #f6f7f9;
  --color-surface: #ffffff;
  --color-surface-muted: #eef0f3;
  --color-border: #d9dee4;
  --color-border-strong: #b9c0c9;
  --color-text-secondary: #4a5158;
  --color-text: #1b1f24;

  /* 主色 */
  --color-primary: #2459a8;
  --color-primary-strong: #1d4786;

  /* 语义色：状态 */
  --color-pass: #1c7c4a;
  --color-block: #b3261e;
  --color-pending: #8a5b00;
  --color-info: #2a7de1;
  /* 语义色：涨跌（A股惯例：红涨绿跌）与基准 */
  --color-up: #c23a2b;
  --color-down: #1c7c4a;
  --color-benchmark: #6b7280;
  /* 状态底色对（hint/error 等） */
  --color-block-bg: #fdecea;
  --color-block-border: #f0b4ae;
  --color-pending-bg: #fff6e0;
  --color-pending-border: #e5c36b;
  --color-inverse-bg: #101418;
  --color-inverse-text: #d7dde3;

  /* 间距（4px 基阶） */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 24px;
  --space-6: 32px;

  /* 圆角 / 阴影（两档） */
  --radius-card: 8px;
  --radius-pop: 12px;
  --shadow-card: 0 1px 2px rgba(27, 31, 36, 0.06);
  --shadow-pop: 0 4px 16px rgba(27, 31, 36, 0.12);

  /* 字号（正文 14；KPI 28/36 两档） */
  --font-size-body: 14px;
  --font-size-small: 12px;
  --font-size-heading: 18px;
  --font-size-kpi: 28px;
  --font-size-kpi-large: 36px;

  /* 版式 */
  --content-max-width: 1280px;
  --sidebar-width: 220px;
}

/* dark 预留（裁定 4）：同名变量；本期不做对比度校验、不做切换。 */
[data-theme="dark"] {
  --color-bg: #101418;
  --color-surface: #171c22;
  --color-surface-muted: #1f262e;
  --color-border: #2a323c;
  --color-border-strong: #3a444f;
  --color-text-secondary: #9aa4ae;
  --color-text: #e6eaee;
  --color-inverse-bg: #e6eaee;
  --color-inverse-text: #101418;
}
```

`web/src/styles.css` 整文件重写为（**选择器集合与既有完全一致**，仅取值改走 token；组件类由 Task 2–6 追加）：

```css
:root { font-family: system-ui, sans-serif; color: var(--color-text); background: var(--color-bg); }
* { box-sizing: border-box; }
body { margin: 0; }
code { font-family: ui-monospace, monospace; font-size: 0.9em; background: var(--color-surface-muted); padding: 0 4px; border-radius: 3px; word-break: break-all; }
pre { background: var(--color-inverse-bg); color: var(--color-inverse-text); padding: var(--space-3); overflow-x: auto; border-radius: 6px; }
.top-bar { display: flex; flex-wrap: wrap; gap: var(--space-3) var(--space-5); align-items: center; padding: 10px var(--space-4); background: var(--color-surface); border-bottom: 1px solid var(--color-border); }
.brand { font-weight: 700; }
.nav a { margin-right: var(--space-3); color: var(--color-primary); text-decoration: none; }
.nav a.router-link-active { font-weight: 700; text-decoration: underline; }
.fingerprint, .resolved { font-size: 0.9em; color: var(--color-text-secondary); }
.badge { display: inline-block; margin-left: 6px; padding: 1px 8px; border: 1px solid var(--color-info); border-radius: 10px; color: var(--color-info); font-size: 0.8em; }
.hint { margin: var(--space-2) var(--space-4); padding: var(--space-2) var(--space-3); background: var(--color-pending-bg); border: 1px solid var(--color-pending-border); border-radius: 6px; }
.page { padding: var(--space-4) var(--space-5); max-width: var(--content-max-width); }
table { border-collapse: collapse; width: 100%; margin: var(--space-2) 0 var(--space-5); background: var(--color-surface); }
th, td { border: 1px solid var(--color-border); padding: 6px 10px; text-align: left; font-size: 0.92em; vertical-align: top; }
th { background: var(--color-surface-muted); }
.error { color: var(--color-block); background: var(--color-block-bg); border: 1px solid var(--color-block-border); padding: var(--space-2) var(--space-3); border-radius: 6px; }
.warn { color: var(--color-pending); }
.acceptance-columns { display: flex; gap: var(--space-5); }
.acceptance-columns > div { border: 1px solid var(--color-border); padding: var(--space-2) var(--space-4); background: var(--color-surface); }
form label { display: inline-block; margin: var(--space-1) 14px var(--space-1) 0; font-size: 0.92em; }
form input, form select { margin-left: 6px; }
fieldset { border: 1px solid var(--color-border); margin: 10px 0; }
.copy { margin-left: 6px; }
button { cursor: pointer; }
blockquote { margin: var(--space-2) 0; padding: var(--space-2) 14px; border-left: 4px solid var(--color-info); background: var(--color-surface); }
```

`web/src/main.ts` 全文替换为：

```ts
import { createApp } from "vue";
import App from "./App.vue";
import { router } from "./router";
import "./styles/tokens.css";
import "./styles.css";

createApp(App).use(router).mount("#app");
```

- [ ] **Step 4: 运行测试确认通过**

Run: `npm test -- tokens.spec`
Expected: PASS（3 个用例全绿）。

- [ ] **Step 5: 全量回归（DOM 行为不应受影响）**

Run: `npm test`
Expected: 全部既有 spec PASS（本任务只改样式取值，不改任何 DOM/断言语义）。

- [ ] **Step 6: 提交**

```bash
git add web/src/styles/tokens.css web/src/styles.css web/src/main.ts web/tests/tokens.spec.ts
git commit -m "feat(web): add design token layer and tokenize global styles

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: StateBadge 语义状态徽章

**Files:**
- Create: `web/src/components/StateBadge.vue`
- Modify: `web/src/styles.css`（追加徽章类）
- Test: `web/tests/state-badge.spec.ts`

**Interfaces:**
- Consumes: Task 1 的 token。
- Produces: 组件 props `{ kind: "acceptance" | "conclusion" | "job"; value: string; title?: string }`；类型导出 `BadgeKind`、`BadgeTone`；根元素 `data-testid="state-badge"`，class 形如 `badge-state--pass`。S1 的策略页将直接消费 `kind: "conclusion"`。

- [ ] **Step 1: 写失败测试**

```ts
// web/tests/state-badge.spec.ts
import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPortalRouter } from "../src/router";
import StateBadge from "../src/components/StateBadge.vue";

function mountBadge(kind: "acceptance" | "conclusion" | "job", value: string) {
  return mount(StateBadge, {
    props: { kind, value },
    global: { plugins: [createPortalRouter()] },
  });
}

describe("StateBadge（语义状态徽章）", () => {
  it("验收四态映射（真实状态词原文显示）", () => {
    const cases: Array<[string, string]> = [
      ["ACCEPTED", "pass"],
      ["REJECTED", "block"],
      ["PENDING_CONFIRMATION", "pending"],
      ["UNVERIFIED", "neutral"],
    ];
    for (const [value, tone] of cases) {
      const wrapper = mountBadge("acceptance", value);
      expect(wrapper.get('[data-testid="state-badge"]').classes()).toContain(`badge-state--${tone}`);
      expect(wrapper.get('[data-testid="state-badge"]').text()).toBe(value);
    }
  });

  it("walk-forward 三态结论映射", () => {
    const cases: Array<[string, string]> = [
      ["STABLE", "pass"],
      ["UNSTABLE", "block"],
      ["INCONCLUSIVE", "pending"],
    ];
    for (const [value, tone] of cases) {
      const wrapper = mountBadge("conclusion", value);
      expect(wrapper.get('[data-testid="state-badge"]').classes()).toContain(`badge-state--${tone}`);
    }
  });

  it("任务状态映射", () => {
    const cases: Array<[string, string]> = [
      ["SUCCEEDED", "pass"],
      ["FAILED", "block"],
      ["QUEUED", "pending"],
      ["RUNNING", "pending"],
      ["CANCELLED_BY_SHUTDOWN", "neutral"],
    ];
    for (const [value, tone] of cases) {
      const wrapper = mountBadge("job", value);
      expect(wrapper.get('[data-testid="state-badge"]').classes()).toContain(`badge-state--${tone}`);
    }
  });

  it("未知值诚实回落：neutral + 原文显示（不翻译、不隐藏）", () => {
    const wrapper = mountBadge("acceptance", "SOMETHING_NEW");
    expect(wrapper.get('[data-testid="state-badge"]').classes()).toContain("badge-state--neutral");
    expect(wrapper.get('[data-testid="state-badge"]').text()).toBe("SOMETHING_NEW");
  });

  it("title 默认回退到值本身，可显式覆盖", () => {
    const plain = mountBadge("job", "RUNNING");
    expect(plain.get('[data-testid="state-badge"]').attributes("title")).toBe("RUNNING");
    const titled = mount(StateBadge, {
      props: { kind: "job", value: "RUNNING", title: "任务仍在运行" },
      global: { plugins: [createPortalRouter()] },
    });
    expect(titled.get('[data-testid="state-badge"]').attributes("title")).toBe("任务仍在运行");
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npm test -- state-badge.spec`
Expected: FAIL——组件文件不存在，导入报错。

- [ ] **Step 3: 实现组件并追加样式**

```vue
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
```

`web/src/styles.css` 末尾追加：

```css
/* StateBadge（Task 2）：语义色来自 token；neutral 走次级文本色。 */
.badge-state { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: var(--font-size-small); border: 1px solid var(--color-border-strong); color: var(--color-text-secondary); }
.badge-state--pass { border-color: var(--color-pass); color: var(--color-pass); }
.badge-state--block { border-color: var(--color-block); color: var(--color-block); }
.badge-state--pending { border-color: var(--color-pending); color: var(--color-pending); }
.badge-state--neutral { border-color: var(--color-border-strong); color: var(--color-text-secondary); }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `npm test -- state-badge.spec`
Expected: PASS（5 用例全绿）。

- [ ] **Step 5: 提交**

```bash
git add web/src/components/StateBadge.vue web/src/styles.css web/tests/state-badge.spec.ts
git commit -m "feat(web): add StateBadge semantic status badge

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: Card 与 KpiCard

**Files:**
- Create: `web/src/components/Card.vue`
- Create: `web/src/components/KpiCard.vue`
- Modify: `web/src/styles.css`（追加卡片类）
- Test: `web/tests/card-kpi.spec.ts`

**Interfaces:**
- Produces:
  - `Card`：props `{ title?: string; testid?: string }`，默认插槽为内容；根 `data-testid` 取 `props.testid ?? "card"`。
  - `KpiCard`：props `{ label: string; value: string; compare?: string; tone?: KpiTone; testid?: string }`；类型导出 `KpiTone = "neutral" | "up" | "down" | "pending" | "block"`；根 `data-testid` 取 `props.testid ?? "kpi-card"`。**不做任何数值计算**：着色必须由调用方显式给 `tone`（零金融计算纪律）。

- [ ] **Step 1: 写失败测试**

```ts
// web/tests/card-kpi.spec.ts
import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import Card from "../src/components/Card.vue";
import KpiCard from "../src/components/KpiCard.vue";

describe("Card", () => {
  it("渲染标题与默认插槽；无标题时不渲染标题元素", () => {
    const withTitle = mount(Card, { props: { title: "① 数据现在可信吗？", testid: "block-data-trust" }, slots: { default: "<p>内容</p>" } });
    expect(withTitle.get('[data-testid="block-data-trust"]').find("h2").text()).toBe("① 数据现在可信吗？");
    expect(withTitle.get('[data-testid="block-data-trust"]').text()).toContain("内容");

    const noTitle = mount(Card, { slots: { default: "<p>内容</p>" } });
    expect(noTitle.get('[data-testid="card"]').find("h2").exists()).toBe(false);
  });
});

describe("KpiCard", () => {
  it("渲染 label/value/compare，value 用等宽数字类", () => {
    const wrapper = mount(KpiCard, {
      props: { label: "质量问题", value: "3 条", compare: "基准 000300.SH" },
    });
    expect(wrapper.get('[data-testid="kpi-card"]').text()).toContain("质量问题");
    expect(wrapper.get('[data-testid="kpi-card"]').text()).toContain("3 条");
    expect(wrapper.get(".kpi-value").classes()).toContain("kpi-num");
    expect(wrapper.find(".kpi-compare").exists()).toBe(true);
  });

  it("tone 由调用方显式给出（组件不做正负判定）", () => {
    const up = mount(KpiCard, { props: { label: "超额", value: "+2.1%", tone: "up" } });
    expect(up.get(".kpi-value").classes()).toContain("kpi-value--up");
    const none = mount(KpiCard, { props: { label: "换手", value: "4.3" } });
    expect(none.get(".kpi-value").classes()).not.toContain("kpi-value--up");
  });

  it("compare 缺省时不渲染对照行", () => {
    const wrapper = mount(KpiCard, { props: { label: "换手", value: "4.3" } });
    expect(wrapper.find(".kpi-compare").exists()).toBe(false);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npm test -- card-kpi.spec`
Expected: FAIL——组件不存在。

- [ ] **Step 3: 实现两个组件并追加样式**

```vue
<!-- web/src/components/Card.vue -->
<script setup lang="ts">
const props = defineProps<{ title?: string; testid?: string }>();
</script>

<template>
  <section class="card" :data-testid="props.testid ?? 'card'">
    <h2 v-if="props.title" class="card-title">{{ props.title }}</h2>
    <slot />
  </section>
</template>
```

```vue
<!-- web/src/components/KpiCard.vue -->
<script lang="ts">
export type KpiTone = "neutral" | "up" | "down" | "pending" | "block";
</script>

<script setup lang="ts">
const props = defineProps<{
  label: string;
  value: string;
  compare?: string;
  tone?: KpiTone;
  testid?: string;
}>();
</script>

<template>
  <div class="kpi" :data-testid="props.testid ?? 'kpi-card'">
    <span class="kpi-label">{{ props.label }}</span>
    <span class="kpi-value kpi-num" :class="props.tone ? `kpi-value--${props.tone}` : undefined">
      {{ props.value }}
    </span>
    <span v-if="props.compare" class="kpi-compare">{{ props.compare }}</span>
  </div>
</template>
```

`web/src/styles.css` 末尾追加：

```css
/* Card / KpiCard（Task 3） */
.card { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-card); box-shadow: var(--shadow-card); padding: var(--space-4) var(--space-5); margin: 0 0 var(--space-4); }
.card-title { margin: 0 0 var(--space-3); font-size: var(--font-size-heading); }
.kpi { display: flex; flex-direction: column; gap: var(--space-1); padding: var(--space-3) var(--space-4); background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-card); }
.kpi-label { font-size: var(--font-size-small); color: var(--color-text-secondary); }
.kpi-num { font-variant-numeric: tabular-nums; }
.kpi-value { font-size: var(--font-size-kpi); font-weight: 600; }
.kpi-value--up { color: var(--color-up); }
.kpi-value--down { color: var(--color-down); }
.kpi-value--pending { color: var(--color-pending); }
.kpi-value--block { color: var(--color-block); }
.kpi-compare { font-size: var(--font-size-small); color: var(--color-text-secondary); }
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: var(--space-3); }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `npm test -- card-kpi.spec`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add web/src/components/Card.vue web/src/components/KpiCard.vue web/src/styles.css web/tests/card-kpi.spec.ts
git commit -m "feat(web): add Card and KpiCard display components

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: EmptyState 与 Skeleton

**Files:**
- Create: `web/src/components/EmptyState.vue`
- Create: `web/src/components/Skeleton.vue`
- Modify: `web/src/styles.css`（追加两类样式）
- Test: `web/tests/empty-skeleton.spec.ts`

**Interfaces:**
- Produces:
  - `EmptyState`：props `{ title: string; description?: string; actionLabel?: string; actionTo?: string }`；根 `data-testid="empty-state"`；动作链接 `data-testid="empty-state-action"`（`actionLabel` 与 `actionTo` 同时给出才渲染）。
  - `Skeleton`：props `{ rows?: number }`（默认 3）；根 `data-testid="skeleton"` 且 `aria-busy="true"`。

- [ ] **Step 1: 写失败测试**

```ts
// web/tests/empty-skeleton.spec.ts
import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPortalRouter } from "../src/router";
import EmptyState from "../src/components/EmptyState.vue";
import Skeleton from "../src/components/Skeleton.vue";

function mountWithRouter(component: Parameters<typeof mount>[0], props: Record<string, unknown> = {}) {
  return mount(component, { props, global: { plugins: [createPortalRouter()] } });
}

describe("EmptyState（空态是一级场景，诚实引导）", () => {
  it("渲染标题与原因说明", () => {
    const wrapper = mountWithRouter(EmptyState, {
      title: "尚无已发布实验",
      description: "data/experiments 为空；发起一次 research run 并发布后，结论将在此展示。",
    });
    expect(wrapper.get('[data-testid="empty-state"]').text()).toContain("尚无已发布实验");
    expect(wrapper.get('[data-testid="empty-state"]').text()).toContain("data/experiments 为空");
  });

  it("给出下一步动作链接（label 与 to 成对才渲染）", () => {
    const wrapper = mountWithRouter(EmptyState, {
      title: "尚无已发布实验",
      actionLabel: "查看报告页",
      actionTo: "/reports",
    });
    const action = wrapper.get('[data-testid="empty-state-action"]');
    expect(action.text()).toBe("查看报告页");
    expect(action.attributes("href")).toBe("#/reports");
  });

  it("没有动作时不渲染链接", () => {
    const wrapper = mountWithRouter(EmptyState, { title: "无待办" });
    expect(wrapper.find('[data-testid="empty-state-action"]').exists()).toBe(false);
  });
});

describe("Skeleton", () => {
  it("按 rows 渲染骨架行，aria-busy 标记加载中", () => {
    const wrapper = mountWithRouter(Skeleton, { rows: 4 });
    expect(wrapper.get('[data-testid="skeleton"]').attributes("aria-busy")).toBe("true");
    expect(wrapper.findAll(".skeleton-row")).toHaveLength(4);
  });

  it("rows 缺省为 3", () => {
    const wrapper = mountWithRouter(Skeleton);
    expect(wrapper.findAll(".skeleton-row")).toHaveLength(3);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npm test -- empty-skeleton.spec`
Expected: FAIL——组件不存在。

- [ ] **Step 3: 实现组件并追加样式**

```vue
<!-- web/src/components/EmptyState.vue -->
<script setup lang="ts">
const props = defineProps<{
  title: string;
  description?: string;
  actionLabel?: string;
  actionTo?: string;
}>();
</script>

<template>
  <div class="empty-state" data-testid="empty-state">
    <p class="empty-state-title">{{ props.title }}</p>
    <p v-if="props.description" class="empty-state-description">{{ props.description }}</p>
    <RouterLink
      v-if="props.actionLabel !== undefined && props.actionTo !== undefined"
      :to="props.actionTo"
      data-testid="empty-state-action"
    >
      {{ props.actionLabel }}
    </RouterLink>
  </div>
</template>
```

```vue
<!-- web/src/components/Skeleton.vue -->
<script setup lang="ts">
const props = withDefaults(defineProps<{ rows?: number }>(), { rows: 3 });
</script>

<template>
  <div class="skeleton" data-testid="skeleton" aria-busy="true">
    <div v-for="row in props.rows" :key="row" class="skeleton-row" />
  </div>
</template>
```

`web/src/styles.css` 末尾追加：

```css
/* EmptyState / Skeleton（Task 4） */
.empty-state { padding: var(--space-5); border: 1px dashed var(--color-border-strong); border-radius: var(--radius-card); background: var(--color-surface); text-align: center; }
.empty-state-title { margin: 0 0 var(--space-2); font-weight: 600; }
.empty-state-description { margin: 0 0 var(--space-3); color: var(--color-text-secondary); font-size: var(--font-size-small); }
.empty-state a { color: var(--color-primary); }
.skeleton { display: flex; flex-direction: column; gap: var(--space-2); padding: var(--space-3) 0; }
.skeleton-row { height: 14px; border-radius: var(--radius-card); background: var(--color-surface-muted); animation: skeleton-pulse 1.2s ease-in-out infinite; }
@keyframes skeleton-pulse { 0% { opacity: 1; } 50% { opacity: 0.45; } 100% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .skeleton-row { animation: none; } }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `npm test -- empty-skeleton.spec`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add web/src/components/EmptyState.vue web/src/components/Skeleton.vue web/src/styles.css web/tests/empty-skeleton.spec.ts
git commit -m "feat(web): add EmptyState and Skeleton components

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: DataTable（排序 + 粘性表头 + 空态回落）

**Files:**
- Create: `web/src/components/DataTable.vue`
- Modify: `web/src/styles.css`（追加表格类）
- Test: `web/tests/data-table.spec.ts`

**Interfaces:**
- Produces: 类型导出 `DataTableColumn { key: string; label: string; sortable?: boolean; align?: "left" | "right"; mono?: boolean }`；props `{ columns: DataTableColumn[]; rows: Record<string, unknown>[]; rowKey?: string; emptyText?: string; testid?: string }`；根 `data-testid` 取 `props.testid ?? "data-table"`；空数据渲染 `data-testid="data-table-empty"` 单行。**展示组件不格式化、不派生**：单元格渲染调用方给定的值（null/undefined/"" → "—"）；排序是 UI 稳定行为不是指标计算。S0b（五页换皮）与 S1（列表/逐折表）消费它。

- [ ] **Step 1: 写失败测试**

```ts
// web/tests/data-table.spec.ts
import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import DataTable from "../src/components/DataTable.vue";

const columns = [
  { key: "name", label: "名称", sortable: true },
  { key: "value", label: "数值", sortable: true, align: "right" as const },
  { key: "hash", label: "哈希", mono: true },
];

const rows = [
  { name: "b 折", value: 2, hash: "bbbb" },
  { name: "a 折", value: 10, hash: "aaaa" },
  { name: "c 折", value: 5, hash: null },
];

function mountTable(props: Record<string, unknown> = {}) {
  return mount(DataTable, { props: { columns, rows, ...props } });
}

describe("DataTable（纯展示表格）", () => {
  it("渲染列头与行；null 单元格回落为破折号", () => {
    const wrapper = mountTable();
    const header = wrapper.findAll("th").map((th) => th.text());
    expect(header).toEqual(["名称", "数值", "哈希"]);
    const cells = wrapper.findAll("tbody tr")[0].findAll("td").map((td) => td.text());
    expect(cells).toEqual(["b 折", "2", "bbbb"]);
    const thirdRow = wrapper.findAll("tbody tr")[2].findAll("td").map((td) => td.text());
    expect(thirdRow).toEqual(["c 折", "5", "—"]);
  });

  it("点击排序列：数值升序 → 再点降序，aria-sort 跟随", async () => {
    const wrapper = mountTable();
    const valueHeader = wrapper.findAll("th")[1];
    await valueHeader.trigger("click");
    let firstRow = wrapper.findAll("tbody tr")[0].findAll("td")[1].text();
    expect(firstRow).toBe("2");
    expect(valueHeader.attributes("aria-sort")).toBe("ascending");
    await valueHeader.trigger("click");
    firstRow = wrapper.findAll("tbody tr")[0].findAll("td")[1].text();
    expect(firstRow).toBe("10");
    expect(valueHeader.attributes("aria-sort")).toBe("descending");
  });

  it("字符串列按中文 locale 排序；不可排序列点击无效", async () => {
    const wrapper = mountTable();
    await wrapper.findAll("th")[0].trigger("click");
    const names = wrapper.findAll("tbody tr").map((tr) => tr.findAll("td")[0].text());
    expect(names).toEqual(["a 折", "b 折", "c 折"]);
    const hashHeader = wrapper.findAll("th")[2];
    await hashHeader.trigger("click");
    expect(hashHeader.attributes("aria-sort")).toBeUndefined();
  });

  it("空数据渲染空态单行，文案可配", () => {
    const wrapper = mountTable({ rows: [], emptyText: "尚无已发布实验" });
    expect(wrapper.get('[data-testid="data-table-empty"]').text()).toBe("尚无已发布实验");
  });

  it("右对齐与等宽类按列配置落在单元格上", () => {
    const wrapper = mountTable();
    expect(wrapper.findAll("th")[1].classes()).toContain("cell-right");
    expect(wrapper.findAll("tbody tr")[0].findAll("td")[2].classes()).toContain("cell-mono");
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npm test -- data-table.spec`
Expected: FAIL——组件不存在。

- [ ] **Step 3: 实现组件并追加样式**

```vue
<!-- web/src/components/DataTable.vue -->
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
          @click="toggleSort(column)"
        >
          {{ column.label }}
        </th>
      </tr>
    </thead>
    <tbody v-if="sortedRows.length > 0">
      <tr
        v-for="(row, index) in sortedRows"
        :key="props.rowKey !== undefined ? String(row[props.rowKey]) : index"
      >
        <td
          v-for="column in props.columns"
          :key="column.key"
          :class="[column.align === 'right' ? 'cell-right' : '', column.mono ? 'cell-mono' : '']"
        >
          {{ cellText(row, column) }}
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
```

`web/src/styles.css` 末尾追加：

```css
/* DataTable（Task 5）：复用全局 table 基样式，叠加排序/对齐/粘性表头。 */
.data-table thead th { position: sticky; top: 0; z-index: 1; }
.th-sortable { cursor: pointer; user-select: none; }
.th-sortable::after { content: " ⇅"; color: var(--color-text-secondary); font-size: var(--font-size-small); }
.cell-right { text-align: right; }
.cell-mono { font-family: ui-monospace, monospace; font-variant-numeric: tabular-nums; }
.data-table-empty { text-align: center; color: var(--color-text-secondary); }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `npm test -- data-table.spec`
Expected: PASS（5 用例全绿）。

- [ ] **Step 5: 提交**

```bash
git add web/src/components/DataTable.vue web/src/styles.css web/tests/data-table.spec.ts
git commit -m "feat(web): add sortable DataTable display component

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: AppShell 侧边栏分组导航（含 router 分组与顶栏瘦身）

**Files:**
- Create: `web/src/components/AppShell.vue`
- Modify: `web/src/router.ts`（`NAV_GROUPS` + 派生 `NAV_ITEMS`；路由表本任务不动）
- Modify: `web/src/App.vue`（改用 AppShell）
- Modify: `web/src/components/AppTopBar.vue`（移除品牌与导航；其余不动）
- Modify: `web/src/styles.css`（追加壳样式）
- Test: `web/tests/app-shell.spec.ts`（更新断言）

**Interfaces:**
- Produces:
  - `NAV_GROUPS: readonly NavGroup[]`，`NavGroup { label: string | null; items: readonly NavItem[] }`，`NavItem { label: string; path: string }`（router.ts 类型导出）；`NAV_ITEMS` 继续存在（由 `NAV_GROUPS.flatMap` 派生），既有消费方不断。
  - 侧边栏 nav 元素保留 `data-testid="main-nav"`。
  - AppTopBar 不再渲染 `.brand` 与 `.nav`（迁至侧边栏），`project-fingerprint` / `top-resolved-version` / `copy-version` / `current-badge` / `refresh-current` / `new-version-hint` 全部 testid 与语义不变。

- [ ] **Step 1: 更新失败测试**

`web/tests/app-shell.spec.ts` 全文替换为：

```ts
import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import App from "../src/App.vue";
import { NAV_GROUPS, NAV_ITEMS, createPortalRouter } from "../src/router";
import { fakeClient } from "./helpers";

describe("应用骨架（侧边栏分组导航，v3 规格 §4）", () => {
  it("侧边栏渲染分组与独立项；扁平顺序 = NAV_ITEMS", async () => {
    const wrapper = mount(App, {
      props: { client: fakeClient() },
      global: { plugins: [createPortalRouter()] },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    const nav = wrapper.get('[data-testid="main-nav"]');
    const links = nav.findAll("a");
    expect(links.map((link) => link.text())).toEqual(NAV_ITEMS.map((item) => item.label));
    expect(links.map((link) => link.attributes("href"))).toEqual(
      NAV_ITEMS.map((item) => `#${item.path}`),
    );
    // 分组标签存在且顺序固定（规格 §4：数据 → 运维；报告为独立项直到 S1 下线）。
    const groupLabels = nav.findAll(".side-group-label").map((label) => label.text());
    expect(groupLabels).toEqual(NAV_GROUPS.filter((g) => g.label !== null).map((g) => g.label));
  });

  it("顶栏不再承载导航，但保留指纹与已解析版本区", async () => {
    const wrapper = mount(App, {
      props: { client: fakeClient() },
      global: { plugins: [createPortalRouter()] },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(wrapper.get('[data-testid="top-bar"]').find("nav").exists()).toBe(false);
    expect(wrapper.get('[data-testid="project-fingerprint"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="top-resolved-version"]').exists()).toBe(true);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npm test -- app-shell.spec`
Expected: FAIL——`NAV_GROUPS` 未导出（导入错误）。

- [ ] **Step 3: 实现 router 分组、AppShell、App 与顶栏修改**

`web/src/router.ts` 的头部（import 与 NAV 部分）替换为：

```ts
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
```

（`routes` 与 `createPortalRouter` 保持原样；本任务不加 `/` 决策台路由。）

`web/src/components/AppShell.vue`：

```vue
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
```

`web/src/App.vue` 全文替换为：

```vue
<script setup lang="ts">
import { createApiClient, provideApiClient, type ApiClient } from "./api/client";
import AppShell from "./components/AppShell.vue";

const props = defineProps<{ client?: ApiClient }>();
provideApiClient(props.client ?? createApiClient());
</script>

<template>
  <AppShell />
</template>
```

`web/src/components/AppTopBar.vue`：删除模板里的 `<span class="brand">…</span>` 与 `<nav class="nav" …>…</nav>` 两段，删除 `NAV_ITEMS` 导入；`<script setup>` 其余逻辑（health/refresh/copy）与剩余模板（fingerprint/resolved/CURRENT/refresh/hint）**逐字不动**。修改后模板为：

```vue
<template>
  <header class="top-bar" data-testid="top-bar">
    <span class="fingerprint" data-testid="project-fingerprint">
      项目指纹：<code>{{ versionPinState.projectFingerprint ?? "获取失败" }}</code>
    </span>
    <span class="resolved" data-testid="top-resolved-version">
      已解析版本：
      <code v-if="versionPinState.resolvedVersion !== null">{{ versionPinState.resolvedVersion }}</code>
      <span v-else>未解析</span>
      <button
        type="button"
        class="copy"
        data-testid="copy-version"
        :disabled="versionPinState.resolvedVersion === null"
        @click="copyResolvedVersion"
      >
        复制
      </button>
      <span
        v-if="
          versionPinState.resolvedVersion !== null &&
          versionPinState.resolvedVersion === versionPinState.currentVersion
        "
        class="badge"
        title="CURRENT 是指针标记，不是可信等级"
        data-testid="current-badge"
      >
        CURRENT
      </span>
      <button type="button" data-testid="refresh-current" @click="refreshCurrent">
        刷新 CURRENT
      </button>
    </span>
  </header>
  <p v-if="hasNewerCurrent" class="hint" data-testid="new-version-hint">
    有新版本：<code>{{ versionPinState.currentVersion }}</code>
    ；当前页面保持 <code>{{ versionPinState.resolvedVersion }}</code>
    ，不自动切换（<RouterLink to="/versions">查看版本面板</RouterLink>）
  </p>
</template>
```

`web/src/styles.css` 末尾追加：

```css
/* AppShell（Task 6）：侧边栏 + 顶栏 + 主区。窄屏折叠为纵向堆叠。 */
.app-shell { display: flex; min-height: 100vh; }
.sidebar { width: var(--sidebar-width); flex-shrink: 0; background: var(--color-surface); border-right: 1px solid var(--color-border); padding: var(--space-4) var(--space-3); }
.sidebar .brand { margin: 0 0 var(--space-4); font-weight: 700; }
.side-nav { display: flex; flex-direction: column; gap: var(--space-2); }
.side-group-label { margin: var(--space-3) 0 var(--space-1); font-size: var(--font-size-small); color: var(--color-text-secondary); }
.side-nav a { color: var(--color-primary); text-decoration: none; }
.side-nav a.router-link-active { font-weight: 700; text-decoration: underline; }
.shell-main { flex: 1; min-width: 0; }
@media (max-width: 800px) {
  .app-shell { flex-direction: column; }
  .sidebar { width: auto; border-right: none; border-bottom: 1px solid var(--color-border); }
}
```

- [ ] **Step 4: 运行测试确认通过（含全量回归）**

Run: `npm test`
Expected: app-shell 新断言 PASS；top-bar.spec 与五个页面 spec 全绿（顶栏 testid 未动、路由未动）。

- [ ] **Step 5: 提交**

```bash
git add web/src/router.ts web/src/App.vue web/src/components/AppShell.vue web/src/components/AppTopBar.vue web/src/styles.css web/tests/app-shell.spec.ts
git commit -m "feat(web): regroup navigation into sidebar AppShell

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 7: 决策台数据组装 store（含 pin I9 的 503 容忍）

**Files:**
- Create: `web/src/stores/console.ts`
- Test: `web/tests/console-store.spec.ts`

**Interfaces:**
- Consumes: `ApiClient`（零修改）、`toDisplayError`（errors.ts 既有）。
- Produces（Task 8 消费，签名如下）：
  - `consoleState: ConsoleState`（reactive）：`{ datasets: DatasetSummary[]; current: string | null; experiments: ExperimentSummary[]; jobs: UpdateJobSummary[]; operationsEnabled: boolean; loaded: boolean; error: DisplayError | null }`
  - `loadConsoleData(client: ApiClient): Promise<void>`
  - computed：`currentDataset: ComputedRef<DatasetSummary | null>`、`pendingConfirmations: ComputedRef<DatasetSummary[]>`（`acceptance.state === "PENDING_CONFIRMATION"`）、`activeJobs: ComputedRef<UpdateJobSummary[]>`（`status` 为 `QUEUED`/`RUNNING`）、`consoleActions: ComputedRef<ConsoleAction[]>`
  - `ConsoleAction { key: string; kind: "pending_acceptance" | "running_job"; label: string; to: string }`
  - **pin I9 语义**：`listUpdateJobs` 抛 `ApiError` 且 `status === 503` 时**不是错误**——置 `operationsEnabled = false`、jobs 为空；其余失败走 `toDisplayError` 进 `error`。

- [ ] **Step 1: 写失败测试**

```ts
// web/tests/console-store.spec.ts
import { describe, expect, it } from "vitest";
import { ApiError } from "../src/api/client";
import type { ApiClient } from "../src/api/client";
import {
  activeJobs,
  consoleActions,
  consoleState,
  currentDataset,
  loadConsoleData,
  pendingConfirmations,
} from "../src/stores/console";
import {
  datasetListResponse,
  experimentsResponse,
  fakeClient,
  updateJobSummary,
} from "./helpers";

function resetState() {
  consoleState.datasets = [];
  consoleState.current = null;
  consoleState.experiments = [];
  consoleState.jobs = [];
  consoleState.operationsEnabled = true;
  consoleState.loaded = false;
  consoleState.error = null;
}

function listWithPending(): ReturnType<typeof datasetListResponse> {
  const response = datasetListResponse();
  response.datasets[1].acceptance = {
    state: "PENDING_CONFIRMATION",
    has_valid_accepted_record: false,
    latest_verdict: "PENDING_CONFIRMATION",
    record_count: 0,
  };
  return response;
}

describe("决策台数据组装（stores/console.ts）", () => {
  it("加载三源并派生：CURRENT 版本、待确认验收、活跃任务、行动列表", async () => {
    resetState();
    const client: ApiClient = fakeClient({
      listDatasets: async () => listWithPending(),
      listExperiments: async () => experimentsResponse(),
      listUpdateJobs: async () => ({ jobs: [updateJobSummary({ job_id: "job-0001", status: "RUNNING" })] }),
    });
    await loadConsoleData(client);
    expect(consoleState.loaded).toBe(true);
    expect(currentDataset.value?.dataset_version).toBe("a".repeat(64));
    expect(pendingConfirmations.value.map((d) => d.dataset_version)).toEqual(["b".repeat(64)]);
    expect(activeJobs.value.map((j) => j.job_id)).toEqual(["job-0001"]);
    expect(consoleActions.value).toEqual([
      {
        key: `pending-${"b".repeat(64)}`,
        kind: "pending_acceptance",
        label: `验收待确认：${"b".repeat(64).slice(0, 8)}`,
        to: `/versions/${"b".repeat(64)}`,
      },
      { key: "job-job-0001", kind: "running_job", label: "更新任务运行中：job-0001", to: "/jobs" },
    ]);
  });

  it("pin I9：操作面 503 是正常态——置 operationsEnabled=false，不产生错误", async () => {
    resetState();
    const client: ApiClient = fakeClient({
      listDatasets: async () => datasetListResponse(),
      listExperiments: async () => experimentsResponse(),
      listUpdateJobs: async () => {
        throw new ApiError(503, "operations_disabled", "");
      },
    });
    await loadConsoleData(client);
    expect(consoleState.error).toBeNull();
    expect(consoleState.operationsEnabled).toBe(false);
    expect(consoleState.loaded).toBe(true);
    expect(activeJobs.value).toEqual([]);
  });

  it("数据源失败走 toDisplayError（稳定 code），loaded 不置位", async () => {
    resetState();
    const client: ApiClient = fakeClient({
      listDatasets: async () => {
        throw new ApiError(500, "internal_error", "boom");
      },
      listExperiments: async () => experimentsResponse(),
      listUpdateJobs: async () => ({ jobs: [] }),
    });
    await loadConsoleData(client);
    expect(consoleState.error?.code).toBe("internal_error");
    expect(consoleState.loaded).toBe(false);
  });

  it("无待办：两个派生列表为空，行动列表为空", async () => {
    resetState();
    const client: ApiClient = fakeClient({
      listDatasets: async () => datasetListResponse(),
      listExperiments: async () => experimentsResponse(),
      listUpdateJobs: async () => ({ jobs: [updateJobSummary({ status: "SUCCEEDED" })] }),
    });
    await loadConsoleData(client);
    expect(pendingConfirmations.value).toEqual([]);
    expect(activeJobs.value).toEqual([]);
    expect(consoleActions.value).toEqual([]);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npm test -- console-store.spec`
Expected: FAIL——store 文件不存在。

- [ ] **Step 3: 实现 store**

```ts
// web/src/stores/console.ts
import { computed, reactive, type ComputedRef } from "vue";
import { ApiError, type ApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import type { DatasetSummary, ExperimentSummary, UpdateJobSummary } from "../api/types";

/** 决策台行动项：每条必有跳转（v3 规格 §5.1 ③）。 */
export interface ConsoleAction {
  key: string;
  kind: "pending_acceptance" | "running_job";
  label: string;
  to: string;
}

export interface ConsoleState {
  datasets: DatasetSummary[];
  current: string | null;
  experiments: ExperimentSummary[];
  jobs: UpdateJobSummary[];
  /** pin I9：503 = 操作面默认禁用，是正常态不是错误。 */
  operationsEnabled: boolean;
  loaded: boolean;
  error: DisplayError | null;
}

export const consoleState = reactive<ConsoleState>({
  datasets: [],
  current: null,
  experiments: [],
  jobs: [],
  operationsEnabled: true,
  loaded: false,
  error: null,
});

export const currentDataset: ComputedRef<DatasetSummary | null> = computed(
  () => consoleState.datasets.find((dataset) => dataset.is_current) ?? null,
);

export const pendingConfirmations: ComputedRef<DatasetSummary[]> = computed(() =>
  consoleState.datasets.filter(
    (dataset) => dataset.acceptance.state === "PENDING_CONFIRMATION",
  ),
);

export const activeJobs: ComputedRef<UpdateJobSummary[]> = computed(() =>
  consoleState.jobs.filter(
    (job) => job.status === "QUEUED" || job.status === "RUNNING",
  ),
);

export const consoleActions: ComputedRef<ConsoleAction[]> = computed(() => [
  ...pendingConfirmations.value.map((dataset) => ({
    key: `pending-${dataset.dataset_version}`,
    kind: "pending_acceptance" as const,
    label: `验收待确认：${dataset.dataset_version.slice(0, 8)}`,
    to: `/versions/${dataset.dataset_version}`,
  })),
  ...activeJobs.value.map((job) => ({
    key: `job-${job.job_id}`,
    kind: "running_job" as const,
    label: `更新任务运行中：${job.job_id}`,
    to: "/jobs",
  })),
]);

export async function loadConsoleData(client: ApiClient): Promise<void> {
  consoleState.error = null;
  try {
    const [datasets, experiments] = await Promise.all([
      client.listDatasets(),
      client.listExperiments(),
    ]);
    consoleState.datasets = datasets.datasets;
    consoleState.current = datasets.current;
    consoleState.experiments = experiments.experiments;
    // 操作面独立容忍：503 = 默认禁用（pin I9），其余失败照常走错误通道。
    consoleState.operationsEnabled = true;
    try {
      consoleState.jobs = (await client.listUpdateJobs()).jobs;
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 503) {
        consoleState.operationsEnabled = false;
        consoleState.jobs = [];
      } else {
        throw cause;
      }
    }
    consoleState.loaded = true;
  } catch (cause) {
    consoleState.error = toDisplayError(cause);
  }
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `npm test -- console-store.spec`
Expected: PASS（4 用例全绿）。

- [ ] **Step 5: 提交**

```bash
git add web/src/stores/console.ts web/tests/console-store.spec.ts
git commit -m "feat(web): add decision-console data store with 503 tolerance

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 8: DecisionConsolePage（块① + 块②空态/计数 + 块③）与 `/` 路由

**Files:**
- Create: `web/src/pages/DecisionConsolePage.vue`
- Modify: `web/src/router.ts`（`/` 由 redirect 改为决策台；NAV_GROUPS 头部加"概览"独立项）
- Modify: `web/tests/app-shell.spec.ts`（扁平顺序期望随导航头加"决策台"自动来自 `NAV_ITEMS`——断言无需改值，因为断言读的就是 `NAV_ITEMS`；但要加一条 `/` 路由断言）
- Modify: `web/e2e/portal.spec.ts`（新增决策台用例）
- Test: `web/tests/decision-console-page.spec.ts`

**Interfaces:**
- Consumes: Task 2–4 组件、Task 7 store、既有 `blockingIssueCount`/`totalIssueCount`（api/quality.ts）、`hasNewerCurrent`（stores/version.ts）。
- Produces: 路由 `{ path: "/", name: "console", component: DecisionConsolePage }`；页面 testid：`block-data-trust`、`block-strategy`、`block-strategy-empty`、`block-strategy-count`、`block-actions`、`action-item`、`console-error`、`console-loading`。

- [ ] **Step 1: 写失败测试**

```ts
// web/tests/decision-console-page.spec.ts
import { describe, expect, it } from "vitest";
import { flushPromises } from "@vue/test-utils";
import DecisionConsolePage from "../src/pages/DecisionConsolePage.vue";
import { consoleState } from "../src/stores/console";
import { versionPinState } from "../src/stores/version";
import { ApiError, type ApiClient } from "../src/api/client";
import {
  datasetListResponse,
  experimentsResponse,
  fakeClient,
  mountAt,
  updateJobSummary,
} from "./helpers";

function resetState() {
  consoleState.datasets = [];
  consoleState.current = null;
  consoleState.experiments = [];
  consoleState.jobs = [];
  consoleState.operationsEnabled = true;
  consoleState.loaded = false;
  consoleState.error = null;
  versionPinState.resolvedVersion = null;
  versionPinState.currentVersion = null;
}

describe("决策台（v3 规格 §5.1：三问首屏）", () => {
  it("块①：CURRENT 版本卡——门禁措辞沿用 P5 语义 + 验收徽章 + 链接", async () => {
    resetState();
    const client: ApiClient = fakeClient({
      listDatasets: async () => datasetListResponse(),
      listExperiments: async () => ({ experiments: [] }),
      listUpdateJobs: async () => ({ jobs: [] }),
    });
    const wrapper = await mountAt(DecisionConsolePage, client, "/");
    await flushPromises();
    const block = wrapper.get('[data-testid="block-data-trust"]');
    expect(block.text()).toContain("门禁通过");
    expect(block.text()).toContain("质量问题 0 条");
    expect(block.text()).toContain("有效 accepted record：是");
    expect(block.get('[data-testid="state-badge"]').text()).toBe("ACCEPTED");
    expect(block.get('a[href*="/versions/"]').exists()).toBe(true);
  });

  it("块②：注册表为空 → 诚实空态与到达路径（不渲染任何指标）", async () => {
    resetState();
    const client: ApiClient = fakeClient({
      listDatasets: async () => datasetListResponse(),
      listExperiments: async () => ({ experiments: [] }),
      listUpdateJobs: async () => ({ jobs: [] }),
    });
    const wrapper = await mountAt(DecisionConsolePage, client, "/");
    await flushPromises();
    const block = wrapper.get('[data-testid="block-strategy"]');
    expect(block.get('[data-testid="block-strategy-empty"]').text()).toContain("尚无已发布实验");
    expect(block.text()).toContain("research run");
    expect(block.find(".kpi").exists()).toBe(false);
  });

  it("块②：注册表非空 → 只报计数一句话（结论卡属 S2②，不提前渲染）", async () => {
    resetState();
    const client: ApiClient = fakeClient({
      listDatasets: async () => datasetListResponse(),
      listExperiments: async () => experimentsResponse(),
      listUpdateJobs: async () => ({ jobs: [] }),
    });
    const wrapper = await mountAt(DecisionConsolePage, client, "/");
    await flushPromises();
    expect(wrapper.get('[data-testid="block-strategy-count"]').text()).toContain("已发布实验 2 个");
  });

  it("块③：待办列表（验收待确认 + 运行中任务），每条带跳转；无待办显'无待办'", async () => {
    resetState();
    const pending = datasetListResponse();
    pending.datasets[1].acceptance = {
      state: "PENDING_CONFIRMATION",
      has_valid_accepted_record: false,
      latest_verdict: "PENDING_CONFIRMATION",
      record_count: 0,
    };
    const client: ApiClient = fakeClient({
      listDatasets: async () => pending,
      listExperiments: async () => ({ experiments: [] }),
      listUpdateJobs: async () => ({ jobs: [updateJobSummary({ job_id: "job-0001", status: "RUNNING" })] }),
    });
    const wrapper = await mountAt(DecisionConsolePage, client, "/");
    await flushPromises();
    const actions = wrapper.findAll('[data-testid="action-item"]');
    expect(actions).toHaveLength(2);
    expect(actions[0].text()).toContain("验收待确认");
    expect(actions[0].get("a").attributes("href")).toBe(`#/versions/${"b".repeat(64)}`);
    expect(actions[1].text()).toContain("job-0001");

    resetState();
    const quietClient: ApiClient = fakeClient({
      listDatasets: async () => datasetListResponse(),
      listExperiments: async () => ({ experiments: [] }),
      listUpdateJobs: async () => ({ jobs: [] }),
    });
    const quiet = await mountAt(DecisionConsolePage, quietClient, "/");
    await flushPromises();
    expect(quiet.get('[data-testid="block-actions"]').text()).toContain("无待办");
  });

  it("pin I9：操作面禁用时块③给出真实说明而非错误", async () => {
    resetState();
    const client: ApiClient = fakeClient({
      listDatasets: async () => datasetListResponse(),
      listExperiments: async () => ({ experiments: [] }),
      listUpdateJobs: async () => {
        throw new ApiError(503, "operations_disabled", "");
      },
    });
    const wrapper = await mountAt(DecisionConsolePage, client, "/");
    await flushPromises();
    const block = wrapper.get('[data-testid="block-actions"]');
    expect(block.text()).toContain("操作面未启用");
    expect(block.text()).toContain("无待办");
  });

  it("加载中骨架与失败错误码（唯一错误通道 toDisplayError）", async () => {
    resetState();
    const slow: ApiClient = fakeClient({
      listDatasets: () => new Promise(() => {}),
      listExperiments: () => new Promise(() => {}),
      listUpdateJobs: () => new Promise(() => {}),
    });
    const loading = await mountAt(DecisionConsolePage, slow, "/");
    expect(loading.get('[data-testid="console-loading"]').exists()).toBe(true);

    resetState();
    const failing: ApiClient = fakeClient({
      listDatasets: async () => {
        throw new ApiError(500, "internal_error", "");
      },
      listExperiments: async () => ({ experiments: [] }),
      listUpdateJobs: async () => ({ jobs: [] }),
    });
    const errored = await mountAt(DecisionConsolePage, failing, "/");
    await flushPromises();
    expect(errored.get('[data-testid="console-error"]').text()).toContain("internal_error");
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npm test -- decision-console-page.spec`
Expected: FAIL——页面组件不存在。

- [ ] **Step 3: 实现页面与路由**

`web/src/router.ts`：import 加 `DecisionConsolePage`；`routes` 的 `{ path: "/", redirect: "/versions" }` 替换为 `{ path: "/", name: "console", component: DecisionConsolePage }`；`NAV_GROUPS` 头部插入独立组：

```ts
export const NAV_GROUPS: readonly NavGroup[] = [
  {
    label: null,
    items: [{ label: "决策台", path: "/" }],
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
  {
    label: null,
    items: [{ label: "报告", path: "/reports" }],
  },
];
```

`web/src/pages/DecisionConsolePage.vue`：

```vue
<script setup lang="ts">
import { onMounted } from "vue";
import { useApiClient } from "../api/client";
import { blockingIssueCount, totalIssueCount } from "../api/quality";
import { hasNewerCurrent } from "../stores/version";
import {
  activeJobs,
  consoleActions,
  consoleState,
  currentDataset,
  loadConsoleData,
  pendingConfirmations,
} from "../stores/console";
import Card from "../components/Card.vue";
import EmptyState from "../components/EmptyState.vue";
import Skeleton from "../components/Skeleton.vue";
import StateBadge from "../components/StateBadge.vue";

const client = useApiClient();
onMounted(() => {
  void loadConsoleData(client);
});
</script>

<template>
  <section>
    <h1>决策台</h1>

    <p
      v-if="consoleState.error !== null"
      class="error"
      data-testid="console-error"
    >
      错误 {{ consoleState.error.code }}：{{ consoleState.error.message === "" ? "无安全摘要" : consoleState.error.message }}
    </p>
    <div v-else-if="!consoleState.loaded" data-testid="console-loading">
      <Skeleton :rows="6" />
    </div>

    <template v-else>
      <Card title="① 数据现在可信吗？" testid="block-data-trust">
        <template v-if="currentDataset !== null">
          <p>
            <StateBadge
              kind="acceptance"
              :value="currentDataset.acceptance.state"
              title="验收四态原文；门禁通过与阻断读 acceptance.state（P5 语义）"
            />
            <span v-if="currentDataset.acceptance.state === 'ACCEPTED'">门禁通过</span>
            <span v-else class="warn">
              门禁阻断（{{ blockingIssueCount(currentDataset.quality) }} 项）
            </span>
            <span>；质量问题 {{ totalIssueCount(currentDataset.quality) }} 条</span>
          </p>
          <p>
            创建时间：{{ currentDataset.created_at ?? "—" }}；
            有效 accepted record：{{ currentDataset.acceptance.has_valid_accepted_record ? "是" : "否" }}
          </p>
          <p v-if="hasNewerCurrent" class="hint">
            CURRENT 指针已前移（顶栏提示不自动切换）
          </p>
          <RouterLink :to="`/versions/${currentDataset.dataset_version}`">
            查看版本详情 <code>{{ currentDataset.dataset_version.slice(0, 8) }}</code>
          </RouterLink>
        </template>
        <EmptyState
          v-else
          title="尚无 CURRENT 数据集版本"
          description="数据尚未发布或 CURRENT 指针为空。"
        />
      </Card>

      <Card title="② 策略结论是什么？" testid="block-strategy">
        <div v-if="consoleState.experiments.length === 0" data-testid="block-strategy-empty">
          <EmptyState
            title="尚无已发布实验"
            description="data/experiments 为空；经 CLI 发起一次 research run 并发布实验后，结论将在此展示。"
            action-label="查看报告页"
            action-to="/reports"
          />
        </div>
        <p v-else data-testid="block-strategy-count">
          已发布实验 {{ consoleState.experiments.length }} 个；策略结论卡与策略列表页随策略层（S1）上线。
        </p>
      </Card>

      <Card title="③ 需要我做什么？" testid="block-actions">
        <ul v-if="consoleActions.length > 0" class="action-list">
          <li v-for="action in consoleActions" :key="action.key" data-testid="action-item">
            <RouterLink :to="action.to">{{ action.label }}</RouterLink>
          </li>
        </ul>
        <template v-else>
          <p v-if="!consoleState.operationsEnabled" class="hint">
            操作面未启用（默认禁用），任务状态不可查；等价 CLI 见更新任务页。
          </p>
          <EmptyState title="无待办" />
        </template>
        <p v-if="pendingConfirmations.length > 0" class="hint">
          验收确认不在 web 内执行：按 RUNBOOK 的 acceptance confirm 流程处理。
        </p>
      </Card>
    </template>
  </section>
</template>
```

（块②空态的 `block-strategy-empty` testid 落在包裹 `div` 上，满足 Step 1 测试选择器。）

`web/src/styles.css` 末尾追加：

```css
/* 决策台（Task 8） */
.action-list { margin: 0; padding-left: 20px; }
.action-list li { margin: var(--space-2) 0; }
```

`web/tests/app-shell.spec.ts` 第一个用例后追加一个用例（同一 describe 内）：

```ts
  it("根路由渲染决策台，导航第一项为决策台", async () => {
    const wrapper = mount(App, {
      props: { client: fakeClient() },
      global: { plugins: [createPortalRouter()] },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(wrapper.get('[data-testid="main-nav"]').find("a").attributes("href")).toBe("#/");
  });
```

（该用例只断言导航第一项指向 `/`；决策台内容断言在 decision-console-page.spec。）

- [ ] **Step 4: 运行测试确认通过**

Run: `npm test -- decision-console-page.spec app-shell.spec`
Expected: PASS。

- [ ] **Step 5: 更新 e2e 并运行**

在 `web/e2e/portal.spec.ts` 的测试区末尾追加（复用既有 `state`/`seedJob`/`installMockBackend`；mock 事实见"开工前"第 3 条：HASH_A 为 ACCEPTED+FATAL:1 → "门禁通过；质量问题 1 条"；experiments 恒 2 条）：

```ts
test("决策台：三块首屏（门禁措辞/实验计数/待办跳转）", async ({ page }) => {
  const mock = state({ jobs: [seedJob("job-0001", "RUNNING")] });
  await installMockBackend(page, mock);
  await page.goto("/");
  await expect(page.getByTestId("block-data-trust")).toContainText("门禁通过");
  await expect(page.getByTestId("block-data-trust")).toContainText("质量问题 1 条");
  await expect(page.getByTestId("block-strategy-count")).toContainText("已发布实验 2 个");
  await expect(page.getByTestId("block-actions")).toContainText("job-0001");
  await expect(page.getByTestId("main-nav").locator("a").first()).toHaveAttribute("href", "#/");
});
```

Run: `npx playwright test`
Expected: 全部既有用例 + 新用例 PASS（若有既有用例因导航 DOM 变化失败，只修该用例对侧边栏结构的断言，不改被测行为）。

- [ ] **Step 6: 提交**

```bash
git add web/src/pages/DecisionConsolePage.vue web/src/router.ts web/src/styles.css web/tests/decision-console-page.spec.ts web/tests/app-shell.spec.ts web/e2e/portal.spec.ts
git commit -m "feat(web): add decision console page with trust, strategy, and action blocks

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 9: 批次收口（验收勾稽 + 全量回归）

**Files:**
- Modify: 无新文件（只跑验证；如勾稽发现偏差，修对应任务文件后重跑）

**Interfaces:**
- Consumes: Task 1–8 全部产物。
- Produces: 批次验收结论（对应规格 §8 的 S0a 与 S2①③ 两行验收标准）。

- [ ] **Step 1: 全量测试与类型检查**

Run: `npm test && npm run typecheck`
Expected: 全部 PASS、零类型错误。

- [ ] **Step 2: e2e 全量**

Run: `npx playwright test`
Expected: 全部 PASS。

- [ ] **Step 3: 零依赖与零契约勾稽**

Run（在仓库根）:

```bash
git diff --stat HEAD~8 -- web/package.json web/package-lock.json web/src/api
```

Expected: **空输出**（dependencies/devDependencies、lock、api 契约目录零改动）。

（`HEAD~8` 而非 `HEAD~9`：Task 1–8 各一个提交、Task 9 本身只跑验证（Step 6 仅在勾稽产生修正时才提交），故开工前的最后一个提交是 `HEAD~8`。更稳的写法是把开工时的 `git rev-parse HEAD` 记下来，用 `git diff --stat <开工提交> -- …` 锚定；不要猜 `HEAD~N`。）

- [ ] **Step 4: testid 保留勾稽**

Run（在 `web/` 下）:

```bash
grep -Rho 'data-testid="[a-z-]*"' src tests e2e | sort -u
```

Expected: 开工前存在的 testid 全部仍在（`main-nav`、`project-fingerprint`、`top-resolved-version`、`copy-version`、`current-badge`、`refresh-current`、`new-version-hint`、五个页面各自的 testid）；新增的只有本批次的（`state-badge`、`empty-state`、`empty-state-action`、`skeleton`、`data-table`、`data-table-empty`、`card`、`kpi-card`、`block-*`、`action-item`、`console-error`、`console-loading`）。

- [ ] **Step 5: 规格验收对照（S0a + S2①③ 两行）**

- S0a：tokens ✅（Task 1）；DataTable/StateBadge/EmptyState/KpiCard ✅（Task 2–5）；布局壳 ✅（Task 6）；无端点/契约变更 ✅（Step 3）；无新增运行时依赖 ✅（Step 3）。
- S2①③：块①数据可信卡 ✅、块③行动列表 ✅（Task 8），空态可测 ✅、行动项每条有跳转 ✅、不出现 web 内验收操作 ✅（只有 RUNBOOK 指引文案）。
- 块②空态/计数为计划层消解（见"规格审定记录"第 4 条），S2② 结论卡**不在本批次**。

- [ ] **Step 6: 最终提交（如勾稽产生修正）**

```bash
git add -A web
git commit -m "chore(web): close out decision-layer first batch verification

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

（无修正则跳过本步，不造空提交。）

---

## Self-Review 记录（2026-10-03）

**1. 规格覆盖（v3 规格 → 本计划任务）**

| 规格条目 | 任务 |
| --- | --- |
| §6.1 token 层（light 默认 + dark 预留） | Task 1 |
| §6.2 组件清单中的 S0a 四件 + Card/Skeleton | Task 2–5 |
| §4 侧边栏分组导航（策略组不渲染直到 S1；报告保守保留） | Task 6、8 |
| §5.1 块①（数据可信卡） | Task 8 |
| §5.1 块③（行动列表，503 容忍，无 web 内验收操作） | Task 7、8 |
| §5.1 块②（S2② 结论卡不提前；空态/计数消解见审定记录） | Task 8 |
| §8 S0a/S2①③ 验收标准 | Task 9 Step 3–5 |
| 不在本批次：S1（端点/策略页/`/reports` 下线/ECharts）、S0b 五页换皮、S2②、挑战区块、S3a、S4 | 见下方触发点 |

**2. 占位符扫描**：全文无 TBD/TODO/"类似 Task N"/只有描述没有代码的步骤；每个代码步骤都给出完整代码与运行期望。

**3. 类型一致性**：`BadgeKind`/`BadgeTone`（Task 2 导出，Task 8 消费 `kind: "acceptance"`）；`KpiTone`（Task 3）；`DataTableColumn`（Task 5）；`ConsoleState`/`ConsoleAction`/`loadConsoleData`（Task 7 导出，Task 8 消费同名）；`NavGroup`/`NavItem`/`NAV_GROUPS`/`NAV_ITEMS`（Task 6 导出，Task 8 追加组）。已逐任务核对无漂移。

**4. 已知边界**：vitest 断言 `aria-sort` 为 `undefined` 用 `attributes("aria-sort")` 返回 undefined——@vue/test-utils 对未设置属性返回 undefined，`toBeUndefined()` 成立；若版本行为差异导致失败，改为 `.toBe("")` 并在提交信息注明。

## 后续计划触发点（计划集已全部生成，2026-10-03）

1. **rights-issue 实施计划**（第一优先，与本计划并行）：[2026-10-03-rights-issue-booking-implementation.md](2026-10-03-rights-issue-booking-implementation.md)。它是 S1 硬门的前半（首个正式实验发布的解锁项）。
2. **S1 策略层计划**：[2026-10-03-web-decision-layer-s1-strategy.md](2026-10-03-web-decision-layer-s1-strategy.md)。触发条件 = rights-issue 落地**且**首个正式实验发布（其 Task 1 Step 1 实例对账闭合"契约 ≠ 实例"缺口）；四端点 + 策略列表/详情 + ECharts + `/reports` 下线。
3. **S0b 五页换皮计划**：[2026-10-03-web-decision-layer-s0b-reskin.md](2026-10-03-web-decision-layer-s0b-reskin.md)。本批次后任意时点，机械迁移，testid 不删。
4. **S2② 决策台后半计划**：[2026-10-03-web-decision-layer-s2-console-conclusion.md](2026-10-03-web-decision-layer-s2-console-conclusion.md)。随 S1 之后；块②升级为结论卡。
5. **S3a 注册台计划**：[2026-10-03-web-decision-layer-s3a-register.md](2026-10-03-web-decision-layer-s3a-register.md)。S1 后；纯前端命令/YAML 生成器（裁定 2：无任何写面）。
6. **未出计划的两项（有意）**：挑战裁决区块——需先立设计（消费端点 + holdout 消费状态展示语义，brainstorm → spec → plan 流程，不得跳步）；S4 股票/universe——owner 裁定 1 缓做，待真实使用诉求另立设计小节。
