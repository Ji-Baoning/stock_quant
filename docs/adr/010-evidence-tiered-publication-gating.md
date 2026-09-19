---
status: accepted
date: 2026-09-19
decision: Publication gating resolves blocking quality codes per table evidence tier — a registered table declares `core`, `anchored` or `research_only` in sources.yml `data_contracts`, global process codes always block, undeclared tables fail closed as `core`, and a non-core table-level structural defect stops blocking the release and instead records `coverage_downgraded` (UNTRUSTED) that the research preflight intercepts at the consumer.
affects:
  - src/stock_quant/data_quality/gates.py
  - src/stock_quant/data_quality/models.py
  - src/stock_quant/data_pipeline.py
  - project/configs/sources.yml
---

# 010 — Evidence-tiered publication gating

相关：ADR-011（验收窗口锚定验收义务，同批 B1）、spec
2026-09-19-data-type-expansion-architecture-design §2 D1 / §6 A1

## 背景

`PUBLICATION_BLOCKING_CODES`（gates.py）是一个扁平 frozenset，15 个阻断码
对任何表一视同仁。新增数据类型（财务、行业等）的核验成熟度低于存量七表：
一表结构问题拖垮整轮发布，会把新数据永久挡在门外；但直接放行又违背
「不得弱化发布门禁」的仓库纪律。

## 决策

发布门禁按「证据强度」分三档（spec D1），档位写进 sources.yml 的
`data_contracts` 段，判定谓词从 `item.code` 改为 `(item.code, item.table)`
查声明映射：

1. `core`：15 码任一命中即整轮不发布（现状不变）。存量七主表与全部
   coverage 表一律 core（spec §6 B1）。
2. `anchored`：声明过独立锚点的表；表级结构码命中时不阻断发布，改为该表
   coverage `UNTRUSTED` 记录（照 corporate_action_coverage 的
   per-symbol-window 形状），research run 预检在消费端拦截。
3. `research_only`：无独立锚点的表；发布语义同 anchored，但无论 coverage
   多干净，正式 research run 恒拒绝消费；只能被工程诊断规格引用并标注
   RESEARCH-ONLY。

`(code × tier)` 首版矩阵（spec §6 A1）：`REPORT_GENERATION_FAILED`、
`QUARANTINE_MISSING_REASON` 为全局进程码恒阻断；其余 13 个表级码按档位
分流。未声明表按 core 阻断（fail-closed）——「忘记声明」不得等于绕过开关。

## 后果

- 档位变更 = 配置变更 + 本 ADR 同批留痕；开发者不得即席决定档位。
- 档位是运行时策略：不进冻结规格、不参与实验身份哈希；已冻结/已接受
  实验不回溯重判，新 run 立即生效。
- 非 core 表的降级记录以 `coverage_downgraded`（WARNING，不进阻断码表）
  落质量报告，消费端预检按钉住版本的质量报告裁决。
