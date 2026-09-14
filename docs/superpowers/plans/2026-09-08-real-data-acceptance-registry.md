# Real Data Acceptance Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an immutable real-data acceptance registry and require every formal Research experiment to pin a currently valid ACCEPTED record before factor computation.

**Architecture:** A strict Pydantic contract defines checklists and acceptance records; a content-addressed local registry publishes them atomically outside immutable datasets. Dataset builds gain sanitized source/snapshot provenance, an offline checker recomputes all evidence, a service exposes prepare/publish/show commands, and Research freezes the selected `data_acceptance_id` into experiment identity and artifacts.

**Tech Stack:** Python 3.10, Pydantic 2, pandas 2+, PyArrow 14+, PyYAML 6+, Typer, pytest, Jinja2, SHA-256 content addressing

**Spec:** `docs/superpowers/specs/2026-09-08-real-data-acceptance-registry-design.md`

## Global Constraints

- Policy identifier is exactly `real-data-v1`; schema version is integer `1`.
- Acceptance records live at `data/acceptances/<dataset_version>/<acceptance_id>/acceptance.json` and are immutable.
- ACCEPTED requires every automated and manual check to be PASS; missing values never imply success.
- REJECTED attempts are atomically recorded before the CLI returns a nonzero exit.
- No command may modify standardized datasets, raw snapshots, `CURRENT`, or existing acceptance records.
- Acceptance artifacts store hashes, summaries, relative paths, and public identifiers only—never market payloads, credentials, exception stacks, or absolute local paths.
- Research resolves acceptance after dataset pinning and before experiment identity/factor work; `data_acceptance_id` participates in experiment identity.
- Engineering may run without an acceptance, but its performance decision remains UNTRUSTED.
- All default tests remain offline; live supplier access is explicitly operator-triggered.
- Execute in an isolated worktree created through `superpowers:using-git-worktrees` because the main checkout contains unrelated user changes.

---

### Task 1: Strict acceptance contracts and canonical identity

**Files:**
- Create: `src/stock_quant/research/acceptance/__init__.py`
- Create: `src/stock_quant/research/acceptance/models.py`
- Test: `tests/unit/test_acceptance_models.py`

**Interfaces:**
- Produces: `POLICY_VERSION = "real-data-v1"`, `SCHEMA_VERSION = 1`, and `CURRENT_ACCEPTED = "CURRENT_ACCEPTED"`.
- Produces: `CheckStatus`, `AcceptanceDecision`, `EvidenceReference`, `RawSnapshotBinding`, `CheckResult`, `AcceptanceChecklist`, and `AcceptanceRecord`.
- Produces: `compute_acceptance_id(record: AcceptanceRecord) -> str` and `canonical_record_json(record: AcceptanceRecord) -> str`.

- [ ] **Step 1: Write failing strict-model and completeness tests**

```python
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from stock_quant.research.acceptance.models import (
    AUTOMATED_CHECK_CODES,
    MANUAL_CHECK_CODES,
    AcceptanceChecklist,
    CheckResult,
    CheckStatus,
)


def test_policy_check_codes_are_fixed():
    assert AUTOMATED_CHECK_CODES == (
        "dataset_manifest_integrity",
        "quality_report_integrity",
        "required_table_coverage",
        "date_window_completeness",
        "security_master_evidence",
        "corporate_action_evidence",
        "raw_snapshot_traceability",
        "source_role_health",
    )
    assert MANUAL_CHECK_CODES == (
        "exchange_calendar_sample",
        "source_row_count_sample",
        "missing_reason_sample",
        "cross_source_price_sample",
        "corporate_action_sample",
        "benchmark_sample",
        "trading_rule_effective_dates",
        "security_master_sample",
        "secret_scan",
    )


def test_check_result_rejects_empty_summary_and_unknown_fields():
    with pytest.raises(ValidationError):
        CheckResult(code="x", status=CheckStatus.PASS, summary="")
    with pytest.raises(ValidationError):
        CheckResult(code="x", status=CheckStatus.PASS, summary="ok", extra=True)


def test_checklist_requires_every_manual_code_once(checklist_payload):
    checklist_payload["manual_checks"] = checklist_payload["manual_checks"][:-1]
    with pytest.raises(ValidationError, match="manual check codes"):
        AcceptanceChecklist.model_validate(checklist_payload)
```

- [ ] **Step 2: Run the model tests to verify they fail**

Run: `pytest tests/unit/test_acceptance_models.py -k 'policy or strict or requires' -v`

Expected: FAIL because the acceptance package does not exist.

- [ ] **Step 3: Implement enums, constants, and strict value models**

```python
POLICY_VERSION = "real-data-v1"
SCHEMA_VERSION = 1
CURRENT_ACCEPTED = "CURRENT_ACCEPTED"

AUTOMATED_CHECK_CODES = (
    "dataset_manifest_integrity",
    "quality_report_integrity",
    "required_table_coverage",
    "date_window_completeness",
    "security_master_evidence",
    "corporate_action_evidence",
    "raw_snapshot_traceability",
    "source_role_health",
)
MANUAL_CHECK_CODES = (
    "exchange_calendar_sample",
    "source_row_count_sample",
    "missing_reason_sample",
    "cross_source_price_sample",
    "corporate_action_sample",
    "benchmark_sample",
    "trading_rule_effective_dates",
    "security_master_sample",
    "secret_scan",
)


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class AcceptanceDecision(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["local", "external"]
    reference: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    summary: str = Field(min_length=1)


class RawSnapshotBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    request_key: str = Field(min_length=1)
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str = Field(min_length=1)
    status: CheckStatus
    summary: str = Field(min_length=1)
    details: dict[str, JsonValue] = Field(default_factory=dict)
    evidence: tuple[EvidenceReference, ...] = ()
```

- [ ] **Step 4: Implement checklist and record cross-field validation**

```python
class AcceptanceChecklist(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = SCHEMA_VERSION
    policy_version: Literal["real-data-v1"] = POLICY_VERSION
    dataset_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prepared_at: datetime
    operator_id: str = Field(min_length=1)
    automated_checks: tuple[CheckResult, ...]
    manual_checks: tuple[CheckResult, ...]
    raw_snapshot_evidence: tuple[RawSnapshotBinding, ...]

    @model_validator(mode="after")
    def validate_codes(self):
        _require_exact_codes(self.automated_checks, AUTOMATED_CHECK_CODES, "automated")
        _require_exact_codes(self.manual_checks, MANUAL_CHECK_CODES, "manual")
        return self


class AcceptanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = SCHEMA_VERSION
    policy_version: str = Field(default=POLICY_VERSION, min_length=1)
    acceptance_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    operator_id: str = Field(min_length=1)
    automated_checks: tuple[CheckResult, ...]
    manual_checks: tuple[CheckResult, ...]
    raw_snapshot_evidence: tuple[RawSnapshotBinding, ...]
    decision: AcceptanceDecision
    reasons: tuple[str, ...]

    @model_validator(mode="after")
    def validate_decision(self):
        all_pass = all(
            item.status is CheckStatus.PASS
            for item in (*self.automated_checks, *self.manual_checks)
        )
        if self.decision is AcceptanceDecision.ACCEPTED and (
            not all_pass or self.reasons
        ):
            raise ValueError("ACCEPTED requires all checks PASS and no reasons")
        if self.decision is AcceptanceDecision.REJECTED and not self.reasons:
            raise ValueError("REJECTED requires at least one reason")
        return self
```

- [ ] **Step 5: Write failing canonical-identity tests**

```python
def test_acceptance_identity_is_canonical(record):
    first = compute_acceptance_id(record.model_copy(update={"acceptance_id": "0" * 64}))
    reordered = record.model_copy(
        update={"automated_checks": tuple(reversed(record.automated_checks))}
    )
    second = compute_acceptance_id(reordered.model_copy(update={"acceptance_id": "f" * 64}))
    assert first == second


def test_created_at_and_operator_change_identity(record):
    assert compute_acceptance_id(record) != compute_acceptance_id(
        record.model_copy(update={"operator_id": "second-operator"})
    )
    assert compute_acceptance_id(record) != compute_acceptance_id(
        record.model_copy(update={"created_at": datetime(2026, 9, 9, tzinfo=timezone.utc)})
    )
```

- [ ] **Step 6: Implement canonical serialization and identity**

```python
def _canonical_payload(record: AcceptanceRecord) -> dict[str, JsonValue]:
    payload = record.model_dump(mode="json")
    payload.pop("acceptance_id", None)
    payload["automated_checks"] = sorted(payload["automated_checks"], key=lambda row: row["code"])
    payload["manual_checks"] = sorted(payload["manual_checks"], key=lambda row: row["code"])
    payload["raw_snapshot_evidence"] = sorted(
        payload["raw_snapshot_evidence"],
        key=lambda row: (
            row["source"], row["endpoint"], row["request_key"], row["file_sha256"]
        ),
    )
    return payload


def compute_acceptance_id(record: AcceptanceRecord) -> str:
    encoded = json.dumps(
        _canonical_payload(record), sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def canonical_record_json(record: AcceptanceRecord) -> str:
    if compute_acceptance_id(record) != record.acceptance_id:
        raise ValueError("acceptance_id does not match canonical payload")
    return json.dumps(
        record.model_dump(mode="json"), sort_keys=True, ensure_ascii=False,
        indent=2, allow_nan=False,
    ) + "\n"
```

- [ ] **Step 7: Run the complete model suite**

Run: `pytest tests/unit/test_acceptance_models.py -v`

Expected: PASS for strict fields, exact check vocabularies, UTC datetimes, deterministic ordering, and identity changes.

- [ ] **Step 8: Commit the contract**

```bash
git add src/stock_quant/research/acceptance/__init__.py src/stock_quant/research/acceptance/models.py tests/unit/test_acceptance_models.py
git commit -m "feat: define real data acceptance contracts"
```

---

### Task 2: Immutable acceptance registry

**Files:**
- Create: `src/stock_quant/research/acceptance/registry.py`
- Test: `tests/integration/test_acceptance_registry.py`

**Interfaces:**
- Consumes: `AcceptanceRecord`, `canonical_record_json`, and `compute_acceptance_id` from Task 1.
- Produces: `AcceptanceRegistry(project_root: Path)`.
- Produces: `publish(record: AcceptanceRecord) -> AcceptanceRecord`, `get(dataset_version: str, acceptance_id: str) -> AcceptanceRecord`, `list(dataset_version: str) -> tuple[AcceptanceRecord, ...]`, and `select(dataset_version: str, requested_id: str, policy_version: str = POLICY_VERSION) -> AcceptanceRecord`.
- Produces: `AcceptanceNotFound`, `AcceptanceIntegrityError`, `AcceptanceIdentityConflict`, and `NoValidAcceptance`.

- [ ] **Step 1: Write failing atomic-publication tests**

```python
def test_publish_writes_content_addressed_record(tmp_path, accepted_record):
    registry = AcceptanceRegistry(tmp_path)
    published = registry.publish(accepted_record)
    path = (
        tmp_path / "data" / "acceptances" / accepted_record.dataset_version
        / accepted_record.acceptance_id / "acceptance.json"
    )
    assert path.is_file()
    assert registry.get(
        accepted_record.dataset_version, accepted_record.acceptance_id
    ) == published


def test_identical_publish_is_idempotent(tmp_path, accepted_record):
    registry = AcceptanceRegistry(tmp_path)
    first = registry.publish(accepted_record)
    before = registry.path_for(first).read_bytes()
    second = registry.publish(accepted_record)
    assert second == first
    assert registry.path_for(second).read_bytes() == before
```

- [ ] **Step 2: Run the registry tests to verify they fail**

Run: `pytest tests/integration/test_acceptance_registry.py -k 'publish' -v`

Expected: FAIL because `AcceptanceRegistry` is not defined.

- [ ] **Step 3: Implement safe paths and atomic publication**

```python
class AcceptanceRegistry:
    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root) / "data" / "acceptances"

    def path_for(self, record: AcceptanceRecord) -> Path:
        return self.root / record.dataset_version / record.acceptance_id / "acceptance.json"

    def publish(self, record: AcceptanceRecord) -> AcceptanceRecord:
        payload = canonical_record_json(record).encode("utf-8")
        destination = self.path_for(record)
        if destination.is_file():
            if destination.read_bytes() != payload:
                raise AcceptanceIdentityConflict(record.acceptance_id)
            return record
        destination.parent.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent.parent / f".{record.acceptance_id}.{uuid.uuid4().hex}.tmp"
        temporary.mkdir()
        try:
            (temporary / "acceptance.json").write_bytes(payload)
            os.replace(temporary, destination.parent)
        except BaseException:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        return record
```

- [ ] **Step 4: Write failing integrity, history, and selection tests**

```python
def test_history_keeps_rejected_and_accepted_records(tmp_path, rejected_record, accepted_record):
    registry = AcceptanceRegistry(tmp_path)
    registry.publish(rejected_record)
    registry.publish(accepted_record)
    assert [row.decision.value for row in registry.list(accepted_record.dataset_version)] == [
        "REJECTED", "ACCEPTED"
    ]


def test_select_current_accepted_ignores_rejected_and_old_policy(
    tmp_path, rejected_record, old_policy_record, accepted_record
):
    registry = AcceptanceRegistry(tmp_path)
    for record in (rejected_record, old_policy_record, accepted_record):
        registry.publish(record)
    assert registry.select(
        accepted_record.dataset_version, CURRENT_ACCEPTED
    ).acceptance_id == accepted_record.acceptance_id


def test_get_detects_tampered_record(tmp_path, accepted_record):
    registry = AcceptanceRegistry(tmp_path)
    registry.publish(accepted_record)
    path = registry.path_for(accepted_record)
    path.write_text(path.read_text().replace("operator-a", "operator-b"))
    with pytest.raises(AcceptanceIntegrityError):
        registry.get(accepted_record.dataset_version, accepted_record.acceptance_id)
```

- [ ] **Step 5: Implement strict reads and stable selection**

```python
def get(self, dataset_version: str, acceptance_id: str) -> AcceptanceRecord:
    path = self.root / dataset_version / acceptance_id / "acceptance.json"
    if not path.is_file():
        raise AcceptanceNotFound(acceptance_id)
    record = AcceptanceRecord.model_validate_json(path.read_text(encoding="utf-8"))
    if record.dataset_version != dataset_version or record.acceptance_id != acceptance_id:
        raise AcceptanceIntegrityError("acceptance path disagrees with payload")
    if compute_acceptance_id(record) != acceptance_id:
        raise AcceptanceIntegrityError("acceptance payload hash mismatch")
    return record


def list(self, dataset_version: str) -> tuple[AcceptanceRecord, ...]:
    directory = self.root / dataset_version
    if not directory.is_dir():
        return ()
    records = [self.get(dataset_version, child.name) for child in directory.iterdir() if child.is_dir()]
    return tuple(sorted(records, key=lambda row: (row.created_at, row.acceptance_id)))


def select(self, dataset_version, requested_id, policy_version=POLICY_VERSION):
    records = self.list(dataset_version)
    if requested_id != CURRENT_ACCEPTED:
        records = tuple(row for row in records if row.acceptance_id == requested_id)
    valid = tuple(
        row for row in records
        if row.decision is AcceptanceDecision.ACCEPTED
        and row.policy_version == policy_version
        and all(check.status is CheckStatus.PASS for check in row.automated_checks)
        and all(check.status is CheckStatus.PASS for check in row.manual_checks)
    )
    if not valid:
        raise NoValidAcceptance(dataset_version)
    return valid[-1]
```

- [ ] **Step 6: Add concurrent-publication coverage**

Use two threads publishing the same record. Assert both return the same ID, exactly one final directory exists, no temporary directory remains, and the JSON passes `get` integrity validation. If `os.replace` loses a same-target race, treat an already-existing byte-identical destination as idempotent success.

- [ ] **Step 7: Run the registry suite**

Run: `pytest tests/integration/test_acceptance_registry.py -v`

Expected: PASS for atomicity, idempotency, immutable history, tamper detection, explicit selection, and current-policy selection.

- [ ] **Step 8: Commit the registry**

```bash
git add src/stock_quant/research/acceptance/registry.py tests/integration/test_acceptance_registry.py
git commit -m "feat: add immutable data acceptance registry"
```

---

### Task 3: Bind sanitized build evidence into dataset identity

**Files:**
- Modify: `src/stock_quant/data_pipeline.py`
- Modify: `src/stock_quant/bootstrap.py`
- Modify: `src/stock_quant/data_sources/raw_store.py`
- Modify: `tests/unit/test_raw_store.py`
- Modify: `tests/integration/test_data_pipeline.py`
- Modify: `tests/integration/test_dataset_publish.py`

**Interfaces:**
- Produces: `DATASET_BUILD_CONTRACT_VERSION = 1`.
- Produces: `SourceStatus.reason_code: str | None` and `dataset_build_config(*, run_id: str, request: DataUpdateRequest, resolved_end_date: date, resolved_end_is_fallback: bool, statuses: Mapping[str, SourceStatus], raw_snapshots: Sequence[RawSnapshot]) -> dict[str, object]`.
- Produces: `RawSnapshotEvidence` and `RawStore.verify_evidence(evidence: RawSnapshotEvidence) -> RawSnapshot`.
- Produces: dataset manifest `build_config` containing origin, request/resolution dates, source status, and sorted raw snapshot hashes.

- [ ] **Step 1: Write failing build-config identity tests**

```python
def test_successful_update_binds_sanitized_build_evidence(project):
    result = DataPipeline(project.root, sources=_all_stubs()).update(_request())
    manifest = json.loads(
        (result.dataset_ref.path / "dataset_manifest.json").read_text()
    )
    build = manifest["build_config"]
    assert build["origin"] == "data_update"
    assert build["pipeline_contract_version"] == 1
    assert build["requested_start_date"] == "2020-01-01"
    assert build["resolved_end_date"] == "2020-12-31"
    assert isinstance(build["resolved_end_is_fallback"], bool)
    assert build["raw_snapshots"] == sorted(
        build["raw_snapshots"],
        key=lambda row: (
            row["source"], row["endpoint"], row["request_key"], row["file_sha256"]
        ),
    )
    assert all(set(row) == {"source", "required", "ok", "reason_code"} for row in build["source_status"])
    assert "token" not in json.dumps(build).lower()


def test_raw_snapshot_hashes_change_dataset_identity(tmp_path):
    tables = valid_tables()
    first = DatasetPublisher(tmp_path).publish(
        tables,
        QualityReport(),
        build_config={"raw_snapshots": [{
            "source": "tushare", "endpoint": "daily", "request_key": "first",
            "file_sha256": "a" * 64, "manifest_sha256": "c" * 64,
        }]},
    )
    second = DatasetPublisher(tmp_path).publish(
        tables,
        QualityReport(),
        build_config={"raw_snapshots": [{
            "source": "tushare", "endpoint": "daily", "request_key": "second",
            "file_sha256": "b" * 64, "manifest_sha256": "d" * 64,
        }]},
    )
    assert first.version != second.version
```

- [ ] **Step 2: Run the build-evidence tests to verify they fail**

Run: `pytest tests/integration/test_data_pipeline.py -k 'sanitized_build_evidence' -v`

Expected: FAIL because current build configuration contains only `run_id`.

- [ ] **Step 3: Add structured source reason codes**

```python
@dataclass(frozen=True)
class SourceStatus:
    source: str
    required: bool
    ok: bool
    reason: str | None = None
    reason_code: str | None = None


def _source_evidence(statuses: Mapping[str, SourceStatus]) -> list[dict[str, object]]:
    return [
        {
            "source": status.source,
            "required": status.required,
            "ok": status.ok,
            "reason_code": status.reason_code or ("ok" if status.ok else "unspecified_failure"),
        }
        for status in sorted(statuses.values(), key=lambda row: row.source)
    ]
```

Assign stable codes at every `SourceStatus` construction: `not_run`, `ok`, `required_source_disabled`, `source_unavailable`, `source_fetch_failed`, `partial_fetch_failure`, and `optional_source_unavailable`. Preserve `reason` for operator output but never put it in dataset build evidence.

```python
@dataclass(frozen=True)
class RawSnapshotEvidence:
    source: str
    endpoint: str
    request_key: str
    file_sha256: str
    manifest_sha256: str

    @classmethod
    def from_snapshot(cls, snapshot: RawSnapshot) -> "RawSnapshotEvidence":
        return cls(
            source=str(snapshot.manifest["source"]),
            endpoint=str(snapshot.manifest["endpoint"]),
            request_key=str(snapshot.manifest["request_key"]),
            file_sha256=snapshot.sha256,
            manifest_sha256=_sha256_file(snapshot.path / "manifest.json"),
        )


def _raw_snapshot_evidence_rows(
    snapshots: Sequence[RawSnapshot],
) -> list[dict[str, str]]:
    unique: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for snapshot in snapshots:
        evidence = RawSnapshotEvidence.from_snapshot(snapshot)
        row = asdict(evidence)
        key = (
            evidence.source, evidence.endpoint,
            evidence.request_key, evidence.file_sha256,
        )
        unique[key] = row
    return [unique[key] for key in sorted(unique)]
```

Change `DataPipeline._record_raw` to return the complete `RawSnapshot` from `RawStore.save`, and keep `raw_snapshots` internally as `list[RawSnapshot]`. Preserve the public `DataUpdateResult.raw_snapshots: tuple[str, ...]` contract by converting to `tuple(snapshot.sha256 for snapshot in raw_snapshots)` inside `_result`; no caller receives local paths or manifests.

- [ ] **Step 4: Build the exact sanitized publication payload**

```python
def dataset_build_config(
    *,
    run_id: str,
    request: DataUpdateRequest,
    resolved_end_date: date,
    resolved_end_is_fallback: bool,
    statuses: Mapping[str, SourceStatus],
    raw_snapshots: Sequence[RawSnapshot],
) -> dict[str, object]:
    return {
        "origin": "data_update",
        "pipeline_contract_version": DATASET_BUILD_CONTRACT_VERSION,
        "run_id": run_id,
        "requested_start_date": request.start_date.isoformat() if request.start_date else None,
        "requested_end_date": request.end_date.isoformat() if request.end_date else None,
        "resolved_end_date": resolved_end_date.isoformat(),
        "resolved_end_is_fallback": bool(resolved_end_is_fallback),
        "source_status": _source_evidence(statuses),
        "raw_snapshots": _raw_snapshot_evidence_rows(raw_snapshots),
    }
```

Pass this mapping to `DatasetPublisher.publish`. Bootstrap passes `{"origin": "bootstrap", "pipeline_contract_version": 1}`.

- [ ] **Step 5: Write failing raw-snapshot lookup tests**

```python
def test_verify_evidence_returns_exact_snapshot(tmp_path, fetch_result):
    store = RawStore(tmp_path)
    saved = store.save(fetch_result)
    evidence = RawSnapshotEvidence.from_snapshot(saved)
    found = store.verify_evidence(evidence)
    assert found.path == saved.path
    assert found.manifest["file_sha256"] == saved.sha256


def test_verify_evidence_rejects_invalid_path_component(tmp_path, evidence):
    escaped = evidence.model_copy(update={"request_key": "../escape"})
    with pytest.raises(ValueError, match="request key"):
        RawStore(tmp_path).verify_evidence(escaped)
```

- [ ] **Step 6: Implement read-only hash lookup with content verification**

```python
def verify_evidence(self, evidence: RawSnapshotEvidence) -> RawSnapshot:
    source = _path_component(evidence.source, "source")
    endpoint = _path_component(evidence.endpoint, "endpoint")
    request_key = _path_component(evidence.request_key, "request key")
    file_sha256 = _sha256_value(evidence.file_sha256)
    path = self._root / source / endpoint / request_key / file_sha256
    data_path = path / "data.parquet"
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if _sha256_file(data_path) != file_sha256:
        raise ValueError("raw data hash mismatch")
    if _sha256_file(manifest_path) != evidence.manifest_sha256:
        raise ValueError("raw manifest hash mismatch")
    for key, expected in (
        ("source", source), ("endpoint", endpoint),
        ("request_key", request_key), ("file_sha256", file_sha256),
    ):
        if manifest.get(key) != expected:
            raise ValueError(f"raw manifest {key} mismatch")
    return RawSnapshot(path=path, sha256=file_sha256, manifest=manifest)
```

- [ ] **Step 7: Run data provenance tests**

Run: `pytest tests/unit/test_raw_store.py tests/integration/test_dataset_publish.py tests/integration/test_data_pipeline.py -v`

Expected: PASS; build evidence is deterministic, sanitized, identity-bearing, and each raw hash is resolvable.

- [ ] **Step 8: Commit dataset provenance**

```bash
git add src/stock_quant/data_pipeline.py src/stock_quant/bootstrap.py src/stock_quant/data_sources/raw_store.py tests/unit/test_raw_store.py tests/integration/test_data_pipeline.py tests/integration/test_dataset_publish.py
git commit -m "feat: bind source evidence into dataset versions"
```

---

### Task 4: Offline automated acceptance checks

**Files:**
- Create: `src/stock_quant/research/acceptance/checks.py`
- Test: `tests/unit/test_acceptance_checks.py`
- Test: `tests/integration/test_acceptance_checks.py`

**Interfaces:**
- Consumes: a fixed dataset version, `DatasetReader`, `DataPipeline.validate`, `RawStore.verify_evidence`, project configuration, and `real-data-v1` codes.
- Produces: `AcceptanceCheckInput(project_root: Path, dataset_version: str)`.
- Produces: `run_automated_checks(input: AcceptanceCheckInput) -> tuple[CheckResult, ...]` and `dataset_evidence(input: AcceptanceCheckInput) -> DatasetEvidence`.

- [ ] **Step 1: Write failing manifest and quality integrity tests**

```python
def test_manifest_integrity_passes_for_untouched_dataset(project):
    checks = _checks_by_code(run_automated_checks(_input(project)))
    assert checks["dataset_manifest_integrity"].status is CheckStatus.PASS
    assert checks["quality_report_integrity"].status is CheckStatus.PASS


def test_manifest_integrity_fails_when_table_hash_changes(project):
    table = project.dataset_path / "daily_bar.parquet"
    table.write_bytes(table.read_bytes() + b"tamper")
    checks = _checks_by_code(run_automated_checks(_input(project)))
    assert checks["dataset_manifest_integrity"].status is CheckStatus.FAIL
    assert checks["dataset_manifest_integrity"].details["code"] == "table_hash_mismatch"
```

- [ ] **Step 2: Run the integrity tests to verify they fail**

Run: `pytest tests/integration/test_acceptance_checks.py -k 'integrity' -v`

Expected: FAIL because `run_automated_checks` does not exist.

- [ ] **Step 3: Implement one exception-safe check runner**

```python
@dataclass(frozen=True)
class AcceptanceCheckInput:
    project_root: Path
    dataset_version: str


@dataclass(frozen=True)
class DatasetEvidence:
    dataset_path: Path
    manifest: dict[str, object]
    dataset_manifest_sha256: str
    quality_report_sha256: str
    raw_snapshot_evidence: tuple[RawSnapshotBinding, ...]


def dataset_evidence(value: AcceptanceCheckInput) -> DatasetEvidence:
    dataset_path = (
        value.project_root / "data" / "standardized" / value.dataset_version
    )
    manifest_path = dataset_path / "dataset_manifest.json"
    quality_path = dataset_path / "quality_report.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("dataset_version") != value.dataset_version:
        raise ValueError("dataset manifest version mismatch")
    bindings = tuple(
        RawSnapshotBinding.model_validate(row)
        for row in manifest.get("build_config", {}).get("raw_snapshots", [])
    )
    return DatasetEvidence(
        dataset_path=dataset_path,
        manifest=manifest,
        dataset_manifest_sha256=_sha256_file(manifest_path),
        quality_report_sha256=_sha256_file(quality_path),
        raw_snapshot_evidence=bindings,
    )


def run_automated_checks(value: AcceptanceCheckInput) -> tuple[CheckResult, ...]:
    functions = {
        "dataset_manifest_integrity": _check_dataset_manifest,
        "quality_report_integrity": _check_quality_report,
        "required_table_coverage": _check_required_tables,
        "date_window_completeness": _check_date_window,
        "security_master_evidence": _check_security_master,
        "corporate_action_evidence": _check_corporate_actions,
        "raw_snapshot_traceability": _check_raw_snapshots,
        "source_role_health": _check_source_roles,
    }
    results = []
    for code in AUTOMATED_CHECK_CODES:
        try:
            results.append(functions[code](value))
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            results.append(CheckResult(
                code=code,
                status=CheckStatus.FAIL,
                summary=f"{code} could not be verified",
                details={"error_code": type(error).__name__},
            ))
    return tuple(results)
```

Do not include `str(error)` in persisted details because supplier paths or secrets can appear there. Unexpected programming exceptions are not caught.

- [ ] **Step 4: Implement manifest and quality checks with fresh hashes**

```python
def _check_dataset_manifest(value):
    evidence = dataset_evidence(value)
    failures = []
    for name, declared in evidence.manifest["tables"].items():
        path = evidence.dataset_path / declared["path"]
        if not path.is_file():
            failures.append([name, "missing"])
        elif _sha256_file(path) != declared["sha256"]:
            failures.append([name, "hash_mismatch"])
        elif len(pd.read_parquet(path)) != int(declared["row_count"]):
            failures.append([name, "row_count_mismatch"])
    return _result("dataset_manifest_integrity", failures)


def _check_quality_report(value):
    evidence = dataset_evidence(value)
    report = DataPipeline(value.project_root).validate(value.dataset_version)
    decision = evaluate_publication(report)
    fatal = any(issue.severity is Severity.FATAL for issue in report.issues)
    failures = [] if decision.passed and not fatal else [["quality", "gate_failed"]]
    return _result("quality_report_integrity", failures)
```

```python
def _result(code: str, failures: list[list[str]]) -> CheckResult:
    if failures:
        return CheckResult(
            code=code,
            status=CheckStatus.FAIL,
            summary=f"{code} failed",
            details={"failures": failures},
        )
    return CheckResult(
        code=code,
        status=CheckStatus.PASS,
        summary=f"{code} passed",
    )
```

`dataset_evidence` verifies the directory name equals the manifest's `dataset_version`, computes the manifest and quality report SHA-256 values, and rejects any path declared outside the dataset directory.

- [ ] **Step 5: Write failing semantic evidence tests**

```python
@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("remove_benchmark", "date_window_completeness"),
        ("remove_master_coverage", "security_master_evidence"),
        ("mark_action_coverage_untrusted", "corporate_action_evidence"),
        ("remove_raw_snapshot", "raw_snapshot_traceability"),
        ("fail_required_source", "source_role_health"),
        ("mark_bootstrap_origin", "source_role_health"),
    ],
)
def test_semantic_check_fails_closed(mutated_project, mutation, code):
    project = mutated_project(mutation)
    checks = _checks_by_code(run_automated_checks(_input(project)))
    assert checks[code].status is CheckStatus.FAIL
```

- [ ] **Step 6: Implement semantic checks from existing trust primitives**

Reuse `missing_master_coverage_symbols` and `evaluate_corporate_action_trust` rather than duplicating their rules. Required tables are the tables registered by the current standardized schema contract. Date completeness compares the manifest's resolved window with `trading_calendar`, requires both configured benchmarks, and requires every missing listed-security row to carry an accepted classification rather than demanding fabricated bars for suspensions. Raw traceability requires at least one snapshot for every successful required source and calls `RawStore.verify_evidence` for every structured snapshot record; an empty evidence list cannot pass. Source roles require `origin=data_update`, contract version `1`, and every `required=true` status to have `ok=true` and `reason_code=ok`.

- [ ] **Step 7: Run automated-check suites**

Run: `pytest tests/unit/test_acceptance_checks.py tests/integration/test_acceptance_checks.py -v`

Expected: PASS with exactly eight results in policy order and deterministic, redacted failure details.

- [ ] **Step 8: Commit automated checks**

```bash
git add src/stock_quant/research/acceptance/checks.py tests/unit/test_acceptance_checks.py tests/integration/test_acceptance_checks.py
git commit -m "feat: verify real data acceptance evidence offline"
```

---

### Task 5: Prepare, publish, and show acceptance workflows

**Files:**
- Create: `src/stock_quant/research/acceptance/service.py`
- Modify: `src/stock_quant/cli.py`
- Test: `tests/unit/test_acceptance_service.py`
- Test: `tests/integration/test_acceptance_cli.py`

**Interfaces:**
- Consumes: Tasks 1–4 models, registry, automated checks, and evidence hashes.
- Produces: `prepare_checklist(project_root: Path, dataset_version: str, operator_id: str, prepared_at: datetime | None = None) -> AcceptanceChecklist`.
- Produces: `publish_checklist(project_root: Path, checklist_path: Path, created_at: datetime | None = None) -> AcceptanceRecord`.
- Produces: `show_acceptances(project_root: Path, dataset_version: str) -> tuple[AcceptanceRecord, ...]`.
- Produces CLI hierarchy `data acceptance prepare|publish|show`.

- [ ] **Step 1: Write failing checklist preparation test**

```python
def test_prepare_creates_complete_unpassed_manual_template(project):
    checklist = prepare_checklist(
        project.root,
        project.version,
        "operator-a",
        prepared_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    assert [row.code for row in checklist.automated_checks] == list(AUTOMATED_CHECK_CODES)
    assert [row.code for row in checklist.manual_checks] == list(MANUAL_CHECK_CODES)
    assert all(row.status is CheckStatus.FAIL for row in checklist.manual_checks)
    assert all(row.summary == "operator review required" for row in checklist.manual_checks)
```

- [ ] **Step 2: Run the preparation test to verify it fails**

Run: `pytest tests/unit/test_acceptance_service.py::test_prepare_creates_complete_unpassed_manual_template -v`

Expected: FAIL because the service module does not exist.

- [ ] **Step 3: Implement deterministic checklist preparation**

```python
def prepare_checklist(project_root, dataset_version, operator_id, prepared_at=None):
    value = AcceptanceCheckInput(Path(project_root), dataset_version)
    evidence = dataset_evidence(value)
    automated = run_automated_checks(value)
    manual = tuple(
        CheckResult(code=code, status=CheckStatus.FAIL, summary="operator review required")
        for code in MANUAL_CHECK_CODES
    )
    return AcceptanceChecklist(
        dataset_version=dataset_version,
        dataset_manifest_sha256=evidence.dataset_manifest_sha256,
        quality_report_sha256=evidence.quality_report_sha256,
        prepared_at=prepared_at or datetime.now(timezone.utc),
        operator_id=operator_id.strip(),
        automated_checks=automated,
        manual_checks=manual,
        raw_snapshot_evidence=evidence.raw_snapshot_evidence,
    )
```

- [ ] **Step 4: Write failing publish-decision and evidence-path tests**

```python
def test_publish_recomputes_checks_and_accepts_complete_checklist(project, completed_checklist):
    record = publish_checklist(
        project.root, completed_checklist,
        created_at=datetime(2026, 9, 8, 12, tzinfo=timezone.utc),
    )
    assert record.decision is AcceptanceDecision.ACCEPTED
    assert record.reasons == ()


def test_publish_records_rejection_before_raising(project, incomplete_checklist):
    with pytest.raises(AcceptanceRejected) as captured:
        publish_checklist(project.root, incomplete_checklist)
    saved = AcceptanceRegistry(project.root).get(
        project.version, captured.value.record.acceptance_id
    )
    assert saved.decision is AcceptanceDecision.REJECTED


def test_local_evidence_cannot_escape_project(project, completed_checklist):
    escaped = _replace_evidence_reference(completed_checklist, "../outside.txt")
    with pytest.raises(AcceptanceRejected) as captured:
        publish_checklist(project.root, escaped)
    assert "evidence_path_outside_project" in captured.value.record.reasons
```

- [ ] **Step 5: Implement fail-closed recomputation and publication**

```python
def publish_checklist(project_root, checklist_path, created_at=None):
    root = Path(project_root).resolve()
    checklist = AcceptanceChecklist.model_validate(
        yaml.safe_load(Path(checklist_path).read_text(encoding="utf-8"))
    )
    fresh = prepare_checklist(
        root, checklist.dataset_version, checklist.operator_id,
        prepared_at=checklist.prepared_at,
    )
    reasons = _binding_reasons(checklist, fresh)
    reasons.extend(_manual_check_reasons(root, checklist.manual_checks))
    decision = AcceptanceDecision.REJECTED if reasons else AcceptanceDecision.ACCEPTED
    provisional = AcceptanceRecord(
        acceptance_id="0" * 64,
        dataset_version=checklist.dataset_version,
        dataset_manifest_sha256=fresh.dataset_manifest_sha256,
        quality_report_sha256=fresh.quality_report_sha256,
        created_at=created_at or datetime.now(timezone.utc),
        operator_id=checklist.operator_id,
        automated_checks=fresh.automated_checks,
        manual_checks=checklist.manual_checks,
        raw_snapshot_evidence=fresh.raw_snapshot_evidence,
        decision=decision,
        reasons=tuple(sorted(set(reasons))),
    )
    record = provisional.model_copy(update={"acceptance_id": compute_acceptance_id(provisional)})
    AcceptanceRegistry(root).publish(record)
    if decision is AcceptanceDecision.REJECTED:
        raise AcceptanceRejected(record)
    return record
```

For local evidence, resolve the path under the project root, reject traversal/symlinks outside the root, recompute SHA-256, and compare it with the checklist. For external evidence, hash the stored UTF-8 `summary`; never fetch the URI.

```python
def _binding_reasons(
    requested: AcceptanceChecklist,
    fresh: AcceptanceChecklist,
) -> list[str]:
    reasons = []
    for field in (
        "schema_version", "policy_version", "dataset_version",
        "dataset_manifest_sha256", "quality_report_sha256",
        "raw_snapshot_evidence",
    ):
        if getattr(requested, field) != getattr(fresh, field):
            reasons.append(f"{field}_changed")
    if requested.automated_checks != fresh.automated_checks:
        reasons.append("automated_checks_changed")
    for check in fresh.automated_checks:
        if check.status is CheckStatus.FAIL:
            reasons.append(f"automated_{check.code}_failed")
    return reasons


def _manual_check_reasons(
    project_root: Path,
    checks: tuple[CheckResult, ...],
) -> list[str]:
    reasons = []
    for check in checks:
        if check.status is not CheckStatus.PASS:
            reasons.append(f"manual_{check.code}_failed")
        if not check.evidence:
            reasons.append(f"manual_{check.code}_evidence_missing")
        for evidence in check.evidence:
            reason = _verify_evidence_reference(project_root, evidence)
            if reason is not None:
                reasons.append(f"manual_{check.code}_{reason}")
    return reasons


def _verify_evidence_reference(
    project_root: Path,
    evidence: EvidenceReference,
) -> str | None:
    if evidence.kind == "external":
        actual = hashlib.sha256(evidence.summary.encode("utf-8")).hexdigest()
        return None if actual == evidence.sha256 else "evidence_hash_changed"
    candidate = (project_root / evidence.reference).resolve()
    if not candidate.is_relative_to(project_root):
        return "evidence_path_outside_project"
    if not candidate.is_file():
        return "evidence_missing"
    return None if _sha256_file(candidate) == evidence.sha256 else "evidence_hash_changed"
```

```python
def verify_acceptance_bindings(
    project_root: Path,
    record: AcceptanceRecord,
) -> None:
    fresh = prepare_checklist(
        project_root,
        record.dataset_version,
        record.operator_id,
        prepared_at=record.created_at,
    )
    requested = AcceptanceChecklist(
        dataset_version=record.dataset_version,
        dataset_manifest_sha256=record.dataset_manifest_sha256,
        quality_report_sha256=record.quality_report_sha256,
        prepared_at=record.created_at,
        operator_id=record.operator_id,
        automated_checks=record.automated_checks,
        manual_checks=record.manual_checks,
        raw_snapshot_evidence=record.raw_snapshot_evidence,
    )
    reasons = _binding_reasons(requested, fresh)
    reasons.extend(_manual_check_reasons(Path(project_root).resolve(), record.manual_checks))
    if record.policy_version != POLICY_VERSION:
        reasons.append("policy_version_expired")
    if reasons:
        raise AcceptanceBindingError(tuple(sorted(set(reasons))))


def acceptance_audit_dict(record: AcceptanceRecord) -> dict[str, object]:
    return {
        "acceptance_id": record.acceptance_id,
        "policy_version": record.policy_version,
        "operator_id": record.operator_id,
        "created_at": record.created_at.isoformat(),
        "decision": record.decision.value,
    }


def show_acceptances(
    project_root: Path,
    dataset_version: str,
) -> tuple[AcceptanceRecord, ...]:
    return AcceptanceRegistry(project_root).list(dataset_version)
```

- [ ] **Step 6: Add the Typer command group and safe YAML output**

```python
acceptance_app = typer.Typer(help="Prepare, publish, and inspect data acceptance records.")
data_app.add_typer(acceptance_app, name="acceptance")


@acceptance_app.command("prepare")
def data_acceptance_prepare(
    version: Annotated[str, typer.Option("--version")],
    operator: Annotated[str, typer.Option("--operator")],
    output: Annotated[Path, typer.Option("--output")],
    root: Path = typer.Option(".", "--root"),
) -> None:
    checklist = prepare_checklist(root, version, operator)
    output.write_text(
        yaml.safe_dump(checklist.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    typer.echo(f"checklist={output.name}")


@acceptance_app.command("publish")
def data_acceptance_publish(
    checklist: Annotated[Path, typer.Option("--checklist")],
    root: Path = typer.Option(".", "--root"),
) -> None:
    try:
        record = publish_checklist(root, checklist)
    except AcceptanceRejected as error:
        typer.echo(f"acceptance_id={error.record.acceptance_id}")
        typer.echo("decision=REJECTED")
        for reason in error.record.reasons:
            typer.echo(f"reason={reason}")
        raise typer.Exit(code=1) from None
    typer.echo(f"acceptance_id={record.acceptance_id}")
    typer.echo("decision=ACCEPTED")


@acceptance_app.command("show")
def data_acceptance_show(
    version: Annotated[str, typer.Option("--version")],
    root: Path = typer.Option(".", "--root"),
) -> None:
    records = show_acceptances(root, version)
    if not records:
        typer.echo("UNACCEPTED")
        return
    for record in records:
        typer.echo(
            f"{record.created_at.isoformat()} {record.acceptance_id} "
            f"{record.policy_version} {record.decision.value}"
        )
```

The commands print only basenames, stable identifiers, decisions, and reason codes—never evidence contents or absolute paths.

- [ ] **Step 7: Run service and CLI suites**

Run: `pytest tests/unit/test_acceptance_service.py tests/integration/test_acceptance_cli.py -v`

Expected: PASS for preparation, successful acceptance, persisted rejection, idempotency, traversal blocking, tamper detection, safe output, and history ordering.

- [ ] **Step 8: Commit operator workflows**

```bash
git add src/stock_quant/research/acceptance/service.py src/stock_quant/cli.py tests/unit/test_acceptance_service.py tests/integration/test_acceptance_cli.py
git commit -m "feat: add data acceptance operator workflow"
```

---

### Task 6: Freeze acceptance identity and enforce the Research gate

**Files:**
- Modify: `src/stock_quant/research/spec.py`
- Modify: `src/stock_quant/research/models.py`
- Modify: `src/stock_quant/research/registry.py`
- Modify: `src/stock_quant/research/runner.py`
- Modify: `configs/experiments/momentum_60d.yml`
- Modify: `tests/unit/test_experiment_spec.py`
- Modify: `tests/integration/conftest.py`
- Modify: `tests/integration/test_experiment_registry.py`
- Modify: `tests/integration/test_research_runner.py`

**Interfaces:**
- Consumes: `AcceptanceRegistry.select`, fixed dataset version, and `DataTrustMode`.
- Produces: `ExperimentSpec.data_acceptance_id: str | None` with `CURRENT_ACCEPTED` placeholder support.
- Produces: `RunState.data_acceptance: dict[str, object] | None` and `ExperimentManifest.data_acceptance_id: str | None`.
- Produces: Research gate before `_begin`/factor work and frozen acceptance identity in experiment hashing.
- Produces: FAILED preflight manifests under `data/runs/preflight_acceptance_<uuid>/` when no experiment identity can be formed.

- [ ] **Step 1: Write failing spec-freeze and identity tests**

```python
def test_research_spec_is_not_frozen_with_current_accepted():
    spec = make_spec(data_acceptance_id="CURRENT_ACCEPTED")
    assert not spec.is_frozen
    frozen = spec.freeze(data_acceptance_id="a" * 64)
    assert frozen.data_acceptance_id == "a" * 64
    assert frozen.is_frozen


def test_acceptance_id_changes_experiment_identity():
    first = make_spec(data_acceptance_id="a" * 64)
    second = make_spec(data_acceptance_id="b" * 64)
    assert compute_experiment_id(first) != compute_experiment_id(second)


def test_engineering_spec_may_freeze_without_acceptance():
    spec = make_spec(data_acceptance_id=None, trust_mode=DataTrustMode.ENGINEERING)
    assert spec.is_frozen
```

- [ ] **Step 2: Run spec tests to verify they fail**

Run: `pytest tests/unit/test_experiment_spec.py -k 'acceptance' -v`

Expected: FAIL because `data_acceptance_id` is forbidden.

- [ ] **Step 3: Add and freeze the acceptance field**

```python
_ACCEPTANCE_UNSET = object()


data_acceptance_id: str | None = CURRENT_ACCEPTED

@property
def is_frozen(self) -> bool:
    versions_fixed = self.dataset_version != _CURRENT and self.universe_version != _CURRENT
    acceptance_fixed = self.data_acceptance_id != CURRENT_ACCEPTED
    if self.trust_mode is DataTrustMode.RESEARCH:
        acceptance_fixed = acceptance_fixed and self.data_acceptance_id is not None
    return versions_fixed and acceptance_fixed

def freeze(
    self,
    *,
    dataset_version: str | None = None,
    universe_version: str | None = None,
    data_acceptance_id: str | None | object = _ACCEPTANCE_UNSET,
    code_commit: str | None = None,
    trust_mode: DataTrustMode | None = None,
):
    updates: dict[str, object] = {}
    if dataset_version is not None:
        updates["dataset_version"] = dataset_version
    elif self.dataset_version == _CURRENT:
        raise ValueError("dataset_version requests CURRENT without a resolved version")
    if universe_version is not None:
        updates["universe_version"] = universe_version
    elif self.universe_version == _CURRENT:
        raise ValueError("universe_version requests CURRENT without a resolved version")
    if data_acceptance_id is not _ACCEPTANCE_UNSET:
        updates["data_acceptance_id"] = data_acceptance_id
    elif self.data_acceptance_id == CURRENT_ACCEPTED:
        raise ValueError("data_acceptance_id requests CURRENT_ACCEPTED without a resolved id")
    resolved_acceptance = updates.get("data_acceptance_id", self.data_acceptance_id)
    resolved_mode = trust_mode or self.trust_mode
    if resolved_mode is DataTrustMode.RESEARCH and resolved_acceptance is None:
        raise ValueError("research specs require an explicit data_acceptance_id")
    if code_commit is not None:
        updates["code_commit"] = code_commit
    if trust_mode is not None:
        updates["trust_mode"] = trust_mode
    return self.model_copy(update=updates)
```

Add `data_acceptance_id: CURRENT_ACCEPTED` to the repository and synthetic Research YAML specs.

- [ ] **Step 4: Write failing pre-factor gate tests**

```python
def test_research_without_acceptance_fails_before_factor(project):
    provider = CountingFactorProvider()
    runner = ResearchRunner(project.root, factor_provider=provider.provide)
    with pytest.raises(ResearchRunFailed, match="no valid real-data-v1 acceptance"):
        runner.run(project.experiment_spec)
    assert provider.calls == 0
    state = runner.latest_run_manifest()
    assert state.experiment_id is None
    assert state.failed_stage == "acceptance"


def test_research_pins_accepted_identity(accepted_project):
    published = ResearchRunner(accepted_project.root).run(
        accepted_project.experiment_spec
    )
    frozen = yaml.safe_load((published.path / "experiment_spec.yml").read_text())
    assert frozen["data_acceptance_id"] == accepted_project.acceptance_id
    assert frozen["data_acceptance_id"] != "CURRENT_ACCEPTED"
```

- [ ] **Step 5: Resolve acceptance inside `_freeze` before identity creation**

```python
def _freeze(self, spec_path, *, trust_mode):
    path = Path(spec_path)
    if not path.is_absolute():
        path = self._config_root / path
    spec = load_experiment_spec(path)
    dataset_version = spec.dataset_version
    if dataset_version == _CURRENT:
        dataset_version = DatasetPublisher(self._project_root).current().version
    universe_version = spec.universe_version
    if universe_version == _CURRENT:
        universe_version = Universe.from_yaml(
            self._config_root / "configs" / "universe.yml"
        ).version
    acceptance_id = spec.data_acceptance_id
    self._data_acceptance = None
    if trust_mode is DataTrustMode.RESEARCH or acceptance_id not in (None, CURRENT_ACCEPTED):
        selected = AcceptanceRegistry(self._project_root).select(
            dataset_version, acceptance_id or CURRENT_ACCEPTED
        )
        verify_acceptance_bindings(self._project_root, selected)
        acceptance_id = selected.acceptance_id
        self._data_acceptance = acceptance_audit_dict(selected)
    elif trust_mode is DataTrustMode.ENGINEERING:
        acceptance_id = None
    return spec.freeze(
        dataset_version=dataset_version,
        universe_version=universe_version,
        data_acceptance_id=acceptance_id,
        code_commit=self._detect_code_commit() or spec.code_commit,
        trust_mode=trust_mode,
    )
```

Any `NoValidAcceptance` or binding-integrity failure is wrapped as `ResearchRunFailed` with `failed_stage="acceptance"`; no factor provider, portfolio builder, or backtest engine is invoked. Before raising, call `_record_acceptance_preflight_failure`:

```python
def _record_acceptance_preflight_failure(
    self,
    *,
    dataset_version: str,
    universe_version: str,
    error: Exception,
) -> None:
    run_id = f"preflight_acceptance_{uuid.uuid4().hex}"
    run_dir = self._project_root / "data" / "runs" / run_id
    state = RunState(
        run_id=run_id,
        experiment_id=None,
        status=RunStatus.FAILED,
        stage=DataStage.FAILED,
        dataset_version=dataset_version,
        universe_version=universe_version,
        failed_stage="acceptance",
        error={
            "stage": "acceptance",
            "exception_class": type(error).__name__,
            "message": redact_text(str(error), self._secrets),
            "retriable": False,
        },
    )
    write_run_manifest(run_dir, state)
    self._run_id = run_id
    self._run_dir = run_dir
```

Change `RunState.experiment_id` to `str | None`; completed publication and registry code must explicitly reject a null experiment ID.

- [ ] **Step 6: Persist the acceptance audit in every formal artifact**

Add `data_acceptance` to `RunState`, `data_acceptance_id` to `ExperimentManifest`, the audit mapping to `metrics.json["data_acceptance"]`, and the ID to the published manifest dictionary. Assert the manifest ID equals the frozen spec ID during experiment registry validation/rebuild.

```python
state = RunState(
    run_id=run_id,
    experiment_id=identity.experiment_id,
    dataset_version=frozen.dataset_version,
    universe_version=frozen.universe_version,
    data_acceptance=self._data_acceptance,
    trust_mode=frozen.trust_mode.value,
)

metrics["data_acceptance"] = self._data_acceptance or {
    "acceptance_id": None,
    "status": "UNVERIFIED",
}
```

- [ ] **Step 7: Update deterministic fixtures with one fixed acceptance**

For each trusted synthetic project, first save deterministic `FetchResult` frames for the required Tushare and AKShare roles through `RawStore`, then publish the synthetic standardized tables with `origin=data_update`, contract version `1`, required source statuses set to `ok`, and the resulting structured raw bindings. Run the real automated checker, build a fixed `AcceptanceRecord` with `created_at=2022-01-08T00:00:00Z`, operator `integration-fixture`, all manual checks PASS with fixture-local evidence files, and publish it through `AcceptanceRegistry`. Broken/untrusted fixtures publish no ACCEPTED record unless a test explicitly needs a rejected one. This keeps tests offline while exercising the same evidence path as an operator dataset.

- [ ] **Step 8: Run spec, registry, and runner suites**

Run: `pytest tests/unit/test_experiment_spec.py tests/integration/test_experiment_registry.py tests/integration/test_research_runner.py tests/integration/test_end_to_end.py -v`

Expected: PASS; acceptance changes experiment identity, formal runs fail before factors without acceptance, and accepted runs persist one identical ID everywhere.

- [ ] **Step 9: Commit the Research gate**

```bash
git add src/stock_quant/research/spec.py src/stock_quant/research/models.py src/stock_quant/research/registry.py src/stock_quant/research/runner.py configs/experiments/momentum_60d.yml tests/unit/test_experiment_spec.py tests/integration/conftest.py tests/integration/test_experiment_registry.py tests/integration/test_research_runner.py tests/integration/test_end_to_end.py
git commit -m "feat: require accepted data for formal research"
```

---

### Task 7: Report audit, operator documentation, and full verification

**Files:**
- Modify: `src/stock_quant/reporting/html.py`
- Modify: `src/stock_quant/reporting/templates/experiment.html.j2`
- Modify: `tests/integration/test_reports.py`
- Modify: `README.md`
- Modify: `RUNBOOK.md`
- Modify: `PROJECT_MEMORY.md`
- Modify: `docs/operations/phase-one-validation.md`

**Interfaces:**
- Consumes: `metrics.json["data_acceptance"]` and frozen acceptance fields from Task 6.
- Produces: visible acceptance ID/policy/operator/time/status in HTML and complete operator instructions.

- [ ] **Step 1: Write failing report audit test**

```python
def test_experiment_report_shows_data_acceptance(tmp_path):
    report = _experiment_input(data_acceptance={
        "acceptance_id": "a" * 64,
        "policy_version": "real-data-v1",
        "operator_id": "operator-a",
        "created_at": "2026-09-08T12:00:00+00:00",
        "decision": "ACCEPTED",
    })
    path = build_experiment_report(report, tmp_path / "report.html")
    html = path.read_text(encoding="utf-8")
    assert "真实数据验收" in html
    assert "real-data-v1" in html
    assert "operator-a" in html
    assert "a" * 64 in html
```

- [ ] **Step 2: Run the report test to verify it fails**

Run: `pytest tests/integration/test_reports.py::test_experiment_report_shows_data_acceptance -v`

Expected: FAIL because the report input/template has no data acceptance block.

- [ ] **Step 3: Extend report input and template**

Add `data_acceptance: dict[str, object] | None = None` to `ExperimentReportInput`; pass it to the Jinja template as `data_acceptance`. Render a section before performance charts containing decision, policy version, acceptance ID, operator, and UTC acceptance time. If absent, display `UNVERIFIED` prominently and never infer ACCEPTED.

```html
<section class="data-acceptance">
  <h2>真实数据验收</h2>
  {% if data_acceptance %}
  <p>结论：{{ data_acceptance.decision }}</p>
  <p>规则：{{ data_acceptance.policy_version }}</p>
  <p>验收 ID：<code>{{ data_acceptance.acceptance_id }}</code></p>
  <p>操作者：{{ data_acceptance.operator_id }}；时间：{{ data_acceptance.created_at }}</p>
  {% else %}
  <p class="alert">UNVERIFIED：没有绑定真实数据验收记录。</p>
  {% endif %}
</section>
```

- [ ] **Step 4: Update operator documentation and project status**

Document the exact prepare/edit/publish/show flow, the `CURRENT_ACCEPTED` freeze behavior, rejected-record semantics, bootstrap/legacy migration, evidence path restrictions, and the difference between data acceptance and strategy acceptance. In `PROJECT_MEMORY.md`, change the real-data acceptance item from “尚未闭环” to “机制已规划/实现后等待真实操作者验收” only after the complete verification suite passes.

- [ ] **Step 5: Run Ruff and focused offline suites**

Run: `ruff check .`

Expected: PASS.

Run: `pytest tests/unit/test_acceptance_models.py tests/unit/test_acceptance_service.py tests/unit/test_acceptance_checks.py tests/unit/test_experiment_spec.py tests/unit/test_raw_store.py tests/integration/test_acceptance_registry.py tests/integration/test_acceptance_checks.py tests/integration/test_acceptance_cli.py tests/integration/test_data_pipeline.py tests/integration/test_experiment_registry.py tests/integration/test_research_runner.py tests/integration/test_reports.py tests/integration/test_end_to_end.py -q`

Expected: PASS without network access.

- [ ] **Step 6: Run the complete offline suite**

Run: `pytest -q`

Expected: PASS under `not external and not smoke`.

- [ ] **Step 7: Verify policy strings, safety, and formatting**

Run: `rg -n "real-data-v1|CURRENT_ACCEPTED|data_acceptance_id" src configs README.md RUNBOOK.md PROJECT_MEMORY.md docs tests`

Expected: current behavior, examples, fixtures, and docs all use the same exact identifiers.

Run: `rg -n "TUSHARE_TOKEN=.{8,}|AKSHARE_TOKEN=.{8,}|BAOSTOCK_PASSWORD=.{4,}" data/acceptances tests README.md RUNBOOK.md docs 2>/dev/null`

Expected: no matches.

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 8: Commit reporting and documentation**

```bash
git add src/stock_quant/reporting/html.py src/stock_quant/reporting/templates/experiment.html.j2 tests/integration/test_reports.py README.md RUNBOOK.md PROJECT_MEMORY.md docs/operations/phase-one-validation.md
git commit -m "docs: operationalize real data acceptance"
```

- [ ] **Step 9: Record final review evidence**

Run: `git status --short && git log --oneline -7`

Expected: the isolated worktree is clean and shows one focused commit for each task; hand off exact Ruff, focused pytest, and full pytest summaries.
