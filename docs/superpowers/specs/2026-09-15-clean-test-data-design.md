# 项目测试数据清理设计

**日期：** 2026-09-15

## 目标

提供 `project/clean_test_data.py`，在不触碰可复用原始证据的前提下清理历史派生
数据和实验产物。清理后，`CURRENT` 数据集仍可执行自动验证、准备新的数据验收，并在
人工确认后用于回测；脚本不伪造、迁移或绕过任何验收结论。

## 保留集

- `data/raw/` 中所有供应商原始响应和 CSI 成分证据；
- `data/membership/` 中可复用成员事实；
- `data/standardized/CURRENT` 指向的完整内容寻址版本，以及 `CURRENT` 指针；
- 与 CURRENT 同版本的 `data/acceptances/`、`data/acceptance-evidence/` 和
  `data/acceptance-worksheets/` 项（若存在）；
- `data/acceptance-external-inputs/`：这些是可复用的人工抽查候选材料，不是已签
  验收结论。

CURRENT 内的 manifest、质量报告、覆盖表、成员表和派生表必须作为该版本的一部分一并
保留。当前版本没有 ACCEPTED 记录时，清理不会使它“已验收”；后续仍需从 CURRENT
重新执行 `data acceptance prepare` 并完成版本绑定的人工确认。

## 清理集

脚本仅处理以下固定白名单中的内容：

- `data/standardized/` 下除 CURRENT 指向版本外的哈希目录；
- `data/experiments/`、`data/reports/`、`data/runs/`、`data/staging/` 的全部子项；
- 遗留 `data/acceptance/` 的全部子项；
- `data/acceptances/`、`data/acceptance-evidence/`、`data/acceptance-worksheets/`
  下名称不等于 CURRENT 版本的子项。

脚本不处理未知路径、配置、代码、`data/raw/`、`data/membership/`、
`data/acceptance-external-inputs/`，也不删除容器目录本身。

## 安全接口

```bash
PYTHONPATH=src python project/clean_test_data.py --root project
PYTHONPATH=src python project/clean_test_data.py --root project \
  --apply --confirm-current <CURRENT_SHA256>
```

- 默认是 dry-run，逐项打印候选相对路径和总字节数；
- 真实删除同时要求 `--apply` 和与磁盘 `CURRENT` 精确相等的
  `--confirm-current`；
- `--root` 必须是显式的有效项目根；
- CURRENT 值、候选项和路径解析须拒绝软链和项目 `data/` 根外的路径；
- 只在完整计划构建成功后才执行删除，计划构建阶段不得修改磁盘。

## 验证

单元测试使用临时项目根，覆盖：

1. dry-run 精确列出清理集且不删除任何内容；
2. apply 删除清理集、保留 CURRENT 与全部保留集；
3. 缺失或错误的 `--confirm-current` 拒绝执行；
4. 软链或不规范 CURRENT 值拒绝执行；
5. 未知 `data/` 目录不进入计划。

测试不对真实 `project/data/` 调用 `--apply`。
