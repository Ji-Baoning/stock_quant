# 公司行为残留削减战役：复算 → 修复 → 重建

- 日期：2026-09-27
- 指令：owner 直接指令 —— "不管你用什么方法（可以是换数据源/判断方法等方法，不能是
  数据过拟合的方法），最大程度上减少公司行为的问题。完全删除 baostock 在公司行为中
  的应用。"
- 决定依据：[ADR-017](../adr/017-economically-equivalent-split-merge.md)、
  [ADR-018](../adr/018-compensatory-share-transfer-representation.md)（均 accepted）
- 性质：工程证据记录。**复算部分是测量；重建读数是新版数据集自己的事实。**

## 1. 修复前的复算（CURRENT = `e1db8328…`，2026-09-25 11:02 发布）

口径与 [2026-09-16 台账](2026-09-16-corporate-action-residual-attribution.md) §4
相同（`status == "UNTRUSTED"`，逐 (symbol, window) 行）：

| 读数 | 行 | 只数 |
| --- | --- | --- |
| 覆盖表 UNTRUSTED 总计 | 62 | **58** |
| ├ FACTS_INCOMPLETE | 39 | 35 |
| ├ SOURCE_CONFLICT | 17 | 17 |
| └ SOURCE_FETCH_FAILED | 6 | 6 |

**其中 34 只只败在一个倒置窗口** `[2026-06-20 → 2020-12-31]`（起点晚于终点的
空区间，携带行）：29 只 incomplete + 5 只 fetch-failed；它们在其余三个窗口全部
VERIFIED/VERIFIED_EMPTY。研究/验收门禁 `evaluate_corporate_action_trust` 对倒置
行有防御跳过（`trust.py` "covers no day"），所以不阻断研究，但已发布表带着死判决，
任何直接读表都会多算。成因：`_merge_carried_coverage` 旧版把完全落在刷新边界之后
的携带行"裁剪"到边界前一天，制造出 end < start 的行；产生侧已修（supersession
分支），**清除侧缺失**——倒置行的 window_end 早于一切后续边界，永远走 `before`
分支被携带。

**门禁真正读到的真实问题 = 24 只**（窗口 1/2）：

| 归属 | 只数 | 符号 | 性质 |
| --- | --- | --- | --- |
| 冲突回退 | 17 | 002269 + 甲11/乙3/丁2 | 旧台账同一批；本轮回退源于裁决链未复现（质量报告仅 1 次 `tdx_xdxr` 失败 + baostock daily 659/659 全灭） |
| ADR-006 政策残留 | 2 | 000503、000629 | 旧判决；ADR-012 的 VERIFIED_EMPTY 兜底已实现于当前代码，属携带旧判决 |
| 承诺补偿表示法缺口 | 2 | 002131、600733 | ADR-009 decision 6 预留 |
| ADR-012 设计性降级 | 2 | 600515、600518 | row-5：公告比例≠市场因子，**故意** blocking |
| 抓取失败 | 1 | 000963 | 运维问题 |

逐项与 09-17 的"11 只"对账闭合：8戊中 6 只重整转增已被 ADR-008/012 豁免
（600515/600518 又被 012 条件降级拉回），002608 全窗口干净，002131/600733 仍在；
+16 冲突回退 +1 抓取 = 24。

## 2. 修复内容（代码）

| # | 改动 | 位置 | 解除的问题 |
| --- | --- | --- | --- |
| 1 | 携带行清除：已是倒置（end < start）的死行不再携带进新版本 | `data_pipeline._merge_carried_coverage` | 34 只伪影（重建后消失） |
| 2 | 经济等价合并：登记日/现金/配股/配售价逐字相等且送+转合计精确相等 → 以 `cninfo+eastmoney(split_equiv)` 入账，链位在全部裁决器与 ADR-014 表示下限合并**之后** | `corporate_actions._economically_equivalent_split` + `normalize_corporate_actions` | 丙类 002269 + 未来同形冲突 |
| 3 | 承诺补偿表示法：类型拒绝 `compensatory_share_transfer`（日期门之前，与重整转增同位）+ 加入 ADR-012 条件豁免清单 | `corporate_actions._reject_reason` + `data_pipeline._NON_BLOCKING_QUARANTINE_REASONS` | 002131、600733 |
| 4 | 删除 baostock 在公司行为的全部应用：`baostock_factor.py` 模块删除（星耀为 ADR-009 因子通道唯一实现），管线/测试/注释清理 | `data_sources/baostock_factor.py`（删除）等 | owner 指令；避免双实现漂移 |
| 5 | TDX 空帧可见性：可达通道对冲突符号返回空帧时记 WARNING（此前静默弃权） | `data_pipeline._LazyActionArbiter.frame_for` | "零完成比对伪装成零漂移" |
| 6 | 深对账旋钮 `--disclosure-lookback-days`：加宽披露日历策略（公司行为三表）的重问窗口；地板 = **基线自身已覆盖的最早跨度**（不是 `project.yml` 的 `start_date`），"重判你发布的全部证据，不越过它" | `fetch_windows._contract_start` + `DataUpdateRequest` + CLI + RUNBOOK | B2/F1 之后系统丧失深窗口重对账能力：普通更新只重判最近 90 天，携带的旧判决永生（17 只冲突回退的机制性根因）；第一次深跑被 `start_date: 2021-01-01` 锢在窗口二，窗口一的 5 只真残留无法触达 |
| 7 | 价格基准表示法（ADR-019）：被类型拒绝的重整转增行（有声明除权日）在四腿证据一致时按**交易所除权参考价隐含的持有人比例**入账，标签 `cninfo+price_basis`；四腿 = 供应商声明的参考价 + TDX category-1 事件 + 涨跌停带证伪 + （配置了通道时的）因子序列一致 | `data_sources/price_basis.py` + `corporate_actions.normalize_corporate_actions` + 管线接线 | 600515、600518 —— ADR-012 降级机制下"数值分歧"永远解锁不了的最后两只（分类只看事件日集合不看幅度）；owner 审计的库内字节测量（因子序列步长 vs 交易价格隐含参考价，一致到 ~3e-7）把 ADR-009 搁置的裁决做掉了 |

裁决失败粒度（逐符号惰性 + 失败缓存）在 09-20 已落地（`a1e45be01`），本轮无需改。

**深对账运行史**（同日三次，各有教训）：

1. `--start 2015-01-05`（RUNBOOK 旧习惯）→ F1 把全部表判 NOT_FETCHED，四个源
   `not_run`，发布纯携带版本 `5870417b`（UNTRUSTED 62 行原样）。RUNBOOK 已补警告。
2. `--disclosure-lookback-days 5000`（无 --start）→ 发布 `93e80b68`，UNTRUSTED
   62→23 行、58→21 只；但重判只覆盖 [2021-01-01→2026-09-22]——锚点钳制。窗口一
   （2015-2020）判决全是携带旧判（000503 checked_at 仍为 09-20），002269/002131/
   600733/000503/000629 的修复全部卡在这。由此发现并修复地板语义（上表 #6）。
3. 修复后重跑（本记录 §4 的读数来源）。

## 3. 测试

- 新增：等价合并 5 例（含链位测试：裁决器意见优先于等价合并）、承诺补偿拒绝 2 例、
  豁免与 ADR-012 降级 2 例、倒置携带行清除 1 例、披露窗口地板 4 例、TDX 双签名
  getter 2 例。
- 修改：`test_tdx_arbiter.py` 因子通道注入改用星耀实现（宽表 + `backward_factor`）；
  `test_no_arbitration_when_the_total_matches_both_sides` 钉住新语义（仲裁器弃权
  ⇒ 等价合并入账）。
- 单元：1507+ 通过（全量 `tests/unit/`），唯一失败
  `test_every_configured_source_is_buildable` 为环境依赖（缺 TUSHARE_TOKEN/
  TUSHARE_TRANSPORT 凭据），干净树上同样失败，与本轮改动无关。
- 集成：`test_cli.py` 23 passed；数据管道四件套
  （`test_data_pipeline`/`test_raw_snapshot_reuse`/`test_pipeline_fetch_coverage`/
  `test_source_contracts`）106 passed。

**附带修复：TDX 裁决器复活的最后一公里。** 最终重建的质量报告一度出现 31 条
`TdxData.get_xdxr() missing 1 required positional argument: 'code'`——环境装的是
pytdxdata **0.5.0**（`get_xdxr(market, code)` 两参），而适配器只实现了 0.6.0 的单
前缀串形态。`tdx.py` 增加双签名 getter（按安装版本的签名选择调用形态；两版本的
`XdxrRecord` 字段面一致），实测 `600989.SH` 返回 16 行真实 xdxr。修复后 TDX 在
最终重建中裁决 24 起冲突全部成功（`cninfo+tdx` 23 + `eastmoney+tdx` 1，与 09-17
实测逐项一致），WARNING 从 31 降到 0。另：CLI 的 `source …: not_ok` 现在携带
reason_code（`not_ok(not_run)` 表示"本轮无 lane 可跑"，与真实失败区分）。

## 4. 全窗口重建读数（最终 CURRENT = `f68df633…`，§1 同口径）

**600515 / 600518 的最终解决（ADR-019，owner 审计驱动）。** 本战役第一轮报告曾把这两只
的原因写成"公告比例（2.8）≠ 市场实际因子（1.065）"并判断"需要一个 owner 决策裁决分歧
值"——owner 复核指出该表述有三处站不住，全部成立并已更正：

1. "2.8 ≠ 1.065"只是 600518 的读数；600515 的正确对照是公告 1+r = 2.92387 vs 价格/
   因子 1.477002（ADR-009 表里"因子序列在该日开始"是 09-19 取数窗口的伪影——存储的
   baostock 全历史序列起于 2002-08-06）。
2. "市场实际因子"的措辞本身越权——ADR-009 原文是"三份证据 state three different
   values and this record does not adjudicate"；把 1.065116 写成"市场实际"等于把那个
   搁置的裁决偷偷做掉。
3. **实质性**：降级由 `adjustment_observed` 分类触发，`market_view` 只问探针日是否在
   通道事件日集合里、不看幅度；且星耀 `enabled: false`（ADR-016 decision 11）后因子
   通道为 None，两只的降级由 TDX category-1 记录单独撑住。**所以无论数值分歧怎么裁，
   只要"那天有价格事件"，豁免就回不来**——缺的是表示法，不是一次挑值。

owner 的决定性测量（全部库内字节，只读）：两个价格通道一致到 ~1e-7——存储因子序列
步长（600518：36.113495→38.465059 = 1.065116；600515：1.927730→2.847260 = 1.477001）
与交易价格隐含的交易所参考价（4.58→4.30；8.67→5.87）互证；CNINFO/TDX 报的转增比例
描述的是股本扩张（重整投资人/抵债），不是价格调整；12-14/12-21 为停牌、复牌日成交
构成从参考价起算的完整 5% 跌停梯，−6.11%/−32.3% 不可能是真实成交。ADR-009 搁置的
裁决由此在库内可裁决，差额的去向（重整投资人/抵债）仍需官方公告确认——那是资本侧
的记录完整性问题，不影响价格侧按参考价入账。

深对账共六跑，逐版读数：

| 版本 | UNTRUSTED 行 / 只 | 相对上一版的增量 |
| --- | --- | --- |
| `e1db8328`（修复前基线，09-25） | 62 / **58** | — |
| `5870417b`（纯携带，F1 教训） | 62 / 58 | 无变化（教训本身） |
| `93e80b68`（深对账，锚点钳制） | 23 / 21 | 窗口二重判 |
| `cac1eaa5`（全窗口，TDX 故障） | 3 / 3 | 全部不完整清零；丙类收编 |
| `57726cb4`（全窗口 + TDX 双签名） | 2 / 2 | 甲/乙/丁 24 起全部复裁 |
| `93e80b68`→`57726cb4` 间两次尝试 | 2 / 2 | ADR-019 settler 的 `volume`/`vol` 列名错配被复算捕获（见下） |
| **`f68df633`（ADR-019 修复后）** | **0 / 0** | 600515/600518 按 `cninfo+price_basis` 入账 |

**最终判决分布**：VERIFIED 650 / VERIFIED_EMPTY 9 / UNTRUSTED **0**（单窗口
[2015-01-05 → 2026-09-22]，659 只）。质量报告零 ERROR、零 FATAL；唯一 WARNING 是
空帧可见性警告（TDX 对 `600733.SH` 的分类咨询返回空帧——ADR-019 之前它静默弃权，
现在有迹可查）。入账标签分布含 `cninfo+price_basis` 2 行（600518 资本化比例
0.065116/股、600515 0.477002/股——与交易所参考价 4.58/4.30、8.67/5.87 精确互逆），
INFO 级 `price_basis_settlement` 证据 2 条记录了全部算术。

**验收门禁复算**：`evaluate_corporate_action_trust`（2021-01-01 → 2026-08-30，
659 只固定工程 universe）读 **trusted=True、失败 0** —— owner 审计指出的
"`corporate_action_evidence` 剩余全部触发项"已清零。正式 ACCEPTED 记录仍是 owner
的行为，本记录不代签。

** settler 首轮重建未入账的教训**：`rebuild5`（`60719f23`）后复算发现两只仍
UNTRUSTED、无 price_basis 行。逐腿独立复跑（真实 fetch + 真实 TDX 帧）定位到
`_ex_day_trades_around_the_reference` 读 `volume` 列，而 tushare 原始帧的列名是
**`vol`** —— 单元 fixture 用了改名后的列所以全绿，真实数据第一轮才暴露。修复为
两列名兼容，fixture 改镜像原生列名。`rebuild6`（`f68df633`）即入账。

**逐项清账**（对 §1 的 24 只真实问题）：

| §1 归属 | 只数 | 现状 |
| --- | --- | --- |
| 冲突回退（甲11+乙3+丁2+丙1） | 17 | 16 复裁入账（TDX 24 起含携带）+ 1 等价合并（`split_equiv`） |
| ADR-006 政策残留（000503/000629） | 2 | ADR-012 兜底生效：`VERIFIED_EMPTY` |
| 承诺补偿缺口（002131/600733） | 2 | ADR-018 表示法：`compensatory_share_transfer` 豁免，窗口 VERIFIED |
| ADR-012 设计性降级（600515/600518） | 2 | ADR-019 价格基准表示法：四腿证据一致，按交易所参考价入账 `cninfo+price_basis` |
| 抓取失败（000963） | 1 | 本轮抓取成功 |
| 倒置窗口伪影 | 34 | 携带清除，覆盖表重排为单窗口 |

## 5. 本记录未做的事

- 未改已发布版本的任何字节；判决移动全部落在新数据集版本。
- 未把 600515/600518 的降级当作待修项——那是 ADR-012 的故意行为。
- 未引入星耀分红表作为证据源或仲裁器（评估过：券商聚合数据入证据链违反官方公告
  原则，收益仅覆盖已被 ADR-017 解决的丙类；留待未来独立决策）。

## 6. 附录（同日晚）：验收就绪读数与两项结构性发现

`f68df633…` 之后为清除验收阻塞做了三次推进（`70b46238` → `10cc8c4c` 为 CURRENT），
[ADR-019](../adr/019-price-basis-representation.md) 的实测入账与 [ADR-020](../adr/020-
suspension-proof-grid-is-the-fetch-window-and-its-seam.md) 的物化网格修正均落于此段：

- **`corporate_action_evidence` 首次 PASS**（公司行为战役的验收目标达成）。
- **`source_role_health` 的死锁与自愈**：稳态轮次里日历/日线 lane 因"已覆盖到 end"合法
  跳过，必需源停在初始 `not_run` → 验收判死；且 `end` 由已发布日历解析、日历刷新又因
  覆盖而跳过——**系统无法前进，唯一路径是显式 `--end`**。`--end 2026-09-26` 后
  tushare/akshare 恢复 `ok`，该项 PASS。
- **`date_window_completeness` 的剩余失败（3 只 × 1 天）**：tushare 单股响应在
  2026-09-22 对 `601059/601198/601238`（停牌中）无行——供应商单日缺口，落在已覆盖
  跨度内部；`pre_close` 证明规则的 after 锚（09-23 零成交行）在携带表里但发布表无
  pre_close 列、且无任何操作员路径重抓已覆盖跨度。601995（09-23 复牌）同类缺口已被
  锚定回填。**解封需要 owner 裁决**（ADR-020 §Decision 4）：有界零成交运行证明规则、
  tushare `suspend_d` 证明通道、或作为已归属残留保留。
- 附带修复（ADR-020）：物化证明网格从全验证窗口改为 **fetch 窗口 + 携带/新鲜缝隙**，
  消除稳态轮次的 659 条 `suspension_run_unverified` 噪声与全符号回扫探测风暴。
- 非阻塞运维债：星耀启用仍以批量通道为前提（ADR-016 decision 11）；漂移审计的通道
  覆盖随 baostock 退役/星耀停用收窄；`project/probe_batch_channel.py`（owner 新探针）
  尚未登记脚本清单。
