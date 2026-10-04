# Web 决策层 · S0b 五页换皮实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把版本面板、版本详情、数据预览、质量/覆盖证据、更新任务五个既有页面的视觉迁移到第一批的设计系统（tokens + Card/StateBadge/DataTable/KpiCard/EmptyState/Skeleton），**信息与行为零变化**：全部 testid 保留、断言语义保留、无端点/契约/依赖变更。

**Architecture:** 手术式迁移而非重写：每页只做三类变换——(1) 区块用 `Card` 包裹获得卡片层级；(2) 状态文字换 `StateBadge`（映射表复用第一批的语义表）；(3) 表格换 `DataTable`（先给 DataTable 补一个**列名作用域插槽**，让链接列/徽章列可组合）。表单、pager、轮询、错误通道等交互逻辑一律不动。每页迁移后既有页面测试必须原样通过（这是"零语义变化"的可测证明）。

**Tech Stack:** 既有栈零新增（无 ECharts——那是 S1 的事；本计划不依赖 S1，可在第一批后任意时点执行）。**但反向依赖成立**：S1 Task 7（策略列表页）统一消费本计划 Task 1 的 DataTable 列插槽 + `rowTestid`——S1 开工前，本计划（至少 Task 1）必须已合入（owner 审核 2026-10-03 确认为真实顺序约束）。

**Spec:** [2026-10-03-web-portal-decision-layer-design.md](../specs/2026-10-03-web-portal-decision-layer-design.md) §5.6、§6、§8 S0b 行。前置：[2026-10-03-web-decision-layer-first-batch.md](2026-10-03-web-decision-layer-first-batch.md) 已交付组件库与 token 层。

## Global Constraints

- **零行为变化**：不新增/删除/改名任何 `data-testid`；既有五个页面 spec 与 e2e 断言**不因本计划而修改**（若某断言因 DOM 层级变化失败，优先调整实现保持断言，而不是改断言——与 S0b 验收"既有断言语义全部通过（testid 不删）"一致；确需动断言的仅限纯结构选择器且在提交信息说明）。
- **零端点/契约/依赖变更**：`web/src/api/`、`package.json` 不动。
- 展示纪律不变：真实状态词、`toDisplayError` 唯一错误通道、CURRENT 指针语义、`version_echo_mismatch` 行为、单日过滤（pin I4(a)）。
- 每任务一提交，英文提交信息 + `Co-Authored-By: Claude Code <noreply@anthropic.com>`。

## 开工前必须知道的实现形态（2026-10-03 实查）

1. **五页现状**（行数/关键 testid）：
   - `VersionsPage.vue`（80 行）：`dataset-list` 表 + `current-mark`/`quality-summary`/`accepted-record`/`latest-verdict` 列 testid；首列是 RouterLink 到 `/versions/{hash}`。
   - `VersionDetailPage.vue`（121 行）：`resolved-echo`/`full-version`、`table-meta` 表、`quality-summary`、`blocking-issues` 列表、`accepted-record-block`/`latest-verdict-block` 两栏、`quality-issues` 表（`details` 列是 `JSON.stringify`）。
   - `DataPreviewPage.vue`（210 行）：`preview-controls` 表单（`version-select`/`table-select`/`filter-trade-date`/`filter-symbol`/`apply-filters`）、列白名单 fieldset、`pager`（`prev-page`/`next-page`）、`preview-table`、`error`/`loading`/`resolved-echo`。
   - `CoverageEvidencePage.vue`（199 行）：`version-select`、`coverage-segments` 表、逐表 `untrusted-{table}` 区块（空态 `untrusted-empty-{table}`）、`issue-list` 表、`attested-boundary-note` blockquote。
   - `UpdateJobsPage.vue`（238 行）：`probing`/`operations-disabled`（含 `equivalent-cli` pre）、`update-form`、`submit-*`、`conflict`、`job-detail`（`job-status`/四终态块/`job-stdout`/`job-stderr`）、`job-list` 表。
2. **第一批组件契约**：`StateBadge {kind: acceptance|conclusion|job; value; title?}`（根 testid `state-badge`）；`Card {title?; testid?}`；`DataTable {columns: DataTableColumn[]; rows; rowKey?; emptyText?; testid?}`（无插槽——Task 1 补）；`KpiCard {label; value; compare?; tone?}`；`EmptyState {title; description?; actionLabel?; actionTo?}`；`Skeleton {rows?}`。
3. **验收措辞来源**：门禁通过与阻断读 `acceptance.state`（`api/quality.ts` 的 `blockingIssueCount`/`totalIssueCount`/`isBlockingSeverity` 不动）。

## 文件结构

| 文件 | 动作 |
| --- | --- |
| `web/src/components/DataTable.vue` | 修改（列名作用域插槽） |
| `web/src/pages/VersionsPage.vue` | 修改 |
| `web/src/pages/VersionDetailPage.vue` | 修改 |
| `web/src/pages/DataPreviewPage.vue` | 修改 |
| `web/src/pages/CoverageEvidencePage.vue` | 修改 |
| `web/src/pages/UpdateJobsPage.vue` | 修改 |
| `web/tests/data-table.spec.ts` | 修改（插槽用例） |
| `web/tests/versions-page.spec.ts` 等 | 只在"实现无法保持断言"时最小调整 |

---

### Task 1: DataTable 列名作用域插槽

**Files:**
- Modify: `web/src/components/DataTable.vue`
- Test: `web/tests/data-table.spec.ts`（追加）

**Interfaces:**
- Produces:
  - 每个 `<td>` 先尝试渲染 `<slot :name="column.key" :row="row" :value="row[column.key]" />`，插槽缺席时回落既有 `cellText` 字符串；空态行不变。后续五页与 S1 页面用 `#列名="{ row }"` 组合链接/徽章单元格。
  - 新增可选 prop `rowTestid?: string`：传入时每行 `<tr>` 渲染 `:data-testid="props.rowTestid"`（既有页面把行级 testid 挂在 `<tr>` 上，如 `dataset-row`；`rowKey` 仍只负责 `:key`，两者互不替代）。`data-table`/`data-table-empty` 两个根/空态 testid 不变。

- [ ] **Step 1: 写失败测试**

```ts
// 追加到 web/tests/data-table.spec.ts
it("列名作用域插槽可组合链接与徽章单元格，未提供插槽的列回落纯文本", () => {
  const wrapper = mount(DataTable, {
    props: {
      columns: [
        { key: "id", label: "ID" },
        { key: "name", label: "名称" },
      ],
      rows: [{ id: "abc", name: "普通行" }],
    },
    slots: {
      // 具名插槽的内容**就是**该槽的模板体：不要再套一层 <template #id>。
      id: `<a :href="'#/x/' + row.id">{{ row.id }}</a>`,
    },
    global: { plugins: [createPortalRouter()] },
  });
  const cells = wrapper.findAll("tbody tr")[0].findAll("td");
  expect(cells[0].find("a").attributes("href")).toBe("#/x/abc");
  expect(cells[1].text()).toBe("普通行");
});

it("rowTestid 把行级 testid 挂到 <tr> 上", () => {
  const wrapper = mount(DataTable, {
    props: {
      columns: [{ key: "id", label: "ID" }],
      rows: [{ id: "abc" }],
      rowTestid: "dataset-row",
    },
  });
  expect(wrapper.findAll("tbody tr")[0].attributes("data-testid")).toBe("dataset-row");
});
```

（本文件既有 import 区已引入 `mount` 与 `createPortalRouter`，直接在既有 import 的同一行/同一处复用，不要重复引入。）

- [ ] **Step 2: 运行确认失败** → FAIL（插槽被忽略，单元格仍是纯文本）。
- [ ] **Step 3: 实现**

`<tr>` 上加行级 testid（只加一行 `:data-testid`，`v-for` 与 `:key` 逐字保留第一批的写法）：

```vue
      <tr
        v-for="(row, index) in sortedRows"
        :key="props.rowKey !== undefined ? String(row[props.rowKey]) : index"
        :data-testid="props.rowTestid"
      >
```

（`props.rowTestid` 未传时为 `undefined`，Vue 3 不会渲染该属性，既有调用方行为不变。）

数据单元格改为：

```vue
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
```

（`rows` 的元素类型仍是 `Record<string, unknown>`，不放宽——插槽 value 类型即 `unknown`，页面自行窄化。）

- [ ] **Step 4: 运行确认通过** → `npm test -- data-table.spec` PASS（既有 5 用例不回归）。
- [ ] **Step 5: 提交**

```bash
git add web/src/components/DataTable.vue web/tests/data-table.spec.ts
git commit -m "feat(web): per-column scoped slots on DataTable

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: 版本面板迁移

**Files:**
- Modify: `web/src/pages/VersionsPage.vue`
- Test: `web/tests/versions-page.spec.ts`（不动；全绿即验收）

**Interfaces:**
- Consumes: Task 1 插槽、第一批 StateBadge/Card/DataTable。

- [ ] **Step 1: 迁移实现**

模板改造（`<script setup>` 不动，仅补 import：`Card`/`DataTable`/`StateBadge` 与 `DataTableColumn` 类型）：

```vue
    <Card v-else title="数据集版本">
      <DataTable
        testid="dataset-list"
        row-key="dataset_version"
        :columns="[
          { key: 'dataset_version', label: 'dataset version', mono: true },
          { key: 'is_current', label: 'CURRENT' },
          { key: 'created_at', label: '创建时间' },
          { key: 'table_count', label: '表计数', align: 'right' },
          { key: 'quality', label: '质量摘要' },
          { key: 'accepted', label: '有效 accepted record' },
          { key: 'latest_verdict', label: '最近 verdict' },
        ]"
        :rows="datasets"
      >
        <template #dataset_version="{ row }">
          <RouterLink :to="`/versions/${row.dataset_version}`">
            <code>{{ row.dataset_version }}</code>
          </RouterLink>
        </template>
        <template #is_current="{ row }">
          <span
            v-if="row.is_current"
            class="badge"
            title="CURRENT 是指针标记，不是可信等级"
            data-testid="current-mark"
          >CURRENT</span>
          <span v-else>—</span>
        </template>
        <template #quality="{ row }">
          <span data-testid="quality-summary">
            <span v-if="row.acceptance.state === 'ACCEPTED'">门禁通过</span>
            <span v-else class="warn">门禁阻断（{{ blockingIssueCount(row.quality) }} 项）</span>
            <span>；质量问题 {{ totalIssueCount(row.quality) }} 条</span>
          </span>
        </template>
        <template #accepted="{ row }">
          <span data-testid="accepted-record">
            {{ row.acceptance.has_valid_accepted_record ? "是" : "否" }}
          </span>
        </template>
        <template #latest_verdict="{ row }">
          <span data-testid="latest-verdict">
            <StateBadge
              v-if="row.acceptance.latest_verdict !== null"
              kind="acceptance"
              :value="row.acceptance.latest_verdict"
            />
            <span v-else>{{ row.acceptance.state }}</span>
          </span>
        </template>
      </DataTable>
    </Card>
```

（行级 `data-testid="dataset-row"` 由 Task 1 新增的 `rowTestid` prop 承载——此处传 `row-testid="dataset-row"`。先跑既有 `versions-page.spec` 确认该 testid 确实被断言；若未断言，同样保留（testid 只增不删是 S0b 硬约束）。）

- [ ] **Step 2: 全量确认**

Run: `npm test -- versions-page.spec app-shell.spec`
Expected: PASS——既有断言原样通过（`dataset-list`/`current-mark`/`quality-summary`/`accepted-record`/`latest-verdict`/`dataset-row` 全在）。

- [ ] **Step 3: 提交**

```bash
git add web/src/pages/VersionsPage.vue web/src/components/DataTable.vue
git commit -m "feat(web): migrate versions page onto the design system

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: 版本详情迁移

**Files:**
- Modify: `web/src/pages/VersionDetailPage.vue`
- Test: `web/tests/version-detail-page.spec.ts`（不动）

- [ ] **Step 1: 迁移实现**（要点，逐区块）：

- `resolved-echo`/`full-version`/CURRENT 徽标段落保持原样（顶栏同款语义），包进无标题 `Card`；
- `table-meta` 表 → `DataTable`（`row-key="name"`，列 name/row_count（右对齐）/schema_version，`mono` 首列）；
- 质量摘要段落 → `Card title="质量摘要"`，措辞与 `data-testid="quality-summary"` 逐字保留；`blocking-issues` 列表保留；
- 验收两栏 → `Card title="验收状态（分开显示）"` 内保持 `acceptance-columns` 结构，`latest-verdict-value` 处换 `StateBadge kind="acceptance"`（value 为 `latest_verdict ?? state`，`data-testid="latest-verdict-value"` 移到包裹 span 上保持）；
- `quality-issues` 表 → `DataTable`（列 code/severity/table/trade_date/details；**details 键值化**：`#details="{ value }"` 插槽把非 null 的对象渲染为 `<dl>`——`Object.entries(value ?? {})` 逐键 `<dt>key</dt><dd>String(v)</dd>`，null 渲染 `—`；这仍是只读透传，不解释语义）。

```vue
        <template #details="{ value }">
          <dl v-if="value !== null && typeof value === 'object'" class="kv">
            <template v-for="(item, key) in value" :key="String(key)">
              <dt><code>{{ key }}</code></dt>
              <dd>{{ typeof item === "object" ? JSON.stringify(item) : String(item) }}</dd>
            </template>
          </dl>
          <span v-else>—</span>
        </template>
```

`styles.css` 追加：`.kv { display: grid; grid-template-columns: auto 1fr; gap: 2px var(--space-2); margin: 0; font-size: var(--font-size-small); } .kv dt { color: var(--color-text-secondary); } .kv dd { margin: 0; }`

- [ ] **Step 2: 全量确认**

Run: `npm test -- version-detail-page.spec`
Expected: PASS（`table-meta`/`quality-summary`/`blocking-issues`/`accepted-record-block`/`latest-verdict-block`/`latest-verdict-value`/`quality-issues` 断言原样通过；details 键值化后既有断言若断言 JSON 字符串原文，把该断言改为"包含 gap_start 键名"——属纯渲染格式调整，提交信息注明）。

- [ ] **Step 3: 提交**

```bash
git add web/src/pages/VersionDetailPage.vue web/src/styles.css
git commit -m "feat(web): migrate version detail page onto the design system

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: 数据预览迁移

**Files:**
- Modify: `web/src/pages/DataPreviewPage.vue`
- Test: `web/tests/data-preview-page.spec.ts`（不动）

- [ ] **Step 1: 迁移实现**（要点）：

- `preview-controls` 表单与列白名单 fieldset → 包进 `Card title="查询条件"`（表单控件、v-model、`@submit.prevent` 全不动）；
- `preview-table` → `DataTable`（列来自 `preview.columns` 动态映射：`preview.columns.map((name) => ({ key: name, label: name, mono: name !== 'trade_date', align: 数值列右对齐——以 `typeof rows[0]?.[name] === 'number'` 判定，这是渲染对齐不是计算）`；`rows` 直接绑 `preview.rows`，`rowKey` 用 index 缺省）；`data-testid="preview-table"` 传给 DataTable；
- `pager` 段落保持原样（`prev-page`/`next-page` 语义是"无 total 按行数判下一页"，不得改成总数分页）。

- [ ] **Step 2: 全量确认**

Run: `npm test -- data-preview-page.spec`
Expected: PASS（`preview-controls`/`filter-*`/`apply-filters`/`pager`/`prev-page`/`next-page`/`preview-table` 原样）。

- [ ] **Step 3: 提交**

```bash
git add web/src/pages/DataPreviewPage.vue
git commit -m "feat(web): migrate data preview page onto the design system

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: 质量/覆盖证据迁移

**Files:**
- Modify: `web/src/pages/CoverageEvidencePage.vue`
- Test: `web/tests/coverage-evidence-page.spec.ts`（不动）

- [ ] **Step 1: 迁移实现**（要点）：

- `version-select` 表单行保持，包进无标题 Card；
- `coverage-segments` 表 → `DataTable`（列 table/kind/window_start/window_end/reason；`#kind="{ value }"` 插槽渲染语义徽章——纯展示映射 `fetched→pass、carried→info、not_fetched→block`，用 `.badge-state--*` 类或直接 StateBadge 的中性用法：这里 kind 不是 StateBadge 三域之一，用页面内小映射渲染 `<span class="badge-state badge-state--pass">fetched</span>` 等三态，映射表页面常量）；
- `untrusted-{table}` 区块结构保留（含 `untrusted-empty-{table}` 空态文案），内层表保持原生（行数少、动态列）；
- `issue-list` 表 → `DataTable`（severity/code/symbol/trade_date 列；severity 用 `#severity` 插槽按 `isBlockingSeverity` 着色 `--color-block`/`--color-text-secondary`，文案原样）；
- `attested-boundary-note` blockquote **逐字保留**（展示纪律的强制文案）。

- [ ] **Step 2: 全量确认**

Run: `npm test -- coverage-evidence-page.spec`
Expected: PASS。

- [ ] **Step 3: 提交**

```bash
git add web/src/pages/CoverageEvidencePage.vue
git commit -m "feat(web): migrate coverage evidence page onto the design system

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: 更新任务迁移

**Files:**
- Modify: `web/src/pages/UpdateJobsPage.vue`
- Test: `web/tests/update-jobs-page.spec.ts`（不动）

- [ ] **Step 1: 迁移实现**（要点）：

- `operations-disabled` 块与 `equivalent-cli` pre → `Card title="操作面未启用"`（文案与 testid 逐字保留）；
- `update-form` → `Card title="发起更新"`；
- `job-detail` → `Card :title="\`任务 ${selectedJob.job_id}\`"`；`job-status` 段落换 `StateBadge kind="job"` + 原文案前缀保留（`data-testid="job-status"` 包裹）；四终态块（`job-succeeded`/`job-failed`/`job-cancelled`/运行中）结构与 testid 不动；`job-stdout`/`job-stderr` pre 不动；
- `job-list` 表 → `DataTable`（列 job_id（mono，插槽链接选中该 job——点击行为复用页面 `selectJob`，通过 `#job_id` 插槽渲染 `<button class="link-like" @click="selectJob(row.job_id)">`，保持既有"点行选任务"行为；status 列 `#status` 插槽换 StateBadge kind="job"）。

- [ ] **Step 2: 全量确认**

Run: `npm test -- update-jobs-page.spec`
Expected: PASS（探测/提交/轮询/409/终态全部既有断言原样通过——本页交互最重，任何断言失败都先改实现不改断言）。

- [ ] **Step 3: 提交**

```bash
git add web/src/pages/UpdateJobsPage.vue web/src/styles.css
git commit -m "feat(web): migrate update jobs page onto the design system

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

（`.link-like { background: none; border: none; padding: 0; color: var(--color-primary); text-decoration: underline; cursor: pointer; }` 追加进 styles.css。）

---

### Task 7: S0b 收口

- [ ] **Step 1: 全量回归**

Run: `npm test && npm run typecheck && npx playwright test`
Expected: 全绿；**任何 e2e/单测断言的修改都必须在提交信息里逐条列出并说明为何实现无法保持**（目标为零修改）。

- [ ] **Step 2: testid 守卫**

Run（在 `web/`）: `git diff <S0b开工提交> -- src | grep -o 'data-testid="[a-z-]*"' | sort -u` 对比开工前清单——**删除项必须为空**。

- [ ] **Step 3: 零契约勾稽**

Run: `git diff --stat <S0b开工提交> -- src/api package.json`
Expected: 空输出。

- [ ] **Step 4: 视觉走查清单**（`npm run dev` + mock 或真服务）：五页卡片层级、徽章语义色、表格粘性表头/排序、空态、暗色 token 结构（不切换）；逐页截图对比迁移前后信息完整性（列不丢、文案不变）。

- [ ] **Step 5: 收口提交**（如有微调）。

---

## Self-Review 记录（2026-10-03）

**1. 规格覆盖**：§5.6（四页 + 任务页换皮，信息与行为不变）→ Task 2–6；§6 组件落地使用（DataTable 插槽补齐是使组件可用的最小扩展）→ Task 1；§8 S0b 验收（既有断言全过、testid 不删、无新增依赖）→ Task 7。

**2. 占位符扫描**：各页迁移给的是"逐区块变换 + 关键代码片段"而非整页重印——变换是外科手术式的（包 Card/换 DataTable/换 StateBadge），未引用任何未定义符号；Task 1 的 `rowTestid` prop 与插槽在同一任务内定义并被 Task 2 消费。

**3. 类型一致性**：`DataTableColumn`（第一批）沿用；`row-testid` prop 新增后在 Task 2/6 使用；StateBadge `kind` 域沿用第一批定义（coverage 的 kind 三态是页面级徽章类，不扩 StateBadge 域——避免语义域污染）。

**4. 已知风险**：DataPreviewPage 动态数值列右对齐判定读取首行类型——空页时回落左对齐（渲染细节，无行为影响）；UpdateJobsPage 的 job-list 若既有测试依赖整行点击而非按钮，实现时以行 `<tr @click>` 保持（DataTable 无行点击——若测试钉了行点击，该表保留原生表格并在提交信息注明，其余页不受影响）。
