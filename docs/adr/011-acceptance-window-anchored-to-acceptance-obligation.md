---
status: accepted
date: 2026-09-19
decision: The acceptance review window anchors to the acceptance obligation — `_window` uses `full_history_acceptance_start` first, falls back to `requested_start_date`, and otherwise raises the dedicated `full_history_acceptance_start_missing` (a ValueError subclass that run_automated_checks catches into an acceptance FAIL, not a crash, and that `evidence_window` can tell apart from `window_missing`); bootstrap datasets are unacceptable by design.
affects:
  - src/stock_quant/research/acceptance/checks.py
  - src/stock_quant/research/acceptance/evidence.py
  - RUNBOOK.md
---

# 011 — Acceptance window anchored to the acceptance obligation

相关：ADR-010（同批 B1）、spec
2026-09-19-data-type-expansion-architecture-design §2 D5 第 1 条 / §6 B3

## 背景

验收检查 `_window`（acceptance/checks.py）把审查窗口锚在
`build_config.requested_start_date` 上：1d6e43b4… 版本以
`--start 2015-01-01` 发布（早于首个开市日 2015-01-05），按构造
`window_not_calendar_complete` FAIL；RUNBOOK 不得不记录「--start 必须落
开市日」的操作坑。更根本的问题是：审查窗口锚在「取数请求」而非「验收
义务」（universe coverage 起点 `full_history_acceptance_start`，发布时
固定）。B2 增量化落地后，取数窗口每轮缩小，锚在请求上会让审查窗口与
证据包随每次更新静默缩小——evidence_window 的 docstring 明写要防的
正是这件事；requested=null 时 `_window` 抛裸 ValueError 更让默认路径
无法验收。

## 决策

`_window` 起点候选序列（不取 min——min 会放宽到日历证据之前，
checks.py 的 start < 首个开市日硬 FAIL 使 min 窗口保证失败，该提案已
撤回，spec §0 第 11 条）：

1. `full_history_acceptance_start`（非空 str）；
2. 回退 `requested_start_date`（legacy / 非 data_update 来源）；
3. 否则抛专用异常 `full_history_acceptance_start_missing`
   （ValueError 子类：run_automated_checks 捕获表覆盖它 → 验收 FAIL
   而非崩溃；专用类型使 evidence_window 能区分于 `window_missing`）。

bootstrap 数据集按设计不可验收：其 manifest 两字段皆无，验收 FAIL
（`full_history_acceptance_start_missing`），不是崩溃（spec §0 第 12 条）。

## 后果

- `--start 2015-01-01` 发布的版本 `date_window_completeness` 由 FAIL 转
  PASS；requested=acceptance_start 的既有版本行为不变（spec §5 判据 7）。
- data_update 默认路径（requested=null）可被验收（判据 8）。
- RUNBOOK「--start 必须落开市日」条目退役。
- `date_window_completeness` 与 `calendar_coverage_evidence` 首次共用同一
  时钟：每次验收复扫全历史 bar，与 B2 漂移审计互为加强。
