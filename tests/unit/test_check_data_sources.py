"""Root-explicit entry contract for the project scripts.

The diagnostic script must read its supplier gate from the project
configuration reached through ``--root``: a source disabled by
``configs/sources.yml`` is skipped with a ``SKIP`` line and its constructor is
never invoked, and a root that is not a valid project root fails before any
data store or supplier is touched.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from stock_quant.project_root import ProjectRootError

PROJECT = Path(__file__).resolve().parents[2] / "project"
sys.path.insert(0, str(PROJECT))

import audit_raw_provenance  # noqa: E402
import check_data_sources  # noqa: E402
import crosscheck_calendar_relay  # noqa: E402


def make_project(
    root: Path, *, baostock_enabled: bool = False
) -> Path:
    """Create the smallest directory that satisfies a full project root.

    Mirrors the fixture in ``tests/unit/test_project_root.py`` but declares all
    three supplier families so the diagnostic's builder table resolves every
    ``config.sources[name]`` lookup.
    """
    configs = root / "configs"
    configs.mkdir(parents=True)
    (configs / "project.yml").write_text(
        "start_date: 2020-01-01\nend_date: 2020-12-31\n"
        "initial_cash: 100000\nbenchmark_symbols: [000300.SH]\n"
    )
    (configs / "sources.yml").write_text(
        "tushare: {enabled: false}\n"
        "akshare: {enabled: false}\n"
        f"baostock: {{enabled: {str(baostock_enabled).lower()}}}\n"
    )
    (configs / "costs.yml").write_text("scenarios: []\n")
    return root


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch: pytest.MonkeyPatch):
    """Keep the environment from turning a unit test into a network call."""
    for name in (
        "TUSHARE_TOKEN",
        "TUSHARE_RELAY_URL",
        "TUSHARE_RELAY_KEY",
        "TUSHARE_PROXY_URL",
        "TUSHARE_PROXY_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_disabled_baostock_is_skipped_without_construction(tmp_path, monkeypatch, capsys):
    root = make_project(tmp_path, baostock_enabled=False)
    called = []
    monkeypatch.setattr(check_data_sources, "BaoStockSource", lambda *_: called.append(1))
    assert check_data_sources.main(["--root", str(root)]) == 0
    assert called == []
    assert "baostock: SKIP disabled by config" in capsys.readouterr().out


def test_disabled_sources_are_all_skipped_without_construction(
    tmp_path, monkeypatch, capsys
):
    """Every disabled supplier prints SKIP and builds nothing."""
    root = make_project(tmp_path, baostock_enabled=False)
    for name in ("TushareSource", "AkShareSource", "BaoStockSource"):
        monkeypatch.setattr(
            check_data_sources, name, lambda *_: pytest.fail("built a disabled source")
        )
    assert check_data_sources.main(["--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "tushare: SKIP disabled by config" in out
    assert "akshare: SKIP disabled by config" in out
    assert "baostock: SKIP disabled by config" in out


def test_enabled_source_is_still_probed(tmp_path, monkeypatch, capsys):
    """The SKIP filter must not swallow enabled suppliers."""
    root = make_project(tmp_path / "project")
    (root / "configs" / "sources.yml").write_text(
        "tushare: {enabled: false}\n"
        "akshare: {enabled: false}\n"
        "baostock: {enabled: true}\n"
    )
    monkeypatch.setattr(
        check_data_sources,
        "BaoStockSource",
        lambda *_: (_ for _ in ()).throw(RuntimeError("no network in unit tests")),
    )
    assert check_data_sources.main(["--root", str(root)]) == 1
    out = capsys.readouterr().out
    assert "baostock: SKIP" not in out
    assert "baostock: FAIL RuntimeError" in out


def test_default_root_is_the_working_directory(tmp_path, monkeypatch, capsys):
    """Omitting ``--root`` resolves ``.`` against the current directory."""
    root = make_project(tmp_path)
    monkeypatch.chdir(root)
    for name in ("TushareSource", "AkShareSource", "BaoStockSource"):
        monkeypatch.setattr(
            check_data_sources, name, lambda *_: pytest.fail("built a disabled source")
        )
    assert check_data_sources.main([]) == 0
    assert "baostock: SKIP disabled by config" in capsys.readouterr().out


def test_crosscheck_fails_on_an_empty_root_before_touching_data_or_relay(
    tmp_path, monkeypatch
):
    """The root error must fire before the dataset or the relay is touched."""
    empty = tmp_path / "empty"
    empty.mkdir()
    touched = {"store": 0, "relay": 0}
    monkeypatch.setattr(
        crosscheck_calendar_relay,
        "DatasetPublisher",
        lambda *_: touched.__setitem__("store", touched["store"] + 1),
    )
    monkeypatch.setattr(
        crosscheck_calendar_relay,
        "TushareRelayClient",
        type(
            "Relay",
            (),
            {"from_env": staticmethod(lambda: touched.__setitem__("relay", 1))},
        ),
    )
    with pytest.raises(ProjectRootError):
        crosscheck_calendar_relay.main(["--root", str(empty)])
    assert touched == {"store": 0, "relay": 0}


def test_audit_fails_on_an_empty_root_before_scanning_the_raw_store(
    tmp_path, monkeypatch
):
    """The root error must fire before ``scan`` opens ``data/raw``."""
    empty = tmp_path / "empty"
    empty.mkdir()
    scanned = []
    monkeypatch.setattr(
        audit_raw_provenance, "scan", lambda root: scanned.append(root)
    )
    with pytest.raises(ProjectRootError):
        audit_raw_provenance.main(["--root", str(empty)])
    assert scanned == []
