# 阶段一真实数据验收（operator acceptance）

本文档是**操作者（operator）验收清单**：在用户本机具备网络与自备凭证后，用真实
供应商小窗口数据验证阶段一的接口连通与数据契约。它补充 [README](../../README.md)
（README 记录六个 CLI 命令、时点化指数成分工作流与数据目录），不重复整篇文档。

工程验证（engineering-validation MVP）目标仅是“链路能连通、契约能核对、门禁能
给出可解释结论”，**不声称验证数据绝对正确，不构成投资建议或盈利/实盘就绪声明**。

默认 `pytest`（离线）**绝不联网、绝不读取 Token**；`external` / `smoke` 标记的
测试默认被 `pyproject.toml` 的 marker 表达式 `-m "not external and not smoke"`
剔除，需操作者显式运行。

## 1. 前置条件

- 已创建仓库环境：`conda env create -f environment.yml`（`stock-quant`）。
- 供应商 SDK 已安装（tushare / akshare / baostock），本机可访问其服务。
- 自备**轮换后的** Tushare Token，仅以环境变量注入，写入 `configs/`、`.env`、
  或任何提交物均属违规：

```bash
export TUSHARE_TOKEN=<your_rotated_token>   # 仅环境变量，绝不入库
```

- 工作目录下已存在一个**已发布数据集**（携带 `security_master` 与
  `trading_calendar`）。`data update` 只能“扩展”既有数据集；**首个
  security_master/calendar 基线数据集**由 `python -m stock_quant data bootstrap
  --root <ROOT>`（或等价的直调脚本 `bootstrap_seed.py`）引导发布。最小可复制的
  引导写法见 `tests/smoke/test_small_market_download.py::build_smoke_project`
  （把 configs/universe.yml 的 30 只样本换成目标 universe，日历用目标区间开市日），
  与 `tests/integration/conftest.py::build_fixture_project` 同一技法。

## 2. 命令总览

| 目的 | 命令 | 说明 |
| --- | --- | --- |
| 外部契约（真实小窗口） | `pytest -m external -v` | 三个供应商适配器的原始帧契约 |
| 冒烟（单股小窗口全链路） | `pytest -m smoke -v` | 真实 `data update` 语义、PASS/BLOCK 均可接受 |
| 数据更新 | `python -m stock_quant data update --start 2024-01-01 --end 2024-12-31 --root <ROOT>` | 拉取并入原始库→清洗→门禁→发布；打印 `run_id`、`resolved_end_date`、`resolved_end_is_fallback`、来源状态、`dataset_version`，发布成功打印 `PASS` |
| 数据校验 | `python -m stock_quant data validate [--version <VERSION>] --root <ROOT>` | 对指定/当前数据集重跑共享质检；打印摘要与 `PASS` |
| 指数成分导入（离线） | `python -m stock_quant data index-membership prepare --universe-id csi300 --input <快照文件> --snapshot-sha256 <64HEX> --source-document-sha256 <64HEX> --source <来源> --source-url <无凭证URL> --effective-date <ISO> --announcement-date <ISO> --output <帧文件>` | 把已存储的官方成分快照规范化为带证据哈希的不可变 `universe_membership` 事实帧；打印 `membership_table_sha256=`（冻结定义必须钉住的哈希）。缺任一证据哈希 = usage error；无网络、无绕过 |
| 验收准备 | `python -m stock_quant data acceptance prepare --version <VERSION> --operator <OPERATOR> --output <YML> --root <ROOT>` | 为指定数据版本生成脱敏清单：自动项当场离线重跑，人工项全部初始 FAIL（见 §7） |
| 验收发布 | `python -m stock_quant data acceptance publish --checklist <YML> --root <ROOT>` | 重跑自动检查并重算全部哈希后落盘 ACCEPTED/REJECTED；拒绝先落盘再以非零退出并打印 `reason=`（见 §7） |
| 验收查询 | `python -m stock_quant data acceptance show --version <VERSION> --root <ROOT>` | 按时间升序列出验收历史与失败原因；无记录打印 `UNACCEPTED`；损坏记录命名并以非零退出（见 §7） |
| 正式研究（唯一发布者） | `python -m stock_quant research run --spec configs/experiments/momentum_60d.yml --root <ROOT>` | 冻结规格端到端运行并内容寻址发布；打印 `experiment_id=`；运行前先做 `universe_acceptance` 成分证据预检（见步骤 E），且前置要求该数据版本持有有效 ACCEPTED 验收记录（见 §7） |
| 报表 | `python -m stock_quant report build [--experiment <ID>] --root <ROOT>` | 渲染实验 HTML 与当前数据质量 HTML（实验默认取最新发布；`data/reports/<experiment_id>.html`、`data/reports/quality-<version>.html`） |

`data update` / `data validate` 任一失败路径以非零退出并打印 `FAILED: ...`；被门禁
阻止的更新不改变已发布数据集。

## 3. 验收流程

### 步骤 A：离线基线（无需网络/凭证）

```bash
conda run -n stock-quant python -m ruff check .
conda run -n stock-quant python -m pytest -q          # external/smoke 保持 deselected
conda run -n stock-quant python -m pytest --collect-only -q tests/external tests/smoke -m 'external or smoke'
```

预期：离线全部通过；外部/冒烟测试可正常收集且默认被剔除。

### 步骤 B：外部契约

```bash
conda run -n stock-quant python -m pytest -m external -v
```

逐个验证真实原始帧的列名/类型/非空/日期落在请求窗口内。Tushare 缺 Token 时该用例
自动跳过。**任何契约违反（列缺失、空响应、符号/日期不在窗口、认证失败）会以用例
失败暴露**——这是供应商 schema/SDK 漂移的“诊断信号”，见第 4 节 6(c)。

### 步骤 C：冒烟

```bash
conda run -n stock-quant python -m pytest -m smoke -v
```

对 `600000.SH` 与约一周的近期小窗口跑完整 `DataPipeline.update`（真实适配器、写入
原始库、合并、质检、门禁）。测试契约：

- 三个来源 `source_status` 齐全；
- 门禁结论 ∈ {`PASS`, `BLOCK`}；
- `dataset_ref is not None` ⇔ 结论为 `PASS`；
- 若 `PASS`，读取发布的 `daily_bar` 确认窗口内确有该股行（`adjusted_bar` 的口径核对
  见第 4 节第 5 项：发布的 `adjusted_bar` 应只有
  `adjustment=internal_total_return_v1`）。

**`BLOCK` 是合法诊断结果**（例如 AKShare 端点/符号列漂移、BaoStock 可选来源失败，
均被管线转为可解释的 issue/status，而不是让测试崩溃）；只有逃逸出管线的 schema 或
认证**异常**、或“无发布却判 PASS”（环境/凭证配置错误）才使测试失败。

### 步骤 D：CLI 数据更新与校验

在已引导出基线数据集的项目上：

```bash
python -m stock_quant data update --start <FIRST> --end <RECENT> --root <ROOT>
python -m stock_quant data validate --root <ROOT>
```

记录 `run_id` 与 `dataset_version`。`data update` 打印的 `resolved_end_date` 与
`resolved_end_is_fallback` 体现“latest-complete-date 规则”（§14）：当任一必需数据
角色未达最新开市日时回退到上一个确认完整交易日并注明。

### 步骤 E：指数成分（csi300）证据导入、定义冻结与预检

正式 `research run` 的候选集不再是 `security_master` 全量标的：因子在每个信号日
先按时点化 `csi300` 成分过滤。该链必须按序完成，任何一步证据不足即停，**没有
绕过开关**：

1. **来源与原始快照**：取得中证指数公司官方成分公告（开源 `index-constitution`
   类项目可用于交叉核对，但不能替代官方证据），把原始快照存入 `data/raw/csi/...`
   （不入库），记录文件 SHA-256 与无凭证 `source_url`。
2. **离线导入**：`data index-membership prepare`（或等价的
   `project/refresh_index_membership.py`）以**必填**的
   `--snapshot-sha256` / `--source-document-sha256` / 来源 / 日期 / 理由参数把
   快照规范化为不可变事实帧，打印 `membership_table_sha256=`。缺哈希 =
   usage error（非零退出并点名缺失参数）。
3. **数据集发布**：把事实帧作为 `universe_membership` 表并入下一个数据集版本
   一次性发布；之后每次 `data update` 原样携带，`data validate` 复审（缺证据行
   或篡改哈希 = FATAL）。
4. **定义哈希**：用真实值填写 `configs/universes/csi300.yml`（事实表内容哈希、
   证据摘要哈希、数据集**实际**覆盖区间）。仓库内的占位模板不能通过正式运行。
5. **预检验证**：`research run` 在因子之前执行 `index_membership_evidence`
   预检。**证据缺失（`UNIVERSE_EVIDENCE_MISSING`）、成分数量不对
   （`UNIVERSE_MEMBER_COUNT_MISMATCH`）、退市边界不确定、公告先视、覆盖断裂、
   定义哈希与数据集成分表不符——任一命中都以 `universe_acceptance` 失败**：
   非零退出、打印 `FAILED: research run failed at stage universe_acceptance`、
   在 `data/runs/run_preflight_<hash>/universe_preflight.json` 留下仅含
   status/failed_stage/error_codes 的 redacted 清单，不产出因子、不回退全量
   master。更正只能作为带证据的新事实版本重新发布，不得原地改写。

### 步骤 F：研究复现与报表

```bash
python -m stock_quant research run --spec configs/experiments/momentum_60d.yml --root <ROOT>   # 运行两次
python -m stock_quant report build --root <ROOT>
```

同一冻结规格运行两次应得到相同 `experiment_id` 与逐字节一致产物（内容寻址）。打开
两份 HTML 核对：实验报告（三种成本情景净值、两个基准、回撤/换手/成本拆分/持仓/
公司行为流水与限制）、数据质量报告（来源状态与版本、行列数、缺失/非法、隔离原因、
当前版本、门禁结论与来源）。

## 4. 接受一份“活”数据集前的核对清单

按数据集版本与实验 ID 记录结论；**绝不把市场数据文件拷进 Git**。

1. **最新完整日期规则**：核对 `data update` 的 `resolved_end_date` /
   `resolved_end_is_fallback` 与官方交易所日历一致（目标区间内停市日无误判）。
2. **来源行数**：对照 `data/standardized/<version>/daily_bar.parquet` 与原始库
   `data/raw/<source>/<endpoint>/<request_key>/data.parquet` 的行数与窗口交易日数。
3. **隔离/缺失原因**：阅读质量报告 issue 的缺失行分类原因
   （`not_listed`/`delisted`/`non_trading_day`/`unknown_or_suspended`/
   `primary_source_missing`/`quarantine_missing_reason` 等）；停牌与上市前缺口应得到
   解释而非“未知”。
4. **跨源最大差异抽查**：同一 (security, trade_date) 的主源与校验源收盘比较；阈值见
   §13.4（绝对差 ≤ ¥0.01 为 INFO，相对差 > 0.05% 为 WARNING，收盘相对差 > 0.20% 为
   ERROR）。阶段一管线不把 BaoStock 行并入 `daily_bar`，抽查时直接从原始库取两帧比对。
5. **复权抽查（adjusted_bar 口径）**：发布的 `adjusted_bar` 只应有
   `adjustment=internal_total_return_v1` 一种口径；抽查同 (symbol, trade_date) 的
   `raw_close` 与 `daily_bar.close` 一致、`applied_action_ids` 能回溯到
   `corporate_action` 标准记录；隔离/覆盖断点日应为 `quality_severity=ERROR` 且
   `invalid_reason` 有解释（不可信公司行为使跨越它的动量窗口无效，系统不会静默
   回退未复权收盘）；口径说明见第 5 节。
6. **供应商原始帧忠实度**：
   - (c) AKShare EM `index_history` 真实载荷**通常无符号列**——跨源核对须按请求
     顺序映射，而不是按符号列匹配；若当前 akshare 已改名 EM 指数接口
     （`stock_zh_index_hist_em` 在新版消失），基准角色会暴露为 BLOCK/契约失败，
     需先解决版本/映射再验收。
   - 沪深主/创业/科创/ST 各自的日频 OHLC 单位与 tushare（手/千元）换算复核。
7. **公司行为现场核对**：cninfo/东财分红送转明细与上市公司公告逐条对照；特别核对
   **紧凑配股措辞**（如“10配3”类）是否被现有子串标记算法漏匹配（任务 5 已知小项
   (d)），冲突项进入 `corporate_action` 或隔离清单并记录原因。
8. **公司行为可信覆盖证据核验**：研究运行的不可信回退结论取决于数据集是否携带覆盖
   证据，而非“公司行为表恰好为空”。验收活数据集时应抽查证据表
   `data/standardized/<version>/corporate_action_coverage.parquet`：每只股票池标的在
   验收窗口内都应有 `VERIFIED`/`VERIFIED_EMPTY` 行（含 window_start/end、reason、
   sources、checked_at），而不是 `UNTRUSTED`/`SOURCE_NOT_REQUESTED`。对任意冻结实验，
   可读 `data/experiments/<id>/metrics.json` 的 `metrics.corporate_action_trust`
   复核其 mode、dataset_version、window 与 trusted/reasons 记录。
9. **两个基准覆盖**：`configs/project.yml` 的 `benchmark_symbols`
   （`000300.SH`、`000905.SH`）在发布 `daily_bar` 中均有覆盖；逐窗口核对基准与
   tushare 主序列都完整才把该日视为“完整交易日”。
10. **历史涨跌幅时间表（与官方来源核对）**：`configs/trading_rules.yml` 各行生效日期
    与比例，对照交易所当时官方规则再用于真实数据验收——创业板普通股票
    2020-08-24 起 10%→20%、科创板开板（2019-07-22）起 20%、主板 ST/*ST 5%，并确认
    ST 状态为**按生效日（effective-dated）**解析、规则行带文档化生效日期。
11. **日历与官方日历核对**：把 fixture/真实日历与官方交易所日历对照验收日期区间。
12. **秘密扫描（接受活数据前最后一步）**：

```bash
git grep -nE '(TUSHARE_TOKEN=.{8,}|[A-Za-z0-9]{32,})' -- . ':!docs/superpowers'
```

    任何命中人工复核；已提交文件中不得出现凭证或疑似凭证长串。
13. **证券主数据现场核对**：活数据集的 `security_master` 中 `list_date`/`delist_date`/
    `list_status` 应可逐标的回溯到 tushare `stock_basic` 快照：抽查
    `data/standardized/<version>/security_master_coverage.parquet`——每只股票池标的应
    恰有一行（`source=tushare.stock_basic`、`snapshot_sha256` 对应原始快照哈希）。
    研究冻结以「每标的行存在」为 VERIFIED 前提；缺行/空表/旧数据集在 RESEARCH 模式会被
    拒绝（逐标的 `SOURCE_NOT_REQUESTED`），bootstrap 空种子恒被拒。对已发布数据集执行
    `data validate` 复核「事实 vs 行情边界」WARNING 与「coverage↔master 一致性」FATAL。
14. **指数成分证据与冻结定义核对**：活数据集若携带 `universe_membership` 表，逐条
    抽查事实能回溯到已存储的官方快照与公告（`snapshot_sha256` /
    `source_document_sha256` 与 `data/raw/...` 中文件的实际哈希一致、
    `source_url` 可打开且无凭证、区间闭区间语义正确、同标的事实区间无重叠）；
    `configs/universes/csi300.yml` 必须钉住**已发布事实表**的内容哈希与数据集
    **实际**覆盖区间（从版本 manifest 读，不写目标区间）。对任意冻结实验，从
    `data/experiments/<id>/metrics.json` 的 `meta.universe`（universe_id /
    universe_version / membership_table_sha256 / coverage）与
    `meta.universe_daily_snapshots`（每信号日成员快照哈希）复核至原始证据。

## 5. 阶段一已知限制（对齐操作者预期）

- **复权口径（momentum_60d v2）**：动量 v2 只消费不可变 `adjusted_bar` 表
  （`adjustment=internal_total_return_v1`，由未复权收盘与已核验现金分红/送股/转增
  事件导出）；订单、成交、涨跌停判断与账户估值继续使用未复权 `daily_bar` 价格。
  在 `adjusted_bar` 之前创建的数据集仍可审计，但不能运行 v2 研究实验——跑一次完整
  `data update` 发布兼容数据集。不可信公司行为断点使跨越它的每个动量窗口无效；
  系统绝不静默回退到未复权收盘价。每次成功 `data update` 都会重建并发布
  `adjusted_bar` 与 `corporate_action_quarantine`。复权/公司行为一致性已实现，
  等待真实数据验收。
- **BaoStock 仅为可选校验来源**：因子层不消费 BaoStock *复权*序列；研究运行因子的
  适配器只读数据集内的 `adjusted_bar`。BaoStock 失败仅记为 WARNING，不阻断发布。
- **`data bootstrap` 发布首个基线；`data update` 只扩展**：见第 1 节，
  `python -m stock_quant data bootstrap` 发布 `security_master`/`trading_calendar`
  （+空 `daily_bar`/`corporate_action`/`adjusted_bar`/`corporate_action_quarantine`）
  基线；`data update` 只扩展已有数据集。
  `bootstrap_seed.py` 是与该 CLI 等价的直调脚本。
- **tushare `stock_basic` 快照是默认“仅上市（L）”参照**：universe 若含已退市/长期停牌
  样本，会以 `master_snapshot_incomplete` 形式暴露——本期 30 只固定上市样本下属预期
  行为。
- **`--engineering` 仅限诊断，永不构成正式绩效（mode-level）**：正式研究 `research run`
  无此开关（不可降级绕过证据门禁）；`backtest momentum_60d --engineering` 即使覆盖证据
  可信也**永不发布 ACCEPTED**——评价恒为 UNTRUSTED、实验清单 REJECTED（理由为
  diagnostic-only；覆盖证据不足时理由改为点明数据 UNTRUSTED + 原因/标的）。stdout 的
  `trust=` 反映数据可信度（可信数据在工程模式下仍打印 `trust=TRUSTED`，但产物仍非正式
  结论），产物进 `data/runs/debug`，不得作为可信绩效发布。
- **正式研究依赖冻结的 `csi300` 定义，仓库内是占位模板**：`configs/universes/csi300.yml`
  在操作者用真实证据哈希与实际覆盖区间填写之前不可用，正式运行会在
  `universe_acceptance` 处失败（这是设计而非缺陷）。指数成分证据链（步骤 E）必须在
  首次正式研究前完成；证据缺失、数量不对、退市边界不确定一律停止工作，无绕过开关。
- **真实数据验收（§7）针对数据供给，不针对策略**：验收记录证明“当时该数据
  版本在 `real-data-v1` 规则下证据齐全、检查全过”，是正式研究的**数据供给侧
  质量门禁**；它不构成策略有效性或绩效可信度结论——工程模式即使引用有效
  ACCEPTED 记录，实验评价仍恒为 UNTRUSTED（diagnostic-only）。数据验收与
  策略验收（绩效可信度）是两个独立决定，永不互相替代。
- 无任何策略盈利或实盘就绪声明。

## 6. 记录模板（按数据集/实验 ID 留存，不入库）

| 日期 | dataset_version | run_id / experiment_id | 区间 | 门禁 | 行数差 | 跨源最大差 | 公司行为冲突 | 秘密扫描 | 结论/备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| YYYY-MM-DD | `<hash>` | `<id>` | start..end | PASS/BLOCK | … | … | … | clean | … |

验收结论记录在本项目之外或 `.superpowers` 文档区；`data/`、`reports/` 等市场数据
产物永远由 `.gitignore` 排除。

## 7. 真实数据验收操作流（data acceptance，规则版本 real-data-v1）

正式 `research run` 只接受**已验收**的数据集版本：实验规格的
`data_acceptance_id` 解析为一条有效 ACCEPTED 记录，验收身份随后写入 run
manifest、`metrics.json["data_acceptance"]`、实验 manifest 与实验报告的
**真实数据验收**小节（结论/规则/验收 ID/操作者/UTC 时间）。没有验收的 run
（工程模式诊断）在报告中显示 UNVERIFIED 警示——报告绝不把缺失数据推断为
ACCEPTED。

**状态声明**：验收机制已实现并全部离线测试覆盖；截至本文档更新，**尚无操作者
在真实数据上执行过完整验收流程**——首次真实验收仍待执行。

### 7.1 prepare → 操作者编辑 → publish → show

```bash
# (1) 准备：自动项按 real-data-v1 当场离线重跑（数据集清单/质量报告哈希、
#     必需表覆盖、日期窗口完整性、证券主数据证据、公司行为证据、原始快照
#     可追溯、来源角色健康）；人工项全部初始为显式 FAIL。
python -m stock_quant data acceptance prepare \
  --version <数据版本哈希> --operator <操作者ID> \
  --output acceptance-<数据版本哈希>.yml --root <ROOT>

# (2) 操作者手工编辑清单：把 9 条人工项逐条改为 PASS，并附证据。
#     local 证据 = 项目内的相对路径 + 该文件 sha256 + 一句话摘要；
#     external 证据永不抓取：sha256 只钉住清单内 UTF-8 摘要文本本身。

# (3) 发布：发布时不信任 (1) 的结果——自动检查与数据集/质量报告/原始快照/
#     人工证据哈希全部当场重算。全部通过 → decision=ACCEPTED，退出码 0；
#     任一绑定漂移/人工未过 → 先原子落盘 REJECTED 记录（不可变留档），
#     打印 acceptance_id 与逐条 reason=，再以退出码 1 结束。
python -m stock_quant data acceptance publish \
  --checklist acceptance-<数据版本哈希>.yml --root <ROOT>

# (4) 查询：按 created_at、acceptance_id 稳定升序列出全部记录、结论、规则
#     版本与 reason= 行；无记录明确打印 UNACCEPTED（缺失绝不解释为通过）。
python -m stock_quant data acceptance show \
  --version <数据版本哈希> --root <ROOT>
```

人工项清单（每条都必须有证据，参见 §4 的核对要点）：
`exchange_calendar_sample`、`source_row_count_sample`、`missing_reason_sample`、
`cross_source_price_sample`、`corporate_action_sample`、`benchmark_sample`、
`trading_rule_effective_dates`、`security_master_sample`、`secret_scan`。

### 7.2 语义与边界（务必记住）

- **CURRENT_ACCEPTED 冻结行为**：可编辑规格可写 `CURRENT_ACCEPTED`（运行时
  解析为该数据版本最新的有效 ACCEPTED 记录，并**每次运行重新复核**全部绑定
  哈希与人工证据，复核失败即门禁失败、不回退更早记录），也可显式写 64 位
  十六进制 id（只验证该条记录）。冻结规格回写解析后的具体 id，绝不保留占位
  符；`data_acceptance_id` 参与实验身份计算。
- **REJECTED 记录语义**：先原子持久化、后非零退出；记录不可变、永久留档，
  但**永不可被研究选中**。只有 REJECTED 记录时正式研究在任何计算前失败，仅
  留 `data/runs/preflight_acceptance_<uuid>/` 的 FAILED preflight（脱敏原因）。
- **bootstrap/legacy 迁移**：bootstrap 空种子与在 `build_config` 验收证据出现
  之前发布的数据集，其自动检查（如 `raw_snapshot_traceability`）必然 FAIL——
  跑一次完整 `data update` 重新生成溯源后再走验收；旧数据集与旧实验仍可读取
  审计。工程模式无需验收即可运行，但产物恒记 UNVERIFIED/UNTRUSTED。
- **证据路径限制**：local 证据只允许项目根内的相对路径；绝对路径、目录穿越、
  软链越界一律 FAIL（`evidence_path_outside_project` 等）；无法安全持久化的
  引用在记录中以稳定占位符存储，记录绝不携带操作者路径；external 证据永不
  抓取。
- **损坏记录**：`show` 对哈希不匹配/损坏的记录打印
  `corrupt acceptance_id=<id>`、照常列出其余记录，但以非零退出——损坏绝不
  解释为通过，也不输出 traceback/路径/载荷。
- CLI 输出不含 Token、原始供应商载荷或本机绝对路径。
