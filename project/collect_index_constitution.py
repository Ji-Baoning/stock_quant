"""Export the index-constitution csi300 frames into a dated immutable snapshot.

Runs in an **isolated interpreter** (Python >= 3.11 with pandas >= 3): the
package's bundled pickles are serialized with pandas 3's ``StringDtype`` and
raise ``NotImplementedError`` when read by pandas 2.x, which this project pins.
The project itself never imports ``index_constitution`` -- this script writes
plain CSVs that the main environment reads with ``pd.read_csv``.

Output goes to ``data/raw/csi/index_constitution/<YYYY-MM-DD>/`` and is never
overwritten: a new upstream release gets a new dated directory, so every
snapshot hash stays resolvable and historical builds stay reproducible.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
SOURCE = "index_constitution"
SOURCE_URL = "https://github.com/unliftedq/index-constitution"

#: Upstream frame -> snapshot file name.  ``cn_events`` is audit material
#: only; it never enters the membership chain.
FRAME_FILES = (
    ("csi300_history", "csi300_history.csv"),
    ("csi300_latest", "csi300_latest.csv"),
    ("cn_events", "cn_events.csv"),
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_frames(
    history: pd.DataFrame,
    latest: pd.DataFrame,
    events: pd.DataFrame,
    out_dir: Path,
    *,
    package_version: str,
    python_version: str,
    pandas_version: str,
    exported_on: date,
) -> dict:
    """Write the three frames as CSV plus a manifest, and return the manifest.

    The target directory must not exist: snapshots are immutable evidence, so
    a re-export always lands in a fresh dated directory rather than
    overwriting hashes an earlier build may still pin.
    """
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise FileExistsError(
            f"snapshot directory {out_dir} already exists; snapshots are "
            "immutable -- choose a new dated directory instead of overwriting"
        )
    out_dir.mkdir(parents=True)
    frames = {"csi300_history": history, "csi300_latest": latest, "cn_events": events}
    files: dict[str, str] = {}
    for key, name in FRAME_FILES:
        path = out_dir / name
        frames[key].to_csv(path, index=False)
        files[name] = _sha256_file(path)
    manifest = {
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "package_version": package_version,
        "python_version": python_version,
        "pandas_version": pandas_version,
        "exported_on": exported_on.isoformat(),
        "files": files,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8"
    )
    return manifest
