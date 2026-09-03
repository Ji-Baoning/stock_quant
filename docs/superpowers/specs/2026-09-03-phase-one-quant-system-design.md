# A股量化项目第一阶段设计规格

日期：2026-09-03  
状态：已完成对话评审，待用户审阅书面规格

## 1. 背景与定位

本项目服务于个人资金实盘准备，同时作为量化研究与求职作品。第一阶段定位为工程验证型MVP，目标是验证从多数据源获取到研究回测报告的完整链路，不判断策略是否具备实盘价值。

第一阶段使用30只人工策划的边界样本股和两个基准指数，处理2020-01-01至最近完整交易日的日频数据。使用60交易日动量作为唯一策略因子，以10万元虚拟资金运行周频组合回测。

第一阶段的成功标准是数据、时序、账户和结果可复现，而不是年化收益、夏普或是否跑赢基准。

## 2. 已确认约束

- 市场：A股。
- 项目类型：工程验证型MVP。
- 股票样本：30只固定边界样本股。
- 基准：沪深300、中证500。
- 数据范围：2020-01-01至最近完整交易日。
- 环境：Conda与Python 3.10。
- 数据源：Tushare Pro免费API、AKShare、BaoStock。
- 存储：Parquet持久化，DuckDB查询。
- 运行：手动、幂等的命令行任务。
- 研究：60交易日动量，周频调仓，前10名等权。
- 成交：信号日后下一交易日开盘模拟成交。
- 资金：10万元名义初始资金。
- 交付界面：CLI、Notebook和静态HTML报告。
- 公司行为：最小真实记账，现金分红按税前金额处理。
- 第一阶段不连接券商，不使用结果指导真实资金交易。

## 3. 设计原则

1. 数据层自主，不把供应商格式传播到研究和回测模块。
2. 原始数据不可变，所有转换均可追溯。
3. 清洗只修复确定性的格式与单位问题，不猜测市场事实。
4. 质量错误优先停止流程，不静默填充或替换。
5. 复权价格用于信号，真实未复权价格与公司行为用于账户。
6. 信号、目标组合、订单和成交分层，禁止将预测直接视为成交。
7. 核心逻辑只实现一份；Notebook和报告不得复制计算逻辑。
8. 第一阶段保持单进程、本地文件系统和单一策略，避免平台化扩张。

## 4. 总体架构

```text
配置
 ↓
数据源适配器
 ↓
原始数据层（按来源隔离、不可覆盖）
 ↓
原始数据校验
 ↓
清洗与标准化
 ↓
跨源一致性检查
 ↓
标准数据质量门禁
 ↓
因子计算
 ↓
目标组合
 ↓
模拟订单与成交
 ↓
绩效分析
 ↓
Notebook与HTML报告
```

系统采用单向依赖。上游数据模块不能依赖因子、回测、分析或报告模块。

## 5. 目录与模块

```text
stock/
├── PROJECT_MEMORY.md
├── README.md
├── environment.yml
├── .env.example
├── .gitignore
├── configs/
│   ├── project.yml
│   ├── universe.yml
│   ├── costs.yml
│   ├── sources.yml
│   └── experiments/
│       └── momentum_60d.yml
├── src/stock_quant/
│   ├── config.py
│   ├── cli.py
│   ├── data_sources/
│   │   ├── base.py
│   │   ├── tushare.py
│   │   ├── akshare.py
│   │   └── baostock.py
│   ├── data_model/
│   │   ├── schemas.py
│   │   ├── symbols.py
│   │   ├── clean.py
│   │   └── normalize.py
│   ├── data_quality/
│   │   ├── raw_checks.py
│   │   ├── compare.py
│   │   └── gates.py
│   ├── factors/
│   │   ├── base.py
│   │   ├── models.py
│   │   └── momentum.py
│   ├── research/
│   │   ├── spec.py
│   │   ├── registry.py
│   │   └── runner.py
│   ├── portfolio/
│   │   └── equal_weight.py
│   ├── backtest/
│   │   ├── engine.py
│   │   ├── execution.py
│   │   ├── costs.py
│   │   └── models.py
│   ├── analytics/
│   │   └── performance.py
│   └── reporting/
│       └── html.py
├── notebooks/
│   └── 01_momentum_baseline.ipynb
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── data/
│   ├── raw/
│   ├── staging/
│   ├── standardized/
│   ├── quarantine/
│   ├── quality/
│   ├── runs/
│   └── experiments/
│       └── registry.parquet
└── reports/
```

### 5.1 模块职责

- `data_sources`只请求数据并保留原始响应。
- `data_model`负责代码、日期、类型、单位、清洗和标准化。
- `data_quality`负责原始检查、跨源比较和发布门禁。
- `factors`定义统一因子契约，并只根据固定版本的标准数据生成标准化因子结果。
- `research`校验实验规格、解析固定数据版本，并编排因子、组合、回测和报告；不承载具体策略规则。
- `portfolio`把因子排名转换为目标权重与目标股数。
- `backtest`负责账户、持仓批次、公司行为、订单、成交和成本。
- `analytics`只读取回测产物并计算指标。
- `reporting`只组织展示，不重新计算核心结果。
- `cli.py`只编排用例，不包含业务规则。
- Notebook只调用正式模块并解释结果。

## 6. 配置与密钥

配置文件职责如下：

- `project.yml`：日期范围、数据路径、两个基准、运行参数。
- `universe.yml`：30只固定样本及选入原因。
- `costs.yml`：按生效日期维护佣金、税费、滑点和整手规则。
- `sources.yml`：数据源开关、超时、重试和限流，不包含Token。
- `experiments/*.yml`：研究假设、固定输入版本、因子、组合、成本和评价策略；不包含运行时密钥。

Tushare Token仅从`TUSHARE_TOKEN`环境变量读取。`.env.example`只包含占位名称，真实`.env`被Git忽略。Token不得进入代码、配置快照、运行清单、日志或异常响应。

## 7. 样本选择

30只股票不是推荐组合，也不根据历史收益选择。固定样本的板块配额为：

- 沪市主板8只；
- 深市主板8只；
- 创业板7只；
- 科创板7只。

样本应覆盖：2020年前持续交易、2020年后上市、现金分红、送股或转增、连续缺失或停牌、高低价格、高低流动性、代码名称或状态变化。

选择只使用上市时间、板块、公司行为和数据质量特征，不使用未来收益。最终名单写入`universe.yml`并记录：

```text
symbol
name_at_selection
exchange
board
selected_as_of
boundary_tags
selection_reason
```

名单固定后不随当前市场变化自动更换。

## 8. 数据源职责

- Tushare Pro免费API：未复权股票日线主源。
- BaoStock：复权序列候选主源，同时校验未复权股票行情。
- AKShare：股票列表、两个指数、市场状态、公司行为和第三方行情校验。公司行为优先读取巨潮资讯口径，并用东方财富分红送配明细交叉核验。
- 交易所与巨潮资讯：对公告日期、证券状态和异常记录进行人工抽检。

BaoStock采用涨跌幅复权法，其复权序列用于保持价格变化连续并计算本阶段动量，但不用于推导投资者实际获得的现金分红或送配股份。真实账户公司行为只依据标准化后的`corporate_action`事件处理。

不同来源的原始数据分别存放：

```text
data/raw/tushare/
data/raw/akshare/
data/raw/baostock/
```

来源冲突时不取平均，不静默使用备用源覆盖主源。系统保留全部原始记录、指定字段主源、记录差异，并对重要异常执行官方核验。

## 9. 数据获取流程

`python -m stock_quant data update`执行：

1. 读取样本、基准和日期范围。
2. 计算每个来源缺少的日期区间。
3. 分别请求三个数据源。
4. 保存不可变原始响应及元数据。
5. 校验原始结构与请求范围。
6. 清洗代码、日期、类型、单位和重复项。
7. 执行跨来源比较。
8. 执行标准数据质量门禁。
9. 原子发布新标准数据版本。
10. 生成数据清单和质量报告。

重复执行只请求缺失或需要复查的数据。已有内容相同的快照不重复保存；供应商修订历史数据时保存为新版本并记录差异。

## 10. 原始数据与元数据

原始响应按来源、接口、获取日期和运行编号保存。每个快照记录：

```text
source
endpoint
requested_symbols
requested_start
requested_end
fetched_at
library_version
row_count
content_hash
run_id
```

原始文件一旦写入不修改。标准数据先写入`data/staging/<run_id>/`，质量门禁通过后发布到`data/standardized/versions/<dataset_version>/`，随后原子更新`data/standardized/CURRENT`。

## 11. 标准数据模型

### 11.1 日线行情

`daily_bar`字段至少包括：

```text
symbol
trade_date
open
high
low
close
volume
amount
source
adjustment_mode
ingested_at
record_hash
```

内部股票代码采用`000001.SZ`格式，日期采用无时区交易日期，成交量单位为股，成交额单位为人民币元。

### 11.2 其他核心表

- `security_master`：证券代码、名称、交易所、板块、上市与退市日期。
- `benchmark_bar`：沪深300和中证500指数行情。
- `adjusted_bar`：按来源隔离的复权行情。
- `corporate_action`：公司行为。
- `quality_issue`：质量异常和状态。
- `source_comparison`：跨源差异。
- `dataset_version`：标准数据版本与原始快照引用。
- `cleaning_audit`：确定性转换和隔离动作。

Parquet是唯一持久化标准数据，DuckDB通过视图查询Parquet，不维护另一份数据库副本。

## 12. 清洗规则

清洗包括：

- 统一股票代码、日期、数值类型和单位；
- 去除内容完全相同的重复记录；
- 检测相同主键但内容不同的冲突记录；
- 检查价格、成交量、成交额和日期合法性；
- 检查`low <= open/close <= high`；
- 区分非交易日、未上市、退市、停牌、真实零成交和数据缺失；
- 记录清洗前后行数、修改字段和隔离原因。

以下行为禁止：

- 对行情缺失值前向填充后用于成交；
- 对冲突来源取平均；
- 静默删除极端价格；
- 将停牌日虚构为可交易日；
- 用复权价格覆盖真实价格；
- 对无法解释的市场事实进行猜测修复。

无法可靠处理的记录进入`data/quarantine/<source>/<run_id>/`。清洗审计保存来源、证券、日期、规则、旧值、新值、动作和运行编号。

## 13. 数据质量规则

质量严重级别：

- `INFO`：正常转换或可解释差异，记录后继续。
- `WARNING`：可疑但不确定错误，保留并突出显示。
- `ERROR`：局部数据不可用，隔离相关记录。
- `FATAL`：整批数据不可信，不发布新版本。

### 13.1 原始数据检查

- 返回字段、日期和代码与请求一致；
- 响应不为空、未截断且不是错误页面；
- 主键不存在内容冲突；
- 库版本、文件哈希和行数记录完整。

### 13.2 单条行情检查

价格非正、数量金额为负、OHLC关系非法、日期非法、证券代码无法识别或主键冲突均标记为`ERROR`。零成交量进入状态识别流程，不直接判错。

### 13.3 缺失分类

依次判断尚未上市、已退市、非交易日、多源均缺失疑似停牌、仅主源缺失、无法解释缺失。多源均缺失标记`unknown_or_suspended`并禁止成交；主源缺失而校验源有正常行情时标记`primary_source_missing`；复权序列缺失时股票退出当期因子排名。

### 13.4 跨源阈值

只比较相同调整口径和统一单位后的数据：

- 未复权价格差异不超过0.01元：`INFO`；
- 差异超过0.01元且相对差异超过0.05%：`WARNING`；
- 收盘价相对差异超过0.20%：对应证券日期为`ERROR`；
- 调仓或成交依赖的记录出现`ERROR`：阻止相关回测。

阈值写入配置，修改时必须形成新配置版本。

### 13.5 数据发布门禁

标准数据发布只检查与具体策略无关的条件：必需字段存在，主键唯一，无未处理非法OHLC，来源与单位明确，所有隔离记录都有原因，数据质量报告和运行清单生成成功。数据发布门禁不得依赖60日动量或其他策略参数。

### 13.6 回测就绪门禁

因子或回测启动前另行检查：两个基准在回测区间完整；调仓窗口所需的60日复权序列可用；信号日与成交日的真实价格无`ERROR`；持仓期间公司行为完整且受支持。未通过时，标准数据版本仍可供检查，但不得用于对应回测。

质量报告同时提供全局、每只股票、每日、最长连续缺失、最近60日、调仓关键日和各来源独有记录的覆盖情况。

## 14. 最近完整交易日

默认结束日期需要同时满足：

- 两个基准均有当日记录；
- Tushare股票主源达到预期覆盖；
- 至少一个校验源已经更新；
- 当前时间超过配置的数据发布时间。

条件不满足时回退到上一个确认完整交易日并在报告注明。`--end YYYY-MM-DD`可显式固定结束日期以复现结果。

## 15. 因子设计

每周最后一个交易日收盘后计算60交易日动量：

\[
Momentum_{60}(i,t)=\frac{AdjustedClose_{i,t}}{AdjustedClose_{i,t-60}}-1
\]

要求：

- 使用同一来源、同一复权口径；
- 至少61个有效观测值；
- 过去60个交易日不存在质量`ERROR`；
- 股票已经交易至少120个交易日；
- 不使用未来值填充；
- 并列时按股票代码排序，结果确定。

### 15.1 统一因子契约

60日动量必须作为统一`Factor`协议的第一个实现，不允许由研究脚本直接生成无约束的临时列。协议至少公开：

```text
name
version
lookback
required_fields
frequency
compute(context) -> FactorResult
```

`context`在实验开始时解析并固定`dataset_version`、股票池版本和允许读取的日期范围。因子运行期间只能读取该只读上下文，不得再次解析`CURRENT`，也不得写入标准数据层。

`FactorResult`至少包含：

```text
trade_date
symbol
factor_name
factor_version
raw_value
processed_value
is_valid
invalid_reason
```

`processed_value`在第一阶段可与`raw_value`相同，但保留该字段用于后续去极值、标准化和中性化。任何无效值必须给出原因，不能仅以`NaN`表达。

### 15.2 实验规格与身份

所有正式研究由不可变的`ExperimentSpec`描述，至少包含：

```text
hypothesis
factor_names_and_versions
dataset_version
universe_version
date_range
train_validation_holdout_policy
preprocessing
portfolio_rule
cost_model
random_seed
code_commit
parent_experiment_ids
agent_id_optional
```

`experiment_id`由规范化后的实验规格、固定数据版本和代码提交共同计算确定性哈希。相同输入得到相同ID；任何会改变结果的输入变化都必须产生新ID。

实验产物先写入`data/runs/<run_id>/`并完成校验，再原子发布到`data/experiments/<experiment_id>/`；发布目录不得覆盖。相同ID已经存在时复用并报告既有结果。未通过研究评价门禁的实验也作为完整结果发布并保留结论，避免只保留表现良好的结果；因异常而未完成的运行保留在`data/runs/<run_id>/`，记录失败阶段和原因，但不得形成半成品实验目录。实验索引`data/experiments/registry.parquet`由单一发布者依据各实验清单重建，Agent或研究进程不得直接并发修改。

第一阶段仅做工程验证，不伪造统计意义上的训练集、验证集和隐藏测试集；`train_validation_holdout_policy`明确记录为`not_applicable_engineering_mvp`。该字段和边界校验仍在一期建立，以便扩大到可信全市场数据后冻结时间切分。

## 16. 组合构建

每个信号日从合格股票中按动量降序选择前10只，每只目标权重10%。合格股票不足10只时保留现金，不使用次日信息临时替代无法成交股票。

目标股数在信号日使用当日未复权收盘价估算，并按100股向下取整。第一阶段不做行业中性化、风险优化或市场择时。

## 17. 订单与成交

```text
周内最后交易日收盘
→ 计算因子与目标组合
→ 固化目标股数和订单
→ 下一交易日开盘先卖后买
```

订单股数不能根据次日开盘价重新优化。开盘跳空造成现金不足时禁止透支，按预定优先级逐次减少100股买单，剩余资金保留现金。

基础成交价为下一交易日未复权开盘价：

\[
BuyPrice=Open\times(1+Slippage)
\]

\[
SellPrice=Open\times(1-Slippage)
\]

缺少开盘价、停牌或状态未知、数据质量为`ERROR`、触及限制且规则不允许成交、现金或可卖数量不足时不成交，并记录明确原因。第一阶段不模拟盘口排队和概率性部分成交。

## 18. T+1与持仓批次

买入生成独立持仓批次，记录证券、买入日期、数量、成本和可用日期。买入当日不可卖出，下一交易日转为可卖。卖出可一次处理公司行为产生的零股。

## 19. 公司行为

`corporate_action`至少包含：

```text
symbol
announcement_date
record_date
ex_date
cash_dividend_per_share
bonus_share_ratio
capitalization_ratio
rights_issue_ratio
rights_issue_price
source
status
```

公司行为通过AKShare接入巨潮资讯和东方财富公开数据。只有进入“实施”状态并且登记日、除权日及分配比例完整的事件才能入账；两种口径冲突时进入隔离区并要求人工核验，不能选择对回测结果更有利的记录。

除权日开盘前处理：

- 现金分红按登记日持股数量增加现金；
- 送股与转增增加持股数量；
- 每个事件只允许应用一次；
- 所有动作形成独立账务流水。

第一阶段现金分红按税前金额入账，不实现持有期限相关红利税。配股、吸收合并和换股等复杂行为标记`unsupported_corporate_action`；持仓遇到不支持或冲突行为时停止运行，不静默忽略。

## 20. 估值

每日收盘按未复权收盘价估值：

\[
Equity_t=Cash_t+\sum_i Quantity_{i,t}\times Close_{i,t}
\]

持仓停牌时沿用最近有效收盘价仅用于估值，标记`stale_valuation`和陈旧天数，并报告停牌资产比例。延续价格不得用于成交。

## 21. 费用情景

默认偏保守配置：

- 券商佣金：成交额0.03%，双边，每笔最低5元；
- 印花税：卖出成交额0.05%；
- 滑点：买卖双边0.10%；
- 交易规费视为包含在佣金中，避免重复；
- 不建立非线性市场冲击模型。

分别运行三套独立账户：零成本、佣金税费、佣金税费加滑点。成本会改变现金和后续股数，不能只在最终收益上扣减。

费率按交易日期读取配置，不嵌入策略代码。

## 22. 运行状态与错误处理

运行状态为：

```text
CREATED → FETCHING → RAW_SAVED → CLEANING → VALIDATING
→ PUBLISHED → FACTOR_READY → BACKTESTED → REPORTED
```

失败转为`FAILED`，记录阶段、异常类型和可否重试。

研究实验另有`CREATED → RUNNING → COMPLETED → ACCEPTED|REJECTED`状态；`ACCEPTED`仅表示通过当前配置的研究门禁，不表示获准实盘。执行异常进入运行级`FAILED`，不把不完整产物发布为实验。

临时网络错误、限流、服务端错误和BaoStock会话失效最多重试3次，递增等待且单次不超过30秒。Token无效、权限不足、字段变化、类型错误、参数错误、主键冲突和无法解释的公司行为不自动重试。

上一版`CURRENT`在失败时保持可用。中断后允许使用相同`run_id`继续，不重复请求已经成功并验证的数据。

## 23. 数据源可用性规则

- Tushare未复权股票日线是成交模拟必需数据。
- BaoStock复权行情是60日动量必需数据。
- AKShare两个基准行情是基准分析必需数据。
- AKShare与BaoStock的额外股票行情校验为非必需，失败时产生`WARNING`。
- 持仓期间发生的公司行为是账户计算必需数据。

流程按失败接口的实际用途判断能否继续，不按整个供应商简单判定成功或失败。

## 24. 幂等性

幂等键由来源、接口、证券范围、日期范围、参数和数据版本组成。

- 内容相同的标准记录跳过；
- 内容变化生成新版本并标记`source_revision`；
- 公司行为使用唯一事件ID防止重复入账；
- 同一数据版本、代码版本和配置必须生成相同回测结果。

## 25. 日志与运行审计

结构化日志至少包含时间、级别、运行编号、阶段、来源、证券、事件和消息。异常响应先脱敏，任何密钥都不得写入日志。

每次正式研究在`data/experiments/<experiment_id>/`输出：

```text
experiment_spec.yml
experiment_manifest.json
run_manifest.json
config_snapshot.yml
dataset_version.txt
factor_results.parquet
signals.parquet
target_positions.parquet
orders.parquet
fills.parquet
cash_ledger.parquet
corporate_action_ledger.parquet
daily_equity.parquet
metrics.json
```

实验清单和运行清单记录Git提交、Python与依赖版本、数据哈希、因子名称与版本、成本情景、随机种子、父实验ID及可选的`agent_id`。没有Git版本时明确记录`unversioned`。

CLI成功退出码为0，失败返回非0。终端只显示摘要，完整诊断保存在运行目录。禁止部分失败后仍返回成功。

## 26. 使用界面

第一阶段提供：

```bash
python -m stock_quant data update
python -m stock_quant data validate
python -m stock_quant research run --spec configs/experiments/momentum_60d.yml
python -m stock_quant backtest momentum_60d  # 仅供局部调试
python -m stock_quant report build
```

`research run`是产生可复现正式结果的唯一入口，负责固定数据版本并发布完整实验产物。直接`backtest`命令仅用于局部调试，其结果不得进入正式实验索引。Notebook解释数据、因子和回测结果；HTML报告用于浏览与分享；CSV或Parquet明细用于人工核验。第一阶段不开发Web仪表盘。

## 27. 报告内容

数据质量报告至少包含：来源状态与版本、原始和标准行数、隔离数量、重复缺失非法OHLC、单位异常、跨源差异分布、最大差异样本、复权抽查、当前数据版本和门禁结论。

回测报告至少包含：三种成本情景净值、两个基准、最大回撤、换手率、成本拆分、持仓、现金、未成交订单、停牌资产比例、公司行为流水和所有已知限制。

## 28. 测试设计

使用`pytest`。普通自动测试不依赖外部API。

### 28.1 单元测试

覆盖代码与单位转换、重复和OHLC检查、因子协议、`FactorResult`模式、60日动量、稳定排序、`ExperimentSpec`校验与确定性ID、等权组合、整手、现金约束、佣金最低额、印花税、滑点、T+1、公司行为、停牌估值、回撤和换手率。

### 28.2 合成边界数据

固定构造10只股票、80个交易日，覆盖正常交易、停牌、现金分红、送转、新股、开盘限制、高价无法买入、重复冲突、非法OHLC及跨源冲突。测试必须断言具体结果。

### 28.3 数据源契约测试

验证每个适配器的列名、类型、单位、日期范围、代码、空响应、限流和认证错误。联网契约测试使用`pytest -m external`，普通测试不运行且不使用真实Token。

### 28.4 集成测试

使用固定响应文件验证原始响应到标准数据发布全链路，以及非关键来源失败、主源失败、门禁阻止发布、中断恢复、重复执行和历史数据修订。

### 28.5 防未来函数测试

先截断到信号日计算结果，再追加未来数据重新计算；历史信号必须完全不变。还需验证信号日与成交日分离、订单股数不受次日开盘反向影响。

### 28.6 黄金样例

固定小型数据、配置和预期输出，逐项验证排名、股数、订单、成交、费用、公司行为、现金、权益和回撤。只有业务规则明确变化时才能更新黄金结果。

### 28.7 真实数据冒烟测试

`pytest -m smoke`在用户本地提供环境变量后请求少量股票短区间，只验证接口连通和契约，不声称验证数据绝对正确。

### 28.8 实验隔离与并发边界测试

验证实验开始后即使`CURRENT`变化也仍只读取已固定的数据版本；相同规格产生相同`experiment_id`并复用既有结果；已发布实验目录不可覆盖；评价为`REJECTED`的实验进入索引并保留原因；执行失败只保留运行审计且不发布半成品；多个研究进程先写各自运行目录，共享索引只能由持有发布锁的单一发布者更新。

## 29. 验收标准

- Conda环境可从配置重新创建。
- 30只股票和两个基准覆盖2020年至最近完整交易日。
- 三源原始快照独立、不可变且可追溯。
- 清洗审计、跨源比较和HTML质量报告完整。
- 故意破坏数据时门禁阻止回测。
- 60日动量通过统一`Factor`协议运行并输出合规`FactorResult`。
- 60日动量通过防未来函数测试。
- 正式研究只能经`ExperimentSpec`和`research run`启动。
- 实验ID可确定性复现，已发布产物不可覆盖，`REJECTED`实验与执行失败均可审计。
- 实验全程只读固定`dataset_version`，`CURRENT`变化不影响进行中的实验。
- 三种成本情景均能完成回测。
- 整手、T+1、停牌和最小公司行为通过测试。
- 相同数据、配置和代码重复运行结果相同。
- Notebook没有重复核心业务逻辑。
- HTML报告包含约定的绩效、成本、持仓和限制。
- 全部离线自动测试通过。
- 缓存后的完整回测在普通个人电脑上数分钟内完成。

## 30. 运行依赖

第一阶段核心依赖为Pandas、NumPy、PyArrow、DuckDB、Tushare、AKShare、BaoStock、Pydantic、PyYAML、Typer、Jinja2、Plotly、pytest、pytest-cov和Ruff。

第一阶段不安装VectorBT、RQAlpha、Qlib或VeighNa。它们仅作为后续快速研究、独立事件驱动复核、机器学习和实盘执行的候选边界。

## 31. 不在第一阶段范围内

- 真实资金交易、券商接口和自动下单；
- 定时任务、常驻服务和Web仪表盘；
- 全A股可信策略评价；
- 财务因子、多因子组合、行业中性化和优化器；
- 机器学习、分钟或Tick数据；
- LLM或多Agent编排器、并行任务调度器和自动因子生成；
- 自动模型训练、预测服务和隐藏测试集评审服务；
- 融资融券；
- 完整红利税；
- 配股、合并、换股等复杂公司行为；
- 对收益能力作出任何承诺。

## 32. 后续阶段接口

标准数据层应允许后续：

- 引入VectorBT进行快速参数与信号筛选；
- 编写薄适配器接入RQAlpha进行独立事件驱动复核；
- 将研究数据转换为Qlib格式进行机器学习；
- 将目标组合和订单接口接入VeighNa或券商系统；
- 升级Tushare权限或替换数据供应商而不改写因子逻辑。

这些接口只保持清晰边界，第一阶段不提前实现。

## 33. 多Agent扩展边界

一期不实现多Agent系统，但其研究内核必须允许未来由人、脚本或Agent通过同一个`ExperimentSpec`和`Factor`协议发起实验。未来推荐职责拆分为：假设生成、因子实现、独立评价、反例审查和因子组合；各角色交换结构化实验产物，不以自然语言聊天记录作为唯一依据。

并发与数据所有权遵循：

- 标准数据单写多读；研究Agent只能读取固定的`dataset_version`。
- 每个Agent在独立Git worktree或等价隔离环境中修改代码，在独立`experiment_id`目录写产物。
- 只有中央发布流程可更新`CURRENT`、实验注册表和候选策略状态。
- Agent不得修改历史原始数据、成本假设、冻结的时间切分或隐藏测试结果。
- Agent不得删除失败实验、接触券商密钥、直接下单或自行批准进入实盘。
- 自动预测只输出带版本的预期收益、排名或置信度；仍须经过组合、风控、订单和人工授权边界。

这使一期的单进程实现保持简单，同时避免未来为多Agent研究重写因子、实验追踪和数据访问接口。

## 34. 后续研究治理

30只股票与免费数据只能验证工程链路，不能支撑自动发现有效因子或评价模型泛化能力。进入自动因子探索、因子组合或机器学习前，必须先具备可信的时点化全市场数据，并建立：

- 冻结的训练、验证和隐藏时间外测试区间；
- 滚动训练与市场状态分段评价；
- 多重检验、试验次数和父子实验谱系记录；
- 与基准因子、成本、换手和容量的统一比较门禁；
- 独立评价者无法读取或反复调试隐藏测试结果的权限隔离；
- 人工审批后才允许候选策略进入影子盘，影子盘通过后才讨论小资金实盘。

Qlib可在这一阶段承接数据集、模型训练与预测，但预测输出仍须适配本项目的`FactorResult`或后续统一信号接口；Agent编排层只负责任务和证据流转，不绕过数据、回测、风险及实盘边界。
