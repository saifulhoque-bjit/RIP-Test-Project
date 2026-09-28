"""Shared Redis connection factories for FastAPI (async) and Celery/scripts (sync).

Centralises the socket-level resilience settings — connect timeout,
keepalive, health-check ping — so every ad-hoc Redis client in the process
(WebSocket pub/sub managers, health checks, queue monitoring) degrades and
recovers the same way. Several call sites previously built clients with no
``socket_connect_timeout`` at all, so a downed Redis would block the caller
for the OS-level TCP timeout (minutes) instead of failing fast.
"""

from __future__ import annotations

import socket

import redis as sync_redis
import redis.asyncio as aioredis

# Fail fast on a dead/unreachable Redis instead of blocking on the OS-level
# TCP connect timeout (can be 2+ minutes on Linux).
REDIS_SOCKET_CONNECT_TIMEOUT = 5.0
# Detect silently-dropped idle connections (managed Redis / NAT / firewall
# cutoffs) before the next command hits a stale socket.
REDIS_HEALTH_CHECK_INTERVAL = 30
# socket_keepalive=True alone just flips on SO_KEEPALIVE and inherits the OS
# default idle time (7200s on Linux) before the first probe — far longer than
# AWS's ~350s idle-connection eviction inside a VPC, so a quiet pub/sub or
# health-check connection gets silently dropped by the network fabric long
# before the kernel would ever notice. Mirrors the same timing already tuned
# for the Celery broker in app/core/celery_app.py: first probe after IDLE
# seconds, a probe every INTVL seconds, give up after CNT missed probes.
REDIS_SOCKET_KEEPALIVE_IDLE = 30
REDIS_SOCKET_KEEPALIVE_INTVL = 10
REDIS_SOCKET_KEEPALIVE_CNT = 3

_SOCKET_KEEPALIVE_OPTIONS = (
    {
        socket.TCP_KEEPIDLE: REDIS_SOCKET_KEEPALIVE_IDLE,
        socket.TCP_KEEPINTVL: REDIS_SOCKET_KEEPALIVE_INTVL,
        socket.TCP_KEEPCNT: REDIS_SOCKET_KEEPALIVE_CNT,
    }
    if hasattr(socket, "TCP_KEEPIDLE")  # absent on macOS dev machines
    else {}
)


def create_async_redis(
    url: str, *, socket_connect_timeout: float = REDIS_SOCKET_CONNECT_TIMEOUT
) -> aioredis.Redis:
    """Build an async Redis client with consistent resilience settings."""
    return aioredis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=socket_connect_timeout,
        socket_keepalive=True,
        socket_keepalive_options=_SOCKET_KEEPALIVE_OPTIONS,
        health_check_interval=REDIS_HEALTH_CHECK_INTERVAL,
        retry_on_timeout=True,
    )


def create_sync_redis(
    url: str, *, socket_connect_timeout: float = REDIS_SOCKET_CONNECT_TIMEOUT
) -> sync_redis.Redis:
    """Build a sync Redis client with consistent resilience settings.

    Used from Celery workers and one-off scripts where no event loop is
    available.
    """
    return sync_redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=socket_connect_timeout,
        socket_keepalive=True,
        socket_keepalive_options=_SOCKET_KEEPALIVE_OPTIONS,
        health_check_interval=REDIS_HEALTH_CHECK_INTERVAL,
        retry_on_timeout=True,
    )
