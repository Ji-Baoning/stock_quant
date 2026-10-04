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
    // 基线 hypothesis 即占位符：生成器拒绝占位符出厂（防呆设计），
    // 且该断言同时证明基线除占位符外不触发任何其他违规。
    expect(validateForm(baselineForm())).toEqual(["请填写真实假设"]);
    expect(validateForm({ ...baselineForm(), hypothesis: "   " })).toContain("hypothesis 不能为空");
    expect(validateForm({ ...baselineForm(), dateStart: "2026-09-01", dateEnd: "2026-01-01" }))
      .toContain("start_date 不能晚于 end_date");
    expect(validateForm({ ...baselineForm(), costScenarios: [] }))
      .toContain("cost_scenarios 至少一项");
    expect(validateForm({ ...baselineForm(), fileName: "Bad Name" }))
      .toContain("文件名只允许小写字母/数字/下划线");
    expect(validateForm({ ...baselineForm(), topN: 0 })).toContain("top_n 必须 ≥ 1");
    // research × 空 universe：不写入 universe_definition 键，正式 run 静默回落工程 universe。
    expect(validateForm({ ...baselineForm(), trustMode: "research", universeDefinition: "" }))
      .toContain("research 档必须填写 universe_definition");
    // 正向：research 且 universe 已填 → 无该违规（占位符违规为本校验的预期项，过滤后比对）。
    expect(
      validateForm({ ...baselineForm(), trustMode: "research" }).filter(
        (message) => message !== "请填写真实假设",
      ),
    ).not.toContain("research 档必须填写 universe_definition");
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
