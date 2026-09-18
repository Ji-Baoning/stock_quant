# 数据供给能力报告：接口级实测与开发路线（2026-09-19）

性质：带日期的调研记录（dated evidence）。本版**取代** 2026-09-18 的初稿——初稿把
"本地 token 积分档位"当成供给约束，前提已被实测推翻。本文所有能力结论均来自
**对本机 `.env` 所配 relay 通道的 37 次直接调用**（2026-09-19）与既有运维记录
（2026-09-11/12/14/15），不使用任何"官方文档说"级别的转述。

接入任何新接口前，以 `.claude/rules/data.md`、`docs/architecture/data-flow.md` 与
`docs/operations/2026-09-12-tushare-proxy-assessment.md` 的信任边界为准。

---

## 0. 摘要：三个事实纠正与一张行动表

**纠正 1 —— relay 就是 Tushare Pro 权限面，不存在"积分档位墙"。**
2026-09-19 实测 relay（jiaoch.top）28+9 个接口：`suspend_d`、三大报表
（带 `ann_date`/`f_ann_date` PIT 字段）、`fina_indicator`、`index_classify`、
`index_member`、`index_member_all`、`stk_limit`、`limit_list_d`、`moneyflow`、
`margin_detail`、`top_list`、`dividend`、`adj_factor`、`daily_basic`、
`stk_holdernumber`、`namechange`、`bak_basic`、`stk_factor`、`cb_basic`
**全部返回数据**。旧报告"升级 Tushare 积分 / 每年 200 元"的建议作废——
所需接口今天就能读。存量记录里"`suspend_d` 无权限"、"`dividend` 零证据"、
"`adj_factor` 无端点"三条结论（PROJECT_MEMORY §8.5、§8.4）**均已过时**，
应视为"经 relay 可得，待接入"。

**纠正 2 —— 退市股全链路可得，幸存者偏差修复有数据路径。**
以 000003.SZ（1995 年退市）实测：`daily` 366 行（含 `pre_close`）、
`adj_factor` 2,668 行全历史、`dividend` 17 行、`income` 71 行（含 PIT 字段）、
`daily_basic` 542 行全部成功。此前 RDS 入口对退市行情报 HTTP 400 的限制**不适用
于 relay**。"25 只退市成员不在池内"（§8.4）的历史回补不再被数据卡住。

**纠正 3 —— proxy 备份通道当前不可用。**
2026-09-12 评估的 298 接口聚合前置，2026-09-19 实测其文档化路径
（`/tushare/capabilities/income`、`/tushare/pro/suspend_d`）均返回 **HTTP 404**
（0.6s 应答：服务在线、路径已变或接口已下线）。RDS 快速入口未复测，沿用 09-12
记录（同源不可互证）。**当前唯一高权限通道就是 relay**，这既简化了拓扑、也意味着
单点风险重新集中（见 §4 风险）。

| 优先级 | 行动 | 服务于 | 数据就绪？ |
| --- | --- | --- | --- |
| P0 | AkShare 新增东财日线 endpoint（独立第二价格源） | 验收工作表 `cross_source_price_sample` 最后一项 + 日线交叉校验单点 | ✅ 通道已通 |
| P0 | 阶段 5b 真实数据验收（操作者人工项） | 解锁一切正式 `research run` | 机制就绪 |
| P0 | `suspend_d` 停牌证据车道设计稿（只读存证先行） | 验收 `date_window_completeness` 1,437 条 + 29 条 unverified | ✅ 实测可得 |
| P1 | 财务车道：`income`/`balancesheet`/`cashflow`/`fina_indicator`（PIT 契约按 `f_ann_date`） | 估值/质量因子（README 既定路线下一步） | ✅ 实测可得 |
| P1 | 行业车道：`index_classify` + `index_member` 快照存证 | 行业中性化、暴露控制 | ✅ 实测可得 |
| P1 | `adj_factor` 与 `adjusted_bar` 一致性互证（只做校验证据） | 复权/公司行为一致性验收项 | ✅ 实测可得 |
| P1 | 退市成员回补设计（daily+adj_factor+dividend 退市链） | 幸存者偏差（§8.4 遗留重审项） | ✅ 实测可得 |
| P2 | `moneyflow`/`margin_detail`/`top_list`/`stk_holdernumber`/`stk_limit`/`daily_basic` 等因子与约束车道 | 策略扩展阶段 | ✅ 实测可得 |

---

## 1. 供给通道拓扑（本仓现状，按信任等级）

```text
                      ┌─ relay (jiaoch.top)  ★唯一高权限通道、发布传输
  Tushare 兼容协议 ───┤     权限面 = Tushare Pro 全量（2026-09-19 实测 37 接口）
                      │     证据地位：与官方逐位一致（4/4）+ 空结果一致（2/2，09-12 探针）
                      └─ 本地直连 api.waditu.com（token 限流档案：stock_basic 5/日、
                            index_daily 1/时；仅作 relay 故障时的降级读路径）

  akshare ── 公司行为双源（cninfo 主 × 东财校验）、指数 fallback 链、广度补充
               （东财/新浪/腾讯/巨潮/同花顺上游；接口稳定性是结构性短板）

  官方渠道 ── 巨潮资讯（公告/公司行为权威，可达）、上交所（可达）、
               中证指数（TCP 通、成分 API 仍 500）、深交所（直连超时）

  停用/观察 ── baostock（数据端口 :10030 停机，09-18 复测仍不通）
               proxy 298 接口聚合前置（09-19 实测 404，不可用）
               RDS datahubco（快速同源第二入口，09-12 实测退市行情 400）
               TDX：pytdxdata 仲裁器（ADR-007 已接线；需 Py≥3.11 独立环境；
                     6 个主站 3 个存活）+ 官方 TdxQuant（2026-01 正式版，观察项）
               QMT/miniQMT：虚拟盘阶段既定路线（实时行情/分钟/tick），未接入
```

各通道角色边界（沿用既有治理，本文只补事实）：

- **relay 是发布传输**：`TUSHARE_TRANSPORT=relay` 显式导出、发布路径不自动回退
  （`tushare_transport.py`，D7 教训）。它承载 Pro 权限是"权限借道"：探针只能证明
  **输出与官方不可区分**，不能证明**上游即官方**（relay-substitution-probe 的
  "证据上界"一节）。因此它的正确用法是：作为 tushare 源的传输层消费既有契约，
  而不是把它当"新增独立源"计入交叉验证的独立性。
- **proxy 的既有定性不变**：数据源（transport），永不是证据源；上游不可溯源 +
  `fallback_on_empty` + 共享 IP 预算（09-12 评估）。当前它 404，连"数据源"都暂不
  是——恢复前一切容量/能力规划不得引用它的 catalog。
- **akshare 是公司行为主源与校验源**，也是独立于 tushare 上游链的第二事实来源
  （东财/巨潮 ≠ tushare 上游），这是它在验收工作表里的价值所在。

## 2. 当前阻塞问题 × 数据解法（对准运维记录逐条）

以下 7 项是 PROJECT_MEMORY §8.4/§8.5 与 2026-09-14/15 运维记录里**当前真实卡住
链路的问题**。每条给：现状证据 → 实测数据能力 → 还差的工程/设计动作。
区分"数据缺口"与"契约缺口"是本节的核心：**7 项里没有一项是数据缺口**。

### B1 · 正式研究发布被验收门卡（所有 research run 的总闸）

- 现状：`data/acceptances/` 注册表为空、无 `CURRENT_ACCEPTED`；CURRENT 已前移到
  `01c74bee…`（ADR-007 仲裁后的重发布版，晚于记忆里 2026-09-15 的 `1d6e43b4…`）。
  任何正式 `research run` 会按 `NoValidAcceptance` FAILED（09-14 定时任务已实测）。
- 数据能力：不缺数据。工作表 9 项已确认 8 项，余下 `cross_source_price_sample`
  需要**独立于 tushare 上游链的第二价格源**做人工比对。
- 解法：**AkShare 新增东财日线 endpoint**（`stock_zh_a_hist` 一类），把"独立第二
  价格源"从人工临时找数变成常设车道——工作表比对、以及 B7 的日线交叉校验共用。
  它与 relay 无共同上游，独立性成立（proxy 那条"同源不可互证"的教训在此不适用，
  因为东财是真正的不同上游）。
- 剩余动作：`AkShareSource` 加一个 endpoint + 走既有 `compare_daily_sources` 阈值
  契约；最后一项人工比对 + `data acceptance publish` 是操作者动作。

### B2 · 停牌证明的 1,437 条 `unknown_or_suspended` + 29 条 `suspension_run_unverified`

- 现状：CURRENT 质量报告的头部 issue（1d6e43b4 实读：`unknown_or_suspended` 1,437
  条跨 29 只，全部落在 2015-01-05..2015-12-07——窗口首日就开着的停牌 run 没有
  `before` 锚点，`suspensions.py` 拒绝落地证明）。这是 09-14 记录里两项自动检查
  FAIL 之一。
- 数据能力：**`suspend_d` 全历史可用**。实测 2015-07-08 单日 1,348 行（股灾停牌潮），
  字段 `ts_code, trade_date, suspend_timing, suspend_type`；按标的窗口形态亦通
  （600654.SH 两行）。已停牌窗口能否闭合，第一次有独立于价格链的证据可查。
- 解法（**契约缺口，非数据缺口**）：新增 `suspend_d` 证据车道是一个 src 设计决定
  （证据只做检查输入，还是落一张 suspend evidence 表？`suspend_type` 语义与旧
  docstring 相反的坑要写进契约）。落地路径照 D 系列的流程：设计规格 → 实现 →
  `tests/unit/test_suspensions.py` 扩展。**先做只读存证**（拉 2015 全窗口 suspend_d
  入 raw 树留证），设计决定可以后置。

### B3 · 公司行为 quarantine/coverage 残余（46 只被窗口前记录拉黑、50 行 cross_source_conflict）

- 现状（09-15 补记）：quarantine 118 行中窗口内 58 行/25 符号，窗口前记录拉黑
  46 只；裁剪后仍有 2 只停在 `FACTS_INCOMPLETE`。
- 数据能力：relay `dividend` 实测可用（000001.SZ 全历史 112 行，含
  `div_proc`/`stk_div`/`cash_div` 全字段；1995 年退市股也有 17 行）。
  这提供了 ADR-007 TDX 仲裁之外的**又一张第三方选票**：conflict 行可以用
  relay `dividend` 复核（同样"输出不可区分" caveat 适用，见 §4）。
- 解法：**设计决定**——是否给 `corporate_actions.py` 的对账链加第三源通道
  （像 ADR-007 那样只仲裁不消费）。在此之前，quarantine 维持隔离不动。
  配股问题（D2/D3：36 条配股记录、31 个"除权日=复牌日"阻断项、复权递推式无配股项）
  是已记录的设计决策，数据侧 cninfo 配股车道已就绪，与 relay 无关。

### B4 · 幸存者偏差：25 只退市成员不在池内

- 现状：`universe.yml` 只保留在市标的；§8.4 明示"幸存者偏差语义仍待重审"。
- 数据能力：**本次实测的关键增量**。退市股 daily/adj_factor/dividend/income/
  daily_basic 全链路经 relay 可得（000003.SZ 五项全通，adj_factor 2,668 行全历史）。
  relay `stock_basic` D 名单 341 只、`delist_date` 全带（与 RDS 09-12 实测的
  100% 覆盖一致）。
- 解法：扩池回补战役可以把"已退市成员"纳入范围（daily + dividend +
  adjusted_bar 重建走既有离线管线）。工程量主要在批量回补与覆盖证据，
  不在任何接口权限。

### B5 · `universe_master_mismatch` 契约把 master 钉死在 30 只

- 现状：09-14 已定位（数据集契约变更，未擅自执行）；全市场 stock_basic（L/D/P
  合并）已验证可行。
- 数据能力：`stock_basic` L=5,568 / D=341 实测正常（今日全量 5,568 行 vs 09-12
  RDS 侧 5,562，合理增长）。
- 解法：配置/契约决策，与数据供给无关。本文只确认：放开后供给侧无新缺口。

### B6 · 官方抽检通道降级（csindex 500、深交所超时、baostock 停机）

- 现状：09-18 本机实测——巨潮/上交所可达，csindex 成分 API 仍服务端 500，
  深交所直连超时，baostock 数据端口仍停机。
- 解法：官方抽检继续走巨潮+上交所；指数成分证据继续走 relay `index_weight`
  （已固化的 `custom_csi300_tw` 谱系）；baostock 复机后原位恢复，不排期等它。

### B7 · 日线交叉校验单点（baostock 停机后校验源空缺）

- 现状：`sources.yml` baostock 禁用后，`compare_daily_sources` 的第二输入缺位。
- 解法：与 B1 共用 AkShare 东财日线 endpoint。注意既有教训：东财对单票窗口的
  返回形态与 tushare 不同（复权口径、停牌表示），契约要按"校验源"写，
  不进 `_CONFIGURED_SOURCES` 的 required 角色。

## 3. 未来开发数据需求 → 接口级映射（全部 2026-09-19 实测）

通用坑（适用所有 relay 调用，接契约前必读）：

- **~6,000 行静默截断**：`daily_basic` 按 34 年窗口取回恰好 6,000 行（真实应有
  ~8,600 行）。凡全历史读取必须按日期窗口切片（与 proxy 同源的已知坑，relay 亦然）。
- **偶发 TLS EOF**：28+9 次调用出现 2 次瞬态 `URLError: EOF occurred in violation
  of protocol`，重试即成。既有 `fetch_with_retry` 覆盖即可，但要确认 SSL 层错误
  在重试白名单里。
- **必填参数形态**：`forecast`/`express` 市场级公告日窗口被拒（"必填参数, 标的"），
  必须按 `ts_code` 逐只调用——批量回补的调用次数按标的数预算。
- **`stock_basic` 空参数只回 L**：取 D/P 必须显式 `list_status`。
- **字段投影**：需要 `fields=` 显式列名，避免全字段拉取的量。

### 3.1 基本面（估值/质量因子——README 既定的下一步研究输入)

| 数据类型 | relay endpoint | 实测 | 历史深度 | PIT 契约要点 |
| --- | --- | --- | --- | --- |
| 财务三表 | `income` / `balancesheet` / `cashflow` | ✅ 各返回数据；000001.SZ 无 period 全历史 **129 行**（1992 起）；退市股 71 行 | 1992 起 | **`ann_date` 与 `f_ann_date` 都在列**。契约必须按 `f_ann_date`（实际公告日）进模型，`end_date`（报告期）只做键；缺失 `f_ann_date` 的旧行用 `ann_date` 兜底并留 WARN |
| 财务指标 | `fina_indicator` | ✅ `roe/eps/grossprofit_margin` + 同样双公告日字段 | 同上 | 同上；与三表按 `(ts_code, end_date, f_ann_date)` 对齐 |
| 业绩预告/快报 | `forecast` / `express` | ✅ 可达（必填 ts_code；000001.SZ 该期 0 行属实） | — | `ann_date` 即 PIT 锚点 |
| 日频估值/市值 | `daily_basic` | ✅ `turnover_rate/pe_ttm/pb/total_mv`（09-12 已验 `total_share/free_share` 同在） | 2000 年起量足（000003.SZ 1994-95 有 542 行） | 日频快照天然 PIT；**注意 6,000 行截断**；`pe/pb` 为当期报告口径，做因子建议用三表 `f_ann_date` 自算分母 |
| 股本变更 | `daily_basic`（total_share/free_share） | ✅（09-12 记录） | 同上 | 送转变动由公司行为车道驱动，`daily_basic` 作日频核验 |

质量评级：**relay 财务线 = 免费档内最高**（结构化、双公告日、深历史、退市股覆盖）。
必做的核验设计：抽样 N 条 `f_ann_date` 对巨潮公告日（既有 cninfo 车道）对账，
这是把"输出不可区分"升级为"PIT 可信"的最低成本路径。

### 3.2 停牌与交易约束（回测真实性 §3.3 直接相关）

| 数据类型 | relay endpoint | 实测 | 说明 |
| --- | --- | --- | --- |
| 停复牌事实 | `suspend_d` | ✅ 单日全市场（2015-07-08 = 1,348 行）+ 按标的窗口；`suspend_timing`/`suspend_type` | 全历史覆盖（akshare 东财停复牌"忽略历史日期"的缺口就此关闭）；`suspend_type` 语义与旧 docstring 相反（09-12 记录），契约要写清 |
| 涨跌停价 | `stk_limit` | ✅ 逐日 `up_limit/down_limit` | 替代"前收盘 ×±10/20%"推导的最直接来源；与推导值互检后进回测约束 |
| 涨跌停榜 | `limit_list_d` | ✅ 82 行（2026-08-28 U 榜） | 一字板/连板情绪因子素材；高积分接口可用进一步佐证权限面 |
| 日线含 `pre_close` | `daily` | ✅ 既有主源 | D4 供应商停牌行规范化已处理其停牌表示 |

### 3.3 行业与参考数据

| 数据类型 | relay endpoint | 实测 | 说明 |
| --- | --- | --- | --- |
| 申万层级结构 | `index_classify` | ✅ SW2021 全层级 **511 行**（L1/L2/L3） | 免费档最完整的行业树 |
| 行业成员（含 in/out 日） | `index_member` | ✅ 3,000 行，`in_date/out_date/is_new` | **自带历史变更**，天然支持时点化行业归属 |
| 申万三级成员（新） | `index_member_all` | ✅（1 次瞬态 SSL 后重试成功） | 与 `index_member` 二选一进契约，先做快照存证 |
| 改名史 | `namechange` | ✅ `ann_date/change_reason` | ST/风险警示历史的旁证 |
| 公司名单/属地/行业 | `bak_basic` | ✅ 5,562 行/日 | 备用名簿（注意它是"备份库"口径，主档仍走 `stock_basic`） |
| 可转债 | `cb_basic` | ✅ 1,165 只 | 远期扩展 |

行业数据接入纪律：成员快照按指数成分证据的标准走（存证 + 内容哈希 + 时点边界），
**不要**把 `in_date/out_date` 直接当成员事实表——它是供应商口径，不是官方公告。

### 3.4 量价与另类因子（策略扩展阶段）

| 数据类型 | relay endpoint | 实测 | 历史深度 |
| --- | --- | --- | --- |
| 资金流 | `moneyflow` | ✅ 20 列分级买卖 | 逐日；窗口切片防截断 |
| 两融明细 | `margin_detail` | ✅ 4,436 只/日 | 2010 起口径 |
| 龙虎榜 | `top_list` | ✅ 64 行/日（2024-01-02）、15 列 | 历史深 |
| 股东户数 | `stk_holdernumber` | ✅ `ann_date/end_date/holder_num` | 按披露日进模型 |
| 技术因子 | `stk_factor` | ✅ 5,000 分档接口可用 | 只建议作交叉校验，不替代自算因子 |
| 复权因子 | `adj_factor` | ✅ 退市股全历史 2,668 行 | **只做 `adjusted_bar` 的互证证据**，`internal_total_return_v1` 唯一口径不变 |

### 3.5 既有通道的广度补充（AkShare，2026-09 现状）

公司行为双源（cninfo 主 × 东财校验 × 同花顺降级）、指数三级 fallback、宏观、
公告文本（巨潮封装）等继续按现状使用。新增建议只有一条（B1/B7 的东财日线）。
2026-01 东财实时/板块接口失效先例（akshare issue #6986）重申其使用纪律：
锁版本、逐接口标注上游、失效面不进 required 角色。

## 4. 质量结论：怎么用才不降低现有质量架构

1. **relay 的证据地位是"输出与官方不可区分"，不是"上游即官方"**。因此：
   - 可以作为 tushare 源的发布传输消费**既有已验收契约**的接口（daily/trade_cal/
     stock_basic/index_daily/index_weight 已在链上）；
   - 新接口（suspend_d/财务/行业…）进正式链路前，必须各自建立**独立核验锚点**：
     财务对巨潮公告日、停牌对价格链推导互证、行业对官方快照、adj_factor 对
     internal_total_return_v1。每个锚点就是一份验收检查项——这正是本仓库
     "数据要可证明、不是看起来对"的既有标准在新数据线上的延伸。
2. **通道单点风险重新集中**：proxy 404、RDS 未复测、baostock 停机之后，高权限
   读取只剩 relay 一条。缓解不靠再加同源入口（09-12 已证明同源不可互证），靠：
   本地直连 `api.waditu.com` 作降级读路径（限流档案在案）、relay 故障时
   `data update` 的既有语义（不发布、CURRENT 不动）本来就是安全网。
3. **`adj_factor` 与 `adjusted_bar` 的关系**：内部全收益口径是唯一复权事实；
   `adj_factor` 只用于一致性核对（比值 vs 内部递推因子，抽窗比对）。任何偏离进
   quarantine 语义复核，不做自动修正。
4. **退市股数据的一个已知边界**：relay 对退市股行情无障碍（实测），但 RDS 对同一
   请求 HTTP 400——说明**不同入口的上游能力不同**。凡依赖退市链的战役，
   以 relay 实测为准并留存证；不得假设"另一个 Tushare 入口也行"。
5. **供应商归属语义**：新接口全部仍属 `tushare` 供应商（KNOWN_SUPPLIERS 不变），
   但**每个新接口进 published 表都要新契约**（schema、主键、覆盖语义、
   `_coverage_verdict` 的失败面），D2 的教训（"不是加个接口"）适用于每一行
   §3 的表。

## 5. 分阶段行动清单

### P0（当前阻塞，1–2 个工作日 + 操作者动作）

1. **AkShare 东财日线 endpoint**（B1+B7）：`AkShareSource` 新增 endpoint +
   `compare_daily_sources` 契约 + 单测；产出 = 验收工作表最后一项的比对数据 +
   常设日线校验源。不动 `_CONFIGURED_SOURCES` 角色。
2. **阶段 5b 验收 publish**（操作者）：对 CURRENT `01c74bee…` 完成工作表终项 →
   `data acceptance publish` → `CURRENT_ACCEPTED` 落盘。此后 walk-forward 与
   一次性挑战的发布路径才开闸。
3. **`suspend_d` 只读存证**（B2 前置）：把 2015 全窗口（及抽查年）suspend_d 拉
   入 raw 树留证，作为设计输入；src 契约变更单独立项（新增证据车道 or 检查项，
   需要一次设计决定）。

### P1（研究扩展前置，1–2 周）

4. **财务车道**：`income`/`balancesheet`/`cashflow`/`fina_indicator` 四接口契约
   （`f_ann_date` PIT、6,000 行切片、退市股含入）+ 巨潮公告日抽检核验 + raw 存证。
   产出 = 估值/质量因子的合法输入层。
5. **行业车道**：`index_classify` + `index_member` 快照存证 + 时点归属事实表
   （对齐 universe_membership 的证据标准）。
6. **`adj_factor` 互证**：抽窗比对脚本 + 偏离进复核清单；服务复权一致性验收项。
7. **退市成员回补设计**（B4）：按 §2 B4 的实测路径写扩池战役设计稿
   （含 25 只退市成员 + `stock_basic` D 名单），复用既有离线重建管线。

### P2（策略扩展阶段按需拉线）

8. `stk_limit`（涨跌停价进回测约束，替代推导）、`daily_basic`（市值/换手进组合层）、
   `moneyflow`/`margin_detail`/`top_list`/`stk_holdernumber`/`namechange` 另类因子线、
   `cb_basic` 可转债、QMT 实时与分钟（虚拟盘阶段既定路线）。
9. 观察项：proxy 路径迁移后复活重评（其"数据源非证据源"定性不变）、baostock 复机
   回位、TdxQuant 官方化替代 pytdxdata 的可行性。

---

## 6. 附录 A：relay 接口实测明细（2026-09-19，本机，37 次调用）

| endpoint | 参数形态 | 结果 | 关键字段/备注 |
| --- | --- | --- | --- |
| daily | 单票月窗 | OK 21 行 | 含 pre_close |
| index_daily | 000300.SH 月窗 | OK 21 行 | |
| stock_basic | L / D / 全量 | OK 5,568 / 341 / 5,568 行 | 空参数只回 L；D 带 delist_date |
| trade_cal | SSE 月窗 | OK 30 行 | |
| **suspend_d** | 单日 / 单票窗 | OK 7 行 / 1,348 行(2015-07-08) / 2 行 | suspend_timing, suspend_type |
| **income** | period / 全历史 / 退市 | OK 1 行 / **129 行** / 71 行 | ann_date, f_ann_date |
| **balancesheet** | period | OK 2 行 | ann_date, f_ann_date |
| **cashflow** | period | OK 1 行 | ann_date, f_ann_date |
| **fina_indicator** | period | OK 1 行 | roe, eps + 双公告日 |
| forecast | period / 公告日窗 | OK 0 行 / REJECT(必填标的) | 必须按 ts_code |
| express | period / 公告日窗 | OK 0 行 / REJECT(必填标的) | 同上 |
| **index_classify** | SW2021 | OK **511 行** | L1/L2/L3 全层级 |
| **index_member** | 单票 | OK 3,000 行(市场级) | in_date, out_date, is_new |
| index_member_all | 单票 | OK（1 次瞬态 SSL EOF→重试成功） | |
| **stk_limit** | 月窗 | OK 21 行 | up_limit, down_limit |
| **limit_list_d** | 单日 U 榜 | OK 82 行 | |
| **moneyflow** | 月窗 | OK 21 行 × 20 列 | |
| **margin_detail** | 单日 | OK 4,436 行 | |
| **top_list** | 单日 | OK 57 行 / 64 行(2024-01-02) | |
| **dividend** | 单票 / 退市股 | OK 112 行 / 17 行 | div_proc, stk_div, cash_div 全字段 |
| **adj_factor** | 月窗 / 退市股全历史 | OK 21 行 / **2,668 行** | |
| **daily_basic** | 月窗 / 34 年 | OK 21 行 / 6,000 行=**截断** | pe_ttm, pb, total_mv, turnover_rate；退市股 1994-95 有数据 |
| **stk_holdernumber** | 年窗 | OK 10 行 | ann_date |
| namechange | 单票 | OK 8 行 | ann_date, change_reason |
| **bak_basic** | 单日 | OK 5,562 行 | |
| **stk_factor** | 月窗 | OK 21 行 | |
| **cb_basic** | 全量 | OK 1,165 只 | |
| **退市股 daily** | 000003.SZ 1994-95 | OK **366 行** | 退市行情无障碍（RDS 上曾 400） |

平均时延 2.4–6.9s；2 次 `URLError SSL EOF`（重试即成）。

## 7. 附录 B：通道现状快照

- relay：唯一高权限通道，发布传输（显式 `TUSHARE_TRANSPORT=relay`）。
- proxy（jiaoch 网关另一形态/聚合前置）：`/tushare/capabilities/*`、
  `/tushare/pro/*` 均 **404**（2026-09-19），09-12 评估的 298 接口能力面暂停可用。
- 本地直连 `api.waditu.com`：443 通；token 限流档案见 PROJECT_MEMORY §8.5。
- baostock：:10030 超时（2026-09-18 复测，停机未复）。
- 官方：巨潮/上交所通；csindex 成分 API 500；深交所直连超时（09-18 实测）。
- TDX 主站：6 探 3 活（115.238.90.165 / 124.71.187.122 / 180.153.18.170:7709）。
- 验收注册表：`data/acceptances/` 空，无 `CURRENT_ACCEPTED`；CURRENT =
  `01c74bee…`（ADR-007 仲裁后版本）。

## 8. 附录 C：外部参考

- Tushare 接口文档与积分口径：https://tushare.pro/document/2
- AKShare 文档：https://akshare.akfamily.xyz/data/stock/stock.html ；东财接口失效先例：
  https://github.com/akfamily/akshare/issues/6986
- BaoStock：https://www.baostock.com
- 通达信 TdxQuant（官方量化平台）：https://help.tdx.com.cn ；mootdx：
  https://github.com/mootdx/mootdx
- miniQMT/XtQuant：https://miniqmt.com
- 本仓既有评估（本文结论的对照基线）：
  `docs/operations/2026-09-12-tushare-proxy-assessment.md`、
  `docs/operations/relay-substitution-probe-2026-09-12.md`、
  `docs/operations/2026-09-14-blocking-gap-root-cause.md`、
  `PROJECT_MEMORY.md` §8
