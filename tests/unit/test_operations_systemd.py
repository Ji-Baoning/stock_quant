"""Pure render checks for the committed systemd units (spec 9.4).

Nothing here installs, enables or starts systemd; the assertions pin the
contract the units must keep: the timer path may only call the operations
shell (never ``data update`` directly), the names are fixed, the timezone
is Asia/Shanghai, and missed triggers are never replayed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_quant.operations.systemd_units import (
    INTERPRETER,
    TIMER_UNIT_PREFIX,
    service_unit_name,
    systemd_escape_path,
    timer_unit_name,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SYSTEMD_DIR = _REPO_ROOT / "systemd"


def _unit_text(name: str) -> str:
    path = _SYSTEMD_DIR / name
    assert path.is_file(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


def test_escape_maps_the_real_repo_root():
    assert (
        systemd_escape_path(Path("/home/ji/work/program/stock"))
        == "home-ji-work-program-stock"
    )


def test_escape_disambiguates_dashes_and_spaces():
    assert (
        systemd_escape_path(Path("/data/projects/a-b c"))
        == "data-projects-a\\x2db\\x20c"
    )


def test_escape_requires_an_absolute_root():
    with pytest.raises(ValueError):
        systemd_escape_path(Path("relative/root"))


def test_unit_names_use_the_fixed_prefix_and_the_instance():
    root = Path("/home/ji/work/program/stock")
    assert TIMER_UNIT_PREFIX == "stock-quant-data-update"
    assert INTERPRETER == "/home/ji/miniconda3/envs/sq312/bin/python"
    assert timer_unit_name(root) == (
        "stock-quant-data-update@home-ji-work-program-stock.timer"
    )
    template = "stock-quant-data-update@home-quant-data-update.service"
    assert service_unit_name(root) == template.replace(
        "home-quant-data-update", "home-ji-work-program-stock"
    )


def test_the_committed_files_use_the_fixed_template_names():
    assert (_SYSTEMD_DIR / "stock-quant-data-update@.service").is_file()
    assert (_SYSTEMD_DIR / "stock-quant-data-update@.timer").is_file()


def test_service_execstart_only_calls_the_operations_shell():
    service = _unit_text("stock-quant-data-update@.service")
    exec_start = next(
        line for line in service.splitlines() if line.startswith("ExecStart=")
    )
    # 只调 operations update（内层 data update 的输出/锁/记录才能进 job 体系），
    # 且解释器是钉死的 sq312 入口；%f 把实例名还原为绝对项目根。
    assert exec_start == (
        f"ExecStart={INTERPRETER} -m stock_quant operations update --root %f"
    )
    assert "stock_quant data update" not in service
    assert "Restart=no" in service


def test_timer_is_shanghai_non_persistent_and_targets_the_service():
    timer = _unit_text("stock-quant-data-update@.timer")
    on_calendar = next(
        line for line in timer.splitlines() if line.startswith("OnCalendar=")
    )
    assert on_calendar.endswith("Asia/Shanghai")
    assert "Persistent=false" in timer
    assert "Unit=stock-quant-data-update@%i.service" in timer
