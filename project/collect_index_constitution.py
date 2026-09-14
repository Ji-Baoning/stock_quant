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

import argparse
import hashlib
import importlib.metadata
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pandas as pd

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


def require_pandas_major(version: str, *, minimum: int = 3) -> None:
    """Fail fast when this interpreter cannot read the bundled frames.

    The package's pickles use pandas 3's ``StringDtype``; pandas 2 raises
    ``NotImplementedError`` deep inside ``read_pickle``.  Checking up front
    turns that opaque traceback into an actionable instruction.
    """
    major = int(str(version).split(".", 1)[0])
    if major < minimum:
        raise SystemExit(
            f"index-constitution's bundled frames need pandas >= {minimum} to "
            f"read, but this interpreter has pandas {version}. Run the export "
            "in the isolated interpreter instead, e.g.\n"
            "  /home/ji/miniconda3/envs/sq312/bin/python "
            "project/collect_index_constitution.py"
        )


def _distribution_version() -> str:
    """The installed wheel's version, e.g. ``1.0.0``.

    ``index_constitution.__version__`` is stale (it still reads 0.1.0 while
    the released wheel is 1.0.0), so the distribution metadata is the
    authoritative record for the manifest and the definition's rule version.
    """
    try:
        return importlib.metadata.version("index-constitution")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export the index-constitution csi300 frames into a dated, "
            "immutable snapshot directory (requires pandas >= 3)."
        )
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "snapshot directory; defaults to "
            "data/raw/csi/index_constitution/<today>"
        ),
    )
    return parser


def _default_out_dir(root: Path) -> Path:
    return (
        Path(root) / "data" / "raw" / "csi" / "index_constitution"
        / date.today().isoformat()
    )


def run(
    root: Path,
    ic_module: object,
    *,
    out_dir: Path | None = None,
) -> int:
    """Export the frames under ``root`` (already resolved and validated)."""
    out_dir = out_dir or _default_out_dir(root)
    manifest = export_frames(
        ic_module.history("csi300"),
        ic_module.latest("csi300"),
        ic_module.events(region="cn"),
        out_dir,
        # The module's own __version__ is stale ("0.1.0" while the released
        # wheel is 1.0.0); the installed distribution metadata is authoritative.
        package_version=_distribution_version(),
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}."
        f"{sys.version_info.micro}",
        pandas_version=pd.__version__,
        exported_on=date.today(),
    )
    print(f"snapshot={out_dir}")
    for name, digest in sorted(manifest["files"].items()):
        print(f"  {name} sha256={digest}")
    print(f"manifest sha256={_sha256_file(out_dir / 'manifest.json')}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    require_pandas_major(pd.__version__)

    # Local imports: the module must stay loadable under pandas 2.x so the
    # tests can exercise export_frames in the main environment, and the
    # exporter may run in an isolated interpreter where the package layout
    # differs; nothing project-level is touched before the pandas check.
    from stock_quant.config import load_project_config
    from stock_quant.project_root import resolve_project_root

    import index_constitution as ic

    root = resolve_project_root(args.root)
    config = load_project_config(root)
    return run(root, ic, out_dir=args.out_dir)


if __name__ == "__main__":
    raise SystemExit(main())
