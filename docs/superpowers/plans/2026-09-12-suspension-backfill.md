# 停牌日回补建模 Implementation Plan

> **前提已过时（2026-09-12）：** 本文所记"停牌证据外部来源全部受阻
> （`suspend_d` 无权限、baostock 停机、`stock_tfp_em` 无历史覆盖）"对
> **本地直连**仍然成立，但经共享代理可读：实测 `suspend_d`
> `000333.SZ` 2016-05-01..06-30 返回 10 行 = `2016-05-18..05-31`，正是本文
> 判定无源的那段区间。**这不改变本方案取向**——用主源 `pre_close` 链合成
> 停牌行仍是既成事实；`suspend_d` 现在可以充当**独立交叉校验**（见
> `docs/operations/2026-09-12-tushare-proxy-assessment.md` §6）。

> **For agentic workers:** 按 TDD 顺序实施；先写失败测试再实现。步骤用 checkbox 跟踪。

**Goal:** 用主源自身的 `pre_close` 链作为停牌证据，把验收窗口内"已上市 × 开市日 × 无行"的缺口物化为诚实标注的停牌 bar（`volume=0`、价格前收平推），使 `date_window_completeness` 在不放宽口径的前提下 PASS；链条断裂且无公司行为解释时视为数据丢失，响亮阻断发布。

**Architecture:** 新增纯函数模块 `data_model/suspensions.py`（证据判定 + 行合成），管线在主源抓取后收集 raw 帧、在公司行为调和后调用它，合成行并入 primary 集合。发布门禁新增一个 ERROR 阻断码；provenance 白名单新增 `tushare_suspend` 供应商标签。

**Tech Stack:** Python 3.10 / pandas / pytest。

## Global Constraints

- 不放宽 `_ACCEPTED_MISSING_CODES`、不改验收口径；门禁仍是"完整网格"。
- 合成行必须与 raw 行可区分：`source="tushare_suspend"`（新增白名单标签）。
- 链条不匹配且无已接受公司行为解释 → `unexplained_primary_gap`（ERROR，阻断发布）——这是防数据丢失的守卫，浮点 bug 在此机制下会被就地拦截。
- 窗口首/尾无锚行的停牌段不合成（证据不足），如实 WARNING 留给验收关卡判 FAIL。
- 除权日落在停牌段内时，除权前日 carry 前收、除权日起 carry 复牌日 `pre_close`（该值即交易所除权参考价，是实测值而非公式推算）。
- 禁止跑全量测试与 `tests/integration`。

## 已实测前提（2026-09-11/12）

- tushare `daily` 对停牌证券不返回行；美的 000333.SZ 2016-05-18..05-31 停牌区间，复牌日
  2016-06-01 的 `pre_close=21.35` 与停牌前最后收盘（05-17）逐位相等——链证据成立。
- 除权日链条合法断裂：000333.SZ 2016-05-06 `pre_close=21.19` vs 前收 32.99（除权参考价），
  须由已核验公司行为解释。
- 停牌证据外部来源全部受阻：`suspend_d` 无权限、baostock 停机、`stock_tfp_em` 无历史覆盖。
- 714 条缺口分布：16/30 只，集中于 2015（396）/2016（202），连续区块形态。

## Task 1: 纯函数 `suspension_rows` 与质量码

**Files:** Create `src/stock_quant/data_model/suspensions.py`；改 `data_quality/models.py`（新码）、`raw_checks.py`（`KNOWN_SUPPLIERS` + `tushare_suspend`）、`gates.py`（阻断集 + `unexplained_primary_gap`）；Test: `tests/unit/test_suspensions.py`（新建）。

- [ ] 红灯：8–10 个用例覆盖——完整序列零产出；链条匹配的停牌段物化（vol=0/amount=0/OHLC=前收/`source="tushare_suspend"`）+ INFO 审计；断裂无解释 → ERROR `unexplained_primary_gap` 且不物化；段内含已接受公司行为 → 分段 carry（除权前=前收，除权起=复牌 pre_close）；窗口首/尾无锚段 → WARNING 不物化；非开市日/上市前后日不参与；容差 0.005；`tushare_suspend` 过 provenance；ERROR 码阻断发布。
- [ ] 绿灯实现（签名：`suspension_rows(symbol, chain, open_days, *, list_date, delist_date, actions, ingested_at) -> tuple[DataFrame, list[QualityIssue]]`；`chain` 含 `trade_date/close/pre_close`）。
- [ ] `python -m pytest tests/unit/test_suspensions.py tests/unit/test_quality_checks.py -q` 全绿；`ruff check` 干净。
- [ ] 提交：`feat: materialize pre-close-proven suspension bars`

## Task 2: 管线接线与端到端验收

**Files:** Modify `src/stock_quant/data_pipeline.py`（`_fetch_primary_stock` 收集 raw 帧；公司行为调和后新增 `_materialize_suspensions`，合成行并入 `primary_rows`/`primary_dates`）；Test: `tests/unit/test_suspensions.py` 追加 update 级用例（本地 stub，含 `pre_close` 列，抽掉一个开市日 → 发布表含 carry 行、逐格重算缺口为 0；无 `pre_close` 列的 stub 行为不变）。

- [ ] 红灯：update 级用例证明合成行入库且验收重算通过。
- [ ] 绿灯接线；`python -m pytest tests/unit/test_suspensions.py tests/unit/test_quality_checks.py tests/unit/test_normalize.py -q` 全绿。
- [ ] 提交：`feat: wire suspension materialization into the data update`

## Task 3: 真实更新与关卡收口（operator）

- [ ] `data update`（注意 `stock_basic` 每日 5 次配额，见 PROJECT_MEMORY §8.5）。
- [ ] `probe_dataset_gates.py`：预期 `bar_gate passed=true unexplained=0`、`corporate_action_gate trusted=true`（601318 复核生效）、8 项自动检查全 PASS、`verdict passed=true`。
- [ ] 更新 `docs/operations/2026-09-11-trusted-data-chain.md` 并提交。

## 自审

- 浮点 bug 反事实检验：若历史数据缺行（真丢失），链断裂 → 本机制 ERROR 阻断，绝不把丢失洗成停牌。
- 与 `_missing_issues` 的关系：合成对进入 `primary_dates` 后不再误警；`unexplained_primary_gap` 与该 WARNING 独立。
- `adjusted_bar`：carry 行 close 与邻行一致，不改变事件因子链；update 级用例覆盖。
