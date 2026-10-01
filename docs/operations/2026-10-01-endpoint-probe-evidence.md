# index_weight / daily_basic 端点探针：实测读数（owner 授权下运行）

- 日期：2026-10-01
- 状态：**measured**。探针已于 owner 书面授权下运行，读数由脚本写入
  `2026-10-01-endpoint-probe-evidence.evidence.json`（`probes.index_weight` /
  `probes.daily_basic` / `probes.daily_basic_bar_diff` 三节，`_status` 已翻为
  `measured`）。本 md 承载结论性叙述；JSON 承载逐条原始读数。
- 脚本：`project/probe_index_weight_daily_basic.py`（离线 `render` 子命令打印
  全部请求形状，无需授权；联网子命令消耗配额，严禁无授权运行）。
- 纪律：凭据只由 `build_transport` 从环境读取；脚本与记录不读、不打印、不落盘
  任何凭据。空响应绝不解释为"该日无成分/无因子"（本文所有空响应均按形态记录）。

## 授权块（成文）

- 授权人：owner（2026-10-01 会话内书面授权"我已授权，继续"）。
- 参数（计划默认）：指数代码 `399300.SZ`/`000905.SH`/`000852.SH`（panda 代码
  仅为探针起点，非冻结依据）；节奏月 4 个月（202604 起）；历史窗口
  2005/2010/2015/2020（各取 1 月窗）；daily_basic 参照符号 `000001.SZ`；
  trade-date 2026-09-30（2026-10-01 为国庆休市日）。
- 预算：脚本内置 60 次/子命令。实际发出：index-weight 25 label × 2 transport
  = 50 次；daily-basic 6 label × 2 transport + 1 次 diff-by-day = 13 次。
  两次预运行（凭据未导出、脚本日期缺陷）均未发出任何供应商请求。

## 运行过程记录（dated evidence）

1. 第一次尝试 index-weight：两 transport 均 `unavailable`（未按 RUNBOOK 惯例
   `set -a; . ./.env; set +a` 由启动方导出凭据；`.env` 本身齐备，进程环境为空）。
   transport 未构造，退出码 1，0 次请求。
2. 第二次尝试 index-weight：退出码 3（`DateParseError: day is out of range for
   month: 20260431`）——脚本 cadence 窗口用 `f"{m}31"` 拼月末，202604/202606
   非法。首个请求前即崩，0 次请求。已修复为按 `Period.end_time` 取真实月末
   （各月窗口语义不变，label 不变）。
3. 修复后重跑：index-weight 退出码 0；daily-basic（`--trade-date 2026-09-30
   --root project`）退出码 0。全部读数见 JSON。
4. 读数落盘后核实发现：脚本 `_write_evidence` 的 `_status` 翻转判断只查了节
   顶层 `entry.get("readings")`，而 readings 在 findings[kind] 下一层，导致
   `_status` 恒不翻转（第二个脚本缺陷）。已修复该判断；并用脚本自身
   `_write_evidence` 对既有三节离线重放（0 次网络请求）完成翻转，重放前后
   probes 内容字节一致（仅 `_status`→`measured` 与 `last_updated_utc` 更新）。
   读数本体自始至终由脚本写入，未经手工编辑。

## index_weight（relay 观测于 2026-10-01T09:02:45Z；proxy 于 09:14:22Z）

### 可用性（transport × 指数码）

- relay：25/25 个 label 全部返回读数，0 错误。三指数码每个 cadence 月窗与
  历史月窗均有响应（含空响应形态）。
- proxy：25 个 label 中 9 个成功、16 个 `ServerError`。失败散布无模式：
  `000852.SH` 全部 8 个调用失败；`399300.SZ` 的 cadence 202604/202606、
  history 200501/201001 失败；`000905.SH` 的 cadence 202605、history
  201001/201501/202001 失败。成功的 label 读数与 relay 同窗读数一致或同构。

### 节奏判定：可判定（非 unknown）

- relay 每个整月窗（202604..202607）返回的快照日数稳定：
  `399300.SZ` 每月窗 2 个快照日 × 300 成分 = 600 行；
  `000905.SH` 每月窗 1 个快照日 × 500 成分 = 500 行；
  `000852.SH` 每月窗 1 个快照日 × 1000 成分 = 1000 行。
  即月度快照假设成立，且 399300.SZ 为月内两次快照。
- 对照 2010-01 历史窗：`399300.SZ` 返回 6000 行 / 20 个快照日，
  `000905.SH` 返回 6000 行 / 12 个快照日——两窗都恰好 6000 行，与 ~6000 行
  上限截断形态吻合（推定被截断；确切截断标记未在读数中留痕）。该窗快照密度
  显著高于 2026 年月窗（疑似逐日快照），因此"月度"结论只对近期窗口成立。

### 历史窗口起点（各 1 月窗，空响应不解释为无数据）

- 2005-01：仅 `000905.SH` 有数据（500 行 / 1 快照日，weight 0.066–0.595）；
  `399300.SZ` 空响应、`000852.SH` 空响应。
- 2010-01：`399300.SZ`、`000905.SH` 有数据（各 6000 行，见上，截断推定）；
  `000852.SH` 空响应。
- 2015-01 / 2020-01：三指数码均有数据。`000852.SH` 2020-01 为 1001 行 /
  con_codes 1001 / 1 快照日。
- 结论（受探针窗口约束）：`000905.SH` ≤2005-01 起可得；`399300.SZ` 在
  2005-01 至 2010-01 之间开始可得（2005-01 空响应与其 2005-04 发布时点相容，
  但本探针未单独验证发布月）；`000852.SH` 在 2010-01 至 2015-01 之间开始
  可得。

### 单快照行数与权重列形态

- 列集（三码一致）：`index_code, con_code, trade_date, weight`；全部列
  null 计数 0。
- 单快照行数：300（399300.SZ）/ 500（000905.SH）/ 1000（000852.SH）——
  与成分数一致（con_code 去重数同值；2020-01 的 000852.SH 为 1001）。
- `weight` dtype 全部 `float64`；min/max 量级：399300.SZ 0.019–5.008、
  000905.SH 0.013–1.662、000852.SH 0.008–1.063——百分数量级（和约 100）。

### 空响应形态

- 1990 探针窗（覆盖前）：`rows=0, columns=[]`——无列空帧（不是 `None`，
  也不是带列空帧）。relay 与 proxy 同形态。

### 两 transport 差异与配额

- 同窗读数差异：proxy 的 `399300.SZ` cadence 月窗返回 300 行 / 1 快照日，
  而 relay 返回 600 行 / 2 快照日（proxy 只回窗内一个快照日）；proxy
  `399300.SZ` cadence 202607 的 weight min/max（0.025/4.012）与 relay
  （0.0218/4.8431）不同，说明其返回的快照日与 relay 不同。
- relay 零错误；proxy 16/25 `ServerError`。index_weight 的可靠 transport 是
  relay。
- 消耗：50 次请求（25 × 2 transport），子命令预算 60 次内。

## daily_basic（relay 观测于 2026-10-01T09:14:58Z；proxy 于 09:15:46Z）

### 历史窗口起点（ts_code=000001.SZ，1 月窗）

- 2005-01：19 行 / 19 快照日，有数据（历史起点 ≤2005-01，受探针窗口约束）。
- 2010-01：20 行 / 20 快照日；2015-01：20 行 / 20 快照日；2020-01：16 行 /
  16 快照日（2020-01 实际交易日数）。
- 按区间取法可稳定取到 2005 年。

### 两种取法均可用

- 按日（`trade_date=20260930` + `fields=ts_code,trade_date,total_mv,
  turnover_rate`）：5561 行，恰好返回所请求 4 列（原样供应商帧）。
- 按区间（`ts_code=000001.SZ, 20260920..20260930`）：7 行 / 7 快照日；未限
  `fields` 时返回全默认列集 18 列（`close, turnover_rate, turnover_rate_f,
  volume_ratio, pe, pe_ttm, pb, ps, ps_ttm, dv_ratio, dv_ttm, total_share,
  float_share, free_share, total_mv, circ_mv` + `ts_code, trade_date`）。

### total_mv / turnover_rate 原生单位（以 relay by-day 读数为准）

- magnitude_reference（relay by-day，label `by-day`）：symbol `000001.SZ`、
  trade_date `20260930`、raw_total_mv `22452647.3574`、raw_turnover_rate
  `0.5387`。
- **total_mv：×10000 被实测支持。** 22452647.3574 × 10000 元 ≈ 2.2453 万亿元，
  落在平安银行 2026-09 末总市值合理量级（约 2.2 万亿）；若原生为"元"则读数
  偏小 4 个数量级，若为"百万元"则偏大 2 个数量级，均不合理。
- **turnover_rate：÷100 被实测支持。** 0.5387 ÷ 100 = 0.005387（0.54%），
  落在平安银行单日换手率合理量级；若原生已是比率（不除），则等于 53.87%
  日换手，对超大盘银行股不合理。
- 量级旁证（relay by-range 历史窗，同一换算）：20050131 total_mv
  `1179168.2223`（×10000 ≈ 117.9 亿，与深发展 2005 年初量级一致）、20100129
  `6738791.2635`（≈ 673.9 亿）、20150130 `15914878.4383`（≈ 1591.5 亿）、
  20200123 `30156796.8797`（≈ 3015.7 亿，与平安银行 2020 年初约 3000 亿一致）；
  turnover_rate 0.1318/0.8231/0.9456/0.5671 全部落 in 百分数量级。

### 空值形态

- by-day（5561 行）：4 列 null 计数全部 0。
- relay by-range 与历史窗：除 200501/201001 两窗 `dv_ttm` 全空（19/19、
  20/20 null）外，其余列 null 计数 0。
- proxy by-range：`turnover_rate_f/volume_ratio/dv_ratio/dv_ttm/free_share`
  各 2 null；proxy 202001 帧额外多出 `limit_status` 列（16 行中 14 null），
  且 `close/volume_ratio/pe/pe_ttm/pb/ps/ps_ttm/dv_ratio/dv_ttm` 各 1 null。

### 两 transport 差异

- by-day 两 transport 完全一致（5561 行，magnitude_reference 逐字符相同）。
- 历史精度差异：proxy 202001 total_mv `30156800.0` vs relay
  `30156796.8797`——proxy 丢失小数精度（float32 量级舍入）。单位与量级判定
  一律以 relay 读数为准。

### 同日 symbol 集合与 daily_bar 差异（bar-diff）

- `--root project` 命中已发布数据集（`project/data/standardized/CURRENT`），
  bar-diff 以退出码 0 执行完毕，未跳过。
- 读数（trade_date 20260930）：`daily_bar_symbols=0`、
  `daily_basic_symbols=5561`、`only_in_daily_bar=[]`、
  `only_in_daily_basic` 列出全部 5561（JSON 只保留前 20）。
- 解释约束：本地已发布数据集 `daily_bar` 覆盖 2015-01-05..2026-09-24
  （末日 661 只/日），不含 20260930。故该读数是**数据集覆盖事实**（发布数据
  尚未覆盖探针日），不是端点符号集差异的证据；同日对齐后才可判
  daily_basic 与 daily_bar 的符号集合差异。

### 单日行数 vs ~6000 截断阈值

- 20260930 by-day 5561 行 < 6000：单日全市场一页内完整返回，未观测到截断。
- 与 6000 阈值的贴近度：5561/6000 ≈ 93%；阈值行为本身（恰好 6000 行的
  截断形态）由 index_weight 2010-01 两窗的 6000 行读数旁证（见上）。

### 配额与失败形态

- 本子命令 6 label × 2 transport + 1 次 diff-by-day = 13 次请求，两 transport
  均零错误（daily_basic 上 proxy 6/6 成功），未触发限流。

### 可得性时点观测

- trade_date 20260930 的 by-day 完整帧（5561 行）在 `observed_at_utc`
  2026-10-01T09:14:58Z（北京时间 T+1 17:14，国庆休市日）已可得。
- T 日当日何时可查未观测（探针在 T+1 运行），此点留待后续 dated evidence。

## 末节：冻结去向（建议，裁定权在 owner）

- 单位冻结值建议：`TOTAL_MV_TO_YUAN_MULTIPLIER = 10000`（total_mv 原生
  万元）；`TURNOVER_RATE_TO_RATIO_DIVISOR = 100`（turnover_rate 原生百分数）。
  两者均由 relay by-day magnitude_reference 直接支持并有多历史窗量级旁证
  （见上）。proxy 读数存在 float32 精度损失，不作为单位判定依据。
- 具名端点集合裁定建议：两端点在 relay 上均按契约形态应答——`index_weight`
  （`index_code`+起止窗）返回 `index_code/con_code/trade_date/weight` 且
  weight 非空、百分数量级；`daily_basic` 按 `trade_date`+`fields` 原样返回
  所请 4 列（null 保持 null）、按区间返回全默认列集。proxy 对 index_weight
  退化严重（16/25 ServerError）、对 daily_basic 完整可用。建议将两端点纳入
  `_NAMED_ENDPOINTS`，transport 主用 relay（proxy 的 index_weight 行为差异：
  月窗只回一个快照日；精度损失），供 owner 裁定后记录。
- 指数代码冻结表：本探针的三个代码仅是起点；节奏与形态结论（月度快照、
  单快照行数=成分数、weight 百分数量级）可作为冻结依据的形态部分，代码
  集合本身仍需 owner 按规格 §6.2 裁定。
