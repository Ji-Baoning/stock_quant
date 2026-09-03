"""Structured JSONL pipeline logging with deterministic secret redaction.

The research runner emits every event as a single JSON object per line with a
fixed context field set -- ``timestamp``, ``severity``, ``run_id``, ``stage``,
``source``, ``symbol``, ``event`` and ``message`` -- so logs can be filtered and
replayed deterministically.  Before a record reaches the file or terminal,
:func:`redact_text` removes every configured secret literal and masks the
values of sensitive key/value pairs (``token``, ``authorization``,
``api_key`` and synonyms), so a secret can never be persisted to a log line, a
terminal or -- via the same helper -- a run manifest.

The file holds one JSON object per line; the terminal shows the same record as
a human line.  Both destinations are redacted again as whole lines as a
belt-and-braces guarantee that escaping or context fields cannot smuggle a
secret back in.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, TextIO

#: Replacement emitted wherever a secret or sensitive value would appear.
REDACTED = "[REDACTED]"

_SENSITIVE_KEY = re.compile(
    r"""(?ix)
    (?<![A-Za-z0-9_])
    (?P<key>token|auth(?:orization)?|api[_-]?key|password|passwd|
              secret|credential|access[_-]?key|private[_-]?key)
    \b"?\s*[=:]\s*
    (?P<value>"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;:}"'\]\)]+)
    """
)

#: Severity names ``StructuredLogger`` understands; anything else is passed
#: through so callers may use their own labels.
_SEVERITIES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def redact_text(text: object, secrets: Iterable[object] = ()) -> str:
    """Return ``text`` with configured secret values and sensitive values masked.

    Literal secret values are removed verbatim wherever they appear; the values
    of recognised sensitive keys (``token``, ``authorization``, ``api_key`` and
    friends) are masked by key/value pattern so unconfigured tokens are still
    removed from ``token=...`` / ``"token": ...`` / ``token: ...`` shapes.
    Non-string inputs are stringified first.
    """
    masked = str(text)
    for secret in secrets:
        if secret is None or str(secret) == "":
            continue
        masked = masked.replace(str(secret), REDACTED)
    return _SENSITIVE_KEY.sub(_mask_sensitive_value, masked)


def _mask_sensitive_value(match: "re.Match[str]") -> str:
    return f"{match.group('key')}{REDACTED}"


class StructuredLogger:
    """Writes redacted structured records to an optional JSONL file and terminal.

    ``path``, when given, receives one JSON object per line; ``terminal`` prints
    a human line to ``stream`` (default stdout) so interactive progress is
    visible while the file stays machine-parseable.  Both are redacted.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        terminal: bool = True,
        stream: TextIO | None = None,
        secrets: Iterable[object] = (),
    ) -> None:
        self._path = Path(path) if path is not None else None
        self._terminal = bool(terminal)
        # ``stream`` is resolved lazily per write (not pinned at construction):
        # pytest and other harnesses swap ``sys.stdout`` between fixture setup
        # and the test body, so a cached file handle can go stale/closed.
        self._stream = stream
        self._secrets = tuple(secrets)
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Public surface
    # ------------------------------------------------------------------ #

    @property
    def path(self) -> Path | None:
        """The JSONL destination, or ``None`` when logging is file-less."""
        return self._path

    @property
    def secrets(self) -> tuple[object, ...]:
        """The configured secret literals redacted from every output."""
        return self._secrets

    def debug(self, message, *, run_id=None, stage=None, source=None, symbol=None,
              event=None) -> None:
        self.log("DEBUG", message, run_id=run_id, stage=stage, source=source,
                 symbol=symbol, event=event)

    def info(self, message, *, run_id=None, stage=None, source=None, symbol=None,
             event=None) -> None:
        self.log("INFO", message, run_id=run_id, stage=stage, source=source,
                 symbol=symbol, event=event)

    def warning(self, message, *, run_id=None, stage=None, source=None, symbol=None,
                event=None) -> None:
        self.log("WARNING", message, run_id=run_id, stage=stage, source=source,
                 symbol=symbol, event=event)

    def error(self, message, *, run_id=None, stage=None, source=None, symbol=None,
              event=None) -> None:
        self.log("ERROR", message, run_id=run_id, stage=stage, source=source,
                 symbol=symbol, event=event)

    def critical(self, message, *, run_id=None, stage=None, source=None, symbol=None,
                 event=None) -> None:
        self.log("CRITICAL", message, run_id=run_id, stage=stage, source=source,
                 symbol=symbol, event=event)

    def log(self, severity, message, *, run_id=None, stage=None, source=None,
            symbol=None, event=None) -> None:
        """Emit one structured record after redacting its content."""
        if not isinstance(severity, str) or not severity.strip():
            raise ValueError(f"severity must be a non-empty string: {severity!r}")
        now = _utc_now()
        record = {
            "timestamp": now,
            "severity": severity,
            "run_id": run_id,
            "stage": stage,
            "source": source,
            "symbol": symbol,
            "event": event,
            "message": redact_text(message, self._secrets),
        }
        json_line = json.dumps(record, ensure_ascii=False, sort_keys=False)
        json_line = redact_text(json_line, self._secrets)
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json_line + "\n")
        if self._terminal:
            safe_message = redact_text(message, self._secrets)
            line = f"[{now}] {severity}"
            context = " ".join(
                str(value)
                for value in (run_id, stage, source, symbol, event)
                if value not in (None, "")
            )
            if context:
                line += f" [{context}]"
            line += f": {safe_message}"
            stream = self._stream if self._stream is not None else sys.stdout
            print(redact_text(line, self._secrets), file=stream)


def _utc_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
