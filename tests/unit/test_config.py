from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_quant.config import ProjectConfig, load_project_config


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
