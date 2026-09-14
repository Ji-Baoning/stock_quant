"""Version-bound evidence pack behaviour on a real fixture project."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from stock_quant.research.acceptance.checks import _window
from stock_quant.research.acceptance.evidence import (
    MECHANISABLE_CODES,
    build_mechanisable_evidence,
)


def _manifest(root: Path, version: str) -> dict[str, object]:
    return json.loads(
        (
            root / "data" / "standardized" / version / "dataset_manifest.json"
        ).read_text(encoding="utf-8")
    )


def test_pack_is_version_bound_relative_and_hash_valid(fixture_root):
    refs = build_mechanisable_evidence(fixture_root.root, fixture_root.version)
    assert list(refs) == list(MECHANISABLE_CODES)
    for code, reference in refs.items():
        assert reference.kind == "local"
        assert not Path(reference.reference).is_absolute()
        path = fixture_root.root / reference.reference
        assert path.is_file(), code
        assert hashlib.sha256(path.read_bytes()).hexdigest() == reference.sha256
    assert all(
        reference.reference.startswith("data/acceptance-evidence/")
        for reference in refs.values()
    )


def test_pack_records_the_window_the_automated_check_uses(fixture_root):
    refs = build_mechanisable_evidence(fixture_root.root, fixture_root.version)
    start, end = _window(
        _manifest(fixture_root.root, fixture_root.version)["build_config"]
    )
    for code in ("missing_reason_sample", "benchmark_sample"):
        payload = json.loads(
            (fixture_root.root / refs[code].reference).read_text(encoding="utf-8")
        )
        assert payload["window"] == {
            "start": start.isoformat(),
            "end": end.isoformat(),
        }


def test_pack_rebuild_is_byte_identical(fixture_root):
    first = build_mechanisable_evidence(fixture_root.root, fixture_root.version)
    second = build_mechanisable_evidence(fixture_root.root, fixture_root.version)
    assert first == second
