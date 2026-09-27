# 批量通道四项探针：冻结 spec §6/§8 常量（骨架，待运行）

- 日期：2026-09-27（本记录只落骨架；**实测读数一概未填**）
- 状态：**待 owner 授权后运行** —— Task 10 Step 1（写脚本与记录骨架）已完成；Step 2
  （请求授权并逐条运行）未发生。本记录不含任何实测值，不预填、不代签。
- 授权：**未获得。** 每条命令都要登录券商、消耗周配额，必须 owner 明确授权后才可执行。
- 脚本：`project/probe_batch_channel.py`（已写好，**从未执行**；仅通过 `py_compile` 与
  `--help` 验证语法）
- 证据：`2026-09-27-batched-channel-probes.evidence.json`（骨架，`_status:
  "awaiting_authorization"`，全部测值为 `null`；运行时脚本按段合并回填并把 `_status`
  翻转为 `"measured"`）
- 纪律：
  - 解释器一律 `/home/ji/miniconda3/envs/sq312/bin/python`（pandas 3.0.5）。
  - 凭据只从 `AD_USERNAME`/`AD_PASSWORD`/`AD_HOST`/`AD_PORT` 环境变量进适配器；脚本
    不读、不打印、不落盘任何凭据；evidence JSON 只含命令行（argv 无凭据）与测值。
  - 脚本不 import 私有 SDK：日线走 `XingyaoSource(SourceConfig(...)).fetch_batch`
    （`stock_quant.data_sources.xingyao`），因子走 `fetch_factor_frames`
    （`stock_quant.data_sources.xingyao_factor`）；每次调用都在适配器自带的可终止
    子进程内（`run_isolated`），探针自身 `--timeout-seconds` 只加 1..3600 的界。
  - `--symbols-file` 必给（一行一个规范代码 `NNNNNN.SH/.SZ/.BJ`，`#` 注释可），无内置
    代码表，缺文件即拒跑。
  - 退出码：0 = 完成读数（含"所有候选全被拒"）；1 = 读数未完成（调用失败/文件错误）；
    2 = 认证被拒；3 = 未预期失败。
- 上游依据：[2026-09-26 Phase 0 实测](2026-09-26-xingyao-phase0-probes.md)（缺行形态、
  配额口径 1 单位 = 1GB 线上流量、601238.SH 纯停牌窗与 601059.SH 混合窗的证据）。

## 一、四条命令与预期输出

以下 `<SYMBOLS-FILE>` 由 owner 运行时给出（日线 probe-limit 需要足以覆盖 1000 起步的
代码量；文件不足时脚本自动把起点降到文件实际代码数并在读数中注明）。

### 1. probe-limit —— 单次调用的 code 上限（喂 `batch_size` / `factor_batch_size`）

```bash
/home/ji/miniconda3/envs/sq312/bin/python project/probe_batch_channel.py probe-limit \
  --endpoint daily --symbols-file <SYMBOLS-FILE> \
  --evidence docs/operations/2026-09-27-batched-channel-probes.evidence.json
/home/ji/miniconda3/envs/sq312/bin/python project/probe_batch_channel.py probe-limit \
  --endpoint backward_factor --symbols-file <SYMBOLS-FILE> \
  --evidence docs/operations/2026-09-27-batched-channel-probes.evidence.json
```

- 行为：daily 从 1000 起按减半步进向下试探；backward_factor 从 50 起按加倍步进向上
  试探（上限 1000 = `SourceConfig` 的 batch-size 界，再大也不可冻结，故不试）。候选
  调用遇到**报错、缺键、多键、或对照帧行数低于其小批基线（截断信号）**即终止该方向的
  步进；对照帧基线来自每次开跑前 3 只代码的小批调用。
- 预期输出：逐候选一行 `trial codes=N accepted=… reason=… elapsed_s=…`，末尾结构化读数
  `max_acceptable_codes: <N>`、`feeds: batch_size|factor_batch_size`、`verdict: …`。
- evidence 段：`probe_limit_daily` / `probe_limit_backward_factor`（逐候选试验表 + 上限）。

### 2. probe-absence —— 无交易日标的的返回形态（决策分支，见 §三）

```bash
/home/ji/miniconda3/envs/sq312/bin/python project/probe_batch_channel.py probe-absence \
  --symbols-file <SYMBOLS-FILE> \
  --evidence docs/operations/2026-09-27-batched-channel-probes.evidence.json
```

- 行为：**一次**多 code 日线调用，默认窗 2026-09-14..2026-09-24（601238.SH 纯停牌窗），
  调用代码 = 缺席标的（默认 601238.SH，置于队首）+ 对照标的（默认 601059.SH：窗内既有
  交易日 09-14/22/23/24、也有停牌日 09-15..09-21，Phase 0 实测）+ symbols 文件全体
  （去重保序）。
- 预期输出：`answered_mapping_keys`（返回 mapping 载有可用帧的键集合）、
  `mapping_key_row_counts`（每键行数）、`refused`（无帧/坏帧的键及其消息）、
  `absence_status[601238.SH]`、`control_status[601059.SH]` 与分支 `verdict`。
- evidence 段：`probe_absence`。

### 3. probe-latency —— 整片一次调用的墙钟（喂 `batch_timeout_seconds` / `factor_batch_timeout_seconds`）

```bash
/home/ji/miniconda3/envs/sq312/bin/python project/probe_batch_channel.py probe-latency \
  --endpoint daily --symbols-file <SYMBOLS-FILE> --repeats 3 \
  --evidence docs/operations/2026-09-27-batched-channel-probes.evidence.json
/home/ji/miniconda3/envs/sq312/bin/python project/probe_batch_channel.py probe-latency \
  --endpoint backward_factor --symbols-file <SYMBOLS-FILE> --repeats 3 \
  --evidence docs/operations/2026-09-27-batched-channel-probes.evidence.json
```

- 行为：把 symbols 文件全体代码作为**一片**（一次调用），重复 `--repeats` 次（默认 3），
  记每次墙钟；取**最大值**并输出建议 `batch_timeout_seconds`（daily）/
  `factor_batch_timeout_seconds`（backward_factor）= 最大耗时 × 3（向上取整）。文件超过
  1000 只即拒跑（超出可冻结的 batch-size 界，车道不会发出这种片）。建议值超过 3600s
  时读数中注明"片过重"。
- 预期输出：逐次 `repeat i elapsed_s=… codes_without_answer=…`，末尾
  `max_elapsed_seconds`、`suggested_timeout_seconds`、`feeds`、`verdict`。
- evidence 段：`probe_latency_daily` / `probe_latency_backward_factor`。

### 4. probe-counters —— 一轮真实会话与计数器读数（核验车道计数模型，不冻结数值）

```bash
/home/ji/miniconda3/envs/sq312/bin/python project/probe_batch_channel.py probe-counters \
  --symbols-file <SYMBOLS-FILE> \
  --evidence docs/operations/2026-09-27-batched-channel-probes.evidence.json
```

- 行为：一轮两段。daily：全体代码一次 `fetch_batch`，用与车道
  `data_pipeline._transport_attempt` 相同口径的 `on_attempt` 钩子计数，**预期
  `sessions`/`code_queries` 均为 1**；factor：按 `--factor-batch-size`（默认 200，两份
  sources.yml 的注释候选值）分片调 `fetch_factor_frames`，每片开跑前计一次（与车道
  `_fetch_factor_chunks` 同口径），**预期均为 `ceil(候选数 / factor_batch_size)`**。
- 预期输出：`daily` 与 `factor` 两个计数块（sessions / code_queries / expected /
  逐片缺键）与比对 `verdict`（match / MISMATCH）。
- evidence 段：`probe_counters`。

## 二、冻结去向（Step 4，未执行）

实测后按读数把**两份** `sources.yml`（`project/configs/sources.yml` 与
`templates/project-config/sources.yml`）xingyao 段的四个注释键放开为实测值，两份必须
同步；读数同时写入 ADR-020 的 Evidence 一节（Task 11）。

| 常量 | 现注释候选 | 来源探针 | 冻结规则 |
| --- | --- | --- | --- |
| `batch_size` | `# batch_size: 1000` | probe-limit daily | 单次调用最大可接受 code 数 |
| `batch_timeout_seconds` | `# batch_timeout_seconds: 180` | probe-latency daily | 整片最大墙钟 × 3 |
| `factor_batch_size` | `# factor_batch_size: 200` | probe-limit backward_factor | 单次调用最大可接受 code 数 |
| `factor_batch_timeout_seconds` | `# factor_batch_timeout_seconds: 300` | probe-latency backward_factor | 整片最大墙钟 × 3 |

四个键成对生效（`SourceConfig` 成对校验），任一为空即整对不冻结、批量通路不启用、
车道回退逐标的——本记录未回填前维持此状态。

## 三、probe-absence 决策分支（Step 3）

| 读数（601238.SH，2026-09-14..09-24 纯停牌窗） | 分支 | 动作 |
| --- | --- | --- |
| 有 key、帧空（`absence_status = empty`） | 分支 1 | 保留 `empty` 分支与 `test_an_empty_answer_is_evidence_and_does_not_touch_the_status`，不动 |
| 无 key（`absence_status = refused`，"supplier answer carried no frame for this code"） | 分支 2 | **停下回报 owner**：fail-closed 默认会让每个长期停牌标的每轮记一次逐标的 `refused`。要么接受该噪音，要么由 owner 裁定引入独立状态；**不得**自行改判为 `empty`，**不得**删 `refused` 用例 |
| 符号端点从不返回零行对象（跨全部观测） | 分支 3 | 按 spec §4 收尾段**删掉 `empty` 分支**及其用例（不保留实测不可达的状态），并同步 ADR-020 |
| 对照标的（默认 601059.SH）`control_status = ok` | 对照 | 证明窗内混合（有交易日也有停牌日）的标的多 code 调用正常返回；对照被拒则该轮读数不可靠，须回报 |

## 四、结果（待回填，全部占位）

- probe-limit daily：`max_acceptable_codes = 【待运行】`
- probe-limit backward_factor：`max_acceptable_codes = 【待运行】`
- probe-absence：`absence_status = 【待运行】`；判定分支 = 【待运行】
- probe-latency daily：`max_elapsed_seconds = 【待运行】`，建议 `batch_timeout_seconds = 【待运行】`
- probe-latency backward_factor：`max_elapsed_seconds = 【待运行】`，建议
  `factor_batch_timeout_seconds = 【待运行】`
- probe-counters：daily `sessions/code_queries = 【待运行】`（预期 1）；factor
  `sessions/code_queries = 【待运行】`（预期 ceil(C/200)）；比对 = 【待运行】
- Step 4 冻结值（两份 sources.yml 同步）：`batch_size = 【】`、
  `batch_timeout_seconds = 【】`、`factor_batch_size = 【】`、
  `factor_batch_timeout_seconds = 【】`
- Step 2 运行台账（逐条命令、时刻、配额消耗）= 【待运行后补记】

## 五、配额与安全说明

- 预计消耗：probe-limit 至多 1 次基线 + 全部候选各 1 次会话（daily 减半步、因子加倍
  步）；probe-latency `repeats` 次整片会话；probe-counters 1 + ceil(C/200) 次会话；
  probe-absence 1 次会话。配额口径见 Phase 0 记录 §六（1 计数单位 = 1GB 线上流量，
  计数器读数 `UsedWeekFlow`）。
- 所有调用都在适配器的可终止子进程内；探针默认界 600s（`--timeout-seconds` 可在
  1..3600 内调整），无 2026-09-19 记录的那种无界挂起形态。
- 本记录不改 `xingyao.enabled`（保持 `false`）、不碰任何已发布数据集；冻结只发生在
  Step 4 且须 owner 授权运行取得读数之后。
