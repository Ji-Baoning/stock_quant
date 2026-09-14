"""Shared fixtures for the acceptance unit tests.

The worksheet tests need a project root with one really published dataset
version -- the same thing the service tests build -- so it is exposed here as
a fixture rather than duplicated or imported across test modules.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from unit.test_acceptance_service import AcceptanceProject, _published_project


@pytest.fixture
def published_project(tmp_path: Path) -> AcceptanceProject:
    """A project root holding one published, validated dataset version."""
    return _published_project(tmp_path / "project")
