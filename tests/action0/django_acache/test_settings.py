import asyncio
import unittest

import redis.asyncio
from asgiref.sync import sync_to_async
from django.core.cache import caches
from django.test import override_settings

from action0.django_acache import RedisCache
from action0.django_acache import registry

from .support import cache_settings
from .support import close_sync_pools


class SettingsTestCase(unittest.IsolatedAsyncioTestCase):
    """
    tests for :py:class:`~action0.django_acache.backend.RedisCache` configured in ``CACHES``
    and used through Django's cache handler
    """

    async def test_backend(self) -> None:
        """
        Test that the dotted path in BACKEND loads the backend, whose sync and async methods
        share the values.
        """
        with override_settings(CACHES={"default": cache_settings()}):
            cache = caches["default"]
            assert isinstance(cache, RedisCache)
            await cache.aclear()
            await cache.aset("key", "value")
            self.assertEqual(await sync_to_async(cache.get)("key"), "value")
            close_sync_pools(cache)

    async def test_async_options(self) -> None:
        """
        Test that ASYNC_OPTIONS reach the async pools.
        """
        settings = cache_settings(
            ASYNC_OPTIONS={"pool_class": "redis.asyncio.BlockingConnectionPool"}
        )
        with override_settings(CACHES={"default": settings}):
            await caches["default"].aget("key")
        (pool,) = registry.pools()
        self.assertIsInstance(pool, redis.asyncio.BlockingConnectionPool)

    async def test_requests_share_pools(self) -> None:
        """
        Test that concurrent requests — tasks, each with a cache instance of its own, as under
        ASGI — share one pool and its connections.
        """

        async def request(number: int) -> int:
            cache = caches["default"]
            await cache.aset(f"request-{number}", number)
            return id(cache)

        with override_settings(CACHES={"default": cache_settings()}):
            # the cache must not be touched here: tasks inherit the instances of this context
            instances = await asyncio.gather(*(asyncio.create_task(request(n)) for n in range(5)))
        self.assertEqual(len(set(instances)), 5)
        self.assertEqual(len(registry.pools()), 1)
