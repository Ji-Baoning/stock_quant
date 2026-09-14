"""A bootstrap dataset must explain its calendar as an offline seed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from stock_quant.bootstrap import bootstrap_dataset
from stock_quant.data_model.calendar_coverage import SOURCE_BOOTSTRAP_SEED
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "project.yml").write_text(
        yaml.safe_dump(
            {
                "start_date": "2020-01-01",
                "end_date": "2020-01-31",
                "benchmark_symbols": ["000300.SH"],
                "publication_time": "15:00",
            }
        ),
        encoding="utf-8",
    )
    (configs / "universe.yml").write_text(
        yaml.safe_dump(
            {
                "selected_as_of": "2020-01-01",
                "entries": [
                    {
                        "symbol": "600000.SH",
                        "name_at_selection": "浦发银行",
                        "exchange": "SH",
                        "board": "sh_main",
                        "selected_as_of": "2020-01-01",
                        "boundary_tags": ["engineering_smoke"],
                        "selection_reason": "种子日历单元测试",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_bootstrap_writes_one_seed_span_and_exposes_it(project_root):
    result = bootstrap_dataset(project_root)
    with DatasetReader(project_root).open(result.version) as context:
        build = context.manifest["build_config"]
    assert build["calendar_coverage"] == [
        {
            "start_date": result.start.isoformat(),
            "end_date": result.end.isoformat(),
            "source": SOURCE_BOOTSTRAP_SEED,
        }
    ]
    assert build["full_history_acceptance_start"] is None


def test_bootstrap_span_payload_is_the_published_manifest_bytes(project_root):
    result = bootstrap_dataset(project_root)
    manifest_path = (
        DatasetPublisher(project_root).standardized_root
        / result.version
        / "dataset_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    span = manifest["build_config"]["calendar_coverage"][0]
    assert "snapshot_sha256s" not in span
