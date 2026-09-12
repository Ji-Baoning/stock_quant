# 存量 raw 快照出处审计（2026-09-12）

来源：`project/audit_raw_provenance.py`（设计规格 §5，落地阶段 2）。

## 这次审计能得出什么、不能得出什么

- **不能**判定每份快照的真实 provider。历史 manifest 没有留下判别依据，
  本审计**不对历史 provider 下结论**。`tushare.pro.*` 这个标签既证明不了
  是官方、也证明不了不是官方 —— 官方 SDK 与会话基址被改写的中转是同一个
  客户端、不同的 host，标签本身不具判别力。
- **能**判定产出环境的分布：哪些 SDK 版本出现在**当前解释器**、哪些不出现。
- 本脚本只检查运行它的那个解释器。SDK 装在别的 conda 环境、别的虚拟环境或
  缓存里，本报告看不见，因此**只断言「当前解释器未安装」**，不断言「本机
  不存在」；也不据此断言数据集不能复现——那需要扫描多环境与缓存，本脚本
  不做这件事。
- 反查不出产出环境的记 `unknown`（本报告的**结论字段**，与 §2.3 的保留字
  `transport_id` 无关），不猜、不重标。是否需要用新标注重建，由 owner 决定，
  不在本脚本内自动进行。

## 总量

共 391 份快照。

| 源 | 份数 |
| --- | --- |
| `akshare` | 263 |
| `index_constitution` | 1 |
| `sina_index_history_component` | 1 |
| `tushare` | 126 |

## SDK 版本分布与产出环境

| 源 | SDK 版本 | 份数 | 当前解释器 |
| --- | --- | --- | --- |
| `akshare` | `1.18.23` | 127 | installed |
| `akshare` | `1.18.88` | 136 | unknown |
| `index_constitution` | `unknown` | 1 | unknown |
| `sina_index_history_component` | `unknown` | 1 | unknown |
| `tushare` | `1.4.24` | 63 | installed |
| `tushare` | `1.4.29` | 63 | unknown |

## 结论

201 份快照的产出环境**当前解释器未安装**（SDK 版本组合与本解释器安装的不符），记 `unknown`。这不等于本机没有该环境：SDK 可能装在别的环境或缓存里，本脚本不扫描那些位置。是否需要用新标注重建，由owner 决定。
