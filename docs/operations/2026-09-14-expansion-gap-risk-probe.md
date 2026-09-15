# 扩池停牌缺口风险实测（Stage B 前置）

- 运行时间：2026-09-13T16:14:20.599898+00:00
- 窗口：2015-01-05..2026-08-28
- relay host：`jiaoch.top`
- 抽样：60 只（日线成功 60，失败 0）
- 目标总体：master 相对现行 universe.yml 的 654 只新增标的（其中在市 629 只 = Stage B 的扩池对象，退市 25 只 = Stage A 的对象）

## 结论

- reviews：项目 reviews (300750.SZ@2024-04-30, 601318.SH@2018-06-07) 未在样本内命中隔离行，本轮按未复核结果计：reviewed corporate action 300750.SZ#2024-04-30 has 0 matching cninfo conflicts
- Stage B（在市新增）：样本 60 只中 5 个阻断项 → 总体 629 只预计 **52.4** 个，95% 上界 **96.4**
- Stage A（退市新增）：样本为空，无法估计
- 抽样中共 **5 个阻断项**（`unexplained_primary_gap`），分布在 5 只：000656.SZ, 002202.SZ, 600008.SH, 600089.SH, 600654.SH。 `data update` 遇到任意一个即整轮不发布——按上界，Stage B 单独跑通的前提并不牢靠。

## 逐号结果

| symbol | board | 状态 | 日线行 | 已接受动作 | 已证明停牌 | 未证实 | 阻断 | 错误 |
|---|---|---|---|---|---|---|---|---|
| 000002.SZ | sz_main | 在市 | 2698 | 9 | 4 | 0 | 0 |  |
| 000063.SZ | sz_main | 在市 | 2759 | 9 | 6 | 0 | 0 |  |
| 000066.SZ | sz_main | 在市 | 2636 | 6 | 3 | 0 | 0 |  |
| 000301.SZ | sz_main | 在市 | 2657 | 10 | 6 | 0 | 0 |  |
| 000338.SZ | sz_main | 在市 | 2831 | 23 | 1 | 0 | 0 |  |
| 000408.SZ | sz_main | 在市 | 2771 | 0 | 12 | 0 | 0 | reconcile: TypeError: Cannot compare NaT with datetime.date object |
| 000538.SZ | sz_main | 在市 | 2669 | 0 | 5 | 0 | 0 | reconcile: TypeError: Cannot compare NaT with datetime.date object |
| 000656.SZ | sz_main | 在市 | 2755 | 0 | 6 | 0 | 1 | reconcile: ValueError: cninfo reports more than one implemented supported action for 000656.SZ on NaT |
| 000709.SZ | sz_main | 在市 | 2794 | 0 | 1 | 0 | 0 | reconcile: TypeError: Cannot compare NaT with datetime.date object |
| 000895.SZ | sz_main | 在市 | 2818 | 0 | 3 | 0 | 0 | reconcile: TypeError: Cannot compare NaT with datetime.date object |
| 001965.SZ | sz_main | 在市 | 2106 | 9 | 0 | 0 | 0 |  |
| 002001.SZ | sz_main | 在市 | 2819 | 0 | 3 | 0 | 0 | reconcile: ValueError: eastmoney reports more than one implemented supported action for 002001.SZ on NaT |
| 002050.SZ | sz_main | 在市 | 2767 | 18 | 4 | 1 | 0 |  |
| 002202.SZ | sz_main | 在市 | 2827 | 12 | 0 | 0 | 1 |  |
| 002304.SZ | sz_main | 在市 | 2833 | 13 | 0 | 0 | 0 |  |
| 002400.SZ | sz_main | 在市 | 2664 | 11 | 3 | 0 | 0 |  |
| 002459.SZ | sz_main | 在市 | 2760 | 4 | 3 | 0 | 0 |  |
| 002555.SZ | sz_main | 在市 | 2635 | 25 | 6 | 0 | 0 |  |
| 002608.SZ | sz_main | 在市 | 2497 | 0 | 5 | 0 | 0 | reconcile: TypeError: Cannot compare NaT with datetime.date object |
| 002773.SZ | sz_main | 在市 | 2717 | 11 | 0 | 0 | 0 |  |
| 300002.SZ | chinext | 在市 | 2822 | 9 | 2 | 0 | 0 |  |
| 300085.SZ | chinext | 在市 | 2705 | 5 | 6 | 0 | 0 |  |
| 300207.SZ | chinext | 在市 | 2810 | 14 | 3 | 0 | 0 |  |
| 300394.SZ | chinext | 在市 | 2790 | 14 | 2 | 0 | 0 |  |
| 300601.SZ | chinext | 在市 | 2325 | 10 | 0 | 0 | 0 |  |
| 300803.SZ | chinext | 在市 | 1646 | 4 | 0 | 0 | 0 |  |
| 600008.SH | sh_main | 在市 | 2817 | 13 | 2 | 0 | 1 |  |
| 600011.SH | sh_main | 在市 | 2833 | 10 | 0 | 0 | 0 |  |
| 600018.SH | sh_main | 在市 | 2830 | 14 | 1 | 0 | 0 |  |
| 600038.SH | sh_main | 在市 | 2823 | 12 | 1 | 0 | 0 |  |
| 600039.SH | sh_main | 在市 | 2811 | 0 | 4 | 0 | 0 | reconcile: TypeError: Cannot compare NaT with datetime.date object |
| 600089.SH | sh_main | 在市 | 2823 | 12 | 2 | 0 | 1 |  |
| 600115.SH | sh_main | 在市 | 2819 | 4 | 3 | 0 | 0 |  |
| 600196.SH | sh_main | 在市 | 2818 | 12 | 3 | 0 | 0 |  |
| 600348.SH | sh_main | 在市 | 2833 | 9 | 0 | 0 | 0 |  |
| 600376.SH | sh_main | 在市 | 2816 | 9 | 2 | 0 | 0 |  |
| 600426.SH | sh_main | 在市 | 2826 | 14 | 1 | 0 | 0 |  |
| 600438.SH | sh_main | 在市 | 2681 | 10 | 7 | 0 | 0 |  |
| 600654.SH | sh_main | 在市 | 2397 | 3 | 15 | 0 | 1 |  |
| 600664.SH | sh_main | 在市 | 2698 | 4 | 3 | 1 | 0 |  |
| 600703.SH | sh_main | 在市 | 2816 | 0 | 3 | 0 | 0 | reconcile: TypeError: Cannot compare NaT with datetime.date object |
| 600760.SH | sh_main | 在市 | 2751 | 0 | 5 | 0 | 0 | reconcile: TypeError: Cannot compare NaT with datetime.date object |
| 600816.SH | sh_main | 在市 | 2764 | 5 | 9 | 0 | 0 |  |
| 600872.SH | sh_main | 在市 | 2744 | 11 | 2 | 0 | 0 |  |
| 601009.SH | sh_main | 在市 | 2826 | 14 | 2 | 0 | 0 |  |
| 601018.SH | sh_main | 在市 | 2696 | 13 | 2 | 0 | 0 |  |
| 601139.SH | sh_main | 在市 | 2832 | 12 | 1 | 0 | 0 |  |
| 601186.SH | sh_main | 在市 | 2833 | 12 | 0 | 0 | 0 |  |
| 601238.SH | sh_main | 在市 | 2823 | 21 | 1 | 0 | 0 |  |
| 601298.SH | sh_main | 在市 | 1839 | 10 | 2 | 0 | 0 |  |
| 601816.SH | sh_main | 在市 | 1604 | 8 | 0 | 0 | 0 |  |
| 601838.SH | sh_main | 在市 | 2080 | 9 | 0 | 0 | 0 |  |
| 601901.SH | sh_main | 在市 | 2820 | 12 | 2 | 0 | 0 |  |
| 603019.SH | sh_main | 在市 | 2807 | 14 | 5 | 0 | 0 |  |
| 603658.SH | sh_main | 在市 | 2425 | 11 | 0 | 0 | 0 |  |
| 603833.SH | sh_main | 在市 | 2284 | 10 | 1 | 0 | 0 |  |
| 603885.SH | sh_main | 在市 | 2726 | 10 | 1 | 0 | 0 |  |
| 605499.SH | sh_main | 在市 | 1277 | 8 | 0 | 0 | 0 |  |
| 688005.SH | star | 在市 | 1721 | 7 | 2 | 0 | 0 |  |
| 688521.SH | star | 在市 | 1453 | 0 | 1 | 0 | 0 |  |

## 汇总

- `suspension_row_materialized`（已证明停牌，INFO）：167
- `suspension_run_unverified`（窗口首尾无锚，WARNING）：2
- `unexplained_primary_gap`（阻断，ERROR）：5
- 单只公司行为对账失败（生产记为 WARNING，等于该只失去全部动作）：10

### 对账失败明细

- 000408.SZ: TypeError: Cannot compare NaT with datetime.date object
- 000538.SZ: TypeError: Cannot compare NaT with datetime.date object
- 000656.SZ: ValueError: cninfo reports more than one implemented supported action for 000656.SZ on NaT
- 000709.SZ: TypeError: Cannot compare NaT with datetime.date object
- 000895.SZ: TypeError: Cannot compare NaT with datetime.date object
- 002001.SZ: ValueError: eastmoney reports more than one implemented supported action for 002001.SZ on NaT
- 002608.SZ: TypeError: Cannot compare NaT with datetime.date object
- 600039.SH: TypeError: Cannot compare NaT with datetime.date object
- 600703.SH: TypeError: Cannot compare NaT with datetime.date object
- 600760.SH: TypeError: Cannot compare NaT with datetime.date object

### 阻断明细

- 000656.SZ: 2017-05-05..2017-07-04
- 002202.SZ: 2019-03-21..2019-03-28
- 600008.SH: 2020-09-21..2020-09-28
- 600089.SH: 2017-06-01..2017-06-08
- 600654.SH: 2022-12-22..2022-12-22

## 口径

本探针只读：不发布数据集、不写 raw 快照、不动 `CURRENT`。
公司行为走生产同款 prepare → clip → **逐只** reconcile → reviews 链路；
逐只隔离是刻意的——生产在 `_refresh_corporate_actions` 里逐只对账并吞掉异常，
所以一只对账失败只会让它自己失去动作，不会波及其他标的。
即便如此，估算值仍是阻断项的**下界**：对账只会丢弃动作（跨源冲突进隔离），
而丢弃动作会把已证明的停牌变成未解释缺口；且未抽中的标的仍可能带阻断项。
样本外的结论是估计，不是保证。
