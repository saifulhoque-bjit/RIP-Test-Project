"""Celery application factory."""

from __future__ import annotations

import logging
import socket

from celery import Celery
from celery.signals import (
    before_task_publish,
    setup_logging,
    task_postrun,
    task_prerun,
    task_revoked,
    worker_process_init,
)

from app.core.config import settings
from app.core.constants import (
    CELERY_BROKER_VISIBILITY_TIMEOUT,
    QUEUE_DOCUMENT_PARSING,
    QUEUE_INCREMENTAL_UPDATE,
    QUEUE_MAINTENANCE,
    QUEUE_MODULE_FEATURE_GENERATION,
    QUEUE_MODULE_FEATURE_REGENERATION,
    QUEUE_NEO4J_SYNC,
    QUEUE_NOTIFICATIONS,
    QUEUE_PARSING,
    QUEUE_ROUTING,
    QUEUE_SOURCE_CODE_CLEANUP,
    QUEUE_SOURCE_CODE_FEATURE_REGENERATION,
    QUEUE_SOURCE_CODE_PARSING,
    QUEUE_SOURCE_CODE_PERSISTENCE,
    QUEUE_SOURCE_CODE_PROCESSING,
    QUEUE_USER_STORY_FEEDBACK_REGENERATION,
    QUEUE_USER_STORY_GENERATION,
    STALE_INGESTION_SWEEP_INTERVAL_SECONDS,
)


@setup_logging.connect
def _use_app_json_logging(loglevel: int | str | None = None, **_kwargs: object) -> None:
    """Take over Celery's logging setup so worker logs match the API's JSON format.

    Connecting to this signal tells Celery to skip its own default logging
    config entirely; without it, broker/consumer internals (kombu, redis)
    print raw multi-line tracebacks instead of the structured, single-line
    JSON the rest of the app emits. This only swaps the handler/formatter —
    the level each record is logged at (INFO/WARNING/ERROR) is untouched, so
    reconnect warnings and connection-failure errors keep their original
    severity and ``exc_info`` traceback, just serialized as JSON.

    ``loglevel`` here is whatever the process was started with, e.g.
    ``celery worker --loglevel=info``; root logger level mirrors it so
    nothing is filtered out that the operator asked to see.
    """
    from app.utils.logger import configure_root_json_logging  # noqa: PLC0415

    configure_root_json_logging(loglevel or logging.INFO)


@before_task_publish.connect
def _attach_log_context_to_task(headers: dict | None = None, **_kwargs: object) -> None:
    """Stamp the publishing request's correlation_id/project_id/... onto the task message.

    Queues are split across separate worker containers in production (see
    task_routes below), so a task dispatched by one process is very likely
    picked up by another — request-scoped ContextVars (correlation_id,
    log_context) do not cross that boundary on their own. Carrying them in
    the message headers (not args/kwargs, so no task signature changes are
    needed) lets ``_bind_log_context_from_task`` below restore them inside
    the worker process that actually runs the task, so its logs can still be
    traced back to the originating request/project.
    """
    from app.utils.correlation import get_correlation_id  # noqa: PLC0415
    from app.utils.log_context import get_log_context  # noqa: PLC0415

    if headers is None:
        return
    context = dict(get_log_context())
    context["correlation_id"] = get_correlation_id()
    headers["log_context"] = context


@task_prerun.connect
def _bind_log_context_from_task(task: object = None, **_kwargs: object) -> None:
    """Restore the originating request's log context for this task's execution."""
    from app.utils.correlation import set_correlation_id  # noqa: PLC0415
    from app.utils.log_context import set_log_context  # noqa: PLC0415

    request = getattr(task, "request", None)
    data = dict(getattr(request, "log_context", None) or {})
    set_correlation_id(data.pop("correlation_id", None) or "none")
    set_log_context(data)


@task_postrun.connect
def _unbind_log_context_from_task(**_kwargs: object) -> None:
    """Clear the log context so it doesn't leak into the next task on this thread."""
    from app.utils.correlation import set_correlation_id  # noqa: PLC0415
    from app.utils.log_context import set_log_context  # noqa: PLC0415

    set_correlation_id("none")
    set_log_context({})


@task_revoked.connect
def _mark_project_task_cancelled_on_revoke(request: object = None, **_kwargs: object) -> None:
    """Reconcile ProjectTask + its Source/SourceIngestion rows when Celery revokes a task.

    ``ProjectTaskService.cancel_request`` already writes "cancelled"
    synchronously — task, sources, and ingestion alike — and calls
    ``control.revoke`` for queued tasks, so on the normal cancel path this
    handler finds the row already terminal and does nothing (see
    ``ProjectTaskRepository.update_status``). It remains as the backstop for a
    revoke that originates elsewhere, e.g. an operator running
    ``celery ... control revoke <task_id>`` directly against the broker: revoke's
    own in-memory registry is not persisted (a worker restart forgets it), so a
    task's own cooperative check (``app/core/task_control.py``) is the durable
    stopping mechanism — this handler's job is only to reconcile the DB rows
    for a revoke that never went through ``cancel_request``.

    Delegates to the same ``ProjectTaskService`` helpers ``cancel_request``
    itself uses (``_sources_for_task`` / ``_mark_cancelled`` /
    ``_rollback_source_code_run``) so an out-of-band revoke can never leave a
    Source/SourceIngestion row stuck reading "running"/"processing" forever —
    the Pipelines UI renders those, not the ProjectTask status.
    """
    from app.core.constants import TASK_STATUS_CANCELLED  # noqa: PLC0415
    from app.db.unit_of_work import UnitOfWork  # noqa: PLC0415
    from app.services.project_task_service import ProjectTaskService  # noqa: PLC0415

    celery_task_id = getattr(request, "id", None)
    if not celery_task_id:
        return

    with UnitOfWork() as uow:
        task = uow.project_tasks.get_by_celery_task_id(celery_task_id)
        if task is None or task.status == TASK_STATUS_CANCELLED:
            return
        source_ids, is_source_code = ProjectTaskService._sources_for_task(uow, task)
        uow.project_tasks.update_status(task.id, status=TASK_STATUS_CANCELLED, progress=100)
        uow.commit()

    if is_source_code:
        ProjectTaskService._rollback_source_code_run(task, source_ids)
    else:
        ProjectTaskService._mark_cancelled(task, source_ids)


@worker_process_init.connect
def _reset_forked_child_connections(**_kwargs: object) -> None:
    """Give each freshly-forked prefork child its own DB/Neo4j connections.

    Only fires under the ``prefork`` pool (the source-code queues — see the
    instance table below); ``threads``/``solo`` pools never call ``os.fork()``
    so this never runs for those queues and they are unaffected.

    ``app.db.session.engine``/``async_engine`` are created at import time
    (before fork, since ``UnitOfWork`` pulls in ``app.db.session`` transitively
    via this module's ``include=`` list) and the Neo4j driver is a
    lazily-created singleton; both hold real TCP sockets that must NOT be
    shared by two OS processes — two processes issuing queries over one
    inherited socket corrupts both. See ``dispose_engines_after_fork`` /
    ``reset_driver_after_fork`` docstrings for why we drop rather than close
    them here.

    Nothing needs to be done here for the source-code pipeline's process-wide
    LLM circuit-breaker (``llm_client._BREAKER``) — none of this module's
    ``include=`` targets import that module at top level, so it's only ever
    first imported lazily, inside a task body, i.e. already inside the child
    process. Combined with ``--max-tasks-per-child=1`` on the source-code
    queues, every module-processing task gets a brand-new interpreter and
    therefore a never-before-tripped breaker for free — this is what actually
    fixes the "one project's timeout storm permanently poisons every other
    project on that worker" bug the old long-lived-threads-pool deployment had.
    """
    from app.db.neo4j import reset_driver_after_fork  # noqa: PLC0415
    from app.db.session import dispose_engines_after_fork  # noqa: PLC0415
    from app.utils.pipeline_output import ensure_stdout_tagging  # noqa: PLC0415

    dispose_engines_after_fork()
    reset_driver_after_fork()
    ensure_stdout_tagging()


celery_app = Celery(
    "rip_worker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.workers.tasks",
        "app.workers.project_tasks",
        "app.workers.process_source_tasks",
        "app.workers.document_task",
        "app.workers.image_task",
        "app.workers.source_code_task",
        "app.workers.incremental_task",
        "app.workers.jira_sync_task",
        "app.workers.maintenance_task",
    ],
)

_broker_transport_options: dict[str, object] = {
    "retry_on_timeout": True,
    "socket_keepalive": settings.CELERY_REDIS_SOCKET_KEEPALIVE,
    "socket_connect_timeout": settings.CELERY_BROKER_SOCKET_CONNECT_TIMEOUT,
    "health_check_interval": settings.CELERY_BROKER_HEALTH_CHECK_INTERVAL,
    "visibility_timeout": CELERY_BROKER_VISIBILITY_TIMEOUT,
}
if settings.CELERY_BROKER_SOCKET_TIMEOUT is not None:
    _broker_transport_options["socket_timeout"] = settings.CELERY_BROKER_SOCKET_TIMEOUT
if settings.CELERY_REDIS_SOCKET_KEEPALIVE and hasattr(socket, "TCP_KEEPIDLE"):
    # Linux-only TCP_KEEPIDLE/INTVL/CNT (absent on macOS dev machines, hence the
    # guard) — see the setting docstrings in app/core/config.py for why the OS
    # default keepalive timing isn't aggressive enough on AWS.
    _broker_transport_options["socket_keepalive_options"] = {
        socket.TCP_KEEPIDLE: settings.CELERY_REDIS_SOCKET_KEEPALIVE_IDLE,
        socket.TCP_KEEPINTVL: settings.CELERY_REDIS_SOCKET_KEEPALIVE_INTVL,
        socket.TCP_KEEPCNT: settings.CELERY_REDIS_SOCKET_KEEPALIVE_CNT,
    }

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Global fallback limits for lightweight tasks (routing, notifications).
    # Heavy document/AI tasks override these per-task via soft_time_limit /
    # time_limit on the @celery_app.task decorator.
    task_soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,
    task_time_limit=settings.CELERY_TASK_TIME_LIMIT,
    # Background processing tasks do not need their results read back by the
    # API.  Setting this prevents the Redis result backend from opening a
    # pub/sub subscription on every apply_async() call, which was the root
    # cause of "Retry limit exceeded" connection errors when the broker was
    # momentarily unreachable during container startup.
    task_ignore_result=True,
    # Keep broker/result connections alive and recover quickly from transient
    # Redis disconnects that are common behind managed-network idling.
    broker_connection_retry=True,
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=settings.CELERY_BROKER_CONNECTION_MAX_RETRIES,
    # Do NOT cancel tasks when the broker connection is lost.  Tasks can run
    # for up to 25 h (source-code pipeline); killing them on a transient Redis
    # reconnect would discard hours of work.  Celery 6.0 will flip this
    # default to True, so we pin it explicitly to preserve expected behaviour.
    worker_cancel_long_running_tasks_on_connection_loss=False,
    # Cap the broker connection pool to avoid connection storms during
    # reconnect loops (each worker thread tries to grab a connection; without
    # a limit, a crash-loop can exhaust Redis's client slots before recovering).
    broker_pool_limit=10,
    broker_transport_options=_broker_transport_options,
    redis_retry_on_timeout=True,
    redis_socket_keepalive=settings.CELERY_REDIS_SOCKET_KEEPALIVE,
    redis_socket_connect_timeout=settings.CELERY_RESULT_SOCKET_CONNECT_TIMEOUT,
    redis_health_check_interval=settings.CELERY_RESULT_HEALTH_CHECK_INTERVAL,
    # ── Queue routing ──────────────────────────────────────────────────────
    # Tasks are routed to dedicated queues so worker pools can be scaled
    # independently.  In development you can start a single worker without
    # the -Q flag and it will consume the default "celery" queue.
    #
    # Production worker startup — 4 instances x 2 workers each. Every queue is
    # consumed by exactly one worker except "document_parsing", which is split
    # across i1_worker1 (instance 1) and i2_worker1 (instance 2) for extra
    # capacity — cross-instance queue sharing works the same way as
    # same-instance sharing (competing consumers via Redis), it just spans two
    # machines instead of one. Concurrency below is sized from each instance's
    # actual cpu/ram (see table), not a flat guess — tune further per observed
    # queue depth (app/utils/queue_monitoring.py).
    #
    # Instance | cpu | ram | workers | notes
    # 1        | 2   | 4   | 2 (fixed) | LLM generation + document parsing — mostly I/O-wait, some real CPU (LLM stream accumulation)
    # 2        | 1   | 2   | 2         | smallest box; worker2's tasks are quick/cheap so tolerate more threads than worker1's parsing/LLM calls
    # 3        | 1   | 2   | 2         | low-volume regeneration paths
    # 4        | 2   | 8   | 3         | source-code pipeline — runs -P prefork (not threads, see below): worker1 (process/persist) at --concurrency=5 + --max-tasks-per-child=25, worker2 (feature-regen/cleanup) at --concurrency=5 + --max-tasks-per-child=25, worker3 (the per-project orchestrator, dedicated) at --concurrency=5; worker1/worker2 recycle every 25 tasks, trading some LLM-circuit-breaker cross-project isolation for throughput — see the detailed tradeoff notes below. worker3 is intentionally separate from worker1 — see its own note below for why.
    #
    # Instance 1 (2 cpu / 4 ram) — module/feature generation + document parsing + user story generation
    #   celery -A app.core.celery_app.celery_app worker --loglevel=info -P threads --concurrency=4 -n i1_worker1@%h -Q document_parsing,module_feature_generation
    #   celery -A app.core.celery_app.celery_app worker --loglevel=info -P threads --concurrency=4 -n i1_worker2@%h -Q user_story_generation
    #
    # Instance 2 (1 cpu / 2 ram) — document parsing + incremental update + interactive/fast tasks
    #   celery -A app.core.celery_app.celery_app worker --loglevel=info -P threads --concurrency=4 -n i2_worker1@%h -Q document_parsing,incremental_update
    #   celery -A app.core.celery_app.celery_app worker --loglevel=info -P threads --concurrency=10 -n i2_worker2@%h -Q routing,parsing,neo4j_sync,notifications,maintenance
    #   celery -A app.core.celery_app.celery_app beat --loglevel=info   # fires beat_schedule below (tasks.maintenance.detect_stale_ingestions) — not yet deployed, see docker-compose.yml's `beat` service for the local-dev equivalent
    #
    # Instance 3 (1 cpu / 2 ram) — regeneration paths, isolated so a regen backlog can't
    # starve first-time generation on instances 1/2
    #   celery -A app.core.celery_app.celery_app worker --loglevel=info -P threads --concurrency=3 -n i3_worker1@%h -Q module_feature_regeneration
    #   celery -A app.core.celery_app.celery_app worker --loglevel=info -P threads --concurrency=3 -n i3_worker2@%h -Q user_story_feedback_regeneration
    #
    # Instance 4 (2 cpu / 8 ram) — source-code pipeline (own deploy cadence; see docs/PERFORMANCE_ARCHITECTURE_REVIEW.md S2/S5)
    #
    # -P prefork (NOT threads), unlike every other instance above: each task
    # runs in its own OS process rather than sharing one long-lived Python
    # process/interpreter across every module of every project.
    #
    # --concurrency=5 on worker1 / worker2 / worker3: up to that many module,
    # feature-regen, or orchestrator tasks run at a time PER NODE, across all
    # projects (not just within one — modules of a single project are already
    # forced sequential via one big chain() in _run_pipeline_orchestrator, see
    # source_code_task.py). Each concurrent task gets its own prefork OS
    # process, so this does not reopen the single-process
    # ConcurrentPipelineError guard in llm_client.py (that guard only rejects
    # two runs landing in the SAME process). worker1 went 1 -> 3 -> 6 -> 5 for
    # cross-project throughput on this 8 GB box (its tasks are mostly
    # I/O-wait on LLM calls, so several processes on 2 cpus is tolerable).
    # Dial back any of the three if queue depth
    # (app/utils/queue_monitoring.py) shows the 2 cpus contending too hard.
    #
    # worker3 (source_code_parsing) is a DEDICATED pool, separate from
    # worker1's (source_code_processing/source_code_persistence) — this is
    # not optional. tasks.parse_code (the per-project orchestrator dispatched
    # to source_code_parsing) dispatches that project's module chain to
    # worker1's queues and then blocks inside its own task body
    # (async_result.get() in _run_pipeline_orchestrator) until the whole
    # chain finishes — up to TASK_SOURCE_CODE_PIPELINE_TIME_LIMIT (25h). If
    # source_code_parsing shared worker1's pool (as it originally did), N
    # concurrent projects' orchestrator tasks would eventually fill every
    # child in that shared pool with nothing left to run the module tasks
    # those very orchestrators are blocking on — a self-deadlock, and the
    # actual mechanism that made "N projects fully in parallel" unsafe to
    # rely on despite worker1's concurrency=5. Splitting the queue onto its
    # own pool (worker3) removes that contention: an orchestrator task
    # blocking on worker3 never competes with the module tasks it depends on
    # for a slot on worker1. No --max-tasks-per-child on worker3 — this task
    # never calls LLMClient directly, so there is no circuit-breaker state to
    # worry about sharing across recycled children.
    #
    # --max-tasks-per-child=25 (worker1/worker2 only): each child process
    # handles up to 25 tasks — which may span DIFFERENT projects — before
    # recycling. Be aware this reopens cross-project sharing of
    # app.services.source_code_pipeline...llm_client._BREAKER, a process-wide
    # singleton whose trip state and wall-clock timer NEVER reset on their own
    # (see its class docstring — it's an intentional one-way "stop the run"
    # guard): one project's LLM timeout storm can again poison every other
    # project's calls that land in the same child until it recycles. This was
    # previously prevented by pinning max-tasks-per-child=1 (a fresh,
    # never-tripped breaker per task) plus a _guard_single_pipeline_process
    # check that failed loudly on a cross-project collision — both were
    # deliberately removed to allow this higher throughput. If timeout-storm
    # aborts return, lowering this value back down (or re-adding that guard)
    # is the first thing to try.
    #
    # --max-memory-per-child=614400 (600 MB, checked after each task
    # completes — never kills a task mid-run): without this, an unbounded
    # child can grow until the OS OOM killer steps in first, which produces
    # an untraceable "WorkerLostError: exitcode 0" (cgroups intercepts the
    # SIGKILL before billiard can read the real signal). Proactive recycling
    # trades a bit more process-restart overhead for a traceable,
    # non-destructive one instead. Tune from observed RSS (docker stats /
    # box monitoring), not from this number alone. Not applied to worker3 —
    # its tasks hold no large in-memory pipeline state of their own, they
    # just poll the result backend while blocked.
    #   celery -A app.core.celery_app.celery_app worker --loglevel=info -P prefork --concurrency=5 --max-tasks-per-child=25 --max-memory-per-child=614400 -n i4_worker1@%h -Q source_code_processing,source_code_persistence
    #   celery -A app.core.celery_app.celery_app worker --loglevel=info -P prefork --concurrency=5 --max-tasks-per-child=25 --max-memory-per-child=614400 -n i4_worker2@%h -Q source_code_feature_regeneration,source_code_cleanup
    #   celery -A app.core.celery_app.celery_app worker --loglevel=info -P prefork --concurrency=5 -n i4_worker3@%h -Q source_code_parsing
    task_routes={
        "tasks.process_source": {"queue": QUEUE_ROUTING},
        "tasks.parse_document": {"queue": QUEUE_DOCUMENT_PARSING},
        "tasks.parse_image": {"queue": QUEUE_PARSING},
        "tasks.parse_code": {"queue": QUEUE_SOURCE_CODE_PARSING},
        "tasks.parse_code.process_single_module": {"queue": QUEUE_SOURCE_CODE_PROCESSING},
        "tasks.parse_code.persist_single_module": {"queue": QUEUE_SOURCE_CODE_PERSISTENCE},
        # Deferred, best-effort LLM generation dispatched from the final
        # module's persist task — same queue as the other LLM-heavy
        # per-source-code-pipeline work so it shares that pool's AI-generation
        # time budget instead of the tighter persistence queue's.
        "tasks.parse_code.generate_pipeline_documents": {"queue": QUEUE_SOURCE_CODE_PROCESSING},
        "tasks.parse_code.cleanup_project_folder": {"queue": QUEUE_SOURCE_CODE_CLEANUP},
        # Deferred post-cancellation backlog delete — same cleanup queue as the
        # temp-folder cleanup it is dispatched alongside.
        "tasks.parse_code.delete_project_backlog": {"queue": QUEUE_SOURCE_CODE_CLEANUP},
        "tasks.parse_code.regenerate_feature_mfu": {
            "queue": QUEUE_SOURCE_CODE_FEATURE_REGENERATION
        },
        "tasks.modules_and_features.generate_modules_and_features": {
            "queue": QUEUE_MODULE_FEATURE_GENERATION
        },
        "tasks.modules_and_features.regenerate_modules_and_features": {
            "queue": QUEUE_MODULE_FEATURE_REGENERATION
        },
        "tasks.user_stories.generate_user_story": {"queue": QUEUE_USER_STORY_GENERATION},
        "tasks.user_stories.regenerate_user_story": {"queue": QUEUE_USER_STORY_GENERATION},
        "tasks.user_stories.regenerate_by_feedback": {
            "queue": QUEUE_USER_STORY_FEEDBACK_REGENERATION
        },
        "tasks.project.sync_project_to_neo4j": {"queue": QUEUE_NEO4J_SYNC},
        "tasks.project.delete_project_graph": {"queue": QUEUE_NEO4J_SYNC},
        "tasks.incremental_update": {"queue": QUEUE_INCREMENTAL_UPDATE},
        "tasks.send_notification_email": {"queue": QUEUE_NOTIFICATIONS},
        "tasks.maintenance.detect_stale_ingestions": {"queue": QUEUE_MAINTENANCE},
    },
    # Requires a `celery -A app.core.celery_app.celery_app beat` process running
    # somewhere (see docker-compose.yml's `beat` service for local dev) — beat
    # is what actually fires tasks on this schedule; without it, this config
    # alone does nothing.
    beat_schedule={
        "detect-stale-source-ingestions": {
            "task": "tasks.maintenance.detect_stale_ingestions",
            "schedule": STALE_INGESTION_SWEEP_INTERVAL_SECONDS,
        },
    },
)

if settings.CELERY_RESULT_SOCKET_TIMEOUT is not None:
    celery_app.conf.redis_socket_timeout = settings.CELERY_RESULT_SOCKET_TIMEOUT
