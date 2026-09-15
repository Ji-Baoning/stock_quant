# 资产保留规则

本规则治理仓库中可见的重复、历史或大体积资产；它不授予删除数据、证据或运行产物的
权限。操作入口仍是根 [`RUNBOOK.md`](../../RUNBOOK.md)。

## 必须保留

- `project/data/standardized/` 内由内容哈希标识的数据集版本及其 `CURRENT` 指针；相同
  表出现在多个不可变版本中是可追溯性所需的结果，不按文件重复删除。
- `project/data/raw/` 内原始响应、快照、清单及其哈希证据。
- 验收记录与证据，包括 `data/acceptances/`、`data/acceptance-evidence/`、
  `data/acceptance-worksheets/`、`data/acceptance-external-inputs/`。
- 实验、报告、调试和挑战工件；它们是结论与失败的可审计记录。
- 已采纳 ADR、历史设计文档和日期化操作记录；它们不能作为当前事实替代层，但必须保留
  可追溯性。

## 可再生且默认不提交

以下目录可以按本机需要重建或清理，但必须先确认没有正在运行的任务使用它们：

- `.venv/` 及其他本地虚拟环境；
- Python、测试和工具缓存；
- `.codegraph/` 索引；
- `.superpowers/` 工作区状态。

这些目录不属于数据保留策略，不能据此删除任何 `project/data/` 内容。

## 需人工确认的归档包

`phase-one-*.bundle` 可能是可恢复历史的 Git bundle。在确认其来源、校验可读性，并确认
已有等价远端或受控备份之前，禁止删除。确认记录应写入新的日期化操作记录，而不是在
本规则中静默修改。
