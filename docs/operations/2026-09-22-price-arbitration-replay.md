# 价格观测规则对 25 个跨源冲突的回放：定案 16 / 合并 2 / 维持 7

- 日期：2026-09-22
- 数据集：`e732b19177bda1938d4ac6008ee4f5be131a5f40ebc04765cda2186e5837cf05`（已发布、不可变；其隔离表仍持有全部 50 行冲突，含 2 条 float32 对）
- 脚本：`project/replay_price_arbitration.py`
- 命令（只读；不联网、不写入、不发布会话）

```bash
PYTHONPATH=src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python \
    project/replay_price_arbitration.py
```

输入是盘上已有的 3703 个 tushare `daily` 原始快照与上述版本的隔离表；两次运行
输出逐字节相同，退出码 0。本记录是该测量的证据，规则自下一次 `data update`
起才生效（ADR-014、规格 §6：当前数据集不可变）。

## 结果

```
conflicts=25 {'held': 7, 'cninfo': 16, 'merged (D4)': 2}
```

达到规格 §7 验收 3 要求的 **定案 16、合并 2、维持 7**。

## 逐条输出

```text
002269.SZ 2015-05-12 observed=0.398198 cninfo=0.398198 (0.0 tick) eastmoney=0.398198 (0.0 tick) -> held
002352.SZ 2024-11-07 observed=0.969551 cninfo=0.969332 (1.0 tick) eastmoney=0.991238 (99.0 tick) -> cninfo
002709.SZ 2026-04-29 observed=0.994468 cninfo=0.994468 (0.0 tick) eastmoney=0.996312 (10.0 tick) -> cninfo
300124.SZ 2016-05-18 observed=0.493162 cninfo=0.493193 (0.1 tick) eastmoney=0.493193 (0.1 tick) -> merged (D4)
301308.SZ 2026-06-02 observed=0.998043 cninfo=0.998041 (0.1 tick) eastmoney=0.998041 (0.1 tick) -> merged (D4)
600025.SH 2020-06-19 observed=0.959016 cninfo=0.959016 (0.0 tick) eastmoney=0.950820 (3.0 tick) -> cninfo
600188.SH 2021-07-23 observed=0.944444 cninfo=0.944444 (0.0 tick) eastmoney=0.966667 (40.0 tick) -> cninfo
600188.SH 2022-07-14 observed=0.944352 cninfo=0.944352 (0.0 tick) eastmoney=0.955481 (40.0 tick) -> cninfo
600188.SH 2023-07-17 observed=0.581931 cninfo=0.582029 (0.3 tick) eastmoney=0.606240 (82.3 tick) -> cninfo
600600.SH 2023-07-14 observed=0.982777 cninfo=0.982777 (0.0 tick) eastmoney=0.987561 (50.0 tick) -> cninfo
600803.SH 2024-08-01 observed=0.953642 cninfo=0.953642 (0.0 tick) eastmoney=0.966378 (25.0 tick) -> cninfo
600803.SH 2025-07-22 observed=0.947927 cninfo=0.947927 (0.0 tick) eastmoney=0.959050 (22.0 tick) -> cninfo
600900.SH 2016-07-19 observed=0.972582 cninfo=0.969535 (4.0 tick) eastmoney=0.990140 (23.1 tick) -> held
600989.SH 2019-09-27 observed=0.973180 cninfo=0.973180 (0.0 tick) eastmoney=0.969261 (4.1 tick) -> cninfo
600989.SH 2020-06-04 observed=0.969298 cninfo=0.969298 (0.0 tick) eastmoney=0.964813 (4.1 tick) -> cninfo
600989.SH 2021-05-20 observed=0.981615 cninfo=0.981615 (0.0 tick) eastmoney=0.982619 (1.5 tick) -> held
600989.SH 2022-05-12 observed=0.980282 cninfo=0.980282 (0.0 tick) eastmoney=0.981352 (1.5 tick) -> held
600989.SH 2022-12-27 observed=0.988701 cninfo=0.988701 (0.0 tick) eastmoney=0.990186 (1.8 tick) -> held
600989.SH 2025-05-13 observed=0.974051 cninfo=0.974051 (0.0 tick) eastmoney=0.974051 (0.0 tick) -> held
601601.SH 2021-06-30 observed=0.956768 cninfo=0.956768 (0.0 tick) eastmoney=0.960093 (10.0 tick) -> cninfo
601808.SH 2022-06-17 observed=0.990164 cninfo=0.990164 (0.0 tick) eastmoney=0.998689 (13.0 tick) -> cninfo
601828.SH 2023-07-24 observed=0.983871 cninfo=0.983871 (0.0 tick) eastmoney=0.993145 (4.6 tick) -> cninfo
601898.SH 2024-08-20 observed=0.959320 cninfo=0.958950 (0.5 tick) eastmoney=0.967308 (10.8 tick) -> cninfo
601966.SH 2025-07-10 observed=0.994642 cninfo=0.994374 (0.4 tick) eastmoney=0.995311 (1.0 tick) -> held
603259.SH 2025-05-21 observed=0.978774 cninfo=0.978747 (0.2 tick) eastmoney=0.984333 (34.8 tick) -> cninfo
```

## 与规格 §2.1 的对照

**7 条维持**与 §2.1 表格逐条一致，含每侧 tick 偏离量：

| symbol | ex_date | 规格给出的维持理由 | 本次实测 |
| --- | --- | --- | --- |
| 002269.SZ | 2015-05-12 | 两侧总额相同、拆分不同，价格因子无法分离 | 两侧均 0.0 tick，无分离度 |
| 600900.SH | 2016-07-19 | 赢方自身偏离 4.0 tick，超出赢方容差 | cninfo 4.0 tick（> 2.0） |
| 600989.SH | 2021-05-20 | 败方仅偏离 1.5 tick，不可分辨 | eastmoney 1.5 tick（≤ 3.0） |
| 600989.SH | 2022-05-12 | 败方仅偏离 1.5 tick | eastmoney 1.5 tick |
| 600989.SH | 2022-12-27 | 败方仅偏离 1.8 tick | eastmoney 1.8 tick |
| 600989.SH | 2025-05-13 | 两侧预期因子完全相同，无分离度 | 两侧均 0.0 tick |
| 601966.SH | 2025-07-10 | 败方仅偏离 1.0 tick | eastmoney 1.0 tick |

**2 条合并**正是 300124.SZ 2016-05-18 与 301308.SZ 2026-06-02（ADR-014 的 丁 类）。

**§8 证据附录的关键样本逐条复现**：600188.SH 2023-07-17（0.581931 / 0.582029 /
0.606240 → cninfo）、600188.SH 2021-07-23、601898.SH 2024-08-20、600188.SH
2022-07-14、600900.SH 2016-07-19（维持，赢方 4.0 tick）、002269.SZ 2015-05-12、
300124.SZ 2016-05-18 —— 每一个数字与 §8 表格相同。

**`no observation` 为 0**：25 条全部有盘上价格，7 条维持都是规则判定，不是证据
缺失（规格 §5 曾预计 7 条可能按有无价格拆成 `held` / `no observation`）。

## 方向分布（须监控）

**16 次定案全部指向 cninfo，0 次指向 eastmoney。** 按规格 §6 与 ADR 的要求显式
记录：**该分布必须被监控。** 一个永远选同一边的仲裁器不是仲裁器，是盖章——若后续
语料仍为 100% 单向，说明这条通道在区分"两个 feed 谁更完整"，而不是"谁更正确"
（与 eastmoney feed 不完整这一已知事实一致，见 §2.1）。

## 一处实现说明：通道顺序

本脚本按 **ADR-014 的顺序**分类——先问通道，通道都不认领才由 float32 表示下限
合并。对这两条 丁 类，`settle` 返回 `None`（两侧均 0.1 tick，`_winner` 的"两侧
都在容差内"与"都不在"同解），因此最终仍由下限折叠。这**顺带验证了 ADR-014 的
断言**：通道仍然先被问，所以配置通道不会改变 25 条的仲裁结果（16 / 2 / 7 与
ADR-007 记录的 23 CNINFO / 1 Eastmoney / 1 不可解在同一语料上并存）。

原计划 Task 6 的脚本按"先合并、再定案"书写并引用 `_same_ratio`；该函数随
ADR-014 被 `_within_representation_floor` 取代，已改为调用**生产函数**（对全部
五个比例字段、且 `record_date` 精确相等），而不是复制其常数。

## 复现

```bash
PYTHONPATH=src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python \
    project/replay_price_arbitration.py > /tmp/replay.out
```

只读：读 `project/data/standardized/e732b191…/corporate_action_quarantine.parquet`
与 `project/data/raw/tushare/daily/*/*/*/data.parquet`（3703 个）。
