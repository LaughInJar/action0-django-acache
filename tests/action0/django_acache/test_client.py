import unittest
from unittest import mock

import redis.asyncio

from action0.django_acache import registry

from .support import close_sync_pools
from .support import make_cache


class AGetClientTestCase(unittest.IsolatedAsyncioTestCase):
    """
    tests for :py:meth:`~action0.django_acache.client.RedisCacheClient.aget_client`
    """

    async def test_one_client(self) -> None:
        """
        Test that repeated calls return the same shared client.
        """
        client = make_cache()._cache
        self.assertIs(await client.aget_client(), await client.aget_client(write=True))
        self.assertEqual(len(registry.pools()), 1)

    async def test_instances_share(self) -> None:
        """
        Test that separate cache instances with equal settings share the client — Django
        creates one instance per request under ASGI.
        """
        first = make_cache()
        # same fakeredis server, but new instances of everything else, like the next request
        second = make_cache(OPTIONS=first._options, ASYNC_OPTIONS=first._async_options)
        self.assertIs(await first._cache.aget_client(), await second._cache.aget_client())

    async def test_async_client(self) -> None:
        """
        Test that the client is a redis.asyncio one, with the pool class of ASYNC_OPTIONS.
        """
        cache = make_cache(ASYNC_OPTIONS={"pool_class": "redis.asyncio.BlockingConnectionPool"})
        client = await cache._cache.aget_client()
        self.assertIsInstance(client, redis.asyncio.Redis)
        self.assertIsInstance(client.connection_pool, redis.asyncio.BlockingConnectionPool)

    async def test_server_selection(self) -> None:
        """
        Test that writes go to the first server and reads to one of the others, like on the
        sync side.
        """
        cache = make_cache()
        cache._servers = ["redis://first:6379/0", "redis://second:6379/0", "redis://third:6379/0"]
        client = cache._cache

        async def host(write: bool) -> str:
            pool = (await client.aget_client(write=write)).connection_pool
            host: str = pool.connection_kwargs["host"]
            return host

        self.assertEqual(await host(write=True), "first")
        with mock.patch("random.randint", return_value=2):
            self.assertEqual(await host(write=False), "third")

    async def test_single_server_reads(self) -> None:
        """
        Test that with a single server reads use it, too.
        """
        client = make_cache()._cache
        self.assertIs(await client.aget_client(), await client.aget_client(write=True))


class ConfigurationTestCase(unittest.TestCase):
    """
    tests for how :py:class:`~action0.django_acache.client.RedisCacheClient` splits the options
    """

    def test_sync_side_unchanged(self) -> None:
        """
        Test that the sync pools keep Django's pool class and the OPTIONS as they are.
        """
        cache = make_cache(
            OPTIONS={"pool_class": "redis.BlockingConnectionPool", "socket_timeout": 2},
            ASYNC_OPTIONS={"socket_timeout": 3},
        )
        self.addCleanup(close_sync_pools, cache)
        pool = cache._cache.get_client().connection_pool
        self.assertEqual(type(pool).__name__, "BlockingConnectionPool")
        self.assertEqual(pool.connection_kwargs["socket_timeout"], 2)
        self.assertEqual(cache._cache._async_pool_options["socket_timeout"], 3)
        self.assertIs(cache._cache._async_pool_class, redis.asyncio.ConnectionPool)
