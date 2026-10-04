// S3a 注册台规格生成器：纯函数，零写盘面。
// 表单 → 确定性 spec-YAML 草稿内容 + 精确冻结 CLI 命令；黄金对拍见 tests/register-builder.spec.ts。

export interface RegisterForm {
  fileName: string; // 无扩展名，[a-z0-9_]+
  hypothesis: string;
  executionPipeline: "walk_forward_oos_v1" | "engineering_single_window";
  trustMode: "research" | "engineering";
  universeDefinition: string; // 如 custom_csi300_tw_tradable；空串 = 不写入该键（工程路径）
  dateStart: string; // YYYY-MM-DD
  dateEnd: string;
  portfolioRuleName: "top_n_equal_weight" | "buffered_risk_weighted";
  topN: number; // equal-weight 域
  lotSize: number;
  buffered: {
    // buffered 域（页面按规则名切换可见性）
    targetCount: number;
    entryRank: number;
    holdRank: number;
    riskLookbackDays: number;
    minRiskObservations: number;
    volatilityFloorAnnualized: string;
    maxSingleWeight: string;
    rebalanceBandAbsolute: string;
    grossExposure: string;
  };
  costScenarios: string[]; // ⊆ {zero_cost, commission_tax, full_cost}，≥1
  randomSeed: number;
}

// 基线值取自现行模板 configs/experiments/momentum_60d_wf_tw_baseline.yml。
// hypothesis 初始为占位符：validateForm 拒绝占位符出厂——笨蛋防错初始值，永不原样提交。
export function baselineForm(): RegisterForm {
  return {
    fileName: "momentum_60d_wf_draft",
    hypothesis: "【在此填写假设：经济逻辑与预期来源】",
    executionPipeline: "walk_forward_oos_v1",
    trustMode: "engineering",
    universeDefinition: "custom_csi300_tw_tradable",
    dateStart: "2020-01-01",
    dateEnd: "2026-08-28",
    portfolioRuleName: "top_n_equal_weight",
    topN: 10,
    lotSize: 100,
    buffered: {
      targetCount: 10,
      entryRank: 10,
      holdRank: 15,
      riskLookbackDays: 60,
      minRiskObservations: 40,
      volatilityFloorAnnualized: "0.10",
      maxSingleWeight: "0.15",
      rebalanceBandAbsolute: "0.02",
      grossExposure: "1.00",
    },
    costScenarios: ["zero_cost", "commission_tax", "full_cost"],
    randomSeed: 42,
  };
}

// 校验镜像冻结模型（src/stock_quant/research/spec.py）的前端可判定子集。
// 返回全部违规消息（各规则独立判定，不短路）；空列表 = 通过。
export function validateForm(form: RegisterForm): string[] {
  const violations: string[] = [];
  if (!/^[a-z0-9_]+$/.test(form.fileName)) {
    violations.push("文件名只允许小写字母/数字/下划线");
  }
  if (form.hypothesis.trim() === "") {
    violations.push("hypothesis 不能为空");
  }
  if (form.hypothesis.includes("【")) {
    violations.push("请填写真实假设");
  }
  if (form.dateStart > form.dateEnd) {
    violations.push("start_date 不能晚于 end_date");
  }
  if (form.costScenarios.length < 1) {
    violations.push("cost_scenarios 至少一项");
  }
  if (form.portfolioRuleName === "top_n_equal_weight" && form.topN < 1) {
    violations.push("top_n 必须 ≥ 1");
  }
  if (form.portfolioRuleName === "buffered_risk_weighted") {
    const b = form.buffered;
    if (b.targetCount < 1) violations.push("target_count 必须 ≥ 1");
    if (b.entryRank < 1) violations.push("entry_rank 必须 ≥ 1");
    if (b.holdRank < 1) violations.push("hold_rank 必须 ≥ 1");
    if (b.riskLookbackDays < 2) violations.push("risk_lookback_days 必须 ≥ 2");
    if (b.minRiskObservations < 2) violations.push("min_risk_observations 必须 ≥ 2");
  }
  return violations;
}

function scalar(value: string | number | boolean): string {
  return typeof value === "string" ? value : String(value);
}

// 确定性 YAML 序列化（2 空格缩进，手写行，键序与现行基线模板逐键一致）。
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
