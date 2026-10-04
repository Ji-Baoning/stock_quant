/** §8.2 验收摘要：四态 verdict 与"存在有效 accepted record"分开回显。 */
export type AcceptanceVerdict =
  | "ACCEPTED"
  | "REJECTED"
  | "PENDING_CONFIRMATION"
  | "UNVERIFIED";

export interface AcceptanceSummary {
  state: AcceptanceVerdict;
  has_valid_accepted_record: boolean;
  latest_verdict: string | null;
  record_count: number;
}

/** pin I1：只有 severity 计数，没有 passed/blocking_reasons/issue_count。 */
export interface QualitySummary {
  by_severity: Record<string, number>;
}

export interface DatasetSummary {
  dataset_version: string;
  is_current: boolean;
  created_at: string | null;
  table_count: number;
  quality: QualitySummary;
  acceptance: AcceptanceSummary;
}

export interface DatasetListResponse {
  /** 当前版本（完整哈希或 null）；顶栏读这个。 */
  current: string | null;
  datasets: DatasetSummary[];
}

/** pin I2：表清单是对象数组。 */
export interface TableMeta {
  name: string;
  row_count: number;
  schema_version: string;
}

export interface DatasetDetailResponse {
  /** 永远是完整 64 位哈希（请求 current 时为解析结果回显）。 */
  dataset_version: string;
  requested_version: string;
  manifest: Record<string, unknown>;
  tables: TableMeta[];
  quality: QualitySummary;
  acceptance: AcceptanceSummary;
}

/**
 * pin I2：覆盖段的形状（`data_model/fetch_coverage.py:87-96`）。它在完整的
 * `manifest.build_config.table_fetch_coverage` 里，**不在详情响应顶层**；
 * `reason` 仅在 `kind === "not_fetched"` 时出现（为 None 时该键整个省略）。
 */
export interface FetchCoverageSegmentRecord {
  table: string;
  kind: "fetched" | "carried" | "not_fetched";
  window_start: string;
  window_end: string;
  reason?: string;
}

export interface QualityIssueRecord {
  severity: string | null;
  code: string | null;
  table: string | null;
  symbol: string | null;
  trade_date: string | null;
  details: Record<string, unknown> | null;
}

/** pin I3：分页游标是 total，不是 next_offset。 */
export interface QualityListResponse {
  dataset_version: string;
  requested_version: string;
  offset: number;
  limit: number;
  total: number;
  issues: QualityIssueRecord[];
}

/** pin I4：回显字段名就是 `arguments`（§8.2 / OpenBB 先例），日期过滤是单值。 */
export interface TableArguments {
  requested_version: string;
  table: string;
  columns: string[];
  symbol: string | null;
  trade_date: string | null;
  offset: number;
  limit: number;
}

export interface TablePreviewResponse {
  dataset_version: string;
  /** 对账修正：P3 实现带顶层 `requested_version`（tables.py:51-58），计划漏了它。 */
  requested_version: string;
  table: string;
  arguments: TableArguments;
  columns: string[];
  rows: Record<string, string | number | null>[];
}

/** 前端发起的表预览参数（服务端回显字段 requested_version/table 由 client 补）。 */
export interface TablePreviewParams {
  columns: string[] | null;
  symbol: string | null;
  /** pin I4：单值日期过滤；没有区间。 */
  trade_date: string | null;
  offset: number;
  limit: number;
}

/** pin I5：列表项字段比 DatasetSummary 少。 */
export interface ExperimentSummary {
  experiment_id: string;
  status: string | null;
  dataset_version: string | null;
  universe_version: string | null;
  evaluation_reason: string | null;
}

export interface ExperimentsResponse {
  experiments: ExperimentSummary[];
}

export type UpdateJobStatus =
  | "QUEUED"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED_BY_SHUTDOWN";

/** §9.1 允许参数：start/end/sources/disclosure-lookback-days；默认空 = 常规增量。 */
export interface UpdateJobRequest {
  start: string | null;
  end: string | null;
  sources: string[] | null;
  disclosure_lookback_days: number | null;
}

/** pin I10：列表项——只有这六个字段。 */
export interface UpdateJobSummary {
  job_id: string;
  status: UpdateJobStatus;
  created_at: string;
  updated_at: string;
  run_id: string | null;
  dataset_version: string | null;
}

/** pin I10：详情在摘要之上加进程/心跳/退出与已脱敏的日志**字符串**。 */
export interface UpdateJob extends UpdateJobSummary {
  pid: number | null;
  boot_id: string | null;
  heartbeat_at: string | null;
  exit_code: number | null;
  failure_reason: string | null;
  failure_detail: string | null;
  stdout_tail: string;
  stderr_tail: string;
}

/** pin I10：列表外层是对象。 */
export interface UpdateJobsResponse {
  jobs: UpdateJobSummary[];
}

/** pin I11：201 只回两个字段，详情另取。 */
export interface UpdateJobCreated {
  job_id: string;
  status: UpdateJobStatus;
}

/** pin I11：409 的 `error.job_id`。 */
export interface UpdateConflictBody {
  code: "update_already_running";
  job_id: string;
}

/** pin I7 / I8：健康探针与嵌套错误信封。 */
export interface HealthResponse {
  /** P3 冻结为 `Literal["ok"]`；不要放宽成 string。 */
  status: "ok";
  /** 16 位十六进制；故意不含绝对路径，前端只显示/比对，不反解。 */
  project_root_fingerprint: string;
}

export interface ErrorBody {
  code: string;
  message?: string;
  [key: string]: unknown;
}

export interface ErrorResponse {
  error: ErrorBody;
}

/** S1：/experiments/summaries 行（pin I5 五字段 + 结论层）。 */
export interface ExperimentSummaryRow {
  experiment_id: string;
  status: string | null;
  dataset_version: string | null;
  universe_version: string | null;
  evaluation_reason: string | null;
  hypothesis: string | null;
  stability_conclusion: string | null;
  stability_policy_hash: string | null;
  research_status: string | null;
  canonical_scenario: string | null;
  aggregates: ScenarioAggregate[] | null;
  display_extremes: {
    max_per_fold_drawdown: number | null;
    max_reject_rate: number | null;
    mean_turnover: number | null;
  } | null;
}

export interface ScenarioAggregate {
  scenario: string;
  aggregate_return: number | null;
  annualized_return: number | null;
  annualized_volatility: number | null;
  sharpe_zero_rf: number | null;
  oos_return_observations: number | null;
  annualization_observations: number | null;
}

export interface ExperimentResultsResponse {
  experiment_id: string;
  manifest: Record<string, unknown>;
  metrics: Record<string, unknown> | null;
  stability_report: Record<string, unknown> | null;
}

export interface FoldEquityResponse {
  experiment_id: string;
  fold_id: string;
  scenario: string | null;
  rows: Array<{ trade_date: string } & Record<string, number | string | null>>;
}

export interface BenchmarkResponse {
  dataset_version: string;
  requested_version: string;
  symbol: string;
  rows: Array<{ trade_date: string; close: number }>;
}
