# 数据架构全景图（2026-09-19 快照）

性质：带日期的可视化记录。**规范源仍是 `docs/architecture/data-flow.md` 与
`docs/architecture/invariants.md`**；本文是按当前代码与已定稿规格
（`2026-09-19-data-type-expansion-architecture-design.md`）绘制的全景快照，
§1–§8 为已验证的当前事实，§9 为已定稿、待实施的目标态（明确标注"规划"）。
事实变化时先改规范源，本文随之重绘或作废。

---

## 1. 全景总览（自上而下即数据生命周期）

```text
┌─ ① 供给通道（全部免费）──────────────────────────────────────────────
│   主通道                    校验/仲裁通道                官方权威（抽检/锚）
│   ──────                    ────────────                ────────────────
│   tushare relay             akshare 公司行为双源          巨潮资讯（公告权威）
│    (jiaoch.top) ★发布传输    ├ cninfo 分红/配股(主)       上交所（可达）
│    daily · index_daily      └ eastmoney 分红(校验)       深交所（直连超时）
│    stock_basic · trade_cal  akshare 指数三级fallback     中证指数（API 500）
│    index_weight(成分证据)    （east→sina→tencent）        申万研究官网（待验证锚）
│    （Pro 权限面还含:         baostock 校验源（停机禁用）    QMT/miniQMT
│     suspend_d·财务三表·      TDX xdxr 仲裁器               （虚拟盘阶段规划，
│     行业·两融·龙虎榜…）       （ADR-007，停用待启用）        实时/分钟/tick）
│   本地直连 api.waditu.com（降级读路径，token 限流档案在案）
│
│   传输治理：发布构建必须显式 TUSHARE_TRANSPORT=relay，永不自动回退；
│   proxy 聚合前置当前 404、RDS 同源不可互证——均不进发布路径
                                    │
                                    ▼  supplier adapter（base.py 契约：
┌─ ② 取数与原始存证 ────────────      DataRequest/FetchResult/RetryPolicy/
│   python -m stock_quant             validate_supplier_frame，重试/超时治理）
│   data update --root project
│
│   原始树按源分离、内容寻址、永不互写：
│     data/raw/tushare/   data/raw/akshare/   data/raw/baostock/(停用)
│     data/raw/csi/(官方成分快照)   data/raw/tdx/(仲裁证据)
│   每轮 raw snapshot 哈希写进 manifest 的 build_config.raw_snapshots
│   —— 这份证据就是后续漂移审计（§9 B2）要比对的锚
                                    │
                                    ▼
┌─ ③ 校核与规范化（data_quality × data_model）────────────────────────
│   原始检查   schema / 主键冲突 / 数值(OHLC·量额) / 溯源(KNOWN_SUPPLIERS)
│   规范化     → 规范字段集 trade_date·symbol·OHLC·volume·amount·source…
│   停牌证明   pre_close 链推导 + 供应商停牌行规范化(source=tushare_suspend)
│   跨源比较   compare_daily_sources（OHLC 阈值分级，冲突留证不平均）
│   公司行为   cninfo × eastmoney 逐字段对账
│                ├ 一致 → corporate_action 入账
│                ├ 冲突 → corporate_action_quarantine 隔离留证
│                │         └ reviews.yml 人工裁定 / TDX 第三票仲裁(ADR-007)
│                └ 覆盖判定 _coverage_verdict: 每 symbol/window →
│                    VERIFIED / VERIFIED_EMPTY / UNTRUSTED
│                    （任一被请求端点失败即 UNTRUSTED；降级不阻断发布）
│   复权序列   adjusted_bar = 未复权收盘 + 已采信公司行为
│                口径恒为 internal_total_return_v1（唯一）；
│                不可信断点 → 跨越它的动量窗口无效，绝不静默回退
                                    │
                                    ▼
┌─ ④ 质量门禁与发布（单写者、fail-stop）───────────────────────────────
│   evaluate_publication：15 个阻断码（schema/PK/OHLC/溯源/隔离理由/
│     复权血缘/主源缺口…）任一命中 → PublicationBlocked，
│     staging 移除、无新版本、CURRENT 不动
│   通过 → staged/ ──os.replace──→ data/standardized/<dataset_version>/
│     内容寻址、不可变；相同输入重发布 = no-op
│     附 dataset_manifest.json（表哈希+build_config+成员定义哈希）
│      与 quality_report.json；CURRENT 指针原子替换
│   9 张表：daily_bar · trading_calendar · security_master(+coverage)
│     corporate_action(+quarantine+coverage) · adjusted_bar
│     universe_membership
│   读取：按版本的 DuckDB 只读 catalog；运行期钉版本、绝不跟 CURRENT 漂移
                                    │
                                    ▼
┌─ ⑤ 时点宇宙证据链（与 ④ 并行汇入发布）───────────────────────────────
│   官方月度成分快照(data/raw/csi/index_weight/，经 relay 逐月取回)
│     → data index-membership prepare（离线导入）
│     → attested-boundary 成员事实（逐条绑定快照 SHA-256）
│     → 随数据集发布（universe_membership 表）
│     → configs/universes/custom_csi300_tw_tradable.yml 钉成员表内容哈希
│     → universe_version = 定义内容哈希（进实验身份）
└──────────────────────────────────────────────────────────────────────
                                    │
                                    ▼
┌─ ⑥ 真实数据验收（阶段 5b；操作者人工项所在）─────────────────────────
│   data acceptance prepare / publish / show
│   内容寻址注册表 data/acceptances/（real-data-v1 规则）
│   自动检查（如 date_window_completeness、corporate_action_evidence）
│     + 人工工作表（cross_source_price_sample 等九项）
│   REJECTED 记录先原子落盘、再非零退出——失败也留证
│   → CURRENT_ACCEPTED 指向最新有效 ACCEPTED（⑦ 的闸门）
└──────────────────────────────────────────────────────────────────────
                                    │
                                    ▼
┌─ ⑦ 正式研究（research run 是唯一正式发布者；顺序即闸门）─────────────
│   load spec → 钉数据集版本(恰一次) → 宇宙预检(证据门,失败即停,无绕过)
│     → 真实数据验收门 → 冻结规格 + 3 快照(策略/实验/数据环境)
│     → experiment_id(身份方案 v2)
│     → 因子（只吃时点可用数据；momentum_60d v2 只消费 adjusted_bar）
│     → 组合（buffered_risk_weighted 预注册冻结参数）
│     → 回测 walk_forward_oos_v1（年度 fold、T+1 账户、三成本情景、
│        stability-v1 判定）
│     → 原子发布 data/experiments/<experiment_id>/
│   工程旁路：backtest --engineering → data/runs/debug，
│     恒记 UNTRUSTED，永不能产出 ACCEPTED 实验
│   一次性挑战：research challenge（预注册声明 → holdout 原子消费
│     → 配对比较 → PROMOTED/REJECTED/…，消费不可逆）
└──────────────────────────────────────────────────────────────────────
                                    │
                                    ▼
┌─ ⑧ 报告 ────────────────────────────────────────────────────────────
│   report build：只从已提交产物渲染自包含静态 HTML（Jinja2+Plotly）；
│   渲染时零取数、零重算——报告永远是已发布实验的忠实视图
└──────────────────────────────────────────────────────────────────────
```

## 2. 「计划」层：规格目标态（已定稿待实施，非当前事实）

```text
┌─ ⑨ 拓展架构（spec 2026-09-19-data-type-expansion-architecture-design）──
│   B0  契约注册：sources.yml 增 data_contracts 段
│         （tier / 锚点 / 冲突语义 / pit / incremental 五要素）
│         + 存量 9 张表全部回填 tier=core
│         + 发布时逐表校验，缺声明 FATAL unregistered_table（限 data_update 来源）
│   B1  三档证据门禁：core(阻断) / anchored(降级+消费端拦截,复用 trust.py)
│         / research_only(恒不进正式研究)；(code × tier) 矩阵，全局进程码恒阻断
│         + ADR-010（档位门禁）+ ADR-011（验收窗口换锚
│            full_history_acceptance_start 单独作锚，退役 RUNBOOK:80-84 的坑）
│   B2  更新经济学：每表 incremental 策略取数窗（CLI 窗=最小公共窗口；
│            显式 --start = 跳过+NOT_FETCHED 留痕）+ 表级取数覆盖入 manifest
│         + 季度漂移审计（重取 raw 快照比 build_config.raw_snapshots 哈希）
│   B3  端点描述符套件（只服务新端点；帧校验/截断守卫/provenance 生成）
│         + 财务 PIT as_of 访问器（纯函数钉版本）+ Factor.inputs 表级声明
│   D6  月度通道金丝雀（官方直连实测；结果脱敏落 docs/operations/；
│         禁止自动切换降级路径）
└──────────────────────────────────────────────────────────────────────
```

## 3. 贯穿全流程的硬不变量（图上每一层都在执行）

1. **原始证据不可变**：raw 树内容寻址、按源分离；发布数据集内容寻址、
   单写者；`CURRENT` 只进不退。
2. **冲突不平均**：跨源差异留证（quarantine / quality issue），按字段指定
   主源；重要事实走人工裁定或第三方仲裁。
3. **fail-stop 无绕过**：源失败/门禁拒绝/预检失败/验收失败全部"先留证、
   后失败"，没有任何 override 开关。
4. **复权口径唯一**：`internal_total_return_v1` 是唯一复权事实；外部
   adj_factor 只能做校验证据。
5. **时点纪律**：成分按 attested 边界、财务按披露日（规划中 PIT 层）、
   研究只读钉住版本。
6. **研究/工程隔离**：`research run` 恒 RESEARCH 模式；工程诊断恒
   UNTRUSTED，两者产物物理分离（experiments/ vs runs/debug）。
