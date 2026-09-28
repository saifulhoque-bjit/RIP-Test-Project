"""Backlog context supplied to the incremental update LLM pipeline.

Controls whether the LLM receives the complete project backlog (FULL) or a
selector-filtered slice of relevant features (SUBSET).
"""

from __future__ import annotations

from enum import Enum


class ContextMode(str, Enum):
    FULL = "full"
    SUBSET = "subset"


CONTEXT_MODE_VALUES: tuple[str, ...] = tuple(context_mode.value for context_mode in ContextMode)

# Human-readable labels for API responses.
CONTEXT_MODE_DISPLAY_LABELS: dict[str, str] = {
    ContextMode.FULL.value: "Full",
    ContextMode.SUBSET.value: "Subset",
}
