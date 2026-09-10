# index-constitution csi300 静态快照与冻结股票池 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `index-constitution` 的 `csi300` 全历史冻结成带日期的不可变快照，并在其上建一条可审计的构建链，产出一个能进正式 Research 的冻结股票池定义。

**Architecture:** 两个脚本职责分离。`project/collect_index_constitution.py` 只在隔离解释器（py3.11+/pandas 3）跑一次，把上游三个 DataFrame 导成 CSV + `manifest.json`。`project/build_csi300_universe.py` 在主环境（py3.10/pandas 2）跑，验证快照全部哈希、套用 `repairs.csv` 裁定、映射成 `MembershipFact`、逐日校验、发布。原始 CSV 一字不改，所有修正集中在同目录的 `repairs.csv`。

**Tech Stack:** Python 3.10、pandas 2.x、pydantic 2、PyYAML、pytest。隔离导出侧另需 Python ≥ 3.11 + pandas ≥ 3（实测环境 `/home/ji/miniconda3/envs/sq312`）。

**设计依据:** `docs/superpowers/specs/2026-09-10-index-constitution-csi300-snapshot-design.md`

## Global Constraints

- **不迁移项目**。`pyproject.toml` 保持 `requires-python = ">=3.10"`、`pandas>=2`。pandas 3 只存在于隔离导出解释器。
- **主环境永不 `import index_constitution`**。它在 `collect_index_constitution.py` 里只允许出现在 `main()` 函数体内（局部 import），模块顶层不得出现——否则测试无法在 pandas 2 下加载该模块。
- **`data/` 整个被 `.gitignore`**。快照是落盘证据，**永不提交**。
- **快照目录不可变**。写入前必须已存在检查；已存在即报错，绝不覆盖。
- **规范 symbol 格式** `\d{6}\.(?:SH|SZ|BJ)`（如 `000001.SZ`）。上游格式是 `SZ000001`。
- **`universe_id`** 只能是 `csi300|csi500|csi1000|sse50|sse180|szse100|custom_[a-z0-9_]+`。本计划用到 `csi300` 与 `custom_csi300_ic`。
- **哈希一律 64 位小写十六进制**。
- **`source_url` 必须是可审计的 http(s) URL，authority 段不得含 `@`（不得内嵌凭证）**。
- **`MembershipFact` 是 `extra="forbid"`，恰好 11 个字段**：`universe_id, symbol, raw_effective_from, raw_effective_to, announcement_date, status, reason, source, source_url, snapshot_sha256, source_document_sha256`。**没有 `rules_version` 字段**——它只存在于 `UniverseDefinition`（即 `configs/universes/<id>.yml`）。
- **`MembershipFact` 硬校验**：`status` 与 `raw_effective_to` 是否为空必须一致；`initial_constituent` 必须 active；`delisting`/`merger_or_reorganization` 必须 removed；同一 `universe_id`/`symbol` 区间不得重叠。
- **`reason` 只能是** `initial_constituent | regular_rebalance | temporary_adjustment | delisting | merger_or_reorganization | correction`。
- **测试通过 `importlib.util.spec_from_file_location` 加载 `project/` 脚本**（仓库既有惯例，见 `tests/unit/test_index_membership_checks.py:518`）。`project/` 不是 Python 包。
- `python -m pytest` 在仓库根目录运行；`pyproject.toml` 已设 `pythonpath = ["src"]`。
- **禁止跑全量测试。禁止跑 `tests/integration`。** 实测：单个新测试文件 0.8 秒，整个 `tests/unit`（769 个）24 秒，而 `tests/integration` 要 ~18.5 分钟。每个任务的测试步骤**只允许**跑本任务新增的那一个测试文件（用下面给出的具名命令），lint 也只允许跑本任务改动的文件。不得写 `python -m pytest`（无路径）、不得写 `python -m pytest tests/`、不得以「确认没有回归」为由扩大到 `tests/integration`。
- **不需要回归全量套件的理由：** 本计划只新增两个文件（`project/collect_index_constitution.py`、`project/build_csi300_universe.py`）和两个测试文件，**不修改任何既有模块**。没有既有模块被改动，就没有回归面——全量套件验证的是本计划碰不到的东西。若执行中发现必须改动既有源码，先停下来报告，不要自行扩大测试范围。
- **唯一例外（Task 7 Step 7 之后，单次、可选、需用户确认）：** Task 7 会真实 `publisher.publish(...)` 重发数据集，而 `tests/integration` 读的正是数据集，这是本链上唯一的真实回归面。因此**只在全部真实数据落地之后**，允许由用户决定是否跑一次 `python -m pytest tests/integration -q`（~18.5 分钟）。这一条不适用于 Task 1–6，也不适用于 Task 7 的前六步。

---

### Task 1: 导出器 `export_frames` 与 manifest

**Files:**
- Create: `project/collect_index_constitution.py`
- Test: `tests/unit/test_index_constitution_snapshot.py`

**Interfaces:**
- Consumes: 无（本任务自包含）
- Produces: `export_frames(history: pd.DataFrame, latest: pd.DataFrame, events: pd.DataFrame, out_dir: Path, *, package_version: str, python_version: str, pandas_version: str, exported_on: date) -> dict`；`_sha256_file(path: Path) -> str`；模块常量 `ROOT: Path`、`SOURCE: str`、`SOURCE_URL: str`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_index_constitution_snapshot.py`：

```python
"""The index-constitution export module writes an immutable dated snapshot.

Loaded by path because ``project/`` is not a package (repo convention, see
``test_index_membership_checks.py``).  The module must stay importable under
pandas 2.x: it may not import ``index_constitution`` at module level.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_export_module():
    path = REPO_ROOT / "project" / "collect_index_constitution.py"
    spec = importlib.util.spec_from_file_location(
        "collect_index_constitution", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    history = pd.DataFrame(
        {
            "symbol": ["SZ000001", "SH600000"],
            "name": ["平安银行", "浦发银行"],
            "opt-in": pd.to_datetime(["2005-04-08", "2005-04-08"]),
            "opt-out": pd.to_datetime([None, "2007-04-30"]),
        }
    )
    latest = pd.DataFrame(
        {
            "symbol": ["SZ000001"],
            "name": ["平安银行"],
            "opt-in": pd.to_datetime(["2005-04-08"]),
        }
    )
    events = pd.DataFrame(
        {
            "event_date": pd.to_datetime(["2007-12-26"]),
            "event_type": ["merger"],
            "old_symbol": ["SH600472"],
            "new_symbol": ["SH601600"],
            "old_name": ["包头铝业"],
            "new_name": ["中国铝业"],
            "source_url": ["https://www.sse.com.cn/"],
            "notes": ["merger note"],
        }
    )
    return history, latest, events


def test_export_frames_writes_three_csvs_and_a_manifest(tmp_path: Path):
    module = _load_export_module()
    history, latest, events = _frames()
    out_dir = tmp_path / "2026-09-10"

    manifest = module.export_frames(
        history,
        latest,
        events,
        out_dir,
        package_version="1.0.0",
        python_version="3.11.9",
        pandas_version="3.0.5",
        exported_on=date(2026, 9, 10),
    )

    assert sorted(manifest["files"]) == [
        "csi300_history.csv",
        "csi300_latest.csv",
        "cn_events.csv",
    ]
    assert (out_dir / "manifest.json").is_file()
    for name in manifest["files"]:
        assert (out_dir / name).is_file()


def test_export_frames_round_trips_every_row_and_column(tmp_path: Path):
    module = _load_export_module()
    history, latest, events = _frames()
    out_dir = tmp_path / "2026-09-10"

    module.export_frames(
        history,
        latest,
        events,
        out_dir,
        package_version="1.0.0",
        python_version="3.11.9",
        pandas_version="3.0.5",
        exported_on=date(2026, 9, 10),
    )

    written = pd.read_csv(out_dir / "csi300_history.csv")
    assert list(written.columns) == list(history.columns)
    assert len(written) == len(history)
    assert written["symbol"].tolist() == history["symbol"].tolist()


def test_manifest_hashes_match_the_written_bytes(tmp_path: Path):
    module = _load_export_module()
    history, latest, events = _frames()
    out_dir = tmp_path / "2026-09-10"

    manifest = module.export_frames(
        history,
        latest,
        events,
        out_dir,
        package_version="1.0.0",
        python_version="3.11.9",
        pandas_version="3.0.5",
        exported_on=date(2026, 9, 10),
    )

    for name, recorded in manifest["files"].items():
        assert module._sha256_file(out_dir / name) == recorded
    assert manifest["package_version"] == "1.0.0"
    assert manifest["pandas_version"] == "3.0.5"
    assert manifest["exported_on"] == "2026-09-10"


def test_export_frames_refuses_to_overwrite_an_existing_snapshot(tmp_path: Path):
    module = _load_export_module()
    history, latest, events = _frames()
    out_dir = tmp_path / "2026-09-10"
    out_dir.mkdir()

    with pytest.raises(FileExistsError):
        module.export_frames(
            history,
            latest,
            events,
            out_dir,
            package_version="1.0.0",
            python_version="3.11.9",
            pandas_version="3.0.5",
            exported_on=date(2026, 9, 10),
        )


def test_module_does_not_import_index_constitution_at_module_level():
    """The module must load under pandas 2.x, where the package is unreadable."""
    module = _load_export_module()
    assert not hasattr(module, "ic")
    source = (
        REPO_ROOT / "project" / "collect_index_constitution.py"
    ).read_text(encoding="utf-8")
    # The only allowed occurrence is the local import inside main().
    assert source.count("import index_constitution") == 1
    assert "    import index_constitution as ic" in source
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_index_constitution_snapshot.py -q`
Expected: FAIL — `FileNotFoundError` / `ModuleNotFoundError`，因为 `project/collect_index_constitution.py` 还不存在。

- [ ] **Step 3: 写最小实现**

创建 `project/collect_index_constitution.py`：

```python
"""Export the index-constitution csi300 frames into a dated immutable snapshot.

Runs in an **isolated interpreter** (Python >= 3.11 with pandas >= 3): the
package's bundled pickles are serialized with pandas 3's ``StringDtype`` and
raise ``NotImplementedError`` when read by pandas 2.x, which this project pins.
The project itself never imports ``index_constitution`` -- this script writes
plain CSVs that the main environment reads with ``pd.read_csv``.

Output goes to ``data/raw/csi/index_constitution/<YYYY-MM-DD>/`` and is never
overwritten: a new upstream release gets a new dated directory, so every
snapshot hash stays resolvable and historical builds stay reproducible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
SOURCE = "index_constitution"
SOURCE_URL = "https://github.com/unliftedq/index-constitution"

#: Upstream frame -> snapshot file name.  ``cn_events`` is audit material
#: only; it never enters the membership chain.
FRAME_FILES = (
    ("csi300_history", "csi300_history.csv"),
    ("csi300_latest", "csi300_latest.csv"),
    ("cn_events", "cn_events.csv"),
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_frames(
    history: pd.DataFrame,
    latest: pd.DataFrame,
    events: pd.DataFrame,
    out_dir: Path,
    *,
    package_version: str,
    python_version: str,
    pandas_version: str,
    exported_on: date,
) -> dict:
    """Write the three frames as CSV plus a manifest, and return the manifest.

    The target directory must not exist: snapshots are immutable evidence, so
    a re-export always lands in a fresh dated directory rather than
    overwriting hashes an earlier build may still pin.
    """
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise FileExistsError(
            f"snapshot directory {out_dir} already exists; snapshots are "
            "immutable -- choose a new dated directory instead of overwriting"
        )
    out_dir.mkdir(parents=True)
    frames = {"csi300_history": history, "csi300_latest": latest, "cn_events": events}
    files: dict[str, str] = {}
    for key, name in FRAME_FILES:
        path = out_dir / name
        frames[key].to_csv(path, index=False)
        files[name] = _sha256_file(path)
    manifest = {
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "package_version": package_version,
        "python_version": python_version,
        "pandas_version": pandas_version,
        "exported_on": exported_on.isoformat(),
        "files": files,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8"
    )
    return manifest
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/unit/test_index_constitution_snapshot.py -q -k "not module_level"`
Expected: 4 passed

`test_module_does_not_import_index_constitution_at_module_level` 断言源码里存在 `    import index_constitution as ic` 这一行，而 `main()` 要到 Task 2 才写，所以本任务先排除它；Task 2 会把它跑通。

- [ ] **Step 5: 提交**

```bash
git add project/collect_index_constitution.py tests/unit/test_index_constitution_snapshot.py
git commit -m "feat: export index-constitution frames into a dated snapshot"
```

---

### Task 2: pandas 版本闸门与 `main()`

**Files:**
- Modify: `project/collect_index_constitution.py`
- Test: `tests/unit/test_index_constitution_snapshot.py`

**Interfaces:**
- Consumes: Task 1 的 `export_frames`、`ROOT`、`SOURCE`、`SOURCE_URL`
- Produces: `require_pandas_major(version: str, *, minimum: int = 3) -> None`；`build_parser() -> argparse.ArgumentParser`；`main() -> None`

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_index_constitution_snapshot.py`：

```python
def test_require_pandas_major_rejects_pandas_2():
    """The message must name the substitute interpreter, not just any failure."""
    module = _load_export_module()
    with pytest.raises(SystemExit) as excinfo:
        module.require_pandas_major("2.3.3")
    assert "sq312" in str(excinfo.value)


def test_require_pandas_major_accepts_pandas_3():
    module = _load_export_module()
    module.require_pandas_major("3.0.5")  # must not raise


def test_parser_defaults_to_todays_dated_directory():
    """``out_dir`` defaults to None so main() derives today's dated path."""
    module = _load_export_module()
    args = module.build_parser().parse_args([])
    assert args.out_dir is None
    assert module._default_out_dir() == (
        module.ROOT
        / "data"
        / "raw"
        / "csi"
        / "index_constitution"
        / date.today().isoformat()
    )


def test_parser_accepts_an_explicit_out_dir(tmp_path: Path):
    module = _load_export_module()
    args = module.build_parser().parse_args(["--out-dir", str(tmp_path / "x")])
    assert args.out_dir == tmp_path / "x"
```

Task 2 的这 4 条测试之外不再重复断言模块级 import 约束——Task 1 的
`test_module_does_not_import_index_constitution_at_module_level` 已经用**逐字相同**的
两个判定覆盖了它，再写一遍是零覆盖的冗余（Task 2 审查发现）。`main()` 落地后那条
测试自然转绿，无需另立一条。

`date` 已由 Task 1 的测试文件顶部 `from datetime import date` 导入（Task 1 的
`exported_on=date(2026, 9, 10)` 就用了它），**不要重复导入**（ruff F811）。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_index_constitution_snapshot.py -q -k "pandas_major or parser"`
Expected: FAIL with `AttributeError: module ... has no attribute 'require_pandas_major'`

- [ ] **Step 3: 写最小实现**

在 `project/collect_index_constitution.py` 末尾（`export_frames` 之后）追加：

```python
def require_pandas_major(version: str, *, minimum: int = 3) -> None:
    """Fail fast when this interpreter cannot read the bundled frames.

    The package's pickles use pandas 3's ``StringDtype``; pandas 2 raises
    ``NotImplementedError`` deep inside ``read_pickle``.  Checking up front
    turns that opaque traceback into an actionable instruction.
    """
    major = int(str(version).split(".", 1)[0])
    if major < minimum:
        raise SystemExit(
            f"index-constitution's bundled frames need pandas >= {minimum} to "
            f"read, but this interpreter has pandas {version}. Run the export "
            "in the isolated interpreter instead, e.g.\n"
            "  /home/ji/miniconda3/envs/sq312/bin/python "
            "project/collect_index_constitution.py"
        )


def _distribution_version() -> str:
    """The installed wheel's version, e.g. ``1.0.0``.

    ``index_constitution.__version__`` is stale (it still reads 0.1.0 while
    the released wheel is 1.0.0), so the distribution metadata is the
    authoritative record for the manifest and the definition's rule version.
    """
    try:
        return importlib.metadata.version("index-constitution")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export the index-constitution csi300 frames into a dated, "
            "immutable snapshot directory (requires pandas >= 3)."
        )
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "snapshot directory; defaults to "
            "data/raw/csi/index_constitution/<today>"
        ),
    )
    return parser


def _default_out_dir() -> Path:
    return (
        ROOT / "data" / "raw" / "csi" / "index_constitution"
        / date.today().isoformat()
    )


def main() -> None:
    args = build_parser().parse_args()
    require_pandas_major(pd.__version__)

    # Local import: the module must stay loadable under pandas 2.x so the
    # tests can exercise export_frames in the main environment.
    import index_constitution as ic

    out_dir = args.out_dir or _default_out_dir()
    manifest = export_frames(
        ic.history("csi300"),
        ic.latest("csi300"),
        ic.events(region="cn"),
        out_dir,
        # The module's own __version__ is stale ("0.1.0" while the released
        # wheel is 1.0.0); the installed distribution metadata is authoritative.
        package_version=_distribution_version(),
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}."
        f"{sys.version_info.micro}",
        pandas_version=pd.__version__,
        exported_on=date.today(),
    )
    print(f"snapshot={out_dir}")
    for name, digest in sorted(manifest["files"].items()):
        print(f"  {name} sha256={digest}")
    print(f"manifest sha256={_sha256_file(out_dir / 'manifest.json')}")


if __name__ == "__main__":
    main()
```

同时在文件顶部的 import 区把 `sys` 加进去：

```python
import argparse
import hashlib
import importlib.metadata
import json
import sys
from datetime import date
from pathlib import Path
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/unit/test_index_constitution_snapshot.py -q`
Expected: 9 passed（Task 1 的 5 条 + 本任务的 4 条）

- [ ] **Step 5: 跑 lint**

Run: `ruff check project/collect_index_constitution.py tests/unit/test_index_constitution_snapshot.py`
Expected: 无输出（通过）

- [ ] **Step 6: 提交**

```bash
git add project/collect_index_constitution.py tests/unit/test_index_constitution_snapshot.py
git commit -m "feat: guard the export on pandas 3 and add its CLI entry point"
```

---

### Task 3: symbol 映射与 fact 派生

**Files:**
- Create: `project/build_csi300_universe.py`
- Test: `tests/unit/test_csi300_universe_build.py`

**Interfaces:**
- Consumes: 无
- Produces: `to_canonical_symbol(raw: str) -> str`；`membership_rows(history: pd.DataFrame) -> pd.DataFrame`（列：`symbol, raw_effective_from, raw_effective_to, announcement_date, reason`）；常量 `BASE_COHORT_FROM: date`、`OPT_IN`/`OPT_OUT` 列名常量

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_csi300_universe_build.py`：

```python
"""The csi300 build turns an index-constitution snapshot into validated facts.

Loaded by path because ``project/`` is not a package (repo convention).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_build_module():
    path = REPO_ROOT / "project" / "build_csi300_universe.py"
    spec = importlib.util.spec_from_file_location("build_csi300_universe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _history_with_nat() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["SZ000001", "SH600000", "SH600501"],
            "name": ["平安银行", "浦发银行", "航天晨光"],
            "opt-in": pd.to_datetime(["2005-04-08", "2005-04-08", None]),
            "opt-out": pd.to_datetime([None, "2007-04-30", "2008-06-14"]),
        }
    )


def test_to_canonical_symbol_reverses_the_exchange_prefix():
    module = _load_build_module()
    assert module.to_canonical_symbol("SZ000001") == "000001.SZ"
    assert module.to_canonical_symbol("SH600000") == "600000.SH"


def test_to_canonical_symbol_rejects_unknown_input():
    module = _load_build_module()
    with pytest.raises(ValueError):
        module.to_canonical_symbol("000001.SZ")
    with pytest.raises(ValueError):
        module.to_canonical_symbol("BJ430047")


def test_membership_rows_marks_the_open_base_cohort_as_initial():
    module = _load_build_module()
    rows = module.membership_rows(
        pd.DataFrame(
            {
                "symbol": ["SZ000001"],
                "name": ["平安银行"],
                "opt-in": pd.to_datetime(["2005-04-08"]),
                "opt-out": pd.to_datetime([None]),
            }
        )
    )
    assert rows.iloc[0]["symbol"] == "000001.SZ"
    assert rows.iloc[0]["reason"] == "initial_constituent"
    assert pd.isna(rows.iloc[0]["raw_effective_to"])


def test_membership_rows_marks_later_entries_as_regular_rebalance():
    module = _load_build_module()
    rows = module.membership_rows(
        pd.DataFrame(
            {
                "symbol": ["SH601006"],
                "name": ["大秦铁路"],
                "opt-in": pd.to_datetime(["2006-08-12"]),
                "opt-out": pd.to_datetime([None]),
            }
        )
    )
    assert rows.iloc[0]["reason"] == "regular_rebalance"


def test_membership_rows_never_marks_a_removed_row_initial():
    module = _load_build_module()
    rows = module.membership_rows(
        pd.DataFrame(
            {
                "symbol": ["SH600000"],
                "name": ["浦发银行"],
                "opt-in": pd.to_datetime(["2005-04-08"]),
                "opt-out": pd.to_datetime(["2007-04-30"]),
            }
        )
    )
    assert rows.iloc[0]["reason"] == "regular_rebalance"


def test_membership_rows_sets_announcement_date_to_the_effective_date():
    module = _load_build_module()
    rows = module.membership_rows(
        pd.DataFrame(
            {
                "symbol": ["SZ000001"],
                "name": ["平安银行"],
                "opt-in": pd.to_datetime(["2005-04-08"]),
                "opt-out": pd.to_datetime([None]),
            }
        )
    )
    assert rows.iloc[0]["announcement_date"] == pd.Timestamp("2005-04-08")


def test_membership_rows_rejects_a_missing_opt_in_instead_of_dropping_it():
    module = _load_build_module()
    with pytest.raises(ValueError) as excinfo:
        module.membership_rows(_history_with_nat())
    assert "SH600501" in str(excinfo.value)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_csi300_universe_build.py -q`
Expected: FAIL — `FileNotFoundError`，模块尚不存在。

- [ ] **Step 3: 写最小实现**

创建 `project/build_csi300_universe.py`：

```python
"""Build the frozen csi300 universe from a sealed index-constitution snapshot.

Offline and evidence-bound: the snapshot's recorded hashes are verified before
a single fact is built, the raw CSVs are never rewritten, and every deviation
from the adjudication is expressed in the snapshot's ``repairs.csv``.

The upstream frame is ``symbol, name, opt-in, opt-out`` in ``SZ000001`` form;
facts carry the project's canonical ``000001.SZ`` form.  ``announcement_date``
is taken from the interval start: the data set records no announcement date,
and using the effective date means a fact becomes visible only on the day it
takes effect.  That is deliberately the late side of the truth (real
announcements precede the effective date by about a fortnight), so it can
underestimate early inclusion but can never leak future information.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent

#: The index launch cohort; the only intervals allowed to stay
#: ``initial_constituent`` (which the fact contract requires to be active).
BASE_COHORT_FROM = date(2005, 4, 8)

OPT_IN = "opt-in"
OPT_OUT = "opt-out"

EXCHANGES = ("SH", "SZ")

#: The columns ``prepare_membership_file`` reads, in canonical order.
MEMBERSHIP_ROW_COLUMNS = (
    "symbol",
    "raw_effective_from",
    "raw_effective_to",
    "announcement_date",
    "reason",
)


def to_canonical_symbol(raw: str) -> str:
    """Map index-constitution's ``SZ000001`` to the repo's ``000001.SZ``."""
    text = str(raw).strip().upper()
    if len(text) != 8 or text[:2] not in EXCHANGES or not text[2:].isdigit():
        raise ValueError(f"not an index-constitution symbol: {raw!r}")
    return f"{text[2:]}.{text[:2]}"


def membership_rows(history: pd.DataFrame) -> pd.DataFrame:
    """Derive ``prepare_membership_file`` rows from the raw history frame.

    A row whose ``opt-in`` is missing cannot become a fact: it has no start
    date.  Such rows are rejected loudly rather than dropped, because a
    silently dropped row is exactly how a missing inclusion hides -- and a
    missing inclusion is what makes a later removal never take effect.
    """
    missing = history[history[OPT_IN].isna()]
    if not missing.empty:
        symbols = sorted(missing["symbol"].astype(str))
        raise ValueError(
            "history carries rows with no opt-in date, which cannot be mapped "
            "to a membership fact: "
            + ", ".join(symbols)
            + " (adjudicate them in repairs.csv or exclude them explicitly)"
        )
    rows = pd.DataFrame(
        {
            "symbol": history["symbol"].map(to_canonical_symbol),
            "raw_effective_from": pd.to_datetime(history[OPT_IN]),
            "raw_effective_to": pd.to_datetime(history[OPT_OUT]),
            "announcement_date": pd.to_datetime(history[OPT_IN]),
        }
    )
    active = rows["raw_effective_to"].isna()
    base = rows["raw_effective_from"].dt.date == BASE_COHORT_FROM
    rows = rows.reset_index(drop=True)
    rows["reason"] = [
        "initial_constituent" if still_open and launched else "regular_rebalance"
        for still_open, launched in zip(active, base, strict=True)
    ]
    return rows[list(MEMBERSHIP_ROW_COLUMNS)]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/unit/test_csi300_universe_build.py -q`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add project/build_csi300_universe.py tests/unit/test_csi300_universe_build.py
git commit -m "feat: map index-constitution history onto membership facts"
```

---

### Task 4: 修复表应用 `apply_repairs`

**Files:**
- Modify: `project/build_csi300_universe.py`
- Test: `tests/unit/test_csi300_universe_build.py`

**Interfaces:**
- Consumes: Task 3 的 `OPT_IN`/`OPT_OUT`、`to_canonical_symbol`
- Produces: `apply_repairs(history: pd.DataFrame, repairs: pd.DataFrame) -> pd.DataFrame`；`read_repairs(path: Path) -> pd.DataFrame`；常量 `REPAIR_COLUMNS: tuple[str, ...]`、`REPAIR_ACTIONS: tuple[str, ...]`、`REPAIR_TIERS: tuple[str, ...]`

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_csi300_universe_build.py`：

```python
def _history() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["SZ000001", "SZ000780", "SH600000"],
            "name": ["平安银行", "平庄能源", "浦发银行"],
            "opt-in": pd.to_datetime(["2005-04-08", "2005-04-08", "2005-04-08"]),
            "opt-out": pd.to_datetime([None, "2013-12-16", "2007-04-30"]),
        }
    )


def _repairs(**overrides) -> pd.DataFrame:
    row = {
        "symbol": "SZ000780",
        "action": "set_field",
        "field": "opt-out",
        "old_value": "2013-12-16",
        "new_value": "2006-08-14",
        "evidence_tier": "A",
        "evidence_source": "data/raw/csi/csi_index_announcements/85.json",
        "evidence_detail": "官方公告：2006-08-15 起调出 000780 草原兴发",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_read_repairs_returns_the_declared_columns(tmp_path: Path):
    module = _load_build_module()
    path = tmp_path / "repairs.csv"
    path.write_text(
        "symbol,action,field,old_value,new_value,evidence_tier,"
        "evidence_source,evidence_detail\n",
        encoding="utf-8",
    )
    repairs = module.read_repairs(path)
    assert list(repairs.columns) == list(module.REPAIR_COLUMNS)
    assert repairs.empty


def test_empty_repairs_leave_the_history_untouched():
    module = _load_build_module()
    history = _history()
    result = module.apply_repairs(
        history, pd.DataFrame(columns=list(module.REPAIR_COLUMNS))
    )
    pd.testing.assert_frame_equal(result, history)


def test_set_field_replaces_the_matching_value():
    module = _load_build_module()
    result = module.apply_repairs(_history(), _repairs())
    row = result[result["symbol"] == "SZ000780"].iloc[0]
    assert row["opt-out"] == pd.Timestamp("2006-08-14")
    assert len(result) == 3


def test_set_field_does_not_mutate_the_input_frame():
    module = _load_build_module()
    history = _history()
    module.apply_repairs(history, _repairs())
    assert history.loc[history["symbol"] == "SZ000780", "opt-out"].iloc[0] == (
        pd.Timestamp("2013-12-16")
    )


def test_set_field_with_a_stale_old_value_sets_nothing():
    """A repair whose old_value no longer matches must not silently apply."""
    module = _load_build_module()
    result = module.apply_repairs(
        _history(), _repairs(old_value="1999-01-01")
    )
    row = result[result["symbol"] == "SZ000780"].iloc[0]
    assert row["opt-out"] == pd.Timestamp("2013-12-16")


def test_drop_row_removes_the_matching_interval():
    module = _load_build_module()
    result = module.apply_repairs(
        _history(),
        _repairs(action="drop_row", field="opt-in", old_value="2005-04-08"),
    )
    assert "SZ000780" not in set(result["symbol"])


def test_insert_row_appends_a_new_interval():
    module = _load_build_module()
    result = module.apply_repairs(
        _history(),
        _repairs(
            symbol="SZ002558",
            action="insert_row",
            field="opt-in",
            old_value="2026-06-12",
            new_value="",
        ),
    )
    assert "SZ002558" in set(result["symbol"])
    assert len(result) == 4


def test_unknown_action_is_rejected():
    module = _load_build_module()
    with pytest.raises(ValueError) as excinfo:
        module.apply_repairs(_history(), _repairs(action="frobnicate"))
    assert "frobnicate" in str(excinfo.value)


def test_unknown_field_is_rejected():
    module = _load_build_module()
    with pytest.raises(ValueError) as excinfo:
        module.apply_repairs(_history(), _repairs(field="name"))
    assert "name" in str(excinfo.value)


def test_unknown_evidence_tier_is_rejected():
    module = _load_build_module()
    with pytest.raises(ValueError) as excinfo:
        module.apply_repairs(_history(), _repairs(evidence_tier="C"))
    assert "C" in str(excinfo.value)


def test_set_field_on_an_absent_symbol_is_rejected():
    module = _load_build_module()
    with pytest.raises(ValueError) as excinfo:
        module.apply_repairs(_history(), _repairs(symbol="SZ999999"))
    assert "SZ999999" in str(excinfo.value)


def test_drop_row_with_a_stale_old_value_sets_nothing():
    """The no-op behavior must hold for drop_row, not only set_field."""
    module = _load_build_module()
    result = module.apply_repairs(
        _history(),
        _repairs(action="drop_row", field="opt-in", old_value="1999-01-01"),
    )
    assert len(result) == 3
    assert "SZ000780" in set(result["symbol"])


def test_drop_row_on_an_absent_symbol_is_rejected():
    module = _load_build_module()
    with pytest.raises(ValueError) as excinfo:
        module.apply_repairs(
            _history(),
            _repairs(
                symbol="SZ999999", action="drop_row", field="opt-in",
                old_value="2005-04-08",
            ),
        )
    assert "SZ999999" in str(excinfo.value)


def test_missing_evidence_source_is_rejected():
    module = _load_build_module()
    with pytest.raises(ValueError) as excinfo:
        module.apply_repairs(_history(), _repairs(evidence_source=""))
    assert "SZ000780" in str(excinfo.value)


def test_repair_old_value_format_is_normalized():
    """A non-ISO date in the hand-written table must still match.

    Without this, a mistyped ``old_value`` matches nothing -- and because a
    stale value is deliberately a no-op, the correction would silently never
    apply rather than fail loudly.
    """
    module = _load_build_module()
    result = module.apply_repairs(_history(), _repairs(old_value="2013/12/16"))
    row = result[result["symbol"] == "SZ000780"].iloc[0]
    assert row["opt-out"] == pd.Timestamp("2006-08-14")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_csi300_universe_build.py -q -k "repairs or set_field or drop_row or insert_row or unknown or absent"`
Expected: FAIL with `AttributeError: module ... has no attribute 'apply_repairs'`

- [ ] **Step 3: 写最小实现**

在 `project/build_csi300_universe.py` 中，`membership_rows` 之后追加：

```python
#: The adjudicated-correction table.  Every row must name the evidence it
#: rests on; an unadjudicated dispute is deliberately absent from this table
#: and lives in adjudication_report.md instead.
REPAIR_COLUMNS = (
    "symbol",
    "action",
    "field",
    "old_value",
    "new_value",
    "evidence_tier",
    "evidence_source",
    "evidence_detail",
)
REPAIR_ACTIONS = ("set_field", "drop_row", "insert_row")
REPAIR_FIELDS = (OPT_IN, OPT_OUT)
#: A = official CSI announcement body; B = Sina history table (corroborating).
REPAIR_TIERS = ("A", "B")


def read_repairs(path: Path) -> pd.DataFrame:
    """Read the repair table, tolerating a header-only (no-repair) file."""
    return pd.read_csv(Path(path), dtype=str).fillna("")


def _validated_repairs(repairs: pd.DataFrame) -> pd.DataFrame:
    if repairs.empty:
        return repairs
    unknown_actions = sorted(set(repairs["action"]) - set(REPAIR_ACTIONS))
    if unknown_actions:
        raise ValueError(f"unknown repair actions: {', '.join(unknown_actions)}")
    unknown_fields = sorted(set(repairs["field"]) - set(REPAIR_FIELDS))
    if unknown_fields:
        raise ValueError(f"unknown repair fields: {', '.join(unknown_fields)}")
    unknown_tiers = sorted(set(repairs["evidence_tier"]) - set(REPAIR_TIERS))
    if unknown_tiers:
        raise ValueError(f"unknown evidence tiers: {', '.join(unknown_tiers)}")
    for row in repairs.itertuples(index=False):
        if not str(row.evidence_source).strip():
            raise ValueError(
                f"repair on {row.symbol} names no evidence source"
            )
    return repairs


def _cell(value: object) -> str:
    """Comparable text for one side of a repair comparison.

    Both the frame cell and the repair's ``old_value`` are normalized here, so
    ``2013-12-16``, ``2013/12/16`` and ``2013-12-16 00:00:00`` all compare
    equal.  Normalizing only the frame side would let a mistyped date in
    ``repairs.csv`` match nothing -- indistinguishable from a genuinely stale
    repair, and this table is hand-written.  NaT/NaN/blank read as empty; text
    that will not parse as a date falls back to itself so non-date values
    still compare literally.
    """
    if value is None or value is pd.NaT:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return text
    return str(pd.Timestamp(parsed).date())


def apply_repairs(history: pd.DataFrame, repairs: pd.DataFrame) -> pd.DataFrame:
    """Apply the adjudicated corrections to a copy of the raw history frame.

    Each repair identifies its target by ``symbol`` plus the current value in
    ``field`` (``old_value``); a repair whose ``old_value`` no longer matches
    does nothing, so a stale table cannot mis-edit a changed upstream frame.
    A repair naming a ``symbol`` absent from the frame raises instead -- a row
    that matches no target at all is a broken adjudication, not a stale value.
    Unknown actions, fields and tiers raise rather than being skipped: a typo
    in the adjudication must not silently drop a fix.  ``insert_row`` is the
    exception -- it has no target, and takes its start date from ``old_value``
    and its end date (empty for still-active) from ``new_value``.
    """
    frame = history.copy()
    if repairs.empty:
        return frame
    repairs = _validated_repairs(repairs)
    for row in repairs.itertuples(index=False):
        symbol = str(row.symbol).strip()
        if row.action == "insert_row":
            frame = pd.concat(
                [
                    frame,
                    pd.DataFrame(
                        [
                            {
                                "symbol": symbol,
                                "name": str(row.evidence_detail).strip() or symbol,
                                OPT_IN: pd.to_datetime(row.old_value or pd.NaT),
                                OPT_OUT: pd.to_datetime(row.new_value or pd.NaT),
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            )
            continue
        on_symbol = frame["symbol"].astype(str) == symbol
        if not on_symbol.any():
            raise ValueError(
                f"repair on {symbol} matches no row in the history; the "
                f"snapshot or the repair is stale"
            )
        matched = on_symbol & (
            frame[row.field].map(_cell) == _cell(row.old_value)
        )
        if row.action == "set_field":
            if matched.any():
                frame.loc[matched, row.field] = pd.to_datetime(
                    row.new_value or pd.NaT
                )
            continue
        # drop_row: a stale old_value is a no-op, not an error.
        if matched.any():
            frame = frame.loc[~matched].reset_index(drop=True)
    return frame
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/unit/test_csi300_universe_build.py -q`
Expected: 22 passed（Task 3 的 7 + 本任务的 15）

- [ ] **Step 5: 提交**

```bash
git add project/build_csi300_universe.py tests/unit/test_csi300_universe_build.py
git commit -m "feat: apply the adjudicated repair table to the raw history"
```

---

### Task 5: 快照封印与校验

**Files:**
- Modify: `project/build_csi300_universe.py`
- Test: `tests/unit/test_csi300_universe_build.py`

**Interfaces:**
- Consumes: 无新依赖
- Produces: `seal_evidence(snapshot_dir: Path) -> dict`；`verify_snapshot(snapshot_dir: Path) -> tuple[dict, dict]`；常量 `MANIFEST_NAME`、`REPAIRS_NAME`、`EVIDENCE_NAME`

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_csi300_universe_build.py`：

```python
import json


def _sealed_snapshot(tmp_path: Path) -> Path:
    """A minimal sealed snapshot: one CSV, a manifest and a repairs table."""
    directory = tmp_path / "2026-09-10"
    directory.mkdir()
    (directory / "csi300_history.csv").write_text(
        "symbol,name,opt-in,opt-out\nSZ000001,平安银行,2005-04-08,\n",
        encoding="utf-8",
    )
    module = _load_build_module()
    manifest = {
        "source": "index_constitution",
        "files": {
            "csi300_history.csv": module._sha256_file(
                directory / "csi300_history.csv"
            )
        },
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8"
    )
    (directory / "repairs.csv").write_text(
        "symbol,action,field,old_value,new_value,evidence_tier,"
        "evidence_source,evidence_detail\n",
        encoding="utf-8",
    )
    module.seal_evidence(directory)
    return directory


def test_seal_evidence_pins_the_manifest_and_the_repairs(tmp_path: Path):
    module = _load_build_module()
    directory = _sealed_snapshot(tmp_path)
    summary = json.loads(
        (directory / "evidence_summary.json").read_text(encoding="utf-8")
    )
    assert summary["manifest_sha256"] == module._sha256_file(
        directory / "manifest.json"
    )
    assert summary["repairs_sha256"] == module._sha256_file(
        directory / "repairs.csv"
    )


def test_verify_snapshot_accepts_a_sealed_snapshot(tmp_path: Path):
    module = _load_build_module()
    directory = _sealed_snapshot(tmp_path)
    manifest, summary = module.verify_snapshot(directory)
    assert manifest["source"] == "index_constitution"
    assert "repairs_sha256" in summary


def test_verify_snapshot_rejects_a_tampered_csv(tmp_path: Path):
    module = _load_build_module()
    directory = _sealed_snapshot(tmp_path)
    (directory / "csi300_history.csv").write_text(
        "symbol,name,opt-in,opt-out\nSZ000001,平安银行,2005-04-08,2010-01-01\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as excinfo:
        module.verify_snapshot(directory)
    assert "csi300_history.csv" in str(excinfo.value)


def test_verify_snapshot_rejects_an_edited_repairs_table(tmp_path: Path):
    module = _load_build_module()
    directory = _sealed_snapshot(tmp_path)
    (directory / "repairs.csv").write_text(
        "symbol,action,field,old_value,new_value,evidence_tier,"
        "evidence_source,evidence_detail\n"
        "SZ000001,set_field,opt-out,,2010-01-01,A,somewhere,note\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as excinfo:
        module.verify_snapshot(directory)
    assert "repairs.csv" in str(excinfo.value)


def test_seal_evidence_requires_a_repairs_table(tmp_path: Path):
    module = _load_build_module()
    directory = tmp_path / "fresh"
    directory.mkdir()
    (directory / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        module.seal_evidence(directory)


def test_seal_evidence_returns_the_summary_it_wrote(tmp_path: Path):
    """The return contract is part of the interface, not just the file."""
    module = _load_build_module()
    directory = _sealed_snapshot(tmp_path)
    summary = module.seal_evidence(directory)  # idempotent on unchanged inputs
    assert summary["manifest_sha256"] == module._sha256_file(
        directory / "manifest.json"
    )
    assert summary["repairs_sha256"] == module._sha256_file(
        directory / "repairs.csv"
    )


def test_verify_snapshot_rejects_a_tampered_manifest(tmp_path: Path):
    module = _load_build_module()
    directory = _sealed_snapshot(tmp_path)
    (directory / "manifest.json").write_text(
        json.dumps({"source": "index_constitution", "files": {}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as excinfo:
        module.verify_snapshot(directory)
    assert "manifest" in str(excinfo.value)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_csi300_universe_build.py -q -k "seal or verify_snapshot"`
Expected: FAIL with `AttributeError: module ... has no attribute 'seal_evidence'`

- [ ] **Step 3: 写最小实现**

在 `project/build_csi300_universe.py` 顶部补 `hashlib` 与 `json`：

```python
import hashlib
import json
```

在 `apply_repairs` 之后追加：

```python
MANIFEST_NAME = "manifest.json"
REPAIRS_NAME = "repairs.csv"
EVIDENCE_NAME = "evidence_summary.json"

SOURCE = "index_constitution"
SOURCE_URL = "https://github.com/unliftedq/index-constitution"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seal_evidence(snapshot_dir: Path) -> dict:
    """Pin ``manifest.json`` and ``repairs.csv`` into ``evidence_summary.json``.

    This file's own SHA-256 becomes each fact's ``source_document_sha256`` and
    the definition's ``evidence_summary_sha256``; the file itself pins
    ``manifest_sha256`` (which covers the upstream CSVs) and ``repairs_sha256``
    (which covers the hand-authored corrections).  Binding the repair table is
    what makes the corrections tamper-evident: without it a repair could be
    edited while every other hash still verified.
    """
    snapshot_dir = Path(snapshot_dir)
    manifest_path = snapshot_dir / MANIFEST_NAME
    repairs_path = snapshot_dir / REPAIRS_NAME
    for path in (manifest_path, repairs_path):
        if not path.is_file():
            raise FileNotFoundError(f"snapshot is missing {path.name}: {path}")
    summary = {
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "manifest_sha256": _sha256_file(manifest_path),
        "repairs_sha256": _sha256_file(repairs_path),
    }
    (snapshot_dir / EVIDENCE_NAME).write_text(
        json.dumps(summary, indent=1, sort_keys=True), encoding="utf-8"
    )
    return summary


def verify_snapshot(snapshot_dir: Path) -> tuple[dict, dict]:
    """Verify every recorded hash and return ``(manifest, evidence_summary)``.

    Nothing is built until the whole evidence set matches: a snapshot that
    cannot be verified must not produce facts.
    """
    snapshot_dir = Path(snapshot_dir)
    manifest_path = snapshot_dir / MANIFEST_NAME
    repairs_path = snapshot_dir / REPAIRS_NAME
    summary_path = snapshot_dir / EVIDENCE_NAME
    for path in (manifest_path, repairs_path, summary_path):
        if not path.is_file():
            raise FileNotFoundError(f"snapshot is missing {path.name}: {path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if _sha256_file(manifest_path) != summary["manifest_sha256"]:
        raise ValueError(
            f"{MANIFEST_NAME} does not match the recorded manifest_sha256"
        )
    if _sha256_file(repairs_path) != summary["repairs_sha256"]:
        raise ValueError(
            f"{REPAIRS_NAME} does not match the recorded repairs_sha256; the "
            "repair table was edited after the snapshot was sealed"
        )
    for name, recorded in manifest["files"].items():
        actual = _sha256_file(snapshot_dir / name)
        if actual != recorded:
            raise ValueError(
                f"{name} hashes to {actual} but the manifest records {recorded}"
            )
    return manifest, summary
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/unit/test_csi300_universe_build.py -q`
Expected: 29 passed（Task 3/4 的 22 + 本任务的 7）

- [ ] **Step 5: 提交**

```bash
git add project/build_csi300_universe.py tests/unit/test_csi300_universe_build.py
git commit -m "feat: seal and verify snapshot evidence hashes"
```

---

### Task 6: 逐日校验、id 决策与构建入口

**Files:**
- Modify: `project/build_csi300_universe.py`
- Test: `tests/unit/test_csi300_universe_build.py`

**Interfaces:**
- Consumes: Task 3 的 `membership_rows`、Task 4 的 `apply_repairs`/`read_repairs`、Task 5 的 `verify_snapshot`/`seal_evidence`/`_sha256_file`
- Produces: `cardinality_deviations(rows: pd.DataFrame, sessions: Sequence[date], *, expected: int = 300) -> list[str]`；`resolve_universe_id(deviations: list[str], *, requested: str) -> str`；`membership_snapshot_path(universe_id: str) -> Path`；`build_parser() -> argparse.ArgumentParser`；`main() -> None`

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_csi300_universe_build.py`：

```python
def _rows_for_count(count: int, start: str = "2010-01-04") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": [f"{i:06d}.SZ" for i in range(count)],
            "raw_effective_from": pd.to_datetime([start] * count),
            "raw_effective_to": pd.to_datetime([None] * count),
            "announcement_date": pd.to_datetime([start] * count),
            "reason": ["regular_rebalance"] * count,
        }
    )


def test_cardinality_reports_no_deviation_at_exactly_300():
    module = _load_build_module()
    sessions = [date(2010, 1, 4), date(2010, 1, 5)]
    assert module.cardinality_deviations(_rows_for_count(300), sessions) == []


def test_cardinality_reports_each_deviating_day_with_its_count():
    module = _load_build_module()
    sessions = [date(2010, 1, 4)]
    deviations = module.cardinality_deviations(_rows_for_count(301), sessions)
    assert deviations == ["2010-01-04:301"]


def test_cardinality_reports_a_session_before_every_interval_as_zero():
    module = _load_build_module()
    sessions = [date(2010, 1, 4), date(1999, 1, 4)]
    deviations = module.cardinality_deviations(_rows_for_count(300), sessions)
    assert deviations == ["1999-01-04:0"]


def test_membership_snapshot_is_staged_outside_the_snapshot_dir():
    module = _load_build_module()
    path = module.membership_snapshot_path("custom_csi300_ic")
    assert path.name == "custom_csi300_ic_membership_snapshot.csv"
    assert path.parent.name == "csi"
    assert "index_constitution" not in path.parts


def test_resolve_universe_id_keeps_the_canonical_id_when_exact():
    module = _load_build_module()
    assert module.resolve_universe_id([], requested="csi300") == "csi300"


def test_resolve_universe_id_falls_back_to_custom_when_deviating():
    module = _load_build_module()
    assert (
        module.resolve_universe_id(["2010-01-04:301"], requested="csi300")
        == "custom_csi300_ic"
    )


def test_resolve_universe_id_keeps_an_explicitly_requested_custom_id():
    module = _load_build_module()
    assert (
        module.resolve_universe_id(["2010-01-04:301"], requested="custom_other")
        == "custom_other"
    )


def test_parser_requires_an_explicit_snapshot_dir():
    module = _load_build_module()
    with pytest.raises(SystemExit):
        module.build_parser().parse_args([])


def test_parser_takes_the_snapshot_dir(tmp_path: Path):
    module = _load_build_module()
    args = module.build_parser().parse_args(["--snapshot-dir", str(tmp_path)])
    assert args.snapshot_dir == tmp_path


def test_parser_has_a_seal_only_mode(tmp_path: Path):
    module = _load_build_module()
    args = module.build_parser().parse_args(
        ["--snapshot-dir", str(tmp_path), "--seal-evidence"]
    )
    assert args.seal_evidence is True
```

同时在该测试文件顶部补 `from datetime import date`：

```python
from datetime import date
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_csi300_universe_build.py -q -k "cardinality or resolve_universe_id or parser"`
Expected: FAIL with `AttributeError: module ... has no attribute 'cardinality_deviations'`

- [ ] **Step 3: 写最小实现**

在 `project/build_csi300_universe.py` 顶部补 `argparse` 与 `Sequence`：

```python
import argparse
from typing import Sequence
```

在 `verify_snapshot` 之后追加：

```python
CANONICAL_ID = "csi300"
CUSTOM_ID = "custom_csi300_ic"
EXPECTED_MEMBERS = 300


def cardinality_deviations(
    rows: pd.DataFrame, sessions: Sequence[date], *, expected: int = 300
) -> list[str]:
    """``["<day>:<count>", ...]`` for every session whose member count is off.

    Checked globally rather than at the repaired interval: a locally plausible
    fix can move a +1 onto another date, and only a full re-scan catches that.

    This answers a build-time question -- "is this history clean enough to
    claim the canonical ``csi300`` id?" -- so it deliberately has no notion of
    a sanctioned exception and treats every off-count day as a deviation.
    Erring strict is the safe direction here: a doubtful history is downgraded
    to ``custom_csi300_ic`` rather than allowed to masquerade as canonical.

    This grid intentionally differs from the published-facts check
    ``_cardinality_issues``, which starts counting at the universe's first
    claimed start date.  This scan instead counts every session in the pinned
    calendar, including sessions before the history's first inclusion.  That is
    the strict choice: if the calendar ever reached before the history, every
    such session would be flagged ``<day>:0`` and the history would always
    downgrade to the custom id -- the intended safe outcome, not a bug.

    Officially sanctioned temporary exceptions are a separate, later concern
    owned by the dataset quality layer, which already models them via
    ``MembershipSizeException`` in
    ``stock_quant.data_quality.raw_checks._cardinality_issues``.  That check
    runs on published facts and cannot be reused here without a publish
    round-trip, which is why this scan is a local duplicate rather than a
    call into it.
    """
    intervals = [
        (
            pd.Timestamp(row.raw_effective_from).date(),
            None
            if pd.isna(row.raw_effective_to)
            else pd.Timestamp(row.raw_effective_to).date(),
        )
        for row in rows.itertuples(index=False)
    ]
    deviations: list[str] = []
    for day in sessions:
        count = sum(
            1
            for start, end in intervals
            if start <= day and (end is None or day <= end)
        )
        if count != expected:
            deviations.append(f"{day.isoformat()}:{count}")
    return deviations


def resolve_universe_id(deviations: list[str], *, requested: str) -> str:
    """Keep ``csi300`` only when every session carries exactly 300 members.

    A deviating history may not claim the canonical id, because that id is
    what downstream cardinality acceptance gates on.  The custom pool skips
    only that check; the evidence chain is identical.
    """
    if not deviations or requested != CANONICAL_ID:
        return requested
    return CUSTOM_ID


def membership_snapshot_path(universe_id: str) -> Path:
    """Staging path for the derived membership CSV handed to the importer.

    Deliberately OUTSIDE the sealed snapshot directory: a snapshot holds only
    the enumerated evidence files, and this derived artifact is legitimately
    rewritten on every build.
    """
    return ROOT / "data" / "raw" / "csi" / f"{universe_id}_membership_snapshot.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build the frozen csi300 universe from a sealed "
            "index-constitution snapshot (offline; no network access)."
        )
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        required=True,
        help=(
            "the dated snapshot directory to build from; always explicit so "
            "the same experiment cannot silently pick up a newer snapshot"
        ),
    )
    parser.add_argument(
        "--universe-id",
        default=CANONICAL_ID,
        help=(
            "requested universe id; falls back to custom_csi300_ic when the "
            "history cannot guarantee exactly 300 members per session"
        ),
    )
    parser.add_argument(
        "--seal-evidence",
        action="store_true",
        help="write evidence_summary.json and exit (run after adjudication)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="membership parquet path; defaults to data/membership/<id>.parquet",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    snapshot_dir = Path(args.snapshot_dir)

    if args.seal_evidence:
        summary = seal_evidence(snapshot_dir)
        print(f"sealed {snapshot_dir / EVIDENCE_NAME}")
        print(f"  manifest_sha256={summary['manifest_sha256']}")
        print(f"  repairs_sha256={summary['repairs_sha256']}")
        return

    manifest, summary = verify_snapshot(snapshot_dir)
    print(
        f"verified snapshot {snapshot_dir}\n"
        f"  manifest_sha256={summary['manifest_sha256']}\n"
        f"  repairs_sha256={summary['repairs_sha256']}\n"
        f"  package_version={manifest['package_version']}"
    )
    history = pd.read_csv(snapshot_dir / "csi300_history.csv")
    repairs = read_repairs(snapshot_dir / REPAIRS_NAME)
    repaired = apply_repairs(history, repairs)
    print(
        f"history rows={len(history)} repairs={len(repairs)} "
        f"effective rows={len(repaired)}"
    )
    rows = membership_rows(repaired)

    publisher = DatasetPublisher(ROOT)
    with DatasetReader(ROOT).open(publisher.current().version) as dataset:
        calendar = dataset.read("trading_calendar")
        tables = {name: dataset.read(name) for name in dataset.tables}
    sessions = [
        day.date()
        for day in pd.to_datetime(
            calendar.loc[calendar["is_trading_day"], "calendar_date"]
        )
    ]
    deviations = cardinality_deviations(rows, sessions)
    universe_id = resolve_universe_id(deviations, requested=args.universe_id)
    if deviations:
        print(
            f"cardinality deviates from {EXPECTED_MEMBERS} on "
            f"{len(deviations)}/{len(sessions)} sessions "
            f"(first: {deviations[:8]})"
        )
        if universe_id != args.universe_id:
            print(
                f"falling back to {universe_id}: a custom pool skips only the "
                "cardinality check; the evidence chain is identical"
            )
    else:
        print(f"cardinality exactly {EXPECTED_MEMBERS} on every session")

    snapshot_csv = membership_snapshot_path(universe_id)
    snapshot_csv.parent.mkdir(parents=True, exist_ok=True)
    rows.to_csv(snapshot_csv, index=False)
    output = args.output or (
        ROOT / "data" / "membership" / f"{universe_id}.parquet"
    )
    result = prepare_membership_file(
        snapshot_csv,
        universe_id=universe_id,
        source=SOURCE,
        source_url=SOURCE_URL,
        snapshot_sha256=manifest["files"]["csi300_history.csv"],
        source_document_sha256=_sha256_file(snapshot_dir / EVIDENCE_NAME),
        effective_date=pd.Timestamp(
            rows["raw_effective_from"].min()
        ).date(),
        announcement_date=pd.Timestamp(
            rows["raw_effective_from"].min()
        ).date(),
        reason="regular_rebalance",
        output=output,
    )
    print(
        f"membership rows={len(result.frame)} "
        f"membership_table_sha256={result.content_hash}"
    )

    tables["universe_membership"] = result.frame
    published = publisher.publish(tables, QualityReport())
    print(f"dataset_version={published.version}")

    with DatasetReader(ROOT).open(published.version) as dataset:
        daily = dataset.read("daily_bar")
    repairs_sha = _sha256_file(snapshot_dir / REPAIRS_NAME)
    definition = {
        "schema_version": 1,
        "universe_id": universe_id,
        "rules_version": (
            f"{SOURCE}-{manifest['package_version']}"
            f"+repairs-{repairs_sha[:8]}"
        ),
        "membership_table_sha256": result.content_hash,
        "evidence_summary_sha256": _sha256_file(snapshot_dir / EVIDENCE_NAME),
        "coverage_start": pd.to_datetime(daily["trade_date"]).min().date().isoformat(),
        "coverage_end": pd.to_datetime(daily["trade_date"]).max().date().isoformat(),
    }
    definition_path = ROOT / "configs" / "universes" / f"{universe_id}.yml"
    definition_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Frozen universe definition generated by build_csi300_universe.py.\n"
        f"# Built from snapshot {snapshot_dir}\n"
        "# evidence_summary_sha256 = SHA-256 of that snapshot's\n"
        "# evidence_summary.json, which pins manifest.json (the upstream CSV\n"
        "# hashes) and repairs.csv (the adjudicated corrections).\n"
    )
    definition_path.write_text(
        header + yaml.safe_dump(definition, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"definition={definition_path}")


if __name__ == "__main__":
    main()
```

并在文件顶部补齐 `stock_quant` 与 `yaml` 的 import：

```python
import yaml

from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.index_membership_import import (
    prepare_membership_file,
)
from stock_quant.data_quality.models import QualityReport
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/unit/test_csi300_universe_build.py -q`
Expected: 39 passed（Task 3/4/5 的 29 + 本任务的 10）

- [ ] **Step 5: 跑 lint**

Run: `ruff check project/build_csi300_universe.py tests/unit/test_csi300_universe_build.py`
Expected: 无输出（通过）

- [ ] **Step 6: 提交**

```bash
git add project/build_csi300_universe.py tests/unit/test_csi300_universe_build.py
git commit -m "feat: add cardinality gate and build entry point"
```

---

### Task 7: 真实导出、裁定与构建

这是操作任务，不是编码任务。产物是真实快照 + 裁定报告 + 冻结定义。

**Files:**
- Create: `data/raw/csi/index_constitution/<date>/`（gitignored，不提交）
- Create: `data/membership/<universe_id>.parquet`（gitignored）
- Create: `configs/universes/<universe_id>.yml`

**Interfaces:**
- Consumes: Task 1–6 的两个脚本
- Produces: 真实快照与冻结定义；`adjudication_report.md`

- [ ] **Step 0: 确认隔离解释器就绪**

导出环境是 `/home/ji/miniconda3/envs/sq312`（Python 3.12.14 / pandas 3.0.5），`index-constitution` 1.0.0 已装入。wheel 来源与指纹：

```
index_constitution-1.0.0-py3-none-any.whl
sha256 7fa360e62d27c8f84f4e5d1bc50dbfc393c5616f41fccc9131e10717474b29ed
```

**该包原先只装在 `/tmp/ic_probe/.venv311`，而 `/tmp` 会被系统清空**——已改装入 `sq312`。若某次清空了 conda 环境，用上面的 sha256 校验 wheel 后重装：

```bash
/home/ji/miniconda3/envs/sq312/bin/pip install <path>/index_constitution-1.0.0-py3-none-any.whl
```

自检：

```bash
/home/ji/miniconda3/envs/sq312/bin/python -c "
import index_constitution as ic, pandas as pd
assert 'csi300' in ic.INDICES
h, l, e = ic.history('csi300'), ic.latest('csi300'), ic.events(region='cn')
print(list(h.columns), len(h))
print(list(l.columns), len(l))
print(list(e.columns), len(e))
"
```
Expected（实测值）：
```
['symbol', 'name', 'opt-in', 'opt-out'] 1225
['symbol', 'name', 'opt-in'] 300
['event_date', 'event_type', 'old_symbol', 'new_symbol', 'old_name', 'new_name', 'source_url', 'notes'] 51
```

- [ ] **Step 1: 在隔离解释器里跑导出**

Run:
```bash
/home/ji/miniconda3/envs/sq312/bin/python project/collect_index_constitution.py
```
Expected: 打印 `snapshot=.../2026-09-10`、三个 CSV 的 sha256 与 manifest sha256。目录里出现三个 CSV + `manifest.json`。`package_version` 应为 `1.0.0`（不是模块自报的 0.1.0）。

- [ ] **Step 2: 确认导出内容与实测一致**

Run:
```bash
python - <<'PY'
import pandas as pd
h = pd.read_csv("data/raw/csi/index_constitution/2026-09-10/csi300_history.csv")
print("rows", len(h), "symbols", h["symbol"].nunique())
print("missing opt-in rows:", h[h["opt-in"].isna()]["symbol"].tolist())
PY
```
Expected: `rows 1225 symbols 949`，missing opt-in 为 `['SH600312', 'SH600501', 'SH600549', 'SH600786']`。

- [ ] **Step 3: 建立空修复表并封印**

Run:
```bash
python - <<'PY'
from pathlib import Path
d = Path("data/raw/csi/index_constitution/2026-09-10")
(d / "repairs.csv").write_text(
    "symbol,action,field,old_value,new_value,evidence_tier,"
    "evidence_source,evidence_detail\n",
    encoding="utf-8",
)
PY
python project/build_csi300_universe.py --snapshot-dir data/raw/csi/index_constitution/2026-09-10 --seal-evidence
```
Expected: 打印 `sealed .../evidence_summary.json` 与两个 sha256。

- [ ] **Step 4: 跑一次未修复的构建，记录基线偏离**

Run:
```bash
python project/build_csi300_universe.py --snapshot-dir data/raw/csi/index_constitution/2026-09-10
```
Expected: `membership_rows` 会因 4 个缺失 `opt-in` 的 symbol **报错退出**。这是**预期行为**（Global Constraints：缺失行必须显式处理）。把报错信息里的 4 个 symbol 记下来。

- [ ] **Step 5: 逐条裁定并写 `repairs.csv`**

对 4 段偏离逐个裁定。已知证据起点：

- **2006-08-12 ~ 2007-04-29（301）**：`data/raw/csi/csi_index_announcements/85.json` 的正文给出权威答案——2006-08-15 起调入 `601006` 大秦铁路、调出 `000780` 草原兴发。**注意**：ic 中 `SZ000780` 是单区间 `2005-04-08 → 2013-12-16`，直接改 end 会让 2013-12-16 少一只。裁定必须同时决定 `000780` 在 2006 之后的重新纳入区间，否则 ±1 只是被挪走。
- **2008-06-14 ~ 2009-12-31（301）**：该日 19 进 20 出，`SH600501`、`SH600786` 的剔除因 `opt-in` 缺失而不生效。需裁定这两只的真实纳入日。新浪表（`data/raw/csi/sina_history_component/`）给出 `600501` 纳入 2007-04-30、`600786` 纳入 2005-07-01，与 ic 冲突，属 B 级旁证。
- **2012-01-01 ~ 2014-07-09（301）**：`SH600312` 的 `opt-in` 缺失使其剔除不生效。需裁定真实纳入日。
- **2017-02-13 ~ 2019-06-16（299）**：`SH600005` 武钢股份因被宝钢吸收合并单独剔除，无补入。`cn_events.csv` 里有 `SH600005 → SH600019` 的 merger 记录（2017-02-13）。

每裁定一条就往 `repairs.csv` 追加一行；`evidence_tier` 只能是 `A`（官方公告正文）或 `B`（新浪表）。**裁定不出来的不写进表**，留在报告里。

- [ ] **Step 6: 写裁定报告**

创建 `data/raw/csi/index_constitution/2026-09-10/adjudication_report.md`，逐个偏离区间写：候选解释、各来源原值、采纳与否及理由、仍未解决的项。

必须写明方向性结论：`announcement_date` 取生效日可能**低估**早期纳入（不是高估），因此不会引入前视。

- [ ] **Step 7: 重新封印并构建**

Run:
```bash
python project/build_csi300_universe.py --snapshot-dir data/raw/csi/index_constitution/2026-09-10 --seal-evidence
python project/build_csi300_universe.py --snapshot-dir data/raw/csi/index_constitution/2026-09-10
```
Expected: 要么 `cardinality exactly 300 on every session` 且 `universe_id=csi300`；要么打印偏离天数并落到 `custom_csi300_ic`。**两者都是成功**——按 spec 风险第 1 条，修不到恰好 300 是预设结果，不算失败。

- [ ] **Step 8: 记录结果到记忆**

把最终 `universe_id`、偏离天数、未裁定项写进项目记忆，供后续会话使用。

---

## Self-Review

**Spec coverage：**

| Spec 章节 | 覆盖任务 |
| --- | --- |
| 数据落地结构（带日期目录 + 7 个文件） | Task 1、5、7 |
| 导出与运行时隔离（隔离解释器、自检、manifest） | Task 1、2 |
| 修复表契约（三 action、A/B 分级、未裁定不进表） | Task 4、7 |
| 裁定报告 | Task 7 |
| 硬约束（全局重校验、canonical 拒绝、可追溯） | Task 4、6、7 |
| 溯源 schema 与哈希绑定（rules_version 字符串、evidence_summary） | Task 5、6 |
| 构建步骤 1–7 | Task 5、6 |
| 映射表 11 列 + NaT 显式处理 + 无重叠 | Task 3 |
| 测试清单（导出器、修复表、快照完整性、指纹、逐日、确定性、端到端） | Task 1–6 |

**未覆盖项（有意）：** spec 测试清单里的「确定性：同一输入构建两次 → `membership_table_sha256` 相同」。这一条由 `membership_content_hash` 自身保证（既有实现，已有测试），且 Task 6 的 `membership_rows` 是纯函数。若要在本链上再钉一遍，可在 Task 6 加一条断言两次 `membership_rows` 输出相等的测试——**建议实现时补上**，成本一行。

**类型一致性核查：**
- `export_frames(history, latest, events, out_dir, *, package_version, python_version, pandas_version, exported_on)` — Task 1 定义，Task 2 调用一致。
- `membership_rows(history) -> DataFrame` — Task 3 定义，Task 6 调用一致（传 `repaired`）。
- `apply_repairs(history, repairs)`、`read_repairs(path)` — Task 4 定义，Task 6 调用一致。
- `seal_evidence(snapshot_dir)`、`verify_snapshot(snapshot_dir) -> (manifest, summary)` — Task 5 定义，Task 6 调用一致。
- `cardinality_deviations(rows, sessions, *, expected)`、`resolve_universe_id(deviations, *, requested)` — Task 6 定义并自用。
- `_sha256_file(path) -> str` — Task 5 定义，Task 6 复用。Task 1 在另一模块里独立定义了一份（两脚本不能互相 import，`project/` 不是包），是有意的重复。
- 常量 `MANIFEST_NAME`/`REPAIRS_NAME`/`EVIDENCE_NAME`/`SOURCE`/`SOURCE_URL` — Task 5 定义，Task 6 使用。
