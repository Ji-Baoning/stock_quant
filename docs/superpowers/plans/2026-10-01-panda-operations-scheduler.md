# Panda 嫁接 · P4 操作面、单飞锁与调度器实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地总规格 Phase 4——`data update` 的 project-local flock 单飞锁与稳定机器可读冲突信号、`operations update` 外壳与 UpdateRunner、`data/service/jobs/<job_id>/` 持久 job 记录与心跳孤儿判定、默认禁用且仅环回的操作 API、systemd timer 单元与 RUNBOOK 运维节，使手工 CLI / Web / timer 三入口最终都经同一把锁单飞、每次运行都有持久可审计的 job 记录。

**Architecture:** 锁与冲突信号落在 CLI 层（`stock_quant/operations/update_lock.py` + `cli.py` 的 `data update` 接线）——内层 `data update` 子进程取锁，因此手工 CLI、`operations update`（内层仍调 `data update`）、Web API 与 timer 四条路天然共用同一把 flock；`operations/runner.py` 只以参数数组 spawn 子进程、镜像其退出码、把退出码 75 映射为 job FAILED/`update_already_running`（绝不匹配自由文本）；`operations/jobs.py` 持久化 job 目录并以 status.json 心跳 + boot_id 判存活；`operations/api.py` + `serve.py` 是独立 router/process（fastapi，`service` optional extra），默认禁用、启用仍只环回；`systemd/` 提交两个模板 unit，`operations/systemd_units.py` 提供纯渲染函数，真实安装/触发单列为 owner 授权步骤。

**Tech Stack:** Python 3.12（`/home/ji/miniconda3/envs/sq312/bin/python`）、typer、pytest、fcntl（Linux flock(2)）；Task 4 起新增 `service` optional extra（fastapi、uvicorn，spec §5.4）。无新数据源依赖、无 APScheduler。

**Spec:** [docs/superpowers/specs/2026-09-29-panda-data-loop-grafting-design.md](../specs/2026-09-29-panda-data-loop-grafting-design.md) §9 全部、§5.3、§5.4、§11 相关行。本计划是[总路线图](2026-10-01-panda-data-loop-grafting-implementation.md) Batch P4（Task 26–29）的阶段计划；P4 依赖 P3 契约冻结，开工前按总路线图 Global Constraints 核对上游漂移。

## Global Constraints

- 解释器 `/home/ji/miniconda3/envs/sq312/bin/python`；跑**点名测试文件**，不跑裸 `pytest`（integration 全量约 18.5 分钟）。
- **锁必须是 flock(2) 语义**（spec §9.2）：锁的生存期 = 持锁进程生存期，进程崩溃由内核自动释放，无 stale lock、无锁清理恢复路径；**不得**实现 PID lockfile（写 pid、比对 pid、删文件的那套全部禁止）。锁文件固定 `data/.locks/update.lock`（相对 resolved project root），恢复路径永不删除该文件。
- **冲突信号二选一已裁定为"专用非零退出码"**：退出码 `75`（sysexits `EX_TEMPFAIL`，"稍后再试"正是该状态），写死在常量 `UPDATE_ALREADY_RUNNING_EXIT_CODE = 75`；稳定 token 行 `update_already_running`（常量 `UPDATE_ALREADY_RUNNING_CODE`）仅供人/journal 阅读，消费者一律判退出码，**不得匹配自由文本**。
- 已有运行时：**不排队、不杀死当前任务**；手工 CLI 返回 75，操作 API 对自己的活跃 job 返回 409（同一冲突码字符串）。
- `operations update` **不预检锁**：撞锁时仍创建 job 并记 FAILED/`update_already_running` 后非零退出（spec §9.4 要求 timer 冲突在 journal 与 Web 两处可见）。
- **真实更新 / 真实发布 / 任何联网步骤一律需 owner 明确授权**（总路线图 Global Constraints）；本计划测试全部离线（stub 持锁进程、fake 子进程、环回 HTTP）。Task 4 的依赖安装与 Task 5 的一次真实 timer 触发各单列 owner 授权步骤。
- **凭据零容忍**：token 只从环境读；不进代码、配置、日志、fixture、报告；**不进 job 的 request.json / stdout.log / stderr.log / status.json / API 响应**；API 日志尾部对秘密值与项目绝对路径打码。
- **job id 不进 dataset version**（spec §9.2）：runner 只向内层 CLI 传 start/end/sources/disclosure-lookback-days 与 --root，job id 永不出现在子进程 argv；dataset version 仍由既有内容寻址机制决定。
- 允许透传给内层 CLI 的参数**只有** `start/end/sources/disclosure-lookback-days`，且按 CLI 同一类型约束验证（ISO 日期、非空 source 名、整数 ≥ 1）；spawn 一律参数数组，**禁 shell 字符串拼接**（spec §9.1）。
- 操作 API 只提供三个端点（POST/GET/GET），**不提供**取消、重试到成功、删除日志、验收、研究端点；失败后的再次运行必须产生**新 job id**（spec §9.3）。
- **错误响应信封与 P3 逐字节同形**：所有非 2xx 一律 `{"error": {"code": ..., ...}}`（`Content-Type: application/json`），包括 404——**不得**用 `HTTPException(detail=...)`，那会得到 FastAPI 默认的 `{"detail": ...}`，成为消费者唯一读不到 `code` 的响应。P3 在 [panda-query-service](2026-10-01-panda-query-service.md) 里冻结的是这个**信封形状**（`ErrorBody`/`ErrorResponse` + `register_error_handlers`）；P4 在自己的 `stock_quant.operations` 包里用 `JSONResponse` 产出同一形状，**不跨包 import 读服务的错误类**——两个服务是独立进程（spec §8.1/§9.1），共享的是线上格式而不是 Python 符号。
- systemd timer：`ExecStart` 只调 `operations update`；固定 unit 名前缀 `stock-quant-data-update@`（实例 = systemd-escape 的项目根）；时区 `Asia/Shanghai`；`Persistent=false`（停机错过不补跑）。调度器不调用 acceptance、research、report build 或清理命令（spec §9.4）。
- **保护在途 WIP**：工作区已修改 `src/stock_quant/cli.py`、`src/stock_quant/reporting/html.py`、`templates/experiment.html.j2`（即 `src/stock_quant/reporting/templates/experiment.html.j2`）、`RUNBOOK.md`、两份 spec（`docs/superpowers/specs/2026-09-29-panda-data-loop-grafting-design.md`、`docs/superpowers/specs/2026-09-27-rights-issue-booking-design.md`）与测试文件（2026-09-30 实测 `git status --short` 为 `tests/integration/test_cli.py`、`tests/integration/test_reports.py`、`tests/unit/test_fetch_coverage.py` 三处在途；**2026-10-01 实况已增加** `src/stock_quant/data_model/fetch_coverage.py`、`src/stock_quant/research/runner.py`、`tests/unit/test_table_tier_preflight.py`、`tests/integration/test_table_tier_preflight.py`，且 `docs/superpowers/specs/2026-09-27-rights-issue-booking-design.md` 是未跟踪新文件而非已修改；开工时以 `git status --short` 实况为准）——不覆盖、不回退、不暂存、不重排。对 **cli.py 与 RUNBOOK.md 的改动描述为未来执行时进行且只做本任务增量**（执行时先重读当刻文件，锚点漂移时按锚文本就近落点，不做任何无关重排）；本计划全部新测试写**新文件**，不改任何在途测试文件。
- `data_pipeline.py` 的 `DataPipeline.update()`（data_pipeline.py:871，现状无锁无互斥）**本计划不改**：规格 §9.2 把取锁义务放在 `data update` CLI 自身；13 个离线发布入口不经此路径，不属本批。
- 每任务独立提交，提交信息英文，结尾 `Co-Authored-By: Claude Code <noreply@anthropic.com>`；本计划不改任何门禁、不碰数据与生成物。

## 开工前必须知道的实现形态（先读再接）

1. **cli.py 是在途 WIP**（2026-09-30 `git status` 已修改）。现状锚点（执行 Task 1/2 时先重读当刻文件核对）：
   - `data update` 命令 `data_update`（当前 cli.py:303-362）：参数集 `--start/--end/--sources/--disclosure-lookback-days`（`typer.Option(None, min=1)`，即 `<int range> [x>=1]`）与 `--root`；命令体首两行为 `_enable_transport_logging()` 与 `project_root = _resolved_project_root(root)`；失败路径统一 `_echo_failure(...)` + `raise typer.Exit(code=1)`。
   - 顶部 import 区（`:35-94`）与 `app.add_typer(...)` 注册区（`:118-123`）是 Task 1/2 的两处落点。
   - `python -m stock_quant` 经 `src/stock_quant/__main__.py` 进入 `cli.app`。
2. **锁的进程语义**：flock 按 open file description 计——同一进程对同一文件**再次 open + flock(LOCK_NB) 也会失败**（`flock(2)` 文档明确）；因此"同进程第二次 acquire 抛冲突"是有效的单测断言，双进程冲突用真实子进程测。
3. **sq312 环境已 editable 安装本包**（实测 `/home/ji/miniconda3/envs/sq312/bin/python -c "import stock_quant"` 从任意 cwd 指向 `/home/ji/work/program/stock/src`）：runner 用 `sys.executable -m stock_quant ...` spawn 子进程、测试用 `subprocess.Popen([sys.executable, "-m", ...])` 都不依赖 cwd/PYTHONPATH。
4. **fastapi/uvicorn 尚未安装**（2026-09-30 实测 `import fastapi` ModuleNotFoundError；`pyproject.toml` 无 `[project.optional-dependencies]`；`environment.yml` 也没有）。Task 4 有 owner 授权的安装前置步骤；若 P3 已先落地 service extra，该步退化为核对。
5. **boot_id 实测可用**：`/proc/sys/kernel/random/boot_id` 存在（Linux-only，本仓目标平台即 Linux）；runner 把它随心跳写进 status.json，防 PID 复用（spec §9.2）。
6. **ADR-021 现状**：`docs/adr/` 实测只有两个 020 与 022，**021 空缺**——ADR-021（常驻查询面与调度正名）是总路线图 P0 的 Task 2 任务卡。开工本计划前若 021 仍未成文，把该事实报告 owner（它是本批"操作面默认禁用、只环回、调度只调外壳"的决策依据）；本计划不代写 ADR。
7. **测试惯例**：`tests/integration/conftest.py:954` 的 `cli_runner` fixture（Typer CliRunner，函数级）；`build_fixture_project(root, *, broken=False)` 构造完整合成工程（含已发布数据集），`tests/integration` 无 `__init__.py`，新测试文件用 `from conftest import build_fixture_project`（照 `tests/integration/test_cli.py:18`）。`tests/unit` 有 `__init__.py`。
8. **CLI 冲突先于一切慢路径**：锁在 `_resolved_project_root` 之后立即获取，先于 `DataPipeline` 构造与任何 transport/凭据解析——离线测试无需网络即可触发冲突路径；既有离线行为参考 `tests/integration/test_cli.py` 的 `test_data_update_without_transport_fails_and_prints_failed`（无 `TUSHARE_TRANSPORT` 时 `data update` 在锁之后失败、exit 1）。
9. **成功态的 run_id/dataset_version 来源是 CLI 稳定 key=value 契约行**（`run_id=...`、`dataset_version=...`，cli.py:347/357）：runner 只按整行前缀解析这组契约键——这与 §9.1 禁止的"匹配自由文本猜失败原因"不同类：失败映射只认退出码 75。
10. **API 的 409 语义裁定**（spec §11"两次更新并发：首个持锁，第二个 409/非零退出"的二选一空间）：操作 API 只对**自己的**活跃 job（新鲜心跳的 RUNNING 或新近 QUEUED）返回 409 并附 job_id；若锁被手工 CLI 持有（job 体系外），POST 返回 201、产生的 job 由内层锁裁定为 FAILED/`update_already_running`——flock 是跨三入口的**最终**单飞裁决者，API 预检不做锁探测（探测会在自身 spawn 窗口引入假冲突）。
11. **systemd 实例名**：`systemd-escape -p /home/ji/work/program/stock` → `home-ji-work-program-stock`；unit 文件内 `%f` 把实例名还原为绝对路径（`--root %f`），`%i` 是转义实例名（`Unit=` 行使用）。`OnCalendar=*-*-* 17:10:00 Asia/Shanghai` 的时区后缀自 systemd v235 起支持。
12. **RUNBOOK 追加点 = 文件末尾**（现末节为 `## 已知边界（务必记住，不是 bug）`，其后追加本批新节，不动任何既有行）。`tests/unit/test_operational_docs.py` 与 `tests/unit/test_context_governance_docs.py` 是文档治理的邻居测试。
13. **jobs 目录命名空间**：`data/service/jobs/`（磁盘目录）归 operations 持久化；P3 的 Python 包 `src/stock_quant/service/` 是另一回事，无冲突。
14. 本计划新增的 systemd/ 目录与 `operations/` 包不触碰 `project/SCRIPTS.md`（不新增 `project/*.py`）。

## 文件结构

**新增**

| 文件 | 职责 |
| --- | --- |
| `src/stock_quant/operations/__init__.py` | operations 包声明 |
| `src/stock_quant/operations/update_lock.py` | flock 单飞锁 + 冲突常量（75 / update_already_running） |
| `src/stock_quant/operations/jobs.py` | `data/service/jobs/<job_id>/` 持久化：request 一次写定、追加日志、原子 status.json、心跳、孤儿自检、读取器 |
| `src/stock_quant/operations/runner.py` | UpdateRunner：参数数组 spawn、允许参数校验、退出码映射、心跳循环、SIGTERM 取消 |
| `src/stock_quant/operations/api.py` | 操作 API router + app 工厂（默认禁用→503；三端点；脱敏日志尾部） |
| `src/stock_quant/operations/serve.py` | API 进程启动器：`--enable` 必需、拒绝非环回绑定 |
| `src/stock_quant/operations/systemd_units.py` | systemd-escape 渲染与 unit 名（纯函数，不安装） |
| `systemd/stock-quant-data-update@.service` | timer 触发的 oneshot 服务：只调 `operations update --root %f` |
| `systemd/stock-quant-data-update@.timer` | 固定时区 Asia/Shanghai、Persistent=false |
| `tests/unit/test_update_lock.py` | 锁语义（同进程二次 acquire、内核释放） |
| `tests/unit/test_operations_runner.py` | argv 构造、参数校验、退出码映射、契约行解析 |
| `tests/unit/test_operations_jobs.py` | 持久化语义、孤儿判定、读取器、词汇冻结 |
| `tests/unit/test_operations_systemd.py` | 转义/单元名渲染 + 已提交 unit 文件契约 |
| `tests/integration/test_operations_cli.py` | CLI 冲突路径、operations update、runner 编排（真/假子进程） |
| `tests/integration/test_operations_api.py` | API 三端点、409/422/404、脱敏、路由清单、serve 拒绝 |

**修改（全部为未来执行时的增量；cli.py 与 RUNBOOK.md 是在途 WIP，只做下述增量）**

| 文件 | 改动 |
| --- | --- |
| `src/stock_quant/cli.py` | Task 1：import + `data update` 取锁接线（冲突 → 打 token 行 + FAILED 行 + exit 75）；Task 2：import + `operations_app` 注册 + `operations update` 命令 |
| `pyproject.toml` | Task 4：`[project.optional-dependencies] service` 的 `fastapi>=0.115` / `uvicorn>=0.30`（**下限以 P3 为准**——该 extra 由 P3 Task 2 拥有；P3 已加则不重复声明，只核对下限一致） |
| `RUNBOOK.md` | Task 6：文件末尾**只追加**"阶段 10 · 操作面与调度"新节（**不是**阶段 9——阶段 9 已被 P2b membership 计划占用，后者按批次顺序先落地） |
| `docs/architecture/overview.md`、`module-map.md`、`data-flow.md` | Task 6 Step 2：§5.3 同变更义务的操作面条目（条件性：P3 已同步三件套则只追加操作面行） |
| `docs/superpowers/plans/2026-10-01-panda-data-loop-grafting-implementation.md` | Task 6 Step 3：阶段计划表把本计划一行标为已成文（只改该行） |

---

### Task 1: data update 的 flock 单飞锁与稳定冲突信号

**Files:**
- Create: `src/stock_quant/operations/__init__.py`、`src/stock_quant/operations/update_lock.py`
- Modify: `src/stock_quant/cli.py`（`data_update` 命令体内取锁接线 + 顶部 import；在途 WIP，执行时重读，锚点见下）
- Test: `tests/unit/test_update_lock.py`（新）、`tests/integration/test_operations_cli.py`（新）

**Interfaces:**
- Produces（后续任务依赖的精确名字）:
  - `UPDATE_ALREADY_RUNNING_EXIT_CODE = 75`（`int`）
  - `UPDATE_ALREADY_RUNNING_CODE = "update_already_running"`（`str`）
  - `UpdateAlreadyRunning(RuntimeError)`（属性 `lock_path: Path`）
  - `acquire_update_lock(project_root: Path) -> UpdateLock`（非阻塞；持锁抛 `UpdateAlreadyRunning`）
  - `UpdateLock(path: Path, fd: int)`（fd 故意不关闭：进程退出/崩溃由内核释放）
  - `UPDATE_LOCK_RELATIVE_PATH = Path("data/.locks/update.lock")`
  - CLI 行为契约：锁被持时 `data update` 输出 token 行 `update_already_running` 与 `FAILED:` 行、退出码 75、不排队、不影响持锁者。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_update_lock.py`（新文件，全文）：

```python
"""flock single-flight semantics for the data-update lock (spec 9.2).

The lock's lifetime IS the holder process's lifetime: the kernel releases
it on exit or crash, so recovery never touches the lock file.  These tests
exercise that contract with real file descriptors and one real holder
subprocess; nothing here needs a network, a token or a dataset.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from stock_quant.operations.update_lock import (
    UPDATE_ALREADY_RUNNING_EXIT_CODE,
    UPDATE_LOCK_RELATIVE_PATH,
    UpdateAlreadyRunning,
    acquire_update_lock,
)


def _minimal_project(tmp_path: Path) -> Path:
    """A directory that satisfies ``resolve_project_root``'s config check."""
    configs = tmp_path / "configs"
    configs.mkdir()
    for name in ("project.yml", "sources.yml", "costs.yml"):
        (configs / name).write_text("{}\n", encoding="utf-8")
    return tmp_path


def test_constants_are_frozen_as_decided():
    assert UPDATE_ALREADY_RUNNING_EXIT_CODE == 75
    assert UPDATE_LOCK_RELATIVE_PATH == Path("data/.locks/update.lock")


def test_a_second_acquire_in_the_same_process_raises():
    # flock() judges per open file description: a second open() in the same
    # process is a second claim on the same lock and must be refused.
    root = _minimal_project(Path("/tmp") / "does-not-matter" if False else None) \
        if False else _minimal_project(pytest.tmp_folder)
    first = acquire_update_lock(root)
    try:
        with pytest.raises(UpdateAlreadyRunning):
            acquire_update_lock(root)
        assert first.path == root / UPDATE_LOCK_RELATIVE_PATH
        assert first.path.is_file()
    finally:
        import os

        os.close(first.fd)  # test hygiene only; production never closes it


_HOLDER_SCRIPT = """\
import sys, time
from pathlib import Path
from stock_quant.operations.update_lock import acquire_update_lock
acquire_update_lock(Path(sys.argv[1]))
print("held", flush=True)
time.sleep(float(sys.argv[2]))
"""


def test_the_kernel_releases_the_lock_when_the_holder_dies(tmp_path):
    root = _minimal_project(tmp_path)
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER_SCRIPT, str(root), "120"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(UpdateAlreadyRunning):
            acquire_update_lock(root)
        holder.kill()
        holder.wait(timeout=10)
        # No cleanup, no waiting, no lock-file deletion: the next acquire
        # succeeds at once against the very same file.
        lock = acquire_update_lock(root)
        import os

        os.close(lock.fd)
        assert lock.path.is_file()
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=10)
```

（注意：`test_a_second_acquire_in_the_same_process_raises` 里那两行 `if False` 折叠是笔误示例不得照抄——实际写入时用 `tmp_path` fixture：`def test_a_second_acquire_in_the_same_process_raises(tmp_path):` 并 `root = _minimal_project(tmp_path)`。以本框下面这版为准：）

```python
def test_a_second_acquire_in_the_same_process_raises(tmp_path):
    # flock() judges per open file description: a second open() in the same
    # process is a second claim on the same lock and must be refused.
    root = _minimal_project(tmp_path)
    first = acquire_update_lock(root)
    try:
        with pytest.raises(UpdateAlreadyRunning):
            acquire_update_lock(root)
        assert first.path == root / UPDATE_LOCK_RELATIVE_PATH
        assert first.path.is_file()
    finally:
        os.close(first.fd)  # test hygiene only; production never closes it
```

（文件顶部 import 因此需要 `import os`。）

`tests/integration/test_operations_cli.py`（新文件，本任务先写冲突路径部分，全文）：

```python
"""CLI-visible single-flight behaviour of ``data update`` (spec 9.1/9.2).

The conflict path must be reachable fully offline: the lock is taken right
after root resolution, before any transport or credential is ever touched.
The multiprocess test below uses a real holder subprocess so "does not
queue, does not kill the first runner" is asserted against a real process,
not a mock.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from conftest import build_fixture_project

from stock_quant.cli import app
from stock_quant.operations.update_lock import (
    UPDATE_ALREADY_RUNNING_CODE,
    UPDATE_ALREADY_RUNNING_EXIT_CODE,
    acquire_update_lock,
)

_HOLDER_SCRIPT = """\
import sys, time
from pathlib import Path
from stock_quant.operations.update_lock import acquire_update_lock
acquire_update_lock(Path(sys.argv[1]))
print("held", flush=True)
time.sleep(float(sys.argv[2]))
"""


def _offline_env() -> dict:
    env = os.environ.copy()
    env.pop("TUSHARE_TRANSPORT", None)
    env.pop("TUSHARE_TOKEN", None)
    return env


def test_a_conflicting_data_update_exits_75_with_the_token_and_never_queues(
    cli_runner, fixture_root
):
    lock = acquire_update_lock(fixture_root.root)
    try:
        started = time.monotonic()
        result = cli_runner.invoke(
            app,
            [
                "data",
                "update",
                "--start",
                "2021-11-01",
                "--end",
                "2021-11-30",
                "--root",
                str(fixture_root.root),
            ],
        )
        elapsed = time.monotonic() - started
        assert result.exit_code == UPDATE_ALREADY_RUNNING_EXIT_CODE
        assert UPDATE_ALREADY_RUNNING_CODE in result.stdout
        assert "FAILED" in result.stdout
        assert elapsed < 10  # 不排队：立即以稳定冲突码退出
        # 冲突处理既没有偷走也没有释放持锁者的锁。
        again = cli_runner.invoke(app, ["data", "update", "--root", str(fixture_root.root)])
        assert again.exit_code == UPDATE_ALREADY_RUNNING_EXIT_CODE
    finally:
        os.close(lock.fd)  # 归还会话级 fixture 工程的锁，供其他测试使用


def test_two_processes_second_gets_conflict_code_first_survives(tmp_path):
    project = build_fixture_project(tmp_path / "p")
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER_SCRIPT, str(project.root), "120"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        started = time.monotonic()
        conflicting = subprocess.run(
            [
                sys.executable,
                "-m",
                "stock_quant",
                "data",
                "update",
                "--start",
                "2021-11-01",
                "--end",
                "2021-11-30",
                "--root",
                str(project.root),
            ],
            capture_output=True,
            text=True,
            env=_offline_env(),
            timeout=60,
        )
        elapsed = time.monotonic() - started
        assert conflicting.returncode == UPDATE_ALREADY_RUNNING_EXIT_CODE
        assert UPDATE_ALREADY_RUNNING_CODE in conflicting.stdout
        assert elapsed < 30  # 显著短于持锁者的 120s：没有排队等待
        assert holder.poll() is None  # 首个持锁者未被杀死、未被干扰

        holder.terminate()
        holder.wait(timeout=10)
        # 崩溃/退出后由内核释放：同一锁文件、无清理动作，下一次更新不再冲突
        #（离线环境下它以 transport 缺失失败，退出码 1 而不是 75）。
        after = subprocess.run(
            [
                sys.executable,
                "-m",
                "stock_quant",
                "data",
                "update",
                "--root",
                str(project.root),
            ],
            capture_output=True,
            text=True,
            env=_offline_env(),
            timeout=120,
        )
        assert after.returncode == 1
        assert "FAILED" in after.stdout
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=10)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_update_lock.py tests/integration/test_operations_cli.py -q`
Expected: FAIL — collection error `ModuleNotFoundError: No module named 'stock_quant.operations'`（两个文件都在 import 行失败；这就是本任务要新增的包）。

- [ ] **Step 3: 最小实现**

`src/stock_quant/operations/__init__.py`（新文件，全文）：

```python
"""Operations surface: single-flight locking, supervised updates, jobs."""
```

`src/stock_quant/operations/update_lock.py`（新文件，全文）：

```python
"""Project-local single-flight advisory lock for ``data update`` (spec 9.2).

flock(2) semantics, on purpose: the lock's lifetime IS the holder process's
lifetime.  The kernel releases it when the holder exits or crashes for any
reason, so there is no stale lock, no pid file to trust, and no recovery
path that ever deletes or rewrites the lock file.  The lock file lives
inside the project root at ``data/.locks/update.lock``; every entry point
(the manual CLI, the ``operations update`` shell whose inner child is the
same CLI, the operations API and the systemd timer) funnels through the
inner ``data update`` process, so they all contend on this one lock.
"""

from __future__ import annotations

import fcntl
import os
from dataclasses import dataclass
from pathlib import Path

#: Stable machine-readable conflict signal (spec 9.1): the dedicated-exit-code
#: option, frozen here.  75 is sysexits ``EX_TEMPFAIL`` -- "try again later",
#: which is exactly this state.  Consumers (UpdateRunner, operations API,
#: operator scripts) key on this code; they must never match prose.
UPDATE_ALREADY_RUNNING_EXIT_CODE = 75

#: The same conflict as a fixed token, printed on its own line for humans
#: and the journal.  It is a readability echo, not a parsing contract.
UPDATE_ALREADY_RUNNING_CODE = "update_already_running"

#: The advisory lock file, relative to the resolved project root.
UPDATE_LOCK_RELATIVE_PATH = Path("data") / ".locks" / "update.lock"


class UpdateAlreadyRunning(RuntimeError):
    """Another process holds this project root's update lock."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        super().__init__(
            "another data update already holds this project root's lock "
            "(flock on data/.locks/update.lock); not queuing, not killing it"
        )


@dataclass(frozen=True)
class UpdateLock:
    """An acquired single-flight lock.

    ``fd`` is deliberately never closed by anyone: it stays open for the
    holder process's whole lifetime and the kernel drops the lock with it.
    There is no ``release()`` on purpose -- recovery never involves the lock
    file (no deletion, no truncation, no pid comparisons).
    """

    path: Path
    fd: int


def acquire_update_lock(project_root: Path) -> UpdateLock:
    """Acquire the project's update lock or raise :class:`UpdateAlreadyRunning`.

    Non-blocking by design: a held lock is reported immediately -- nothing
    queues and the current holder is never disturbed (spec 9.2).
    """
    lock_path = Path(project_root) / UPDATE_LOCK_RELATIVE_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        raise UpdateAlreadyRunning(lock_path) from None
    return UpdateLock(path=lock_path, fd=descriptor)
```

`src/stock_quant/cli.py` 增量一（顶部 import 区，`from stock_quant.project_root import ...` 一行之后追加）：

```python
from stock_quant.operations.update_lock import (
    UPDATE_ALREADY_RUNNING_CODE,
    UPDATE_ALREADY_RUNNING_EXIT_CODE,
    UpdateAlreadyRunning,
    acquire_update_lock,
)
```

`src/stock_quant/cli.py` 增量二（`data_update` 命令体内；执行时以当刻文件为准，锚点为 `project_root = _resolved_project_root(root)` 这一行，紧随其后插入，其余命令体不动）：

```python
    project_root = _resolved_project_root(root)
    try:
        acquire_update_lock(project_root)
    except UpdateAlreadyRunning:
        # Stable conflict signal (spec 9.1/9.2): exit code 75 plus the fixed
        # token line.  Nothing queues, the holder is never disturbed, and no
        # cleanup is ever needed -- the kernel owns the lock's lifetime.
        typer.echo(UPDATE_ALREADY_RUNNING_CODE)
        _echo_failure(
            "another data update already holds this project root's lock"
        )
        raise typer.Exit(code=UPDATE_ALREADY_RUNNING_EXIT_CODE) from None
```

（锁对象不保存引用是刻意的：裸 fd 没有析构关闭，进程退出即由内核释放；本行先于 `DataUpdateRequest`/`DataPipeline`/transport 解析执行。）

- [ ] **Step 4: 跑测试确认通过，并跑邻居**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_update_lock.py tests/integration/test_operations_cli.py -q`
Expected: PASS（5 项：`test_update_lock.py` 3 项 + 本任务 `test_operations_cli.py` 2 项）。

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_cli.py tests/integration/test_project_root_cli.py -q`
Expected: PASS（在途 WIP 的 test_cli.py 只跑不改；`data update` 的既有用例必须仍绿——锁的获取在其失败路径之前不改变任何离线行为）。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/operations/__init__.py src/stock_quant/operations/update_lock.py src/stock_quant/cli.py tests/unit/test_update_lock.py tests/integration/test_operations_cli.py
git commit -m "feat(operations): single-flight flock lock and stable conflict signal for data update" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: `operations update` CLI + UpdateRunner（含 JobStore 核心）

**Files:**
- Create: `src/stock_quant/operations/runner.py`、`src/stock_quant/operations/jobs.py`（本任务先落 runner 所需的核心持久化原语；Task 3 再补孤儿自检与读取器）
- Modify: `src/stock_quant/cli.py`（顶部 import 追加 + `operations_app` 注册 + `operations update` 命令；在途 WIP，只做本任务增量）
- Test: `tests/unit/test_operations_runner.py`（新）、`tests/integration/test_operations_cli.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `UPDATE_ALREADY_RUNNING_EXIT_CODE`、`UPDATE_ALREADY_RUNNING_CODE`。
- Produces（Task 3/4/5 依赖的精确名字与签名）:
  - `UpdateRunParams(start: date | None, end: date | None, sources: tuple[str, ...] | None, disclosure_lookback_days: int | None)`，`.to_payload() -> dict`
  - `InvalidUpdateParams(parameter: str, reason: str)`
  - `validate_update_params(*, start=None, end=None, sources=None, disclosure_lookback_days=None) -> UpdateRunParams`（sources 接受逗号串或序列；其余同 CLI 类型约束）
  - `build_data_update_argv(project_root: Path, params: UpdateRunParams) -> list[str]`
  - `build_operations_update_argv(project_root, params, *, job_id: str | None = None) -> list[str]`
  - `classify_child_exit(code: int) -> tuple[str, str | None]`
  - `parse_contract_lines(text: str) -> dict[str, str]`
  - `operations_exit_code(result: OperationsUpdateResult) -> int`（0 / 75 / 1）
  - `read_boot_id(path: Path = BOOT_ID_PATH) -> str`、`BOOT_ID_PATH`
  - `HEARTBEAT_INTERVAL_SECONDS = 30.0`
  - `run_operations_update(project_root, params, *, job_id=None, entrypoint="operations_cli", heartbeat_interval_seconds=30.0, boot_id=None, clock=None, popen=subprocess.Popen) -> OperationsUpdateResult`（`popen/clock/boot_id/heartbeat_interval_seconds` 是测试注入点）
  - `OperationsUpdateResult(job_id, status, exit_code, failure_reason=None, run_id=None, dataset_version=None)`
  - jobs 核心：`JobStore(project_root)` 及 `.create(request: Mapping, *, job_id=None)`、`.log_paths(job_id) -> tuple[Path, Path]`、`.mark_running(job_id, *, pid, boot_id, now=None)`、`.heartbeat(job_id, *, now=None)`、`.mark_succeeded(job_id, *, run_id, dataset_version, exit_code=0, now=None)`、`.mark_failed(job_id, *, reason, exit_code, now=None, detail=None)`、`.mark_cancelled_by_shutdown(job_id, *, now=None)`、`.get(job_id)`；`JobRecord`；`new_job_id(*, now=None)`；`QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED_BY_SHUTDOWN`；`JOB_STATUSES`；`FAILURE_UPDATE_ALREADY_RUNNING`、`FAILURE_UPDATE_FAILED`；`JOBS_RELATIVE_PATH`；异常 `JobNotFound/JobAlreadyExists/JobStateError`
  - CLI 契约：`operations update` 打印 `job_id=`、`status=`，成功附 `run_id=`/`dataset_version=`，冲突附 token 行，退出码 0/1/75；`--job-id` 采用预建 QUEUED job。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_operations_runner.py`（新文件，全文）：

```python
"""Pure surfaces of the UpdateRunner: argv building, CLI-mirroring
parameter validation, exit-code mapping and the success contract-line
parser (spec 9.1).  The orchestration itself is exercised with real and
fake child processes in tests/integration/test_operations_cli.py.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

from stock_quant.operations.runner import (
    HEARTBEAT_INTERVAL_SECONDS,
    InvalidUpdateParams,
    OperationsUpdateResult,
    build_data_update_argv,
    build_operations_update_argv,
    classify_child_exit,
    operations_exit_code,
    parse_contract_lines,
    read_boot_id,
    validate_update_params,
)
from stock_quant.operations.update_lock import UPDATE_ALREADY_RUNNING_EXIT_CODE


def test_the_inner_argv_is_an_argument_array_with_only_allowed_options():
    argv = build_data_update_argv(
        Path("/tmp/p"),
        validate_update_params(
            start="2026-09-01",
            end="2026-09-30",
            sources="tushare, akshare",
            disclosure_lookback_days=120,
        ),
    )
    assert argv == [
        sys.executable,
        "-m",
        "stock_quant",
        "data",
        "update",
        "--root",
        "/tmp/p",
        "--start",
        "2026-09-01",
        "--end",
        "2026-09-30",
        "--sources",
        "tushare,akshare",
        "--disclosure-lookback-days",
        "120",
    ]


def test_no_job_id_or_shell_string_ever_enters_the_spawned_argv():
    # The job id must never reach the update path (spec 9.2: job ids do not
    # enter dataset versions), and no argv is ever a shell string.
    argv = build_data_update_argv(Path("/tmp/p"), validate_update_params())
    assert isinstance(argv, list)
    assert all(isinstance(item, str) for item in argv)
    assert not any("job_" in item for item in argv)
    outer = build_operations_update_argv(
        Path("/tmp/p"), validate_update_params(), job_id="job_20261001T1010Z_abcdef12"
    )
    assert outer[-2:] == ["--job-id", "job_20261001T1010Z_abcdef12"]
    assert not any("job_" in item for item in outer[:-2])


def test_params_reject_what_the_cli_would_reject():
    with pytest.raises(InvalidUpdateParams) as error:
        validate_update_params(start="2026-9-1")
    assert error.value.parameter == "start"

    with pytest.raises(InvalidUpdateParams) as error:
        validate_update_params(end="not-a-date")
    assert error.value.parameter == "end"

    with pytest.raises(InvalidUpdateParams) as error:
        validate_update_params(disclosure_lookback_days=0)
    assert error.value.parameter == "disclosure_lookback_days"

    with pytest.raises(InvalidUpdateParams) as error:
        validate_update_params(disclosure_lookback_days=True)
    assert error.value.parameter == "disclosure_lookback_days"

    with pytest.raises(InvalidUpdateParams) as error:
        validate_update_params(sources="tushare,,akshare")
    assert error.value.parameter == "sources"


def test_params_accept_the_cli_shape_everywhere():
    params = validate_update_params(
        sources=["tushare", "akshare"], disclosure_lookback_days=1
    )
    assert params.sources == ("tushare", "akshare")
    assert validate_update_params() == validate_update_params(sources=None)


def test_exit_codes_map_onto_the_job_vocabulary():
    assert classify_child_exit(0) == ("SUCCEEDED", None)
    assert classify_child_exit(UPDATE_ALREADY_RUNNING_EXIT_CODE) == (
        "FAILED",
        "update_already_running",
    )
    assert classify_child_exit(1) == ("FAILED", "update_failed")
    assert classify_child_exit(2) == ("FAILED", "update_failed")


def test_contract_line_parsing_reads_only_stable_keys():
    text = (
        "INFO stock_quant.data_sources.tushare: resolved transport=relay\n"
        "run_id=data_update_abc123\n"
        "resolved_end_date=2026-09-30\n"
        "some free prose about a failure, run_id= not at line start anyway\n"
        "dataset_version="
        + "ab"
        * 32
        + "\n"
        "PASS\n"
    )
    assert parse_contract_lines(text) == {
        "run_id": "data_update_abc123",
        "resolved_end_date": "2026-09-30",
        "dataset_version": "ab" * 32,
    }


def test_operations_exit_code_mirrors_success_and_conflict():
    assert (
        operations_exit_code(
            OperationsUpdateResult(job_id="j", status="SUCCEEDED", exit_code=0)
        )
        == 0
    )
    conflict = OperationsUpdateResult(
        job_id="j", status="FAILED", exit_code=75, failure_reason="update_already_running"
    )
    assert operations_exit_code(conflict) == UPDATE_ALREADY_RUNNING_EXIT_CODE
    failed = OperationsUpdateResult(
        job_id="j", status="FAILED", exit_code=1, failure_reason="update_failed"
    )
    assert operations_exit_code(failed) == 1


def test_boot_id_reads_the_kernel_value(tmp_path):
    boot_file = tmp_path / "boot_id"
    boot_file.write_text("2fdf3cf0-97bf-4896-bd6f-1a5b8841637d\n", encoding="utf-8")
    assert read_boot_id(boot_file) == "2fdf3cf0-97bf-4896-bd6f-1a5b8841637d"


def test_the_heartbeat_cadence_is_well_under_the_orphan_threshold():
    assert HEARTBEAT_INTERVAL_SECONDS == 30.0
```

`tests/integration/test_operations_cli.py` 追加（接在 Task 1 的两个用例之后）：

```python
# --------------------------------------------------------------------------- #
# operations update + UpdateRunner (spec 9.1/9.2)
# --------------------------------------------------------------------------- #

import json

from stock_quant.operations.jobs import (
    CANCELLED_BY_SHUTDOWN,
    FAILED,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    JobStore,
)
from stock_quant.operations.runner import (
    UpdateRunParams,
    operations_exit_code,
    run_operations_update,
)
from stock_quant.operations.update_lock import UPDATE_ALREADY_RUNNING_EXIT_CODE

_SUCCESS_CHILD = """\
print("run_id=data_update_fixture01")
print("dataset_version=" + "ab" * 32)
print("PASS")
"""

_CONFLICT_CHILD = """\
import sys
from stock_quant.operations.update_lock import UPDATE_ALREADY_RUNNING_EXIT_CODE
sys.exit(UPDATE_ALREADY_RUNNING_EXIT_CODE)
"""

_FAIL_CHILD = "import sys\nsys.exit(3)\n"


def _fake_popen(script: str):
    def popen(argv, *, stdout, stderr, **kwargs):
        return subprocess.Popen(
            [sys.executable, "-c", script], stdout=stdout, stderr=stderr
        )

    return popen


def _job_ids(project_root) -> list:
    store = JobStore(project_root)
    if not store.root.is_dir():
        return []
    return sorted(child.name for child in store.root.iterdir() if child.is_dir())


def test_operations_update_records_a_failed_conflict_job(cli_runner, tmp_path):
    project = build_fixture_project(tmp_path / "p")
    lock = acquire_update_lock(project.root)
    try:
        result = cli_runner.invoke(
            app,
            [
                "operations",
                "update",
                "--start",
                "2021-11-01",
                "--end",
                "2021-11-30",
                "--root",
                str(project.root),
            ],
        )
        assert result.exit_code == UPDATE_ALREADY_RUNNING_EXIT_CODE
        assert "job_id=" in result.stdout
        assert "status=FAILED" in result.stdout
        assert UPDATE_ALREADY_RUNNING_CODE in result.stdout
        job_id = next(
            line.split("=", 1)[1]
            for line in result.stdout.splitlines()
            if line.startswith("job_id=")
        )
        record = JobStore(project.root).get(job_id)
        assert record.status == FAILED
        assert record.failure_reason == "update_already_running"
        assert record.exit_code == UPDATE_ALREADY_RUNNING_EXIT_CODE
        stdout_path, stderr_path = JobStore(project.root).log_paths(job_id)
        assert stdout_path.is_file() and stderr_path.is_file()
        assert (JobStore(project.root).root / job_id / "request.json").is_file()
    finally:
        os.close(lock.fd)


def test_operations_update_rejects_invalid_parameters_before_any_job(
        tmp_path, cli_runner):
    project = build_fixture_project(tmp_path / "p")
    result = cli_runner.invoke(
        app,
        ["operations", "update", "--start", "2026-9-1", "--root", str(project.root)]
    )
    assert result.exit_code == 1
    assert "invalid parameter start" in result.stdout
    assert _job_ids(project.root) == []


def test_a_successful_child_records_run_id_and_dataset_version(tmp_path):
    project = build_fixture_project(tmp_path / "p")
    result = run_operations_update(
        project.root,
        UpdateRunParams(),
        popen=_fake_popen(_SUCCESS_CHILD),
        heartbeat_interval_seconds=0.05,
    )
    assert result.status == SUCCEEDED
    assert result.exit_code == 0
    assert result.run_id == "data_update_fixture01"
    assert result.dataset_version == "ab" * 32
    record = JobStore(project.root).get(result.job_id)
    assert record.status == SUCCEEDED
    assert record.run_id == "data_update_fixture01"
    assert record.dataset_version == "ab" * 32
    assert record.heartbeat_at is not None  # 心跳是存活判定的唯一证据
    stdout_path, _ = JobStore(project.root).log_paths(result.job_id)
    assert "run_id=data_update_fixture01" in stdout_path.read_text(encoding="utf-8")


def test_a_conflicting_child_maps_to_update_already_running(tmp_path):
    project = build_fixture_project(tmp_path / "p")
    result = run_operations_update(
        project.root,
        UpdateRunParams(),
        popen=_fake_popen(_CONFLICT_CHILD),
        heartbeat_interval_seconds=0.05,
    )
    assert result.status == FAILED
    assert result.failure_reason == "update_already_running"
    assert operations_exit_code(result) == UPDATE_ALREADY_RUNNING_EXIT_CODE
    record = JobStore(project.root).get(result.job_id)
    assert record.failure_reason == "update_already_running"
    assert record.exit_code == UPDATE_ALREADY_RUNNING_EXIT_CODE


def test_another_failing_child_maps_to_update_failed(tmp_path):
    project = build_fixture_project(tmp_path / "p")
    result = run_operations_update(
        project.root,
        UpdateRunParams(),
        popen=_fake_popen(_FAIL_CHILD),
        heartbeat_interval_seconds=0.05,
    )
    assert result.status == FAILED
    assert result.failure_reason == "update_failed"
    record = JobStore(project.root).get(result.job_id)
    assert record.exit_code == 3


def test_a_precreated_queued_job_is_adopted_not_duplicated(tmp_path):
    project = build_fixture_project(tmp_path / "p")
    pre = JobStore(project.root).create(
        {"entrypoint": "operations_api", "request": UpdateRunParams().to_payload()}
    )
    result = run_operations_update(
        project.root,
        UpdateRunParams(),
        job_id=pre.job_id,
        entrypoint="operations_api",
        popen=_fake_popen(_SUCCESS_CHILD),
        heartbeat_interval_seconds=0.05,
    )
    assert result.job_id == pre.job_id
    assert _job_ids(project.root) == [pre.job_id]


_CANCEL_DRIVER = """\
import subprocess, sys
from pathlib import Path
from stock_quant.operations import runner


def slow_popen(argv, *, stdout, stderr, **kwargs):
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, time; print(os.getpid(), flush=True); time.sleep(120)",
        ],
        stdout=stdout,
        stderr=stderr,
    )


result = runner.run_operations_update(
    Path(sys.argv[1]),
    runner.UpdateRunParams(),
    popen=slow_popen,
    heartbeat_interval_seconds=0.05,
)
print("status=" + result.status, flush=True)
"""


def test_sigterm_marks_cancelled_by_shutdown_and_terminates_the_child(tmp_path):
    project = build_fixture_project(tmp_path / "p")
    driver = subprocess.Popen(
        [sys.executable, "-c", _CANCEL_DRIVER, str(project.root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        store = JobStore(project.root)
        job_id = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and job_id is None:
            for candidate in _job_ids(project.root):
                try:
                    if store.get(candidate).status == RUNNING:
                        job_id = candidate
                        break
                except Exception:
                    continue
            time.sleep(0.05)
        assert job_id is not None, "the runner never reached RUNNING"

        stdout_log, _ = store.log_paths(job_id)
        grandchild_pid = None
        pid_deadline = time.monotonic() + 10
        while time.monotonic() < pid_deadline and grandchild_pid is None:
            text = (
                stdout_log.read_text(encoding="utf-8") if stdout_log.is_file() else ""
            )
            digits = [line for line in text.splitlines() if line.strip().isdigit()]
            if digits:
                grandchild_pid = int(digits[0])
            else:
                time.sleep(0.05)
        assert grandchild_pid is not None

        driver.terminate()
        stdout, _ = driver.communicate(timeout=30)
        assert "status=CANCELLED_BY_SHUTDOWN" in stdout
        assert store.get(job_id).status == CANCELLED_BY_SHUTDOWN

        gone_deadline = time.monotonic() + 10
        while time.monotonic() < gone_deadline:
            try:
                os.kill(grandchild_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("the inner child survived the runner shutdown")
    finally:
        if driver.poll() is None:
            driver.kill()
            driver.wait(timeout=10)


def test_the_queued_job_request_is_immutable_and_secret_free(tmp_path):
    project = build_fixture_project(tmp_path / "p")
    result = run_operations_update(
        project.root,
        UpdateRunParams(sources=("tushare",)),
        popen=_fake_popen(_SUCCESS_CHILD),
        heartbeat_interval_seconds=0.05,
    )
    request = json.loads(
        (
            JobStore(project.root).root / result.job_id / "request.json"
        ).read_text(encoding="utf-8")
    )
    assert request["request"]["sources"] == ["tushare"]
    assert "TOKEN" not in json.dumps(request)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_operations_runner.py tests/integration/test_operations_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stock_quant.operations.runner'`（unit 文件在 import 处失败；integration 文件同样在 import 处失败）。

- [ ] **Step 3: 实现**

`src/stock_quant/operations/jobs.py`（新文件，本任务交付核心持久化原语，全文）：

```python
"""Persistent job records under ``data/service/jobs/<job_id>/`` (spec 9.2).

Directory contract per job:
- ``request.json``  immutable: written once, with exclusive create.
- ``stdout.log`` / ``stderr.log``  append-only child output.
- ``status.json``  atomically replaced (temp file + ``os.replace``) on every
  transition and heartbeat; it carries pid, boot_id and heartbeat_at, the
  only evidence of liveness.

The status vocabulary is frozen (spec 9.2): QUEUED / RUNNING / SUCCEEDED /
FAILED / CANCELLED_BY_SHUTDOWN.  Nothing outside that set is ever written.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from stock_quant.operations.update_lock import UPDATE_ALREADY_RUNNING_CODE

QUEUED = "QUEUED"
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
CANCELLED_BY_SHUTDOWN = "CANCELLED_BY_SHUTDOWN"

#: The frozen job-status vocabulary (spec 9.2).
JOB_STATUSES = frozenset(
    {QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED_BY_SHUTDOWN}
)

#: Job failure reasons.  ``update_already_running`` is the SAME token the
#: conflict exit code carries (single source of truth: update_lock.py).
FAILURE_UPDATE_ALREADY_RUNNING = UPDATE_ALREADY_RUNNING_CODE
FAILURE_UPDATE_FAILED = "update_failed"

#: Job directories live here, relative to the resolved project root.
JOBS_RELATIVE_PATH = Path("data") / "service" / "jobs"


class JobNotFound(KeyError):
    """No readable job record under this id."""


class JobAlreadyExists(ValueError):
    """A job directory or request.json already exists for this id."""


class JobStateError(ValueError):
    """A transition the job's current status does not allow."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_job_id(*, now: datetime | None = None) -> str:
    """A lexicographically sortable job id (UTC stamp + random tail)."""
    moment = now or _utcnow()
    return f"job_{moment:%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class JobRecord:
    """One job's persisted state (parsed from ``status.json``)."""

    job_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    pid: int | None = None
    boot_id: str | None = None
    heartbeat_at: datetime | None = None
    exit_code: int | None = None
    failure_reason: str | None = None
    failure_detail: str | None = None
    run_id: str | None = None
    dataset_version: str | None = None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class JobStore:
    """Read/write access to one project root's ``data/service/jobs`` tree."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root) / JOBS_RELATIVE_PATH

    # -- creation ------------------------------------------------------ #
    def create(
        self, request: Mapping[str, object], *, job_id: str | None = None
    ) -> JobRecord:
        """Create a QUEUED job; ``request.json`` is written exactly once."""
        now = _utcnow()
        self.root.mkdir(parents=True, exist_ok=True)
        while True:
            candidate = job_id if job_id is not None else new_job_id(now=now)
            try:
                (self.root / candidate).mkdir()
            except FileExistsError:
                if job_id is not None:
                    raise JobAlreadyExists(job_id) from None
                continue  # random-tail collision: draw again
            break
        payload = {**dict(request), "job_id": candidate, "created_at": _iso(now)}
        with (self.root / candidate / "request.json").open(
            "x", encoding="utf-8"
        ) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        return self._write_status(
            JobRecord(job_id=candidate, status=QUEUED, created_at=now, updated_at=now)
        )

    def log_paths(self, job_id: str) -> tuple[Path, Path]:
        """The append-only output logs of one job."""
        directory = self.root / job_id
        return directory / "stdout.log", directory / "stderr.log"

    # -- transitions --------------------------------------------------- #
    def mark_running(
        self, job_id: str, *, pid: int, boot_id: str, now: datetime | None = None
    ) -> JobRecord:
        moment = now or _utcnow()
        record = self.get(job_id)
        self._require_active(record)
        return self._write_status(
            replace(
                record,
                status=RUNNING,
                pid=pid,
                boot_id=boot_id,
                heartbeat_at=moment,
                updated_at=moment,
            )
        )

    def heartbeat(self, job_id: str, *, now: datetime | None = None) -> JobRecord:
        """Refresh the liveness evidence; pid/boot_id stay as they were."""
        moment = now or _utcnow()
        record = self.get(job_id)
        self._require_active(record)
        return self._write_status(
            replace(record, heartbeat_at=moment, updated_at=moment)
        )

    def mark_succeeded(
        self,
        job_id: str,
        *,
        run_id: str | None,
        dataset_version: str | None,
        exit_code: int = 0,
        now: datetime | None = None,
    ) -> JobRecord:
        moment = now or _utcnow()
        record = self.get(job_id)
        self._require_active(record)
        return self._write_status(
            replace(
                record,
                status=SUCCEEDED,
                run_id=run_id,
                dataset_version=dataset_version,
                exit_code=exit_code,
                updated_at=moment,
            )
        )

    def mark_failed(
        self,
        job_id: str,
        *,
        reason: str,
        exit_code: int | None,
        now: datetime | None = None,
        detail: str | None = None,
    ) -> JobRecord:
        moment = now or _utcnow()
        record = self.get(job_id)
        self._require_active(record)
        return self._write_status(
            replace(
                record,
                status=FAILED,
                failure_reason=reason,
                failure_detail=detail,
                exit_code=exit_code,
                updated_at=moment,
            )
        )

    def mark_cancelled_by_shutdown(
        self, job_id: str, *, now: datetime | None = None
    ) -> JobRecord:
        moment = now or _utcnow()
        record = self.get(job_id)
        self._require_active(record)
        return self._write_status(
            replace(record, status=CANCELLED_BY_SHUTDOWN, updated_at=moment)
        )

    # -- reads --------------------------------------------------------- #
    def get(self, job_id: str) -> JobRecord:
        status_path = self.root / job_id / "status.json"
        if not status_path.is_file():
            raise JobNotFound(job_id)
        return _record_from_payload(
            json.loads(status_path.read_text(encoding="utf-8"))
        )

    def _require_active(self, record: JobRecord) -> None:
        if record.status not in (QUEUED, RUNNING):
            raise JobStateError(
                f"job {record.job_id} is {record.status}; "
                "only an active job can transition"
            )

    def _write_status(self, record: JobRecord) -> JobRecord:
        if record.status not in JOB_STATUSES:
            raise JobStateError(
                f"{record.status!r} is outside the frozen job status vocabulary"
            )
        payload = {
            "job_id": record.job_id,
            "status": record.status,
            "created_at": _iso(record.created_at),
            "updated_at": _iso(record.updated_at),
            "pid": record.pid,
            "boot_id": record.boot_id,
            "heartbeat_at": _iso(record.heartbeat_at),
            "exit_code": record.exit_code,
            "failure_reason": record.failure_reason,
            "failure_detail": record.failure_detail,
            "run_id": record.run_id,
            "dataset_version": record.dataset_version,
        }
        destination = self.root / record.job_id / "status.json"
        temporary = destination.with_name("status.json.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, destination)
        return record


def _record_from_payload(payload: Mapping[str, object]) -> JobRecord:
    created = _parse_iso(payload.get("created_at")) or _utcnow()
    updated = _parse_iso(payload.get("updated_at")) or created
    pid = payload.get("pid")
    exit_code = payload.get("exit_code")
    return JobRecord(
        job_id=str(payload["job_id"]),
        status=str(payload["status"]),
        created_at=created,
        updated_at=updated,
        pid=pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
        boot_id=(
            str(payload["boot_id"]) if payload.get("boot_id") is not None else None
        ),
        heartbeat_at=_parse_iso(payload.get("heartbeat_at")),
        exit_code=(
            exit_code
            if isinstance(exit_code, int) and not isinstance(exit_code, bool)
            else None
        ),
        failure_reason=(
            str(payload["failure_reason"])
            if payload.get("failure_reason") is not None
            else None
        ),
        failure_detail=(
            str(payload["failure_detail"])
            if payload.get("failure_detail") is not None
            else None
        ),
        run_id=(
            str(payload["run_id"]) if payload.get("run_id") is not None else None
        ),
        dataset_version=(
            str(payload["dataset_version"])
            if payload.get("dataset_version") is not None
            else None
        ),
    )
```

`src/stock_quant/operations/runner.py`（新文件，全文）：

```python
"""UpdateRunner: one supervised ``data update`` subprocess per job (spec 9.1/9.2).

The runner never calls the ``DataPipeline`` Python API and never builds a
shell string: it launches ``python -m stock_quant data update --root ...``
as an argument array carrying only the four options the CLI allows, and
mirrors the child's success/failure exit code.  While the child runs, the
runner atomically heartbeats pid + timestamp + boot_id into the job's
``status.json`` (spec 9.2).  On SIGTERM the job is recorded
CANCELLED_BY_SHUTDOWN and the child is terminated -- cancellation is a
process-shutdown semantic, never an API endpoint (spec 9.3).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from stock_quant.operations.jobs import (
    CANCELLED_BY_SHUTDOWN,
    FAILED,
    FAILURE_UPDATE_ALREADY_RUNNING,
    FAILURE_UPDATE_FAILED,
    QUEUED,
    SUCCEEDED,
    JobAlreadyExists,
    JobStateError,
    JobStore,
)
from stock_quant.operations.update_lock import UPDATE_ALREADY_RUNNING_EXIT_CODE

#: The runner heartbeats at this cadence -- an order of magnitude under the
#: ~5 minute orphan threshold (spec 9.2).
HEARTBEAT_INTERVAL_SECONDS = 30.0

#: The kernel boot id source, guarding heartbeat pids against PID reuse.
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")

#: The stable ``key=value`` stdout lines the CLI contract guarantees on
#: success.  Reading these whole-line keys is contract parsing, not the
#: free-text failure guessing spec 9.1 forbids (the conflict signal is the
#: dedicated exit code and nothing else).
_CONTRACT_KEYS = frozenset({"run_id", "dataset_version", "resolved_end_date"})


class InvalidUpdateParams(ValueError):
    """A parameter outside the CLI's own type constraints (spec 9.1)."""

    def __init__(self, parameter: str, reason: str) -> None:
        self.parameter = parameter
        self.reason = reason
        super().__init__(f"invalid {parameter}: {reason}")


@dataclass(frozen=True)
class UpdateRunParams:
    """Exactly the options ``data update`` accepts (spec 9.1)."""

    start: date | None = None
    end: date | None = None
    sources: tuple[str, ...] | None = None
    disclosure_lookback_days: int | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "sources": list(self.sources) if self.sources else None,
            "disclosure_lookback_days": self.disclosure_lookback_days,
        }


def validate_update_params(
    *,
    start: str | None = None,
    end: str | None = None,
    sources: str | Sequence[str] | None = None,
    disclosure_lookback_days: int | None = None,
) -> UpdateRunParams:
    """Validate the options exactly the way ``data update`` constrains them.

    ``start``/``end`` must parse as ISO dates (``date.fromisoformat``); a
    comma-separated ``--sources`` string is split and stripped per item the
    way the CLI does it, and every name must be non-empty; the lookback must
    be an integer >= 1 (the CLI declares ``min=1``).
    """
    parsed_start = _parse_date_option("start", start)
    parsed_end = _parse_date_option("end", end)
    parsed_sources: tuple[str, ...] | None = None
    if sources is not None:
        raw = sources.split(",") if isinstance(sources, str) else tuple(sources)
        parsed_sources = tuple(item.strip() for item in raw)
        if any(not item for item in parsed_sources):
            raise InvalidUpdateParams(
                "sources", "source names must be non-empty after stripping"
            )
    if disclosure_lookback_days is not None and (
        not isinstance(disclosure_lookback_days, int)
        or isinstance(disclosure_lookback_days, bool)
        or disclosure_lookback_days < 1
    ):
        raise InvalidUpdateParams(
            "disclosure_lookback_days", "must be an integer >= 1"
        )
    return UpdateRunParams(
        start=parsed_start,
        end=parsed_end,
        sources=parsed_sources,
        disclosure_lookback_days=disclosure_lookback_days,
    )


def _parse_date_option(parameter: str, value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise InvalidUpdateParams(
            parameter, f"{value!r} is not an ISO date (YYYY-MM-DD)"
        ) from error


def build_data_update_argv(project_root: Path, params: UpdateRunParams) -> list[str]:
    """The inner CLI argv (spec 9.1): an argument array, never a shell string."""
    argv = [
        sys.executable,
        "-m",
        "stock_quant",
        "data",
        "update",
        "--root",
        str(project_root),
    ]
    _append_update_options(argv, params)
    return argv


def build_operations_update_argv(
    project_root: Path, params: UpdateRunParams, *, job_id: str | None = None
) -> list[str]:
    """The outer shell argv the operations API spawns (spec 9.3)."""
    argv = [
        sys.executable,
        "-m",
        "stock_quant",
        "operations",
        "update",
        "--root",
        str(project_root),
    ]
    _append_update_options(argv, params)
    if job_id is not None:
        argv += ["--job-id", job_id]
    return argv


def _append_update_options(argv: list[str], params: UpdateRunParams) -> None:
    """Only the four allowed options ever reach a spawned CLI (spec 9.1)."""
    if params.start is not None:
        argv += ["--start", params.start.isoformat()]
    if params.end is not None:
        argv += ["--end", params.end.isoformat()]
    if params.sources:
        argv += ["--sources", ",".join(params.sources)]
    if params.disclosure_lookback_days is not None:
        argv += ["--disclosure-lookback-days", str(params.disclosure_lookback_days)]


def classify_child_exit(code: int) -> tuple[str, str | None]:
    """Map the inner CLI's exit code onto the job vocabulary (spec 9.1).

    The conflict is identified by the dedicated exit code alone -- never by
    matching the child's output text.
    """
    if code == 0:
        return SUCCEEDED, None
    if code == UPDATE_ALREADY_RUNNING_EXIT_CODE:
        return FAILED, FAILURE_UPDATE_ALREADY_RUNNING
    return FAILED, FAILURE_UPDATE_FAILED


def parse_contract_lines(text: str) -> dict[str, str]:
    """Read the CLI's stable ``key=value`` stdout lines (success contract)."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in _CONTRACT_KEYS:
            values[key] = value
    return values


def operations_exit_code(result: "OperationsUpdateResult") -> int:
    """The outer shell exits with the inner outcome (0 / 75 / 1)."""
    if result.status == SUCCEEDED:
        return 0
    if result.failure_reason == FAILURE_UPDATE_ALREADY_RUNNING:
        return UPDATE_ALREADY_RUNNING_EXIT_CODE
    return 1


def read_boot_id(path: Path = BOOT_ID_PATH) -> str:
    """The current kernel boot id (``/proc``, Linux)."""
    return path.read_text(encoding="utf-8").strip()


@dataclass(frozen=True)
class OperationsUpdateResult:
    job_id: str
    status: str
    exit_code: int
    failure_reason: str | None = None
    run_id: str | None = None
    dataset_version: str | None = None


def run_operations_update(
    project_root: Path,
    params: UpdateRunParams,
    *,
    job_id: str | None = None,
    entrypoint: str = "operations_cli",
    heartbeat_interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
    boot_id: str | None = None,
    clock: Callable[[], datetime] | None = None,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> OperationsUpdateResult:
    """Create/adopt the job, run the inner CLI child, persist every state.

    ``popen``/``clock``/``boot_id``/``heartbeat_interval_seconds`` are test
    injection points; production uses the real subprocess, wall clock,
    ``/proc`` boot id and the 30 s cadence.  The lock is NOT pre-checked
    here on purpose: when the inner CLI hits it, this job becomes the
    FAILED/``update_already_running`` record spec 9.4 requires to be visible
    in both the journal and the operations API.
    """
    moment = clock or (lambda: datetime.now(timezone.utc))
    kernel_boot_id = boot_id if boot_id is not None else read_boot_id()
    store = JobStore(project_root)
    request = {"entrypoint": entrypoint, "request": params.to_payload()}
    try:
        job = store.create(request, job_id=job_id)
    except JobAlreadyExists:
        existing = store.get(str(job_id))
        if existing.status != QUEUED:
            raise JobStateError(
                f"job {job_id} is {existing.status}; only a QUEUED job can be adopted"
            ) from None
        job = existing
    argv = build_data_update_argv(project_root, params)
    stdout_path, stderr_path = store.log_paths(job.job_id)
    cancelled = threading.Event()
    previous_handler: object = None
    with stdout_path.open("ab") as stdout_log, stderr_path.open("ab") as stderr_log:
        child = popen(argv, stdout=stdout_log, stderr=stderr_log)
        try:
            previous_handler = _install_sigterm_handler(cancelled, child)
            store.mark_running(
                job.job_id, pid=os.getpid(), boot_id=kernel_boot_id, now=moment()
            )
            while child.poll() is None:
                if cancelled.wait(heartbeat_interval_seconds):
                    try:
                        child.terminate()
                    except ProcessLookupError:
                        pass
                    continue
                store.heartbeat(job.job_id, now=moment())
            exit_code = int(child.returncode)
        finally:
            if previous_handler is not None:
                signal.signal(signal.SIGTERM, previous_handler)  # type: ignore[arg-type]
    if cancelled.is_set():
        store.mark_cancelled_by_shutdown(job.job_id, now=moment())
        return OperationsUpdateResult(
            job_id=job.job_id, status=CANCELLED_BY_SHUTDOWN, exit_code=1
        )
    status, failure_reason = classify_child_exit(exit_code)
    if status == SUCCEEDED:
        contract = parse_contract_lines(
            stdout_path.read_text(encoding="utf-8", errors="replace")
        )
        run_id = contract.get("run_id") or None
        dataset_version = contract.get("dataset_version") or None
        store.mark_succeeded(
            job.job_id,
            run_id=run_id,
            dataset_version=dataset_version,
            exit_code=exit_code,
            now=moment(),
        )
        return OperationsUpdateResult(
            job_id=job.job_id,
            status=SUCCEEDED,
            exit_code=0,
            run_id=run_id,
            dataset_version=dataset_version,
        )
    store.mark_failed(
        job.job_id,
        reason=failure_reason or FAILURE_UPDATE_FAILED,
        exit_code=exit_code,
        now=moment(),
    )
    return OperationsUpdateResult(
        job_id=job.job_id,
        status=FAILED,
        exit_code=exit_code,
        failure_reason=failure_reason,
    )


def _install_sigterm_handler(
    cancelled: threading.Event, child: subprocess.Popen
) -> object:
    """Mark the job CANCELLED_BY_SHUTDOWN and stop the child on SIGTERM."""

    def _on_sigterm(signum, frame) -> None:  # noqa: ANN001
        cancelled.set()
        try:
            child.terminate()
        except ProcessLookupError:
            pass

    try:
        return signal.signal(signal.SIGTERM, _on_sigterm)
    except ValueError:
        return None  # not the main thread: no handler installed
```

`src/stock_quant/cli.py` 增量三（顶部 import 区追加，放在 Task 1 的 update_lock import 之后）：

```python
from stock_quant.operations.runner import (
    InvalidUpdateParams,
    operations_exit_code,
    run_operations_update,
    validate_update_params,
)
```

`src/stock_quant/cli.py` 增量四（`app.add_typer(report_app, name="report")` 一行之后追加）：

```python
operations_app = typer.Typer(
    help=(
        "Supervised operations shell: persistent job records around one "
        "single-flighted data update (spec 2026-09-29 §9)."
    )
)
app.add_typer(operations_app, name="operations")
```

`src/stock_quant/cli.py` 增量五（新命令，追加在 `data group` 区域之后、`data acceptance group` 注释之前均可；执行时以当刻文件就近放置）：

```python
# --------------------------------------------------------------------------- #
# operations group (supervised update shell; spec §9.1)
# --------------------------------------------------------------------------- #


@operations_app.command("update")
def operations_update(
    start: str | None = typer.Option(
        None, "--start", help="Inclusive start (YYYY-MM-DD); forwarded to the inner data update."
    ),
    end: str | None = typer.Option(
        None, "--end", help="Inclusive end (YYYY-MM-DD); forwarded to the inner data update."
    ),
    sources: str | None = typer.Option(
        None, "--sources", help="Comma-separated source subset; forwarded unchanged."
    ),
    disclosure_lookback_days: int | None = typer.Option(
        None,
        "--disclosure-lookback-days",
        min=1,
        help="Disclosure re-ask window override; the same constraint as data update.",
    ),
    root: Path = typer.Option(".", "--root", help="Project root."),
    job_id: str | None = typer.Option(
        None,
        "--job-id",
        help=(
            "Adopt a pre-created QUEUED job (used by the operations API); "
            "by default a fresh job id is created."
        ),
    ),
) -> None:
    """Run one supervised data update and persist its job record.

    Spawns ``data update`` as an argument-array subprocess (never a shell
    string; only start/end/sources/disclosure-lookback-days are forwarded),
    streams its output into ``data/service/jobs/<job_id>/`` append logs,
    heartbeats ``status.json`` while it runs, and exits with the inner
    CLI's success/failure code -- a conflict exits 75 with the stable
    token ``update_already_running`` (spec §9.1).
    """
    project_root = _resolved_project_root(root)
    try:
        params = validate_update_params(
            start=start,
            end=end,
            sources=sources,
            disclosure_lookback_days=disclosure_lookback_days,
        )
    except InvalidUpdateParams as error:
        _echo_failure(f"invalid parameter {error.parameter}: {error.reason}")
        raise typer.Exit(code=1) from None
    result = run_operations_update(project_root, params, job_id=job_id)
    typer.echo(f"job_id={result.job_id}")
    typer.echo(f"status={result.status}")
    if result.run_id:
        typer.echo(f"run_id={result.run_id}")
    if result.dataset_version:
        typer.echo(f"dataset_version={result.dataset_version}")
    if result.failure_reason == UPDATE_ALREADY_RUNNING_CODE:
        typer.echo(UPDATE_ALREADY_RUNNING_CODE)
    elif result.failure_reason:
        typer.echo(f"failure_reason={result.failure_reason}")
    raise typer.Exit(code=operations_exit_code(result))
```

- [ ] **Step 4: 跑测试确认通过，并跑邻居**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_operations_runner.py tests/integration/test_operations_cli.py tests/unit/test_update_lock.py -q`
Expected: PASS（integration 中 `test_operations_update_records_a_failed_conflict_job` 走真实子进程全链：内层 CLI 撞测试所持锁 → 75 → job FAILED）。

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_cli.py -q`
Expected: PASS（在途 WIP 文件只跑不改）。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/operations/runner.py src/stock_quant/operations/jobs.py src/stock_quant/cli.py tests/unit/test_operations_runner.py tests/integration/test_operations_cli.py
git commit -m "feat(operations): operations update shell with UpdateRunner and persistent jobs" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: job 持久化语义、心跳孤儿判定与读取器

**Files:**
- Modify: `src/stock_quant/operations/jobs.py`（追加孤儿自检、活跃判定、读取器、日志尾部；并把模块 docstring 的孤儿判定句补全）
- Test: `tests/unit/test_operations_jobs.py`（新）

**Interfaces:**
- Consumes: Task 2 的 `JobStore`/`JobRecord`/状态常量/`mark_failed`。
- Produces（Task 4 依赖的精确签名）:
  - `ORPHAN_HEARTBEAT_TIMEOUT_SECONDS = 300`（默认量级 5 分钟）
  - `FAILURE_ORPHANED_PROCESS = "orphaned_process"`
  - `reap_orphaned_jobs(project_root: Path, *, now: datetime, boot_id: str, timeout_seconds: int = ORPHAN_HEARTBEAT_TIMEOUT_SECONDS) -> list[str]`
  - `active_job(project_root: Path, *, now: datetime, boot_id: str, timeout_seconds: int = ...) -> JobRecord | None`
  - `list_jobs(project_root: Path) -> list[JobRecord]`（按 job_id 升序 = 创建序；跳过不可读目录）
  - `read_log_tail(path: Path, *, max_bytes: int = 8192) -> str`
  - `JobStore.mark_orphaned_unreadable(job_id: str, *, now: datetime) -> None`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_operations_jobs.py`（新文件，全文）：

```python
"""Durable job-record semantics and the orphan self-check (spec 9.2).

Liveness is judged from the status.json heartbeat alone -- never from "the
directory exists" or "the logs went quiet" (the Dagster run-monitoring
lesson the spec cites).  Every test injects its clock and boot id; nothing
waits five real minutes.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stock_quant.operations.jobs import (
    CANCELLED_BY_SHUTDOWN,
    FAILED,
    JOB_STATUSES,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    FAILURE_ORPHANED_PROCESS,
    ORPHAN_HEARTBEAT_TIMEOUT_SECONDS,
    JobAlreadyExists,
    JobStateError,
    JobStore,
    active_job,
    list_jobs,
    new_job_id,
    read_log_tail,
    reap_orphaned_jobs,
)

_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
_BOOT = "2fdf3cf0-97bf-4896-bd6f-1a5b8841637d"
_OTHER_BOOT = "11111111-2222-3333-4444-555555555555"


def test_the_status_vocabulary_is_frozen():
    assert JOB_STATUSES == frozenset(
        {QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED_BY_SHUTDOWN}
    )
    assert ORPHAN_HEARTBEAT_TIMEOUT_SECONDS == 300
    assert FAILURE_ORPHANED_PROCESS == "orphaned_process"


def test_job_ids_sort_by_creation():
    earlier = new_job_id(now=_NOW)
    later = new_job_id(now=_NOW + timedelta(seconds=1))
    assert earlier < later


def test_request_json_is_written_once(tmp_path):
    store = JobStore(tmp_path)
    job = store.create({"entrypoint": "test"})
    request_path = store.root / job.job_id / "request.json"
    before = request_path.read_bytes()
    with pytest.raises(JobAlreadyExists):
        store.create({"entrypoint": "again"}, job_id=job.job_id)
    assert request_path.read_bytes() == before  # 不可变：字节未动


def test_terminal_jobs_never_transition(tmp_path):
    store = JobStore(tmp_path)
    job = store.create({"entrypoint": "test"})
    store.mark_running(job.job_id, pid=1, boot_id=_BOOT, now=_NOW)
    store.mark_failed(job.job_id, reason="update_failed", exit_code=1, now=_NOW)
    with pytest.raises(JobStateError):
        store.mark_failed(job.job_id, reason="again", exit_code=1, now=_NOW)
    with pytest.raises(JobStateError):
        store.mark_succeeded(job.job_id, run_id="r", dataset_version="v", now=_NOW)


def test_status_json_stays_parseable_and_leaves_no_temporary(tmp_path):
    store = JobStore(tmp_path)
    job = store.create({"entrypoint": "test"})
    store.mark_running(job.job_id, pid=1, boot_id=_BOOT, now=_NOW)
    store.heartbeat(job.job_id, now=_NOW + timedelta(seconds=30))
    store.mark_succeeded(
        job.job_id, run_id="data_update_x", dataset_version="ab" * 32, now=_NOW
    )
    # 每一步之后 status.json 都是完整合法 JSON（原子替换，无撕裂半写）。
    assert store.get(job.job_id).status == SUCCEEDED
    assert store.get(job.job_id).dataset_version == "ab" * 32
    leftovers = [
        child.name
        for child in (store.root / job.job_id).iterdir()
        if child.name.endswith(".tmp")
    ]
    assert leftovers == []


def _running_job(
    tmp_path: Path,
    *,
    heartbeat: datetime | None,
    boot_id: str = _BOOT,
) -> str:
    store = JobStore(tmp_path)
    job = store.create({"entrypoint": "test"})
    store.mark_running(
        job.job_id, pid=4242, boot_id=boot_id, now=_NOW - timedelta(minutes=10)
    )
    if heartbeat is not None:
        store.heartbeat(job.job_id, now=heartbeat)
    return job.job_id


def test_a_fresh_running_job_is_not_reaped(tmp_path):
    job_id = _running_job(tmp_path, heartbeat=_NOW - timedelta(seconds=30))
    assert reap_orphaned_jobs(tmp_path, now=_NOW, boot_id=_BOOT) == []
    assert JobStore(tmp_path).get(job_id).status == RUNNING


def test_a_stale_heartbeat_is_reaped_as_orphaned_process(tmp_path):
    job_id = _running_job(tmp_path, heartbeat=_NOW - timedelta(seconds=400))
    reaped = reap_orphaned_jobs(tmp_path, now=_NOW, boot_id=_BOOT)
    assert reaped == [job_id]
    record = JobStore(tmp_path).get(job_id)
    assert record.status == FAILED
    assert record.failure_reason == FAILURE_ORPHANED_PROCESS
    assert record.failure_detail == "heartbeat_stale"
    # 目录与日志一律不删。
    assert (JobStore(tmp_path).root / job_id).is_dir()
    stdout_path, _ = JobStore(tmp_path).log_paths(job_id)
    assert stdout_path.parent.is_dir()


def test_a_changed_boot_id_is_reaped_even_with_a_fresh_heartbeat(tmp_path):
    # 机器重启过：心跳再新，写它的进程也不可能在世（且 pid 可能已被复用）。
    job_id = _running_job(
        tmp_path, heartbeat=_NOW - timedelta(seconds=1), boot_id=_OTHER_BOOT
    )
    assert reap_orphaned_jobs(tmp_path, now=_NOW, boot_id=_BOOT) == [job_id]
    assert JobStore(tmp_path).get(job_id).failure_detail == "boot_id_changed"


def test_a_running_job_without_any_heartbeat_is_reaped(tmp_path):
    store = JobStore(tmp_path)
    job = store.create({"entrypoint": "test"})
    # 手工构造一个从未心跳的 RUNNING（合法 API 之外能出现的唯一形态）。
    status_path = store.root / job.job_id / "status.json"
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    payload["status"] = RUNNING
    payload.pop("heartbeat_at", None)
    status_path.write_text(json.dumps(payload), encoding="utf-8")
    assert reap_orphaned_jobs(tmp_path, now=_NOW, boot_id=_BOOT) == [job.job_id]
    assert JobStore(tmp_path).get(job.job_id).failure_detail == "no_heartbeat"


def test_an_unreadable_running_status_is_reaped_fail_closed(tmp_path):
    store = JobStore(tmp_path)
    job = store.create({"entrypoint": "test"})
    (store.root / job.job_id / "status.json").write_text("{not json", encoding="utf-8")
    # 不可读 = 没有心跳证据 = 判孤儿（fail closed），不以目录存在推断存活。
    assert reap_orphaned_jobs(tmp_path, now=_NOW, boot_id=_BOOT) == [job.job_id]
    record = JobStore(tmp_path).get(job.job_id)
    assert record.status == FAILED
    assert record.failure_reason == FAILURE_ORPHANED_PROCESS


def test_a_stale_queued_job_is_reaped_as_never_started(tmp_path):
    store = JobStore(tmp_path)
    job = store.create({"entrypoint": "test"})
    older = JobStore(tmp_path).root / job.job_id / "status.json"
    payload = json.loads(older.read_text(encoding="utf-8"))
    payload["created_at"] = (_NOW - timedelta(seconds=400)).isoformat()
    older.write_text(json.dumps(payload), encoding="utf-8")
    assert reap_orphaned_jobs(tmp_path, now=_NOW, boot_id=_BOOT) == [job.job_id]
    assert JobStore(tmp_path).get(job.job_id).failure_detail == "never_started"


def test_active_job_links_the_fresh_running_one(tmp_path):
    fresh = _running_job(tmp_path, heartbeat=_NOW - timedelta(seconds=30))
    assert active_job(tmp_path, now=_NOW, boot_id=_BOOT) is not None
    assert active_job(tmp_path, now=_NOW, boot_id=_BOOT).job_id == fresh


def test_active_job_ignores_stale_and_terminal_jobs(tmp_path):
    _running_job(tmp_path, heartbeat=_NOW - timedelta(seconds=400))
    store = JobStore(tmp_path)
    done = store.create({"entrypoint": "test"})
    store.mark_running(done.job_id, pid=1, boot_id=_BOOT, now=_NOW)
    store.mark_succeeded(done.job_id, run_id="r", dataset_version="v", now=_NOW)
    assert active_job(tmp_path, now=_NOW, boot_id=_BOOT) is None


def test_list_jobs_is_oldest_first_and_skips_unreadable(tmp_path):
    store = JobStore(tmp_path)
    first = store.create({"entrypoint": "test"})
    second = store.create({"entrypoint": "test"})
    (store.root / second.job_id / "status.json").write_text("{broken", encoding="utf-8")
    listed = list_jobs(tmp_path)
    assert [record.job_id for record in listed] == [first.job_id]


def test_read_log_tail_returns_the_last_bytes_lossily(tmp_path):
    log = tmp_path / "stdout.log"
    log.write_text("x" * 10000 + "TAIL", encoding="utf-8")
    tail = read_log_tail(log, max_bytes=8)
    assert tail == "xxxxTAIL"
    assert read_log_tail(tmp_path / "missing.log") == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_operations_jobs.py -q`
Expected: FAIL — `ImportError: cannot import name 'FAILURE_ORPHANED_PROCESS'`（jobs.py 尚无孤儿判定层）。

- [ ] **Step 3: 实现（jobs.py 追加）**

`src/stock_quant/operations/jobs.py` 模块 docstring 末尾追加一句：

```python
Liveness is judged from that heartbeat alone -- never from "the directory
exists" or "the logs went quiet"; RUNNING jobs whose heartbeat is missing,
stale (default threshold ~5 minutes) or from another kernel boot, and
QUEUED jobs that never started, are re-marked FAILED/orphaned_process by
:func:`reap_orphaned_jobs` without deleting anything.
```

常量区（`FAILURE_UPDATE_FAILED` 之后）追加：

```python
#: Orphan self-check threshold (spec 9.2: "默认量级 5 分钟").
ORPHAN_HEARTBEAT_TIMEOUT_SECONDS = 300

#: A RUNNING job whose liveness evidence is gone, or a QUEUED job that
#: never started.  The directory and logs are never deleted.
FAILURE_ORPHANED_PROCESS = "orphaned_process"
```

`JobStore` 内追加一个方法（`mark_cancelled_by_shutdown` 之后）：

```python
    def mark_orphaned_unreadable(self, job_id: str, *, now: datetime) -> None:
        """Fail-closed overwrite for a job whose status.json is unreadable.

        An unreadable status is no heartbeat evidence, so the job is
        recorded FAILED/orphaned_process outright; there is nothing to
        preserve because nothing could be read.
        """
        self._write_status(
            JobRecord(
                job_id=job_id,
                status=FAILED,
                created_at=now,
                updated_at=now,
                failure_reason=FAILURE_ORPHANED_PROCESS,
                failure_detail="status_unreadable",
            )
        )
```

文件末尾追加模块级函数：

```python
def list_jobs(project_root: Path) -> list[JobRecord]:
    """Every readable job record, oldest first (job ids sort by creation).

    Unreadable directories are skipped here and left for
    :func:`reap_orphaned_jobs` to fail closed -- listing must stay usable
    even with a damaged record on disk.
    """
    store = JobStore(project_root)
    if not store.root.is_dir():
        return []
    records: list[JobRecord] = []
    for child in sorted(store.root.iterdir()):
        if not child.is_dir():
            continue
        try:
            records.append(store.get(child.name))
        except (JobNotFound, ValueError, KeyError):
            continue
    return records


def reap_orphaned_jobs(
    project_root: Path,
    *,
    now: datetime,
    boot_id: str,
    timeout_seconds: int = ORPHAN_HEARTBEAT_TIMEOUT_SECONDS,
) -> list[str]:
    """Fail-closed self-check over active jobs (spec 9.2).

    A RUNNING job is orphaned when its heartbeat is missing, was written
    under a different kernel boot (the pid may have been reused since), or
    is older than ``timeout_seconds``.  A QUEUED job older than the timeout
    never started.  Orphans are re-marked FAILED/``orphaned_process`` with
    a machine-readable detail; the directory and its logs are never
    deleted.  Liveness is *never* inferred from directory existence or log
    silence.
    """
    store = JobStore(project_root)
    if not store.root.is_dir():
        return []
    reaped: list[str] = []
    for child in sorted(store.root.iterdir()):
        if not child.is_dir():
            continue
        try:
            record = store.get(child.name)
        except (JobNotFound, ValueError, KeyError):
            store.mark_orphaned_unreadable(child.name, now=now)
            reaped.append(child.name)
            continue
        reason: str | None = None
        detail: str | None = None
        if record.status == RUNNING:
            if record.heartbeat_at is None:
                reason, detail = FAILURE_ORPHANED_PROCESS, "no_heartbeat"
            elif record.boot_id != boot_id:
                reason, detail = FAILURE_ORPHANED_PROCESS, "boot_id_changed"
            elif (now - record.heartbeat_at).total_seconds() > timeout_seconds:
                reason, detail = FAILURE_ORPHANED_PROCESS, "heartbeat_stale"
        elif record.status == QUEUED and (
            (now - record.created_at).total_seconds() > timeout_seconds
        ):
            reason, detail = FAILURE_ORPHANED_PROCESS, "never_started"
        if reason is not None:
            store.mark_failed(
                child.name, reason=reason, exit_code=None, now=now, detail=detail
            )
            reaped.append(child.name)
    return reaped


def active_job(
    project_root: Path,
    *,
    now: datetime,
    boot_id: str,
    timeout_seconds: int = ORPHAN_HEARTBEAT_TIMEOUT_SECONDS,
) -> JobRecord | None:
    """The job a conflict answer should link to, if any (spec 9.3/9.4).

    A RUNNING job with a fresh same-boot heartbeat, or a QUEUED job younger
    than the timeout, counts as running.  Anything else has no liveness
    evidence and must not block a new request -- the flock inside the inner
    ``data update`` remains the final single-flight arbiter.
    """
    chosen: JobRecord | None = None
    for record in list_jobs(project_root):
        if record.status == RUNNING:
            fresh = (
                record.heartbeat_at is not None
                and record.boot_id == boot_id
                and (now - record.heartbeat_at).total_seconds() <= timeout_seconds
            )
        elif record.status == QUEUED:
            fresh = (now - record.created_at).total_seconds() <= timeout_seconds
        else:
            fresh = False
        if fresh and (chosen is None or record.updated_at > chosen.updated_at):
            chosen = record
    return chosen


def read_log_tail(path: Path, *, max_bytes: int = 8192) -> str:
    """The last ``max_bytes`` of a log file, decoded lossily ('' if absent)."""
    if not path.is_file():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes))
        return handle.read().decode("utf-8", errors="replace")
```

- [ ] **Step 4: 跑测试确认通过，并跑邻居**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_operations_jobs.py tests/unit/test_operations_runner.py tests/integration/test_operations_cli.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/operations/jobs.py tests/unit/test_operations_jobs.py
git commit -m "feat(operations): orphan heartbeat self-check and durable job semantics" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: 操作 API（独立 router/process，默认禁用，仅环回）

**Files:**
- Create: `src/stock_quant/operations/api.py`、`src/stock_quant/operations/serve.py`
- Modify: `pyproject.toml`（追加 `service` optional extra；若 P3 已加则只核对）
- Test: `tests/integration/test_operations_api.py`（新）

**Interfaces:**
- Consumes: Task 2 的 `validate_update_params`/`UpdateRunParams.to_payload`/`build_operations_update_argv`/`read_boot_id`；Task 3 的 `JobStore`/`active_job`/`list_jobs`/`read_log_tail`/`reap_orphaned_jobs`；Task 1 的 `UPDATE_ALREADY_RUNNING_CODE`。
- Produces:
  - `create_operations_app(project_root: Path, *, enabled: bool = False) -> FastAPI`（默认禁用 → 所有端点 503 `{"error": {"code": "operations_disabled"}}`）
  - `router: APIRouter`（前缀 `/api/v1`；恰三个端点：`POST /update-jobs`、`GET /update-jobs`、`GET /update-jobs/{job_id}`）
  - `redact_text(text: str, *, secrets: Iterable[str], project_root: Path) -> str`
  - `serve.main(argv: Sequence[str] | None = None) -> int`、`serve.is_loopback(host: str) -> bool`、`serve.DEFAULT_PORT = 8642`
  - 409 响应体：`{"error": {"code": "update_already_running", "job_id": <id>}}`——与 CLI 同一冲突码字符串。

- [ ] **Step 0: owner 授权安装 service extra（环境前置，联网需授权）**

先核对是否已可用（P3 若已落地则直接通过）：

```bash
/home/ji/miniconda3/envs/sq312/bin/python -c "import fastapi, uvicorn; print(fastapi.__version__, uvicorn.__version__)"
```

若失败：向 owner 申请授权后安装（spec §5.4：fastapi/uvicorn 属 `service` optional extra，核心 CLI 安装不被迫安装服务栈）：

```bash
/home/ji/miniconda3/envs/sq312/bin/python -m pip install -e '/home/ji/work/program/stock[service]'
```

未获授权时本任务（及其测试）无法执行——停下向 owner 汇报，不得改用其他 Web 栈绕过。

- [ ] **Step 1: 写失败测试**

`tests/integration/test_operations_api.py`（新文件，全文；用线程内 uvicorn + 标准库 urllib，端到端走真实环回 HTTP，不引入 httpx）：

```python
"""Operations API contract over real loopback HTTP (spec 9.3).

The app is served by uvicorn in a thread on an ephemeral loopback port and
hit with stdlib urllib, so the loopback-only bind is part of what is
tested.  Everything is offline: the spawned ``operations update`` child
fails on the missing transport exactly like the CLI tests, and its job
record is the assertion surface.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import uvicorn
from conftest import build_fixture_project

from stock_quant.operations.api import create_operations_app
from stock_quant.operations.jobs import FAILED, RUNNING, JobStore
from stock_quant.operations.runner import read_boot_id
from stock_quant.operations.serve import DEFAULT_PORT, is_loopback, main as serve_main
from stock_quant.operations.update_lock import UPDATE_ALREADY_RUNNING_CODE


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _request(method: str, base: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


@pytest.fixture()
def operations(tmp_path, monkeypatch):
    monkeypatch.delenv("TUSHARE_TRANSPORT", raising=False)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    project = build_fixture_project(tmp_path / "p")
    app = create_operations_app(project.root, enabled=True)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=_free_port(), log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(400):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}", project
    server.should_exit = True
    thread.join(timeout=10)


def _wait_for_terminal(project_root: Path, job_id: str, timeout: float = 120.0) -> None:
    store = JobStore(project_root)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if store.get(job_id).status in {"SUCCEEDED", "FAILED", "CANCELLED_BY_SHUTDOWN"}:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} never reached a terminal status")


def test_an_empty_project_lists_no_jobs(operations):
    base, _ = operations
    status, body = _request("GET", base, "/api/v1/update-jobs")
    assert status == 200
    assert body == {"jobs": []}


def test_a_disabled_app_refuses_every_endpoint(tmp_path):
    project = build_fixture_project(tmp_path / "p")
    app = create_operations_app(project.root, enabled=False)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=_free_port(), log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(400):
        if server.started:
            break
        time.sleep(0.05)
    try:
        status, body = _request(
            "GET", f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}",
            "/api/v1/update-jobs",
        )
        assert status == 503
        assert body == {"error": {"code": "operations_disabled"}}
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_an_invalid_parameter_is_422_and_creates_no_job(operations):
    base, project = operations
    status, body = _request(
        "POST",
        base,
        "/api/v1/update-jobs",
        {"start": "2026-9-1"},
    )
    assert status == 422
    assert body["error"]["code"] == "invalid_parameter"
    assert body["error"]["parameter"] == "start"
    assert not JobStore(project.root).root.is_dir()


def test_a_conflicting_active_job_answers_409_with_the_same_code(operations):
    base, project = operations
    store = JobStore(project.root)
    job = store.create({"entrypoint": "test"})
    store.mark_running(
        job.job_id,
        pid=999999,
        boot_id=read_boot_id(),
        now=datetime.now(timezone.utc),
    )
    status, body = _request("POST", base, "/api/v1/update-jobs", {})
    assert status == 409
    assert body["error"]["code"] == UPDATE_ALREADY_RUNNING_CODE
    assert body["error"]["job_id"] == job.job_id


def test_a_post_spawns_a_job_that_fails_offline_and_a_rerun_gets_a_new_id(
    operations,
):
    base, project = operations
    status, body = _request("POST", base, "/api/v1/update-jobs", {})
    assert status == 201
    first_id = body["job_id"]
    assert body["status"] == "QUEUED"

    _wait_for_terminal(project.root, first_id)
    status, detail = _request("GET", base, f"/api/v1/update-jobs/{first_id}")
    assert status == 200
    # 离线：内层 data update 因缺 transport 失败 → job FAILED/update_failed。
    assert detail["status"] == FAILED
    assert detail["failure_reason"] == "update_failed"
    assert isinstance(detail["stdout_tail"], str)
    assert isinstance(detail["stderr_tail"], str)

    status, body = _request("POST", base, "/api/v1/update-jobs", {})
    assert status == 201
    assert body["job_id"] != first_id  # 失败后的再次运行 = 新 job id


def test_an_unknown_job_is_404(operations):
    base, _ = operations
    status, body = _request("GET", base, "/api/v1/update-jobs/job_nope")
    assert status == 404
    assert body["error"]["code"] == "job_not_found"


def test_log_tails_are_sanitized(operations, monkeypatch):
    base, project = operations
    monkeypatch.setenv("TUSHARE_TOKEN", "sekrit-token-value")
    store = JobStore(project.root)
    job = store.create({"entrypoint": "test"})
    stdout_path, _ = store.log_paths(job.job_id)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(
        f"token sekrit-token-value at {project.root}/configs\n", encoding="utf-8"
    )
    status, detail = _request("GET", base, f"/api/v1/update-jobs/{job.job_id}")
    assert status == 200
    assert "sekrit-token-value" not in json.dumps(detail)
    assert str(project.root) not in detail["stdout_tail"]
    assert "[redacted]" in detail["stdout_tail"]
    assert "<project-root>" in detail["stdout_tail"]


def test_the_route_surface_is_exactly_the_three_endpoints(tmp_path):
    from conftest import build_fixture_project as build

    app = create_operations_app(build(tmp_path / "p").root, enabled=True)
    # 按 (path, method) 对收集：GET 与 POST /update-jobs 是两条同路径的
    # APIRoute，用 path 做 dict 键会互相覆盖（fastapi 0.128 下只剩 GET，
    # 0.141 起 include_router 延迟展开、连 .methods 都没有，得到空集）。
    api_routes = {
        (route.path, method)
        for route in app.routes
        for method in (getattr(route, "methods", None) or ())
        if route.path.startswith("/api/v1")
    }
    assert api_routes == {
        ("/api/v1/update-jobs", "POST"),
        ("/api/v1/update-jobs", "GET"),
        ("/api/v1/update-jobs/{job_id}", "GET"),
    }


def test_serve_refuses_disabled_and_non_loopback(tmp_path):
    project = build_fixture_project(tmp_path / "p")
    assert (
        serve_main(["--root", str(project.root)]) == 1
    )  # 默认禁用：不带 --enable 拒绝启动
    assert serve_main(["--root", str(project.root), "--enable", "--host", "0.0.0.0"]) == 1
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("localhost")
    assert not is_loopback("0.0.0.0") and not is_loopback("192.168.1.5")
    assert DEFAULT_PORT == 8642
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_operations_api.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stock_quant.operations.api'`（Step 0 已保证 fastapi/uvicorn 可用）。

- [ ] **Step 3: 实现**

`pyproject.toml` 增量（`[project]` 的 `dependencies` 列表之后追加；若 P3 已写入 `[project.optional-dependencies]` 则只在其中核对 fastapi/uvicorn 两项，不重复声明）：

```toml
[project.optional-dependencies]
service = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
]
```

`src/stock_quant/operations/api.py`（新文件，全文）：

```python
"""Operations API: three endpoints over persistent update jobs (spec 9.3).

A separate router/process, disabled by default; when enabled it is still
loopback-only (``operations.serve`` refuses any non-loopback bind before
uvicorn ever runs).  The three endpoints are deliberately the whole
surface: no cancel, no retry-to-green, no log deletion, no acceptance and
no research endpoints.  A rerun after a failure is a NEW job id, created
by a new explicit POST or the next timer trigger -- never by this process
on its own.  Every request first runs the orphan self-check, so restarted
services never present dead jobs as running.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from stock_quant.operations.jobs import (
    JobNotFound,
    JobStore,
    active_job,
    list_jobs,
    read_log_tail,
    reap_orphaned_jobs,
)
from stock_quant.operations.runner import (
    InvalidUpdateParams,
    build_operations_update_argv,
    read_boot_id,
    validate_update_params,
)
from stock_quant.operations.update_lock import UPDATE_ALREADY_RUNNING_CODE

router = APIRouter(prefix="/api/v1")

#: The env keys whose values are redacted from log tails (mirrors the CLI's
#: own secret list; tokens are only ever read from the environment).
_SECRET_ENV_KEYS = (
    "TUSHARE_TOKEN",
    "AKSHARE_TOKEN",
    "BAOSTOCK_USER",
    "BAOSTOCK_PASSWORD",
)


class UpdateJobRequest(BaseModel):
    """Exactly the parameters ``data update`` allows (spec 9.1)."""

    start: str | None = None
    end: str | None = None
    sources: list[str] | None = None
    disclosure_lookback_days: int | None = None


def create_operations_app(project_root: Path, *, enabled: bool = False) -> FastAPI:
    """Build the operations app; ``enabled=False`` answers 503 everywhere.

    Default-disabled is the fail-closed state (spec 9.3): even if the router
    is mounted by mistake, nothing executes until the operator explicitly
    enables the operations surface at launch.
    """
    application = FastAPI(title="stock-quant operations API", version="1")
    application.state.project_root = Path(project_root)
    application.state.enabled = bool(enabled)
    application.include_router(router)
    return application


def redact_text(
    text: str, *, secrets: Iterable[str], project_root: Path
) -> str:
    """Strip secret values and the absolute project root from log tails."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text.replace(str(project_root), "<project-root>")


def _secret_values() -> tuple[str, ...]:
    return tuple(
        value
        for value in (os.environ.get(key) for key in _SECRET_ENV_KEYS)
        if value
    )


def _guard_enabled(request: Request) -> JSONResponse | None:
    if not request.app.state.enabled:
        return JSONResponse(
            status_code=503, content={"error": {"code": "operations_disabled"}}
        )
    return None


def _self_check(request: Request) -> None:
    """Reap orphans before answering, so dead jobs are never shown live."""
    state = request.app.state
    reap_orphaned_jobs(
        state.project_root,
        now=datetime.now(timezone.utc),
        boot_id=read_boot_id(),
    )


@router.post("/update-jobs")
def create_update_job(payload: UpdateJobRequest, request: Request) -> JSONResponse:
    """Validate and launch one ``operations update`` (spec 9.3).

    An active job answers 409 with the same conflict code the CLI exits
    with.  A lock held outside the job system (a manual CLI run) is NOT
    probed here on purpose: the spawned job then ends
    FAILED/``update_already_running`` by the inner CLI's exit code -- the
    flock stays the single-flight arbiter across all three entrances.
    """
    refused = _guard_enabled(request)
    if refused is not None:
        return refused
    project_root: Path = request.app.state.project_root
    _self_check(request)
    now = datetime.now(timezone.utc)
    running = active_job(project_root, now=now, boot_id=read_boot_id())
    if running is not None:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": UPDATE_ALREADY_RUNNING_CODE,
                    "job_id": running.job_id,
                }
            },
        )
    try:
        params = validate_update_params(
            start=payload.start,
            end=payload.end,
            sources=payload.sources,
            disclosure_lookback_days=payload.disclosure_lookback_days,
        )
    except InvalidUpdateParams as error:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_parameter",
                    "parameter": error.parameter,
                    "reason": error.reason,
                }
            },
        )
    job = JobStore(project_root).create(
        {"entrypoint": "operations_api", "request": params.to_payload()}
    )
    argv = build_operations_update_argv(project_root, params, job_id=job.job_id)
    stdout_path, stderr_path = JobStore(project_root).log_paths(job.job_id)
    # Detached: the job survives an API restart and keeps heartbeating on
    # its own; if it dies instead, the self-check reaps it fail-closed.
    with stdout_path.open("ab") as stdout_log, stderr_path.open("ab") as stderr_log:
        subprocess.Popen(
            argv,
            stdout=stdout_log,
            stderr=stderr_log,
            start_new_session=True,
            cwd=str(project_root),
        )
    return JSONResponse(
        status_code=201, content={"job_id": job.job_id, "status": job.status}
    )


@router.get("/update-jobs", response_model=None)
def list_update_jobs(request: Request) -> JSONResponse | dict:
    """Persisted job summaries (spec 9.3)."""
    refused = _guard_enabled(request)
    if refused is not None:
        return refused
    _self_check(request)
    return {
        "jobs": [
            {
                "job_id": record.job_id,
                "status": record.status,
                "created_at": record.created_at.isoformat(),
                "updated_at": record.updated_at.isoformat(),
                "run_id": record.run_id,
                "dataset_version": record.dataset_version,
            }
            for record in list_jobs(request.app.state.project_root)
        ]
    }


@router.get("/update-jobs/{job_id}")
def get_update_job(job_id: str, request: Request) -> JSONResponse:
    """One job's status and sanitized log tails (spec 9.3)."""
    refused = _guard_enabled(request)
    if refused is not None:
        return refused
    _self_check(request)
    project_root: Path = request.app.state.project_root
    try:
        record = JobStore(project_root).get(job_id)
    except JobNotFound:
        # 与 409/422/503 以及 P3 冻结的 `{"error": {...}}` 信封同形；用
        # HTTPException(detail=...) 会得到 FastAPI 默认的 `{"detail": ...}`，
        # 成为三个端点里唯一一个消费者读不到 code 的响应。
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "job_not_found", "job_id": job_id}},
        )
    stdout_path, stderr_path = JobStore(project_root).log_paths(job_id)
    secrets = _secret_values()
    return JSONResponse(
        status_code=200,
        content={
            "job_id": record.job_id,
            "status": record.status,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "pid": record.pid,
            "boot_id": record.boot_id,
            "heartbeat_at": (
                record.heartbeat_at.isoformat() if record.heartbeat_at else None
            ),
            "exit_code": record.exit_code,
            "failure_reason": record.failure_reason,
            "failure_detail": record.failure_detail,
            "run_id": record.run_id,
            "dataset_version": record.dataset_version,
            "stdout_tail": redact_text(
                read_log_tail(stdout_path),
                secrets=secrets,
                project_root=project_root,
            ),
            "stderr_tail": redact_text(
                read_log_tail(stderr_path),
                secrets=secrets,
                project_root=project_root,
            ),
        },
    )
```

（`FAILURE_ORPHANED_PROCESS` 仅作语义参照 import 可省；上面代码已不直接使用它，最终文件里去掉该 import，保留实际使用项。）

`src/stock_quant/operations/serve.py`（新文件，全文）：

```python
"""Launcher for the operations API process (spec 9.3).

Default disabled and loopback-only: ``--enable`` is required to serve at
all, and any non-loopback ``--host`` is refused before uvicorn ever binds.
There is no flag or configuration that turns this process into a network
service.
"""

from __future__ import annotations

import argparse
import ipaddress
from collections.abc import Sequence

import uvicorn

from stock_quant.operations.api import create_operations_app
from stock_quant.project_root import ProjectRootError, resolve_project_root

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8642


def is_loopback(host: str) -> bool:
    """True only for loopback addresses (and the ``localhost`` name)."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost"
    return address.is_loopback


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m stock_quant.operations.serve",
        description=(
            "Serve the stock-quant operations API (default disabled, "
            "loopback only)."
        ),
    )
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument(
        "--enable",
        action="store_true",
        help=(
            "Serve the operations endpoints; without this flag the process "
            "refuses to start (spec 9.3)."
        ),
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind address (loopback only).")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port.")
    args = parser.parse_args(argv)
    if not args.enable:
        print("FAILED: operations API is disabled; start with --enable (spec 9.3)")
        return 1
    if not is_loopback(args.host):
        print(
            f"FAILED: refusing non-loopback bind {args.host!r}; "
            "the operations API is loopback-only"
        )
        return 1
    try:
        project_root = resolve_project_root(args.root)
    except ProjectRootError as error:
        print(f"FAILED: {error}")
        return 1
    uvicorn.run(
        create_operations_app(project_root, enabled=True),
        host=args.host,
        port=args.port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过，并跑邻居**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_operations_api.py tests/unit/test_operations_jobs.py tests/integration/test_operations_cli.py -q`
Expected: PASS（`test_a_post_spawns_a_job_that_fails_offline_and_a_rerun_gets_a_new_id` 走真实两级子进程，约数十秒）。

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml src/stock_quant/operations/api.py src/stock_quant/operations/serve.py tests/integration/test_operations_api.py
git commit -m "feat(operations): loopback-only operations API behind the service extra" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: systemd timer 单元与纯渲染

**Files:**
- Create: `systemd/stock-quant-data-update@.service`、`systemd/stock-quant-data-update@.timer`（新目录 `systemd/`）、`src/stock_quant/operations/systemd_units.py`
- Test: `tests/unit/test_operations_systemd.py`（新）

**Interfaces:**
- Consumes: Global Constraints 的解释器路径；Task 2 的 `operations update` 入口。
- Produces:
  - `systemd_escape_path(project_root: Path) -> str`（绝对路径必需，否则 ValueError）
  - `timer_unit_name(project_root: Path) -> str`（如 `stock-quant-data-update@home-ji-work-program-stock.timer`）
  - `service_unit_name(project_root: Path) -> str`
  - `TIMER_UNIT_PREFIX = "stock-quant-data-update"`、`INTERPRETER = "/home/ji/miniconda3/envs/sq312/bin/python"`
  - 两个已提交 unit 文件（ExecStart 只调 `operations update --root %f`；OnCalendar 带 `Asia/Shanghai` 后缀；`Persistent=false` 显式）

- [ ] **Step 1: 写失败测试**

`tests/unit/test_operations_systemd.py`（新文件，全文）：

```python
"""Pure render checks for the committed systemd units (spec 9.4).

Nothing here installs, enables or starts systemd; the assertions pin the
contract the units must keep: the timer path may only call the operations
shell (never ``data update`` directly), the names are fixed, the timezone
is Asia/Shanghai, and missed triggers are never replayed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_quant.operations.systemd_units import (
    INTERPRETER,
    TIMER_UNIT_PREFIX,
    service_unit_name,
    systemd_escape_path,
    timer_unit_name,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SYSTEMD_DIR = _REPO_ROOT / "systemd"


def _unit_text(name: str) -> str:
    path = _SYSTEMD_DIR / name
    assert path.is_file(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


def test_escape_maps_the_real_repo_root():
    assert (
        systemd_escape_path(Path("/home/ji/work/program/stock"))
        == "home-ji-work-program-stock"
    )


def test_escape_disambiguates_dashes_and_spaces():
    assert (
        systemd_escape_path(Path("/data/projects/a-b c"))
        == "data-projects-a\\x2db\\x20c"
    )


def test_escape_requires_an_absolute_root():
    with pytest.raises(ValueError):
        systemd_escape_path(Path("relative/root"))


def test_unit_names_use_the_fixed_prefix_and_the_instance():
    root = Path("/home/ji/work/program/stock")
    assert TIMER_UNIT_PREFIX == "stock-quant-data-update"
    assert INTERPRETER == "/home/ji/miniconda3/envs/sq312/bin/python"
    assert timer_unit_name(root) == "stock-quant-data-update@home-ji-work-program-stock.timer"
    assert service_unit_name(root) == "stock-quant-data-update@home-quant-data-update.service".replace(
        "home-quant-data-update", "home-ji-work-program-stock"
    )


def test_the_committed_files_use_the_fixed_template_names():
    assert (_SYSTEMD_DIR / "stock-quant-data-update@.service").is_file()
    assert (_SYSTEMD_DIR / "stock-quant-data-update@.timer").is_file()


def test_service_execstart_only_calls_the_operations_shell():
    service = _unit_text("stock-quant-data-update@.service")
    exec_start = next(
        line for line in service.splitlines() if line.startswith("ExecStart=")
    )
    # 只调 operations update（内层 data update 的输出/锁/记录才能进 job 体系），
    # 且解释器是钉死的 sq312 入口；%f 把实例名还原为绝对项目根。
    assert exec_start == (
        f"ExecStart={INTERPRETER} -m stock_quant operations update --root %f"
    )
    assert "stock_quant data update" not in service
    assert "Restart=no" in service


def test_timer_is_shanghai_non_persistent_and_targets_the_service():
    timer = _unit_text("stock-quant-data-update@.timer")
    on_calendar = next(
        line for line in timer.splitlines() if line.startswith("OnCalendar=")
    )
    assert on_calendar.endswith("Asia/Shanghai")
    assert "Persistent=false" in timer
    assert "Unit=stock-quant-data-update@%i.service" in timer
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_operations_systemd.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stock_quant.operations.systemd_units'`（且 `systemd/` 目录不存在，`_unit_text` 的断言也会失败——先以 import 失败为准）。

- [ ] **Step 3: 实现**

`src/stock_quant/operations/systemd_units.py`（新文件，全文）：

```python
"""Rendering helpers for the committed systemd timer units (spec 9.4).

Pure functions only: nothing here installs, enables or starts anything, and
no test requires systemd.  The committed units under ``systemd/`` are the
authoritative templates; these helpers exist so operators and tests can
derive the instance unit name for a project root without calling
``systemd-escape``.  In the unit files, ``%f`` expands the escaped
instance name back to the absolute project root.
"""

from __future__ import annotations

import string
from pathlib import Path

#: Fixed unit prefix; the instance is the systemd-escaped project root
#: (spec 9.4: ``stock-quant-data-update@<project-id>.timer``).
TIMER_UNIT_PREFIX = "stock-quant-data-update"

#: The pinned interpreter (Global Constraints); the units hard-code it too.
INTERPRETER = "/home/ji/miniconda3/envs/sq312/bin/python"

#: Characters systemd-escape leaves untouched ('-' is meaningful and '.' is
#: special only in leading position, so both are handled separately).
_UNRESERVED = frozenset(string.ascii_letters + string.digits + ":_.")


def systemd_escape_path(project_root: Path) -> str:
    """``systemd-escape --path`` for an absolute path (the needed subset).

    ``/home/ji/work/program/stock`` -> ``home-ji-work-program-stock``; a
    literal '-' becomes ``\\x2d`` and a space ``\\x20`` so decoding is
    unambiguous.
    """
    text = str(Path(project_root))
    if not text.startswith("/"):
        raise ValueError(f"an absolute project root is required, got {text!r}")
    pieces: list[str] = []
    for character in text[1:]:
        if character == "/":
            pieces.append("-")
        elif character in _UNRESERVED:
            pieces.append(character)
        else:
            pieces.append(f"\\x{ord(character):02x}")
    escaped = "".join(pieces)
    if escaped.startswith("."):
        escaped = "\\x2e" + escaped[1:]
    return escaped


def timer_unit_name(project_root: Path) -> str:
    """The timer instance unit name for one project root."""
    return f"{TIMER_UNIT_PREFIX}@{systemd_escape_path(project_root)}.timer"


def service_unit_name(project_root: Path) -> str:
    """The oneshot service instance unit name for one project root."""
    return f"{TIMER_UNIT_PREFIX}@{systemd_escape_path(project_root)}.service"
```

`systemd/stock-quant-data-update@.service`（新文件，全文）：

```ini
[Unit]
Description=Stock Quant scheduled data update (one project root per instance)
Documentation=file:///home/ji/work/program/stock/RUNBOOK.md
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
# The scheduler may ONLY call the operations shell (spec 9.4): calling
# `data update` directly would leave the run invisible to the operations
# API's job records.  The instance name is the systemd-escaped project
# root (systemd-escape -p <root>); %f expands it back to the absolute
# path.  A conflicted or failed update exits nonzero (75 for
# update_already_running) so journalctl shows it; no restarts -- the next
# scheduled trigger is the retry, with a NEW job id.
ExecStart=/home/ji/miniconda3/envs/sq312/bin/python -m stock_quant operations update --root %f
Restart=no
```

`systemd/stock-quant-data-update@.timer`（新文件，全文）：

```ini
[Unit]
Description=Schedule Stock Quant data updates (Asia/Shanghai evening window)

[Timer]
# Close-of-day window pinned to Asia/Shanghai regardless of host TZ
# (spec 9.4).  Persistent=false: a trigger missed while the host was down
# is logged by systemd and is NOT replayed on boot.
OnCalendar=*-*-* 17:10:00 Asia/Shanghai
Persistent=false
AccuracySec=30s
Unit=stock-quant-data-update@%i.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_operations_systemd.py tests/unit/test_operations_runner.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add systemd/stock-quant-data-update@.service systemd/stock-quant-data-update@.timer src/stock_quant/operations/systemd_units.py tests/unit/test_operations_systemd.py
git commit -m "feat(operations): systemd timer units and pure render helpers" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

- [ ] **Step 6: （owner 授权后执行，单列）一次真实计划触发验证**

本步修改宿主 systemd 状态并可能触发一次**真实数据更新**（联网、真实发布）——必须先获得 owner 明确授权，且授权范围（触发方式：真实更新 or 冲突路径验证）成文后再执行：

```bash
# 1) 核对实例名与渲染函数一致
systemd-escape -p /home/ji/work/program/stock   # 期望输出 home-ji-work-program-stock

# 2) 链接并启用（不安装拷贝）
sudo systemctl link /home/ji/work/program/stock/systemd/stock-quant-data-update@.service
sudo systemctl link /home/ji/work/program/stock/systemd/stock-quant-data-update@.timer
sudo systemctl daemon-reload
sudo systemctl enable --now stock-quant-data-update@home-ji-work-program-stock.timer

# 3) 验证（二选一，按 owner 授权）：
#    a) 冲突路径（离线安全）：先手工持有锁再触发一次服务
python -c "from pathlib import Path; from stock_quant.operations.update_lock import acquire_update_lock; import time; acquire_update_lock(Path('/home/ji/work/program/stock')); print('held'); time.sleep(60)" &
sudo systemctl start stock-quant-data-update@home-ji-work-program-stock.service
#    b) 真实更新：直接触发并等待完成（联网/发布需 owner 授权）
# 4) 证据：journal 可见非零退出（冲突为 75/update_already_running），Web 可见同 job
journalctl -u stock-quant-data-update@home-ji-work-program-stock.service -n 50
curl -s http://127.0.0.1:8642/api/v1/update-jobs   # 操作面启用时

# 5) 回滚
sudo systemctl disable --now stock-quant-data-update@home-ji-work-program-stock.timer
```

结论（成功/失败与 journal/Web 两处证据）以 dated operations record 记入 `docs/operations/`（本步的产出之一）。

---

### Task 6: RUNBOOK 追加与文档收口

**Files:**
- Modify: `RUNBOOK.md`（**只追加**文件末尾新节，不动任何在途行）；`docs/architecture/overview.md`、`docs/architecture/module-map.md`、`docs/architecture/data-flow.md`（§5.3 同变更义务的操作面条目，条件性）；`docs/superpowers/plans/2026-10-01-panda-data-loop-grafting-implementation.md`（阶段计划表本行状态）

**Interfaces:**
- Consumes: Task 1–5 的全部命令面（锁、退出码、job 目录、API、timer）。
- Produces: operator 可照做的启动/停止、锁冲突处置、失败检查、孤儿判定与恢复步骤。

- [ ] **Step 1: RUNBOOK 末尾追加新节（全文如下；```` ``` ```` 包内为要追加的原文）**

````markdown
## 阶段 10 · 操作面：只读服务、单飞锁、operations update 与 job 记录（2026-10-01 追加，P3+P4）

> 本节由 P4 批次追加，上方各节未改动。规格依据：
> docs/superpowers/specs/2026-09-29-panda-data-loop-grafting-design.md §8（只读查询面）、§9（操作面与调度）。
> **编号说明**：`## 阶段 9` 归 P2b membership refresh 计划（批次顺序在前）；本节的 10.x 子节号与之互不冲突。

### 10.0 组件与稳定信号

- 单飞锁：`data/.locks/update.lock`，flock(2) 语义。锁的生存期 = 持锁进程的
  生存期：进程退出或崩溃由内核自动释放，不存在 stale lock，任何恢复路径都
  **不删除、不重建**该锁文件。手工 `data update`、`operations update`（内层
  仍调 `data update`）、操作 API 与 systemd timer 最终都由内层 `data update`
  取同一把锁。
- 冲突信号（写死常量，`src/stock_quant/operations/update_lock.py`）：退出码
  `75` + 稳定 token 行 `update_already_running`。脚本一律判退出码。
- job 记录：`data/service/jobs/<job_id>/`——`request.json`（一次写定，不可变）、
  `stdout.log`/`stderr.log`（追加）、`status.json`（原子替换；含 pid、boot_id、
  heartbeat_at）。状态词汇固定：QUEUED/RUNNING/SUCCEEDED/FAILED/
  CANCELLED_BY_SHUTDOWN。job id 不参与 dataset version 计算。
- 操作 API：默认禁用；启用后仍只绑 127.0.0.1。

### 10.1 启动与停止

```bash
# 只读查询服务（P3 交付；可选，只在需要 HTTP 查数/看报表时启动）
python -m stock_quant.service --root . --port 8321                # 127.0.0.1:8321
# 停止 = 结束该进程（Ctrl-C 或 kill）。无状态，重启即恢复。

# 操作 API（P4 交付；可选；只在需要 HTTP 触发/查看时启动）
python -m stock_quant.operations.serve --root . --enable          # 127.0.0.1:8642
python -m stock_quant.operations.serve --root . --enable --port 8643
# 停止 = 结束该进程（Ctrl-C 或 kill）。正在运行的 job 子进程独立存活并继续
# 心跳；若它也死了，下一次 API 请求的自检会把它改记 FAILED/orphaned_process。

# systemd timer（安装见 10.6；这是唯一受调度入口）
systemctl list-timers 'stock-quant-data-update@*'
sudo systemctl stop  stock-quant-data-update@<escaped-root>.timer   # 停用计划
sudo systemctl start stock-quant-data-update@<escaped-root>.timer   # 恢复计划
```

**两个服务是两个进程、两个端口**（只读 8321 / 操作 8642），默认都只绑环回。Vue 门户（P5）对两者的调用**必须由同一个源分别转发两条前缀**（dev proxy 两条规则 / 生产反向代理同样拆分），否则浏览器的单源策略会让其中一半请求打不出去。端口默认值**已由 owner 裁定并统一**：只读 8321（本计划与之绑定的 `--port` 默认见 [panda-query-service](2026-10-01-panda-query-service.md) Task 5）、操作 8642（本计划 `serve.DEFAULT_PORT`），P3/P4/P5 三份计划不得各自另定。前端侧的转发形态见 [panda-web-portal](2026-10-01-panda-web-portal.md) pin I12 与"开工前必须知道的实现形态"第 8 条。

### 10.2 锁冲突处置（退出码 75 / update_already_running）

- 含义：另一个更新进程持有本项目 root 的 flock。**不排队、不打扰持锁者**，
  当前命令立即失败。
- 处置：确认谁在跑——`ls data/service/jobs/` 找 RUNNING（手工 CLI 没有 job：
  `ps -ef | grep 'stock_quant data update'`）；等它结束，或由 owner 决定是否
  人工干预持锁进程。
- **永远不要删除 `data/.locks/update.lock`**：文件存在不代表锁被持有；持锁者
  崩溃后内核已自动释放；删除毫无必要。

### 10.3 失败检查

```bash
# CLI 视角（一次失败运行）：输出 job_id= / status=FAILED / failure_reason=...
python -m stock_quant operations update --root .          # 退出码 0/1/75

# job 视角
cat data/service/jobs/<job_id>/status.json      # 状态、退出码、失败原因码
tail -50 data/service/jobs/<job_id>/stdout.log  # 质量门禁阻断行（blocking issue: ...）
tail -50 data/service/jobs/<job_id>/stderr.log

# API 视角（操作面启用时）
curl -s http://127.0.0.1:8642/api/v1/update-jobs
curl -s http://127.0.0.1:8642/api/v1/update-jobs/<job_id>

# timer 视角
journalctl -u stock-quant-data-update@<escaped-root>.service -n 100
```

失败不自动重试：再次运行 = 新 job id（显式命令、POST 或下一次计划触发）。
调度失败通知只读 job 的最终状态，不解析质量问题决定"忽略后继续"。

### 10.4 操作 API 的三个端点（没有别的）

- `POST /api/v1/update-jobs`：参数只接受
  start/end/sources/disclosure_lookback_days；参数非法 → 422；已有活跃 job →
  409（code=update_already_running，附 job_id）。手工 CLI 持锁（job 体系外）
  时 POST 会创建一个最终 FAILED/update_already_running 的 job——锁才是跨
  入口的最终单飞裁决。
- `GET /api/v1/update-jobs`：持久化任务摘要。
- `GET /api/v1/update-jobs/{id}`：状态 + 脱敏日志尾部（秘密值与绝对路径已
  打码）。未知 id → 404。
- 没有取消、重试到成功、删除日志、验收或研究端点；默认禁用（503）。

### 10.5 孤儿 job 判定

- 唯一存活证据是 `status.json` 里的心跳（pid + heartbeat_at + boot_id）。
  **目录存在 ≠ 存活；日志安静 ≠ 存活。**
- API 每次请求前的自检把以下情况改记 FAILED、原因码 `orphaned_process`：
  RUNNING 但心跳缺失；boot_id 与当前内核不同（机器重启过，pid 可能已被
  复用）；心跳超过约 5 分钟（阈值 300s）。超过 5 分钟仍未进入 RUNNING 的
  QUEUED 记 `orphaned_process`/never_started。
- 被判孤儿的 job **目录与日志一律保留**，只改写 status.json。

### 10.6 timer 安装与一次真实触发（owner 授权步骤）

```bash
# 实例名 = systemd-escape 的项目根（渲染函数：stock_quant.operations.systemd_units）
systemd-escape -p /home/ji/work/program/stock     # → home-ji-work-program-stock
sudo systemctl link /home/ji/work/program/stock/systemd/stock-quant-data-update@.service
sudo systemctl link /home/ji/work/program/stock/systemd/stock-quant-data-update@.timer
sudo systemctl daemon-reload
sudo systemctl enable --now stock-quant-data-update@home-ji-work-program-stock.timer
# 验证（触发一次真实更新需 owner 授权；或趁手工更新持锁时验证冲突路径）：
sudo systemctl start stock-quant-data-update@home-ji-work-program-stock.service
journalctl -u stock-quant-data-update@home-ji-work-program-stock.service -n 50
# 回滚：
sudo systemctl disable --now stock-quant-data-update@home-ji-work-program-stock.timer
```

时区 Asia/Shanghai 由 OnCalendar 的时区后缀固定；`Persistent=false`：停机
错过的触发只记 journal，不在恢复后补跑风暴。

### 10.7 崩溃后的恢复

1. 锁：无需处理（内核已释放；不要删锁文件）。
2. RUNNING 但心跳停了的 job：最多等一个自检周期（约 5 分钟）自动转
   FAILED/orphaned_process；急查可直接读该 job 的 status.json 心跳时间。
3. dataset：崩溃期间没有发布就是没有发布——CURRENT 指向的仍是旧版本；重新
   跑一次 `operations update`（或等下一次 timer 触发），会产生**新 job id**。
4. 若内层更新在崩溃前已发布成功而 job 被误判 orphaned_process：以
   `data/dataset/` 的实际版本为准（job 记录不是数据真相）。
5. 残破目录（有 request.json 无 status.json 的极小概率中间态）：无 runner
   会采用它，保留即可，不要手工清理 job 历史。
6. membership generation 不一致（`data/.membership_generation.json` 与磁盘实际
   不符）：refresh 入口与常驻服务启动都会 fail closed 报稳定错误码，**不自动
   猜、不自动修**；处置步骤在 `## 阶段 9`（P2b membership refresh 节），由
   operator 显式 `python -m stock_quant data index-membership recover --to
   <definition_version>` 选回滚到哪一代，回滚是文件替换而非重建。
````

- [ ] **Step 2: §5.3 事实文档三件套的同变更义务（条件性）**

规格 §5.3 要求"服务或调度代码首次合入时，同一变更必须更新"三份事实文档。核对 P3 是否已执行该义务（`git log --oneline -3 -- docs/architecture/overview.md` 并读文件尾部）：P3 已同步则只在各文件**追加操作面条目**；P3 未落地则本批补齐以下三处追加（同样只追加）。

`docs/architecture/overview.md` 末尾追加：

```markdown
- 操作面与调度（P4，2026-10）：CLI 仍是唯一领域写入口；`operations update`
  是带持久 job 记录（`data/service/jobs/`）的触发外壳，内层仍是同一条
  `data update` 链，经 project-local flock 单飞；操作 API 默认禁用、启用后
  仅环回；systemd timer 只调外壳。仍无数据库。
```

`docs/architecture/module-map.md` 末尾追加：

```markdown
- `stock_quant/operations/`：update_lock（flock 单飞锁与冲突常量）、runner
  （参数数组 spawn + 心跳监督）、jobs（`data/service/jobs` 持久化 + 孤儿
  判定）、api/serve（默认禁用、仅环回的三端点操作面）、systemd_units（纯
  渲染）。禁止依赖方向：不 import DataPipeline、不 import research、不构造
  发布器；唯一写路径 = 内层 `data update` CLI 子进程。
```

`docs/architecture/data-flow.md` 末尾追加：

```markdown
- 自动更新路径（P4）：timer → `operations update` →（job 记录 + 心跳）→
  `data update` 子进程（raw → normalize → gate → publish，不变）。验收不
  自动化：job SUCCEEDED 只表示发布门禁通过，ACCEPTED 仍需人工验收。
```

- [ ] **Step 3: 路线图状态行 + 治理校验**

`docs/superpowers/plans/2026-10-01-panda-data-loop-grafting-implementation.md` 的阶段计划表中把本计划一行改为：

```markdown
| [2026-10-01-panda-operations-scheduler.md](2026-10-01-panda-operations-scheduler.md) | P4（操作面、单飞锁与调度器） | 已成文，可执行 |
```

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_operational_docs.py tests/unit/test_context_governance_docs.py -q`
Expected: PASS。

- [ ] **Step 4: 批次收口（G4 核对）并提交**

对照总路线图阶段门 G4（"单飞锁、持久 job、一次计划触发通过（真实验证需授权）"）逐条勾稽：双进程单飞（Task 1）、job 持久化与孤儿判定（Task 2/3）、API 三端点（Task 4）、unit 渲染（Task 5）；"一次计划触发"若 owner 已授权则附 dated operations record 路径，未授权则如实标注"待授权"。

```bash
git add RUNBOOK.md docs/architecture/overview.md docs/architecture/module-map.md docs/architecture/data-flow.md docs/superpowers/plans/2026-10-01-panda-data-loop-grafting-implementation.md
git commit -m "docs(runbook): operations surface runbook section and fact-doc sync" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Self-Review 记录

- **规格覆盖（§9 逐条）**：
  - §9.1 统一 UpdateRunner：不调 DataPipeline Python API、参数数组启动、禁 shell 拼接 → **Task 2**（`build_data_update_argv` + `popen(argv, ...)`，测试断言全 str 数组）；允许参数只有四个且按 CLI 同型约束验证 → **Task 2**（`validate_update_params` + CLI `min=1` 同构）；新正式入口 `operations update` 同步等待并同码退出 → **Task 2**（`operations_exit_code` 0/75/1）；"已有运行中"稳定机器信号 → **Task 1**（退出码 75 裁定 + 常量）；UpdateRunner 据退出码记 FAILED/update_already_running、不匹配自由文本 → **Task 2**（`classify_child_exit`，无任何文本匹配）；timer 只调外层入口 → **Task 5**（ExecStart 断言）；手工诊断直调 `data update` 同受锁保护 → **Task 1**。
  - §9.2 单飞与持久化：CLI 自身取 project-local 锁覆盖三入口、锁粒度 = project root → **Task 1**（锁在 `data update` CLI 层）；flock(2) 语义/内核释放/无 stale lock/恢复不涉锁文件 → **Task 1**（`UpdateLock` 故意无 release；测试"holder 死后同一文件立即可取"）；锁文件 `data/.locks/update.lock` → **Task 1**；已有运行 HTTP/CLI conflict、不排队、不杀 → **Task 1**（双进程测试）+ **Task 4**（409）；job 目录三件套（不可变 request.json、追加日志、原子 status.json）→ **Task 2**（`create`/`log_paths`/`_write_status`）+ **Task 3**（write-once 与原子性测试）；状态词汇五态 → **Task 2**（`JOB_STATUSES`）+ **Task 3**（词汇冻结测试 + `CANCELLED_BY_SHUTDOWN` 由 Task 2 的 SIGTERM 路径产生）；心跳 pid+时间戳原子写入 + boot_id 防 PID 复用 → **Task 2**（`mark_running`/`heartbeat` + `read_boot_id`）；自检阈值约 5 分钟改记 FAILED/orphaned_process 不删目录、不得以目录存在/日志静默推断存活 → **Task 3**（`reap_orphaned_jobs` 四形态测试）；job id 不进 dataset version → **Task 2**（argv 无 job id 断言；dataset version 只来自 CLI 契约行解析）；成功状态记录 run id 与 dataset version → **Task 2**（`mark_succeeded` + 集成断言）。
  - §9.3 操作 API：独立 router/process、默认禁用、启用仍只环回 → **Task 4**（`router` + `enabled=False`→503 + `serve.is_loopback` 拒绝）；POST 校验并启动 operations update、已运行 409 同一冲突码 → **Task 4**；GET 摘要 / GET 单个状态+脱敏日志尾部 → **Task 4**（`redact_text`）；不提供取消/重试/删除/验收/研究端点 → **Task 4**（路由清单恰三端点测试）；失败后再运行必须新 job id → **Task 4**（rerun 测试）。
  - §9.4 调度：systemd timer 不并用 APScheduler → 全计划无该依赖；ExecStart 只调 operations update → **Task 5**；固定 unit 名 `stock-quant-data-update@<project-id>.timer` → **Task 5**（`TIMER_UNIT_PREFIX` + 渲染函数）；Asia/Shanghai → **Task 5**（OnCalendar 时区后缀断言）；Persistent=false 不补跑 → **Task 5**；冲突触发产生 FAILED/update_already_running job 后非零退出（journal 与 Web 两处可见）→ **Task 2**（runner 不预检锁）+ **Task 5**（journal 步骤）+ **Task 6** RUNBOOK 10.3；通知读 job 终态不解析质量问题 → RUNBOOK 10.3 成文；调度器不调 acceptance/research/report/清理 → **Task 5**（ExecStart 唯一且只调外壳）。
  - §5.3 事实文档同步：RUNBOOK 启动/停止/锁冲突/失败检查/恢复 → **Task 6**（含孤儿判定节）；三件套同变更义务 → **Task 6 Step 2**（条件性，成文给出全文）。
  - §5.4 依赖边界：fastapi/uvicorn 入 `service` optional extra → **Task 4**；不引入 apscheduler、不新增数据源依赖 → 全计划；duckdb 移核心属 P3 任务，本计划不动。
  - §11 相关行勾稽："timer/CLI 撞上已持有锁 → 内层 CLI 稳定冲突码、外层 job FAILED/update_already_running" → Task 1+2；"两次更新并发 → 首个持锁，第二个 409/非零退出；不排队、不双写" → Task 1（CLI 75）+ Task 4（409）+ 锁语义（同刻只可能一个写者）；"服务/调度器重启 → 已完成 job 可读；孤儿 RUNNING 转 FAILED，不删日志" → Task 3 + Task 4（分离进程 spawn，job 子进程独立存活）；"发布门禁拒绝 → job FAILED、CURRENT 不变、质量原因可从 CLI/job 查看" → 内层 CLI 既有行为（blocking issue 行入 stdout.log，RUNBOOK 10.3 指路），本计划不改门禁。
- **类型一致性**：`UPDATE_ALREADY_RUNNING_EXIT_CODE/UPDATE_ALREADY_RUNNING_CODE/UpdateAlreadyRunning/acquire_update_lock/UpdateLock`（T1→T2/T4 引用一致）；`UpdateRunParams/validate_update_params/build_data_update_argv/build_operations_update_argv/classify_child_exit/parse_contract_lines/operations_exit_code/read_boot_id/run_operations_update/OperationsUpdateResult`（T2→T4 一致）；`JobStore.create/log_paths/mark_running/heartbeat/mark_succeeded/mark_failed/mark_cancelled_by_shutdown/mark_orphaned_unreadable/get`、`JobRecord` 字段（pid/boot_id/heartbeat_at/exit_code/failure_reason/failure_detail/run_id/dataset_version）、`QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED_BY_SHUTDOWN`、`FAILURE_UPDATE_ALREADY_RUNNING(=UPDATE_ALREADY_RUNNING_CODE)/FAILURE_UPDATE_FAILED/FAILURE_ORPHANED_PROCESS`、`reap_orphaned_jobs/active_job/list_jobs/read_log_tail`（T2/T3/T4 间签名一致）；`create_operations_app/redact_text/serve.main/serve.is_loopback/DEFAULT_PORT`（T4 内自洽）；`systemd_escape_path/timer_unit_name/service_unit_name/TIMER_UNIT_PREFIX/INTERPRETER`（T5 自洽且与 unit 文件内解释器一致）。
- **已知留白（有意的，非占位符）**：
  - Task 5 Step 6 的一次真实计划触发是 owner 授权步骤（真实更新/发布需授权），未授权前 G4 只能标注"待授权"；其结论落 dated operations record。
  - Task 4 Step 0 的 fastapi/uvicorn 安装是联网环境变更，需 owner 授权；未授权则 Task 4 阻塞并汇报，不得换 Web 栈绕过（spec §5.4）。
  - ADR-021（P0 任务卡）实测仍空缺：它是本批"操作面默认禁用/只环回/调度只调外壳"的决策依据，开工前报告 owner；本计划不代写。
  - 成功路径的集成测试用注入 popen 的 fake 子进程（真实成功 `data update` 需网络传输与授权）；真实 CLI 只在冲突/失败路径被真实子进程走到（离线可确定性触发）——这是成文的测试边界，不是缺口：退出码 75 的映射与契约行解析分别有真实/fake 两级覆盖。
  - 手工 CLI 持锁（job 体系外）时 POST 返回 201、该 job 终态 FAILED/update_already_running——这是 §11"第二个 409/非零退出"二选一中的后者，裁定依据写在 Global Constraints 与 api.py docstring。
  - `data bootstrap` 不取锁（spec §9.2 只点名 `data update`；bootstrap 是一次性操作员动作），成文于此不自作主张扩面。
- **复核记录（2026-10-01 成文时）**：Task 1 测试初稿曾把"同进程二次 acquire"误写成需要 fixture 之外的路径，已改为 `tmp_path` 直写版并把初稿标注为不得照抄的笔误示例；runner 的 `mark_running` 同时写入首跳心跳（否则 RUNNING 无心跳会被自检立即误判），语义已在 Task 3 测试中显式锁住（`record.heartbeat_at is not None`）。
