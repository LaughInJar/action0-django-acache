"""
The cache backend: Django's Redis cache, with native async methods.
"""

from collections.abc import Iterable
from typing import Any

from django.core.cache.backends import redis as django_redis
from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.utils.functional import cached_property

from .client import RedisCacheClient


class RedisCache(django_redis.RedisCache):
    """
    Django's :py:class:`~django.core.cache.backends.redis.RedisCache`, with async methods that
    talk to the server through :py:mod:`redis.asyncio` instead of running the sync ones in a
    worker thread.

    Configured like Django's backend, plus an optional ``ASYNC_OPTIONS`` setting that
    overrides ``OPTIONS`` keys for the async pools only:

    .. code-block:: python

        CACHES = {
            "default": {
                "BACKEND": "action0.django_acache.RedisCache",
                "LOCATION": "redis://127.0.0.1:6379",
            },
        }

    The sync methods are Django's own. The async ones mirror them one by one, including
    :py:meth:`aincr` staying a single ``INCR`` on the server; the remaining ones (``adecr``,
    ``aget_or_set``, ``aincr_version``, ...) are Django's generic implementations, built on
    these.
    """

    # set by Django's __init__, but not part of django-stubs
    _servers: list[str]
    _options: dict[str, Any]

    def __init__(self, server: str | list[str], params: dict[str, Any]) -> None:
        """
        :param server: ``LOCATION``: one server URL, several separated by ``,`` or ``;``, or a
            list of them
        :param params: the rest of the cache's settings
        """
        super().__init__(server, params)
        self._class: type[RedisCacheClient] = RedisCacheClient
        self._async_options: dict[str, Any] = params.get("ASYNC_OPTIONS", {})

    @cached_property
    def _cache(self) -> RedisCacheClient:
        return self._class(self._servers, async_options=self._async_options, **self._options)

    def get_backend_timeout(self, timeout: float | None = DEFAULT_TIMEOUT) -> int | None:
        """
        Django's timeout conversion, typed as what it returns for Redis: whole seconds.

        :param timeout: seconds, ``None`` for no expiry, or the default timeout
        """
        backend_timeout = super().get_backend_timeout(timeout)
        return None if backend_timeout is None else int(backend_timeout)

    async def aadd(
        self,
        key: Any,
        value: Any,
        timeout: float | None = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> bool:
        """The async counterpart of :py:meth:`add`."""
        key = self.make_and_validate_key(key, version=version)
        return await self._cache.aadd(key, value, self.get_backend_timeout(timeout))

    async def aget(self, key: Any, default: Any | None = None, version: int | None = None) -> Any:
        """The async counterpart of :py:meth:`get`."""
        key = self.make_and_validate_key(key, version=version)
        return await self._cache.aget(key, default)

    async def aset(
        self,
        key: Any,
        value: Any,
        timeout: float | None = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> None:
        """The async counterpart of :py:meth:`set`."""
        key = self.make_and_validate_key(key, version=version)
        await self._cache.aset(key, value, self.get_backend_timeout(timeout))

    async def atouch(
        self, key: Any, timeout: float | None = DEFAULT_TIMEOUT, version: int | None = None
    ) -> bool:
        """The async counterpart of :py:meth:`touch`."""
        key = self.make_and_validate_key(key, version=version)
        return await self._cache.atouch(key, self.get_backend_timeout(timeout))

    async def adelete(self, key: Any, version: int | None = None) -> bool:
        """The async counterpart of :py:meth:`delete`."""
        key = self.make_and_validate_key(key, version=version)
        return await self._cache.adelete(key)

    async def aget_many(self, keys: Iterable[Any], version: int | None = None) -> dict[Any, Any]:
        """The async counterpart of :py:meth:`get_many`."""
        key_map = {self.make_and_validate_key(key, version=version): key for key in keys}
        ret = await self._cache.aget_many(key_map.keys())
        return {key_map[k]: v for k, v in ret.items()}

    async def ahas_key(self, key: Any, version: int | None = None) -> bool:
        """The async counterpart of :py:meth:`has_key`."""
        key = self.make_and_validate_key(key, version=version)
        return await self._cache.ahas_key(key)

    async def aincr(self, key: Any, delta: int = 1, version: int | None = None) -> int:
        """The async counterpart of :py:meth:`incr`."""
        key = self.make_and_validate_key(key, version=version)
        return await self._cache.aincr(key, delta)

    async def aset_many(
        self,
        data: dict[Any, Any],
        timeout: float | None = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> list[Any]:
        """The async counterpart of :py:meth:`set_many`."""
        if not data:
            return []
        safe_data = {}
        for key, value in data.items():
            key = self.make_and_validate_key(key, version=version)
            safe_data[key] = value
        await self._cache.aset_many(safe_data, self.get_backend_timeout(timeout))
        return []

    async def adelete_many(self, keys: Iterable[Any], version: int | None = None) -> None:
        """The async counterpart of :py:meth:`delete_many`."""
        safe_keys = [self.make_and_validate_key(key, version=version) for key in keys]
        # checked after the conversion, so that an empty generator counts as empty, too
        if safe_keys:
            await self._cache.adelete_many(safe_keys)

    async def aclear(self) -> None:
        """The async counterpart of :py:meth:`clear`."""
        await self._cache.aclear()

    async def aclose(self, **kwargs: Any) -> None:
        """
        Do nothing, just like :py:meth:`close` — but without a detour through a worker thread.

        Django closes its caches after every request, and the async pools are shared by all
        requests on the event loop, so they are closed at loop shutdown instead (or
        explicitly, by :py:func:`~action0.django_acache.pools.aclose_pools`).
        """
