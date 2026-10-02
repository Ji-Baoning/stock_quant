import type { QualitySummary } from "./types";

/**
 * 阻断级 severity。P3 的 `QualitySummary` 只有 `by_severity: Record<string, number>`，
 * 没有 `passed`/`blocking_reasons`/`issue_count`——门禁是否通过读 `acceptance.state`，
 * 阻断项数与本页条数都从这张计数表派生（`data_quality/models.py` 的 Severity：
 * INFO/WARNING/ERROR/FATAL）。
 */
export const BLOCKING_SEVERITIES = ["ERROR", "FATAL"] as const;

export function isBlockingSeverity(severity: string | null): boolean {
  return severity !== null && (BLOCKING_SEVERITIES as readonly string[]).includes(severity);
}

/** 阻断级质量问题条数（不是"门禁通过的判定"——那读 acceptance.state）。 */
export function blockingIssueCount(quality: QualitySummary): number {
  return BLOCKING_SEVERITIES.reduce(
    (total, severity) => total + (quality.by_severity[severity] ?? 0),
    0,
  );
}

export function totalIssueCount(quality: QualitySummary): number {
  return Object.values(quality.by_severity).reduce((total, count) => total + count, 0);
}
