"""
Async connection pools, shared per event loop.

A :py:mod:`redis.asyncio` connection belongs to the event loop it was opened on, so every loop
needs pools of its own. And under ASGI, Django usually creates a new cache backend instance for
every request: the cache handler keeps its instances in a context-local, each request runs in
a task of its own, and a task only sees the instances its parent context had already created.
Pools kept on the backend instance would mean a new pool, and a new connection to the server,
for every request.

The pools therefore live in one process-wide :py:class:`PoolRegistry`, per running event loop
and per configuration: all cache instances with equal settings share them, whichever request
created them. Every loop's pools are closed when the loop shuts down. :py:func:`asyncio.run`,
:py:class:`asyncio.Runner`, ASGI servers and asgiref's ``async_to_sync`` all call
:py:meth:`~asyncio.loop.shutdown_asyncgens` before they close a loop, and the registry leaves
an async generator on every loop it serves whose ``finally`` clause closes that loop's pools.
"""

import asyncio
import threading
import weakref
from collections.abc import AsyncGenerator
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from redis.asyncio import ConnectionPool
from redis.asyncio import Redis

from .options import AsyncPoolClass


@dataclass(eq=False)
class PoolEntry:
    """
    A pool, the configuration it was created from, and the client that shares it.

    One client per pool, rather than a new one for every call as Django's sync side does:
    creating a :py:class:`redis.asyncio.Redis` costs about as much as a round trip to a local
    server, and the client holds no state besides its pool.
    """

    pool_class: AsyncPoolClass
    url: str
    options: Mapping[str, Any]
    client: Redis

    def matches(self, pool_class: AsyncPoolClass, url: str, options: Mapping[str, Any]) -> bool:
        """
        Whether this pool was created from exactly this configuration.

        :param pool_class: the pool class
        :param url: the server URL
        :param options: the keyword arguments for the pool's ``from_url()``
        """
        # identity is the fast path for the repeated calls of one cache client; other clients
        # (other requests) compare equal instead. Equality rather than a hash: some option
        # values are unhashable, e.g. the redis.DriverInfo Django passes along
        return (
            self.pool_class is pool_class
            and self.url == url
            and (self.options is options or self.options == options)
        )


class LoopPools:
    """
    The pools of one event loop.

    Only the loop's own thread ever touches an instance, so it needs no lock.
    """

    def __init__(self) -> None:
        self.entries: list[PoolEntry] = []
        #: the async generator that closes the pools at loop shutdown; it is kept here because
        #: the loop itself only holds a weak reference to it
        self.watcher: AsyncGenerator[None, None] | None = None

    def client(self, pool_class: AsyncPoolClass, url: str, options: Mapping[str, Any]) -> Redis:
        """
        The client of the pool for this configuration, creating both on first use.

        :param pool_class: the pool class
        :param url: the server URL
        :param options: the keyword arguments for the pool's ``from_url()``; they are kept, not
            copied, and must not be changed afterwards
        """
        for entry in self.entries:
            if entry.matches(pool_class, url, options):
                return entry.client
        client = Redis(connection_pool=pool_class.from_url(url, **options))
        self.entries.append(PoolEntry(pool_class, url, options, client))
        return client

    async def aclose(self) -> None:
        """Close all pools and forget them."""
        entries, self.entries = self.entries, []
        # return_exceptions: a server that is unreachable by now must not keep the remaining
        # pools from being closed, and there is nobody to report the error to at loop shutdown
        await asyncio.gather(
            *(entry.client.connection_pool.aclose() for entry in entries),
            return_exceptions=True,
        )


class PoolRegistry:
    """
    The async clients and their pools, per event loop and configuration.

    Thread-safe: every thread may run a loop of its own. The lock only guards the mapping of
    loops, and is never held across an ``await``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loops: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, LoopPools] = (
            weakref.WeakKeyDictionary()
        )

    async def client(
        self, pool_class: AsyncPoolClass, url: str, options: Mapping[str, Any]
    ) -> Redis:
        """
        The running loop's client for this configuration.

        The pool is created on first use, and closed when the loop shuts down; its connections
        are opened on demand.

        :param pool_class: the pool class
        :param url: the server URL
        :param options: the keyword arguments for the pool's ``from_url()``; they are kept, not
            copied, and must not be changed afterwards
        """
        loop = asyncio.get_running_loop()
        with self._lock:
            loop_pools = self._loops.get(loop)
            new_loop = loop_pools is None
            if loop_pools is None:
                self._forget_closed_loops()
                loop_pools = self._loops[loop] = LoopPools()
        if new_loop:
            await self._watch(loop_pools)
        return loop_pools.client(pool_class, url, options)

    def pools(self, loop: asyncio.AbstractEventLoop | None = None) -> tuple[ConnectionPool, ...]:
        """
        The open pools of a loop, in the order they were created.

        :param loop: the loop; the running one by default
        """
        with self._lock:
            loop_pools = self._loops.get(loop or asyncio.get_running_loop())
        if loop_pools is None:
            return ()
        return tuple(entry.client.connection_pool for entry in loop_pools.entries)

    async def aclose(self) -> None:
        """Close the running loop's pools now rather than at loop shutdown."""
        with self._lock:
            loop_pools = self._loops.get(asyncio.get_running_loop())
        if loop_pools is not None and loop_pools.watcher is not None:
            # closing the watcher runs its finally clause: the same path as at loop shutdown
            await loop_pools.watcher.aclose()

    async def _watch(self, loop_pools: LoopPools) -> None:
        """Leave an async generator on the running loop that closes its pools at shutdown."""
        watcher = self._close_at_shutdown(loop_pools)
        loop_pools.watcher = watcher
        # running it up to its yield registers it with the loop (asyncio's firstiter hook);
        # only a started generator runs its finally clause when it is closed
        await anext(watcher)

    async def _close_at_shutdown(self, loop_pools: LoopPools) -> AsyncGenerator[None, None]:
        """Wait for the loop's shutdown (or :py:meth:`aclose`), then close its pools."""
        try:
            yield
        finally:
            self._forget(asyncio.get_running_loop(), loop_pools)
            await loop_pools.aclose()

    def _forget(self, loop: asyncio.AbstractEventLoop, loop_pools: LoopPools) -> None:
        """Remove a loop's pools, unless they have been replaced in the meantime."""
        with self._lock:
            if self._loops.get(loop) is loop_pools:
                del self._loops[loop]

    def _forget_closed_loops(self) -> None:
        """
        Drop the pools of loops that were closed without shutting down their async generators.

        Their connections cannot be closed properly any more, but they must not be kept alive
        forever either: they reference their loop, so the weak mapping never drops them itself.
        Called with the lock held.
        """
        for loop in [loop for loop in self._loops if loop.is_closed()]:
            del self._loops[loop]


#: the registry that all cache backends of the process share
registry = PoolRegistry()


async def aclose_pools() -> None:
    """
    Close the running event loop's async connection pools now.

    They are closed at loop shutdown anyway; this is for when that is too late, e.g. in a test
    that checks for leaked connections before its loop ends. The pools are shared by all cache
    backends, so don't call it while other tasks on the loop still use the cache. Using a cache
    afterwards simply creates new pools.
    """
    await registry.aclose()
