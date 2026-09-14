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
            "--operator", _OPERATOR, "--root", str(root),
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
            "--operator", _OPERATOR, "--root", str(root),
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
