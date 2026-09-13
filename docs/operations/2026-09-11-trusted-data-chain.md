# 可信数据链路：真实数据更新与验收关卡实测

> **信任等级：数据链路已全绿（`verdict passed=true`），等待 owner 完成 3 项外部佐证并签署 ACCEPTED。**
> 按惯例仍须声明：**股票池是人工挑选、仍在市的 30 只（裁剪后 28 只），幸存者偏差仍在**；
> 验收记录只证明数据完整，不证明股票池无偏，更不证明任何策略有效性。

> **前提已过时（2026-09-12）：** 本文的 `cross_source_price_sample` 第二价格源
> 候选经复核**不成立**：共享代理不暴露本次作答的上游（正文与响应头都没有），
> 且 `fallback_on_empty: true` 覆盖全部 224 个 tushare 方言接口；已接入的
> datahubco RDS 与代理对同一事实逐位一致，属**同源**，不能互相担保。故
> "独立第二价格源"仍需另找。详见
> `docs/operations/2026-09-12-tushare-proxy-assessment.md` §2 与 §7。

## 0. 结论摘要

- 真实 `data update`（窗口 **2015-01-05 .. 2026-08-28**，tushare + akshare）发布
  `b6c9a64180474606aabd3cfee9e29542a58995d809d194bdd53b9c2d75db5213`：
  质量报告 **0 FATAL / 0 ERROR / 0 WARNING / 44 INFO**，`resolved_end_date=2026-08-28` 非回退。
- **两道关卡首次全部通过**：`bar_gate passed=true unexplained=0`（714 条停牌缺口以
  `pre_close` 链证据回补）、`corporate_action_gate trusted=true reasons=0`
  （601318.SH 冲突经 owner 复核采信 cninfo 后消除）。
- **8 项自动检查全部 PASS**（`verdict passed=true`）。
- 验收清单已备：`data/acceptance/b6c9a641…-checklist.yml`，**6 项脚本化人工核证 PASS**，
  剩 **3 项外部佐证 FAIL 留给 owner**（`exchange_calendar_sample`、
  `cross_source_price_sample`、`trading_rule_effective_dates`），签署后即可发布 ACCEPTED。

日历证据说明：更新终点的日历证据由 `calendar_coverage` span 与版本绑定的
`full_history_acceptance_start` 提供，`resolved_end_is_fallback` 已删除；
旧 manifest 会被 `data validate` 报 `calendar_coverage_missing`，处置方式是重发布。

## 1. 从失败到全绿：三个被修复的真问题

| # | 问题 | 根因 | 修复 |
| --- | --- | --- | --- |
| 1 | 首跑缺口 8,813 条（未知缺失） | `normalize_daily` 用 `float.is_integer()` 校验手→股换算，IEEE754 误差使 ~11.5% 正常两位小数手数被整行丢弃 | `d4d710b`：`Decimal` 精确换算 + 回归测试；缺口 8,813 → 714 |
| 2 | 质量报告 56,660 条失真 WARNING | `primary_dates` 被填成裸日期，而缺失判定用 `(symbol, date)` 元组成员测试，永不匹配 | `85c25c6`：改填元组对 + 回归测试 |
| 3 | 714 条真实停牌缺口 | tushare `daily` 不返回停牌证券的行；验收口径（设计如此）要求完整网格 | `3978cd1`/`6277e3f`/`06c138c`：停牌回补建模（下节） |

另修复：`601318.SH` 2018-06-07 除息分红双源冲突（cninfo 1.2 vs eastmoney 1.0）——
owner 裁定采信 cninfo（`69522b4` 复核条目），管线按既有 `apply_corporate_action_reviews`
流程消解，该标的转 `VERIFIED`、隔离区清空。

## 2. 停牌回补建模（`3978cd1` 方案 → `6277e3f`/`06c138c` 实现）

**证据 = 主源自洽性**：tushare `daily` 每行带交易所参考价 `pre_close`。复牌首行的
`pre_close` 恰等于停牌前最后收盘（美的 2016-05-17 收盘 21.35，06-01 复牌 `pre_close=21.35`，
实测逐位一致）。因此：

- 缺口两侧能链上 → 中间开市日**证明**无交易 → 物化停牌 bar：
  `volume=0`、`amount=0`、OHLC=前收、`source="tushare_suspend"`（新增 provenance 白名单标签）；
- 停牌段内含已接受公司行为除权日 → 除权前 carry 前收、除权起 carry 复牌日 `pre_close`
  （交易所除权参考价的实测值，非公式推算）；
- **链断裂且无公司行为解释 = 真实数据丢失** → 新增 `unexplained_primary_gap`
  （ERROR，阻断发布）。该守卫使回补永远无法把丢数洗成停牌——首晚的浮点 bug
  在此机制下会被当场拦截；
- 窗口首/尾无锚段的停牌无法证明 → 不合成，如实 WARNING 留给关卡判 FAIL。

验收口径未做任何放宽：`_ACCEPTED_MISSING_CODES` 不含停牌，完整网格要求不变。
本次发布物化 **714 行停牌 bar**（16 只标的，分年 2015:396 / 2016:202 / 2017:52 /
2018:29 / 2019:19 / 2025:16，与 A 股停牌史吻合），44 个停牌段逐一留 INFO 审计记录。

## 3. 发布内容（`b6c9a641…`）

| 表 | 行数 | 说明 |
| --- | --- | --- |
| `daily_bar` | 78,091 | 含 714 行 `tushare_suspend` 停牌 bar |
| `adjusted_bar` | 74,940 量级 | 由新 daily + 已核验公司行为导出 |
| `corporate_action` | 511 | 601318 冲突已按复核裁定消解 |
| `corporate_action_quarantine` | **0** | 冲突两行已按复核转正 |
| `trading_calendar` | 2,833 | |
| `universe_membership` | 31 行 / 28 只 | 方案一裁剪版原样携带 |

窗口起点为 **2015-01-05**（2015 年首个开市日）：验收检查要求请求窗口落在日历开市
区间内，`2015-01-01` 为假期不满足；01-01..04 本是非交易日，数据覆盖与原约定完全一致。
`project/probe_dataset_gates.py` 与 `build_acceptance_evidence.py` 的窗口常量已同步。

## 4. 两道关卡与 8 项自动检查（终态）

| 检查 | 状态 |
| --- | --- |
| `dataset_manifest_integrity` | PASS |
| `quality_report_integrity` | PASS |
| `required_table_coverage` | PASS |
| `date_window_completeness` | **PASS** |
| `security_master_evidence` | PASS |
| `corporate_action_evidence` | **PASS** |
| `raw_snapshot_traceability` | PASS |
| `source_role_health` | PASS |

`bar_gate passed=true unexplained=0`；`corporate_action_gate trusted=true reasons=0`；
`verdict passed=true`。

## 5. 验收清单：6 PASS + 3 项 owner 外部佐证

`data acceptance prepare` + `build_acceptance_evidence.py` 已就绪
（`data/acceptance/b6c9a641…-checklist.yml`，证据包 `data/acceptance-evidence/b6c9a641…/`）：

- **PASS（脚本化取证）**：`source_row_count_sample`、`missing_reason_sample`、
  `corporate_action_sample`、`benchmark_sample`、`security_master_sample`、`secret_scan`。
- **FAIL（owner 逐项填外部佐证后转 PASS）**：
  1. `exchange_calendar_sample`——以交易所官方日历抽查核对；
  2. `cross_source_price_sample`——以第二价格源抽查（baostock 停机，可用公开行情页）；
  3. `trading_rule_effective_dates`——官方交易规则生效日核对。
  `kind: external` 时 `sha256` 填 `summary` 字符串自身的 sha256；
  `kind: local` 时 `reference` 为项目根内相对路径且哈希与文件一致。

owner 逐条确认 9 项人工核证后执行
`python -m stock_quant data acceptance publish --checklist <清单> --root .`，
得到 `acceptance_id` 与 `decision=ACCEPTED`。**签字是 owner 的行为，AI 不代为认定。**

## 6. 正式通路与后续

- ACCEPTED 发布后：`research run --spec configs/experiments/momentum_60d_pit_tradable.yml`
  即可走通正式通路（方案一产出的时点宇宙定义 + 本数据集 + 验收记录三方就位），
  walk-forward 稳定性结论与一次性挑战（阶段 8）随之解锁。
- `data/acceptance/` 当前只有清单草稿，**尚无 ACCEPTED 记录**。
- 幸存者偏差声明持续有效：30 只人工池仍在市，回补只影响停牌日完整性，
  不改变股票池无偏性。

## 7. 可复现命令

```
# 前置检查（只读）
PYTHONPATH=src python project/verify_update_readiness.py
# 真实更新（联网；TUSHARE_TOKEN 须在环境；注意 stock_basic 限流，见 PROJECT_MEMORY §8.5）
python -m stock_quant data update --start 2015-01-05 --end 2026-08-28 --root .
# 两道关卡 + 8 项自动检查（只读）
PYTHONPATH=src python project/probe_dataset_gates.py
# 验收清单 + 证据回填
python -m stock_quant data acceptance prepare --version <64hex> --operator <id> \
    --output data/acceptance/<64hex>-checklist.yml --root .
# [2026-09-13 已废弃] PYTHONPATH=src python build_acceptance_evidence.py <64hex> data/acceptance/<64hex>-checklist.yml
```

> **2026-09-13 更新**：`project/build_acceptance_evidence.py` 已删除，证据生成并入
> `data acceptance prepare` 主线（见 `docs/superpowers/plans/2026-09-13-acceptance-pending-confirmation.md`）。
> 证据窗口不再由脚本常量决定，改为取自该版本 manifest 的请求窗口；`prepare` 生成的六项人工证据
> 状态为 `PENDING_CONFIRMATION`，须由审核者逐项确认后转 `PASS`。

本轮提交：`2e29867`（前置检查）、`eb0137f`（关卡探针）、`1fc0a1b`（证据包）、
`d4d710b`（浮点换算修复）、`85c25c6`（覆盖集合元组修复）、`69522b4`（601318 复核）、
`3978cd1`/`6277e3f`/`06c138c`（停牌回补方案与实现）、`03a2ddb`/`61d1898`/`38f1cb8`/
`d5caf4b`（本报告各版本）。
