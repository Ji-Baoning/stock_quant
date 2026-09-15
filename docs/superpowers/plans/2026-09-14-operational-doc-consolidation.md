# 运行文档与脚本治理收敛实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让根 `RUNBOOK.md` 成为唯一操作入口，删除 `project/RUNBOOK.md`，并建立脚本与资产保留治理。

**Architecture:** 先以内容差异为证据收敛两个 RUNBOOK，再删除项目副本。脚本和资产只增加状态/保留文档，不移动数据、工件或脚本。一个标准库验证器检查唯一入口、脚本清单和资产规则。

**Tech Stack:** Markdown、Python 3.10 标准库、pytest。

**Spec:** `docs/superpowers/specs/2026-09-14-operational-doc-consolidation-design.md`

## Global Constraints

- 根 `RUNBOOK.md` 是唯一操作手册；删除 `project/RUNBOOK.md`，不留副本或重定向。
- 不删除 `project/data/standardized/`、原始证据、验收记录、实验工件或历史设计资料。
- 不改变 CLI、数据发布、研究或配置的运行时行为。
- 不覆盖现有未提交的根 RUNBOOK 或其他用户 WIP；只在审查差异后追加仍适用说明。

---

### Task 1: 比较并收敛 RUNBOOK

**Files:**

- Modify: `RUNBOOK.md`
- Delete: `project/RUNBOOK.md`
- Create: `docs/operations/runbook-consolidation.md`

- [ ] 比较两个 RUNBOOK 的章节和命令，写出 `docs/operations/runbook-consolidation.md`：逐项记录“已在根文档”“迁移到根文档”“过期不迁移”，每项给出原章节与目标章节。
- [ ] 检查根 RUNBOOK 的未提交差异；只在不覆盖已有行的情况下补充项目根、relay 环境、验收与诊断的仍适用细节。所有命令使用显式 `--root project` 或解释 `cd project` 的前提。
- [ ] 写失败测试：执行新验证器时，若 `project/RUNBOOK.md` 存在，输出 `ERROR: duplicate operational runbook: project/RUNBOOK.md` 并退出 1。
- [ ] 删除 `project/RUNBOOK.md`；实现验证器并运行该测试至通过。
- [ ] 运行 `git diff --check`，只提交这三个文档和测试/验证器；提交消息 `docs: consolidate operational runbooks`。

### Task 2: 建立脚本状态清单

**Files:**

- Create: `project/SCRIPTS.md`
- Modify: `project/*.py` 的模块 docstring（仅缺少状态的文件）
- Modify: 文档验证器与其 pytest 文件

- [ ] 将每个 `project/` 顶层 `.py` 文件列入 `project/SCRIPTS.md` 表格，字段为路径、状态（`active` / `diagnostic` / `migration` / `retired`）、唯一用途、正式 CLI 替代项或“无替代项”。
- [ ] 将发布/采集辅助、只读探针、一次性回补与已退役 IC 谱系工具分类；不移动或改名任何脚本。
- [ ] 给缺少状态的脚本 docstring 添加一行 `Status: <status>.`；不得把脚本描述为正式发布器，正式发布一律指向 `python -m stock_quant ...`。
- [ ] 写失败测试：从 `project/SCRIPTS.md` 提取反引号中的 `project/*.py` 路径，断言集合恰等于顶层脚本集合；验证不存在未分类文件。
- [ ] 运行定向 pytest、`git diff --check`，仅提交清单、必要 docstring、测试/验证器；提交消息 `docs: classify project scripts`。

### Task 3: 记录资产保留与最终验证

**Files:**

- Create: `docs/operations/asset-retention.md`
- Modify: `README.md`
- Modify: 文档验证器与其 pytest 文件

- [ ] 编写资产规则，明确不可删除的内容寻址数据集、原始证据、验收/实验工件；可再生且不提交的 `.venv/`、缓存、CodeGraph、Superpowers 工作区；以及需人工确认后才能删除的 `phase-one-*.bundle`。
- [ ] 在 README 的导航区域加入根 RUNBOOK、`project/SCRIPTS.md` 和资产规则链接，不复制操作步骤。
- [ ] 写失败测试：缺少 `docs/operations/asset-retention.md`、缺少 `project/SCRIPTS.md` 或 README 未链接根 RUNBOOK 时，验证器必须失败并命名缺失项。
- [ ] 运行 `pytest` 的文档治理定向测试、`python tools/check_context_governance.py --root .`、新的验证器和 `git diff --check`；再运行默认 `pytest -q` 并记录完整退出结果。
- [ ] 仅提交本任务文件；提交消息 `docs: document asset retention`。
