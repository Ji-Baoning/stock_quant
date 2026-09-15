"""The package's one YAML entry point for configuration that must fail closed.

``yaml.safe_load`` keeps the *last* of two identical mapping keys, so a document
that declares an execution-affecting input twice (two ``trust_mode`` lines in an
experiment spec, say) loads under a value no reader of the file can see.  Every
document this package loads from disk decides an identity, an acceptance
decision, a trading rule or a universe: submit-to-freeze correspondence,
acceptance evidence and cost/limit rules all break silently if a repeated key
is allowed to overwrite its twin.  Such documents therefore go through
:func:`read_yaml` / :func:`load_yaml`, which reject a repeated mapping key at
any nesting level.

A YAML merge key (``<<``) is not a repeat: merging then overriding a key
explicitly is the language's own rule, so only the keys written out are
compared.  The check is structural -- it runs before any mapping is
constructed, so it never fails on an anchor/alias structure and never depends
on how a value would be typed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

#: The YAML tag PyYAML resolves a merge key (``<<``) to.  A quoted ``"<<"``
#: stays a plain string key and is therefore checked like any other.
_MERGE_TAG = "tag:yaml.org,2002:merge"


class DuplicateKeyError(yaml.YAMLError, ValueError):
    """A YAML document declares the same mapping key more than once.

    Both bases are deliberate.  It is a parse error, so callers that already
    translate ``yaml.YAMLError`` into their own fail-closed category (the
    acceptance worksheet's unreadable program/signature blocks, the external
    trading-rule input) keep doing so; and it is a value error, so the CLI
    paths that report ``invalid_checklist`` for unusable input keep reporting
    it instead of letting a traceback escape.
    """


class UniqueKeyLoader(yaml.SafeLoader):
    """A safe loader whose mappings reject a key the document repeats."""

    def construct_mapping(self, node: Any, deep: bool = False) -> Any:
        if not isinstance(node, yaml.MappingNode):
            raise yaml.constructor.ConstructorError(
                None, None, "expected a mapping node", node.start_mark
            )
        explicit: set[Any] = set()
        for key_node, _ in node.value:
            if key_node.tag == _MERGE_TAG:
                continue
            key = self.construct_object(key_node, deep=deep)
            if key in explicit:
                raise DuplicateKeyError(f"duplicate key {key!r}")
            explicit.add(key)
        return super().construct_mapping(node, deep=deep)


def load_yaml(text: str, *, source: str | None = None) -> Any:
    """Parse one YAML document from ``text``, rejecting a repeated mapping key.

    ``source`` names the document in the error message (a path, or whatever
    identifies an embedded block).  An empty document yields ``None``, as
    ``yaml.safe_load`` does.
    """
    try:
        return yaml.load(text, Loader=UniqueKeyLoader)
    except DuplicateKeyError as error:
        if source is None:
            raise
        raise DuplicateKeyError(f"{source} declares a {error}") from error


def read_yaml(path: str | Path) -> Any:
    """Read ``path`` as UTF-8 and parse it with :func:`load_yaml`."""
    path = Path(path)
    return load_yaml(path.read_text(encoding="utf-8"), source=str(path))
