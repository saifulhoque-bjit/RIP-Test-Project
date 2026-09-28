#!/usr/bin/env bash
# Launches the API server and all Celery workers/beat in one gnome-terminal
# window, one tab per process, each tab activating venv first.
#
# Usage: ./scripts/dev_up.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_ACTIVATE="$PROJECT_DIR/venv/bin/activate"

if ! command -v gnome-terminal >/dev/null 2>&1; then
  echo "error: gnome-terminal not found on PATH" >&2
  exit 1
fi

if [[ ! -f "$VENV_ACTIVATE" ]]; then
  echo "error: venv not found at $VENV_ACTIVATE (create it with 'python -m venv venv')" >&2
  exit 1
fi

# title|command pairs — one gnome-terminal tab per entry, in this order.
TABS=(
  "api|uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
  "i1_worker1|celery -A app.core.celery_app.celery_app worker --loglevel=info -P threads --concurrency=4 -n i1_worker1@%h -Q document_parsing,module_feature_generation"
  "i1_worker2|celery -A app.core.celery_app.celery_app worker --loglevel=info -P threads --concurrency=4 -n i1_worker2@%h -Q user_story_generation"
  "i2_worker1|celery -A app.core.celery_app.celery_app worker --loglevel=info -P threads --concurrency=4 -n i2_worker1@%h -Q document_parsing,incremental_update"
  "i2_worker2|celery -A app.core.celery_app.celery_app worker --loglevel=info -P threads --concurrency=10 -n i2_worker2@%h -Q routing,parsing,neo4j_sync,notifications,maintenance"
  "beat|celery -A app.core.celery_app.celery_app beat --loglevel=info"
  "i3_worker1|celery -A app.core.celery_app.celery_app worker --loglevel=info -P threads --concurrency=3 -n i3_worker1@%h -Q module_feature_regeneration"
  "i3_worker2|celery -A app.core.celery_app.celery_app worker --loglevel=info -P threads --concurrency=3 -n i3_worker2@%h -Q user_story_feedback_regeneration"
  "i4_worker1|celery -A app.core.celery_app.celery_app worker --loglevel=info -P prefork --concurrency=5 --max-tasks-per-child=25 -n i4_worker1@%h -Q source_code_processing,source_code_persistence"
  "i4_worker2|celery -A app.core.celery_app.celery_app worker --loglevel=info -P prefork --concurrency=5 --max-tasks-per-child=25 -n i4_worker2@%h -Q source_code_feature_regeneration,source_code_cleanup"
  "i4_worker3|celery -A app.core.celery_app.celery_app worker --loglevel=info -P prefork --concurrency=5 -n i4_worker3@%h -Q source_code_parsing"
)

# Chaining `--tab -- cmd --tab -- cmd ...` in one gnome-terminal invocation is
# unreliable on modern GNOME Terminal (client/server model; extra --tab groups
# can bleed into the previous command's argv instead of opening a new tab).
# Firing one `--tab` invocation per entry is reliable instead: it opens a new
# window implicitly when none exists yet, then attaches a tab to that window
# for every subsequent call. The very first call also has to start the
# gnome-terminal-server process, which is slower than the plain D-Bus calls
# used for the rest, so it gets a longer pause before the next call fires.
for i in "${!TABS[@]}"; do
  entry="${TABS[$i]}"
  title="${entry%%|*}"
  cmd="${entry#*|}"
  # `exec bash` keeps the tab open (with history) after the command exits or errors.
  shell_cmd="cd '$PROJECT_DIR' && source '$VENV_ACTIVATE' && $cmd; exec bash"
  gnome-terminal --tab --title="$title" -- bash -c "$shell_cmd"
  if [[ $i -eq 0 ]]; then
    sleep 1.5
  else
    sleep 0.4
  fi
done
