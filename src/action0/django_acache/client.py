"""
The cache client: Django's, plus async twins of its methods.
"""

from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from typing import Any
from typing import Protocol

import redis
from django.core.cache.backends import redis as django_redis
from redis.asyncio import Redis

from .options import async_pool_config
from .pools import registry


class Serializer(Protocol):
    """What Django's :py:class:`~django.core.cache.backends.redis.RedisSerializer` does."""

    def dumps(self, obj: Any) -> Any:
        """Turn a cache value into what is sent to the server."""

    def loads(self, data: Any) -> Any:
        """Turn what the server returned back into the cache value."""


class RedisCacheClient(django_redis.RedisCacheClient):
    """
    Django's Redis cache client, with an async method for each of its sync ones.

    The async methods are line-by-line translations of Django's sync ones onto
    :py:mod:`redis.asyncio`, with the same serializer and the same server selection (writes go
    to the first server, reads to a random other one), so the two sides read each other's
    values. Their pools come from the shared :py:data:`~action0.django_acache.pools.registry`.
    """

    # set by Django's __init__, but not part of django-stubs
    _servers: list[str]
    _serializer: Serializer
    _pool_options: dict[str, Any]
    _pools: dict[int, redis.ConnectionPool]
    _get_connection_pool_index: Callable[[bool], int]

    def __init__(
        self, servers: list[str], async_options: Mapping[str, Any] | None = None, **options: Any
    ) -> None:
        """
        :param servers: the server URLs, as Django splits them out of ``LOCATION``
        :param async_options: the cache's ``ASYNC_OPTIONS``, overrides for the async pools
        :param options: the cache's ``OPTIONS``, as Django's client takes them
        """
        super().__init__(servers, **options)
        self._async_pool_class, self._async_pool_options = async_pool_config(
            self._pool_options, async_options or {}
        )

    async def aget_client(self, key: str | None = None, *, write: bool = False) -> Redis:
        """
        The async counterpart of :py:meth:`get_client`: the running loop's client for a server.

        :param key: unused, like in :py:meth:`get_client`; there for clients that pick a server
            by key
        :param write: whether the client is used to write
        """
        index = self._get_connection_pool_index(write)
        return await registry.client(
            self._async_pool_class, self._servers[index], self._async_pool_options
        )

    async def aadd(self, key: str, value: Any, timeout: int | None) -> bool:
        """The async counterpart of :py:meth:`add`."""
        client = await self.aget_client(key, write=True)
        value = self._serializer.dumps(value)
        if timeout == 0:
            if ret := bool(await client.set(key, value, nx=True)):
                await client.delete(key)
            return ret
        return bool(await client.set(key, value, ex=timeout, nx=True))

    async def aget(self, key: str, default: Any) -> Any:
        """The async counterpart of :py:meth:`get`."""
        client = await self.aget_client(key)
        value = await client.get(key)
        return default if value is None else self._serializer.loads(value)

    async def aset(self, key: str, value: Any, timeout: int | None) -> None:
        """The async counterpart of :py:meth:`set`."""
        client = await self.aget_client(key, write=True)
        value = self._serializer.dumps(value)
        if timeout == 0:
            await client.delete(key)
        else:
            await client.set(key, value, ex=timeout)

    async def atouch(self, key: str, timeout: int | None) -> bool:
        """The async counterpart of :py:meth:`touch`."""
        client = await self.aget_client(key, write=True)
        if timeout is None:
            return bool(await client.persist(key))
        return bool(await client.expire(key, timeout))

    async def adelete(self, key: str) -> bool:
        """The async counterpart of :py:meth:`delete`."""
        client = await self.aget_client(key, write=True)
        return bool(await client.delete(key))

    async def aget_many(self, keys: Iterable[str]) -> dict[str, Any]:
        """The async counterpart of :py:meth:`get_many`."""
        keys = list(keys)
        client = await self.aget_client(None)
        ret = await client.mget(keys)
        return {k: self._serializer.loads(v) for k, v in zip(keys, ret) if v is not None}

    async def ahas_key(self, key: str) -> bool:
        """The async counterpart of :py:meth:`has_key`."""
        client = await self.aget_client(key)
        return bool(await client.exists(key))

    async def aincr(self, key: str, delta: int) -> int:
        """The async counterpart of :py:meth:`incr`."""
        client = await self.aget_client(key, write=True)
        if not await client.exists(key):
            raise ValueError("Key '%s' not found." % key)
        # ty picks redis-py's sync overload of incr() here; mypy and pyright get it right
        result: int = await client.incr(key, delta)  # ty: ignore[invalid-await]
        return result

    async def aset_many(self, data: Mapping[str, Any], timeout: int | None) -> None:
        """The async counterpart of :py:meth:`set_many`."""
        client = await self.aget_client(None, write=True)
        pipeline = client.pipeline()
        pipeline.mset({k: self._serializer.dumps(v) for k, v in data.items()})
        if timeout is not None:
            # Redis has no timeout for MSET, so every key gets its own EXPIRE
            for key in data:
                pipeline.expire(key, timeout)
        await pipeline.execute()

    async def adelete_many(self, keys: Iterable[str]) -> None:
        """The async counterpart of :py:meth:`delete_many`."""
        client = await self.aget_client(None, write=True)
        await client.delete(*keys)

    async def aclear(self) -> bool:
        """The async counterpart of :py:meth:`clear`."""
        client = await self.aget_client(None, write=True)
        return bool(await client.flushdb())
