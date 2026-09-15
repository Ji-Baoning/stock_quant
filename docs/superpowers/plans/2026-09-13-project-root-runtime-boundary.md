# 项目根运行时边界 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让项目命令只从当前工作目录或显式 `--root` 所指项目读取配置和数据。

**Architecture:** 新建唯一的项目根解析器，跟随符号链接并区分路径错误和配置缺失。配置、CLI、管线和维护脚本只使用它返回的绝对根；根目录的可运行配置迁为模板。

**Tech Stack:** Python 3.10、pathlib、argparse、Typer、PyYAML、pytest。

**Spec:** `docs/superpowers/specs/2026-09-13-project-root-runtime-boundary-design.md`

## Global Constraints

- `--root` 未传时为 `.`；不得自动猜测仓库内的 `project/`。
- `resolve()` 跟随符号链接，错误同时包含原始与解析后路径。
- 完整项目根必须有 `configs/project.yml`、`configs/sources.yml`、`configs/costs.yml`。
- 失败发生在 source、raw store、staging 或网络请求之前，且不得改变 `CURRENT`。
- `baostock.enabled: false` 不得被 `--sources baostock` 绕过；诊断输出 `baostock: SKIP disabled by config`，且不构造或请求它。
- 用精确文件路径提交；绝不使用 `git add .`。

---

### Task 1: 建立唯一的项目根解析器

**Files:**
- Create: `src/stock_quant/project_root.py`
- Modify: `src/stock_quant/config.py:81-94`
- Create: `tests/unit/test_project_root.py`
- Modify: `tests/unit/test_config.py`

**Interfaces:**
- Produces `resolve_project_root(root: str | Path) -> Path`。
- Produces `ProjectRootPathError(raw_path: str, resolved_path: Path)` 和 `ProjectRootConfigError(raw_path: str, resolved_path: Path, missing: tuple[str, ...])`。
- `load_project_config(root: str | Path) -> ProjectConfig` starts by resolving its root and reads every YAML only through that result.

- [ ] **Step 1: 写失败测试**

```python
def test_symlink_root_resolves_to_target(tmp_path):
    target = make_project(tmp_path / "target")
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    assert resolve_project_root(link) == target.resolve()


def test_missing_root_exposes_both_paths(tmp_path):
    raw = tmp_path / "missing"
    with pytest.raises(ProjectRootPathError) as caught:
        resolve_project_root(raw)
    assert caught.value.raw_path == str(raw)
    assert caught.value.resolved_path == raw.resolve()


def test_incomplete_root_lists_all_required_configs(tmp_path):
    (tmp_path / "work" / "configs").mkdir(parents=True)
    with pytest.raises(ProjectRootConfigError) as caught:
        resolve_project_root(tmp_path / "work")
    assert caught.value.missing == (
        "configs/costs.yml", "configs/project.yml", "configs/sources.yml"
    )
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/unit/test_project_root.py tests/unit/test_config.py -q`

Expected: FAIL，模块尚不存在。

- [ ] **Step 3: 最小实现**

```python
REQUIRED_PROJECT_CONFIGS = (
    "configs/project.yml", "configs/sources.yml", "configs/costs.yml",
)

def resolve_project_root(root: str | Path) -> Path:
    raw_path = str(root)
    resolved = Path(root).expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ProjectRootPathError(raw_path, resolved)
    missing = tuple(
        item for item in REQUIRED_PROJECT_CONFIGS if not (resolved / item).is_file()
    )
    if missing:
        raise ProjectRootConfigError(raw_path, resolved, missing)
    return resolved
```

`load_project_config()` 第一行调用该函数，并只通过 `root / "configs"` 打开必需 YAML 和可选 `corporate_action_reviews.yml`；不保留 cwd、仓库或模板回退。

- [ ] **Step 4: 验证**

Run: `python -m pytest tests/unit/test_project_root.py tests/unit/test_config.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/project_root.py src/stock_quant/config.py \
  tests/unit/test_project_root.py tests/unit/test_config.py
git commit -m "feat: validate runtime project roots"
```

### Task 2: 用同一根构造管线和 CLI 服务

**Files:**
- Modify: `src/stock_quant/data_pipeline.py:417-433`
- Modify: `src/stock_quant/cli.py:196-677`
- Modify: `tests/integration/conftest.py`
- Modify: `tests/integration/test_cli.py`
- Modify: `tests/integration/test_data_pipeline.py`

**Interfaces:**
- Consumes `resolve_project_root()` from Task 1.
- `DataPipeline(project_root: str | Path, *, sources=None, sleeper=...)` removes `config_root`; config, dataset and raw paths all derive from `_project_root`.
- Produces `_resolved_project_root(root: Path) -> Path` in CLI, mapping `ProjectRootError` to existing non-zero command output before any service object exists.

- [ ] **Step 1: 写失败测试**

```python
def test_disabled_baostock_is_never_constructed_or_fetched(project_root, monkeypatch):
    write_sources(project_root, baostock=False)
    constructed = []
    def build(name, _config):
        if name == "baostock":
            constructed.append(name)
            raise AssertionError("disabled baostock constructed")
        return all_stubs()[name]
    monkeypatch.setattr("stock_quant.data_pipeline._build_source", build)
    DataPipeline(project_root).update(DataUpdateRequest(sources=("baostock",)))
    assert constructed == []
```

在 `test_cli.py` 增加空根 `data update --root <empty>` 用例；替换 `DataPipeline` 为计数替身，断言退出非零、输出含 `configs/project.yml`、计数为零。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/integration/test_cli.py tests/integration/test_data_pipeline.py -q`

Expected: 新 CLI 用例失败，因为入口尚未在构造服务前验证根。

- [ ] **Step 3: 实现单根传播**

在 `DataPipeline.__init__` 使用：

```python
self._project_root = resolve_project_root(project_root)
self._project_config = load_project_config(self._project_root)
self._raw_store = RawStore(self._project_root)
```

移除 `config_root`。在 `data bootstrap`、`data update`、`data validate`、index-membership、acceptance、research、challenge、backtest、report 的每个 Typer 入口，在创建 `DataPipeline`、`DatasetReader`、`DatasetPublisher` 或服务前解析 `root`；下游只能接收解析结果。

- [ ] **Step 4: 验证**

Run: `python -m pytest tests/integration/test_cli.py tests/integration/test_data_pipeline.py -q`

Expected: PASS；禁用 BaoStock 零构造、零请求。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/data_pipeline.py src/stock_quant/cli.py \
  tests/integration/conftest.py tests/integration/test_cli.py \
  tests/integration/test_data_pipeline.py
git commit -m "refactor: bind pipeline commands to one project root"
```

### Task 3: 使所有项目脚本以工作根运行

**Files:**
- Modify: `project/check_data_sources.py`
- Modify: `project/bootstrap_seed.py`, `project/build_csi300_universe.py`, `project/collect_csi300_official.py`, `project/collect_index_constitution.py`, `project/collect_index_weight_membership.py`, `project/collect_sina_membership.py`, `project/crosscheck_calendar_relay.py`, `project/extend_history_offline.py`, `project/probe_dataset_gates.py`, `project/probe_relay_substitution.py`, `project/probe_tushare_proxy.py`, `project/rebuild_offline_real_dataset.py`, `project/rebuild_trading_calendar.py`, `project/refresh_corporate_action_coverage.py`, `project/refresh_index_membership.py`, `project/trim_universe_membership.py`, `project/verify_update_readiness.py`, `project/audit_raw_provenance.py`, `project/execution_diagnostics.py`

> `project/build_acceptance_evidence.py` 已从本清单移除：它由
> `2026-09-13-acceptance-pending-confirmation.md` 删除（其自动 PASS 回填与验收非目标
> 冲突），证据生成并入 `data acceptance prepare` 主线，不再需要 `--root` 改造。若本计划
> 先执行，跳过该文件即可。
- Create: `tests/unit/test_check_data_sources.py`

**Interfaces:**
- Each named script exposes `main(argv: Sequence[str] | None = None) -> int` and argparse `--root`, default `Path(".")`.
- Each script calls `root = resolve_project_root(args.root)` before project config/data/source access.

- [ ] **Step 1: 写失败诊断测试**

```python
def test_disabled_baostock_is_skipped_without_construction(tmp_path, monkeypatch, capsys):
    root = make_project(tmp_path, baostock_enabled=False)
    called = []
    monkeypatch.setattr(check_data_sources, "BaoStockSource", lambda *_: called.append(1))
    assert check_data_sources.main(["--root", str(root)]) == 0
    assert called == []
    assert "baostock: SKIP disabled by config" in capsys.readouterr().out
```

另加 `crosscheck_calendar_relay.py` 和 `audit_raw_provenance.py` 的空根测试，断言 root 错误发生在 source 或 `data/raw` 访问前。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/unit/test_check_data_sources.py -q`

Expected: FAIL；原诊断脚本无 `--root` 且无条件探测 BaoStock。

- [ ] **Step 3: 实现统一脚本形态**

每个列出的脚本改为以下入口，并将原顶层副作用移入 `run(root, config)`：

```python
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = resolve_project_root(args.root)
    config = load_project_config(root)
    return run(root, config)
```

`check_data_sources.py` 用 `config.sources[name].enabled` 过滤建造器表；在调用建造器前输出禁用源的 `SKIP` 行。所有供应商脚本使用 `config.sources[name]`，不得自行创建 `SourceConfig()`。

- [ ] **Step 4: 验证**

Run: `python -m pytest tests/unit/test_check_data_sources.py tests/unit/test_tushare_transport.py tests/unit/test_tushare_relay.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add project/check_data_sources.py project/bootstrap_seed.py \
  project/build_csi300_universe.py \
  project/collect_csi300_official.py project/collect_index_constitution.py \
  project/collect_index_weight_membership.py project/collect_sina_membership.py \
  project/crosscheck_calendar_relay.py project/extend_history_offline.py \
  project/probe_dataset_gates.py project/probe_relay_substitution.py \
  project/probe_tushare_proxy.py project/rebuild_offline_real_dataset.py \
  project/rebuild_trading_calendar.py project/refresh_corporate_action_coverage.py \
  project/refresh_index_membership.py project/trim_universe_membership.py \
  project/verify_update_readiness.py project/audit_raw_provenance.py \
  project/execution_diagnostics.py tests/unit/test_check_data_sources.py
git commit -m "refactor: make project scripts root-explicit"
```

### Task 4: 迁移模板并防止仓库根回归

**Files:**
- Move: `configs/` → `templates/project-config/`
- Modify: `README.md`, `project/RUNBOOK.md`
- Modify: `tests/integration/conftest.py`, `tests/smoke/test_small_market_download.py`
- Create: `tests/integration/test_project_root_cli.py`

**Interfaces:**
- 仓库根不含 `configs/`；模板不被 `resolve_project_root()` 查找。

- [ ] **Step 1: 写失败测试**

```python
def test_repository_root_never_falls_back_to_template(monkeypatch, runner):
    calls = []
    monkeypatch.setattr(cli, "DataPipeline", lambda *_: calls.append("pipeline"))
    result = runner.invoke(cli.app, ["data", "validate", "--root", "."])
    assert result.exit_code != 0
    assert "configs/project.yml" in result.output
    assert calls == []
```

再复制 `templates/project-config` 到临时工作根，断言显式 `--root` 只传该工作根给服务。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/integration/test_project_root_cli.py -q`

Expected: FAIL；仓库根仍有 `configs/`。

- [ ] **Step 3: 迁移与文档更新**

执行 `git mv configs templates/project-config`。README 写明：复制模板到工作项目的 `configs/`，随后在项目目录运行，或使用显式 `--root`。RUNBOOK 删除任何依赖仓库根配置的示例。测试夹具创建临时项目配置，绝不依赖仓库根。

- [ ] **Step 4: 验证并提交**

Run: `python -m pytest tests/integration/test_project_root_cli.py tests/integration/test_cli.py tests/integration/test_data_pipeline.py tests/smoke/test_small_market_download.py -q && test ! -d configs && git diff --check`

Expected: PASS；仓库根没有 `configs/`。

```bash
git add -u -- configs
git add templates/project-config README.md project/RUNBOOK.md \
  tests/integration/conftest.py tests/smoke/test_small_market_download.py \
  tests/integration/test_project_root_cli.py
git commit -m "docs: separate runtime projects from source templates"
```

## Final Verification

- [ ] Run: `python -m pytest tests/unit/test_project_root.py tests/unit/test_config.py tests/unit/test_check_data_sources.py tests/integration/test_project_root_cli.py tests/integration/test_cli.py tests/integration/test_data_pipeline.py tests/smoke/test_small_market_download.py -q`
- [ ] 从仓库根运行 `python -m stock_quant data validate --root .`；预期项目根错误、零网络和零数据访问。
- [ ] 在临时项目根设 `baostock.enabled: false`，运行 `project/check_data_sources.py --root <temp-root>`；预期只有 `SKIP`，无 BaoStock raw snapshot。
