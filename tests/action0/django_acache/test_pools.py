import asyncio
import threading
import unittest
from typing import Any

import redis.asyncio
from asgiref.sync import async_to_sync
from redis.asyncio.connection import AbstractConnection

from action0.django_acache.pools import LoopPools
from action0.django_acache.pools import PoolRegistry
from action0.django_acache.pools import aclose_pools
from action0.django_acache.pools import registry as shared_registry

from .support import async_server

POOL = redis.asyncio.ConnectionPool


async def _connect(client: redis.asyncio.Redis) -> AbstractConnection:
    """Open a connection of the client's pool and return it."""
    await client.ping()
    # the connection the ping used, back in the pool; get_connection() changed its signature
    # between the supported redis-py versions
    connection: AbstractConnection = client.connection_pool._available_connections[-1]
    return connection


class ClientTestCase(unittest.IsolatedAsyncioTestCase):
    """
    tests for :py:meth:`~action0.django_acache.pools.PoolRegistry.client`
    """

    def setUp(self) -> None:
        self.registry = PoolRegistry()
        self.url, self.options = async_server()

    async def test_created_once(self) -> None:
        """
        Test that a configuration gets one client and pool, however often it is asked for.
        """
        client = await self.registry.client(POOL, self.url, self.options)
        self.assertIs(await self.registry.client(POOL, self.url, self.options), client)
        self.assertEqual(self.registry.pools(), (client.connection_pool,))

    async def test_equal_options_share(self) -> None:
        """
        Test that equal options share the client even when they are not the same dict.
        """
        client = await self.registry.client(POOL, self.url, self.options)
        self.assertIs(await self.registry.client(POOL, self.url, dict(self.options)), client)

    async def test_different_configurations(self) -> None:
        """
        Test that each differing part of the configuration gets a pool of its own.
        """
        client = await self.registry.client(POOL, self.url, self.options)
        others = [
            await self.registry.client(POOL, self.url + "1", self.options),
            await self.registry.client(POOL, self.url, {**self.options, "db": 1}),
            await self.registry.client(
                redis.asyncio.BlockingConnectionPool, self.url, self.options
            ),
        ]
        self.assertEqual(len({id(c) for c in [client, *others]}), 4)
        self.assertEqual(len(self.registry.pools()), 4)

    async def test_pool_class_and_options(self) -> None:
        """
        Test that the pool is created by the given class, from the URL and the options.
        """
        client = await self.registry.client(
            redis.asyncio.BlockingConnectionPool, self.url, {**self.options, "max_connections": 3}
        )
        pool = client.connection_pool
        self.assertIsInstance(pool, redis.asyncio.BlockingConnectionPool)
        self.assertEqual(pool.max_connections, 3)

    async def test_works(self) -> None:
        """
        Test that the client talks to the server.
        """
        client = await self.registry.client(POOL, self.url, self.options)
        self.assertTrue(await client.ping())


class LifecycleTestCase(unittest.TestCase):
    """
    tests for when :py:class:`~action0.django_acache.pools.PoolRegistry` closes pools
    """

    def setUp(self) -> None:
        self.registry = PoolRegistry()
        self.url, self.options = async_server()

    async def _use(self) -> tuple[asyncio.AbstractEventLoop, AbstractConnection]:
        """Open a connection on the running loop; return the loop and the connection."""
        client = await self.registry.client(POOL, self.url, self.options)
        return asyncio.get_running_loop(), await _connect(client)

    def test_closed_at_loop_shutdown(self) -> None:
        """
        Test that asyncio.run() closes the loop's pools and the registry forgets them.
        """
        loop, connection = asyncio.run(self._use())
        self.assertFalse(connection.is_connected)
        self.assertEqual(self.registry.pools(loop), ())

    def test_each_loop_its_own(self) -> None:
        """
        Test that every loop gets a pool of its own.
        """
        _, first = asyncio.run(self._use())
        _, second = asyncio.run(self._use())
        self.assertIsNot(first, second)

    def test_async_to_sync(self) -> None:
        """
        Test that asgiref's async_to_sync closes the pools of the loop it ran.
        """
        loop, connection = async_to_sync(self._use)()
        self.assertFalse(connection.is_connected)
        self.assertEqual(self.registry.pools(loop), ())

    def test_threads(self) -> None:
        """
        Test that loops in several threads get separate pools, each closed with its loop.
        """
        results: list[tuple[asyncio.AbstractEventLoop, AbstractConnection]] = []

        def run() -> None:
            results.append(asyncio.run(self._use()))

        threads = [threading.Thread(target=run) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len({id(connection) for _, connection in results}), 4)
        for loop, connection in results:
            self.assertFalse(connection.is_connected)
            self.assertEqual(self.registry.pools(loop), ())

    def test_closed_loops_forgotten(self) -> None:
        """
        Test that the pools of a loop closed without shutting down its async generators are
        dropped once another loop comes along.
        """
        closed = asyncio.new_event_loop()
        closed.close()
        # a loop that never ran its shutdown: pools registered, watcher never started
        self.registry._loops[closed] = LoopPools()

        async def use() -> None:
            await self.registry.client(POOL, self.url, self.options)

        asyncio.run(use())
        self.assertNotIn(closed, self.registry._loops)


class ACloseTestCase(unittest.IsolatedAsyncioTestCase):
    """
    tests for :py:meth:`~action0.django_acache.pools.PoolRegistry.aclose` and
    :py:func:`~action0.django_acache.pools.aclose_pools`
    """

    def setUp(self) -> None:
        self.registry = PoolRegistry()
        self.url, self.options = async_server()

    async def test_closes_now(self) -> None:
        """
        Test that the running loop's pools are closed and forgotten right away.
        """
        connection = await _connect(await self.registry.client(POOL, self.url, self.options))
        await self.registry.aclose()
        self.assertFalse(connection.is_connected)
        self.assertEqual(self.registry.pools(), ())

    async def test_usable_afterwards(self) -> None:
        """
        Test that asking again afterwards creates a new pool, closed at loop shutdown as usual.
        """
        client = await self.registry.client(POOL, self.url, self.options)
        await self.registry.aclose()
        again = await self.registry.client(POOL, self.url, self.options)
        self.assertIsNot(again, client)
        self.assertTrue(await again.ping())

    async def test_nothing_to_close(self) -> None:
        """
        Test that closing a loop without pools does nothing.
        """
        await self.registry.aclose()
        self.assertEqual(self.registry.pools(), ())

    async def test_aclose_pools(self) -> None:
        """
        Test that aclose_pools() closes the pools of the shared registry.
        """
        client = await shared_registry.client(POOL, self.url, self.options)
        connection = await _connect(client)
        await aclose_pools()
        self.assertFalse(connection.is_connected)
        self.assertEqual(shared_registry.pools(), ())


class PoolsTestCase(unittest.TestCase):
    """
    tests for :py:meth:`~action0.django_acache.pools.PoolRegistry.pools`
    """

    def test_unknown_loop(self) -> None:
        """
        Test that a loop the registry never served has no pools.
        """
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        self.assertEqual(PoolRegistry().pools(loop), ())

    def test_running_loop_by_default(self) -> None:
        """
        Test that without a loop the running one is meant.
        """
        registry = PoolRegistry()
        url, options = async_server()

        async def use() -> tuple[Any, ...]:
            client = await registry.client(POOL, url, options)
            return registry.pools(), (client.connection_pool,)

        pools, expected = asyncio.run(use())
        self.assertEqual(pools, expected)
