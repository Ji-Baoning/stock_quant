"""CLI behaviour of the ``data acceptance`` operator group (Task 5).

The full operator loop runs against a real pipeline build: prepare writes the
redacted YAML checklist, an operator edit completes the manual rows with
project-local evidence files, publish re-verifies everything and records the
decision, and show lists the registry history.  Rejections must be persisted
before the command exits nonzero, identical republishes are idempotent, and
no stdout line ever carries an absolute local path.  Offline only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml
from conftest import build_fixture_project  # noqa: E402
from test_acceptance_checks import _all_stubs  # noqa: E402

from stock_quant.cli import app  # noqa: E402
from stock_quant.data_pipeline import (  # noqa: E402
    DataPipeline,
    DataUpdateRequest,
)
from stock_quant.research.acceptance import service  # noqa: E402
from stock_quant.research.acceptance.registry import (  # noqa: E402
    AcceptanceRegistry,
)

_WINDOW_START = date(2021, 11, 1)
_WINDOW_END = date(2021, 11, 30)


@dataclass(frozen=True)
class AcceptanceProject:
    """One synthetic project pinned to its update-published dataset."""

    root: Path
    version: str


@pytest.fixture
def project(tmp_path):
    """Baseline fixture project plus one real stubbed ``data update``."""
    base = build_fixture_project(tmp_path / "project")
    result = DataPipeline(base.root, sources=_all_stubs()).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )
    assert result.dataset_ref is not None
    return AcceptanceProject(root=base.root, version=result.dataset_ref.version)


def _invoke(cli_runner, project: AcceptanceProject, *args: str):
    return cli_runner.invoke(
        app, ["data", "acceptance", *args, "--root", str(project.root)]
    )


def _prepare(cli_runner, project: AcceptanceProject) -> Path:
    """Run ``data acceptance prepare`` and return the checklist path."""
    output = project.root / "checklist.yml"
    result = _invoke(
        cli_runner,
        project,
        "prepare",
        "--version",
        project.version,
        "--operator",
        "operator-a",
        "--output",
        str(output),
    )
    assert result.exit_code == 0, result.stdout
    assert "checklist=checklist.yml" in result.stdout
    return output


def _complete_checklist(project: AcceptanceProject, path: Path) -> Path:
    """The operator edit: every manual row PASS with project-local evidence."""
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    for check in payload["manual_checks"]:
        summary = f"{check['code']} verified"
        relative = f"evidence/{check['code']}.txt"
        evidence_path = project.root / relative
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(f"{summary}\n", encoding="utf-8")
        check["status"] = "PASS"
        check["summary"] = summary
        check["evidence"] = [
            {
                "kind": "local",
                "reference": relative,
                "sha256": hashlib.sha256(
                    evidence_path.read_bytes()
                ).hexdigest(),
                "summary": summary,
            }
        ]
    completed = path.with_name("completed.yml")
    completed.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return completed


def _acceptance_id(stdout: str) -> str:
    """The ``acceptance_id=`` value printed by ``publish``."""
    for line in stdout.splitlines():
        if line.startswith("acceptance_id="):
            return line.split("=", 1)[1]
    raise AssertionError(f"no acceptance_id line in stdout:\n{stdout}")


def test_show_reports_unaccepted_before_any_publish(cli_runner, project):
    result = _invoke(cli_runner, project, "show", "--version", project.version)
    assert result.exit_code == 0, result.stdout
    assert result.stdout.strip() == "UNACCEPTED"


def test_prepare_publish_show_round_trip(cli_runner, project):
    checklist = _prepare(cli_runner, project)
    assert str(project.root) not in checklist.read_text(encoding="utf-8")
    completed = _complete_checklist(project, checklist)

    published = _invoke(
        cli_runner, project, "publish", "--checklist", str(completed)
    )
    assert published.exit_code == 0, published.stdout
    acceptance_id = _acceptance_id(published.stdout)
    assert "decision=ACCEPTED" in published.stdout
    assert str(project.root) not in published.stdout

    saved = AcceptanceRegistry(project.root).get(project.version, acceptance_id)
    assert saved.decision.value == "ACCEPTED"

    shown = _invoke(cli_runner, project, "show", "--version", project.version)
    assert shown.exit_code == 0, shown.stdout
    assert "UNACCEPTED" not in shown.stdout
    assert acceptance_id in shown.stdout
    assert "real-data-v1 ACCEPTED" in shown.stdout
    assert str(project.root) not in shown.stdout


def test_incomplete_checklist_publishes_rejection_then_exits_nonzero(
    cli_runner, project
):
    checklist = _prepare(cli_runner, project)
    result = _invoke(
        cli_runner, project, "publish", "--checklist", str(checklist)
    )
    assert result.exit_code == 1
    assert "decision=REJECTED" in result.stdout
    saved = AcceptanceRegistry(project.root).get(
        project.version, _acceptance_id(result.stdout)
    )
    assert saved.decision.value == "REJECTED"
    assert any(
        reason.startswith("manual_") and reason.endswith("_failed")
        for reason in saved.reasons
    )


def test_double_publish_is_idempotent(cli_runner, project, monkeypatch):
    checklist = _complete_checklist(project, _prepare(cli_runner, project))

    class _FrozenDatetime(datetime):
        """One fixed ``now`` so both publishes collide on one identity."""

        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 8, 12, tzinfo=timezone.utc)

    monkeypatch.setattr(service, "datetime", _FrozenDatetime)
    first = _invoke(cli_runner, project, "publish", "--checklist", str(checklist))
    second = _invoke(cli_runner, project, "publish", "--checklist", str(checklist))
    assert first.exit_code == 0, first.stdout
    assert second.exit_code == 0, second.stdout
    assert _acceptance_id(first.stdout) == _acceptance_id(second.stdout)
    assert len(AcceptanceRegistry(project.root).list(project.version)) == 1


def test_tampered_checklist_field_is_rejected_with_field_changed(
    cli_runner, project
):
    completed = _complete_checklist(project, _prepare(cli_runner, project))
    payload = yaml.safe_load(completed.read_text(encoding="utf-8"))
    payload["dataset_manifest_sha256"] = "f" * 64
    tampered = completed.with_name("tampered.yml")
    tampered.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    result = _invoke(
        cli_runner, project, "publish", "--checklist", str(tampered)
    )
    assert result.exit_code == 1
    assert "decision=REJECTED" in result.stdout
    assert "reason=dataset_manifest_sha256_changed" in result.stdout


def test_evidence_traversal_attempt_is_rejected(cli_runner, project):
    completed = _complete_checklist(project, _prepare(cli_runner, project))
    payload = yaml.safe_load(completed.read_text(encoding="utf-8"))
    payload["manual_checks"][0]["evidence"][0]["reference"] = "../outside.txt"
    escaped = completed.with_name("escaped.yml")
    escaped.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    result = _invoke(cli_runner, project, "publish", "--checklist", str(escaped))
    assert result.exit_code == 1
    assert "evidence_path_outside_project" in result.stdout


def test_invalid_checklist_yaml_exits_nonzero_without_publishing(
    cli_runner, project
):
    """Unparseable input is not a rejection decision: nothing is recorded."""
    garbage = project.root / "garbage.yml"
    garbage.write_text("not a checklist\n", encoding="utf-8")
    result = _invoke(cli_runner, project, "publish", "--checklist", str(garbage))
    assert result.exit_code == 1
    assert "reason=invalid_checklist" in result.stdout
    assert AcceptanceRegistry(project.root).list(project.version) == ()


def test_show_lists_history_oldest_first(cli_runner, project):
    checklist = _prepare(cli_runner, project)
    completed = _complete_checklist(project, checklist)
    first = _invoke(cli_runner, project, "publish", "--checklist", str(completed))

    payload = yaml.safe_load(completed.read_text(encoding="utf-8"))
    payload["operator_id"] = "operator-b"
    resigned = completed.with_name("resigned.yml")
    resigned.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    second = _invoke(
        cli_runner, project, "publish", "--checklist", str(resigned)
    )

    assert first.exit_code == 0, first.stdout
    assert second.exit_code == 0, second.stdout
    shown = _invoke(cli_runner, project, "show", "--version", project.version)
    lines = shown.stdout.strip().splitlines()
    assert len(lines) == 2
    assert _acceptance_id(first.stdout) in lines[0]
    assert _acceptance_id(second.stdout) in lines[1]
    assert lines[0].endswith("ACCEPTED")
