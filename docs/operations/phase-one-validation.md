# 阶段一真实数据验收（operator acceptance）

本文档是**操作者（operator）验收清单**：在用户本机具备网络与自备凭证后，用真实
供应商小窗口数据验证阶段一的接口连通与数据契约。它补充 [README](../../README.md)
（README 记录五个 CLI 命令与数据目录），不重复整篇文档。

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
| 正式研究（唯一发布者） | `python -m stock_quant research run --spec configs/experiments/momentum_60d.yml --root <ROOT>` | 冻结规格端到端运行并内容寻址发布；打印 `experiment_id=` |
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
- 若 `PASS`，读取发布的 `daily_bar` 确认窗口内确有该股行。

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

### 步骤 E：研究复现与报表

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
5. **复权抽查**：本阶段只消费未复权 `daily_bar`（见第 5 节限制）；复权核对不适用
   于现有产物。
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

## 5. 阶段一已知限制（对齐操作者预期）

- **不发布 `adjusted_bar` 表**：阶段一只发布规范化的未复权 `daily_bar`；无复权产物
  可下载或核对，勿期待 adjusted 制品。
- **BaoStock 仅为可选校验来源**：因子层不消费 BaoStock *复权*序列；研究运行因子的
  适配器使用规范未复权 `daily_bar`。BaoStock 失败仅记为 WARNING，不阻断发布。
- **`data bootstrap` 发布首个基线；`data update` 只扩展**：见第 1 节，
  `python -m stock_quant data bootstrap` 发布 `security_master`/`trading_calendar`
  （+空 `daily_bar`/`corporate_action`）基线；`data update` 只扩展已有数据集。
  `bootstrap_seed.py` 是与该 CLI 等价的直调脚本。
- **`--engineering` 仅限诊断，永不构成正式绩效（mode-level）**：正式研究 `research run`
  无此开关（不可降级绕过证据门禁）；`backtest momentum_60d --engineering` 即使覆盖证据
  可信也**永不发布 ACCEPTED**——评价恒为 UNTRUSTED、实验清单 REJECTED（理由为
  diagnostic-only；覆盖证据不足时理由改为点明数据 UNTRUSTED + 原因/标的）。stdout 的
  `trust=` 反映数据可信度（可信数据在工程模式下仍打印 `trust=TRUSTED`，但产物仍非正式
  结论），产物进 `data/runs/debug`，不得作为可信绩效发布。
- 无任何策略盈利或实盘就绪声明。

## 6. 记录模板（按数据集/实验 ID 留存，不入库）

| 日期 | dataset_version | run_id / experiment_id | 区间 | 门禁 | 行数差 | 跨源最大差 | 公司行为冲突 | 秘密扫描 | 结论/备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| YYYY-MM-DD | `<hash>` | `<id>` | start..end | PASS/BLOCK | … | … | … | clean | … |

验收结论记录在本项目之外或 `.superpowers` 文档区；`data/`、`reports/` 等市场数据
产物永远由 `.gitignore` 排除。
