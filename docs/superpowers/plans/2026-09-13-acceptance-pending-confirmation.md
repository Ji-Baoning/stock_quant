# 验收证据待确认状态 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将六份可机械生成的验收证据纳入唯一的 `data acceptance prepare` 主线，用三态 `PENDING_CONFIRMATION` 保留人工确认门，并让证据窗口与自动检查落在同一口径。

**Architecture:** 人工检查从 `CheckResult` 中分离出 `ManualCheckStatus` / `ManualCheckResult` 三态类型（自动检查仍是二值 `CheckStatus`），并同步修好全部四处读取人工状态的比较点；证据算法从独立脚本迁入 `research.acceptance.evidence`，以同版本 manifest 的窗口生成本地证据包并原子替换；`build_checklist()` 保持纯函数供 publish / verify 复用，只有 `prepare_checklist()` 写盘。

**Tech Stack:** Python 3.10、pydantic v2、pandas、PyYAML、pytest。

**Spec:** `docs/superpowers/specs/2026-09-13-acceptance-pending-confirmation-design.md`

## Global Constraints

- 自动检查始终只允许 `PASS | FAIL`；只有人工检查允许 `PENDING_CONFIRMATION`。
- `prepare` 不能自动把人工项标为 PASS，也不得创建"带虚假证据"的行。
- 证据只来自指定版本及其绑定 raw evidence；窗口取自该版本 manifest，绝不使用固定日期、当前日期或脚本自身目录。
- 生成与验证都不得请求供应商，不得修改标准化版本、raw store 或 `CURRENT`。
- local evidence reference 必须是相对项目根的项目内路径，SHA-256 与文件字节一致。
- 任一 Pending 或 Fail 人工项只能发布 REJECTED，绝不 ACCEPTED；旧 PASS/FAIL 记录保持可读且 `acceptance_id` 逐位不变。
- `project/build_acceptance_evidence.py` 及其单测在本计划中删除；证据生成只有 `data acceptance prepare` 一条路径。
- 目标测试一律点名文件运行，不使用裸 `pytest`。

---

### Task 1: 人工检查三态与人工行分类词表

**Files:**
- Modify: `src/stock_quant/research/acceptance/models.py`（`CheckStatus` 之后新增 `ManualCheckStatus`；`MANUAL_CHECK_CODES` 之后新增 `MECHANISABLE_CODES` / `OPERATOR_ONLY_CODES`；`CheckResult` 之后新增 `ManualCheckResult`；改 `AcceptanceChecklist.manual_checks`、`AcceptanceRecord.manual_checks`、`AcceptanceRecord.validate_decision`）
- Modify: `src/stock_quant/research/acceptance/registry.py:172-181`
- Modify: `src/stock_quant/research/acceptance/service.py:72-105`、`242-282`（`prepare_checklist`、`_manual_check_reasons`、`_sanitized_manual_checks`）
- Modify: `tests/unit/test_acceptance_models.py`
- Modify: `tests/unit/test_acceptance_service.py`
- Modify: `tests/integration/conftest.py:520-540`
- Modify: `tests/integration/test_acceptance_registry.py`

**Interfaces:**
- Produces `ManualCheckStatus(PENDING_CONFIRMATION, PASS, FAIL)`；`ManualCheckStatus.PASS.value == CheckStatus.PASS.value == "PASS"`。
- Produces `ManualCheckResult(code, status: ManualCheckStatus, summary, details, evidence)`，字段集合与 `CheckResult` 完全一致。
- Produces `MECHANISABLE_CODES`（六项可机械取证）与 `OPERATOR_ONLY_CODES`（三项需外部佐证），二者恰好铺满 `MANUAL_CHECK_CODES`。
- `AcceptanceChecklist.manual_checks` 与 `AcceptanceRecord.manual_checks` 改为 `tuple[ManualCheckResult, ...]`。
- `prepare_checklist` 的九项人工行状态为 `ManualCheckStatus.PENDING_CONFIRMATION`；六项摘要 `operator review required`，三项摘要 `external corroboration required`。
- `publish_checklist` 对每个 Pending 人工行产出稳定 reason `manual_<code>_pending_confirmation`。

**背景（实现者必读）：** `ManualCheckResult` 是一个独立的 pydantic 模型，不是 `CheckResult` 的别名。把 `CheckResult` 实例传进 `tuple[ManualCheckResult, ...]` 字段会被 pydantic 拒绝（模型类型不匹配），把 `CheckStatus.PASS` 传进 `status: ManualCheckStatus` 则会被静默接受为合法值——因此下面每个构造点都必须显式改成新类型，漏一处就是运行期而不是编译期的错。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_acceptance_models.py` 顶部 import 中加入 `ManualCheckResult`、`ManualCheckStatus`（与既有 `CheckResult`、`CheckStatus` 并列），然后在 `test_check_result_rejects_empty_summary_and_unknown_fields` 之后追加：

```python
def test_manual_status_adds_only_the_pending_state():
    """The manual vocabulary is the automated one plus one unconfirmed state."""
    assert [status.value for status in ManualCheckStatus] == [
        "PENDING_CONFIRMATION",
        "PASS",
        "FAIL",
    ]
    assert ManualCheckStatus.PASS.value == CheckStatus.PASS.value
    assert ManualCheckStatus.FAIL.value == CheckStatus.FAIL.value


def test_manual_result_renders_the_legacy_check_result_payload():
    """The manual row's canonical JSON is unchanged, so record ids stay stable.

    ``acceptance_id`` is the SHA-256 of the canonical payload, and a status is
    rendered by its ``value``: as long as the two models carry the same fields
    and the two PASS/FAIL values agree, a record published before this change
    keeps its id bit-for-bit.
    """
    assert list(ManualCheckResult.model_fields) == list(CheckResult.model_fields)
    manual = ManualCheckResult(code="secret_scan", status="PASS", summary="ok")
    legacy = CheckResult(code="secret_scan", status="PASS", summary="ok")
    assert manual.model_dump(mode="json") == legacy.model_dump(mode="json")


def test_automated_check_rejects_the_pending_state():
    with pytest.raises(ValidationError):
        CheckResult(code="x", status="PENDING_CONFIRMATION", summary="ok")
    with pytest.raises(ValidationError):
        ManualCheckResult(code="x", status="UNKNOWN", summary="ok")


def test_manual_row_classification_tiles_the_policy_vocabulary():
    assert set(MECHANISABLE_CODES) | set(OPERATOR_ONLY_CODES) == set(
        MANUAL_CHECK_CODES
    )
    assert set(MECHANISABLE_CODES) & set(OPERATOR_ONLY_CODES) == set()


def test_accepted_record_cannot_carry_a_pending_manual_row():
    payload = _record_payload()
    payload["manual_checks"][0]["status"] = "PENDING_CONFIRMATION"
    with pytest.raises(ValidationError, match="ACCEPTED requires"):
        AcceptanceRecord.model_validate(payload)
```

同文件顶部 import 一并补上 `MECHANISABLE_CODES`、`OPERATOR_ONLY_CODES`。

在 `tests/integration/test_acceptance_registry.py` 的 `test_select_rejected_record_is_not_valid` 之后追加：

```python
def test_select_reads_confirmed_manual_rows(tmp_path, accepted_record):
    """A confirmed manual row is a PASS whatever enum carries it.

    Regression guard for the four sites that compare a manual row's status: a
    stale ``CheckStatus`` comparison turns every ACCEPTED record into
    ``NoValidAcceptance``, and the failure surfaces far from the model change
    that caused it.
    """
    registry = AcceptanceRegistry(tmp_path)
    registry.publish(accepted_record)
    assert registry.select(_DATASET_VERSION, CURRENT_ACCEPTED) == accepted_record
```

在 `tests/unit/test_acceptance_service.py` 的 `test_publish_records_rejection_before_raising` 之后追加：

```python
def test_pending_manual_row_is_not_publishable(project, incomplete_checklist):
    """The prepared template is unconfirmed: publishing it must reject loudly."""
    with pytest.raises(AcceptanceRejected) as captured:
        publish_checklist(
            project.root, incomplete_checklist, created_at=_CREATED_AT
        )
    record = captured.value.record
    assert record.decision is AcceptanceDecision.REJECTED
    assert all(
        f"manual_{code}_pending_confirmation" in record.reasons
        for code in MANUAL_CHECK_CODES
    )
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/unit/test_acceptance_models.py tests/unit/test_acceptance_service.py tests/integration/test_acceptance_registry.py -q`

Expected: FAIL。`ManualCheckStatus` / `ManualCheckResult` / `MECHANISABLE_CODES` 尚不存在（ImportError），且 `tests/integration/test_acceptance_registry.py` 的既有 select 用例在 `registry.py` 未同步前会报 `NoValidAcceptance`。

- [ ] **Step 3: 实现三态类型与四处比较点**

在 `models.py` 的 `CheckStatus` 之后插入：

```python
class ManualCheckStatus(str, Enum):
    """Outcome of one manual acceptance check.

    Manual rows carry one extra state on purpose: ``PENDING_CONFIRMATION``
    separates "the machine generated the evidence" from "a human read it and
    signed it off".  Automated rows never take this value -- their contract
    stays the two-valued :class:`CheckStatus`.
    """

    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    PASS = "PASS"
    FAIL = "FAIL"
```

在 `MANUAL_CHECK_CODES` 之后插入：

```python
#: Manual checks a script can evidence on its own, from the pinned version.
MECHANISABLE_CODES = (
    "source_row_count_sample",
    "missing_reason_sample",
    "corporate_action_sample",
    "benchmark_sample",
    "security_master_sample",
    "secret_scan",
)
#: Manual checks that need corroboration this project cannot produce.
OPERATOR_ONLY_CODES = (
    "exchange_calendar_sample",
    "cross_source_price_sample",
    "trading_rule_effective_dates",
)
```

在 `CheckResult` 之后插入：

```python
class ManualCheckResult(BaseModel):
    """One manual check outcome, including the unconfirmed state.

    Field-for-field identical to :class:`CheckResult`, so a persisted row
    renders the same canonical JSON and a legacy ``acceptance_id`` stays
    bit-identical; ``status`` is the only difference, and its PASS/FAIL values
    are shared with :class:`CheckStatus`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1)
    status: ManualCheckStatus
    summary: str = Field(min_length=1)
    details: dict[str, JsonValue] = Field(default_factory=dict)
    evidence: tuple[EvidenceReference, ...] = ()
```

把 `AcceptanceChecklist.manual_checks` 与 `AcceptanceRecord.manual_checks` 的类型从 `tuple[CheckResult, ...]` 改为 `tuple[ManualCheckResult, ...]`，并把 `AcceptanceChecklist` 的类 docstring 中 "manual rows start as explicit FAIL entries" 改为 "manual rows start as explicit PENDING_CONFIRMATION entries"。

把 `AcceptanceRecord.validate_decision` 的一致性检查改为分别比较两套枚举：

```python
        all_pass = all(
            item.status is CheckStatus.PASS for item in self.automated_checks
        ) and all(
            item.status is ManualCheckStatus.PASS for item in self.manual_checks
        )
```

在 `registry.py` 的 `AcceptanceRegistry.select` 中把人工行的比较改为新枚举，并 import `ManualCheckStatus`：

```python
            and all(
                check.status is CheckStatus.PASS
                for check in row.automated_checks
            )
            and all(
                check.status is ManualCheckStatus.PASS
                for check in row.manual_checks
            )
```

在 `service.py` 中：

1. import 补 `MANUAL_CHECK_CODES` 已存在，另加 `MECHANISABLE_CODES`、`ManualCheckResult`、`ManualCheckStatus`。
2. `prepare_checklist` 的人工行构造改为：

```python
    manual = tuple(
        ManualCheckResult(
            code=code,
            status=ManualCheckStatus.PENDING_CONFIRMATION,
            summary=(
                "operator review required"
                if code in MECHANISABLE_CODES
                else "external corroboration required"
            ),
        )
        for code in MANUAL_CHECK_CODES
    )
```

3. `_manual_check_reasons` 的签名改为 `checks: tuple[ManualCheckResult, ...]`，并把状态分支改为三分支（evidence 校验逻辑与顺序保持不变）：

```python
    for check in checks:
        if check.status is ManualCheckStatus.PENDING_CONFIRMATION:
            reasons.append(f"manual_{check.code}_pending_confirmation")
        elif check.status is not ManualCheckStatus.PASS:
            reasons.append(f"manual_{check.code}_failed")
        if not check.evidence:
            reasons.append(f"manual_{check.code}_evidence_missing")
```

4. `_sanitized_manual_checks` 的签名与局部变量类型改为 `ManualCheckResult`（函数体不变）。

5. `publish_checklist` 与 `prepare_checklist` 的 docstring 里 "every manual row starts as a FAIL placeholder" 改为 PENDING_CONFIRMATION 的表述。

最后把所有人工行构造点改成新类型：

- `tests/unit/test_acceptance_service.py:338-356` `_completed_manual_checks`：把 `CheckResult` 换成 `ManualCheckResult`、`status=CheckStatus.PASS` 换成 `status=ManualCheckStatus.PASS`，返回类型注解与局部注解同步改。
- `tests/unit/test_acceptance_service.py:420-436` `test_prepare_creates_complete_unpassed_manual_template`：重命名为 `test_prepare_creates_complete_pending_manual_template`，断言改为

```python
    assert all(
        row.status is ManualCheckStatus.PENDING_CONFIRMATION
        for row in checklist.manual_checks
    )
    assert all(
        row.summary == "operator review required"
        for row in checklist.manual_checks
        if row.code in MECHANISABLE_CODES
    )
    assert all(
        row.summary == "external corroboration required"
        for row in checklist.manual_checks
        if row.code in OPERATOR_ONLY_CODES
    )
```

  同文件顶部 import 补 `MECHANISABLE_CODES`、`OPERATOR_ONLY_CODES`、`ManualCheckResult`、`ManualCheckStatus`。
- `tests/integration/conftest.py:524-540` `publish_fixture_acceptance`：把 `CheckResult(...)` 换成 `ManualCheckResult(...)`、`status=CheckStatus.PASS` 换成 `status=ManualCheckStatus.PASS`，import 同步。
- `tests/integration/conftest.py` 与 `tests/unit/test_acceptance_service.py` 中其余的 `CheckResult` 构造点若构造的是**自动**检查行则保持不动（`conftest.py:520`、`526` 属自动检查，只有 `524` 那处是人工行）。

- [ ] **Step 4: 验证**

Run: `python -m pytest tests/unit/test_acceptance_models.py tests/unit/test_acceptance_service.py tests/unit/test_acceptance_checks.py tests/integration/test_acceptance_registry.py -q`

Expected: PASS。

再跑一次受共享 fixture 影响的集成用例，确认人工行构造点没有遗漏：

Run: `python -m pytest tests/integration/test_acceptance_cli.py tests/integration/test_acceptance_checks.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/research/acceptance/models.py \
  src/stock_quant/research/acceptance/registry.py \
  src/stock_quant/research/acceptance/service.py \
  tests/unit/test_acceptance_models.py tests/unit/test_acceptance_service.py \
  tests/integration/conftest.py tests/integration/test_acceptance_registry.py
git commit -m "feat: add pending acceptance confirmation state"
```

### Task 2: 证据模块：迁入、窗口同口径、原子写包

**Files:**
- Create: `src/stock_quant/research/acceptance/evidence.py`
- Create: `tests/unit/test_acceptance_evidence.py`
- Create: `tests/integration/test_acceptance_evidence.py`
- Delete: `project/build_acceptance_evidence.py`
- Delete: `tests/unit/test_build_acceptance_evidence.py`
- Modify: `docs/operations/2026-09-11-trusted-data-chain.md`

**Interfaces:**
- Consumes：Task 1 的 `MECHANISABLE_CODES`、`OPERATOR_ONLY_CODES`（自 `models`）与 `EvidenceReference`。
- Produces `EVIDENCE_DIRNAME = "acceptance-evidence"`。
- Produces `EVIDENCE_FILENAMES: dict[str, str]`（人工检查 code → 包内文件名）。
- Produces `EVIDENCE_FAILURE_CATEGORIES = ("window_missing", "dataset_unreadable", "evidence_write_failed")`。
- Produces `class EvidenceBuildError(RuntimeError)`，带 `.category`（取值必属上表）。
- Produces `evidence_window(manifest: Mapping[str, object]) -> tuple[date, date]`。
- Produces `build_mechanisable_evidence(project_root: Path, dataset_version: str) -> dict[str, EvidenceReference]`，键为人工检查 code、顺序同 `MECHANISABLE_CODES`。
- Produces `class EvidenceFile`（`name: str`、`text: str`）与六个纯构造函数 `source_row_count_evidence`、`missing_reason_evidence`、`security_master_evidence`、`benchmark_evidence`、`corporate_action_evidence`、`secret_scan_evidence`。
- 只写 `<root>/data/acceptance-evidence/<version>/`，经同级暂存目录原子替换；不写标准化版本、raw store 或 `CURRENT`。

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_acceptance_evidence.py`，把 `tests/unit/test_build_acceptance_evidence.py` 的纯函数用例整体搬过来（import 改为 `from stock_quant.research.acceptance.evidence import ...`，`MECHANISABLE` → `MECHANISABLE_CODES`、`OPERATOR_ONLY` → `OPERATOR_ONLY_CODES`，`test_mechanisable_and_operator_only_tile_the_policy_vocabulary` 与 `test_apply_evidence_passes_scripted_rows_and_leaves_the_rest` 不再搬移：前者已在 Task 1 落到 models 测试、后者针对被删除的自动 PASS 行为），其中两条断言随接口变化同步改写：

```python
# test_secret_scan_flags_a_credential_like_line：改为传 EvidenceFile 并断言包内文件名
    payload = json.loads(
        secret_scan_evidence(
            [
                EvidenceFile("clean.txt", "nothing to see\n"),
                EvidenceFile("leaky.txt", "TUSHARE_TOKEN=abcdef\n"),
            ]
        ).text
    )
    assert payload["scanned"] == ["clean.txt", "leaky.txt"]
    assert payload["hits"] == [{"path": "leaky.txt", "line": 1}]
```

```python
# test_benchmark_evidence_counts_covered_open_days：窗口键使覆盖表下沉一层
    assert payload["window"] == {"start": "2015-01-05", "end": "2015-01-07"}
    assert payload["coverage"]["000300.SH"]["rows"] == 2
    assert payload["coverage"]["000300.SH"]["open_days"] == 3
    assert payload["coverage"]["000300.SH"]["missing_open_days"] == 1
```

`test_missing_reason_evidence_counts_accepted_classifications` 的两条断言不受影响（新增的 `window` 键与它们并列），但可顺手补一条 `assert payload["window"]["start"] == "2015-01-05"`。

并追加：

```python
def test_evidence_window_uses_the_requested_start_the_checks_use() -> None:
    """The manual evidence window is the automated check's window, exactly.

    ``effective_start_date`` is deliberately ignored: a request that started
    before the data does must show up as absent bars, not silently shrink the
    window a human is signing off on.
    """
    build = {
        "requested_start_date": "2015-01-05",
        "effective_start_date": "2015-01-06",
        "resolved_end_date": "2026-08-28",
    }
    assert evidence_window({"build_config": build}) == _window(build)
    assert evidence_window({"build_config": build}) == (
        date(2015, 1, 5),
        date(2026, 8, 28),
    )


def test_evidence_window_is_missing_without_a_build_window() -> None:
    for manifest in ({}, {"build_config": {}}, {"build_config": "broken"}):
        with pytest.raises(EvidenceBuildError) as captured:
            evidence_window(manifest)
        assert captured.value.category == "window_missing"


def test_failed_staging_leaves_the_previous_pack_untouched(
    tmp_path, monkeypatch
) -> None:
    """A pack is replaced whole: a failure never yields a half-written pack."""
    pack = tmp_path / "data" / EVIDENCE_DIRNAME / "v1"
    pack.mkdir(parents=True)
    (pack / "sentinel.txt").write_text("old pack", encoding="utf-8")

    def half_written(staging, files):
        (staging / files[0].name).write_text(files[0].text, encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(evidence, "_stage_files", half_written)
    with pytest.raises(EvidenceBuildError) as captured:
        evidence._write_pack(
            tmp_path, "v1", [EvidenceFile("a.json", "{}\n")]
        )
    assert captured.value.category == "evidence_write_failed"
    assert (pack / "sentinel.txt").read_text(encoding="utf-8") == "old pack"
    assert sorted(item.name for item in pack.parent.iterdir()) == ["v1"]


def test_evidence_build_error_rejects_an_unknown_category() -> None:
    with pytest.raises(ValueError):
        EvidenceBuildError("something_else")
```

顶部 import 需要 `pytest`、`json`、`hashlib`、`date`、`Path`、`pandas as pd`，以及

```python
from stock_quant.research.acceptance import evidence
from stock_quant.research.acceptance.checks import _window
from stock_quant.research.acceptance.evidence import (
    EVIDENCE_DIRNAME,
    MECHANISABLE_CODES,
    OPERATOR_ONLY_CODES,
    EvidenceBuildError,
    EvidenceFile,
    benchmark_evidence,
    build_mechanisable_evidence,
    corporate_action_evidence,
    evidence_window,
    missing_reason_evidence,
    secret_scan_evidence,
    security_master_evidence,
    source_row_count_evidence,
)
```

创建 `tests/integration/test_acceptance_evidence.py`：

```python
"""Version-bound evidence pack behaviour on a real fixture project."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from stock_quant.research.acceptance.checks import _window
from stock_quant.research.acceptance.evidence import (
    MECHANISABLE_CODES,
    build_mechanisable_evidence,
)


def _manifest(root: Path, version: str) -> dict[str, object]:
    return json.loads(
        (
            root / "data" / "standardized" / version / "dataset_manifest.json"
        ).read_text(encoding="utf-8")
    )


def test_pack_is_version_bound_relative_and_hash_valid(fixture_root):
    refs = build_mechanisable_evidence(fixture_root.root, fixture_root.version)
    assert list(refs) == list(MECHANISABLE_CODES)
    for code, reference in refs.items():
        assert reference.kind == "local"
        assert not Path(reference.reference).is_absolute()
        path = fixture_root.root / reference.reference
        assert path.is_file(), code
        assert hashlib.sha256(path.read_bytes()).hexdigest() == reference.sha256
    assert all(
        reference.reference.startswith("data/acceptance-evidence/")
        for reference in refs.values()
    )


def test_pack_records_the_window_the_automated_check_uses(fixture_root):
    refs = build_mechanisable_evidence(fixture_root.root, fixture_root.version)
    start, end = _window(
        _manifest(fixture_root.root, fixture_root.version)["build_config"]
    )
    for code in ("missing_reason_sample", "benchmark_sample"):
        payload = json.loads(
            (fixture_root.root / refs[code].reference).read_text(encoding="utf-8")
        )
        assert payload["window"] == {
            "start": start.isoformat(),
            "end": end.isoformat(),
        }


def test_pack_rebuild_is_byte_identical(fixture_root):
    first = build_mechanisable_evidence(fixture_root.root, fixture_root.version)
    second = build_mechanisable_evidence(fixture_root.root, fixture_root.version)
    assert first == second
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/unit/test_acceptance_evidence.py tests/integration/test_acceptance_evidence.py -q`

Expected: FAIL，`stock_quant.research.acceptance.evidence` 尚不存在（ImportError）。

- [ ] **Step 3: 实现证据模块**

创建 `src/stock_quant/research/acceptance/evidence.py`。模块 docstring 之后按下列内容实现；`_json`、`_as_date`、`_columns` 与五个纯构造函数（`source_row_count_evidence`、`missing_reason_evidence`、`security_master_evidence`、`benchmark_evidence`、`corporate_action_evidence`）从 `project/build_acceptance_evidence.py:77-249` **原样搬移**，只做两处改动：

1. `missing_reason_evidence` 与 `benchmark_evidence` 的 payload 各加一个窗口键，使证据自身记录它覆盖的窗口，并使其可被断言：

```python
    window = {"start": start.isoformat(), "end": end.isoformat()}
    return EvidenceFile(
        "missing_reasons.json",
        _json(
            {
                "window": window,
                "accepted_codes": sorted(_ACCEPTED_MISSING_CODES),
                "counts": counts,
                "first_sample": samples,
            }
        ),
    )
```

```python
    return EvidenceFile(
        "benchmark_coverage.json",
        _json({"window": window, "coverage": payload}),
    )
```

   （`benchmark_evidence` 内部原本直接以 symbol 为顶层键，改为挂在 `coverage` 之下，避免与 `window` 键混在同一层；同文件的单测断言路径同步改为 `payload["coverage"]["000300.SH"]["rows"]` 等。）

2. `secret_scan_evidence` 改为扫描**内存中的包内容**，并用最终包内文件名（不是暂存目录路径）记录命中：

```python
def secret_scan_evidence(
    files: Sequence[EvidenceFile], *, limit: int = 50
) -> EvidenceFile:
    """Scan the pack's own generated files for credential-looking lines.

    The scanned names are the *final* pack names, never the staging
    directory's, so the report's bytes -- and therefore its SHA-256 -- do not
    depend on where the pack happened to be staged.  ``secret_scan.json`` is
    the one artifact that cannot contain its own hash and is therefore not in
    its own scan set.
    """
    hits: list[dict[str, object]] = []
    for file in files:
        for number, line in enumerate(file.text.splitlines(), start=1):
            if _CREDENTIAL_PATTERN.search(line):
                hits.append({"path": file.name, "line": number})
        if len(hits) >= limit:
            break
    return EvidenceFile(
        "secret_scan.json",
        _json(
            {
                "scanned": [file.name for file in files],
                "hits": hits[:limit],
            }
        ),
    )
```

新增部分全部写全如下（`Sequence`、`Mapping` 自 `typing`，`shutil`、`os`、`uuid4` 一并 import）：

```python
#: Directory (under the project root) holding one evidence pack per version.
EVIDENCE_DIRNAME = "acceptance-evidence"

#: The artifact each mechanisable manual check cites.
EVIDENCE_FILENAMES = {
    "source_row_count_sample": "source_row_counts.json",
    "missing_reason_sample": "missing_reasons.json",
    "corporate_action_sample": "corporate_action_sample.csv",
    "benchmark_sample": "benchmark_coverage.json",
    "security_master_sample": "security_master_sample.csv",
    "secret_scan": "secret_scan.json",
}

#: The stable failure categories a checklist row may summarise.
EVIDENCE_FAILURE_CATEGORIES = (
    "window_missing",
    "dataset_unreadable",
    "evidence_write_failed",
)


class EvidenceBuildError(RuntimeError):
    """The evidence pack could not be built.

    ``category`` is one of :data:`EVIDENCE_FAILURE_CATEGORIES` and is the only
    thing a checklist row ever says about the failure, so a persisted row
    stays deterministic and free of exception text.
    """

    def __init__(self, category: str) -> None:
        if category not in EVIDENCE_FAILURE_CATEGORIES:
            raise ValueError(f"unknown evidence failure category {category!r}")
        self.category = category
        super().__init__(category)


@dataclass(frozen=True)
class EvidenceFile:
    """One evidence artifact: its name under the pack and its bytes."""

    name: str
    text: str


def evidence_window(manifest: Mapping[str, object]) -> tuple[date, date]:
    """The window every mechanisable evidence file is computed over.

    Deliberately the *same* window the automated ``date_window_completeness``
    check uses -- requested start through resolved end (``checks._window``).
    It is not ``build_config.effective_start_date``: the evidence a human
    signs off on and the automated verdict must describe one window, and a
    request that started before the data does has to show up as absent bars
    rather than silently shrink what was reviewed.
    """
    build = manifest.get("build_config")
    if not isinstance(build, dict):
        raise EvidenceBuildError("window_missing")
    try:
        return _window(build)
    except (TypeError, ValueError) as error:
        raise EvidenceBuildError("window_missing") from error


def build_mechanisable_evidence(
    project_root: Path, dataset_version: str
) -> dict[str, EvidenceReference]:
    """Build the six-file evidence pack for one version and return its refs.

    The returned mapping is keyed by manual check code in
    :data:`MECHANISABLE_CODES` order; every reference is a project-relative
    path whose SHA-256 pins the staged bytes.  Raises
    :class:`EvidenceBuildError` without touching an existing pack when
    anything fails.
    """
    root = Path(project_root).resolve()
    manifest = _load_manifest(root, dataset_version)
    start, end = evidence_window(manifest)
    files = _evidence_files(root, manifest, dataset_version, start, end)
    return _write_pack(root, dataset_version, files)


def _load_manifest(root: Path, dataset_version: str) -> dict[str, object]:
    """The version's manifest JSON, or a stable build failure."""
    path = (
        root
        / "data"
        / "standardized"
        / dataset_version
        / "dataset_manifest.json"
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceBuildError("dataset_unreadable") from error
    if not isinstance(payload, dict):
        raise EvidenceBuildError("dataset_unreadable")
    return payload


def _evidence_files(
    root: Path,
    manifest: Mapping[str, object],
    dataset_version: str,
    start: date,
    end: date,
) -> tuple[EvidenceFile, ...]:
    """The six artifacts, built in memory from the pinned version."""
    try:
        with DatasetReader(root).open(dataset_version) as dataset:
            daily = dataset.read("daily_bar")
            master = dataset.read("security_master")
            calendar = dataset.read("trading_calendar")
            corporate_action = dataset.read("corporate_action")
    except (OSError, KeyError, ValueError) as error:
        raise EvidenceBuildError("dataset_unreadable") from error
    build = manifest.get("build_config")
    snapshots = (
        list(build.get("raw_snapshots", [])) if isinstance(build, dict) else []
    )
    benchmarks = tuple(load_project_config(root).benchmark_symbols)
    open_days = [day for day in _open_days(calendar) if start <= day <= end]
    written = [
        source_row_count_evidence(
            manifest, snapshots, trading_days=len(open_days)
        ),
        missing_reason_evidence(daily, master, calendar, start, end),
        security_master_evidence(master),
        benchmark_evidence(daily, calendar, benchmarks, start, end),
        corporate_action_evidence(corporate_action),
    ]
    return (*written, secret_scan_evidence(written))


def _pack_dir(root: Path, dataset_version: str) -> Path:
    """The published pack location for one version."""
    return root / "data" / EVIDENCE_DIRNAME / dataset_version


def _write_pack(
    root: Path, dataset_version: str, files: Sequence[EvidenceFile]
) -> dict[str, EvidenceReference]:
    """Stage, hash and swap in one pack; never leave a half-written pack."""
    pack = _pack_dir(root, dataset_version)
    pack.parent.mkdir(parents=True, exist_ok=True)
    staging = pack.parent / f".{dataset_version}.{uuid4().hex}.tmp"
    try:
        staging.mkdir()
        _stage_files(staging, files)
        references = {
            code: EvidenceReference(
                kind="local",
                reference=_reference(root, pack, code),
                sha256=_sha256_file(staging / EVIDENCE_FILENAMES[code]),
                summary=(
                    f"{EVIDENCE_FILENAMES[code]} generated by "
                    "data acceptance prepare"
                ),
            )
            for code in MECHANISABLE_CODES
        }
    except OSError as error:
        shutil.rmtree(staging, ignore_errors=True)
        raise EvidenceBuildError("evidence_write_failed") from error
    _swap_in(staging, pack)
    return references


def _stage_files(staging: Path, files: Sequence[EvidenceFile]) -> None:
    """Write every artifact into the staging directory."""
    for file in files:
        (staging / file.name).write_text(file.text, encoding="utf-8")


def _swap_in(staging: Path, pack: Path) -> None:
    """Replace ``pack`` with ``staging``, keeping the old pack on failure.

    POSIX cannot atomically replace a non-empty directory -- ``os.replace``
    raises ``ENOTEMPTY`` -- so the old pack is renamed aside first and deleted
    only once the new one is in place.  A crash between the two renames leaves
    one complete pack on disk, never a half-written one.
    """
    retired = pack.with_name(f".{pack.name}.{uuid4().hex}.old")
    try:
        if pack.exists():
            os.replace(pack, retired)
        os.replace(staging, pack)
    except OSError as error:
        if not pack.exists() and retired.exists():
            os.replace(retired, pack)
        shutil.rmtree(staging, ignore_errors=True)
        raise EvidenceBuildError("evidence_write_failed") from error
    shutil.rmtree(retired, ignore_errors=True)


def _reference(root: Path, pack: Path, code: str) -> str:
    """The project-relative path of one artifact once the pack is in place."""
    return (pack / EVIDENCE_FILENAMES[code]).relative_to(root).as_posix()


def _sha256_file(path: Path) -> str:
    """The lowercase hex SHA-256 of one file's bytes."""
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()
```

删除 `project/build_acceptance_evidence.py` 与 `tests/unit/test_build_acceptance_evidence.py`：

```bash
git rm project/build_acceptance_evidence.py tests/unit/test_build_acceptance_evidence.py
```

更新 `docs/operations/2026-09-11-trusted-data-chain.md`：把 §5 中 `PYTHONPATH=src python build_acceptance_evidence.py <64hex> data/acceptance/<64hex>-checklist.yml` 一行前面加 `# [2026-09-13 已废弃] ` 前缀，并在该代码块之后追加一段：

```markdown
> **2026-09-13 更新**：`project/build_acceptance_evidence.py` 已删除，证据生成并入
> `data acceptance prepare` 主线（见 `docs/superpowers/plans/2026-09-13-acceptance-pending-confirmation.md`）。
> 证据窗口不再由脚本常量决定，改为取自该版本 manifest 的请求窗口；`prepare` 生成的六项人工证据
> 状态为 `PENDING_CONFIRMATION`，须由审核者逐项确认后转 `PASS`。
```

- [ ] **Step 4: 验证**

Run: `python -m pytest tests/unit/test_acceptance_evidence.py tests/integration/test_acceptance_evidence.py -q`

Expected: PASS。

Run: `python -m pytest tests/unit -q`

Expected: PASS，且没有任何用例再 import 已删除的脚本。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/research/acceptance/evidence.py \
  tests/unit/test_acceptance_evidence.py tests/integration/test_acceptance_evidence.py \
  docs/operations/2026-09-11-trusted-data-chain.md
git rm project/build_acceptance_evidence.py tests/unit/test_build_acceptance_evidence.py
git commit -m "feat: generate version-bound acceptance evidence"
```

### Task 3: 把证据包接入 `data acceptance prepare` 主线

**Files:**
- Modify: `src/stock_quant/research/acceptance/service.py:72-206`
- Modify: `src/stock_quant/cli.py:428-451`
- Modify: `tests/unit/test_acceptance_service.py`
- Modify: `tests/integration/test_acceptance_checks.py`
- Modify: `tests/integration/test_acceptance_cli.py`
- Modify: `project/RUNBOOK.md`

**Interfaces:**
- Consumes：Task 2 的 `build_mechanisable_evidence`、`EvidenceBuildError`、`EVIDENCE_DIRNAME`。
- Produces `build_checklist(project_root: Path, dataset_version: str, operator_id: str, *, prepared_at: datetime | None = None) -> AcceptanceChecklist`——纯函数，只重算自动检查并生成九项 Pending 人工行，不写任何文件。
- Produces `prepare_checklist(project_root: Path, dataset_version: str, operator_id: str, output_path: Path, *, prepared_at: datetime | None = None) -> AcceptanceChecklist`——在 `build_checklist` 之上生成证据包、回填六项引用并写出 checklist YAML（文件本身也原子替换）。
- `publish_checklist` 与 `verify_acceptance_bindings` 改用 `build_checklist`，两者保持只读。
- CLI 输出新增 `evidence_dir=data/acceptance-evidence/<VERSION>` 与 `evidence_attached=<n>`，不输出证据内容或绝对路径。

**背景（实现者必读）：** `prepare_checklist` 今天被 `publish_checklist`（service.py:129）与 `verify_acceptance_bindings`（service.py:175，由 `runner.py:1031` 的 Research 预检调用）当作"重算基准"复用。如果把写盘留在它里面，每次 publish 与每次 research run 预检都会重写证据文件，`verify_acceptance_bindings` 更会先重写文件再校验哈希，从而把篡改检测变成"自我修复"。所以纯算与写盘必须分开，只读路径只调纯函数。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_acceptance_service.py` 的 `test_pending_manual_row_is_not_publishable` 之后追加（import 从 `stock_quant.research.acceptance.service` 补 `build_checklist`、`prepare_checklist` 已存在；从 `stock_quant.research.acceptance.evidence` 补 `EvidenceBuildError`）：

```python
def test_prepare_writes_a_pending_checklist_with_evidence(project, tmp_path):
    output = tmp_path / "checklist.yml"
    checklist = prepare_checklist(
        project.root,
        project.version,
        "operator-a",
        output,
        prepared_at=_PREPARED_AT,
    )
    rows = {row.code: row for row in checklist.manual_checks}
    assert all(
        row.status is ManualCheckStatus.PENDING_CONFIRMATION
        for row in rows.values()
    )
    assert all(rows[code].evidence for code in MECHANISABLE_CODES)
    assert all(not rows[code].evidence for code in OPERATOR_ONLY_CODES)
    assert output.is_file()
    assert (
        AcceptanceChecklist.model_validate(
            yaml.safe_load(output.read_text(encoding="utf-8"))
        )
        == checklist
    )


def test_prepare_degrades_to_pending_rows_when_evidence_fails(
    project, tmp_path, monkeypatch
):
    """A failed pack leaves unconfirmed rows, never rows with fake evidence."""

    def broken(root, version):
        raise EvidenceBuildError("window_missing")

    monkeypatch.setattr(
        "stock_quant.research.acceptance.service.build_mechanisable_evidence",
        broken,
    )
    output = tmp_path / "checklist.yml"
    checklist = prepare_checklist(
        project.root, project.version, "operator-a", output
    )
    rows = {row.code: row for row in checklist.manual_checks}
    for code in MECHANISABLE_CODES:
        assert rows[code].status is ManualCheckStatus.PENDING_CONFIRMATION
        assert rows[code].evidence == ()
        assert rows[code].summary == "evidence generation failed: window_missing"
    for code in OPERATOR_ONLY_CODES:
        assert rows[code].summary == "external corroboration required"
    assert output.is_file()


def test_verify_never_rewrites_the_evidence_pack(
    project, completed_checklist, tmp_path
):
    """The read-only verification path must not touch evidence on disk."""
    record = publish_checklist(
        project.root, completed_checklist, created_at=_CREATED_AT
    )
    evidence_path = project.root / "evidence" / f"{MANUAL_CHECK_CODES[0]}.txt"
    original = evidence_path.read_bytes()
    evidence_path.write_bytes(original + b"tamper")
    before = sorted(
        (path.relative_to(project.root).as_posix(), path.read_bytes())
        for path in (project.root / "evidence").rglob("*")
        if path.is_file()
    )
    with pytest.raises(AcceptanceBindingError):
        verify_acceptance_bindings(project.root, record)
    after = sorted(
        (path.relative_to(project.root).as_posix(), path.read_bytes())
        for path in (project.root / "evidence").rglob("*")
        if path.is_file()
    )
    assert before == after
```

在 `tests/integration/test_acceptance_checks.py` 中 import `prepare_checklist`、`MECHANISABLE_CODES`、`OPERATOR_ONLY_CODES`、`ManualCheckStatus`，并追加：

```python
def test_prepare_creates_pending_checklist_with_six_evidence_files(
    fixture_root, tmp_path
):
    output = tmp_path / "checklist.yml"
    checklist = prepare_checklist(
        fixture_root.root, fixture_root.version, "reviewer", output
    )
    rows = {row.code: row for row in checklist.manual_checks}
    assert all(
        row.status is ManualCheckStatus.PENDING_CONFIRMATION
        for row in rows.values()
    )
    assert all(rows[code].evidence for code in MECHANISABLE_CODES)
    assert all(not rows[code].evidence for code in OPERATOR_ONLY_CODES)
    pack = (
        fixture_root.root
        / "data"
        / "acceptance-evidence"
        / fixture_root.version
    )
    assert sorted(path.name for path in pack.iterdir()) == [
        "benchmark_coverage.json",
        "corporate_action_sample.csv",
        "missing_reasons.json",
        "secret_scan.json",
        "security_master_sample.csv",
        "source_row_counts.json",
    ]
    for code in MECHANISABLE_CODES:
        path = fixture_root.root / rows[code].evidence[0].reference
        assert hashlib.sha256(path.read_bytes()).hexdigest() == (
            rows[code].evidence[0].sha256
        )
    assert output.is_file()


def test_prepare_leaves_current_and_the_dataset_untouched(fixture_root, tmp_path):
    def snapshot() -> list[str]:
        root = fixture_root.root / "data"
        return sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and "acceptance-evidence" not in path.parts
        )

    before = snapshot()
    current = (fixture_root.root / "data" / "standardized" / "CURRENT").read_text(
        encoding="utf-8"
    )
    prepare_checklist(
        fixture_root.root,
        fixture_root.version,
        "reviewer",
        tmp_path / "checklist.yml",
    )
    assert snapshot() == before
    assert (
        fixture_root.root / "data" / "standardized" / "CURRENT"
    ).read_text(encoding="utf-8") == current
```

在 `tests/integration/test_acceptance_cli.py` 的 `_prepare` 辅助函数里的既有断言之后追加：

```python
    assert "evidence_dir=data/acceptance-evidence/" in result.stdout
    assert "evidence_attached=6" in result.stdout
```

并在该文件追加一条未确认清单不可发布的 CLI 用例：

```python
def test_publish_rejects_the_untouched_prepared_checklist(cli_runner, project):
    prepared = _prepare(cli_runner, project)
    result = _invoke(cli_runner, project, "publish", "--checklist", str(prepared))
    assert result.exit_code == 1
    assert "decision=REJECTED" in result.stdout
    assert "reason=manual_benchmark_sample_pending_confirmation" in result.stdout
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/unit/test_acceptance_service.py tests/integration/test_acceptance_checks.py tests/integration/test_acceptance_cli.py -q`

Expected: FAIL。`prepare_checklist` 尚无 `output_path` 参数、不生成证据、也不输出 `evidence_dir`；`build_checklist` 尚不存在。

- [ ] **Step 3: 拆分纯算与写盘，并接入 CLI**

把 `service.py` 现有的 `prepare_checklist` 函数体改名为 `build_checklist` 并保留原签名中的 `prepared_at`（纯函数，无写盘），docstring 改为说明它只重算不落盘；随后新增写盘编排：

```python
def prepare_checklist(
    project_root: Path,
    dataset_version: str,
    operator_id: str,
    output_path: Path,
    *,
    prepared_at: datetime | None = None,
) -> AcceptanceChecklist:
    """Build the checklist, generate the evidence pack, and write both.

    The pack is replaced whole before the checklist is written, and a failed
    build never yields a row with fake evidence: those rows stay
    ``PENDING_CONFIRMATION`` with empty evidence and a stable failure category
    in their summary, so publishing them rejects instead of accepting.
    """
    root = Path(project_root).resolve()
    checklist = build_checklist(
        root, dataset_version, operator_id, prepared_at=prepared_at
    )
    failed: str | None = None
    try:
        references = build_mechanisable_evidence(root, dataset_version)
    except EvidenceBuildError as error:
        references = {}
        failed = error.category
    checklist = checklist.model_copy(
        update={
            "manual_checks": _attach_evidence(
                checklist.manual_checks, references, failed=failed
            )
        }
    )
    _write_checklist(checklist, Path(output_path))
    return checklist


def _attach_evidence(
    rows: tuple[ManualCheckResult, ...],
    references: Mapping[str, EvidenceReference],
    *,
    failed: str | None,
) -> tuple[ManualCheckResult, ...]:
    """Point each mechanisable row at its artifact, or name the failure."""
    attached: list[ManualCheckResult] = []
    for row in rows:
        reference = references.get(row.code)
        if reference is not None:
            attached.append(row.model_copy(update={"evidence": (reference,)}))
        elif failed is not None and row.code in MECHANISABLE_CODES:
            attached.append(
                row.model_copy(
                    update={
                        "summary": f"evidence generation failed: {failed}"
                    }
                )
            )
        else:
            attached.append(row)
    return tuple(attached)


def _write_checklist(
    checklist: AcceptanceChecklist, output_path: Path
) -> None:
    """Write the checklist YAML atomically (temp file, then replace)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    staging = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
    staging.write_text(
        yaml.safe_dump(
            checklist.model_dump(mode="json"),
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    os.replace(staging, output_path)
```

`service.py` 顶部补 `os`、`uuid4`、`Mapping` 与 `MECHANISABLE_CODES`、`ManualCheckResult`、`EvidenceReference`（已存在）、`build_mechanisable_evidence`、`EvidenceBuildError` 的 import。

`prepare_checklist` 多出必填的 `output_path`，把 `tests/unit/test_acceptance_service.py` 中两个旧调用点改走纯函数（它们要的是"重算出的清单"，不是落盘）：

- `_write_checklist_yaml`（约 358-380 行）里的 `prepare_checklist(...)` 改为 `build_checklist(...)`，其余不变（该辅助函数自己写 YAML）。
- `test_prepare_is_deterministic_and_strips_the_operator_id`（约 440-450 行）重命名为 `test_build_checklist_is_deterministic_and_strips_the_operator_id`，内部调用改为 `build_checklist`。
- import 处把 `prepare_checklist` 与新增的 `build_checklist` 一并导入（写盘版仍被 Task 3 的新用例使用）。

把两处只读重算改为纯函数：

```python
    fresh = build_checklist(
        root,
        checklist.dataset_version,
        checklist.operator_id,
        prepared_at=checklist.prepared_at,
    )
```

（`publish_checklist` 与 `verify_acceptance_bindings` 各一处；`verify_acceptance_bindings` 的 docstring 保持"Read-only"，并补一句它不再生成任何证据。）

在 `cli.py` 的 `data_acceptance_prepare` 中改为调用写盘版并把输出放全：

```python
    project_root = _resolved_project_root(root)
    checklist = prepare_checklist(project_root, version, operator, output)
    typer.echo(f"checklist={output.name}")
    typer.echo(f"evidence_dir=data/{EVIDENCE_DIRNAME}/{version}")
    typer.echo(
        "evidence_attached="
        f"{sum(1 for row in checklist.manual_checks if row.evidence)}"
    )
    typer.echo(
        f"manual_checks={len(checklist.manual_checks)} pending_confirmation"
    )
```

`output.write_text(...)` 与随之不再使用的 `yaml` import 一并删除（若 `yaml` 在该模块别处仍被使用则保留）；`cli.py` 顶部新增 `from stock_quant.research.acceptance.evidence import EVIDENCE_DIRNAME`（`prepare_checklist` 已在 cli.py:77 导入）。该命令的 docstring 从 "every manual row starts as an explicit FAIL the operator must turn into PASS with evidence" 改为：

```python
    """Write the checklist YAML and its deterministic evidence pack.

    Automated rows carry the fresh offline checker verdicts; all nine manual
    rows start as ``PENDING_CONFIRMATION``, six of them pointing at evidence
    this command generated under ``data/acceptance-evidence/<version>/`` and
    three requiring external corroboration only the operator can supply.
    Nothing here marks a manual row PASS.
    """
```

把 `project/RUNBOOK.md` 阶段 4.5 的说明段改为：`prepare` 已生成六项待确认证据，审核者仍须逐项审阅六项、补齐三项外部佐证，然后才把九项改为 `PASS` 并 publish；并说明证据包位置为 `data/acceptance-evidence/<VERSION>/`、未确认的清单 publish 只会得到 `REJECTED`。

- [ ] **Step 4: 验证**

Run: `python -m pytest tests/unit/test_acceptance_service.py tests/integration/test_acceptance_checks.py tests/integration/test_acceptance_cli.py -q`

Expected: PASS。

Run: `python -m pytest tests/integration/test_acceptance_registry.py tests/unit -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/research/acceptance/service.py src/stock_quant/cli.py \
  tests/unit/test_acceptance_service.py tests/integration/test_acceptance_checks.py \
  tests/integration/test_acceptance_cli.py project/RUNBOOK.md
git commit -m "feat: prepare pending acceptance evidence"
```

## Final Verification

- [ ] Run: `python -m pytest tests/unit/test_acceptance_models.py tests/unit/test_acceptance_service.py tests/unit/test_acceptance_evidence.py tests/integration/test_acceptance_evidence.py tests/integration/test_acceptance_registry.py tests/integration/test_acceptance_checks.py tests/integration/test_acceptance_cli.py -q`
- [ ] 在一个非 2015 fixture 版本上跑 `data acceptance prepare`：产出六份项目相对、哈希可校验的证据引用与九行 Pending 人工行，`evidence_attached=6`。
- [ ] 未改动的 prepare 清单立刻 `publish` 得到 REJECTED 且 reason 含 `manual_<code>_pending_confirmation`；九行都改 PASS 且每项 evidence 可校验后才 ACCEPTED。
- [ ] 证据窗口与自动检查 `date_window_completeness` 同口径：`evidence_window()` 忽略 `effective_start_date`，证据文件内的 `window` 等于 `checks._window(manifest["build_config"])`。
- [ ] `grep -rn "build_acceptance_evidence" --include=*.py --include=*.md .` 只剩历史计划/操作日志中的记录，源码与测试中无引用。
