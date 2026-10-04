# Web 决策层 · S3a 注册台实施计划（命令 + spec YAML 生成器，零写面）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 `/strategies/register` 注册台：决策者不读代码就能正确发起一次可复现实验——表单（对齐冻结 `ExperimentSpec` 字段）→ 实时生成两样可复制产物：等价 spec YAML（保存到 `configs/experiments/<name>.yml`）与完整 CLI 命令。命令随信任档切换入口：research → `python -m stock_quant research run --spec configs/experiments/<name>.yml --root <root>`；engineering → `python -m stock_quant backtest momentum_60d --spec … --root … --engineering`（`research run` 恒以 RESEARCH 档运行并覆盖规格里的信任字段，工程诊断只有 `backtest --engineering` 一条真实入口——owner 2026-09-13 裁定）。**零写面**（owner 裁定 2：方向性否决浏览器启动发布型 run；本页本身就在强化"人跑冻结命令"）。

**Architecture:** 纯前端、零后端、零新依赖：YAML 由手写确定性序列化器产出（spec 的值域只有标量/标量列表/两层标量映射，不需要 js-yaml）；表单校验只做"前端可判定"的冻结规则镜像（非空/区间/联动），不能判定的（成员表哈希、验收绑定存在性）交回 CLI 的 fail-closed——页面明说。生成的 YAML 字段集与 `project/configs/experiments/momentum_60d_wf_tw_baseline.yml`（现行正式模板）逐字段同集，golden 对拍钉死。

**Tech Stack:** 既有栈零新增。

**Spec:** [2026-10-03-web-portal-decision-layer-design.md](../specs/2026-10-03-web-portal-decision-layer-design.md) §5.4 S3a、§8 S3a 行、§10 裁定 2。前置：S1（策略组导航已上线；详情/列表页存在，注册台入口挂策略组）。

## Global Constraints

- **零写面**：页面不发起任何写请求（不 POST、不触 ops 服务）；产物只有"复制 YAML/复制命令"。
- **诚实边界文案（强制）**：页面固定显示——"注册台只生成草稿：把 YAML 保存为 `configs/experiments/<文件名>.yml` 后在仓库根运行命令。web 不写文件、不启动 run（owner 裁定 2：发布型命令由人负责）。前端校验只是便捷提示，最终以 CLI 的冻结校验为准。"
- **信任档与命令入口必须一致（强制）**：`research run` 恒以 RESEARCH 档运行并覆盖规格里的 `trust_mode`/`data_acceptance_id`，`backtest --engineering` 才是工程诊断入口（owner 2026-09-13 裁定）。页面必须在信任档切换处显示这条，并同步切换 `command-preview` 里给出的命令（`buildCommand` 已按此实现）。
- **产品取舍已确认（2026-10-03 审核，设计 owner 裁定保留开关 + 正确分支，不做"只服务正式研究"的窄化）**。理由：① 两档各填各的注册表——`backtest --engineering` 只发布到 debug 注册表（`data/runs/debug`，cli.py:1034），永不进 `data/experiments`，策略列表页不会被工程诊断污染；② 本仓库的纪律就是"先工程诊断、后正式 run"（一次性挑战持有集不可轻动、正式 run 绑 CURRENT_ACCEPTED 门禁），只留正式入口会把最常用的诊断操作推回手写命令，反而削弱流程；③ 档位在命令文本里显式可见（两条不同命令 + `trust=UNTRUSTED` 回显），无静默档位错配的可能。
- **字段集冻结**：表单字段 = `ExperimentSpec` 的用户可填子集（下文清单），不多不少；序列化键名逐字对齐 pydantic 模型/现行模板。
- 每任务一提交（英文 + Co-Authored-By: Claude Code）。

## 开工前必须知道的实现形态（2026-10-03 实查）

1. **`ExperimentSpec` 冻结字段**（`research/spec.py:157` 起）：`hypothesis: str`、`factor_versions: dict[str,str]`、`dataset_version: str`、`universe_version: str`、`universe_definition: str|null`、`data_acceptance_id: str|null`（缺省 `CURRENT_ACCEPTED`；ENGINEERING 须显式 `null`）、`date_range{start_date,end_date}`、`execution_pipeline: "engineering_single_window"|"walk_forward_oos_v1"`、`train_validation_holdout_policy: "not_applicable_engineering_mvp"`（字面量，表单只读展示）、`preprocessing{winsorization,standardization}`（现值均 `none`）、`portfolio_rule`（判别联合：`top_n_equal_weight{top_n,lot_size}` 或 `buffered_risk_weighted{target_count,entry_rank,hold_rank,risk_lookback_days,min_risk_observations,volatility_floor_annualized,max_single_weight,rebalance_band_absolute,gross_exposure,long_only,leverage,weight_quantum}`）、`cost_scenarios: list[str]`、`random_seed: int`、`code_commit`（缺省 `unversioned`）、`parent_experiment_ids: list[str]`、`agent_id: str|null`、`trust_mode: research|engineering`。
2. **CLI 入口**：`research run --spec <相对 root/configs 的路径> --root <root>`（`cli.py:931`）——仅此两参。
3. **现行正式模板**：`project/configs/experiments/momentum_60d_wf_tw_baseline.yml`（字段全集与注释风格的对拍基准）；known universe 定义：`custom_csi300_tw_tradable`、`custom_csi300_ic_tradable`（`configs/universes/`）。
4. **表单默认值**取自该基线模板（等权 Top-10、三成本情景、seed 42、CURRENT 占位、2020-01-01..2026-08-28）——默认值即"当前可跑的正式基线变体"，不是发明。

## 文件结构

| 文件 | 动作 |
| --- | --- |
| `web/src/register/specBuilder.ts` | 新建 | 表单模型 + 校验 + YAML/命令生成（纯函数） |
| `web/src/pages/RegisterPage.vue` | 新建 | 注册台表单页 |
| `web/src/router.ts` | 修改 | `/strategies/register` 路由 + 策略组第二项 |
| `web/tests/register-builder.spec.ts`、`web/tests/register-page.spec.ts` | 新建 | 测试 |

---

### Task 1: spec 生成器（纯函数）与 golden 对拍

**Files:**
- Create: `web/src/register/specBuilder.ts`
- Test: `web/tests/register-builder.spec.ts`

**Interfaces:**
- Produces:

```ts
export interface RegisterForm {
  fileName: string;                 // 无扩展名，[a-z0-9_]+
  hypothesis: string;
  executionPipeline: "walk_forward_oos_v1" | "engineering_single_window";
  trustMode: "research" | "engineering";
  universeDefinition: string;       // 如 custom_csi300_tw_tradable；空串 = 不写入该键（工程路径）
  dateStart: string;                // YYYY-MM-DD
  dateEnd: string;
  portfolioRuleName: "top_n_equal_weight" | "buffered_risk_weighted";
  topN: number;                     // equal-weight 域
  lotSize: number;
  buffered: {                       // buffered 域（页面按规则名切换可见性）
    targetCount: number; entryRank: number; holdRank: number;
    riskLookbackDays: number; minRiskObservations: number;
    volatilityFloorAnnualized: string; maxSingleWeight: string;
    rebalanceBandAbsolute: string; grossExposure: string;
  };
  costScenarios: string[];          // ⊆ {zero_cost, commission_tax, full_cost}，≥1
  randomSeed: number;
}

export function validateForm(form: RegisterForm): string[];   // 违规消息列表；空 = 通过
export function buildSpecYaml(form: RegisterForm): string;    // 确定性 YAML（2 空格缩进）
export function buildCommand(form: RegisterForm, root: string): string;  // 随 trustMode 切换入口（见下）
```

**命令必须随信任档切换（2026-10-03 实查，不可省）**：`research run` 只接受
`--spec/--root`（`cli.py:931`），并且**无条件**以 `DataTrustMode.RESEARCH` 调用
runner（`cli.py:233-238`：*"Formal research always applies the RESEARCH corporate-action
trust bar"*）——规格里的 `trust_mode: engineering` / `data_acceptance_id: null` 在
`research run` 路径下会被 CLI 覆盖。owner 2026-09-13 已裁定：离线工程诊断只走
`backtest --engineering`（`cli.py:1010-1039`，debug registry，产物落
`data/runs/debug`，永不发布正式实验）。因此：
- `trustMode === "research"` → `python -m stock_quant research run --spec … --root …`
- `trustMode === "engineering"` → `python -m stock_quant backtest momentum_60d --spec … --root … --engineering`

静态的 `research run` 命令 + 可切到 engineering 的表单 = 生成的 YAML 与命令互相
矛盾，且正是"engineering 意图的规格照样发布 ACCEPTED"那个失败模式。页面必须
把这条说清楚（`honesty-note` 的强制文案要包含它）。

- [ ] **Step 1: 失败测试（golden 对拍）**

```ts
// web/tests/register-builder.spec.ts
import { describe, expect, it } from "vitest";
import { baselineForm, buildCommand, buildSpecYaml, validateForm } from "../src/register/specBuilder";

const YAML_KEYS = [
  "hypothesis:", "execution_pipeline:", "factor_versions:", "dataset_version:",
  "universe_version:", "universe_definition:", "trust_mode:", "data_acceptance_id:",
  "date_range:", "  start_date:", "  end_date:", "train_validation_holdout_policy:",
  "preprocessing:", "  winsorization: none", "  standardization: none",
  "portfolio_rule:", "  name: top_n_equal_weight", "  top_n: 10", "  lot_size: 100",
  "cost_scenarios:", "  - zero_cost", "  - commission_tax", "  - full_cost",
  "random_seed: 42", "code_commit: unversioned", "parent_experiment_ids: []", "agent_id: null",
];

describe("注册台生成器（S3a）", () => {
  it("基线表单产出的 YAML 与现行模板字段集逐键一致", () => {
    const yaml = buildSpecYaml(baselineForm());
    for (const key of YAML_KEYS) {
      expect(yaml, `缺少 ${key}`).toContain(key);
    }
    // 信任联动：engineering → data_acceptance_id 显式 null。
    expect(yaml).toContain("trust_mode: engineering");
    expect(yaml).toContain("data_acceptance_id: null");
  });

  it("research 信任档 → CURRENT_ACCEPTED（正式验收门禁不缺席）", () => {
    const yaml = buildSpecYaml({ ...baselineForm(), trustMode: "research" });
    expect(yaml).toContain("data_acceptance_id: CURRENT_ACCEPTED");
  });

  it("buffered 规则域按名字切换并序列化全部参数", () => {
    const yaml = buildSpecYaml({
      ...baselineForm(),
      portfolioRuleName: "buffered_risk_weighted",
    });
    expect(yaml).toContain("  name: buffered_risk_weighted");
    for (const key of ["target_count:", "entry_rank:", "hold_rank:", "risk_lookback_days:",
      "min_risk_observations:", "volatility_floor_annualized:", "max_single_weight:",
      "rebalance_band_absolute:", "gross_exposure:"]) {
      expect(yaml).toContain(key);
    }
  });

  it("校验镜像冻结规则（前端可判定子集）", () => {
    expect(validateForm(baselineForm())).toEqual([]);
    expect(validateForm({ ...baselineForm(), hypothesis: "   " })).toContain("hypothesis 不能为空");
    expect(validateForm({ ...baselineForm(), dateStart: "2026-09-01", dateEnd: "2026-01-01" }))
      .toContain("start_date 不能晚于 end_date");
    expect(validateForm({ ...baselineForm(), costScenarios: [] }))
      .toContain("cost_scenarios 至少一项");
    expect(validateForm({ ...baselineForm(), fileName: "Bad Name" }))
      .toContain("文件名只允许小写字母/数字/下划线");
    expect(validateForm({ ...baselineForm(), topN: 0 })).toContain("top_n 必须 ≥ 1");
  });

  it("命令随信任档切换（engineering 不得走 research run）", () => {
    expect(buildCommand({ ...baselineForm(), trustMode: "research" }, ".")).toBe(
      "python -m stock_quant research run --spec configs/experiments/momentum_60d_wf_draft.yml --root .",
    );
    expect(buildCommand(baselineForm(), ".")).toBe(
      "python -m stock_quant backtest momentum_60d --spec configs/experiments/momentum_60d_wf_draft.yml --root . --engineering",
    );
  });
});
```

- [ ] **Step 2: 运行确认失败** → FAIL（模块不存在）。
- [ ] **Step 3: 实现**（要点 + 关键代码）：

`baselineForm()` 返回现行基线模板的值（hypothesis 用占位"【在此填写假设：经济逻辑与预期来源】"——**生成器拒绝占位符出厂**：`validateForm` 检测 `hypothesis` 含"【"即违规"请填写真实假设"）。YAML 序列化手写：

```ts
function scalar(value: string | number | boolean): string {
  return typeof value === "string" ? value : String(value);
}

export function buildSpecYaml(form: RegisterForm): string {
  const lines: string[] = [
    `hypothesis: >-`,
    ...form.hypothesis.split("\n").map((line) => `  ${line.trim()}`),
    `execution_pipeline: ${form.executionPipeline}`,
    `factor_versions:`,
    `  momentum_60d: 2.0.0`,
    `dataset_version: CURRENT`,
    `universe_version: CURRENT`,
  ];
  if (form.universeDefinition.trim() !== "") {
    lines.push(`universe_definition: ${form.universeDefinition.trim()}`);
  }
  lines.push(`trust_mode: ${form.trustMode}`);
  lines.push(
    form.trustMode === "engineering"
      ? `data_acceptance_id: null`
      : `data_acceptance_id: CURRENT_ACCEPTED`,
  );
  lines.push(
    `date_range:`,
    `  start_date: ${form.dateStart}`,
    `  end_date: ${form.dateEnd}`,
    `train_validation_holdout_policy: not_applicable_engineering_mvp`,
    `preprocessing:`,
    `  winsorization: none`,
    `  standardization: none`,
    `portfolio_rule:`,
  );
  if (form.portfolioRuleName === "top_n_equal_weight") {
    lines.push(`  name: top_n_equal_weight`, `  top_n: ${form.topN}`, `  lot_size: ${form.lotSize}`);
  } else {
    const b = form.buffered;
    lines.push(
      `  name: buffered_risk_weighted`,
      `  target_count: ${b.targetCount}`,
      `  entry_rank: ${b.entryRank}`,
      `  hold_rank: ${b.holdRank}`,
      `  risk_lookback_days: ${b.riskLookbackDays}`,
      `  min_risk_observations: ${b.minRiskObservations}`,
      `  volatility_floor_annualized: ${b.volatilityFloorAnnualized}`,
      `  max_single_weight: ${b.maxSingleWeight}`,
      `  rebalance_band_absolute: ${b.rebalanceBandAbsolute}`,
      `  gross_exposure: ${b.grossExposure}`,
      `  long_only: true`,
      `  leverage: false`,
    );
  }
  lines.push(`cost_scenarios:`);
  for (const scenario of form.costScenarios) lines.push(`  - ${scenario}`);
  lines.push(
    `random_seed: ${form.randomSeed}`,
    `code_commit: unversioned`,
    `parent_experiment_ids: []`,
    `agent_id: null`,
  );
  return lines.join("\n") + "\n";
}

export function buildCommand(form: RegisterForm, root: string): string {
  const spec = `configs/experiments/${form.fileName}.yml`;
  // 信任档决定入口：research 走正式发布路径，engineering 只走 debug 诊断路径。
  // research run 恒以 RESEARCH 档运行（cli.py:233-238），工程诊断必须显式 --engineering。
  return form.trustMode === "engineering"
    ? `python -m stock_quant backtest momentum_60d --spec ${spec} --root ${root} --engineering`
    : `python -m stock_quant research run --spec ${spec} --root ${root}`;
}
```

`validateForm` 按测试清单实现（fileName 正则 `^[a-z0-9_]+$`、日期序、cost_scenarios 非空、topN ≥ 1、buffered 各数值 ≥ 其模型下限、占位假设检测）。

- [ ] **Step 4: 通过 + 提交**

```bash
npm test -- register-builder.spec
git add web/src/register/specBuilder.ts web/tests/register-builder.spec.ts
git commit -m "feat(web): deterministic spec-YAML and command builder for registration

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: 注册台页面

**Files:**
- Create: `web/src/pages/RegisterPage.vue`
- Modify: `web/src/router.ts`（`/strategies/register`；策略组 items 追加 `{ label: "注册台", path: "/strategies/register" }`）
- Test: `web/tests/register-page.spec.ts`

**Interfaces:**
- Produces: 页面 testid：`register-form`、`field-<字段名>`（表单控件统一前缀，本计划用到的至少含 `field-hypothesis`、`field-trust-mode`）、`yaml-preview`、`command-preview`、`copy-yaml`、`copy-command`、`form-errors`、`honesty-note`。

- [ ] **Step 1: 失败测试**

```ts
// web/tests/register-page.spec.ts
import { describe, expect, it } from "vitest";
import { flushPromises } from "@vue/test-utils";
import RegisterPage from "../src/pages/RegisterPage.vue";
import { mountAt } from "./helpers";

describe("注册台（S3a：生成草稿，人跑冻结命令）", () => {
  it("诚实边界文案固定显示（裁定 2）", async () => {
    const wrapper = await mountAt(RegisterPage, undefined as never, "/strategies/register");
    expect(wrapper.get('[data-testid="honesty-note"]').text()).toContain("web 不写文件");
    expect(wrapper.get('[data-testid="honesty-note"]').text()).toContain("由人负责");
  });

  it("表单编辑实时更新 YAML 与命令预览", async () => {
    const wrapper = await mountAt(RegisterPage, undefined as never, "/strategies/register");
    await wrapper.find('[data-testid="field-hypothesis"]').setValue("测试假设：动量延续");
    await flushPromises();
    expect(wrapper.get('[data-testid="yaml-preview"]').text()).toContain("测试假设：动量延续");
    // 默认档是 engineering → 命令走 debug 诊断入口，不是 research run。
    expect(wrapper.get('[data-testid="command-preview"]').text()).toContain(
      "backtest momentum_60d",
    );
    expect(wrapper.get('[data-testid="command-preview"]').text()).toContain("--engineering");
    // 切到 research 档后命令换入口（同一份 YAML 的两个合法入口，不得混淆）。
    await wrapper.find('[data-testid="field-trust-mode"]').setValue("research");
    await flushPromises();
    expect(wrapper.get('[data-testid="command-preview"]').text()).toContain("research run");
  });

  it("占位假设被拦截：form-errors 显示且 YAML 不含占位符", async () => {
    const wrapper = await mountAt(RegisterPage, undefined as never, "/strategies/register");
    await flushPromises();
    expect(wrapper.get('[data-testid="form-errors"]').text()).toContain("请填写真实假设");
  });
});
```

（`mountAt` 需要一个 client——注册页不触 API，`mountAt(RegisterPage, fakeClient(), …)` 传 fakeClient 即可；上面 `undefined as never` 处替换为 `fakeClient()`。）

- [ ] **Step 2: 运行确认失败** → FAIL。
- [ ] **Step 3: 实现页面**：`Card title="注册一个策略实验"` 内两组——(a) 表单（字段按 `RegisterForm`；组合规则名切换 equal-weight/buffered 域；`trust_mode` 切换时页面提示"engineering → 不绑定验收记录（UNTRUSTED 诊断），命令改走 `backtest --engineering`（debug 产物，永不发布正式实验）；research → 绑定 CURRENT_ACCEPTED（正式门禁），命令为 `research run`"——两个字段与 `command-preview` 必须同时变化，不得只改其一）；(b) 输出区（`yaml-preview` `<pre>`、`command-preview` `<pre>`、两个复制按钮走 `navigator.clipboard`，模式复用顶栏 copy-version）；`form-errors` 列出 `validateForm` 消息；`honesty-note` 为 Global Constraints 的强制文案 `<blockquote>`。页面不 import API client 之外任何数据源。

- [ ] **Step 4: 通过 + 提交**

```bash
npm test -- register-page.spec
git add web/src/pages/RegisterPage.vue web/src/router.ts web/tests/register-page.spec.ts
git commit -m "feat(web): registration console that drafts specs and the frozen command

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: 收口

- [ ] `npm test && npm run typecheck && npx playwright test` 全绿（e2e：`/#/strategies/register` 渲染 honesty-note 与命令预览）。
- [ ] 勾稽：`grep -R "POST\|fetch(" web/src/pages/RegisterPage.vue web/src/register/` 为空（零写面证明）；生成的基线 YAML 与 `momentum_60d_wf_tw_baseline.yml` 逐键 diff（字段集一致，值随表单）。
- [ ] 收口提交。

## Self-Review 记录

规格 §5.4 S3a（模板选择/参数表单/命令+YAML 预览/零契约成本/不可能绕过 CLI）→ Task 1/2；裁定 2 的诚实文案 → `honesty-note` 强制测试。占位符扫描：无；`baselineForm` 的占位假设是**被校验拦截的初始值**（防呆设计），不是出厂占位。类型一致性：`RegisterForm` 三处消费一致；YAML 键名与 `ExperimentSpec`/现行模板逐字对齐（golden 用例钉死）；`buildCommand` 的分支与 `cli.py` 两个真实入口（`research run` / `backtest --engineering`）逐字对应。已知边界：`universe_definition` 下拉项为硬编码已知清单 + 自由输入（web 无端点可枚举 configs/；页面注明"以 configs/universes/ 实际存在为准"）；清单里 `custom_csi300_ic_tradable` 必须标注为**已退役**（该 index_constitution 证据链已无任何已发布数据集携带其钉住的成员表哈希，见 `momentum_60d_wf_tw_baseline.yml` 头部说明——文件仍在 `project/configs/universes/` 下，但选中即跑不通），默认项取 `custom_csi300_tw_tradable`。工程诊断入口 `backtest momentum_60d` 的 `--spec` 有默认值 `configs/experiments/momentum_60d.yml`（`cli.py:1011-1013`），本页始终显式传 `--spec`，不依赖该默认值。
