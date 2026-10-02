"""The dependency boundary: duckdb is core, the service stack is an extra."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _project_table() -> dict:
    with PYPROJECT.open("rb") as stream:
        return tomllib.load(stream)["project"]


def _dependency_names(dependencies: list[str]) -> set[str]:
    names = set()
    for entry in dependencies:
        match = re.match(r"[A-Za-z0-9_.-]+", entry.strip())
        assert match is not None, entry
        names.add(match.group(0).lower())
    return names


def test_duckdb_is_a_core_dependency() -> None:
    assert "duckdb" in _dependency_names(_project_table()["dependencies"])


def test_the_service_extra_is_exactly_fastapi_and_uvicorn() -> None:
    extras = _project_table()["optional-dependencies"]["service"]
    assert _dependency_names(extras) == {"fastapi", "uvicorn"}


def test_the_core_install_pulls_no_service_stack() -> None:
    core = _dependency_names(_project_table()["dependencies"])
    assert not core & {"fastapi", "uvicorn", "starlette"}
