"""Tests for the single project-root resolver."""

from pathlib import Path

import pytest

from stock_quant.project_root import (
    ProjectRootConfigError,
    ProjectRootPathError,
    resolve_project_root,
)


def make_project(root: Path) -> Path:
    """Create the smallest directory that satisfies a full project root."""

    configs = root / "configs"
    configs.mkdir(parents=True)
    (configs / "project.yml").write_text(
        "start_date: 2020-01-01\nend_date: 2020-12-31\n"
        "initial_cash: 100000\nbenchmark_symbols: [000300.SH]\n"
    )
    (configs / "sources.yml").write_text("tushare: {enabled: false}\n")
    (configs / "costs.yml").write_text("scenarios: []\n")
    return root


def test_complete_root_resolves_to_itself(tmp_path: Path):
    target = make_project(tmp_path / "project")

    assert resolve_project_root(target) == target.resolve()


def test_complete_root_accepts_string(tmp_path: Path):
    target = make_project(tmp_path / "project")

    assert resolve_project_root(str(target)) == target.resolve()


def test_symlink_root_resolves_to_target(tmp_path: Path):
    target = make_project(tmp_path / "target")
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    assert resolve_project_root(link) == target.resolve()


def test_missing_root_exposes_both_paths(tmp_path: Path):
    raw = tmp_path / "missing"

    with pytest.raises(ProjectRootPathError) as caught:
        resolve_project_root(raw)

    assert caught.value.raw_path == str(raw)
    assert caught.value.resolved_path == raw.resolve()
    assert str(raw) in str(caught.value)
    assert str(raw.resolve()) in str(caught.value)


def test_incomplete_root_lists_all_required_configs(tmp_path: Path):
    (tmp_path / "work" / "configs").mkdir(parents=True)

    with pytest.raises(ProjectRootConfigError) as caught:
        resolve_project_root(tmp_path / "work")

    assert caught.value.missing == (
        "configs/costs.yml",
        "configs/project.yml",
        "configs/sources.yml",
    )


def test_incomplete_root_exposes_both_paths(tmp_path: Path):
    root = tmp_path / "work"
    (root / "configs").mkdir(parents=True)
    (root / "configs" / "project.yml").write_text("start_date: 2020-01-01\n")

    with pytest.raises(ProjectRootConfigError) as caught:
        resolve_project_root(root)

    assert caught.value.raw_path == str(root)
    assert caught.value.resolved_path == root.resolve()
