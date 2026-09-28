"""Unit tests for app.utils.cache."""

from __future__ import annotations

import time
import uuid

from app.utils import cache as cache_module
from app.utils.cache import cache_get, cache_set, invalidate_cache_by_prefix, make_cache_key


def setup_function() -> None:
    cache_module._cache_store.clear()


class TestCacheGetSet:
    def test_returns_none_for_missing_key(self):
        assert cache_get("missing") is None

    def test_returns_stored_value(self):
        cache_set("k1", {"a": 1}, ttl_seconds=60)

        assert cache_get("k1") == {"a": 1}

    def test_expired_entry_returns_none_and_is_cleaned_up(self):
        cache_set("k1", "value", ttl_seconds=-1)  # already expired

        assert cache_get("k1") is None
        assert "k1" not in cache_module._cache_store

    def test_default_ttl_is_used(self):
        before = time.time()
        cache_set("k1", "value")

        _, expiry = cache_module._cache_store["k1"]
        assert expiry > before + 250  # within the 300s default window


class TestInvalidateCacheByPrefix:
    def test_removes_only_matching_keys(self):
        cache_set("project_list:1", "a")
        cache_set("project_list:2", "b")
        cache_set("other:1", "c")

        invalidate_cache_by_prefix("project_list:")

        assert cache_get("project_list:1") is None
        assert cache_get("project_list:2") is None
        assert cache_get("other:1") == "c"

    def test_no_matching_keys_is_a_noop(self):
        cache_set("other:1", "c")

        invalidate_cache_by_prefix("no-match:")

        assert cache_get("other:1") == "c"


class TestMakeCacheKey:
    def test_joins_prefix_and_args(self):
        project_id = uuid.uuid4()
        user_id = uuid.uuid4()

        key = make_cache_key("project", project_id, user_id)

        assert key == f"project:{project_id}:{user_id}"

    def test_prefix_only(self):
        assert make_cache_key("project") == "project"

    def test_mixed_arg_types(self):
        assert make_cache_key("page", 1, "search") == "page:1:search"
