"""Context-aware raw output for the source-code pipeline.

The pipeline still contains legacy ``print()`` calls in deeply nested helpers.
This module tags those writes at the process boundary without changing the
pipeline's execution guards or the structured logging handlers.
"""

from __future__ import annotations

import io
import sys
import threading
from typing import Any

from app.utils.log_context import get_log_context


class _TaggingStream(io.TextIOBase):
    """Prefix complete raw-output lines with the current project identifier."""

    def __init__(self, target: Any) -> None:
        self._target = target
        self._at_line_start = True
        self._lock = threading.RLock()

    def write(self, text: str) -> int:
        if not text:
            return 0

        with self._lock:
            project_id = get_log_context().get("project_id")
            if not project_id:
                written = self._target.write(text)
                self._at_line_start = text.endswith(("\n", "\r"))
                return written

            prefix = f"[project_id={project_id}] "
            output: list[str] = []
            for chunk in text.splitlines(keepends=True):
                if self._at_line_start and chunk.strip("\r\n"):
                    output.append(prefix)
                output.append(chunk)
                self._at_line_start = chunk.endswith(("\n", "\r"))

            rendered = "".join(output)
            self._target.write(rendered)
            return len(text)

    def flush(self) -> None:
        self._target.flush()

    def isatty(self) -> bool:
        return self._target.isatty()

    @property
    def encoding(self) -> str | None:
        return getattr(self._target, "encoding", None)


_install_lock = threading.Lock()


def ensure_stdout_tagging() -> None:
    """Install the context-aware stdout wrapper once per process."""
    with _install_lock:
        if isinstance(sys.stdout, _TaggingStream):
            return
        sys.stdout = _TaggingStream(sys.stdout)