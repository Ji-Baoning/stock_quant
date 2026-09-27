# 批量通道四项探针：冻结 spec §6/§8 常量（已运行）

- 日期：2026-09-27（owner 授权后运行；本记录为实测回填版）
- 状态：**已运行，daily 批量对已冻结**。因子端点的批量分片假设被实测否定，
  `factor_batch_*` 维持未冻结；两项待 owner 裁定，见 §六。
- 授权：owner 于 2026-09-27 在会话中明确指示执行 Task 10（即 Step 2 的授权）。
- 脚本：`project/probe_batch_channel.py`。运行前修复了一处调度 bug（`main()` 的
  位置参数调用与各 handler 签名顺序不一致，首次运行在任何网络调用之前即
  TypeError 退出，未消耗配额；已改为全关键字调用）。
- 证据：`2026-09-27-batched-channel-probes.evidence.json`（`_status: "measured"`；
  `probe_limit_daily` / `probe_absence` / `probe_latency_daily` 由脚本回填；
  `probe_limit_backward_factor` / `probe_latency_backward_factor` /
  `probe_counters` 为运行后按同一形状手工回填的失败/部分读数，逐段注明）。
- 纪律（与骨架一致）：
  - 解释器 `/home/ji/miniconda3/envs/sq312/bin/python`（pandas 3.0.5）。
  - 凭据只从 `AD_USERNAME`/`AD_PASSWORD`/`AD_HOST`/`AD_PORT` 环境变量进适配器
    （来源：仓库根 `.env`，运行前 source）；脚本与记录不读、不打印、不落盘任何
    凭据。SDK 自身会把会话 Token 打到 stdout——本记录与 evidence 一概不引用
    SDK 日志原文。
  - 脚本不 import 私有 SDK；`--symbols-file` 必给。本轮 symbols 文件由当前
    security_master（版本 `f68df633…`，659 个上市 SH/SZ 标的）导出到
    `/tmp/symbols_all.txt`，另派生 329/100 截断文件用于延迟与缺席探针（/tmp
    文件不入库，来源在此注明）。
- 上游依据：[2026-09-26 Phase 0 实测](2026-09-26-xingyao-phase0-probes.md)。

## 一、四条命令与实际运行

命令与设计同骨架版（probe-limit 减半/加倍步进、probe-absence 单次多 code、
probe-latency 整片重复 3 次、probe-counters 车道同口径计数），此处不重复；
运行台账见 §四 末尾。

## 二、冻结去向（Step 4，daily 对已执行）

| 常量 | 骨架注释候选 | 实测值 | 状态 |
| --- | --- | --- | --- |
| `batch_size` | `# batch_size: 1000` | **329** | 已冻结（两份 sources.yml 同步） |
| `batch_timeout_seconds` | `# batch_timeout_seconds: 180` | **20** | 已冻结（两份 sources.yml 同步） |
| `factor_batch_size` | `# factor_batch_size: 200` | —（无法测量） | **未冻结**：因子端点多 code 返回宽表，批量分片假设不成立（§六-1） |
| `factor_batch_timeout_seconds` | `# factor_batch_timeout_seconds: 300` | —（无法测量） | **未冻结**，同上 |

`batch_size = 329` 的读数口径：659-code 调用被拒的直接原因是 601238.SH（纯停牌，
答案里键在但值不可用，见 §三），并非尺寸上限本身；329-code 调用全部接受。精确边界
位于 (329, 659] 区间，为省配额未细化。329 是实测可接受、且一次调用即可覆盖全部
329-code 片的保守值；车道按片分箱，659 标的会自然分成 329/329/1 三片。

## 三、probe-absence 决策分支（Step 3，已观测）

实测：`absence_status[601238.SH] = refused`，消息为 "the supplier's answer for
this code is not a frame"——**键在但值不可用**，不是"无 key"也不是零行帧；
对照 `control_status[601059.SH] = ok`（返回 1 行，Phase 0 的 4 个交易日中仅
1 日有行，差异已原样记录在 evidence，不影响分支判定）。

| 读数 | 分支 | 处置 |
| --- | --- | --- |
| 无 key / 值不可用（`absence_status = refused`） | **分支 2（实测命中）** | owner 未裁定新状态：**接受 fail-closed 现状语义**——长期停牌标的每轮逐标的 `refused` + xingyao 源状态 `partial_fetch_failure`，不改判、不删用例、不引入新状态；启用动作（ADR-016 decision 11）须知悉该噪声 |
| 零行帧（`absence_status = empty`） | 分支 1/3 | **本轮未观测到**任何零行帧形态。一次探针窗口不足以证明"从不"，empty 分支按 fail-safe 保留，此处注明"实测未观测到"；是否删除留 owner 后续裁定 |

## 四、结果（实测回填）

- probe-limit daily：659 codes → 拒（`601238.SH` 值不可用）；329 codes → 全接受
  （5.2s）。`max_acceptable_codes = 329`，喂 `batch_size`。
- probe-limit backward_factor：**基线调用即失败**——3-code 调用返回非 mapping
  （SDK 提示本地无"后复权因子"数据），`ContractError`。结构化检查（一次性脚本，
  未入库；1 次会话 2 次查询）确认：`get_backward_factor(codes, is_local=False)`
  返回**单张宽表**（8733 行 × 以 code 为列，索引为 1990-12-19 起的全历史交易日），
  单 code 与多 code 同形。批量分片假设不成立，`max_acceptable_codes` 无意义。
- probe-absence：102-code 调用（缺席标的队首 + 对照 + 100 标的），101 键有可用帧
  （9 行/键），601238.SH refused；分支 2（见 §三）。
- probe-latency daily：329-code 整片 × 3 次重复全部干净（4.5s / 4.5s / 6.5s），
  `max_elapsed_seconds = 6.463`，建议 `batch_timeout_seconds = max × 3 = 20`。
- probe-latency backward_factor：未运行（依赖的批量分片不成立，见 §六-1）。
- probe-counters：daily 半边实测（329 codes 一次 `fetch_batch`，车道同口径钩子）：
  `sessions = 1`、`code_queries = 1`、`attempt_code_counts = [329]`、状态
  `ok × 329`——与车道计数模型完全一致。factor 半边未运行（同 §六-1）。
- Step 4 冻结值（两份 sources.yml 同步）：`batch_size = 329`、
  `batch_timeout_seconds = 20`；`factor_batch_size` / `factor_batch_timeout_seconds`
  维持注释（未冻结）。
- Step 2 运行台账（全部 2026-09-27，UTC 会话内顺序执行）：
  | # | 命令 | 会话 | 结果 |
  | --- | --- | --- | --- |
  | 1 | probe-limit daily（首次，脚本 bug） | 0 | TypeError，未联网 |
  | 2 | probe-limit daily | 3（基线 + 659 + 329） | 329 接受 |
  | 3 | probe-limit backward_factor | 1（基线） | ContractError：宽表 |
  | 4 | 因子形状检查（一次性脚本） | 1（2 次查询） | 宽表确认 |
  | 5 | probe-absence | 1（102 codes） | 分支 2 |
  | 6 | probe-latency daily ×3 | 3（329 codes） | max 6.463s |
  | 7 | probe-counters daily 半边 | 1（329 codes） | 1/1/[329]，match |
  | 合计 | | **10 次会话** | `UsedWeekFlow` 读数 ~0.45/1e9 量级，消耗可忽略 |

## 五、配额与安全说明

- 实际消耗见 §四 台账：10 次会话、约 11 次带 code 查询；周配额计数器读数与
  Phase 0 同量级（≈0.45/1e9 单位），消耗可忽略。
- 所有调用都在适配器的可终止子进程内（探针界 600s，未触顶）；无无界挂起形态。
- 本记录不改 `xingyao.enabled`（保持 `false`）、不碰任何已发布数据集。daily 批量
  对的冻结只是把实测常量写进配置——源关闭时车道不运行；启用是 ADR-016
  decision 11 门禁下的单独动作。

## 六、待 owner 裁定项（探针 STOP 门的产出）

1. **因子端点批量分片不成立。** `get_backward_factor` 多 code 返回单张宽表
   （列 = code），不是以 code 为键的 mapping——Task 8 的 `_fetch_backward_factor_batch`
   按_mapping 假设实现，多 code 必然 `ContractError`。当前处置：`factor_batch_*`
   不冻结，车道永远走逐标的回退，批量代码闲置，生产行为与 Task 8 之前一致。
   可选后续：(a) 按列切片重做因子批量（宽表的每列即供应商真实给出的该 code
   序列，不是计划禁止的"按行伪造"）——小改动 + ADR-020 注记；(b) 因子通道永久
   逐标的，移除闲置批量代码。裁定前维持现状。
2. **启用后的缺席噪声。** 全窗口无交易日的标的在批量答案里键在值不可用 →
   逐标的 `refused`。接受该噪声（现状，fail-closed）或引入独立状态，由 owner
   在启用（ADR-016 decision 11）之前裁定；本记录与 ADR-020 Evidence 已如实
   记录该形态。
3. **empty 分支存废**：实测未观测到零行帧形态，但单窗证据不足以支持"从不"；
   按默认保留（fail-safe），删除与否留 owner。
