# 上下文与规则治理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立分层、按需加载且自动校验的代理上下文治理体系。

**Architecture:** 新增协议入口、路径规则、架构事实层和 ADR 层；保留 README、RUNBOOK、PROJECT_MEMORY 与 `docs/superpowers` 历史材料。Python 标准库检查器验证治理文档，pytest 以子进程调用它。

**Tech Stack:** Markdown、YAML frontmatter、Python 3.10 标准库、pytest。

**Spec:** `docs/superpowers/specs/2026-09-14-context-governance-design.md`

## Global Constraints

- 不修改 `src/`、`project/configs/`、数据或运行时行为。
- 不删除、移动或批量重写既有文档与历史计划。
- 根入口最多 120 行；新增架构、ADR、规则文档各自最多 400 行。
- 优先级固定为：用户即时指令 > 安全与平台指令 > 路径规则 > 根协议层 > 架构不变量 > ADR > 功能规格 > 运维记录 > 历史资料。
- 所有校验仅使用 Python 标准库，默认 `pytest` 必须通过。

---

### Task 1: 创建检查器和最小红绿测试

**Files:**

- Create: `tools/check_context_governance.py`
- Create: `tests/unit/test_context_governance_docs.py`

**Interfaces:** `python tools/check_context_governance.py [--root PATH]`；成功退出 `0`，每个违反项输出 `ERROR: <message>` 且退出 `1`。

- [ ] 写入失败测试，使用 `subprocess.run([sys.executable, str(CHECKER), "--root", str(tmp_path)], capture_output=True, text=True, check=False)` 执行检查器；断言空目录退出 `1`，并包含 `ERROR: missing required file: AGENTS.md` 与 `CLAUDE.md`。
- [ ] 运行 `pytest tests/unit/test_context_governance_docs.py::test_checker_reports_missing_root_protocol -v`；确认因检查器不存在而失败。
- [ ] 实现 `REQUIRED_FILES`：根入口、四份架构文档和 ADR 索引。`validate(root: Path) -> list[str]` 对不存在的路径返回 `missing required file: <path>`；`argparse` 处理 `--root`，主函数逐行输出 `ERROR:` 并以错误数决定退出码。
- [ ] 重跑同一测试，确认 PASS；提交 `tools/check_context_governance.py` 与测试，消息为 `test: add context governance document checker`。

### Task 2: 为检查器加入完整结构契约

**Files:**

- Modify: `tools/check_context_governance.py`
- Modify: `tests/unit/test_context_governance_docs.py`

**Interfaces:** ADR 索引的 `[标题](NNN-slug.md)` 只可链接到同目录的存在文件；每个路径规则都必须包含 `Paths:` 和 `Read first:`。

- [ ] 编写 `create_complete_governance_tree(tmp_path)` 测试辅助函数；用它创建完整临时树后，让 ADR 索引写入 `[Missing](099-missing.md)`，断言检查器退出 `1` 并打印 `ERROR: indexed ADR does not exist: docs/adr/099-missing.md`；另写合法树退出 `0`、标准输出为空的测试。
- [ ] 运行失效 ADR 测试，确认当前实现红灯。
- [ ] 使用 `re.compile(r"\[[^]]+\]\(([^)]+\.md)\)")` 解析本地 ADR 链接。拒绝跳出 `docs/adr/`、不存在的目标、没有规则文件、规则缺少 `Paths:`、规则缺少 `Read first:`。限制 `AGENTS.md`、`CLAUDE.md` 为 120 行；限制架构文档、非索引 ADR 和规则为 400 行。
- [ ] 运行 `pytest tests/unit/test_context_governance_docs.py -v`，确认 PASS；提交检查器和测试，消息为 `feat: validate governance document structure`。

### Task 3: 创建根协议和路径规则

**Files:**

- Create: `AGENTS.md`
- Create: `CLAUDE.md`
- Create: `.claude/rules/data.md`
- Create: `.claude/rules/research.md`
- Create: `.claude/rules/portfolio.md`
- Create: `.claude/rules/config-and-operations.md`
- Create: `.claude/rules/tests.md`
- Modify: `tests/unit/test_context_governance_docs.py`

- [ ] 写入失败测试：`AGENTS.md` 必须含 `用户即时指令 > 安全与平台指令 > 路径规则` 和 `更具体的路径规则优先`；规则目录必须至少包含五个上述文件名。
- [ ] 运行该测试，确认 `AGENTS.md` 缺失导致红灯。
- [ ] 创建同义、薄的 `AGENTS.md` 与 `CLAUDE.md`：含优先级、冲突处理、按任务加载表、文档生命周期和最小改动/相关测试/不覆盖无关 WIP 约束。每条规则以 `Paths:` 和 `Read first:` 开头，分别覆盖数据、`research`+`backtest`、组合、项目配置与运维、测试；每条列出本领域不变量和验证命令。
- [ ] 运行 `pytest tests/unit/test_context_governance_docs.py -v`，确认 PASS；提交入口、规则和测试，消息为 `docs: add layered agent guidance`。

### Task 4: 创建当前架构事实层

**Files:**

- Create: `docs/architecture/overview.md`
- Create: `docs/architecture/module-map.md`
- Create: `docs/architecture/data-flow.md`
- Create: `docs/architecture/invariants.md`
- Modify: `tests/unit/test_context_governance_docs.py`

- [ ] 写入失败测试：overview 含 `reproducible`，module map 含 `data_sources`，data flow 含 `dataset`，invariants 含 `MUST NOT`。
- [ ] 运行此测试，确认文档不存在导致红灯。
- [ ] 编写四份小于 400 行的文档。`overview.md` 写项目定位及 Python、pandas、PyArrow、Pydantic、Typer 的角色；`module-map.md` 写数据源、数据模型、质量、研究、组合和报告的责任及依赖方向；`data-flow.md` 写根解析、采集、发布、验收、研究和报告；`invariants.md` 用 MUST/MUST NOT 固定禁止前视、不可变发布、失败发布、正式研究验收、PIT 证据、凭据和项目根回退。每份附“何时阅读”和权威边界。
- [ ] 运行 `pytest tests/unit/test_context_governance_docs.py -v && python tools/check_context_governance.py --root .`，确认 PASS 且检查器无输出；提交架构文件和测试，消息为 `docs: add architecture fact layer`。

### Task 5: 创建 ADR、接入导航并全量验证

**Files:**

- Create: `docs/adr/DECISIONS_INDEX.md`
- Create: `docs/adr/001-content-addressed-publication.md`
- Create: `docs/adr/002-real-data-acceptance.md`
- Create: `docs/adr/003-point-in-time-universe.md`
- Create: `docs/adr/004-walk-forward-oos.md`
- Create: `docs/adr/005-explicit-project-root.md`
- Modify: `README.md`
- Modify: `PROJECT_MEMORY.md`
- Modify: `tests/unit/test_context_governance_docs.py`

- [ ] 写失败测试：至少五份三位编号 ADR；每份以 YAML frontmatter 开始且被索引相对链接；README 含 `docs/architecture/`；PROJECT_MEMORY 含 `docs/adr/DECISIONS_INDEX.md`。
- [ ] 运行该测试，确认 ADR 目录缺失导致红灯。
- [ ] 每份 ADR 均含 `status`、`date`、`decision`、`affects` frontmatter 及 Context、Decision、Consequences、Rejected alternatives。依次记录内容寻址不可变发布、真实数据验收、PIT 股票池、预冻结 walk-forward OOS、显式项目根。索引表必须含状态、受影响路径、关键词和何时阅读。README 简介后加最小入口；PROJECT_MEMORY 文档维护约定指向新事实层和 ADR 索引。
- [ ] 运行 `python tools/check_context_governance.py --root . && pytest tests/unit/test_context_governance_docs.py -v && pytest -q`，确认三条命令全为 `0`；运行 `git diff --check`；仅暂存计划文件，提交消息为 `docs: record architecture decisions`。
