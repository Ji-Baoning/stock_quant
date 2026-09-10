"""The index-constitution export module writes an immutable dated snapshot.

Loaded by path because ``project/`` is not a package (repo convention, see
``test_index_membership_checks.py``).  The module must stay importable under
pandas 2.x: it may not import ``index_constitution`` at module level.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_export_module():
    path = REPO_ROOT / "project" / "collect_index_constitution.py"
    spec = importlib.util.spec_from_file_location("collect_index_constitution", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    history = pd.DataFrame(
        {
            "symbol": ["SZ000001", "SH600000"],
            "name": ["平安银行", "浦发银行"],
            "opt-in": pd.to_datetime(["2005-04-08", "2005-04-08"]),
            "opt-out": pd.to_datetime([None, "2007-04-30"]),
        }
    )
    latest = pd.DataFrame(
        {
            "symbol": ["SZ000001"],
            "name": ["平安银行"],
            "opt-in": pd.to_datetime(["2005-04-08"]),
        }
    )
    events = pd.DataFrame(
        {
            "event_date": pd.to_datetime(["2007-12-26"]),
            "event_type": ["merger"],
            "old_symbol": ["SH600472"],
            "new_symbol": ["SH601600"],
            "old_name": ["包头铝业"],
            "new_name": ["中国铝业"],
            "source_url": ["https://www.sse.com.cn/"],
            "notes": ["merger note"],
        }
    )
    return history, latest, events


def test_export_frames_writes_three_csvs_and_a_manifest(tmp_path: Path):
    module = _load_export_module()
    history, latest, events = _frames()
    out_dir = tmp_path / "2026-09-10"

    manifest = module.export_frames(
        history,
        latest,
        events,
        out_dir,
        package_version="1.0.0",
        python_version="3.11.9",
        pandas_version="3.0.5",
        exported_on=date(2026, 9, 10),
    )

    assert sorted(manifest["files"]) == sorted(
        [
            "csi300_history.csv",
            "csi300_latest.csv",
            "cn_events.csv",
        ]
    )
    assert (out_dir / "manifest.json").is_file()
    for name in manifest["files"]:
        assert (out_dir / name).is_file()


def test_export_frames_round_trips_every_row_and_column(tmp_path: Path):
    module = _load_export_module()
    history, latest, events = _frames()
    out_dir = tmp_path / "2026-09-10"

    module.export_frames(
        history,
        latest,
        events,
        out_dir,
        package_version="1.0.0",
        python_version="3.11.9",
        pandas_version="3.0.5",
        exported_on=date(2026, 9, 10),
    )

    written = pd.read_csv(out_dir / "csi300_history.csv")
    assert list(written.columns) == list(history.columns)
    assert len(written) == len(history)
    assert written["symbol"].tolist() == history["symbol"].tolist()


def test_manifest_hashes_match_the_written_bytes(tmp_path: Path):
    module = _load_export_module()
    history, latest, events = _frames()
    out_dir = tmp_path / "2026-09-10"

    manifest = module.export_frames(
        history,
        latest,
        events,
        out_dir,
        package_version="1.0.0",
        python_version="3.11.9",
        pandas_version="3.0.5",
        exported_on=date(2026, 9, 10),
    )

    for name, recorded in manifest["files"].items():
        assert module._sha256_file(out_dir / name) == recorded
    assert manifest["package_version"] == "1.0.0"
    assert manifest["pandas_version"] == "3.0.5"
    assert manifest["exported_on"] == "2026-09-10"


def test_export_frames_refuses_to_overwrite_an_existing_snapshot(tmp_path: Path):
    module = _load_export_module()
    history, latest, events = _frames()
    out_dir = tmp_path / "2026-09-10"
    out_dir.mkdir()

    with pytest.raises(FileExistsError):
        module.export_frames(
            history,
            latest,
            events,
            out_dir,
            package_version="1.0.0",
            python_version="3.11.9",
            pandas_version="3.0.5",
            exported_on=date(2026, 9, 10),
        )


def test_module_does_not_import_index_constitution_at_module_level():
    """The module must load under pandas 2.x, where the package is unreadable."""
    module = _load_export_module()
    assert not hasattr(module, "ic")
    source = (REPO_ROOT / "project" / "collect_index_constitution.py").read_text(
        encoding="utf-8"
    )
    # The only allowed occurrence is the local import inside main().
    assert source.count("import index_constitution") == 1
    assert "    import index_constitution as ic" in source


def test_require_pandas_major_rejects_pandas_2():
    module = _load_export_module()
    with pytest.raises(SystemExit) as excinfo:
        module.require_pandas_major("2.3.3")
    assert "sq312" in str(excinfo.value) or "pandas" in str(excinfo.value)


def test_require_pandas_major_accepts_pandas_3():
    module = _load_export_module()
    module.require_pandas_major("3.0.5")  # must not raise


def test_parser_defaults_to_todays_dated_directory():
    module = _load_export_module()
    args = module.build_parser().parse_args([])
    assert args.out_dir is None  # main() derives the dated path


def test_parser_accepts_an_explicit_out_dir(tmp_path: Path):
    module = _load_export_module()
    args = module.build_parser().parse_args(["--out-dir", str(tmp_path / "x")])
    assert args.out_dir == tmp_path / "x"


def test_module_level_import_guard_is_a_local_import():
    """Re-assert Task 1's structural constraint now that main() exists."""
    source = (
        REPO_ROOT / "project" / "collect_index_constitution.py"
    ).read_text(encoding="utf-8")
    assert source.count("import index_constitution") == 1
    assert "    import index_constitution as ic" in source
