"""Unit behaviour of the acceptance operator service (Task 5).

``prepare_checklist`` turns one pinned dataset version into the deterministic
operator checklist: fresh automated verdicts plus an all-manual-PENDING
template.  The healthy project fixture mirrors
``tests/integration/test_acceptance_checks.py``
(repository configs plus one stubbed ``data update``), so the automated checks
genuinely pass offline and the publish tests exercise the real evidence path
end to end on ``tmp_path`` projects only.  Nothing touches a network or token.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
import yaml

from stock_quant.bootstrap import bootstrap_dataset
from stock_quant.data_model.universe import Universe
from stock_quant.data_pipeline import DataPipeline, DataUpdateRequest
from stock_quant.data_sources.base import (
    AuthenticationError,
    DataRequest,
    DataSource,
    FetchResult,
    request_key,
)
from stock_quant.research.acceptance.evidence import EvidenceBuildError
from stock_quant.research.acceptance.models import (
    AUTOMATED_CHECK_CODES,
    MANUAL_CHECK_CODES,
    MECHANISABLE_CODES,
    OPERATOR_ONLY_CODES,
    AcceptanceChecklist,
    AcceptanceDecision,
    EvidenceReference,
    ManualCheckResult,
    ManualCheckStatus,
)
from stock_quant.research.acceptance.registry import AcceptanceRegistry
from stock_quant.research.acceptance.service import (
    AcceptanceBindingError,
    AcceptanceRejected,
    acceptance_audit_dict,
    build_checklist,
    prepare_checklist,
    publish_checklist,
    show_acceptances,
    verify_acceptance_bindings,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The update window every fixture dataset is built over (inside the bootstrap
#: weekday calendar span, so the resolved window is fully covered).
_WINDOW_START = date(2021, 11, 1)
_WINDOW_END = date(2021, 11, 30)

#: Fixed clock inputs so checklists and records stay byte-deterministic.
_PREPARED_AT = datetime(2026, 9, 8, tzinfo=timezone.utc)
_CREATED_AT = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)

_FIXTURE_UNIVERSE_SYMBOLS = tuple(
    Universe.from_yaml(_REPO_ROOT / "templates" / "project-config" / "universe.yml").symbols
)
_STOCK_BASIC_LIST_DATE = date(2001, 1, 2)


# --------------------------------------------------------------------------- #
# Offline stub suppliers (same contract as the acceptance-checks fixture)
# --------------------------------------------------------------------------- #


def _weekdays(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


@dataclass(frozen=True)
class StubAdapter:
    """A ``DataSource`` whose ``fetch`` returns deterministic raw frames.

    The ``trade_cal_*`` knobs shape the per-exchange ``trade_cal`` halo
    responses (see ``_trade_cal_frame``); they mirror the Step 1 knobs of the
    ``test_data_pipeline`` stub so every suite drives the calendar step alike.
    """

    name: str
    trade_cal_is_open_by_exchange: dict[tuple[str, date], int] | None = None
    trade_cal_failing_exchanges: tuple[str, ...] = ()
    trade_cal_missing_dates: tuple[date, ...] = ()
    trade_cal_pretrade_overrides: dict[date, str] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "trade_cal_is_open_by_exchange",
            self.trade_cal_is_open_by_exchange or {},
        )
        object.__setattr__(
            self,
            "trade_cal_pretrade_overrides",
            self.trade_cal_pretrade_overrides or {},
        )

    def fetch(self, request: DataRequest) -> FetchResult:
        if (
            request.endpoint == "trade_cal"
            and request.params.get("exchange") in self.trade_cal_failing_exchanges
        ):
            raise AuthenticationError(f"{self.name} supplier failure on trade_cal")
        frame = self._frame(request)
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=frame,
            metadata={
                "source": self.name,
                "sdk_version": "stub",
                "transport_id": self.name,
            },
        )

    def _frame(self, request: DataRequest) -> pd.DataFrame:
        if request.endpoint == "stock_basic":
            return self._stock_basic_frame()
        if request.endpoint == "trade_cal":
            return self._trade_cal_frame(request)
        sessions = _weekdays(request.start_date, request.end_date)
        if request.endpoint == "index_history":
            return self._index_frame(sessions)
        if request.endpoint in (
            "cninfo_corporate_actions",
            "eastmoney_corporate_actions",
            "stock_metadata",
        ):
            return pd.DataFrame()
        symbol = request.symbols[0]
        rows: list[dict[str, object]] = []
        for day in sessions:
            rows.append(
                {
                    "code": symbol,
                    "date": day.strftime("%Y%m%d"),
                    "open": 55.0,
                    "high": 55.0,
                    "low": 55.0,
                    "close": 55.0,
                    "volume": 1000,
                    "amount": 55000.0,
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def _index_frame(sessions: list[date]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "日期": sessions,
                "开盘": [4000.0] * len(sessions),
                "最高": [4000.0] * len(sessions),
                "最低": [4000.0] * len(sessions),
                "收盘": [4000.0] * len(sessions),
                "成交量": [0] * len(sessions),
                "成交额": [0.0] * len(sessions),
            }
        )

    def _stock_basic_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "ts_code": symbol,
                    "name": f"stub_{symbol}",
                    "list_date": _STOCK_BASIC_LIST_DATE.strftime("%Y%m%d"),
                    "delist_date": "",
                    "list_status": "L",
                }
                for symbol in _FIXTURE_UNIVERSE_SYMBOLS
            ]
        )

    def _trade_cal_frame(self, request: DataRequest) -> pd.DataFrame:
        """One row per halo natural day; weekends closed, pretrade chained.

        ``trade_cal_is_open_by_exchange`` is keyed by ``(exchange, date)`` so a
        test can make exactly one exchange disagree; the response carries no
        symbol scope, so the exchange is read from ``request.params``.
        """
        exchange = str(request.params.get("exchange"))
        assert exchange in ("SSE", "SZSE")
        rows: list[dict[str, object]] = []
        current = request.start_date
        while current <= request.end_date:
            if current not in self.trade_cal_missing_dates:
                previous = current - timedelta(days=1)
                while previous.weekday() >= 5:
                    previous -= timedelta(days=1)
                rows.append(
                    {
                        "exchange": exchange,
                        "cal_date": current.strftime("%Y%m%d"),
                        "is_open": self.trade_cal_is_open_by_exchange.get(
                            (exchange, current), 1 if current.weekday() < 5 else 0
                        ),
                        "pretrade_date": self.trade_cal_pretrade_overrides.get(
                            current, previous.strftime("%Y%m%d")
                        ),
                    }
                )
            current += timedelta(days=1)
        return pd.DataFrame(rows)


def _all_stubs() -> dict[str, DataSource]:
    return {
        name: StubAdapter(name)
        for name in ("tushare", "akshare", "baostock")
    }


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AcceptanceProject:
    """One synthetic project pinned to its update-published dataset."""

    root: Path
    version: str


def _published_project(root: Path) -> AcceptanceProject:
    """Baseline bootstrap plus one real stubbed ``data update``."""
    root.mkdir(parents=True)
    configs = root / "configs"
    configs.mkdir()
    for name in (
        "project.yml",
        "sources.yml",
        "costs.yml",
        "trading_rules.yml",
        "universe.yml",
    ):
        shutil.copy(_REPO_ROOT / "templates" / "project-config" / name, configs / name)
    # The fixture's baseline window must equal the stubbed update window: on
    # the repository's wide baseline the bootstrap would seed weekday
    # approximations far beyond November 2021, and a relay update can never
    # replace them -- the acceptance chain's ``calendar_coverage_evidence``
    # check then fails closed on the leftover seed inside the version-bound
    # full-history window (by design).  With equal windows the single relay
    # span replaces the seed entirely.
    project_config = yaml.safe_load(
        (configs / "project.yml").read_text(encoding="utf-8")
    )
    project_config["start_date"] = _WINDOW_START
    project_config["end_date"] = _WINDOW_END
    (configs / "project.yml").write_text(
        yaml.safe_dump(project_config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    # One enabled universe definition, so the update binds a real
    # ``full_history_acceptance_start``: without ``configs/universes/`` the
    # criterion scan is empty and the acceptance chain's
    # ``calendar_coverage_evidence`` check must fail closed.  Mirrors the
    # integration fixture's definition (``tests/integration/conftest.py``).
    universes = configs / "universes"
    universes.mkdir()
    (universes / "custom_acceptance_fixture.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "universe_id": "custom_acceptance_fixture",
                "rules_version": "fixture-rules-v1",
                "membership_table_sha256": "ab" * 32,
                "coverage_start": _WINDOW_START.isoformat(),
                "coverage_end": _WINDOW_END.isoformat(),
                "evidence_summary_sha256": "cd" * 32,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    bootstrap_dataset(root)
    result = DataPipeline(root, sources=_all_stubs()).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )
    assert result.dataset_ref is not None
    return AcceptanceProject(root=root, version=result.dataset_ref.version)


@pytest.fixture
def project(tmp_path):
    """A fresh update-published project per test."""
    return _published_project(tmp_path / "project")


def _sha256_bytes(data: bytes) -> str:
    """The lowercase hex SHA-256 of some in-memory bytes."""
    return hashlib.sha256(data).hexdigest()


def _local_evidence(root: Path, code: str) -> EvidenceReference:
    """One local evidence file inside the project, hashed by content."""
    summary = f"{code} sample verified by operator"
    relative = f"evidence/{code}.txt"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{summary}\n", encoding="utf-8")
    return EvidenceReference(
        kind="local",
        reference=relative,
        sha256=_sha256_bytes(path.read_bytes()),
        summary=summary,
    )


def _external_evidence() -> EvidenceReference:
    """One external reference whose hash pins the UTF-8 summary text."""
    summary = "no secrets found in the repository scan"
    return EvidenceReference(
        kind="external",
        reference="operator-scan-session-2026-09-08",
        sha256=_sha256_bytes(summary.encode("utf-8")),
        summary=summary,
    )


def _completed_manual_checks(root: Path) -> tuple[ManualCheckResult, ...]:
    """Every manual check PASS with exactly one verifiable reference."""
    checks: list[ManualCheckResult] = []
    for code in MANUAL_CHECK_CODES:
        evidence = (
            _external_evidence()
            if code == "secret_scan"
            else _local_evidence(root, code)
        )
        checks.append(
            ManualCheckResult(
                code=code,
                status=ManualCheckStatus.PASS,
                summary=f"{code} sample verified by operator",
                evidence=(evidence,),
            )
        )
    return tuple(checks)


def _write_checklist_yaml(
    project: AcceptanceProject,
    *,
    complete: bool,
    name: str,
) -> Path:
    """Serialise a prepared checklist to YAML, optionally completed."""
    checklist = build_checklist(
        project.root,
        project.version,
        "operator-a",
        prepared_at=_PREPARED_AT,
    )
    if complete:
        checklist = checklist.model_copy(
            update={"manual_checks": _completed_manual_checks(project.root)}
        )
    path = project.root / name
    path.write_text(
        yaml.safe_dump(
            checklist.model_dump(mode="json"),
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def completed_checklist(project):
    """Path to a checklist whose manual rows all PASS with evidence."""
    return _write_checklist_yaml(
        project, complete=True, name="completed-checklist.yml"
    )


@pytest.fixture
def incomplete_checklist(project):
    """Path to the prepared template (manual rows unconfirmed, no evidence)."""
    return _write_checklist_yaml(
        project, complete=False, name="incomplete-checklist.yml"
    )


def _replace_evidence_reference(checklist_path: Path, reference: str) -> Path:
    """Point the first manual evidence row at ``reference`` and rewrite."""
    payload = yaml.safe_load(checklist_path.read_text(encoding="utf-8"))
    payload["manual_checks"][0]["evidence"][0]["reference"] = reference
    escaped = checklist_path.with_name("escaped-checklist.yml")
    escaped.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return escaped


# --------------------------------------------------------------------------- #
# Deterministic checklist preparation (plan Task 5 Steps 1-3)
# --------------------------------------------------------------------------- #


def test_prepare_creates_complete_pending_manual_template(project):
    checklist = build_checklist(
        project.root,
        project.version,
        "operator-a",
        prepared_at=_PREPARED_AT,
    )
    assert [row.code for row in checklist.automated_checks] == list(
        AUTOMATED_CHECK_CODES
    )
    assert [row.code for row in checklist.manual_checks] == list(
        MANUAL_CHECK_CODES
    )
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


def test_build_checklist_is_deterministic_and_strips_the_operator_id(project):
    first = build_checklist(
        project.root, project.version, "  operator-a \n", prepared_at=_PREPARED_AT
    )
    second = build_checklist(
        project.root, project.version, "operator-a", prepared_at=_PREPARED_AT
    )
    assert first == second
    assert first.operator_id == "operator-a"


# --------------------------------------------------------------------------- #
# Fail-closed recomputation and publication (plan Task 5 Steps 4-5)
# --------------------------------------------------------------------------- #


def test_publish_recomputes_checks_and_accepts_complete_checklist(
    project, completed_checklist
):
    record = publish_checklist(
        project.root, completed_checklist, created_at=_CREATED_AT
    )
    assert record.decision is AcceptanceDecision.ACCEPTED
    assert record.reasons == ()
    saved = AcceptanceRegistry(project.root).get(
        project.version, record.acceptance_id
    )
    assert saved == record


def test_publish_records_rejection_before_raising(project, incomplete_checklist):
    with pytest.raises(AcceptanceRejected) as captured:
        publish_checklist(project.root, incomplete_checklist)
    saved = AcceptanceRegistry(project.root).get(
        project.version, captured.value.record.acceptance_id
    )
    assert saved.decision is AcceptanceDecision.REJECTED
    assert saved == captured.value.record


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
    """The read-only verification path must not touch evidence on disk.

    Snapshotting the whole project -- not just the operator ``evidence/``
    directory -- is what makes the assertion bite: a verification path wired to
    the writing ``prepare_checklist`` would materialise
    ``data/acceptance-evidence/<version>/`` (and its checklist output) on disk,
    so the file set would grow and ``before == after`` would fail.
    """
    record = publish_checklist(
        project.root, completed_checklist, created_at=_CREATED_AT
    )
    evidence_path = project.root / "evidence" / f"{MANUAL_CHECK_CODES[0]}.txt"
    original = evidence_path.read_bytes()
    evidence_path.write_bytes(original + b"tamper")
    before = sorted(
        (path.relative_to(project.root).as_posix(), path.read_bytes())
        for path in project.root.rglob("*")
        if path.is_file()
    )
    with pytest.raises(AcceptanceBindingError):
        verify_acceptance_bindings(project.root, record)
    after = sorted(
        (path.relative_to(project.root).as_posix(), path.read_bytes())
        for path in project.root.rglob("*")
        if path.is_file()
    )
    assert before == after


def test_local_evidence_cannot_escape_project(project, completed_checklist):
    escaped = _replace_evidence_reference(completed_checklist, "../outside.txt")
    with pytest.raises(AcceptanceRejected) as captured:
        publish_checklist(project.root, escaped)
    assert any(
        reason.endswith("evidence_path_outside_project")
        for reason in captured.value.record.reasons
    )


def test_unresolvable_local_reference_is_invalid_and_redacted(
    project, completed_checklist
):
    """A NUL-byte reference becomes a reason, never a crash or a stored path."""
    payload = yaml.safe_load(completed_checklist.read_text(encoding="utf-8"))
    payload["manual_checks"][0]["evidence"][0]["reference"] = "bad\x00reference"
    broken = completed_checklist.with_name("nul-checklist.yml")
    broken.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    with pytest.raises(AcceptanceRejected) as captured:
        publish_checklist(project.root, broken)
    assert any(
        reason.endswith("evidence_path_invalid")
        for reason in captured.value.record.reasons
    )
    saved = AcceptanceRegistry(project.root).get(
        project.version, captured.value.record.acceptance_id
    )
    assert saved.decision is AcceptanceDecision.REJECTED
    dumped = saved.model_dump_json()
    assert "bad\x00reference" not in dumped
    assert "unverifiable_local_reference" in dumped


def test_rejected_record_redacts_unpublishable_local_references(
    project, completed_checklist
):
    """Persisted records never carry operator-supplied local paths."""
    payload = yaml.safe_load(completed_checklist.read_text(encoding="utf-8"))
    for check in payload["manual_checks"]:
        for evidence in check["evidence"]:
            if evidence["kind"] == "local":
                evidence["reference"] = str(
                    project.root.parent / "outside-evidence.txt"
                )
    escaped = completed_checklist.with_name("absolute-checklist.yml")
    escaped.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    with pytest.raises(AcceptanceRejected) as captured:
        publish_checklist(project.root, escaped)
    saved = AcceptanceRegistry(project.root).get(
        project.version, captured.value.record.acceptance_id
    )
    dumped = saved.model_dump_json()
    assert "/" not in dumped
    assert str(project.root) not in dumped
    assert "unverifiable_local_reference" in dumped
    assert any(
        reason.endswith("evidence_path_outside_project")
        for reason in saved.reasons
    )


def test_identical_publish_is_idempotent(project, completed_checklist):
    first = publish_checklist(
        project.root, completed_checklist, created_at=_CREATED_AT
    )
    second = publish_checklist(
        project.root, completed_checklist, created_at=_CREATED_AT
    )
    assert second == first
    assert len(show_acceptances(project.root, project.version)) == 1


def test_external_evidence_hash_pins_the_summary(project, completed_checklist):
    payload = yaml.safe_load(completed_checklist.read_text(encoding="utf-8"))
    for check in payload["manual_checks"]:
        for evidence in check["evidence"]:
            if evidence["kind"] == "external":
                evidence["summary"] = "tampered summary"
    tampered = completed_checklist.with_name("tampered-external.yml")
    tampered.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    with pytest.raises(AcceptanceRejected) as captured:
        publish_checklist(project.root, tampered)
    assert any(
        reason.endswith("evidence_hash_changed")
        for reason in captured.value.record.reasons
    )


# --------------------------------------------------------------------------- #
# Research-gate verification surface (consumed by the Task 6 runner gate)
# --------------------------------------------------------------------------- #


def test_verify_acceptance_bindings_accepts_the_published_record(
    project, completed_checklist
):
    record = publish_checklist(
        project.root, completed_checklist, created_at=_CREATED_AT
    )
    verify_acceptance_bindings(project.root, record)


def test_verify_acceptance_bindings_fails_closed_on_expired_policy(
    project, completed_checklist
):
    record = publish_checklist(
        project.root, completed_checklist, created_at=_CREATED_AT
    )
    expired = record.model_copy(update={"policy_version": "real-data-v0"})
    with pytest.raises(AcceptanceBindingError) as captured:
        verify_acceptance_bindings(project.root, expired)
    assert captured.value.reasons == ("policy_version_expired",)


def test_verify_acceptance_bindings_flags_tampered_evidence(
    project, completed_checklist
):
    """Post-publication evidence drift fails the binding check, then heals."""
    record = publish_checklist(
        project.root, completed_checklist, created_at=_CREATED_AT
    )
    verify_acceptance_bindings(project.root, record)
    evidence_path = project.root / "evidence" / f"{MANUAL_CHECK_CODES[0]}.txt"
    original = evidence_path.read_bytes()
    evidence_path.write_bytes(original + b"tamper")
    with pytest.raises(AcceptanceBindingError) as captured:
        verify_acceptance_bindings(project.root, record)
    assert any(
        reason.endswith("evidence_hash_changed")
        for reason in captured.value.reasons
    )
    evidence_path.write_bytes(original)
    verify_acceptance_bindings(project.root, record)


def test_acceptance_audit_dict_carries_public_fields_only(
    project, completed_checklist
):
    record = publish_checklist(
        project.root, completed_checklist, created_at=_CREATED_AT
    )
    audit = acceptance_audit_dict(record)
    assert audit == {
        "acceptance_id": record.acceptance_id,
        "policy_version": "real-data-v1",
        "operator_id": "operator-a",
        "created_at": record.created_at.isoformat(),
        "decision": "ACCEPTED",
    }
    assert str(project.root) not in json.dumps(audit)
