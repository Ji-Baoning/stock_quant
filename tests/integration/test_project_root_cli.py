"""Repository-root regression guards for the runtime project-root boundary.

The repository root must never carry a live ``configs/`` tree: the committed
configuration lives in ``templates/project-config`` purely as a copy source and
``resolve_project_root`` never looks there.  These tests pin that contract:

1. Running a command with ``--root .`` from the repository root must fail
   before any service is constructed (``configs/project.yml`` no longer exists
   there, and the template directory is not a fallback target).
2. An explicit ``--root`` pointing at a real working project must be the only
   root handed to the services -- the CLI resolves it and passes exactly that
   path to ``DataPipeline``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from stock_quant.cli import app

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_CONFIG = _REPO_ROOT / "templates" / "project-config"

_COPIED_CONFIG_NAMES = (
    "project.yml",
    "sources.yml",
    "costs.yml",
)


def test_repository_root_never_falls_back_to_template(
    monkeypatch: pytest.MonkeyPatch, cli_runner
):
    """``--root .`` from the repository root fails before any service exists."""
    calls: list[str] = []
    monkeypatch.setattr(
        "stock_quant.cli.DataPipeline", lambda *_: calls.append("pipeline")
    )
    monkeypatch.chdir(_REPO_ROOT)

    result = cli_runner.invoke(app, ["data", "validate", "--root", "."])

    assert result.exit_code != 0
    assert "configs/project.yml" in result.output
    assert calls == []


def test_explicit_root_only_passes_that_root_to_services(
    monkeypatch: pytest.MonkeyPatch, cli_runner, tmp_path: Path
):
    """A real working root is resolved once and handed to ``DataPipeline``."""
    received: list[Path] = []

    def _recording_pipeline(project_root: Path):
        received.append(Path(project_root))
        raise RuntimeError("recording pipeline reached")

    monkeypatch.setattr("stock_quant.cli.DataPipeline", _recording_pipeline)
    config_dir = tmp_path / "configs"
    config_dir.mkdir(parents=True)
    for name in _COPIED_CONFIG_NAMES:
        shutil.copy(_TEMPLATE_CONFIG / name, config_dir / name)

    result = cli_runner.invoke(
        app, ["data", "validate", "--root", str(tmp_path), "--version", "any"]
    )

    assert received, "DataPipeline was never constructed for the explicit root"
    assert received == [tmp_path.resolve()]
    # The fake pipeline is the service under the CLI: its failure (not the
    # project-root resolution) is what surfaces, proving the CLI got that far.
    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)
    assert str(result.exception) == "recording pipeline reached"
