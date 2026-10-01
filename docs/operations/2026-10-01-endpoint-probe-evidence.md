# index_weight / daily_basic 端点探针：骨架（待 owner 授权后运行）

- 日期：2026-10-01
- 状态：**awaiting_authorization**。联网子命令（`index-weight`/`daily-basic`）
  一律需要 owner 明确授权（日期窗口、指数代码、请求数上限成文）后方可运行；
  本记录在探针运行并回填结论前不构成任何事实依据。
- 脚本：`project/probe_index_weight_daily_basic.py`（离线 `render` 子命令打印
  全部请求形状，无需授权；联网子命令消耗配额，严禁无授权运行）。
- 结论载体：`2026-10-01-endpoint-probe-evidence.evidence.json`（探针读数由脚本
  按 `probes.index_weight` / `probes.daily_basic` 节回填；`_status` 从
  `awaiting_authorization` 翻为 `measured` 后，方可在实现中回填单位换算常量与
  指数代码冻结表）。本 md 承载结论性叙述；JSON 承载逐条原始读数。
- 纪律：凭据只由 `build_transport` 从环境读取；脚本与记录不读、不打印、不落盘
  任何凭据。空响应绝不解释为"该日无成分/无因子"。

## 待回填小节清单（探针运行后补写）

### index_weight

- 可用性：relay/proxy × 三指数码（`399300.SZ`/`000905.SH`/`000852.SH`——panda
  代码仅为探针起点，非冻结依据）。
- 节奏判定：快照节奏是否可判定（月度快照假设）；不稳定记 `unknown`（spec §6.4）。
- 历史窗口起点：各指数可得数据的最早月份。
- 单快照行数与权重列形态：`con_code` 去重数、`weight` dtype 与 min/max。
- 空响应形态：覆盖窗口之前（1990 探针窗）的返回形态（`None`/无列空帧/带列空帧）。
- 两 transport 差异与配额：relay/proxy 行为差异、失败形态、消耗请求数。

### daily_basic

- 历史窗口起点。
- 按日（`trade_date`）/按区间（`ts_code`+`start_date`/`end_date`）两种取法的
  实测行为。
- `total_mv`/`turnover_rate` 原生单位与空值形态（单位冻结的唯一效力来源；
  ×10000/÷100 只是靶子假设，以实测为准）。
- 同日 symbol 集合与已发布 `daily_bar` 的差异（`--root` diff 读数）。
- 单日行数 vs proxy ~6000 行静默截断阈值（贴近阈值，必测）。
- 配额与失败形态。
- 可得性时点观测：T 日数据当日何时可查，按 `observed_at_utc` 记录。

### 末节：冻结去向

- 单位冻结值（`TOTAL_MV_TO_YUAN_MULTIPLIER`/`TURNOVER_RATE_TO_RATIO_DIVISOR`）
  与量级参考读数（relay 优先；relay 不可用时报 owner 裁定）。
- 具名端点集合裁定（`_NAMED_ENDPOINTS` 是否纳入两端点；依 evidence + capability
  校验，需 owner 裁定并在此记录依据）。
