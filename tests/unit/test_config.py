from datetime import time as dt_time
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_quant.config import ProjectConfig, load_project_config
from stock_quant.project_root import (
    ProjectRootConfigError,
    ProjectRootError,
    ProjectRootPathError,
)


def test_project_config_publication_time_defaults_to_1500():
    config = ProjectConfig(
        start_date="2020-01-01",
        end_date="2021-12-31",
        initial_cash=100000,
        benchmark_symbols=["000300.SH", "000905.SH"],
    )
    assert config.publication_time == dt_time(15, 0)


def test_project_config_parses_configured_publication_time():
    config = ProjectConfig(
        start_date="2020-01-01",
        end_date="2021-12-31",
        initial_cash=100000,
        benchmark_symbols=["000300.SH"],
        publication_time="18:30",
    )
    assert config.publication_time == dt_time(18, 30)


def test_project_config_rejects_end_before_start():
    with pytest.raises(ValidationError):
        ProjectConfig(
            start_date="2020-02-01",
            end_date="2020-01-01",
            initial_cash=100000,
            benchmark_symbols=["000300.SH", "000905.SH"],
        )


def test_load_project_config_never_contains_tushare_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/project.yml").write_text(
        "start_date: 2020-01-01\nend_date: 2020-12-31\n"
        "initial_cash: 100000\nbenchmark_symbols: [000300.SH, 000905.SH]\n"
    )
    (tmp_path / "configs/sources.yml").write_text("tushare: {enabled: true}\n")
    (tmp_path / "configs/costs.yml").write_text("rates: []\n")
    monkeypatch.setenv("TUSHARE_TOKEN", "secret-value")

    loaded = load_project_config(tmp_path)

    assert "secret-value" not in loaded.model_dump_json()


def test_load_project_config_missing_root_raises_project_root_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    #: The repository root has no live ``configs/`` tree (only the committed
    #: template under ``templates/project-config``); running from there must
    #: not give a fallback target for an unrelated missing root.
    monkeypatch.chdir(Path(__file__).resolve().parents[2])

    with pytest.raises(ProjectRootPathError):
        load_project_config(tmp_path / "nowhere")


def test_load_project_config_incomplete_root_raises_project_root_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/project.yml").write_text(
        "start_date: 2020-01-01\nend_date: 2020-12-31\n"
        "initial_cash: 100000\nbenchmark_symbols: [000300.SH]\n"
    )
    monkeypatch.chdir(Path(__file__).resolve().parents[2])

    with pytest.raises(ProjectRootConfigError) as caught:
        load_project_config(tmp_path)

    assert caught.value.missing == ("configs/costs.yml", "configs/sources.yml")


def test_load_project_config_errors_share_a_base_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ProjectRootError):
        load_project_config(".")
    with pytest.raises(ProjectRootError):
        load_project_config(tmp_path)
