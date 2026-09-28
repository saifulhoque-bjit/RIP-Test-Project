"""Lightweight TTL-based caching utilities for read-model endpoints.

Provides:
- In-memory cache with configurable TTL
- Simple string-based cache key generation
- Safe cache invalidation by prefix pattern
- No external dependencies; uses standard library only
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

# Global cache store: {key: (value, expiry_time)}
_cache_store: dict[str, tuple[Any, float]] = {}


def cache_get(cache_key: str) -> Any | None:
    """Retrieve a cached value if it exists and hasn't expired.

    Args:
        cache_key: The cache key string.

    Returns:
        The cached value if valid, or None if not found or expired.
    """
    if cache_key not in _cache_store:
        return None

    cached_value, expiry = _cache_store[cache_key]
    if time.time() < expiry:
        return cached_value

    # Expired; clean up and return None
    del _cache_store[cache_key]
    return None


def cache_set(cache_key: str, value: Any, ttl_seconds: int = 300) -> None:
    """Store a value in the cache with a TTL.

    Args:
        cache_key: The cache key string.
        value: The value to cache.
        ttl_seconds: Time-to-live in seconds (default 5 min).
    """
    _cache_store[cache_key] = (value, time.time() + ttl_seconds)


def invalidate_cache_by_prefix(prefix: str) -> None:
    """Invalidate all cache entries matching a prefix pattern.

    Args:
        prefix: Cache key prefix to match (e.g., "project_list").

    Usage:
        # After creating/updating a project, invalidate related caches
        invalidate_cache_by_prefix("project_list:")
        invalidate_cache_by_prefix("project:")
    """
    expired_keys = [key for key in _cache_store if key.startswith(prefix)]
    for key in expired_keys:
        del _cache_store[key]


def make_cache_key(prefix: str, *args: str | int | UUID) -> str:
    """Generate a cache key from prefix and parameters.

    Args:
        prefix: Cache key prefix (e.g., "project_list").
        *args: Variable positional arguments to include in the key.

    Returns:
        A colon-separated cache key string.

    Usage:
        key = make_cache_key("project", project_id, user_id)
        # => "project:550e8400-e29b-41d4-a716-446655440000:550e8400-e29b-41d4-a716-446655440001"
    """
    parts = [prefix] + [str(arg) for arg in args]
    return ":".join(parts)
