"""Human-readable short-code generation for Tenant/Project identifiers.

Jira-style: a multi-word name becomes the initials of its first few words
("Acme Corp" -> "AC"); a single-word name becomes its own leading characters
("Acme" -> "ACME"). Collisions are resolved by the caller appending an
incrementing numeric suffix via :func:`bump_code_suffix`.
"""

from __future__ import annotations

import re

_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")
_MAX_SINGLE_WORD_LENGTH = 6
_MIN_CODE_LENGTH = 2
_MAX_INITIAL_WORDS = 4


def generate_tenant_code_candidate(name: str) -> str:
    """Derive a base short code from *name* (before collision suffixing)."""
    words = [w for w in re.split(r"\s+", name.strip()) if _NON_ALNUM_RE.sub("", w)]

    if len(words) >= 2:
        base = "".join(_NON_ALNUM_RE.sub("", w)[:1] for w in words[:_MAX_INITIAL_WORDS]).upper()
    elif words:
        base = _NON_ALNUM_RE.sub("", words[0]).upper()[:_MAX_SINGLE_WORD_LENGTH]
    else:
        base = ""

    if len(base) < _MIN_CODE_LENGTH:
        base = (base + "XX")[:_MIN_CODE_LENGTH] if base else "TN"
    return base


def bump_code_suffix(base_code: str, attempt: int) -> str:
    """Return *base_code* with a numeric suffix for the given retry *attempt*.

    ``attempt`` is 1-based; callers should use the bare *base_code* for
    ``attempt == 1`` and only call this for ``attempt >= 2``.
    """
    return f"{base_code}{attempt}"
