# 验收佐证工作表与统一确认 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为九项人工验收检查落地「工作表 + 确认」主线：`prepare` 写出版本绑定工作表，`confirm` 成为唯一能把人工行变成 `PASS`/`FAIL` 的写入口，三项外部佐证获得机器可执行的输入契约与由程序判定的确认强度。

**Architecture:** 新增 `src/stock_quant/research/acceptance/worksheet.py`（工作表格式、修订链、确认写入者）与 `src/stock_quant/research/acceptance/external_inputs.py`（内容寻址外部输入仓 + 三个比对器）。已签工作表以不可变**修订链**存放在 `data/acceptance-worksheets/<version>/<code>/<sha256>.md`，未签工作表是 `<version>/<code>.md`；清单只绑定链头。`service.py` 只把三个既有私有助手公开化（`write_checklist_atomic` / `binding_reasons` / `verify_evidence_reference`），并给 `prepare` 加上只读预检与 `--force` 恢复分支。

**Tech Stack:** Python 3.10 (`py310` conda env)、pydantic v2、typer、pandas、PyYAML、pytest、ruff。

**Spec:** `docs/superpowers/specs/2026-09-13-acceptance-standing-worksheets-design.md`（已提交 `4001523`）。

## Global Constraints

- 密钥只从仓库根的 `.env` 读入（该文件已被 gitignore）。`.env.example` 是模板、值恒为空，**绝不把真实值写进任何被跟踪的文件**。
- 签名与结论**不进入** `summary` / `details`。
- `confirm` 不得新增行、不得改机械项、不得生成 evidence。
- 不提供「清空同版本签署」的入口：任何代码路径都不能删除已签修订或外部输入。
- `confirm` 与 `--supersede` 都不得改写清单顶层 `operator_id`（容器级字段）。
- 不提供批量确认（`--all` / `--mechanisable` / 任何多 code 一次确认）。
- 强制强度只能由程序判定，绝不是入参。
- 每条测试命令只跑**具名测试文件**，不跑 `pytest` 裸命令（全量 integration 约 18.5 分钟）。
- ruff 门禁只要求 `ruff check` 与改动行格式，不要求仓库级 `ruff format --check` 通过。

---

### Task 1: 工作表格式与 marker 校验

**Files:**
- Create: `src/stock_quant/research/acceptance/worksheet.py`
- Test: `tests/unit/test_acceptance_worksheet.py`

**Interfaces:**
- Consumes: 无（本任务只依赖标准库与 PyYAML）
- Produces:
  - 常量 `WORKSHEET_DIRNAME`、`EXTERNAL_INPUT_DIRNAME`、四个 marker 常量、`SIGNATURE_FENCE`、`EXTERNAL_CORROBORATED`、`OPERATOR_ATTESTED`、`CONFIRMATION_STRENGTHS`、`WORKSHEET_ERROR_CATEGORIES`
  - `class WorksheetError(RuntimeError)`，属性 `category: str`
  - `worksheets_root(project_root) -> Path`、`version_dir(project_root, dataset_version) -> Path`、`pending_path(project_root, dataset_version, code) -> Path`、`revisions_dir(project_root, dataset_version, code) -> Path`、`external_inputs_root(project_root) -> Path`
  - `verify_markers(text) -> tuple[str, str]`
  - `render_program(payload) -> str`、`program_payload(program_text) -> dict[str, object]`
  - `append_signature(human, block) -> str`、`signature_blocks(human) -> tuple[dict, ...]`、`last_signature(human) -> dict[str, object]`
  - `render_worksheet(payload, human) -> str`

- [ ] **Step 1: Write the failing test**

创建 `tests/unit/test_acceptance_worksheet.py`：

```python
"""Unit behaviour of the standing acceptance worksheet format (Task 1).

The worksheet is one markdown file per (dataset version, manual check code)
with a machine-written program area and an append-only human area.  A signed
revision is content-addressed by its own file name, so the format has to be
parseable without guessing: the four markers are validated fail-closed and
every signature block is written by ``confirm`` alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_quant.research.acceptance.worksheet import (
    MARKER_HUMAN_BEGIN,
    MARKER_HUMAN_END,
    MARKER_PROGRAM_BEGIN,
    MARKER_PROGRAM_END,
    WorksheetError,
    append_signature,
    last_signature,
    pending_path,
    program_payload,
    render_program,
    render_worksheet,
    verify_markers,
)


def test_layout_puts_pending_and_revisions_in_separate_shapes(tmp_path: Path) -> None:
    root = tmp_path
    assert pending_path(root, "a" * 64, "secret_scan") == (
        root / "data" / "acceptance-worksheets" / ("a" * 64) / "secret_scan.md"
    )


def test_markers_round_trip_a_program_area() -> None:
    payload = {"code": "secret_scan", "dataset_version": "a" * 64}
    text = render_worksheet(render_program(payload), "")
    program, human = verify_markers(text)
    assert program_payload(program) == payload
    assert human.strip() == ""


@pytest.mark.parametrize(
    "mutate",
    [
        lambda text: text.replace(MARKER_PROGRAM_END, ""),
        lambda text: text.replace(MARKER_PROGRAM_BEGIN, MARKER_PROGRAM_BEGIN * 2),
        lambda text: text.replace(MARKER_HUMAN_BEGIN, ""),
        lambda text: text.replace(MARKER_HUMAN_END, ""),
        lambda text: text.replace(MARKER_HUMAN_BEGIN, "<!-- ws:unknown:begin -->"),
    ],
)
def test_marker_violations_fail_closed(mutate) -> None:
    text = render_worksheet(render_program({"code": "secret_scan"}), "")
    with pytest.raises(WorksheetError) as error:
        verify_markers(mutate(text))
    assert error.value.category == "marker_invalid"


def test_reordered_markers_fail_closed() -> None:
    text = (
        f"{MARKER_HUMAN_BEGIN}\nhuman\n{MARKER_HUMAN_END}\n"
        f"{MARKER_PROGRAM_BEGIN}\n```yaml\ncode: x\n```\n{MARKER_PROGRAM_END}\n"
    )
    with pytest.raises(WorksheetError) as error:
        verify_markers(text)
    assert error.value.category == "marker_invalid"


def test_unparseable_program_area_is_named() -> None:
    text = render_worksheet(f"{MARKER_PROGRAM_BEGIN}\nnot yaml\n", "")
    program, _ = verify_markers(text)
    with pytest.raises(WorksheetError) as error:
        program_payload(program)
    assert error.value.category == "program_unreadable"


def test_signature_blocks_append_and_last_one_wins() -> None:
    human = append_signature("", {"operator_id": "a", "decision": "FAIL"})
    human = append_signature(human, {"operator_id": "b", "decision": "PASS"})
    assert last_signature(human) == {"operator_id": "b", "decision": "PASS"}


def test_human_area_without_a_signature_block_is_rejected() -> None:
    with pytest.raises(WorksheetError) as error:
        last_signature("operator typed prose only\n")
    assert error.value.category == "marker_invalid"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n py310 python -m pytest tests/unit/test_acceptance_worksheet.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stock_quant.research.acceptance.worksheet'`

- [ ] **Step 3: Write minimal implementation**

创建 `src/stock_quant/research/acceptance/worksheet.py`：

```python
"""Standing acceptance worksheets: the operator's confirm main line.

Nine manual checks each get one worksheet per dataset version.  ``prepare``
writes an unsigned worksheet (``<version>/<code>.md``) whose program area is
refreshed on every run; ``confirm`` turns it into one immutable *revision*
(``<version>/<code>/<sha256>.md``) that the checklist binds to, and
``confirm --supersede`` appends a further revision that replaces the current
one without touching it.

The layout exists to enforce three rules:

* A signed revision is never overwritten, moved or deleted -- a published
  ``ACCEPTED`` record's evidence reference is a path plus a SHA-256, and
  :func:`stock_quant.research.acceptance.service.verify_acceptance_bindings`
  re-verifies every one of them on every research run.
* The checklist binds the *effective* revision only (the head of the
  ``supersedes`` chain).
* External inputs (official calendar and rule excerpts) are copied into a
  content-addressed, append-only store under the project root, so an
  ``EXTERNAL_CORROBORATED`` strength stays checkable after publication.

The one-line rule for reading the layout: ``<code>.md`` is always unsigned,
``<code>/<hash>.md`` is always signed.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml

__all__ = [
    "CONFIRMATION_STRENGTHS",
    "EXTERNAL_CORROBORATED",
    "EXTERNAL_INPUT_DIRNAME",
    "MARKER_HUMAN_BEGIN",
    "MARKER_HUMAN_END",
    "MARKER_PROGRAM_BEGIN",
    "MARKER_PROGRAM_END",
    "OPERATOR_ATTESTED",
    "SIGNATURE_FENCE",
    "WORKSHEET_DIRNAME",
    "WORKSHEET_ERROR_CATEGORIES",
    "WorksheetError",
    "append_signature",
    "external_inputs_root",
    "last_signature",
    "pending_path",
    "program_payload",
    "render_program",
    "render_worksheet",
    "revisions_dir",
    "signature_blocks",
    "verify_markers",
    "version_dir",
    "worksheets_root",
]

#: Directory (under the project root) holding every version's worksheets.
WORKSHEET_DIRNAME = "acceptance-worksheets"

#: Directory (under the project root) holding the append-only input store.
EXTERNAL_INPUT_DIRNAME = "acceptance-external-inputs"

#: The four markers bounding the program and human areas, in required order.
MARKER_PROGRAM_BEGIN = "<!-- ws:program:begin -->"
MARKER_PROGRAM_END = "<!-- ws:program:end -->"
MARKER_HUMAN_BEGIN = "<!-- ws:human:begin -->"
MARKER_HUMAN_END = "<!-- ws:human:end -->"
_MARKERS_IN_ORDER = (
    MARKER_PROGRAM_BEGIN,
    MARKER_PROGRAM_END,
    MARKER_HUMAN_BEGIN,
    MARKER_HUMAN_END,
)

#: The fenced-block info string every signature block carries.  Deliberately
#: not an HTML marker: the four markers above are the whole marker vocabulary,
#: and an unknown marker is rejected.
SIGNATURE_FENCE = "```ws-signature"

#: The closed set of confirmation strengths, decided by the program.
EXTERNAL_CORROBORATED = "EXTERNAL_CORROBORATED"
OPERATOR_ATTESTED = "OPERATOR_ATTESTED"
CONFIRMATION_STRENGTHS = (EXTERNAL_CORROBORATED, OPERATOR_ATTESTED)

#: The stable failure categories every worksheet operation reports.
WORKSHEET_ERROR_CATEGORIES = (
    "marker_invalid",
    "program_unreadable",
    "signed_worksheets_present",
    "signed_worksheet_drift",
    "already_signed",
    "nothing_to_supersede",
    "superseded_revision_drift",
    "revision_chain_invalid",
    "previous_signed_ambiguous",
    "acknowledgement_required",
    "conclusion_required",
    "external_input_invalid",
    "candidate_evidence_missing",
)


class WorksheetError(RuntimeError):
    """A worksheet operation refused to proceed.

    ``category`` is one of :data:`WORKSHEET_ERROR_CATEGORIES` and is the only
    thing a caller ever prints, so failures stay stable and free of paths.
    """

    def __init__(self, category: str) -> None:
        if category not in WORKSHEET_ERROR_CATEGORIES:
            raise ValueError(f"unknown worksheet error category {category!r}")
        self.category = category
        super().__init__(category)


def worksheets_root(project_root: Path) -> Path:
    """The directory holding every version's worksheets."""
    return Path(project_root) / "data" / WORKSHEET_DIRNAME


def version_dir(project_root: Path, dataset_version: str) -> Path:
    """The directory holding one dataset version's worksheets."""
    return worksheets_root(project_root) / dataset_version


def pending_path(project_root: Path, dataset_version: str, code: str) -> Path:
    """The one unsigned worksheet of one code.  Never cited by a checklist."""
    return version_dir(project_root, dataset_version) / f"{code}.md"


def revisions_dir(project_root: Path, dataset_version: str, code: str) -> Path:
    """The directory holding one code's immutable signed revisions."""
    return version_dir(project_root, dataset_version) / code


def external_inputs_root(project_root: Path) -> Path:
    """The content-addressed, append-only store for external inputs."""
    return Path(project_root) / "data" / EXTERNAL_INPUT_DIRNAME


def verify_markers(text: str) -> tuple[str, str]:
    """Split a worksheet into its program and human areas, or fail closed.

    Every marker must appear exactly once and in the order
    ``program:begin < program:end < human:begin < human:end``.  A missing
    human area is a violation, never something to create by guessing.
    """
    for marker in _MARKERS_IN_ORDER:
        if text.count(marker) != 1:
            raise WorksheetError("marker_invalid")
    positions = [text.index(marker) for marker in _MARKERS_IN_ORDER]
    if positions != sorted(positions):
        raise WorksheetError("marker_invalid")
    program = text[
        positions[0] + len(MARKER_PROGRAM_BEGIN) : positions[1]
    ]
    human = text[positions[2] + len(MARKER_HUMAN_BEGIN) : positions[3]]
    return program, human


def render_program(payload: Mapping[str, object]) -> str:
    """Render one program area: a single fenced YAML block plus its markers."""
    body = yaml.safe_dump(dict(payload), sort_keys=False, allow_unicode=True)
    return (
        f"{MARKER_PROGRAM_BEGIN}\n```yaml\n{body}```\n{MARKER_PROGRAM_END}\n"
    )


def program_payload(program_text: str) -> dict[str, object]:
    """Parse a program area back into its mapping, or fail closed."""
    lines = program_text.strip().splitlines()
    if len(lines) < 2 or lines[0].strip() != "```yaml":
        raise WorksheetError("program_unreadable")
    if lines[-1].strip() != "```":
        raise WorksheetError("program_unreadable")
    try:
        payload = yaml.safe_load("\n".join(lines[1:-1]))
    except yaml.YAMLError as error:
        raise WorksheetError("program_unreadable") from error
    if not isinstance(payload, dict):
        raise WorksheetError("program_unreadable")
    return payload


def append_signature(human: str, block: Mapping[str, object]) -> str:
    """Append one signature block to a human area, appending only."""
    body = yaml.safe_dump(dict(block), sort_keys=False, allow_unicode=True)
    if human and not human.endswith("\n"):
        human += "\n"
    return f"{human}{SIGNATURE_FENCE}\n{body}```\n"


def signature_blocks(human: str) -> tuple[dict[str, object], ...]:
    """Every signature block in a human area, in file order."""
    blocks: list[dict[str, object]] = []
    lines = human.splitlines()
    index = 0
    while index < len(lines):
        if lines[index].strip() == SIGNATURE_FENCE:
            body: list[str] = []
            index += 1
            while index < len(lines) and lines[index].strip() != "```":
                body.append(lines[index])
                index += 1
            if index >= len(lines):
                raise WorksheetError("marker_invalid")
            try:
                payload = yaml.safe_load("\n".join(body))
            except yaml.YAMLError as error:
                raise WorksheetError("marker_invalid") from error
            if not isinstance(payload, dict):
                raise WorksheetError("marker_invalid")
            blocks.append(payload)
        index += 1
    return tuple(blocks)


def last_signature(human: str) -> dict[str, object]:
    """The effective signature of one human area, or fail closed."""
    blocks = signature_blocks(human)
    if not blocks:
        raise WorksheetError("marker_invalid")
    return blocks[-1]


def render_worksheet(payload: Mapping[str, object], human: str) -> str:
    """Render one whole worksheet: program area then human area."""
    if human and not human.endswith("\n"):
        human += "\n"
    return (
        f"{render_program(payload)}\n"
        f"{MARKER_HUMAN_BEGIN}\n{human}{MARKER_HUMAN_END}\n"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n py310 python -m pytest tests/unit/test_acceptance_worksheet.py -q`
Expected: PASS（8 passed）

- [ ] **Step 5: Commit**

```bash
git add src/stock_quant/research/acceptance/worksheet.py tests/unit/test_acceptance_worksheet.py
git commit -m "feat: add the worksheet format and marker validation"
```

---

### Task 2: 修订链、链头与「上一次 PASS」

**Files:**
- Modify: `src/stock_quant/research/acceptance/worksheet.py`（追加）
- Test: `tests/unit/test_acceptance_worksheet.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 marker/渲染函数与 `WorksheetError`
- Produces:
  - `@dataclass(frozen=True) class Revision`，字段 `reference: str`、`sha256: str`、`path: Path`、`payload: Mapping[str, object]`、`human: str`、`decision: str`、`confirmed_at: str`、`operator_id: str`、`supersedes: str | None`
  - `write_revision(project_root, dataset_version, code, payload, human) -> Revision`（写内容寻址的不可变修订）
  - `revision_chain(project_root, dataset_version, code) -> tuple[Revision, ...]`（沿链从旧到新）
  - `effective_revision(project_root, dataset_version, code) -> Revision | None`
  - `previous_pass_revision(project_root, dataset_version, code) -> Revision | None`
  - `latest_pass_revision(project_root, code, *, exclude_version=None) -> Revision | None`（跨版本）
  - `signed_codes(project_root, dataset_version) -> tuple[str, ...]`（只读预检用）

- [ ] **Step 1: Write the failing test**

追加到 `tests/unit/test_acceptance_worksheet.py`：

```python
from stock_quant.research.acceptance.worksheet import (
    effective_revision,
    latest_pass_revision,
    previous_pass_revision,
    revision_chain,
    signed_codes,
    write_revision,
)

_VERSION = "a" * 64
_OTHER_VERSION = "b" * 64


def _revision(
    root: Path,
    *,
    version: str = _VERSION,
    code: str = "secret_scan",
    decision: str = "PASS",
    confirmed_at: str = "2026-09-08T12:00:00+00:00",
    supersedes: str | None = None,
):
    payload = {
        "code": code,
        "dataset_version": version,
        "dataset_manifest_sha256": version,
        "supersedes": supersedes,
    }
    human = append_signature(
        "",
        {
            "operator_id": "operator-a",
            "confirmed_at": confirmed_at,
            "decision": decision,
            "strength": "OPERATOR_ATTESTED",
            "conclusion": "reviewed",
            "supersedes": supersedes,
        },
    )
    return write_revision(root, version, code, payload, human)


def test_revision_file_name_is_its_own_sha256(tmp_path: Path) -> None:
    revision = _revision(tmp_path)
    assert revision.path.name == f"{revision.sha256}.md"
    assert revision.path.stem == revision.sha256


def test_writing_the_same_revision_twice_is_idempotent(tmp_path: Path) -> None:
    first = _revision(tmp_path)
    second = _revision(tmp_path)
    assert first.reference == second.reference
    assert len(list((tmp_path / "data" / "acceptance-worksheets" / _VERSION / "secret_scan").iterdir())) == 1


def test_chain_head_is_the_only_revision_nobody_supersedes(tmp_path: Path) -> None:
    first = _revision(tmp_path)
    second = _revision(tmp_path, supersedes=first.reference, decision="FAIL")
    chain = revision_chain(tmp_path, _VERSION, "secret_scan")
    assert [row.reference for row in chain] == [first.reference, second.reference]
    assert effective_revision(tmp_path, _VERSION, "secret_scan").reference == (
        second.reference
    )
    assert previous_pass_revision(tmp_path, _VERSION, "secret_scan").reference == (
        first.reference
    )


def test_a_forked_chain_is_rejected(tmp_path: Path) -> None:
    first = _revision(tmp_path)
    _revision(tmp_path, supersedes=first.reference, decision="FAIL")
    _revision(tmp_path, supersedes=first.reference, confirmed_at="2026-09-09T12:00:00+00:00")
    with pytest.raises(WorksheetError) as error:
        revision_chain(tmp_path, _VERSION, "secret_scan")
    assert error.value.category == "revision_chain_invalid"


def test_a_supersedes_target_that_is_absent_is_rejected(tmp_path: Path) -> None:
    _revision(tmp_path, supersedes=f"data/acceptance-worksheets/{_VERSION}/secret_scan/{'c' * 64}.md")
    with pytest.raises(WorksheetError) as error:
        revision_chain(tmp_path, _VERSION, "secret_scan")
    assert error.value.category == "revision_chain_invalid"


def test_a_tampered_revision_file_is_rejected(tmp_path: Path) -> None:
    revision = _revision(tmp_path)
    revision.path.write_text(
        revision.path.read_text(encoding="utf-8") + "\nstray\n", encoding="utf-8"
    )
    with pytest.raises(WorksheetError) as error:
        revision_chain(tmp_path, _VERSION, "secret_scan")
    assert error.value.category == "revision_chain_invalid"


def test_an_unexpected_file_in_the_revision_directory_is_rejected(tmp_path: Path) -> None:
    _revision(tmp_path)
    stray = tmp_path / "data" / "acceptance-worksheets" / _VERSION / "secret_scan" / "notes.md"
    stray.write_text("operator notes\n", encoding="utf-8")
    with pytest.raises(WorksheetError) as error:
        revision_chain(tmp_path, _VERSION, "secret_scan")
    assert error.value.category == "revision_chain_invalid"


def test_latest_pass_revision_spans_versions_and_refuses_ties(tmp_path: Path) -> None:
    _revision(tmp_path, version=_OTHER_VERSION, confirmed_at="2026-09-01T12:00:00+00:00")
    newest = _revision(tmp_path, confirmed_at="2026-09-08T12:00:00+00:00")
    found = latest_pass_revision(tmp_path, "secret_scan", exclude_version=_VERSION)
    assert found.reference != newest.reference
    _revision(tmp_path, version="d" * 64)
    with pytest.raises(WorksheetError) as error:
        latest_pass_revision(tmp_path, "secret_scan", exclude_version=_VERSION)
    assert error.value.category == "previous_signed_ambiguous"


def test_signed_codes_reports_only_chains_with_a_head(tmp_path: Path) -> None:
    assert signed_codes(tmp_path, _VERSION) == ()
    _revision(tmp_path)
    assert signed_codes(tmp_path, _VERSION) == ("secret_scan",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n py310 python -m pytest tests/unit/test_acceptance_worksheet.py -q`
Expected: FAIL — `ImportError: cannot import name 'effective_revision'`

- [ ] **Step 3: Write minimal implementation**

追加到 `src/stock_quant/research/acceptance/worksheet.py`（把新名字补进 `__all__`）：

```python
import hashlib
import os
from dataclasses import dataclass
from uuid import uuid4

from stock_quant.research.acceptance.models import MANUAL_CHECK_CODES

#: The two decisions a signature block may carry.
_SIGNED_DECISIONS = ("PASS", "FAIL")


@dataclass(frozen=True)
class Revision:
    """One immutable signed worksheet revision."""

    reference: str
    sha256: str
    path: Path
    payload: Mapping[str, object]
    human: str
    decision: str
    confirmed_at: str
    operator_id: str
    supersedes: str | None


def _sha256_file(path: Path) -> str:
    """The lowercase hex SHA-256 of one file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _revision_from_path(project_root: Path, path: Path) -> Revision:
    """Parse one stored revision, enforcing its content-addressed name.

    A file whose bytes no longer hash to its own name can never be a
    baseline: its ``sha256`` is the identity every checklist cites.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise WorksheetError("revision_chain_invalid") from error
    program, human = verify_markers(text)
    payload = program_payload(program)
    signature = last_signature(human)
    digest = _sha256_file(path)
    if path.stem != digest:
        raise WorksheetError("revision_chain_invalid")
    decision = str(signature.get("decision", ""))
    if decision not in _SIGNED_DECISIONS:
        raise WorksheetError("revision_chain_invalid")
    supersedes = payload.get("supersedes")
    return Revision(
        reference=path.relative_to(Path(project_root).resolve()).as_posix(),
        sha256=digest,
        path=path,
        payload=payload,
        human=human,
        decision=decision,
        confirmed_at=str(signature.get("confirmed_at", "")),
        operator_id=str(signature.get("operator_id", "")),
        supersedes=(
            str(supersedes["reference"])
            if isinstance(supersedes, dict) and "reference" in supersedes
            else None
        ),
    )


def write_revision(
    project_root: Path,
    dataset_version: str,
    code: str,
    payload: Mapping[str, object],
    human: str,
) -> Revision:
    """Write one immutable revision under its own SHA-256, or reuse it.

    The file is written to a temporary name, hashed, and then renamed to its
    digest, so a crash never leaves a file whose name disagrees with its
    bytes.  An already-present revision is left untouched: revisions are only
    ever appended.
    """
    root = Path(project_root).resolve()
    directory = revisions_dir(root, dataset_version, code)
    directory.mkdir(parents=True, exist_ok=True)
    if payload.get("code") != code:
        raise WorksheetError("revision_chain_invalid")
    text = render_worksheet(payload, human)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    destination = directory / f"{digest}.md"
    if not destination.is_file():
        staging = directory / f".{digest}.{uuid4().hex}.tmp"
        try:
            staging.write_text(text, encoding="utf-8")
            os.replace(staging, destination)
        except OSError as error:
            staging.unlink(missing_ok=True)
            raise WorksheetError("revision_chain_invalid") from error
    return _revision_from_path(root, destination)


def revision_chain(
    project_root: Path, dataset_version: str, code: str
) -> tuple[Revision, ...]:
    """Every signed revision of one code, oldest first along ``supersedes``.

    Fails closed on anything the chain cannot explain: an unparseable or
    renamed file, a ``supersedes`` target that is missing, a fork (two heads)
    or a cycle.  There is no "best effort" head.
    """
    root = Path(project_root).resolve()
    directory = revisions_dir(root, dataset_version, code)
    if not directory.is_dir():
        return ()
    revisions: dict[str, Revision] = {}
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix != ".md":
            raise WorksheetError("revision_chain_invalid")
        revision = _revision_from_path(root, path)
        revisions[revision.reference] = revision
    if not revisions:
        return ()
    cited = {
        revision.supersedes
        for revision in revisions.values()
        if revision.supersedes is not None
    }
    if not cited.issubset(revisions):
        raise WorksheetError("revision_chain_invalid")
    heads = [
        revision
        for revision in revisions.values()
        if revision.reference not in cited
    ]
    if len(heads) != 1:
        raise WorksheetError("revision_chain_invalid")
    chain: list[Revision] = []
    seen: set[str] = set()
    node: Revision | None = heads[0]
    while node is not None:
        if node.reference in seen:
            raise WorksheetError("revision_chain_invalid")
        seen.add(node.reference)
        chain.append(node)
        node = revisions.get(node.supersedes) if node.supersedes else None
    if len(chain) != len(revisions):
        raise WorksheetError("revision_chain_invalid")
    return tuple(reversed(chain))


def effective_revision(
    project_root: Path, dataset_version: str, code: str
) -> Revision | None:
    """The head revision a checklist binds, or ``None`` when unsigned."""
    chain = revision_chain(project_root, dataset_version, code)
    return chain[-1] if chain else None


def previous_pass_revision(
    project_root: Path, dataset_version: str, code: str
) -> Revision | None:
    """The nearest ``PASS`` ancestor of the head revision, or ``None``.

    ``FAIL`` is a historical clue, not a carry-forward baseline: reading it as
    "the operator signed this off" would turn a rejection into a confirmation.
    """
    chain = revision_chain(project_root, dataset_version, code)
    for revision in reversed(chain):
        if revision.decision == "PASS":
            return revision
    return None


def latest_pass_revision(
    project_root: Path,
    code: str,
    *,
    exclude_version: str | None = None,
) -> Revision | None:
    """The most recently confirmed ``PASS`` revision of one code, cross-version.

    Dataset versions are content hashes, so directory names carry no time
    order; ``confirmed_at`` (written by ``confirm``, not typed by hand) is the
    only cross-version ordering available.  Ties fail closed rather than
    guess.  A wrong pick only enlarges the review queue, never shrinks it.
    """
    root = Path(project_root).resolve()
    directory = worksheets_root(root)
    if not directory.is_dir():
        return None
    candidates: list[Revision] = []
    for child in sorted(directory.iterdir()):
        if not child.is_dir() or child.name == exclude_version:
            continue
        revision = previous_pass_revision(root, child.name, code)
        if revision is not None:
            candidates.append(revision)
    if not candidates:
        return None
    newest = max(revision.confirmed_at for revision in candidates)
    newest_set = [
        revision for revision in candidates if revision.confirmed_at == newest
    ]
    if len(newest_set) != 1:
        raise WorksheetError("previous_signed_ambiguous")
    return newest_set[0]


def signed_codes(project_root: Path, dataset_version: str) -> tuple[str, ...]:
    """Every manual code this version already has an effective revision for."""
    return tuple(
        code
        for code in MANUAL_CHECK_CODES
        if effective_revision(project_root, dataset_version, code) is not None
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n py310 python -m pytest tests/unit/test_acceptance_worksheet.py -q`
Expected: PASS（17 passed）

- [ ] **Step 5: Commit**

```bash
git add src/stock_quant/research/acceptance/worksheet.py tests/unit/test_acceptance_worksheet.py
git commit -m "feat: resolve worksheets through an append-only revision chain"
```

---

### Task 3: 内容寻址的外部输入仓

**Files:**
- Create: `src/stock_quant/research/acceptance/external_inputs.py`
- Test: `tests/unit/test_acceptance_external_inputs.py`

**Interfaces:**
- Consumes: Task 1 的 `EXTERNAL_INPUT_DIRNAME`、`WorksheetError`
- Produces:
  - `@dataclass(frozen=True) class StoredBlob`，字段 `reference: str`、`sha256: str`、`name: str`
  - `store_blob(project_root, data: bytes, name: str) -> StoredBlob`
  - `load_blob(project_root, reference: str) -> bytes`

- [ ] **Step 1: Write the failing test**

创建 `tests/unit/test_acceptance_external_inputs.py`：

```python
"""Unit behaviour of the content-addressed external input store (Task 3).

External inputs are the official excerpts an operator supplies.  They are
copied into the project under ``data/acceptance-external-inputs/<sha256>/``
and never overwritten, because a published ``ACCEPTED`` record cites them by
path and hash: a replaced excerpt must be detected, not silently accepted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_quant.research.acceptance.external_inputs import load_blob, store_blob
from stock_quant.research.acceptance.worksheet import WorksheetError


def test_blob_lands_under_its_own_sha256(tmp_path: Path) -> None:
    stored = store_blob(tmp_path, b"2021-11-01\n", "official_calendar.txt")
    assert stored.sha256 == (
        "8e0e7f8d3b1f1f2b0e2e0b3b8e3a3c0e3f0e3b1b8e3a3c0e3f0e3b1b8e3a3c0e"
    )
    assert stored.reference == (
        f"data/acceptance-external-inputs/{stored.sha256}/official_calendar.txt"
    )
    assert (tmp_path / stored.reference).read_bytes() == b"2021-11-01\n"


def test_the_same_content_is_stored_once_whatever_the_name(tmp_path: Path) -> None:
    first = store_blob(tmp_path, b"same\n", "a.txt")
    second = store_blob(tmp_path, b"same\n", "b.txt")
    assert first.reference == second.reference
    directory = tmp_path / "data" / "acceptance-external-inputs" / first.sha256
    assert len(list(directory.iterdir())) == 1


def test_different_content_never_overwrites(tmp_path: Path) -> None:
    first = store_blob(tmp_path, b"one\n", "official.txt")
    second = store_blob(tmp_path, b"two\n", "official.txt")
    assert first.reference != second.reference
    assert (tmp_path / first.reference).read_bytes() == b"one\n"
    assert (tmp_path / second.reference).read_bytes() == b"two\n"


def test_an_unusable_name_falls_back_to_a_stable_one(tmp_path: Path) -> None:
    assert store_blob(tmp_path, b"x\n", "").name == "external-input"
    assert store_blob(tmp_path, b"y\n", ".hidden").name == "external-input"


def test_load_blob_reads_back_what_was_stored(tmp_path: Path) -> None:
    stored = store_blob(tmp_path, b"payload\n", "official.txt")
    assert load_blob(tmp_path, stored.reference) == b"payload\n"


def test_load_blob_refuses_to_leave_the_project(tmp_path: Path) -> None:
    with pytest.raises(WorksheetError) as error:
        load_blob(tmp_path, "../outside.txt")
    assert error.value.category == "external_input_invalid"
```

注意：Step 1 里第一个测试的十六进制常量在 Step 2 会真实失败（值是我按格式占位写的）。把该断言改成按内容自算：

```python
    expected = hashlib.sha256(b"2021-11-01\n").hexdigest()
    assert stored.sha256 == expected
```

（`import hashlib` 加到文件头。）

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n py310 python -m pytest tests/unit/test_acceptance_external_inputs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stock_quant.research.acceptance.external_inputs'`

- [ ] **Step 3: Write minimal implementation**

创建 `src/stock_quant/research/acceptance/external_inputs.py`：

```python
"""External inputs: the operator-supplied official evidence an operator attests.

Three of the nine manual checks cannot be evidenced by this project alone (an
official exchange calendar, a second price source, official trading-rule
effective dates).  Whatever the operator supplies is copied into a
content-addressed, append-only store under the project root rather than
merely pointed at: a published record only pins the worksheet's hash, so an
excerpt deleted or swapped afterwards would leave an
``EXTERNAL_CORROBORATED`` claim nobody could re-check.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from stock_quant.research.acceptance.worksheet import (
    WorksheetError,
    external_inputs_root,
)

__all__ = ["StoredBlob", "load_blob", "store_blob"]


@dataclass(frozen=True)
class StoredBlob:
    """One stored external input: its project-relative path and its hash."""

    reference: str
    sha256: str
    name: str


def _safe_name(name: str) -> str:
    """A file name fit for the store, or a stable fallback."""
    candidate = Path(name).name
    if not candidate or candidate.startswith(".") or "\x00" in candidate:
        return "external-input"
    return candidate


def store_blob(project_root: Path, data: bytes, name: str) -> StoredBlob:
    """Copy bytes into the store under their SHA-256 and return the pointer.

    Identical content is stored exactly once, whatever name it arrives under;
    different content lands under a different digest and never overwrites.
    """
    root = Path(project_root).resolve()
    digest = hashlib.sha256(data).hexdigest()
    directory = external_inputs_root(root) / digest
    existing = sorted(entry for entry in directory.iterdir()) if directory.is_dir() else []
    if existing:
        chosen = existing[0]
    else:
        directory.parent.mkdir(parents=True, exist_ok=True)
        staging = directory.parent / f".{digest}.{uuid4().hex}.tmp"
        try:
            staging.mkdir()
            (staging / _safe_name(name)).write_bytes(data)
            if directory.exists():
                # A concurrent writer won the rename: reuse its copy.
                shutil.rmtree(staging, ignore_errors=True)
                chosen = sorted(directory.iterdir())[0]
            else:
                os.replace(staging, directory)
                chosen = directory / _safe_name(name)
        except OSError as error:
            shutil.rmtree(staging, ignore_errors=True)
            raise WorksheetError("external_input_invalid") from error
    return StoredBlob(
        reference=chosen.relative_to(root).as_posix(),
        sha256=digest,
        name=chosen.name,
    )


def load_blob(project_root: Path, reference: str) -> bytes:
    """Read one stored blob back, refusing any path outside the project root."""
    root = Path(project_root).resolve()
    try:
        candidate = (root / reference).resolve()
    except ValueError as error:
        raise WorksheetError("external_input_invalid") from error
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise WorksheetError("external_input_invalid")
    return candidate.read_bytes()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n py310 python -m pytest tests/unit/test_acceptance_external_inputs.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add src/stock_quant/research/acceptance/external_inputs.py tests/unit/test_acceptance_external_inputs.py
git commit -m "feat: store external acceptance inputs content-addressed"
```

---

### Task 4: 三个比对器（日历 / 交易规则 / 跨源价格）

**Files:**
- Modify: `src/stock_quant/research/acceptance/external_inputs.py`（追加）
- Test: `tests/unit/test_acceptance_external_inputs.py`（追加）

**Interfaces:**
- Consumes: Task 3 的 `WorksheetError`；`stock_quant.data_quality.compare.ComparisonThresholds`、`compare_daily_sources`；`stock_quant.data_quality.models.Severity`
- Produces:
  - `@dataclass(frozen=True) class CalendarExcerpt`，字段 `open_days: frozenset[date]`、`closed_days: frozenset[date]`、`has_close_column: bool`
  - `parse_calendar_excerpt(data: bytes) -> CalendarExcerpt`
  - `@dataclass(frozen=True) class CalendarComparison`，字段 `status: str`、`has_close_column: bool`、`dataset_open_official_absent: tuple[str, ...]`、`official_open_dataset_absent: tuple[str, ...]`、`official_closed_dataset_open: tuple[str, ...]`，属性 `differences: tuple[str, ...]`、`corroborated: bool`、`queue_rows: tuple[str, ...]`
  - `compare_calendar(dataset_open_days, excerpt) -> CalendarComparison`
  - `@dataclass(frozen=True) class RuleRow`，字段 `board: str`、`status: str`、`effective_from: str`、`rate: str`
  - `rule_rows(config_path: Path) -> tuple[RuleRow, ...]`
  - `parse_rule_excerpt(data: bytes) -> tuple[RuleRow, ...]`
  - `@dataclass(frozen=True) class RuleComparison`，字段 `status: str`、`uncovered: tuple[str, ...]`、`conflicting: tuple[str, ...]`、`unclaimed: tuple[str, ...]`，属性 `corroborated: bool`、`queue_rows: tuple[str, ...]`
  - `compare_trading_rules(rows, excerpt) -> RuleComparison`
  - `@dataclass(frozen=True) class PriceComparison`，字段 `status: str`、`reason: str`、`sources: tuple[str, ...]`、`rows_compared: int`、`exceeding: tuple[str, ...]`
  - `price_sample_frame(daily, open_days) -> pd.DataFrame`
  - `compare_price_sources(sample) -> PriceComparison`
  - `@dataclass(frozen=True) class VersionFacts`，字段 `open_days: tuple[date, ...]`、`rules: tuple[RuleRow, ...]`、`price_sample: pd.DataFrame`
  - `version_facts(project_root, dataset_version, start, end) -> VersionFacts`
  - `compare_for_code(code, facts, data) -> CalendarComparison | RuleComparison | PriceComparison`（`data: bytes | None`）

- [ ] **Step 1: Write the failing test**

追加到 `tests/unit/test_acceptance_external_inputs.py`：

```python
from datetime import date

import pandas as pd

from stock_quant.research.acceptance.external_inputs import (
    PriceComparison,
    VersionFacts,
    compare_calendar,
    compare_for_code,
    compare_price_sources,
    compare_trading_rules,
    parse_calendar_excerpt,
    parse_rule_excerpt,
    price_sample_frame,
    rule_rows,
)
from stock_quant.research.acceptance.worksheet import WorksheetError

_RULE_CONFIG = (
    Path(__file__).resolve().parents[2]
    / "templates"
    / "project-config"
    / "trading_rules.yml"
)


def _excerpt_text(*rows: str) -> bytes:
    header = "board,status,effective_from,rate,source_url"
    return ("\n".join((header, *rows)) + "\n").encode("utf-8")


def test_a_single_column_calendar_excerpt_cannot_corroborate() -> None:
    excerpt = parse_calendar_excerpt(b"# official\n2021-11-01\n2021-11-02\n")
    assert excerpt.has_close_column is False
    compared = compare_calendar(
        [date(2021, 11, 1), date(2021, 11, 3)], excerpt
    )
    assert compared.status == "compared"
    assert compared.dataset_open_official_absent == ("2021-11-03",)
    assert compared.official_open_dataset_absent == ("2021-11-02",)
    assert compared.corroborated is False


def test_a_two_column_calendar_excerpt_corroborates_only_when_identical() -> None:
    excerpt = parse_calendar_excerpt(b"2021-11-01 1\n2021-11-02 0\n")
    assert excerpt.has_close_column is True
    exact = compare_calendar([date(2021, 11, 1)], excerpt)
    assert exact.official_closed_dataset_open == ()
    assert exact.corroborated is True
    conflicting = compare_calendar(
        [date(2021, 11, 1), date(2021, 11, 2)], excerpt
    )
    assert conflicting.official_closed_dataset_open == ("2021-11-02",)
    assert conflicting.corroborated is False


def test_a_malformed_calendar_excerpt_is_rejected() -> None:
    with pytest.raises(WorksheetError) as error:
        parse_calendar_excerpt(b"2021-11-01 maybe\n")
    assert error.value.category == "external_input_invalid"


def test_trading_rules_corroborate_only_when_every_row_is_covered() -> None:
    rows = rule_rows(_RULE_CONFIG)
    assert rows, "the shipped trading rules config declares price limits"
    excerpt = parse_rule_excerpt(
        _excerpt_text(
            *(
                f"{row.board},{row.status},{row.effective_from},{row.rate},"
                "https://example.invalid/official"
                for row in rows
            )
        )
    )
    complete = compare_trading_rules(rows, excerpt)
    assert complete.status == "compared"
    assert complete.corroborated is True

    partial = parse_rule_excerpt(
        _excerpt_text(
            f"{rows[0].board},{rows[0].status},{rows[0].effective_from},"
            "0.99,https://example.invalid/official"
        )
    )
    incomplete = compare_trading_rules(rows, partial)
    assert incomplete.conflicting == (f"{rows[0].board}|{rows[0].status}|"
                                      f"{rows[0].effective_from}",)
    assert incomplete.uncovered
    assert incomplete.corroborated is False


def test_rule_rates_compare_numerically_not_textually() -> None:
    rows = (RuleRow("star", "NORMAL", "2019-07-22", "0.10"),)
    excerpt = parse_rule_excerpt(
        _excerpt_text("star,NORMAL,2019-07-22,0.1,https://example.invalid")
    )
    assert compare_trading_rules(rows, excerpt).corroborated is True


def test_a_malformed_rule_excerpt_is_rejected() -> None:
    with pytest.raises(WorksheetError) as error:
        parse_rule_excerpt(b"board,status\nstar,NORMAL\n")
    assert error.value.category == "external_input_invalid"


def _price_frame(sources: tuple[str, ...]) -> pd.DataFrame:
    records = []
    for source in sources:
        close = 10.0 if source == "primary" else 10.5
        records.append(
            {
                "trade_date": date(2021, 11, 1),
                "symbol": "600000.SH",
                "source": source,
                "adjustment": "unadjusted",
                "volume_unit": "share",
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": close,
            }
        )
    return pd.DataFrame(records)


def test_price_sample_takes_the_window_edges() -> None:
    daily = pd.DataFrame(
        {
            "trade_date": [date(2021, 11, 1), date(2021, 11, 2), date(2021, 11, 3)],
            "symbol": ["600000.SH"] * 3,
        }
    )
    sample = price_sample_frame(daily, [date(2021, 11, 1), date(2021, 11, 2), date(2021, 11, 3)])
    assert sorted(sample["trade_date"]) == [date(2021, 11, 1), date(2021, 11, 3)]


def test_a_single_price_source_refuses_to_compare() -> None:
    compared = compare_price_sources(_price_frame(("primary",)))
    assert compared.status == "not_comparable"
    assert compared.reason == "single_price_source"
    assert compared.sources == ("primary",)


def test_two_sources_are_compared_under_the_design_thresholds() -> None:
    within = compare_price_sources(_price_frame(("primary", "secondary")))
    assert within.status == "compared"
    assert within.exceeding == ()
    assert within.rows_compared == 1

    disagreeing = _price_frame(("primary", "secondary"))
    disagreeing.loc[1, "close"] = 11.0
    exceeding = compare_price_sources(disagreeing)
    assert exceeding.exceeding == ("600000.SH@2021-11-01",)


def test_sources_that_never_share_a_bar_are_not_comparable() -> None:
    """Two sources dividing the symbol space must not read as a clean pass.

    The shape this guards is the trusted fixture's: equities carried by one
    source and benchmarks by another, so no ``(symbol, trade_date)`` has two
    readings.  ``compared`` with ``rows_compared=0`` would claim the cross
    check ran and found nothing.
    """
    frame = pd.concat(
        [
            _price_frame(("primary",)).assign(symbol="600000.SH"),
            _price_frame(("secondary",)).assign(symbol="000300.SH"),
        ],
        ignore_index=True,
    )
    compared = compare_price_sources(frame)
    assert compared.status == "not_comparable"
    assert compared.reason == "no_paired_bars"
    assert compared.sources == ("primary", "secondary")
    assert compared.rows_compared == 0
    assert compared.exceeding == ()


def test_compare_for_code_dispatches_on_the_code() -> None:
    """One entry point, so prepare and confirm cannot build two comparisons."""
    facts = VersionFacts(
        open_days=(date(2021, 11, 1),),
        rules=rule_rows(_RULE_CONFIG),
        price_sample=_price_frame(("primary", "secondary")),
    )
    assert (
        compare_for_code("exchange_calendar_sample", facts, None).status
        == "no_external_input"
    )
    assert (
        compare_for_code("trading_rule_effective_dates", facts, None).status
        == "no_external_input"
    )
    # The price comparison takes no excerpt at all: it is always the version's
    # own cross-source sample, so ``data`` is ignored for that code.
    price = compare_for_code("cross_source_price_sample", facts, None)
    assert isinstance(price, PriceComparison)
    assert price.status == "compared"
    assert price.rows_compared == 1
    assert compare_for_code(
        "exchange_calendar_sample", facts, b"2021-11-01 1\n"
    ).corroborated is True
    with pytest.raises(WorksheetError) as error:
        compare_for_code("secret_scan", facts, None)
    assert error.value.category == "unknown_check_code"
```

（把 `RuleRow` 加进 `external_inputs` 的导入清单；`WorksheetError`、`pytest`、`Path`、`date`、`pd` 若 Task 3 的测试已导入则不要重复导入。）

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n py310 python -m pytest tests/unit/test_acceptance_external_inputs.py -q`
Expected: FAIL — `ImportError: cannot import name 'compare_calendar'`

- [ ] **Step 3: Write minimal implementation**

追加到 `src/stock_quant/research/acceptance/external_inputs.py`（把 `CalendarComparison`、`CalendarExcerpt`、`EXCERPT_CODES`、`PriceComparison`、`RuleComparison`、`RuleRow`、`VersionFacts`、`compare_calendar`、`compare_for_code`、`compare_price_sources`、`compare_trading_rules`、`parse_calendar_excerpt`、`parse_rule_excerpt`、`price_sample_frame`、`rule_rows`、`version_facts` 补进 `__all__`）：

```python
import csv
import io
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

from stock_quant.data_quality.compare import (
    DEFAULT_THRESHOLDS,
    ComparisonThresholds,
    compare_daily_sources,
)
from stock_quant.data_quality.models import Severity

#: The two operator-only codes that consume an operator-supplied excerpt.
EXCERPT_CODES = ("exchange_calendar_sample", "trading_rule_effective_dates")


@dataclass(frozen=True)
class CalendarExcerpt:
    """One parsed official calendar excerpt."""

    open_days: frozenset[date]
    closed_days: frozenset[date]
    has_close_column: bool


def parse_calendar_excerpt(data: bytes) -> CalendarExcerpt:
    """Parse the official calendar format ``bootstrap_seed --calendar-csv`` uses.

    One ISO date per line, ``#`` comments, the first token being the date.  A
    second token ``1|0`` marks open/closed; without it the excerpt cannot tell
    "officially closed" from "the operator forgot to list the day", which is
    exactly why the strength then stays ``OPERATOR_ATTESTED``.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorksheetError("external_input_invalid") from error
    open_days: set[date] = set()
    closed_days: set[date] = set()
    has_close_column = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens = stripped.split()
        try:
            day = date.fromisoformat(tokens[0])
        except ValueError as error:
            raise WorksheetError("external_input_invalid") from error
        if len(tokens) >= 2:
            if tokens[1] not in ("0", "1"):
                raise WorksheetError("external_input_invalid")
            has_close_column = True
            (open_days if tokens[1] == "1" else closed_days).add(day)
        else:
            open_days.add(day)
    return CalendarExcerpt(
        open_days=frozenset(open_days),
        closed_days=frozenset(closed_days),
        has_close_column=has_close_column,
    )


@dataclass(frozen=True)
class CalendarComparison:
    """The two-way difference between the dataset calendar and the excerpt."""

    status: str
    has_close_column: bool
    dataset_open_official_absent: tuple[str, ...]
    official_open_dataset_absent: tuple[str, ...]
    official_closed_dataset_open: tuple[str, ...]

    @property
    def differences(self) -> tuple[str, ...]:
        """Every difference, in fixed column order."""
        return (
            *self.dataset_open_official_absent,
            *self.official_open_dataset_absent,
            *self.official_closed_dataset_open,
        )

    @property
    def corroborated(self) -> bool:
        """True only for a two-column excerpt with no difference at all."""
        return (
            self.status == "compared"
            and self.has_close_column
            and not self.differences
        )

    @property
    def queue_rows(self) -> tuple[str, ...]:
        """The differences an operator must look at before signing over them.

        An excerpt may legitimately cover only part of the window, so a
        difference is not an error by itself -- but it is exactly the thing the
        operator is signing about, so it enters the queue and has to be
        acknowledged one by one.
        """
        return self.differences


def compare_calendar(
    dataset_open_days: Sequence[date], excerpt: CalendarExcerpt | None
) -> CalendarComparison:
    """Compare the version's open days with the official excerpt, both ways."""
    if excerpt is None:
        return CalendarComparison("no_external_input", False, (), (), ())
    window = frozenset(dataset_open_days)
    listed = excerpt.open_days | excerpt.closed_days
    return CalendarComparison(
        status="compared",
        has_close_column=excerpt.has_close_column,
        dataset_open_official_absent=tuple(
            sorted(day.isoformat() for day in window if day not in listed)
        ),
        official_open_dataset_absent=tuple(
            sorted(
                day.isoformat()
                for day in excerpt.open_days
                if day not in window
            )
        ),
        official_closed_dataset_open=tuple(
            sorted(
                day.isoformat()
                for day in excerpt.closed_days
                if day in window
            )
        ),
    )


@dataclass(frozen=True)
class RuleRow:
    """One declared or official price-limit row."""

    board: str
    status: str
    effective_from: str
    rate: str


def _rule_key(row: RuleRow) -> str:
    return f"{row.board}|{row.status}|{row.effective_from}"


def _rate(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise WorksheetError("external_input_invalid") from error


def rule_rows(config_path: Path) -> tuple[RuleRow, ...]:
    """Every declared ``price_limits`` row, one per (board, status) pair."""
    try:
        payload = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise WorksheetError("external_input_invalid") from error
    limits = payload.get("price_limits") if isinstance(payload, dict) else None
    if not isinstance(limits, list):
        raise WorksheetError("external_input_invalid")
    rows: list[RuleRow] = []
    for entry in limits:
        if not isinstance(entry, dict):
            raise WorksheetError("external_input_invalid")
        boards = entry.get("boards")
        statuses = entry.get("status")
        boards = boards if isinstance(boards, list) else [boards]
        statuses = statuses if isinstance(statuses, list) else [statuses]
        for board in boards:
            for status in statuses:
                rows.append(
                    RuleRow(
                        board=str(board),
                        status=str(status),
                        effective_from=str(entry.get("effective_from")),
                        rate=str(entry.get("rate")),
                    )
                )
    return tuple(sorted(rows, key=_rule_key))


_RULE_EXCERPT_COLUMNS = (
    "board",
    "status",
    "effective_from",
    "rate",
    "source_url",
)


def parse_rule_excerpt(data: bytes) -> tuple[RuleRow, ...]:
    """Parse the official rule excerpt CSV the operator supplies."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorksheetError("external_input_invalid") from error
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or tuple(reader.fieldnames) != (
        _RULE_EXCERPT_COLUMNS
    ):
        raise WorksheetError("external_input_invalid")
    rows: list[RuleRow] = []
    for record in reader:
        try:
            rows.append(
                RuleRow(
                    board=record["board"].strip(),
                    status=record["status"].strip(),
                    effective_from=record["effective_from"].strip(),
                    rate=record["rate"].strip(),
                )
            )
        except (AttributeError, KeyError) as error:
            raise WorksheetError("external_input_invalid") from error
    if not rows:
        raise WorksheetError("external_input_invalid")
    return tuple(rows)


@dataclass(frozen=True)
class RuleComparison:
    """How far an official rule excerpt covers the declared configuration."""

    status: str
    uncovered: tuple[str, ...]
    conflicting: tuple[str, ...]
    unclaimed: tuple[str, ...]

    @property
    def corroborated(self) -> bool:
        """True only when every declared row is covered and none disagrees."""
        return (
            self.status == "compared"
            and not self.uncovered
            and not self.conflicting
        )

    @property
    def queue_rows(self) -> tuple[str, ...]:
        """The declared rows an operator must look at before signing."""
        return tuple(sorted({*self.uncovered, *self.conflicting}))


def compare_trading_rules(
    declared: Sequence[RuleRow], excerpt: tuple[RuleRow, ...] | None
) -> RuleComparison:
    """Compare declared price limits against the official excerpt row by row."""
    if excerpt is None:
        return RuleComparison("no_external_input", (), (), ())
    official = {_rule_key(row): row for row in excerpt}
    uncovered: list[str] = []
    conflicting: list[str] = []
    for row in declared:
        key = _rule_key(row)
        found = official.get(key)
        if found is None:
            uncovered.append(key)
        elif _rate(found.rate) != _rate(row.rate):
            conflicting.append(key)
    declared_keys = {_rule_key(row) for row in declared}
    unclaimed = tuple(
        sorted(key for key in official if key not in declared_keys)
    )
    return RuleComparison(
        status="compared",
        uncovered=tuple(sorted(uncovered)),
        conflicting=tuple(sorted(conflicting)),
        unclaimed=unclaimed,
    )


@dataclass(frozen=True)
class PriceComparison:
    """The cross-source verdict over the version's price sample."""

    status: str
    reason: str
    sources: tuple[str, ...]
    rows_compared: int
    exceeding: tuple[str, ...]


def price_sample_frame(
    daily: pd.DataFrame, open_days: Sequence[date]
) -> pd.DataFrame:
    """The deterministic price sample: every bar on the window's edge days.

    Bounded by ``2 x symbols`` regardless of window length, and stable for a
    given version, so the same sample is reviewed every time.
    """
    days = sorted(set(open_days))
    if not days:
        return daily.iloc[0:0]
    edges = {days[0], days[-1]}
    wanted = daily[daily["trade_date"].isin(edges)]
    return wanted.sort_values(["trade_date", "symbol", "source"], kind="stable")


def compare_price_sources(
    sample: pd.DataFrame,
    thresholds: ComparisonThresholds = DEFAULT_THRESHOLDS,
) -> PriceComparison:
    """Compare every sampled security-date across independent daily sources.

    Reuses the design-spec §13.4 thresholds.  Two ways to be incomparable, and
    both say so with a stable reason instead of reporting a vacuous pass: fewer
    than two independent daily sources in the version, or two sources that
    never cover the same ``(symbol, trade_date)`` -- the normal shape of a
    fixture where equities come from one source and benchmarks from another.  A
    ``compared`` verdict with ``rows_compared=0`` would read as "checked and
    clean" while nothing was checked at all.
    """
    sources = tuple(sorted({str(value) for value in sample["source"]}))
    if len(sources) < 2:
        return PriceComparison(
            status="not_comparable",
            reason="single_price_source",
            sources=sources,
            rows_compared=0,
            exceeding=(),
        )
    by_key: dict[tuple[str, date], dict[str, Any]] = {}
    for row in sample.to_dict("records"):
        by_key.setdefault((row["symbol"], row["trade_date"]), {})[
            str(row["source"])
        ] = row
    paired = {key: pair for key, pair in by_key.items() if len(pair) >= 2}
    if not paired:
        return PriceComparison(
            status="not_comparable",
            reason="no_paired_bars",
            sources=sources,
            rows_compared=0,
            exceeding=(),
        )
    compared = 0
    exceeding: list[str] = []
    for (symbol, day), pair in sorted(paired.items()):
        # The pair's own two sources, not a fixed global pair: with sources
        # that divide the symbol space there is no global pair to name.
        first, second = sorted(pair)[:2]
        issues = compare_daily_sources(pair[first], pair[second], thresholds)
        compared += 1
        if any(issue.severity is Severity.ERROR for issue in issues):
            exceeding.append(f"{symbol}@{day.isoformat()}")
    return PriceComparison(
        status="compared",
        reason="",
        sources=sources,
        rows_compared=compared,
        exceeding=tuple(sorted(exceeding)),
    )


@dataclass(frozen=True)
class VersionFacts:
    """The version-side facts each worksheet program area is built from.

    Read once per operation and shared by the candidate rows and the three
    comparators, so a worksheet can never be rendered from one read of the
    version while its comparison comes from another.
    """

    open_days: tuple[date, ...]
    rules: tuple[RuleRow, ...]
    price_sample: pd.DataFrame


def version_facts(
    project_root: Path, dataset_version: str, start: date, end: date
) -> VersionFacts:
    """Read one version's calendar, trading rules and price sample.

    The window is the same requested-start/resolved-end window the
    mechanisable evidence pack uses (``checks._window``), so a worksheet's
    program area and the evidence pack describe one window, never two.
    """
    root = Path(project_root).resolve()
    from stock_quant.data_model.dataset import DatasetReader
    from stock_quant.research.acceptance.checks import _open_days

    try:
        with DatasetReader(root).open(dataset_version) as dataset:
            daily = dataset.read("daily_bar")
            calendar = dataset.read("trading_calendar")
    except (OSError, KeyError, ValueError, TypeError) as error:
        raise WorksheetError("external_input_invalid") from error
    open_days = tuple(
        day for day in _open_days(calendar) if start <= day <= end
    )
    return VersionFacts(
        open_days=open_days,
        rules=rule_rows(root / "configs" / "trading_rules.yml"),
        price_sample=price_sample_frame(daily, open_days),
    )


def compare_for_code(
    code: str, facts: VersionFacts, data: bytes | None
) -> CalendarComparison | RuleComparison | PriceComparison:
    """The one comparison one operator-only code is judged by.

    ``prepare`` and ``confirm`` both call this, from the same facts and the
    same stored bytes: two call sites building their own comparison would
    eventually disagree, and the whole point of the queue is that the operator
    sees at signing time what they were shown at preparation time.  ``data`` is
    ``None`` whenever no excerpt was supplied -- including every call for
    ``cross_source_price_sample``, which never takes one.
    """
    if code == "exchange_calendar_sample":
        return compare_calendar(
            facts.open_days,
            parse_calendar_excerpt(data) if data is not None else None,
        )
    if code == "trading_rule_effective_dates":
        return compare_trading_rules(
            facts.rules,
            parse_rule_excerpt(data) if data is not None else None,
        )
    if code == "cross_source_price_sample":
        return compare_price_sources(facts.price_sample)
    raise WorksheetError("unknown_check_code")
```

`yaml` 与 `Mapping` 若已导入则不要重复导入。`@dataclass`、`date`、`Path`、`Sequence` 在 Task 3/Task 4 已导入。

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n py310 python -m pytest tests/unit/test_acceptance_external_inputs.py -q`
Expected: PASS（19 passed）

- [ ] **Step 5: Commit**

```bash
git add src/stock_quant/research/acceptance/external_inputs.py tests/unit/test_acceptance_external_inputs.py
git commit -m "feat: compare calendar, trading-rule and cross-source excerpts"
```

---

### Task 5: 候选内容、待确认队列与确认强度

**Files:**
- Create: `src/stock_quant/research/acceptance/worksheet_program.py`
- Test: `tests/unit/test_acceptance_worksheet_program.py`

**Interfaces:**
- Consumes: Task 1–4 的全部公开名字；`stock_quant.research.acceptance.models.MECHANISABLE_CODES`、`OPERATOR_ONLY_CODES`
- Produces:
  - `candidate_rows(facts: VersionFacts) -> dict[str, tuple[str, ...]]`（每个外部 code 各自的候选行）
  - `candidate_payload(code, rows) -> dict[str, object]`、`candidate_bytes(code, rows) -> bytes`、`candidate_name(code) -> str`
  - `review_queue(code, rows, *, previous_rows, supersede=False, extra=()) -> tuple[str, ...]`
  - `strength_for(code, comparison) -> str`
  - `comparison_for_checklist(code, comparison) -> dict[str, object]`
  - `build_program(...) -> dict[str, object]`

- [ ] **Step 1: Write the failing test**

创建 `tests/unit/test_acceptance_worksheet_program.py`：

```python
"""Unit behaviour of the worksheet program area (Task 5).

The program area carries what one code's confirmation is judged against: the
candidate rows, the pending-review queue and -- for the three operator-only
codes -- the comparison result and the program-decided confirmation strength.
The strength is never an input: an operator cannot claim
``EXTERNAL_CORROBORATED``, only a comparison with no difference can produce it.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_quant.research.acceptance.external_inputs import (
    CalendarComparison,
    PriceComparison,
    RuleComparison,
    RuleRow,
    VersionFacts,
)
from stock_quant.research.acceptance.worksheet import (
    EXTERNAL_CORROBORATED,
    OPERATOR_ATTESTED,
    WorksheetError,
)
from stock_quant.research.acceptance.worksheet_program import (
    candidate_bytes,
    candidate_rows,
    build_program,
    comparison_for_checklist,
    review_queue,
    strength_for,
)


def _facts() -> VersionFacts:
    return VersionFacts(
        open_days=(date(2021, 11, 1), date(2021, 11, 2)),
        rules=(RuleRow("star", "NORMAL", "2019-07-22", "0.20"),),
        price_sample=pd.DataFrame(
            [{"symbol": "600000.SH", "trade_date": date(2021, 11, 1)}]
        ),
    )


def test_candidate_rows_are_grouped_by_the_code_that_reviews_them() -> None:
    assert candidate_rows(_facts()) == {
        "exchange_calendar_sample": (
            "calendar:2021-11-01",
            "calendar:2021-11-02",
        ),
        "trading_rule_effective_dates": ("rule:star|NORMAL|2019-07-22|0.20",),
        "cross_source_price_sample": ("price:600000.SH@2021-11-01",),
    }


def test_candidate_bytes_are_deterministic_and_named_per_code() -> None:
    payload = candidate_bytes("exchange_calendar_sample", ("calendar:2021-11-01",))
    assert payload == candidate_bytes(
        "exchange_calendar_sample", ("calendar:2021-11-01",)
    )
    assert b'"rows"' in payload


def test_mechanisable_codes_never_have_a_queue() -> None:
    assert review_queue(
        "secret_scan", ("row",), previous_rows=("row",)
    ) == ()


def test_first_signing_queues_every_candidate() -> None:
    assert review_queue(
        "exchange_calendar_sample",
        ("calendar:2021-11-01", "calendar:2021-11-02"),
        previous_rows=None,
    ) == ("calendar:2021-11-01", "calendar:2021-11-02")


def test_follow_up_signing_queues_only_the_delta() -> None:
    assert review_queue(
        "exchange_calendar_sample",
        ("calendar:2021-11-01", "calendar:2021-11-02", "calendar:2021-11-03"),
        previous_rows=("calendar:2021-11-01", "calendar:2021-11-02"),
    ) == ("calendar:2021-11-03",)


def test_cross_source_price_always_queues_everything() -> None:
    assert review_queue(
        "cross_source_price_sample",
        ("price:600000.SH@2021-11-01",),
        previous_rows=("price:600000.SH@2021-11-01",),
    ) == ("price:600000.SH@2021-11-01",)


def test_supersede_always_queues_everything() -> None:
    assert review_queue(
        "exchange_calendar_sample",
        ("calendar:2021-11-01",),
        previous_rows=("calendar:2021-11-01",),
        supersede=True,
    ) == ("calendar:2021-11-01",)


def test_strength_is_decided_by_the_comparison_not_by_input() -> None:
    assert strength_for(
        "exchange_calendar_sample",
        CalendarComparison("compared", True, (), (), ()),
    ) == EXTERNAL_CORROBORATED
    assert strength_for(
        "exchange_calendar_sample",
        CalendarComparison("compared", False, (), (), ()),
    ) == OPERATOR_ATTESTED
    assert strength_for(
        "exchange_calendar_sample",
        CalendarComparison("compared", True, ("2021-11-03",), (), ()),
    ) == OPERATOR_ATTESTED
    assert strength_for(
        "trading_rule_effective_dates",
        RuleComparison("compared", (), (), ()),
    ) == EXTERNAL_CORROBORATED
    assert strength_for(
        "trading_rule_effective_dates",
        RuleComparison("compared", ("star|NORMAL|2019-07-22",), (), ()),
    ) == OPERATOR_ATTESTED
    assert strength_for(
        "cross_source_price_sample",
        PriceComparison("compared", "", ("a", "b"), 12, ()),
    ) == OPERATOR_ATTESTED


def test_mechanisable_strength_is_constant() -> None:
    assert strength_for("secret_scan", None) == OPERATOR_ATTESTED


def test_an_unknown_code_is_rejected() -> None:
    with pytest.raises(WorksheetError) as error:
        strength_for("not_a_check", None)
    assert error.value.category == "revision_chain_invalid"


def test_comparison_payload_is_plain_data() -> None:
    assert comparison_for_checklist(
        "exchange_calendar_sample",
        CalendarComparison("compared", True, ("2021-11-03",), (), ()),
    ) == {
        "status": "compared",
        "has_close_column": True,
        "dataset_open_official_absent": ["2021-11-03"],
        "official_open_dataset_absent": [],
        "official_closed_dataset_open": [],
    }


def test_build_program_omits_operator_only_keys_for_mechanisable_codes() -> None:
    program = build_program(
        code="secret_scan",
        dataset_version="a" * 64,
        dataset_manifest_sha256="a" * 64,
        window={"start": "2021-11-01", "end": "2021-11-30"},
        generated_at="2026-09-08T00:00:00+00:00",
        candidate=[{"reference": "data/x.json", "sha256": "b" * 64}],
        previous_signed=None,
        supersedes=None,
    )
    assert set(program) == {
        "code",
        "dataset_version",
        "dataset_manifest_sha256",
        "window",
        "generated_at",
        "candidate_evidence",
        "previous_signed",
        "supersedes",
    }


def test_build_program_carries_the_operator_only_keys() -> None:
    program = build_program(
        code="exchange_calendar_sample",
        dataset_version="a" * 64,
        dataset_manifest_sha256="a" * 64,
        window={"start": "2021-11-01", "end": "2021-11-30"},
        generated_at="2026-09-08T00:00:00+00:00",
        candidate=[{"reference": "data/c.json", "sha256": "b" * 64}],
        previous_signed=None,
        supersedes=None,
        external_input={"reference": "data/i.txt", "sha256": "c" * 64},
        comparison={"status": "compared"},
        strength=EXTERNAL_CORROBORATED,
        queue=["calendar:2021-11-01"],
    )
    assert program["strength"] == EXTERNAL_CORROBORATED
    assert program["queue"] == ["calendar:2021-11-01"]
    assert program["external_input"] == {
        "reference": "data/i.txt",
        "sha256": "c" * 64,
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n py310 python -m pytest tests/unit/test_acceptance_worksheet_program.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stock_quant.research.acceptance.worksheet_program'`

- [ ] **Step 3: Write minimal implementation**

创建 `src/stock_quant/research/acceptance/worksheet_program.py`：

```python
"""The program area of a standing worksheet.

Split from :mod:`~stock_quant.research.acceptance.worksheet` because it is a
different job: that module owns the file format and the revision chain, this
one owns what the operator is being asked to confirm -- the candidate rows,
the pending-review queue, the comparison result and the confirmation strength.

The strength is a *verdict*, never an input.  Only an excerpt that covers the
configuration with no difference at all yields ``EXTERNAL_CORROBORATED``; the
absence of an external input always yields ``OPERATOR_ATTESTED``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from stock_quant.research.acceptance.external_inputs import (
    CalendarComparison,
    PriceComparison,
    RuleComparison,
    VersionFacts,
)
from stock_quant.research.acceptance.models import (
    MECHANISABLE_CODES,
    OPERATOR_ONLY_CODES,
)
from stock_quant.research.acceptance.worksheet import (
    EXTERNAL_CORROBORATED,
    OPERATOR_ATTESTED,
    WorksheetError,
)

__all__ = [
    "build_program",
    "candidate_bytes",
    "candidate_name",
    "candidate_payload",
    "candidate_rows",
    "comparison_for_checklist",
    "review_queue",
    "strength_for",
]

#: The candidate rows' stable prefixes, one per operator-only code.
_CANDIDATE_PREFIX = {
    "exchange_calendar_sample": "calendar",
    "trading_rule_effective_dates": "rule",
    "cross_source_price_sample": "price",
}


def candidate_rows(facts: VersionFacts) -> dict[str, tuple[str, ...]]:
    """Each operator-only code's version-side candidate rows.

    Grouped by the code that reviews them rather than returned as one pile:
    a calendar queue that also listed rule rows would ask the operator to
    acknowledge something their signature says nothing about.
    """
    return {
        "exchange_calendar_sample": tuple(
            f"calendar:{day.isoformat()}" for day in facts.open_days
        ),
        "trading_rule_effective_dates": tuple(
            f"rule:{row.board}|{row.status}|{row.effective_from}|{row.rate}"
            for row in facts.rules
        ),
        "cross_source_price_sample": tuple(
            f"price:{row['symbol']}@{row['trade_date'].isoformat()}"
            for row in facts.price_sample.to_dict("records")
        ),
    }


def candidate_name(code: str) -> str:
    """The stored file name of one code's candidate snapshot."""
    return f"{code}.candidates.json"


def candidate_payload(code: str, rows: Sequence[str]) -> dict[str, object]:
    """The candidate snapshot as plain data: the code and its rows."""
    return {"code": code, "rows": list(rows)}


def candidate_bytes(code: str, rows: Sequence[str]) -> bytes:
    """The candidate snapshot's canonical bytes (stable for equal rows)."""
    return (
        json.dumps(
            candidate_payload(code, rows),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def review_queue(
    code: str,
    rows: Sequence[str],
    *,
    previous_rows: Sequence[str] | None,
    supersede: bool = False,
    extra: Sequence[str] = (),
) -> tuple[str, ...]:
    """The rows this signing must have looked at, computed by the program.

    The six mechanisable codes have no queue at all: their evidence is built
    and bound by ``prepare``, so there is nothing an operator has to hunt for.
    A first signing, a supersede, and the cross-source sample always queue the
    full candidate set; everything else queues only what changed since the
    last ``PASS`` revision.
    """
    if code in MECHANISABLE_CODES:
        return ()
    if code not in OPERATOR_ONLY_CODES:
        raise WorksheetError("revision_chain_invalid")
    if supersede or previous_rows is None or code == "cross_source_price_sample":
        return tuple(rows) + tuple(extra)
    seen = set(previous_rows)
    delta = tuple(row for row in rows if row not in seen)
    return delta + tuple(extra)


def strength_for(code: str, comparison: object) -> str:
    """The confirmation strength the program grants, never the operator asks for."""
    if code in MECHANISABLE_CODES:
        return OPERATOR_ATTESTED
    if code not in OPERATOR_ONLY_CODES:
        raise WorksheetError("revision_chain_invalid")
    if code == "cross_source_price_sample":
        # Design decision: a cross-source check is corroboration only when a
        # second source actually ran; the shipped single-source versions never
        # reach it, so the strength is fixed rather than dressed up.
        return OPERATOR_ATTESTED
    corroborated = (
        comparison.corroborated
        if isinstance(comparison, (CalendarComparison, RuleComparison))
        else False
    )
    return EXTERNAL_CORROBORATED if corroborated else OPERATOR_ATTESTED


def comparison_for_checklist(code: str, comparison: object) -> dict[str, object]:
    """The comparison result as plain JSON-ready data for the program area."""
    if isinstance(comparison, CalendarComparison):
        return {
            "status": comparison.status,
            "has_close_column": comparison.has_close_column,
            "dataset_open_official_absent": list(
                comparison.dataset_open_official_absent
            ),
            "official_open_dataset_absent": list(
                comparison.official_open_dataset_absent
            ),
            "official_closed_dataset_open": list(
                comparison.official_closed_dataset_open
            ),
        }
    if isinstance(comparison, RuleComparison):
        return {
            "status": comparison.status,
            "uncovered": list(comparison.uncovered),
            "conflicting": list(comparison.conflicting),
            "unclaimed": list(comparison.unclaimed),
        }
    if isinstance(comparison, PriceComparison):
        return {
            "status": comparison.status,
            "reason": comparison.reason,
            "sources": list(comparison.sources),
            "rows_compared": comparison.rows_compared,
            "exceeding": list(comparison.exceeding),
        }
    return {"status": "no_external_input"}


def build_program(
    *,
    code: str,
    dataset_version: str,
    dataset_manifest_sha256: str,
    window: Mapping[str, str],
    generated_at: str,
    candidate: Sequence[Mapping[str, str]],
    previous_signed: Mapping[str, object] | None,
    supersedes: Mapping[str, str] | None,
    external_input: Mapping[str, str] | None = None,
    comparison: Mapping[str, object] | None = None,
    strength: str | None = None,
    queue: Sequence[str] = (),
) -> dict[str, object]:
    """One worksheet's program area: the shared header plus operator-only keys.

    The three operator-only keys are present only for the codes that have an
    external contract.  Writing them for a mechanisable code would suggest an
    input that code does not accept.
    """
    program: dict[str, object] = {
        "code": code,
        "dataset_version": dataset_version,
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "window": dict(window),
        "generated_at": generated_at,
        "candidate_evidence": [dict(row) for row in candidate],
        "previous_signed": dict(previous_signed) if previous_signed else None,
        "supersedes": dict(supersedes) if supersedes else None,
    }
    if code in OPERATOR_ONLY_CODES:
        program["external_input"] = (
            dict(external_input) if external_input else None
        )
        program["comparison"] = dict(comparison) if comparison else {
            "status": "no_external_input"
        }
        program["strength"] = strength or OPERATOR_ATTESTED
        program["queue"] = list(queue)
    return program
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n py310 python -m pytest tests/unit/test_acceptance_worksheet_program.py -q`
Expected: PASS（12 passed）

- [ ] **Step 5: Commit**

```bash
git add src/stock_quant/research/acceptance/worksheet_program.py tests/unit/test_acceptance_worksheet_program.py
git commit -m "feat: compute worksheet candidates, queues and strengths"
```

---

### Task 6: `prepare` 侧 —— 只读预检、写工作表、`--force` 恢复

**Files:**
- Modify: `src/stock_quant/research/acceptance/service.py`
- Modify: `src/stock_quant/research/acceptance/evidence.py`（`_load_manifest` → 公开 `load_manifest`）
- Modify: `src/stock_quant/research/acceptance/worksheet.py`（新增 `SIGNED_SUMMARY` 与两个错误类别）
- Create: `src/stock_quant/research/acceptance/worksheet_prepare.py`
- Create: `tests/unit/conftest.py`
- Modify: `src/stock_quant/cli.py`（`data acceptance prepare` 增加 `--force`）
- Test: `tests/unit/test_acceptance_worksheet_prepare.py`

**Interfaces:**
- Consumes: Task 1–5 的全部公开名字
- Produces:
  - `service.write_checklist_atomic(checklist, output_path) -> None`（`_write_checklist` 的公开名）
  - `service.binding_reasons(checklist, fresh) -> list[str]`（`_binding_reasons` 的公开名）
  - `service.verify_evidence_reference(project_root, reference) -> str | None`（`_verify_evidence_reference` 的公开名）
  - `service.prepare_checklist(project_root, dataset_version, operator_id, output_path, *, prepared_at=None, force=False) -> AcceptanceChecklist`
  - `evidence.load_manifest(root, dataset_version) -> dict[str, object]`
  - `evidence.read_pack_references(project_root, dataset_version) -> dict[str, EvidenceReference]`（只读；不写证据包）
  - `worksheet.SIGNED_SUMMARY: dict[str, str]`
  - `worksheet_prepare.precheck_signed(project_root, dataset_version, *, force) -> None`
  - `worksheet_prepare.window_of(project_root, dataset_version) -> tuple[date, date]`
  - `worksheet_prepare.candidate_blob(project_root, code, rows) -> dict[str, str]`
  - `worksheet_prepare.candidate_evidence(project_root, code, rows_by_code, pack_references) -> list[dict[str, str]]`
  - `worksheet_prepare.previous_candidate_rows(project_root, revision) -> tuple[str, ...] | None`
  - `worksheet_prepare.previous_signed_payload(revision) -> dict[str, object] | None`
  - `worksheet_prepare.revision_reference(revision) -> EvidenceReference`
  - `worksheet_prepare.prepare_worksheets(project_root, checklist, *, force=False) -> AcceptanceChecklist`
  - `worksheet_prepare.restore_signed_rows(project_root, checklist) -> tuple[ManualCheckResult, ...]`
  - `tests/unit/conftest.py` 的 `published_project` fixture

- [ ] **Step 1: Write the failing test**

新建 `tests/unit/conftest.py`（`tests/unit/` 有 `__init__.py` 而 `tests/` 没有，所以 pytest 把该目录的模块命名为 `unit.<模块>`；这一行已实测可用）：

```python
"""Shared fixtures for the acceptance unit tests.

The worksheet tests need a project root with one really published dataset
version -- the same thing the service tests build -- so it is exposed here as
a fixture rather than duplicated or imported across test modules.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from unit.test_acceptance_service import AcceptanceProject, _published_project


@pytest.fixture
def published_project(tmp_path: Path) -> AcceptanceProject:
    """A project root holding one published, validated dataset version."""
    return _published_project(tmp_path / "project")
```

新建 `tests/unit/test_acceptance_worksheet_prepare.py`：

```python
"""Unit behaviour of the prepare side of the worksheet line (Task 6).

``prepare`` must be safe to re-run: it never touches a signed revision, and a
re-run over a signed version refuses *before writing anything* unless
``--force`` is given.  ``--force`` restores the signed rows into a rebuilt
checklist without inventing, discarding or re-dating a conclusion.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from stock_quant.research.acceptance.models import (
    MANUAL_CHECK_CODES,
    ManualCheckStatus,
)
from stock_quant.research.acceptance.service import prepare_checklist
from stock_quant.research.acceptance.worksheet import (
    MARKER_HUMAN_BEGIN,
    WorksheetError,
    append_signature,
    pending_path,
    program_payload,
    verify_markers,
    write_revision,
)
from stock_quant.research.acceptance.worksheet_prepare import window_of

_PREPARED_AT = datetime(2026, 9, 8, tzinfo=timezone.utc)


def _prepare(project, tmp_path: Path, name: str, **kwargs):
    return prepare_checklist(
        project.root,
        project.version,
        "operator-a",
        tmp_path / name,
        prepared_at=_PREPARED_AT,
        **kwargs,
    )


def _program(project, code: str) -> dict:
    """One pending worksheet's program area, as parsed."""
    text = pending_path(project.root, project.version, code).read_text(
        encoding="utf-8"
    )
    return program_payload(verify_markers(text)[0])


def _sign(project, code: str, decision: str = "PASS"):
    """Store one signed revision directly, without going through ``confirm``.

    This task is the prepare side only.  A hand-built revision is the smallest
    thing that puts a version into the "already signed" state this side has to
    refuse, so these tests do not depend on the confirm writer of Task 7.
    """
    path = pending_path(project.root, project.version, code)
    program, human = verify_markers(path.read_text(encoding="utf-8"))
    human = append_signature(
        human,
        {
            "operator_id": "operator-a",
            "confirmed_at": _PREPARED_AT.isoformat(),
            "decision": decision,
            "strength": "OPERATOR_ATTESTED",
            "conclusion": "reviewed",
            "supersedes": None,
        },
    )
    return write_revision(
        project.root, project.version, code, program_payload(program), human
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    """Every file under a root, so "wrote nothing" can be asserted exactly."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_prepare_writes_nine_unsigned_worksheets(published_project, tmp_path) -> None:
    checklist = _prepare(published_project, tmp_path, "checklist.yml")
    for code in MANUAL_CHECK_CODES:
        path = pending_path(published_project.root, published_project.version, code)
        assert MARKER_HUMAN_BEGIN in path.read_text(encoding="utf-8")
        assert _program(published_project, code)["code"] == code
    assert all(
        row.status is ManualCheckStatus.PENDING_CONFIRMATION
        for row in checklist.manual_checks
    )


def test_the_window_comes_from_the_version_manifest(published_project, tmp_path) -> None:
    _prepare(published_project, tmp_path, "checklist.yml")
    assert window_of(published_project.root, published_project.version) == (
        date(2021, 11, 1),
        date(2021, 11, 30),
    )
    assert _program(published_project, "secret_scan")["window"] == {
        "start": "2021-11-01",
        "end": "2021-11-30",
    }


def test_a_mechanisable_worksheet_cites_the_evidence_pack(
    published_project, tmp_path
) -> None:
    _prepare(published_project, tmp_path, "checklist.yml")
    program = _program(published_project, "secret_scan")
    assert program["candidate_evidence"][0]["reference"].startswith(
        "data/acceptance-evidence/"
    )
    assert "queue" not in program
    assert "strength" not in program


def test_an_operator_only_worksheet_carries_its_own_contract(
    published_project, tmp_path
) -> None:
    _prepare(published_project, tmp_path, "checklist.yml")
    program = _program(published_project, "exchange_calendar_sample")
    assert program["candidate_evidence"][0]["reference"].startswith(
        "data/acceptance-external-inputs/"
    )
    assert program["external_input"] is None
    assert program["comparison"] == {"status": "no_external_input"}
    assert program["strength"] == "OPERATOR_ATTESTED"
    assert program["queue"], "a first signing queues the whole candidate set"


def test_a_rerun_over_a_signed_version_writes_nothing(
    published_project, tmp_path, monkeypatch
) -> None:
    """The pre-check must fire *before* the evidence pack is rebuilt.

    Rebuilding the pack is itself a write, so a check that ran after it would
    already have touched the tree it is supposed to protect.  Booby-trapping
    the rebuild is the only assertion that proves the order: a snapshot
    comparison would pass even if the pack had been rewritten, because the
    rebuild is deterministic and leaves the same bytes behind.
    """
    _prepare(published_project, tmp_path, "checklist.yml")
    _sign(published_project, "secret_scan")
    before = _snapshot(published_project.root)

    def _explode(*args, **kwargs):
        raise AssertionError("the evidence pack was rebuilt before the pre-check")

    monkeypatch.setattr(
        "stock_quant.research.acceptance.service.build_mechanisable_evidence",
        _explode,
    )
    with pytest.raises(WorksheetError) as error:
        _prepare(published_project, tmp_path, "again.yml")
    assert error.value.category == "signed_worksheets_present"
    assert _snapshot(published_project.root) == before


def test_force_restores_signed_rows_without_touching_the_revision(
    published_project, tmp_path
) -> None:
    _prepare(published_project, tmp_path, "checklist.yml")
    revision = _sign(published_project, "secret_scan")
    before = revision.path.read_bytes()
    checklist = _prepare(published_project, tmp_path, "forced.yml", force=True)
    row = next(row for row in checklist.manual_checks if row.code == "secret_scan")
    assert row.status is ManualCheckStatus.PASS
    assert row.evidence[0].reference == revision.reference
    assert row.evidence[0].sha256 == revision.sha256
    assert revision.path.read_bytes() == before
    assert not pending_path(
        published_project.root, published_project.version, "secret_scan"
    ).exists()
    assert all(
        other.status is ManualCheckStatus.PENDING_CONFIRMATION
        for other in checklist.manual_checks
        if other.code != "secret_scan"
    )


def test_force_refuses_when_a_signed_revision_drifted(
    published_project, tmp_path
) -> None:
    """``--force`` restores signed rows; it never repairs a broken revision."""
    _prepare(published_project, tmp_path, "checklist.yml")
    revision = _sign(published_project, "secret_scan")
    revision.path.chmod(0o644)
    revision.path.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(WorksheetError) as error:
        _prepare(published_project, tmp_path, "forced.yml", force=True)
    assert error.value.category == "signed_worksheet_drift"
    assert revision.path.read_text(encoding="utf-8") == "tampered\n"
    assert not (tmp_path / "forced.yml").exists()


def test_a_failed_evidence_build_still_writes_a_worksheet(
    published_project, tmp_path, monkeypatch
) -> None:
    from stock_quant.research.acceptance import evidence

    def _fail(*args, **kwargs):
        raise evidence.EvidenceBuildError("dataset_unreadable")

    monkeypatch.setattr(
        "stock_quant.research.acceptance.service.build_mechanisable_evidence", _fail
    )
    checklist = _prepare(published_project, tmp_path, "checklist.yml")
    assert all(not row.evidence for row in checklist.manual_checks)
    assert _program(published_project, "secret_scan")["candidate_evidence"] == []
    assert _program(published_project, "exchange_calendar_sample")[
        "candidate_evidence"
    ], "the operator-only snapshot does not depend on the evidence pack"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n py310 python -m pytest tests/unit/test_acceptance_worksheet_prepare.py -q`
Expected: FAIL — `TypeError: prepare_checklist() got an unexpected keyword argument 'force'`

- [ ] **Step 3: Write the implementation**

**3a.** `src/stock_quant/research/acceptance/evidence.py`：把 `_load_manifest` 改名为 `load_manifest`（补进 `__all__`，更新 `build_mechanisable_evidence` 内的调用点）。语义不变。再追加一个只读的 `read_pack_references`（同样补进 `__all__`）：

```python
def read_pack_references(
    project_root: Path, dataset_version: str
) -> dict[str, EvidenceReference]:
    """The references of an **existing** pack, re-hashed read-only.

    Rebuilding a pack is a write, and ``confirm`` may not write evidence; so
    the signer reads what ``prepare`` already published and cites exactly the
    bytes it reviewed.  Reading the pack rather than the checklist row is what
    makes a supersede cite the same artifacts the first signing cited, instead
    of citing the revision it is replacing.  A missing or half-written pack
    yields fewer entries -- or none -- which is what lets ``confirm`` refuse a
    mechanisable row it has nothing to cite for.
    """
    root = Path(project_root).resolve()
    pack = _pack_dir(root, dataset_version)
    references: dict[str, EvidenceReference] = {}
    for code, name in EVIDENCE_FILENAMES.items():
        candidate = pack / name
        if not candidate.is_file():
            continue
        references[code] = EvidenceReference(
            kind="local",
            reference=candidate.relative_to(root).as_posix(),
            sha256=_sha256_file(candidate),
            summary=f"{code} evidence",
        )
    return references
```

（`EvidenceReference` 与 `_sha256_file`/`_pack_dir` 已在该文件内；若 `EvidenceReference` 尚未从 `models` 导入则补上。）

**3b.** `src/stock_quant/research/acceptance/worksheet.py`：把 `"worksheet_missing"`、`"unknown_check_code"` 追加到 `WORKSHEET_ERROR_CATEGORIES` 末尾，并按「类型一致性」在 `__all__` 补 `SIGNED_SUMMARY`；新增常量：

```python
#: The conclusion-free summary a signed manual row carries.  The signature and
#: the conclusion live in the worksheet's human area only: a row's ``summary``
#: is rewritten by every supersede, so putting the old conclusion there would
#: leave a stale judgement attached to the newest decision.
SIGNED_SUMMARY = {
    "PASS": "operator confirmed on worksheet revision",
    "FAIL": "operator rejected on worksheet revision",
}
```

**3c.** `src/stock_quant/research/acceptance/service.py`：把 `_write_checklist`、`_binding_reasons`、`_verify_evidence_reference` 改名为 `write_checklist_atomic`、`binding_reasons`、`verify_evidence_reference`，更新文件内所有调用点，并补进 `__all__`（若该文件有 `__all__`）。三个函数体一律不动。然后把 `prepare_checklist` 改成：

```python
def prepare_checklist(
    project_root: Path,
    dataset_version: str,
    operator_id: str,
    output_path: Path,
    *,
    prepared_at: datetime | None = None,
    force: bool = False,
) -> AcceptanceChecklist:
    """Build the checklist, generate the evidence pack, write worksheets and both.

    The signed-version pre-check runs *before* the evidence pack is rebuilt: a
    check that ran after it would already have changed the tree it protects.
    Without ``force`` an already signed version stops here; with ``force`` the
    signed rows are restored and no revision is written over.
    """
    root = Path(project_root).resolve()
    precheck_signed(root, dataset_version, force=force)
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
    checklist = prepare_worksheets(root, checklist, force=force)
    write_checklist_atomic(checklist, Path(output_path))
    return checklist
```

并在文件顶部 import 行加（`service` → `worksheet_prepare` 是单向依赖，`worksheet_prepare` 反向引用 `service` 的地方是惰性 import，见 3d）：

```python
from stock_quant.research.acceptance.worksheet_prepare import (
    precheck_signed,
    prepare_worksheets,
)
```

**3d.** 新建 `src/stock_quant/research/acceptance/worksheet_prepare.py`：

```python
"""The prepare side of the standing worksheet line.

``prepare`` writes one unsigned worksheet per manual code, carrying forward the
human area of the last confirmed ``PASS`` revision of the same code from
another dataset version -- content carry-forward, never file reuse.  A version
that already has a signed revision is refused *before anything is written*
unless ``--force`` is given, in which case the signed rows are restored into
the rebuilt checklist and the revisions are left byte-identical.

The value builders in this module (``candidate_blob``, ``candidate_evidence``,
``previous_candidate_rows``, ``previous_signed_payload``, ``revision_reference``,
``window_of``) are public because ``worksheet.confirm`` builds the *same*
program area from the *same* facts: two renderings of one worksheet must not be
able to disagree, and duplicating them would guarantee that they eventually do.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from stock_quant.research.acceptance.evidence import (
    evidence_window,
    load_manifest,
    read_pack_references,
)
from stock_quant.research.acceptance.external_inputs import (
    load_blob,
    store_blob,
    version_facts,
)
from stock_quant.research.acceptance.models import (
    MANUAL_CHECK_CODES,
    MECHANISABLE_CODES,
    OPERATOR_ONLY_CODES,
    AcceptanceChecklist,
    EvidenceReference,
    ManualCheckResult,
    ManualCheckStatus,
)
from stock_quant.research.acceptance.worksheet import (
    SIGNED_SUMMARY,
    Revision,
    WorksheetError,
    effective_revision,
    latest_pass_revision,
    pending_path,
    render_worksheet,
    signed_codes,
)
from stock_quant.research.acceptance.worksheet_program import (
    build_program,
    candidate_bytes,
    candidate_name,
    candidate_rows,
    review_queue,
)

__all__ = [
    "candidate_blob",
    "candidate_evidence",
    "precheck_signed",
    "prepare_worksheets",
    "previous_candidate_rows",
    "previous_signed_payload",
    "restore_signed_rows",
    "revision_reference",
    "window_of",
]


def precheck_signed(
    project_root: Path, dataset_version: str, *, force: bool
) -> None:
    """Refuse a re-prepare over a signed version before any write happens.

    Rebuilding the evidence pack is itself a write, so this cannot be checked
    after it: ``prepare`` would already have changed the tree it must protect.
    """
    if not force and signed_codes(Path(project_root).resolve(), dataset_version):
        raise WorksheetError("signed_worksheets_present")


def window_of(project_root: Path, dataset_version: str) -> tuple[date, date]:
    """The window every worksheet of one version is computed over."""
    return evidence_window(
        load_manifest(Path(project_root).resolve(), dataset_version)
    )


def candidate_blob(
    project_root: Path, code: str, rows: tuple[str, ...]
) -> dict[str, str]:
    """Store one code's candidate snapshot and return its evidence pointer."""
    stored = store_blob(
        Path(project_root).resolve(),
        candidate_bytes(code, rows),
        candidate_name(code),
    )
    return {"reference": stored.reference, "sha256": stored.sha256}


def candidate_evidence(
    project_root: Path,
    code: str,
    rows_by_code: dict[str, tuple[str, ...]],
    pack_references: dict[str, EvidenceReference],
) -> list[dict[str, str]]:
    """What one confirmation is judged against.

    The six mechanisable codes point at the evidence pack ``prepare`` wrote;
    the three operator-only codes point at a content-addressed snapshot of the
    version-side rows they enumerate.  Both are read back from disk rather
    than lifted off the checklist row, so a supersede cites the same pack
    files as the first signing instead of citing the revision it replaces.
    When the evidence build failed the list is honestly empty rather than
    pointing at something that was never written.
    """
    if code in MECHANISABLE_CODES:
        reference = pack_references.get(code)
        return (
            []
            if reference is None
            else [{"reference": reference.reference, "sha256": reference.sha256}]
        )
    return [candidate_blob(project_root, code, rows_by_code[code])]


def previous_candidate_rows(
    project_root: Path, revision: Revision
) -> tuple[str, ...] | None:
    """The candidate rows a previous revision queued against, if still readable.

    ``None`` means "no usable baseline", which only ever *enlarges* the next
    queue: a missing or unreadable snapshot falls back to the full candidate
    set instead of silently claiming those rows were already reviewed.
    """
    recorded = revision.payload.get("candidate_evidence")
    if not isinstance(recorded, list) or not recorded:
        return None
    first = recorded[0]
    if not isinstance(first, dict) or "reference" not in first:
        return None
    try:
        payload = json.loads(load_blob(project_root, str(first["reference"])))
    except (WorksheetError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    return tuple(str(row) for row in rows)


def previous_signed_payload(revision: Revision | None) -> dict[str, object] | None:
    """The ``previous_signed`` program header for one revision."""
    if revision is None:
        return None
    return {
        "dataset_version": revision.payload.get("dataset_version"),
        "confirmed_at": revision.confirmed_at,
        "operator_id": revision.operator_id,
        "reference": revision.reference,
        "sha256": revision.sha256,
    }


def revision_reference(revision: Revision) -> EvidenceReference:
    """The evidence pointer a checklist row uses for one revision."""
    return EvidenceReference(
        kind="local",
        reference=revision.reference,
        sha256=revision.sha256,
        summary=f"{revision.payload.get('code')} worksheet revision",
    )


def prepare_worksheets(
    project_root: Path,
    checklist: AcceptanceChecklist,
    *,
    force: bool = False,
) -> AcceptanceChecklist:
    """Write or refresh this version's worksheets and return the checklist."""
    root = Path(project_root).resolve()
    version = checklist.dataset_version
    precheck_signed(root, version, force=force)
    if force:
        checklist = checklist.model_copy(
            update={"manual_checks": restore_signed_rows(root, checklist)}
        )
    start, end = window_of(root, version)
    facts = version_facts(root, version, start, end)
    rows_by_code = candidate_rows(facts)
    pack_references = read_pack_references(root, version)
    window = {"start": start.isoformat(), "end": end.isoformat()}
    for code in MANUAL_CHECK_CODES:
        path = pending_path(root, version, code)
        if effective_revision(root, version, code) is not None:
            # A signed code has no unsigned worksheet: a leftover one would
            # invite a re-signing against a state nothing else agrees with.
            path.unlink(missing_ok=True)
            continue
        previous = latest_pass_revision(root, code, exclude_version=version)
        program = build_program(
            code=code,
            dataset_version=version,
            dataset_manifest_sha256=checklist.dataset_manifest_sha256,
            window=window,
            generated_at=checklist.prepared_at.isoformat(),
            candidate=candidate_evidence(root, code, rows_by_code, pack_references),
            previous_signed=previous_signed_payload(previous),
            supersedes=None,
            queue=(
                review_queue(
                    code,
                    rows_by_code[code],
                    previous_rows=(
                        previous_candidate_rows(root, previous)
                        if previous is not None
                        else None
                    ),
                )
                if code in OPERATOR_ONLY_CODES
                else ()
            ),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            render_worksheet(
                program,
                previous.human
                if previous is not None
                else "",
            ),
            encoding="utf-8",
        )
    return checklist


def restore_signed_rows(
    project_root: Path, checklist: AcceptanceChecklist
) -> tuple[ManualCheckResult, ...]:
    """Rebuild signed rows from their effective revisions, verifying binding.

    Every revision must still hash to its own name (``revision_chain``
    enforces that), bind this version's manifest and the current window, and
    keep every cited file verifiable.  Any drift fails closed here, before a
    single worksheet is written.
    """
    root = Path(project_root).resolve()
    version = checklist.dataset_version
    start, end = window_of(root, version)
    window = {"start": start.isoformat(), "end": end.isoformat()}
    restored: list[ManualCheckResult] = []
    for row in checklist.manual_checks:
        revision = effective_revision(root, version, row.code)
        restored.append(
            row
            if revision is None
            else _restored_row(root, row, revision, checklist, window)
        )
    return tuple(restored)


def _restored_row(
    root: Path,
    row: ManualCheckResult,
    revision: Revision,
    checklist: AcceptanceChecklist,
    window: dict[str, str],
) -> ManualCheckResult:
    """One signed row, restored from its revision without rewriting it."""
    if revision.payload.get("dataset_manifest_sha256") != (
        checklist.dataset_manifest_sha256
    ):
        raise WorksheetError("signed_worksheet_drift")
    if revision.payload.get("window") != window:
        raise WorksheetError("signed_worksheet_drift")
    evidence = [revision_reference(revision)]
    external = revision.payload.get("external_input")
    if isinstance(external, dict) and "reference" in external:
        evidence.append(
            EvidenceReference(
                kind="local",
                reference=str(external["reference"]),
                sha256=str(external["sha256"]),
                summary=f"{revision.payload.get('code')} external input",
            )
        )
    for reference in evidence:
        # ``service`` imports this module, so the shared verifier is imported
        # lazily: a module-level import would be a cycle.
        from stock_quant.research.acceptance.service import (
            verify_evidence_reference,
        )

        if verify_evidence_reference(root, reference) is not None:
            raise WorksheetError("signed_worksheet_drift")
    return row.model_copy(
        update={
            "status": ManualCheckStatus(revision.decision),
            "summary": SIGNED_SUMMARY[revision.decision],
            "evidence": tuple(evidence),
        }
    )
```

**3e.** `src/stock_quant/cli.py`：`data_acceptance_prepare` 增加 `--force`，并把它传下去：

```python
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help=(
                "Restore already signed rows into a rebuilt checklist. Never "
                "overwrites or deletes a signed worksheet."
            ),
        ),
    ] = False,
```

```python
        checklist = prepare_checklist(
            project_root, version, operator, output, force=force
        )
```

并在该命令的 `except` 链里把 `WorksheetError` 打印成稳定类别（与既有 `EvidenceBuildError` 分支同样的形状）：

```python
    except WorksheetError as error:
        typer.echo(f"reason={error.category}")
        raise typer.Exit(code=1) from None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n py310 python -m pytest tests/unit/test_acceptance_worksheet_prepare.py -q`
Expected: PASS（8 passed）

- [ ] **Step 5: 回归既有验收测试**

Run: `conda run -n py310 python -m pytest tests/unit/test_acceptance_service.py tests/unit/test_acceptance_evidence.py tests/integration/test_acceptance_cli.py -q`
Expected: PASS（三个公开名重命名与 `prepare_checklist` 的签名变更不得破坏既有行为）

- [ ] **Step 6: Commit**

```bash
git add src/stock_quant/research/acceptance/service.py \
        src/stock_quant/research/acceptance/evidence.py \
        src/stock_quant/research/acceptance/worksheet.py \
        src/stock_quant/research/acceptance/worksheet_prepare.py \
        src/stock_quant/cli.py tests/unit/conftest.py \
        tests/unit/test_acceptance_worksheet_prepare.py
git commit -m "feat: prepare standing worksheets behind a read-only pre-check"
```

---

### Task 7: `confirm` —— 唯一的人工行写入口

**Files:**
- Modify: `src/stock_quant/research/acceptance/worksheet.py`（追加 `ConfirmationRequest`、`apply_confirmation`、`confirm`）
- Modify: `src/stock_quant/cli.py`（新增 `confirm` 子命令）
- Test: `tests/unit/test_acceptance_worksheet_confirm.py`

**Interfaces:**
- Consumes: Task 1–6 的全部公开名字
- Produces:
  - `@dataclass(frozen=True) class ConfirmationRequest`，字段 `code: str`、`operator_id: str`、`decision: str`、`conclusion: str`、`strength: str`、`supersede: bool`、`evidence: tuple[EvidenceReference, ...]`、`confirmed_at: str`
  - `apply_confirmation(checklist, request, *, effective) -> AcceptanceChecklist`
  - `confirm(project_root, checklist_path, *, code, operator_id, decision, conclusion=None, external_input=None, acknowledge=None, supersede=False, now=None) -> AcceptanceChecklist`
  - 稳定拒绝类别：`unknown_check_code`、`already_signed`、`nothing_to_supersede`、`superseded_revision_drift`、`conclusion_required`、`external_input_invalid`、`acknowledgement_required`、`worksheet_missing`、`candidate_evidence_missing`、`signed_worksheet_drift`（其余人工行不稳时）

- [ ] **Step 1: Write the failing test**

新建 `tests/unit/test_acceptance_worksheet_confirm.py`：

```python
"""Unit behaviour of the worksheet confirm writer (Task 7).

``confirm`` is the only writer that turns a manual row into PASS/FAIL.  It
appends one immutable revision, cites it (plus any external input) as the row's
evidence, and leaves every other byte of the checklist alone -- including the
container's ``operator_id``, which only ``prepare`` may change.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from stock_quant.research.acceptance.models import (
    OPERATOR_ONLY_CODES,
    ManualCheckStatus,
)
from stock_quant.research.acceptance.service import prepare_checklist
from stock_quant.research.acceptance.worksheet import (
    WorksheetError,
    confirm,
    effective_revision,
    pending_path,
    program_payload,
    revision_chain,
    verify_markers,
)

_PREPARED_AT = datetime(2026, 9, 8, tzinfo=timezone.utc)
_CALENDAR_EXCERPT = b"2021-11-01 1\n2021-11-02 1\n"


def _prepared(project, tmp_path: Path) -> Path:
    """A really prepared checklist on disk, with its evidence pack."""
    path = tmp_path / "checklist.yml"
    prepare_checklist(
        project.root,
        project.version,
        "operator-a",
        path,
        prepared_at=_PREPARED_AT,
    )
    return path


def _payload(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _row(path: Path, code: str) -> dict:
    return next(
        row for row in _payload(path)["manual_checks"] if row["code"] == code
    )


def _queue_length(project, code: str) -> int:
    """The queue this code's pending worksheet publishes."""
    text = pending_path(project.root, project.version, code).read_text(
        encoding="utf-8"
    )
    return len(program_payload(verify_markers(text)[0]).get("queue") or ())


def _calendar_rows(project) -> list[str]:
    """The open days the pending worksheet enumerates, as ISO strings.

    Read from the worksheet's own queue -- on a first signing the queue *is*
    the candidate set -- so the excerpt a test transcribes is the same set of
    days the operator was shown, with no second source of truth.
    """
    text = pending_path(project.root, project.version, "exchange_calendar_sample")
    queue = program_payload(verify_markers(text)[0])["queue"]
    return [str(row).removeprefix("calendar:") for row in queue]


def test_confirm_flips_exactly_one_row(published_project, tmp_path) -> None:
    path = _prepared(published_project, tmp_path)
    before = _payload(path)
    confirm(
        published_project.root,
        path,
        code="secret_scan",
        operator_id="operator-a",
        decision="PASS",
    )
    after = _payload(path)
    changed = [
        row["code"]
        for row, other in zip(before["manual_checks"], after["manual_checks"])
        if row != other
    ]
    assert changed == ["secret_scan"]
    assert before["automated_checks"] == after["automated_checks"]
    assert before["operator_id"] == after["operator_id"]
    for field in ("dataset_version", "dataset_manifest_sha256", "prepared_at"):
        assert before[field] == after[field]


def test_confirm_cites_the_revision_and_drops_the_pending_file(
    published_project, tmp_path
) -> None:
    path = _prepared(published_project, tmp_path)
    confirm(
        published_project.root,
        path,
        code="secret_scan",
        operator_id="operator-a",
        decision="PASS",
    )
    revision = effective_revision(
        published_project.root, published_project.version, "secret_scan"
    )
    row = _row(path, "secret_scan")
    assert row["status"] == "PASS"
    assert row["evidence"][0]["reference"] == revision.reference
    assert row["evidence"][0]["sha256"] == revision.sha256
    assert revision.payload["candidate_evidence"], (
        "a mechanisable signing cites the evidence pack it reviewed"
    )
    assert not pending_path(
        published_project.root, published_project.version, "secret_scan"
    ).exists()
    assert all(
        other["status"] == "PENDING_CONFIRMATION"
        for other in _payload(path)["manual_checks"]
        if other["code"] != "secret_scan"
    )


def test_confirming_an_already_signed_row_is_refused(
    published_project, tmp_path
) -> None:
    path = _prepared(published_project, tmp_path)
    confirm(
        published_project.root, path,
        code="secret_scan", operator_id="operator-a", decision="PASS",
    )
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, path,
            code="secret_scan", operator_id="operator-a", decision="PASS",
        )
    assert error.value.category == "already_signed"


def test_a_queue_requires_an_exact_acknowledgement(
    published_project, tmp_path
) -> None:
    path = _prepared(published_project, tmp_path)
    expected = _queue_length(published_project, "trading_rule_effective_dates")
    assert expected > 0
    with pytest.raises(WorksheetError) as missing:
        confirm(
            published_project.root, path,
            code="trading_rule_effective_dates",
            operator_id="operator-a", decision="PASS",
        )
    assert missing.value.category == "acknowledgement_required"
    with pytest.raises(WorksheetError) as wrong:
        confirm(
            published_project.root, path,
            code="trading_rule_effective_dates",
            operator_id="operator-a", decision="PASS",
            acknowledge=expected - 1,
        )
    assert wrong.value.category == "acknowledgement_required"
    confirm(
        published_project.root, path,
        code="trading_rule_effective_dates",
        operator_id="operator-a", decision="PASS",
        acknowledge=expected,
    )


def test_an_external_input_is_stored_and_cited_second(
    published_project, tmp_path
) -> None:
    """A one-column excerpt is stored, cited second, and cannot corroborate."""
    path = _prepared(published_project, tmp_path)
    excerpt = tmp_path / "official_calendar.txt"
    excerpt.write_text(
        "".join(f"{day}\n" for day in _calendar_rows(published_project)),
        encoding="utf-8",
    )
    confirm(
        published_project.root, path,
        code="exchange_calendar_sample",
        operator_id="operator-a", decision="PASS",
        external_input=excerpt,
        acknowledge=_queue_length(published_project, "exchange_calendar_sample"),
    )
    row = _row(path, "exchange_calendar_sample")
    references = [entry["reference"] for entry in row["evidence"]]
    assert references[0].startswith("data/acceptance-worksheets/")
    assert references[1].startswith("data/acceptance-external-inputs/")
    assert (published_project.root / references[1]).read_bytes() == (
        excerpt.read_bytes()
    )
    revision = effective_revision(
        published_project.root, published_project.version, "exchange_calendar_sample"
    )
    assert revision.payload["external_input"]["reference"] == references[1]
    assert revision.payload["comparison"]["status"] == "compared"
    assert revision.payload["comparison"]["has_close_column"] is False
    assert revision.payload["strength"] == "OPERATOR_ATTESTED", (
        "a one-column excerpt cannot tell 'officially closed' from 'forgotten'"
    )


def test_a_partial_excerpt_enlarges_the_queue(published_project, tmp_path) -> None:
    """Differences are what the operator signs about, so they must be claimed.

    The acknowledgement the prepared worksheet advertised no longer suffices
    once the excerpt itself introduces 20 uncovered window days: the queue is
    recomputed at signing time from the input that was actually supplied.
    """
    path = _prepared(published_project, tmp_path)
    excerpt = tmp_path / "partial.txt"
    excerpt.write_bytes(_CALENDAR_EXCERPT)
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, path,
            code="exchange_calendar_sample",
            operator_id="operator-a", decision="PASS",
            external_input=excerpt,
            acknowledge=_queue_length(
                published_project, "exchange_calendar_sample"
            ),
        )
    assert error.value.category == "acknowledgement_required"


def test_a_faithful_two_column_excerpt_corroborates(
    published_project, tmp_path
) -> None:
    """The only way to EXTERNAL_CORROBORATED: cover every window open day."""
    path = _prepared(published_project, tmp_path)
    rows = _calendar_rows(published_project)
    excerpt = tmp_path / "official_calendar.txt"
    excerpt.write_text(
        "".join(f"{day} 1\n" for day in rows), encoding="utf-8"
    )
    confirm(
        published_project.root, path,
        code="exchange_calendar_sample",
        operator_id="operator-a", decision="PASS",
        external_input=excerpt,
        acknowledge=len(rows),
    )
    revision = effective_revision(
        published_project.root, published_project.version, "exchange_calendar_sample"
    )
    comparison = revision.payload["comparison"]
    assert comparison["has_close_column"] is True
    assert comparison["dataset_open_official_absent"] == []
    assert comparison["official_open_dataset_absent"] == []
    assert comparison["official_closed_dataset_open"] == []
    assert revision.payload["strength"] == "EXTERNAL_CORROBORATED"
    assert revision.payload["queue"], "the full candidate set was still queued"


def test_an_external_input_for_a_mechanisable_code_is_refused(
    published_project, tmp_path
) -> None:
    path = _prepared(published_project, tmp_path)
    excerpt = tmp_path / "official_calendar.txt"
    excerpt.write_bytes(_CALENDAR_EXCERPT)
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, path,
            code="secret_scan",
            operator_id="operator-a", decision="PASS",
            external_input=excerpt,
        )
    assert error.value.category == "external_input_invalid"


def test_a_mechanisable_row_without_a_pack_is_refused(
    published_project, tmp_path, monkeypatch
) -> None:
    """No published artifact means nothing the program can cite.

    ``confirm`` may not generate evidence, so signing here would leave the row
    citing only its own revision -- a signature standing in for its evidence.
    This also pins the exemption's other half: rows left by a *failed* evidence
    build (placeholder summary, empty evidence) are an acceptable state for the
    other eight rows, not a drift.
    """
    from stock_quant.research.acceptance import evidence

    def _fail(*args, **kwargs):
        raise evidence.EvidenceBuildError("dataset_unreadable")

    monkeypatch.setattr(
        "stock_quant.research.acceptance.service.build_mechanisable_evidence", _fail
    )
    path = _prepared(published_project, tmp_path)
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, path,
            code="secret_scan", operator_id="operator-a", decision="PASS",
        )
    assert error.value.category == "candidate_evidence_missing"


def test_a_missing_pending_worksheet_is_refused(
    published_project, tmp_path
) -> None:
    path = _prepared(published_project, tmp_path)
    pending_path(
        published_project.root, published_project.version, "secret_scan"
    ).unlink()
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, path,
            code="secret_scan", operator_id="operator-a", decision="PASS",
        )
    assert error.value.category == "worksheet_missing"


def test_fail_requires_a_conclusion(published_project, tmp_path) -> None:
    path = _prepared(published_project, tmp_path)
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, path,
            code="secret_scan", operator_id="operator-a", decision="FAIL",
        )
    assert error.value.category == "conclusion_required"
    confirm(
        published_project.root, path,
        code="secret_scan", operator_id="operator-a", decision="FAIL",
        conclusion="row counts do not match the sample",
    )
    assert _row(path, "secret_scan")["status"] == "FAIL"


def test_confirm_refuses_when_another_row_was_edited_by_hand(
    published_project, tmp_path
) -> None:
    """The three-state exemption accepts a fresh row or a bound signed row."""
    import yaml

    path = _prepared(published_project, tmp_path)
    payload = _payload(path)
    edited = next(
        row for row in payload["manual_checks"] if row["code"] != "secret_scan"
    )
    edited["summary"] = "hand-written by an operator"
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, path,
            code="secret_scan", operator_id="operator-a", decision="PASS",
        )
    assert error.value.category == "signed_worksheet_drift"


def test_supersede_appends_without_touching_the_old_revision(
    published_project, tmp_path
) -> None:
    path = _prepared(published_project, tmp_path)
    confirm(
        published_project.root, path,
        code="secret_scan", operator_id="operator-a", decision="PASS",
    )
    first = effective_revision(
        published_project.root, published_project.version, "secret_scan"
    )
    before = first.path.read_bytes()
    confirm(
        published_project.root, path,
        code="secret_scan", operator_id="operator-b", decision="FAIL",
        conclusion="the first judgement was wrong", supersede=True,
    )
    chain = revision_chain(
        published_project.root, published_project.version, "secret_scan"
    )
    assert [row.reference for row in chain] == [first.reference, chain[-1].reference]
    assert first.path.read_bytes() == before
    assert chain[-1].decision == "FAIL"
    assert chain[-1].supersedes == first.reference
    assert chain[-1].operator_id == "operator-b"
    assert chain[-1].payload["supersedes"] == {
        "reference": first.reference,
        "sha256": first.sha256,
    }
    row = _row(path, "secret_scan")
    assert row["status"] == "FAIL"
    assert row["evidence"][0]["reference"] == chain[-1].reference
    assert row["summary"] == "operator rejected on worksheet revision", (
        "a superseded row carries no stale conclusion: the reason lives in the "
        "revision it belongs to"
    )
    assert "the first judgement was wrong" in chain[-1].human
    assert "the first judgement was wrong" not in first.human


def test_supersede_without_a_signed_row_is_refused(
    published_project, tmp_path
) -> None:
    path = _prepared(published_project, tmp_path)
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, path,
            code="secret_scan", operator_id="operator-a", decision="PASS",
            conclusion="nothing to replace", supersede=True,
        )
    assert error.value.category == "nothing_to_supersede"


def test_supersede_refuses_a_stale_baseline(
    published_project, tmp_path
) -> None:
    """A checklist left behind by another supersede must not flip the row back.

    The row of the stale copy still binds the revision it signed; the chain
    head has moved on.  Accepting that copy would publish a decision against
    a worksheet the operator never saw as current.
    """
    import shutil

    path = _prepared(published_project, tmp_path)
    stale = tmp_path / "stale.yml"
    confirm(
        published_project.root, path,
        code="secret_scan", operator_id="operator-a", decision="PASS",
    )
    shutil.copy(path, stale)
    confirm(
        published_project.root, path,
        code="secret_scan", operator_id="operator-a", decision="FAIL",
        conclusion="second thoughts", supersede=True,
    )
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, stale,
            code="secret_scan", operator_id="operator-a", decision="PASS",
            conclusion="third thoughts", supersede=True,
        )
    assert error.value.category == "superseded_revision_drift"


def test_confirm_refuses_an_unknown_code(published_project, tmp_path) -> None:
    path = _prepared(published_project, tmp_path)
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, path,
            code="not_a_check", operator_id="operator-a", decision="PASS",
        )
    assert error.value.category == "unknown_check_code"


def test_every_operator_only_code_accepts_its_own_signing(
    published_project, tmp_path
) -> None:
    path = _prepared(published_project, tmp_path)
    for code in OPERATOR_ONLY_CODES:
        confirm(
            published_project.root, path,
            code=code, operator_id="operator-a", decision="PASS",
            acknowledge=_queue_length(published_project, code),
        )
    payload = _payload(path)
    assert all(
        row["status"] == "PASS"
        for row in payload["manual_checks"]
        if row["code"] in OPERATOR_ONLY_CODES
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n py310 python -m pytest tests/unit/test_acceptance_worksheet_confirm.py -q`
Expected: FAIL — `ImportError: cannot import name 'confirm' from 'stock_quant.research.acceptance.worksheet'`

- [ ] **Step 3: Write the implementation**

追加到 `src/stock_quant/research/acceptance/worksheet.py`（把 `ConfirmationRequest`、`apply_confirmation`、`confirm` 补进 `__all__`），并在文件顶部补 `import yaml` 之外所需的导入：`from datetime import datetime, timezone`、`from stock_quant.research.acceptance.models import (MANUAL_CHECK_CODES, OPERATOR_ONLY_CODES, AcceptanceChecklist, EvidenceReference, ManualCheckResult, ManualCheckStatus)`。

```python
#: The two decisions a signature block may carry.
_PASS = "PASS"
_FAIL = "FAIL"


@dataclass(frozen=True)
class ConfirmationRequest:
    """Everything one ``confirm`` invocation decided, in one value."""

    code: str
    operator_id: str
    decision: str
    conclusion: str
    strength: str
    supersede: bool
    evidence: tuple[EvidenceReference, ...]
    confirmed_at: str


def apply_confirmation(
    checklist: AcceptanceChecklist,
    request: ConfirmationRequest,
    *,
    effective: Revision | None,
) -> AcceptanceChecklist:
    """The pure kernel: flip exactly one manual row, change nothing else.

    The checklist is *copied*, never rebuilt, so every untouched row stays
    byte-identical and no container field can move.  The guards here are the
    same ones ``confirm`` already applied -- a kernel that trusted its caller
    would be one refactor away from being the second write path.
    """
    if request.code not in MANUAL_CHECK_CODES:
        raise WorksheetError("unknown_check_code")
    if request.decision not in (_PASS, _FAIL):
        raise WorksheetError("unknown_check_code")
    rows = {row.code: row for row in checklist.manual_checks}
    if request.code not in rows:
        raise WorksheetError("unknown_check_code")
    row = rows[request.code]
    if request.supersede:
        # ``nothing_to_supersede`` is decided by the caller, which is the only
        # party that can tell "no revision yet" from "a stale baseline": here
        # the row state alone is enough to refuse an unsigned one.
        if row.status is ManualCheckStatus.PENDING_CONFIRMATION or effective is None:
            raise WorksheetError("nothing_to_supersede")
        if not row.evidence or row.evidence[0].reference != effective.reference:
            raise WorksheetError("superseded_revision_drift")
    else:
        if row.status is not ManualCheckStatus.PENDING_CONFIRMATION:
            raise WorksheetError("already_signed")
        if effective is not None:
            raise WorksheetError("already_signed")
    updated = row.model_copy(
        update={
            "status": ManualCheckStatus(request.decision),
            "summary": SIGNED_SUMMARY[request.decision],
            "evidence": request.evidence,
        }
    )
    return checklist.model_copy(
        update={
            "manual_checks": tuple(
                updated if other.code == request.code else other
                for other in checklist.manual_checks
            )
        }
    )
```

```python
def confirm(
    project_root: Path,
    checklist_path: Path,
    *,
    code: str,
    operator_id: str,
    decision: str,
    conclusion: str | None = None,
    external_input: Path | None = None,
    acknowledge: int | None = None,
    supersede: bool = False,
    now: datetime | None = None,
) -> AcceptanceChecklist:
    """Confirm or reject one manual row, appending one immutable revision.

    Seven steps, and the order *is* the safety property: the revision is
    written and hashed before the checklist cites it, so a crash between the
    two leaves a revision the checklist has not caught up with -- recoverable
    with ``prepare --force`` -- and never a checklist pointing at a file that
    does not exist.
    """
    from stock_quant.research.acceptance.evidence import read_pack_references
    from stock_quant.research.acceptance.external_inputs import (
        EXCERPT_CODES,
        compare_for_code,
        store_blob,
        version_facts,
    )
    from stock_quant.research.acceptance.service import (
        binding_reasons,
        build_checklist,
        write_checklist_atomic,
    )
    from stock_quant.research.acceptance.worksheet_prepare import (
        candidate_evidence,
        previous_candidate_rows,
        previous_signed_payload,
        revision_reference,
        window_of,
    )
    from stock_quant.research.acceptance.worksheet_program import (
        build_program,
        candidate_rows,
        comparison_for_checklist,
        review_queue,
        strength_for,
    )

    root = Path(project_root).resolve()
    if code not in MANUAL_CHECK_CODES:
        raise WorksheetError("unknown_check_code")
    if decision not in (_PASS, _FAIL):
        raise WorksheetError("unknown_check_code")

    # Step 1: read the checklist, then re-verify every binding and every other
    # manual row.  A malformed checklist raises the model's own error: the CLI
    # turns that into one stable ``invalid_checklist`` reason.
    checklist = AcceptanceChecklist.model_validate(
        yaml.safe_load(Path(checklist_path).read_text(encoding="utf-8"))
    )
    version = checklist.dataset_version
    fresh = build_checklist(
        root, version, checklist.operator_id, prepared_at=checklist.prepared_at
    )
    reasons = binding_reasons(checklist, fresh)
    reasons.extend(_other_rows_reasons(root, checklist, fresh, code))
    if reasons:
        raise WorksheetError("signed_worksheet_drift")

    # Step 2: this row and its chain must be in the state the request needs.
    effective = effective_revision(root, version, code)
    row = next(item for item in checklist.manual_checks if item.code == code)
    if supersede:
        if row.status is ManualCheckStatus.PENDING_CONFIRMATION or effective is None:
            raise WorksheetError("nothing_to_supersede")
        if not row.evidence or row.evidence[0].reference != effective.reference:
            raise WorksheetError("superseded_revision_drift")
        if not conclusion:
            raise WorksheetError("conclusion_required")
    elif row.status is not ManualCheckStatus.PENDING_CONFIRMATION or effective is not None:
        raise WorksheetError("already_signed")
    elif decision == _FAIL and not conclusion:
        raise WorksheetError("conclusion_required")

    # Step 3: land the external input, then recompute what this signing is
    # judged against.  The queue is computed *here*, after the input exists,
    # and is what ``--acknowledge`` is compared against -- never the number a
    # prepared worksheet happened to print.
    start, end = window_of(root, version)
    facts = version_facts(root, version, start, end)
    rows_by_code = candidate_rows(facts)
    source: Path | None = None
    data: bytes | None = None
    if external_input is not None:
        if code not in EXCERPT_CODES:
            raise WorksheetError("external_input_invalid")
        source = Path(external_input)
        try:
            data = source.read_bytes()
        except OSError as error:
            raise WorksheetError("external_input_invalid") from error
    # Parse before storing: a malformed excerpt is refused, not preserved in
    # the append-only store, and never becomes something a row can cite.
    comparison = compare_for_code(code, facts, data)
    stored_input = (
        None if data is None or source is None else store_blob(root, data, source.name)
    )
    strength = strength_for(code, comparison)
    previous = latest_pass_revision(root, code, exclude_version=version)
    queue = (
        review_queue(
            code,
            rows_by_code[code],
            previous_rows=(
                previous_candidate_rows(root, previous)
                if previous is not None
                else None
            ),
            supersede=supersede,
            extra=_comparison_queue(comparison),
        )
        if code in OPERATOR_ONLY_CODES
        else ()
    )
    if len(queue) != (acknowledge if acknowledge is not None else 0):
        raise WorksheetError("acknowledgement_required")
    pack_references = read_pack_references(root, version)
    candidate = candidate_evidence(root, code, rows_by_code, pack_references)
    if code in MECHANISABLE_CODES and not candidate:
        # ``confirm`` may not generate evidence, so a mechanisable row with no
        # published artifact cannot be turned into PASS by this command at all:
        # signing it would produce a checklist row whose only citation is this
        # very revision, i.e. a signature standing in for its own evidence.
        raise WorksheetError("candidate_evidence_missing")

    # Steps 4-5: the baseline human area, its markers, then the new signature.
    baseline = _baseline_human(root, version, code, effective, supersede)
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    program = build_program(
        code=code,
        dataset_version=version,
        dataset_manifest_sha256=checklist.dataset_manifest_sha256,
        window={"start": start.isoformat(), "end": end.isoformat()},
        generated_at=stamp,
        candidate=candidate,
        previous_signed=previous_signed_payload(previous),
        supersedes=(
            {"reference": effective.reference, "sha256": effective.sha256}
            if supersede and effective is not None
            else None
        ),
        external_input=(
            {"reference": stored_input.reference, "sha256": stored_input.sha256}
            if stored_input is not None
            else None
        ),
        comparison=comparison_for_checklist(code, comparison),
        strength=strength,
        queue=queue,
    )
    human = append_signature(
        baseline,
        {
            "operator_id": operator_id,
            "confirmed_at": stamp,
            "decision": decision,
            "strength": strength,
            "conclusion": conclusion or "operator confirmed on worksheet revision",
            "supersedes": effective.reference if supersede and effective else None,
        },
    )

    # Step 6: write the revision.  ``write_revision`` refuses to produce a file
    # whose name disagrees with its bytes, and reuses an identical one.
    revision = write_revision(root, version, code, program, human)

    # Step 7: the revision is in place, so the checklist may now cite it.
    evidence = [revision_reference(revision)]
    if stored_input is not None:
        evidence.append(
            EvidenceReference(
                kind="local",
                reference=stored_input.reference,
                sha256=stored_input.sha256,
                summary=f"{code} external input",
            )
        )
    updated = apply_confirmation(
        checklist,
        ConfirmationRequest(
            code=code,
            operator_id=operator_id,
            decision=decision,
            conclusion=conclusion or "",
            strength=strength,
            supersede=supersede,
            evidence=tuple(evidence),
            confirmed_at=stamp,
        ),
        effective=effective,
    )
    pending_path(root, version, code).unlink(missing_ok=True)
    write_checklist_atomic(updated, Path(checklist_path))
    return updated


#: How ``prepare`` marks a mechanisable row whose evidence build failed.
_EVIDENCE_FAILURE_PREFIX = "evidence generation failed: "


def _other_rows_reasons(
    root: Path,
    checklist: AcceptanceChecklist,
    fresh: AcceptanceChecklist,
    code: str,
) -> list[str]:
    """Why the rows *other* than ``code`` are not in an acceptable state.

    Exactly three states are acceptable:

    * an **unsigned** row -- status ``PENDING_CONFIRMATION`` and the summary
      ``prepare`` writes (the placeholder text ``build_checklist`` produces, or
      its ``evidence generation failed:`` form).  The evidence is deliberately
      *not* compared against ``fresh``: ``build_checklist`` is pure, so the
      fresh row always carries empty evidence while the prepared row carries
      the pack reference ``prepare`` legitimately attached.  A reference that
      does not verify is caught below instead;
    * a **signed** row whose first reference is this code's *effective*
      revision and whose every reference still verifies.  A ``FAIL`` row is
      acceptable here: a rejection is a signature, not an error;
    * anything else is a reason to refuse.

    This cannot reuse the publish path's reasons, which treat ``FAIL`` as a
    rejection and would therefore reject every confirm that follows one.
    """
    reasons: list[str] = []
    fresh_rows = {item.code: item for item in fresh.manual_checks}
    for row in checklist.manual_checks:
        if row.code == code:
            continue
        if row.status is ManualCheckStatus.PENDING_CONFIRMATION:
            expected = fresh_rows[row.code].summary
            if row.summary != expected and not row.summary.startswith(
                _EVIDENCE_FAILURE_PREFIX
            ):
                reasons.append(f"manual_{row.code}_placeholder_changed")
            reasons.extend(
                f"manual_{row.code}_{reason}"
                for reason in _unverifiable(root, row.evidence)
            )
            continue
        revision = effective_revision(root, checklist.dataset_version, row.code)
        if (
            revision is None
            or not row.evidence
            or row.evidence[0].reference != revision.reference
        ):
            reasons.append(f"manual_{row.code}_unbound_worksheet")
            continue
        reasons.extend(
            f"manual_{row.code}_{reason}"
            for reason in _unverifiable(root, row.evidence)
        )
    return reasons


def _unverifiable(
    root: Path, evidence: tuple[EvidenceReference, ...]
) -> list[str]:
    """The reason each of these references fails to verify, if any."""
    from stock_quant.research.acceptance.service import verify_evidence_reference

    return [
        reason
        for reference in evidence
        if (reason := verify_evidence_reference(root, reference)) is not None
    ]


def _baseline_human(
    project_root: Path,
    dataset_version: str,
    code: str,
    effective: Revision | None,
    supersede: bool,
) -> str:
    """The human area a new revision carries forward, byte for byte.

    A supersede continues the revision it replaces; a first signing continues
    the pending worksheet ``prepare`` wrote.  A pending worksheet without
    human markers is a hard failure -- creating one would be guessing at the
    content that is supposed to be carried forward.
    """
    if supersede:
        if effective is None:
            raise WorksheetError("nothing_to_supersede")
        return effective.human
    path = pending_path(project_root, dataset_version, code)
    if not path.is_file():
        raise WorksheetError("worksheet_missing")
    _, human = verify_markers(path.read_text(encoding="utf-8"))
    return human


def _comparison_queue(comparison: object) -> tuple[str, ...]:
    """The excerpt-side rows an operator must look at for one comparison."""
    return tuple(
        f"official:{key}" for key in getattr(comparison, "queue_rows", ())
    )
```

`_comparison_queue` 靠 `queue_rows` 把「官方摘录未覆盖 / 与配置冲突 / 与日历不一致」的条目并入队列；这正是「摘录允许覆盖子集」的代价被显式入队的地方。

**CLI**：在 `src/stock_quant/cli.py` 的 `acceptance_app` 下新增子命令：

```python
@acceptance_app.command("confirm")
def data_acceptance_confirm(
    checklist: Annotated[
        Path, typer.Option("--checklist", help="Checklist YAML file.")
    ],
    code: Annotated[
        str, typer.Option("--code", help="Manual check code to confirm.")
    ],
    operator: Annotated[
        str, typer.Option("--operator", help="Signing operator id.")
    ],
    external_input: Annotated[
        Path | None,
        typer.Option(
            "--external-input",
            help=(
                "Official excerpt for an external check. Copied into the "
                "project's content-addressed input store."
            ),
        ),
    ] = None,
    acknowledge: Annotated[
        int | None,
        typer.Option(
            "--acknowledge",
            help="Number of queued rows the operator reviewed before signing.",
        ),
    ] = None,
    fail: Annotated[
        bool, typer.Option("--fail", help="Reject this check.")
    ] = False,
    supersede: Annotated[
        bool,
        typer.Option(
            "--supersede",
            help="Replace this check's signed revision. Never overwrites it.",
        ),
    ] = False,
    conclusion: Annotated[
        str | None,
        typer.Option("--conclusion", help="Signature conclusion text."),
    ] = None,
    conclusion_file: Annotated[
        Path | None,
        typer.Option("--conclusion-file", help="Read the conclusion from a file."),
    ] = None,
    root: Path = typer.Option(".", "--root", help="Project root."),
) -> None:
    """Confirm or reject one manual check, appending one worksheet revision.

    Read-only on everything else: no row is added, no automated row changes,
    no evidence file is generated, and the container's ``operator_id`` stays
    where ``prepare`` put it.  Only the named row flips, and only after the
    worksheet revision citing it is in place.
    """
    if conclusion is not None and conclusion_file is not None:
        typer.echo("reason=conclusion_required")
        raise typer.Exit(code=1)
    text = (
        conclusion_file.read_text(encoding="utf-8")
        if conclusion_file is not None
        else conclusion
    )
    try:
        updated = confirm(
            _resolved_project_root(root),
            checklist,
            code=code,
            operator_id=operator,
            decision="FAIL" if fail else "PASS",
            conclusion=text,
            external_input=external_input,
            acknowledge=acknowledge,
            supersede=supersede,
        )
    except WorksheetError as error:
        typer.echo(f"reason={error.category}")
        raise typer.Exit(code=1) from None
    except (OSError, ValueError, ValidationError) as error:
        typer.echo("reason=invalid_checklist")
        typer.echo(f"error={type(error).__name__}")
        raise typer.Exit(code=1) from None
    row = next(item for item in updated.manual_checks if item.code == code)
    typer.echo(f"code={code}")
    typer.echo(f"decision={row.status.value}")
    typer.echo(f"evidence={row.evidence[0].reference}")
```

`confirm` 与 `WorksheetError`、`ValidationError` 按文件内既有风格加入 import。

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n py310 python -m pytest tests/unit/test_acceptance_worksheet_confirm.py -q`
Expected: PASS（17 passed）

- [ ] **Step 5: Commit**

```bash
git add src/stock_quant/research/acceptance/worksheet.py src/stock_quant/cli.py \
        tests/unit/test_acceptance_worksheet_confirm.py
git commit -m "feat: make confirm the only writer of manual acceptance rows"
```

---

### Task 8: 端到端（九项全签 → ACCEPTED → supersede → REJECTED）与漂移回归

**Files:**
- Create: `tests/integration/test_acceptance_confirm_line.py`
- Modify: `tests/unit/test_acceptance_worksheet_prepare.py`（追加漂移回归）

**Interfaces:**
- Consumes: Task 1–7 的全部公开名字；`tests/integration/conftest.py` 的 `build_fixture_project`、`cli_runner`
- Produces: 无新接口

- [ ] **Step 1: Write the failing integration test**

新建 `tests/integration/test_acceptance_confirm_line.py`：

```python
"""End-to-end behaviour of the worksheet confirm line (Task 8).

One real published fixture version, nine real ``confirm`` invocations through
the CLI, one real ``publish``: the loop an operator actually runs.  The two
external checks are signed with faithful excerpts built from the version's own
calendar and the shipped rule configuration, so both must reach
``EXTERNAL_CORROBORATED``.  A supersede to FAIL must flip the published
decision to REJECTED, a second supersede must flip it back, and the *first*
ACCEPTED record must still verify -- the whole point of an append-only chain.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml
from conftest import build_fixture_project  # noqa: E402

from stock_quant.cli import app  # noqa: E402
from stock_quant.data_model.dataset import DatasetReader  # noqa: E402
from stock_quant.research.acceptance.checks import _open_days  # noqa: E402
from stock_quant.research.acceptance.models import (  # noqa: E402
    MANUAL_CHECK_CODES,
    OPERATOR_ONLY_CODES,
)
from stock_quant.research.acceptance.registry import (  # noqa: E402
    AcceptanceRegistry,
)
from stock_quant.research.acceptance.service import (  # noqa: E402
    verify_acceptance_bindings,
)
from stock_quant.research.acceptance.worksheet import (  # noqa: E402
    effective_revision,
    pending_path,
    program_payload,
    revision_chain,
    verify_markers,
)

_OPERATOR = "e2e-operator"


@pytest.fixture
def confirmed_project(tmp_path):
    """One trusted fixture project, prepared for signing."""
    project = build_fixture_project(tmp_path / "e2e")
    checklist = tmp_path / "checklist.yml"
    return project, checklist


def _run(cli_runner, *args: str):
    return cli_runner.invoke(app, list(args))


def _program(root: Path, version: str, code: str) -> dict:
    """One pending worksheet's program area, as the operator reads it."""
    text = pending_path(root, version, code).read_text(encoding="utf-8")
    return program_payload(verify_markers(text)[0])


def _queue_length(root: Path, version: str, code: str) -> int:
    """The queue a pending worksheet publishes, read from the file itself."""
    return len(_program(root, version, code).get("queue") or ())


def _calendar_excerpt(root: Path, version: str, path: Path) -> Path:
    """A two-column official calendar covering exactly the worksheet window.

    Transcribed from the version's own ``trading_calendar``, which is what an
    operator comparing against the official file does, and clipped to the
    window the worksheet declares: an excerpt listing the whole 2018-2022
    calendar would put every out-of-window day into the queue as an
    "official day the dataset does not have".
    """
    window = _program(root, version, "exchange_calendar_sample")["window"]
    start = date.fromisoformat(str(window["start"]))
    end = date.fromisoformat(str(window["end"]))
    with DatasetReader(root).open(version) as dataset:
        calendar = dataset.read("trading_calendar")
    lines = sorted(
        f"{day.isoformat()} 1"
        for day in _open_days(calendar)
        if start <= day <= end
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _rules_excerpt(root: Path, path: Path) -> Path:
    """A CSV covering every declared price-limit row in ``trading_rules.yml``."""
    payload = yaml.safe_load(
        (root / "configs" / "trading_rules.yml").read_text(encoding="utf-8")
    )
    rows = ["board,status,effective_from,rate,source_url"]
    for entry in payload["price_limits"]:
        boards = entry["boards"]
        boards = boards if isinstance(boards, list) else [boards]
        statuses = entry["status"]
        statuses = statuses if isinstance(statuses, list) else [statuses]
        for board in boards:
            for status in statuses:
                rows.append(
                    f"{board},{status},{entry['effective_from']},"
                    f"{entry['rate']},https://example.invalid/official"
                )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_the_nine_step_confirm_line_publishes_accepted(
    cli_runner, confirmed_project, tmp_path
) -> None:
    project, checklist = confirmed_project
    root, version = project.root, project.version
    assert (
        _run(
            cli_runner,
            "data", "acceptance", "prepare",
            "--version", version, "--operator", _OPERATOR,
            "--output", str(checklist), "--root", str(root),
        ).exit_code
        == 0
    )

    excerpts = {
        "exchange_calendar_sample": _calendar_excerpt(
            root, version, tmp_path / "official_calendar.txt"
        ),
        "trading_rule_effective_dates": _rules_excerpt(
            root, tmp_path / "official_rules.csv"
        ),
    }
    for code in MANUAL_CHECK_CODES:
        args = [
            "data", "acceptance", "confirm",
            "--checklist", str(checklist), "--code", code,
            "--operator", _OPERATOR,
        ]
        if code in excerpts:
            args += ["--external-input", str(excerpts[code])]
        if code in OPERATOR_ONLY_CODES:
            args += ["--acknowledge", str(_queue_length(root, version, code))]
        result = _run(cli_runner, *args)
        assert result.exit_code == 0, (code, result.stdout)

    for code in excerpts:
        revision = effective_revision(root, version, code)
        assert revision.payload["strength"] == "EXTERNAL_CORROBORATED", code
        assert revision.payload["comparison"]["status"] == "compared", code

    published = _run(
        cli_runner,
        "data", "acceptance", "publish",
        "--checklist", str(checklist), "--root", str(root),
    )
    assert published.exit_code == 0, published.stdout
    assert "decision=ACCEPTED" in published.stdout
    accepted = AcceptanceRegistry(root).list(version)[-1]
    assert accepted.decision.value == "ACCEPTED"
    # Raises AcceptanceBindingError on any drift; the nine rows must cite
    # artefacts that are all still exactly where they were signed.
    verify_acceptance_bindings(root, accepted)


def test_supersede_flips_the_published_decision_and_keeps_history(
    cli_runner, confirmed_project, tmp_path
) -> None:
    project, checklist = confirmed_project
    root, version = project.root, project.version
    _run(
        cli_runner,
        "data", "acceptance", "prepare",
        "--version", version, "--operator", _OPERATOR,
        "--output", str(checklist), "--root", str(root),
    )
    for code in MANUAL_CHECK_CODES:
        args = [
            "data", "acceptance", "confirm",
            "--checklist", str(checklist), "--code", code,
            "--operator", _OPERATOR,
        ]
        if code == "exchange_calendar_sample":
            args += [
                "--external-input",
                str(_calendar_excerpt(root, version, tmp_path / "cal.txt")),
            ]
        if code in OPERATOR_ONLY_CODES:
            args += ["--acknowledge", str(_queue_length(root, version, code))]
        assert _run(cli_runner, *args).exit_code == 0
    assert (
        _run(
            cli_runner, "data", "acceptance", "publish",
            "--checklist", str(checklist), "--root", str(root),
        ).exit_code
        == 0
    )
    first = effective_revision(root, version, "secret_scan")
    first_acceptance = AcceptanceRegistry(root).list(version)[-1]
    assert first_acceptance.decision.value == "ACCEPTED"
    first_bytes = first.path.read_bytes()

    rejected = _run(
        cli_runner,
        "data", "acceptance", "confirm",
        "--checklist", str(checklist), "--code", "secret_scan",
        "--operator", _OPERATOR, "--fail", "--supersede",
        "--conclusion", "the sampled rows do not cover the window",
        "--root", str(root),
    )
    assert rejected.exit_code == 0, rejected.stdout
    republished = _run(
        cli_runner, "data", "acceptance", "publish",
        "--checklist", str(checklist), "--root", str(root),
    )
    assert republished.exit_code != 0
    assert "decision=REJECTED" in republished.stdout
    assert "manual_secret_scan_failed" in republished.stdout

    restored = _run(
        cli_runner,
        "data", "acceptance", "confirm",
        "--checklist", str(checklist), "--code", "secret_scan",
        "--operator", _OPERATOR, "--supersede",
        "--conclusion", "the window coverage was re-checked and holds",
        "--root", str(root),
    )
    assert restored.exit_code == 0, restored.stdout
    final = _run(
        cli_runner, "data", "acceptance", "publish",
        "--checklist", str(checklist), "--root", str(root),
    )
    assert final.exit_code == 0, final.stdout
    assert "decision=ACCEPTED" in final.stdout

    chain = revision_chain(root, version, "secret_scan")
    assert len(chain) == 3
    assert chain[0].reference == first.reference
    assert first.path.read_bytes() == first_bytes
    assert chain[1].supersedes == first.reference
    assert chain[2].supersedes == chain[1].reference
    # The load-bearing assertion of the whole line: the first ACCEPTED record
    # still cites its own revisions, and they were never rewritten, moved or
    # deleted by either supersede.  Raises AcceptanceBindingError otherwise.
    verify_acceptance_bindings(root, first_acceptance)
    rejected_record = AcceptanceRegistry(root).list(version)[-2]
    assert rejected_record.decision.value == "REJECTED"
    assert "manual_secret_scan_failed" in rejected_record.reasons
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n py310 python -m pytest tests/integration/test_acceptance_confirm_line.py -q`
Expected: FAIL — `reason=invalid_checklist` 或 `AssertionError`（`confirm` 子命令尚不存在时 Typer 直接报 `No such command 'confirm'`）

- [ ] **Step 3: Write the drift regressions in the unit suite**

追加到 `tests/unit/test_acceptance_worksheet_prepare.py`：

```python
def test_a_tampered_revision_breaks_its_own_chain(published_project, tmp_path) -> None:
    from stock_quant.research.acceptance.worksheet import revision_chain

    _prepare(published_project, tmp_path, "checklist.yml")
    revision = _sign(published_project, "secret_scan")
    revision.path.chmod(0o644)
    revision.path.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(WorksheetError) as error:
        revision_chain(
            published_project.root, published_project.version, "secret_scan"
        )
    assert error.value.category == "revision_chain_invalid"


def test_an_unexpected_file_in_a_revision_directory_is_rejected(
    published_project, tmp_path
) -> None:
    from stock_quant.research.acceptance.worksheet import revisions_dir

    _prepare(published_project, tmp_path, "checklist.yml")
    _sign(published_project, "secret_scan")
    stray = (
        revisions_dir(published_project.root, published_project.version, "secret_scan")
        / "operator_notes.md"
    )
    stray.write_text("notes\n", encoding="utf-8")
    with pytest.raises(WorksheetError) as error:
        _prepare(published_project, tmp_path, "forced.yml", force=True)
    assert error.value.category == "revision_chain_invalid"
    assert stray.exists(), "the stray file is reported, never swept away"
    assert not (tmp_path / "forced.yml").exists()


def test_worksheets_are_written_outside_the_version_evidence_pack(
    published_project, tmp_path
) -> None:
    _prepare(published_project, tmp_path, "checklist.yml")
    pack = (
        published_project.root
        / "data"
        / "acceptance-evidence"
        / published_project.version
    )
    assert pack.is_dir()
    assert not (pack / "acceptance-worksheets").exists()
    assert not (pack / "secret_scan.md").exists()


def test_rebuilding_the_evidence_pack_leaves_its_bytes_unchanged(
    published_project, tmp_path
) -> None:
    """The pack is rebuilt on every prepare; its bytes must not drift.

    A signed checklist row cites pack files by hash, so a rebuild that changed
    a single byte would break published records retroactively.  This is also
    why the candidate snapshots of the operator-only codes were put in the
    external-input store instead of in the pack.
    """
    from stock_quant.research.acceptance.evidence import (
        EVIDENCE_FILENAMES,
        build_mechanisable_evidence,
    )

    _prepare(published_project, tmp_path, "checklist.yml")
    pack = (
        published_project.root
        / "data"
        / "acceptance-evidence"
        / published_project.version
    )
    before = {
        name: hashlib.sha256((pack / name).read_bytes()).hexdigest()
        for name in EVIDENCE_FILENAMES.values()
    }
    build_mechanisable_evidence(published_project.root, published_project.version)
    after = {
        name: hashlib.sha256((pack / name).read_bytes()).hexdigest()
        for name in EVIDENCE_FILENAMES.values()
    }
    assert before == after
```

（把 `import hashlib` 加到该测试文件头部。）

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n py310 python -m pytest tests/integration/test_acceptance_confirm_line.py -q`
Expected: PASS（2 passed）

Run: `conda run -n py310 python -m pytest tests/unit/test_acceptance_worksheet_prepare.py -q`
Expected: PASS（12 passed）

- [ ] **Step 5: 回归既有验收与 CLI 套件**

Run: `conda run -n py310 python -m pytest tests/unit/test_acceptance_service.py tests/unit/test_acceptance_models.py tests/unit/test_acceptance_evidence.py tests/unit/test_acceptance_checks.py tests/integration/test_acceptance_cli.py tests/integration/test_acceptance_registry.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_acceptance_confirm_line.py \
        tests/unit/test_acceptance_worksheet_prepare.py
git commit -m "test: cover the nine-step confirm line end to end"
```

---

### Task 9: RUNBOOK 与 ruff 收尾

**Files:**
- Modify: `project/RUNBOOK.md`（阶段 4.5）
- Modify: `docs/operations/phase-one-validation.md`（§4 人工核对项）

**Interfaces:**
- Consumes: Task 1–8 的 CLI 行为
- Produces: 无代码接口

- [ ] **Step 1: 改写 RUNBOOK 阶段 4.5 的验收流程**

把「审核者逐项审阅六项证据、补齐三项外部佐证，然后把九项 manual 全部改为 PASS」一段替换为逐项 `confirm` 的流程，并写明四条操作事实：

```markdown
# 生成九份工作表（同版本重跑会失败；已签版本要用 --force 恢复清单）
python -m stock_quant data acceptance prepare --version <VERSION> \
  --operator <OPERATOR_ID> --output checklist.yml --root .

# 九项逐条确认：每次一个 code，没有批量入口
python -m stock_quant data acceptance confirm --checklist checklist.yml \
  --code <CODE> --operator <OPERATOR_ID> [--external-input <FILE>] \
  [--acknowledge <N>] [--fail] [--supersede] \
  [--conclusion "<TEXT>"] --root .

python -m stock_quant data acceptance publish --checklist checklist.yml --root .
```

- 工作表在 `data/acceptance-worksheets/<VERSION>/`：`<code>.md` 是**未签**的（会被
  `prepare` 刷新），`<code>/<sha256>.md` 是**已签修订**（永不改写）。清单只绑定后者。
- 外部输入（官方日历、官方规则摘录）必须用 `--external-input` 提交，命令会把它复制到
  `data/acceptance-external-inputs/<sha256>/<原名>`；只指一个仓外路径不会被接受。
  官方日历严格档用两列格式（`日期 1|0`），单列只能得到 `OPERATOR_ATTESTED`。
- 工作表里的**待确认队列**是这次签署必须逐条过目的项。队列非空时必须 `--acknowledge <N>`
  且 `N` 恰好等于队列长度，否则拒绝（`reason=acknowledgement_required`）；六项机械项队列恒空。
  队列在签署当时用**实际提交的**外部输入重算，所以补了摘录之后 `<N>` 可能与工作表上印的不同。
- 签错了（结论文字、签署者、判据）用 `confirm --supersede` 追加一份修订，旧修订原样
  留在链上；清单顶层 `operator_id` 是 `prepare` 的发起者，要改它得重跑
  `prepare --force --operator <正确 ID>` 再 publish。
- 备份 `data/acceptances/` 时必须一并保留 `data/acceptance-evidence/`、
  `data/acceptance-worksheets/` 与 `data/acceptance-external-inputs/`，否则后续
  `research run` 会在人工证据校验上失败。
```

- [ ] **Step 2: 更新运维核对文档**

在 `docs/operations/phase-one-validation.md` §4 的人工核对项里，为三项外部佐证各补一行「用什么输入、程序给什么强度」：日历要两列官方文件；交易规则要五列 CSV（`board,status,effective_from,rate,source_url`）逐条覆盖配置；跨源价格在无法比对时（版本只有一个价格源，或两个源各自覆盖不相交的证券集合）固定为 `OPERATOR_ATTESTED`，并在工作表里用稳定原因（`single_price_source` / `no_paired_bars`）写明为什么没比。

- [ ] **Step 3: ruff 与格式门禁**

Run: `conda run -n py310 python -m ruff check src tests`
Expected: `All checks passed!`

Run: `conda run -n py310 python -m ruff format --check src/stock_quant/research/acceptance/worksheet.py src/stock_quant/research/acceptance/worksheet_program.py src/stock_quant/research/acceptance/worksheet_prepare.py src/stock_quant/research/acceptance/external_inputs.py`
Expected: `4 files already formatted`（只要求改动文件干净，不要求仓库级）

- [ ] **Step 4: 全量具名回归**

Run: `conda run -n py310 python -m pytest tests/unit -q`
Expected: PASS

Run: `conda run -n py310 python -m pytest tests/integration/test_acceptance_cli.py tests/integration/test_acceptance_registry.py tests/integration/test_acceptance_checks.py tests/integration/test_acceptance_evidence.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add project/RUNBOOK.md docs/operations/phase-one-validation.md
git commit -m "docs: document the worksheet confirm line in the runbook"
```

---

## 自查记录

**规格覆盖**（逐节对照 `2026-09-13-acceptance-standing-worksheets-design.md`）：

| 规格条目 | 落点 |
| --- | --- |
| 目标：prepare 写工作表、confirm 唯一写入口、三项外部项有契约与强度 | Task 6 / 7 / 4 / 5 |
| 非目标：不批量、不自动 PASS、不动自动行、不删已签产物 | 全局约束 + Task 7 测试「只翻一行」「不动自动行」 |
| 非目标：签名与结论不进 summary/details | Task 7 `SIGNED_SUMMARY` 常量 |
| `prepare` 非破坏性恢复 / 只读预检 / `signed_worksheets_present` / `signed_worksheet_drift` | Task 6 |
| 写路径唯一性 5 条不变式 | Task 6（预检先于写入）+ Task 7（`apply_confirmation` 拒绝容器字段） |
| 三态豁免 | Task 7 step 1（`row == fresh_row` 或已签且绑定生效修订） |
| 修订链 / 路径规则 / `supersedes` / 链头唯一 | Task 2 |
| `previous_signed` 只取 PASS、跨版本按 `confirmed_at`、并列 fail closed | Task 2 |
| 位置在证据包之外 | Task 8 测试 |
| marker fail-closed 四条 | Task 1 |
| 程序区绑定头（含 `supersedes`、`previous_signed`） | Task 5 `build_program` |
| `OPERATOR_ONLY_CODES` 追加 `external_input`/`comparison`/`strength`/queue | Task 5 |
| 待确认队列四条（六项恒空、首次全量、后续增量、跨源全量、supersede 全量） | Task 5 `review_queue` + Task 7 `_comparison_queue` |
| 确认强度表六行 | Task 5 `strength_for` + Task 4 `compare_for_code` + 三个比对器 |
| 「未覆盖行进入待确认队列，绝不静默通过」 | Task 4 `RuleComparison.queue_rows` → Task 7 `_comparison_queue` |
| 「单一价格源以稳定原因拒绝比对，不得伪装成通过」 | Task 4 `PriceComparison(reason="single_price_source" / "no_paired_bars")` |
| 外部输入落盘 + 双引用 | Task 3 + Task 7 |
| 逐 code 输入契约 | Task 4 `compare_for_code`（prepare 与 confirm 同一入口） |
| 命令与 7 步顺序 | Task 7 `confirm` + CLI |
| `--fail` 复用同一路径、`--conclusion` 必填规则 | Task 7 |
| 模块边界（常量、纯函数、写入者） | Task 1/2/5/6/7 |
| 审核者体验六步 | Task 7/9 + RUNBOOK |
| 测试要求 20 条 | Task 1/2/4/5/6/7/8 的测试步骤 |

**类型一致性**：`Revision`（Task 2）在 Task 6/7 一致使用；`StoredBlob`（Task 3）在 Task 7 用 `.reference` / `.sha256`；`VersionFacts`（Task 4）由 Task 5 `candidate_rows`、Task 4 `compare_for_code`、Task 6 `prepare_worksheets` 与 Task 7 `confirm` 共用；`compare_for_code` 在 Task 4 定义、Task 7 调用（签名 `(code, facts, data | None)`）；`window_of` / `candidate_blob` / `candidate_evidence` / `previous_candidate_rows` / `previous_signed_payload` / `revision_reference` 在 Task 6 定义（公开）、Task 7 调用，`candidate_evidence` 一律收 `(root, code, rows_by_code, pack_references)` 四个参数——跨模块一律走公开名，无下划线私有名跨模块引用；`review_queue` / `strength_for` / `build_program` 的签名在 Task 5 定义、Task 6/7 按同签名调用；`ConfirmationRequest`（Task 7）只在 Task 7 内构造。

**已声明的偏差**（相对规格，需在实现时保留注释）：

1. `spec` 说 `candidate_evidence` 是「路径 + sha256」。三项外部项的候选快照落在同一个内容寻址仓 `data/acceptance-external-inputs/<sha256>/<code>.candidates.json`（Task 6 `candidate_blob`，内容为 `{"code": ..., "rows": [...]}`，`rows` 与程序区渲染的候选行逐字相同），因此仍满足「路径 + sha256」，且不新增目录；快照**不进证据包**，所以既有已发布记录的 `secret_scan.json` 哈希不受影响（`evidence._swap_in` 每次 `prepare` 都会整体换掉证据包目录，往里加文件等于追溯改写已签记录）。
1b. 六项机械项与三项外部项的候选证据都**从磁盘读回**（机械项走 `evidence.read_pack_references`，外部项走 `candidate_blob` 的快照），而不是从清单行的 `evidence` 上抄。原因是 `build_checklist` 是纯函数、行上永远是空证据，而 `prepare` 会把证据包引用挂到行上、`confirm` 会把修订引用换上去：只有从磁盘读回，supersede 才能与首次签署引用同一批证据包文件，而不是引用它要替换掉的那份修订。`confirm` 另加一条守卫：机械项读不到证据包引用时以 `candidate_evidence_missing` 拒绝签署（`confirm` 不得生成 evidence，否则该行唯一的引用就是它自己的修订，签名自己给自己作证）。
2. `spec` 只对「官方摘录未覆盖部分配置行」明确要求入队。本计划把**比对出的全部差异**（日历差异、规则未覆盖/冲突）都并入队列（Task 4 的 `queue_rows` + Task 7 `_comparison_queue`），方向是更保守的：差异正是操作者要签字的那件事，能被看见就必须被逐条认领；`unclaimed`（官方列了配置未声明的行）只计数、不入队、也不阻止 `corroborated`，与规格对「额外官方行不构成障碍」的处理一致。
3. `spec` 的不可比对原因只列了「单一价格源」。本计划加第二个稳定原因 `no_paired_bars`：两个价格源各自覆盖不相交的证券集合（可信夹具就是这个形状——股票走 tushare、基准指数走 akshare），此时 `(symbol, trade_date)` 没有任何一对双源读数，`compared` + `rows_compared=0` 会被读成「查过且干净」。规格的原则是「不得伪装成通过了跨源验证」，这条只是把同一原则覆盖到第二种形状上，强度同样只能 `OPERATOR_ATTESTED`。
4. `spec` 的队列描述未提跨源项的入队项；`cross_source_price_sample` 无外部输入概念，其队列恒为候选内容全量减去上一 PASS 已覆盖（首次签署即全量），不因 `no_paired_bars` 而缩短——`no_paired_bars` 只影响强度与程序区结论文字。
