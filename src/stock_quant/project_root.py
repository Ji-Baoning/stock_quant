"""Single source of truth for runtime project-root resolution.

Every command reads configuration and data from the project root given on the
command line (or ``.`` when ``--root`` is omitted).  There is deliberately no
fallback: the repository root, a ``project/`` sibling, or the current working
directory are never guessed.  ``resolve_project_root`` follows symbolic links
and reports both the raw and the resolved path on failure.
"""

from pathlib import Path

#: A directory is a valid project root only when it carries all of these.
REQUIRED_PROJECT_CONFIGS = (
    "configs/project.yml",
    "configs/sources.yml",
    "configs/costs.yml",
)


class ProjectRootError(Exception):
    """Base class for project-root resolution failures."""


class ProjectRootPathError(ProjectRootError):
    """The given root does not exist or is not a directory."""

    def __init__(self, raw_path: str, resolved_path: Path) -> None:
        self.raw_path = raw_path
        self.resolved_path = resolved_path
        super().__init__(
            f"project root does not exist or is not a directory: "
            f"{raw_path!r} (resolved: {resolved_path})"
        )


class ProjectRootConfigError(ProjectRootError):
    """The root exists but is missing required configuration files."""

    def __init__(
        self, raw_path: str, resolved_path: Path, missing: tuple[str, ...]
    ) -> None:
        self.raw_path = raw_path
        self.resolved_path = resolved_path
        self.missing = missing
        super().__init__(
            f"project root {raw_path!r} (resolved: {resolved_path}) is missing "
            f"required config files: {', '.join(missing)}"
        )


def resolve_project_root(root: str | Path) -> Path:
    """Return the validated, symlink-resolved project root for ``root``."""

    raw_path = str(root)
    resolved = Path(root).expanduser().resolve()
    if not resolved.is_dir():
        raise ProjectRootPathError(raw_path, resolved)
    missing = tuple(
        sorted(
            item
            for item in REQUIRED_PROJECT_CONFIGS
            if not (resolved / item).is_file()
        )
    )
    if missing:
        raise ProjectRootConfigError(raw_path, resolved, missing)
    return resolved
