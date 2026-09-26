"""
What the tests share: Django settings, and caches on fakeredis or on a real server.

Without ``REDIS_URL`` every cache gets a fresh in-process fakeredis server, shared by its sync
and its async pools. With ``REDIS_URL`` set (CI does, for Redis and for Valkey) the caches use
that server instead, and the tests flush its database — never point it at data you want to
keep.
"""

import os
import unittest
from typing import Any

import django
import fakeredis
from django.conf import settings

from action0.django_acache import RedisCache

#: a real Redis or Valkey server to test against, e.g. ``redis://localhost:6379/0``
REDIS_URL = os.environ.get("REDIS_URL")

#: the dotted path of the backend, as it is used in ``CACHES``
BACKEND = "action0.django_acache.RedisCache"

if not settings.configured:
    settings.configure()
    django.setup()


def cache_settings(**params: Any) -> dict[str, Any]:
    """
    A ``CACHES`` entry for the backend, on a new fakeredis server or on ``REDIS_URL``.

    :param params: more settings; ``OPTIONS`` and ``ASYNC_OPTIONS`` are merged into the ones
        that select the server
    """
    server: dict[str, Any]
    if REDIS_URL:
        server = {"LOCATION": REDIS_URL, "OPTIONS": {}, "ASYNC_OPTIONS": {}}
    else:
        server = {
            "LOCATION": "redis://fakeredis:6379/0",
            "OPTIONS": {
                "connection_class": fakeredis.FakeRedisConnection,
                "server": fakeredis.FakeServer(),
            },
            "ASYNC_OPTIONS": {"connection_class": fakeredis.FakeAsyncRedisConnection},
        }
    for key in ("OPTIONS", "ASYNC_OPTIONS"):
        server[key].update(params.pop(key, {}))
    return {"BACKEND": BACKEND, **server, **params}


def async_server() -> tuple[str, dict[str, Any]]:
    """
    The URL and the async pool options for a new fakeredis server, or for ``REDIS_URL``.
    """
    if REDIS_URL:
        return REDIS_URL, {}
    return "redis://fakeredis:6379/0", {
        "connection_class": fakeredis.FakeAsyncRedisConnection,
        "server": fakeredis.FakeServer(),
    }


def make_cache(**params: Any) -> RedisCache:
    """
    A backend instance built from :py:func:`cache_settings`, the way Django's cache handler
    builds one.

    :param params: more settings, see :py:func:`cache_settings`
    """
    params = cache_settings(**params)
    del params["BACKEND"]
    return RedisCache(params.pop("LOCATION"), params)


def close_sync_pools(cache: RedisCache) -> None:
    """
    Disconnect the sync pools of a cache.

    Django never closes them; tests do, because the sockets of a real server would otherwise
    raise a ``ResourceWarning`` whenever they are garbage-collected.
    """
    for pool in cache._cache._pools.values():
        pool.disconnect()


class CacheTestCase(unittest.IsolatedAsyncioTestCase):
    """
    A test case with an empty cache in ``self.cache``, and its sync pools closed afterwards.
    """

    def setUp(self) -> None:
        self.cache = make_cache()
        self.addCleanup(close_sync_pools, self.cache)
        # a real server keeps the previous test's keys
        self.cache.clear()

    def ttl(self, key: str) -> int:
        """
        The server's TTL of a cache key, through the sync client: -2 missing, -1 persistent.

        :param key: the key as the cache user passes it, without prefix or version
        """
        ttl: int = self.cache._cache.get_client().ttl(self.cache.make_key(key))
        return ttl
