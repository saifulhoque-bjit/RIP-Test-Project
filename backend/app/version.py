"""Single source of truth for the application version.

Fields
──────
__version__   SemVer string — bump manually on every release.
              MAJOR: breaking API change (removed endpoint, changed response shape)
              MINOR: new feature, backward-compatible
              PATCH: bug fix, backward-compatible

__sprint__    Sprint identifier — bump at the start of each sprint.
              Format: "S<number>" e.g. "S1", "S2", "S3".
              Override via SPRINT env var:  SPRINT=S2
              Hidden (empty string) when APP_ENV=production.

__release__   Full release label exposed in /health and /.
              Combines version + sprint + build metadata so every running
              instance can be traced back to an exact sprint, commit and date.
              Override at build/deploy time via BUILD_COMMIT, BUILD_DATE and
              BUILD_TAG environment variables (injected by CI or Docker build
              args):

              In production (APP_ENV=production), the git-tag component is
              replaced with the plain __version__ and the sprint segment is
              dropped, since this repo's git tags embed sprint/branch naming
              directly (e.g. "MVP_SP3_MID_FWP_BK_V1.0.0") which would
              otherwise leak internal sprint cadence.

                  BUILD_TAG=$(git describe --tags --abbrev=0)
                  BUILD_COMMIT=$(git rev-parse --short HEAD)
                  BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
                  SPRINT=S1

Release process
───────────────
1. Bump __version__ and __sprint__ below.
2. Commit:  git commit -am "chore: release v0.0.1 sprint S1"
3. Tag:     git tag -a v0.0.1 -m "release v0.0.1 sprint S1"
4. Push:    git push origin <branch> --tags
5. Docker:  docker build
                --build-arg BUILD_TAG=$(git describe --tags --abbrev=0)
                --build-arg BUILD_COMMIT=$(git rev-parse --short HEAD)
                --build-arg BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
                --build-arg SPRINT=S1
                -t rip-backend:0.0.1 .
"""

from __future__ import annotations

from datetime import UTC, datetime
import os
import subprocess

from app.core.config import settings


def _git_tag() -> str:
    """Return the most recent git tag reachable from HEAD (e.g. 'v0.0.1').

    Falls back to 'untagged' when git is unavailable or no tag exists.
    In production this is overridden by the BUILD_TAG env var injected
    during the Docker build / CI pipeline:

        BUILD_TAG=$(git describe --tags --abbrev=0)
    """
    tag = os.getenv("BUILD_TAG")
    if tag:
        return tag
    try:
        return subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "untagged"


# ── Semantic version ───────────────────────────────────────────────────────
__version__ = "MVP-1.0.0"

# ── Sprint identifier (bump at the start of each sprint) ──────────────────
# Hidden in production so external/customer-facing responses don't leak
# internal sprint cadence.
__sprint__ = "" if settings.APP_ENV == "production" else os.getenv("SPRINT_VERSION", "SPV9.0.0")

# ── Build metadata (injected by CI / Docker --build-arg) ──────────────────
# In production, use __version__ (the plain semver) instead of the git tag —
# this repo's git tags embed internal sprint/branch naming directly in the
# tag itself (e.g. "MVP_SP3_MID_FWP_BK_V1.0.0"), which would leak sprint
# cadence even with __sprint__ hidden.
_tag = __version__ if settings.APP_ENV == "production" else _git_tag()
_commit = os.getenv("BUILD_COMMIT", "prod")
_date = os.getenv("BUILD_DATE", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))

# ── Release label  e.g. "v0.0.1-S1+a3f9c12.2026-04-27T10:30:00Z" ─────────
# (sprint segment omitted when __sprint__ is hidden, e.g. "v0.0.1+a3f9c12...")
_sprint_segment = f"-{__sprint__}" if __sprint__ else ""
__release__ = f"{_tag}{_sprint_segment}+{_commit}.{_date}"
