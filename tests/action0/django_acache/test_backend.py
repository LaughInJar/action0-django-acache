import asyncio
from typing import Any
from unittest import mock

from action0.django_acache import RedisCache
from action0.django_acache import RedisCacheClient

from .support import CacheTestCase
from .support import close_sync_pools
from .support import make_cache


class BackendTestCase(CacheTestCase):
    """
    tests for :py:class:`~action0.django_acache.backend.RedisCache` basics
    """

    def test_client(self) -> None:
        """
        Test that the backend builds this package's client, not Django's.
        """
        self.assertIsInstance(self.cache, RedisCache)
        self.assertIsInstance(self.cache._cache, RedisCacheClient)

    def test_backend_timeout(self) -> None:
        """
        Test that timeouts become whole seconds, None stays None and negatives become 0.
        """
        self.assertEqual(self.cache.get_backend_timeout(), 300)
        self.assertEqual(self.cache.get_backend_timeout(1.9), 1)
        self.assertIsNone(self.cache.get_backend_timeout(None))
        self.assertEqual(self.cache.get_backend_timeout(-5), 0)

    async def test_async_methods_never_use_the_sync_client(self) -> None:
        """
        Test that no async method, not even Django's generic ones built on the others, falls
        back to the sync client.
        """
        with mock.patch.object(
            self.cache._cache, "get_client", side_effect=AssertionError("sync client used")
        ):
            await self.cache.aset("key", 1)
            await self.cache.aadd("other", 1)
            await self.cache.aget("key")
            await self.cache.atouch("key", 10)
            await self.cache.ahas_key("key")
            await self.cache.aget_many(["key", "other"])
            await self.cache.aincr("key")
            await self.cache.adecr("key")
            await self.cache.aget_or_set("third", 3)
            await self.cache.aincr_version("key")
            await self.cache.adecr_version("key", version=2)
            await self.cache.aset_many({"a": 1, "b": 2})
            await self.cache.adelete_many(["a", "b"])
            await self.cache.adelete("key")
            await self.cache.aclear()

    async def test_aclose(self) -> None:
        """
        Test that aclose() neither runs close() in a thread nor closes the shared pools.
        """
        await self.cache.aset("key", 1)
        with mock.patch.object(self.cache, "close", side_effect=AssertionError("close used")):
            await self.cache.aclose()
        self.assertEqual(await self.cache.aget("key"), 1)


class GetSetTestCase(CacheTestCase):
    """
    tests for ``aget()``, ``aset()`` and ``aadd()`` of
    :py:class:`~action0.django_acache.backend.RedisCache`
    """

    async def test_round_trip(self) -> None:
        """
        Test that values of all kinds come back as they were stored.
        """
        values: list[Any] = [1, -7, True, 1.5, "text", b"bytes", None, [1, "a"], {"x": (1, 2)}]
        for value in values:
            with self.subTest(value=value):
                await self.cache.aset("key", value)
                self.assertEqual(await self.cache.aget("key", "missing"), value)
                self.assertIs(type(await self.cache.aget("key")), type(value))

    async def test_default(self) -> None:
        """
        Test that a missing key returns the default, None by default.
        """
        self.assertIsNone(await self.cache.aget("missing"))
        self.assertEqual(await self.cache.aget("missing", 42), 42)

    async def test_shared_with_sync_side(self) -> None:
        """
        Test that the sync and the async methods read each other's values.
        """
        await self.cache.aset("from_async", {"a": 1})
        self.cache.set("from_sync", [1, 2])
        self.cache.set("number", 5)
        self.assertEqual(self.cache.get("from_async"), {"a": 1})
        self.assertEqual(await self.cache.aget("from_sync"), [1, 2])
        self.assertEqual(await self.cache.aget("number"), 5)

    async def test_aset_timeout(self) -> None:
        """
        Test that aset() applies the timeout, and the default timeout without one.
        """
        await self.cache.aset("key", 1, timeout=60)
        self.assertTrue(0 < self.ttl("key") <= 60)
        await self.cache.aset("default", 1)
        self.assertTrue(60 < self.ttl("default") <= 300)

    async def test_aset_no_timeout(self) -> None:
        """
        Test that a timeout of None stores the key without expiry.
        """
        await self.cache.aset("key", 1, timeout=None)
        self.assertEqual(self.ttl("key"), -1)

    async def test_aset_zero_timeout(self) -> None:
        """
        Test that a timeout of zero or less deletes the key instead of storing it.
        """
        for timeout in (0, -1):
            with self.subTest(timeout=timeout):
                await self.cache.aset("key", 1)
                await self.cache.aset("key", 2, timeout=timeout)
                self.assertIsNone(await self.cache.aget("key"))

    async def test_aadd(self) -> None:
        """
        Test that aadd() stores a missing key but leaves an existing one alone.
        """
        self.assertTrue(await self.cache.aadd("key", 1, timeout=60))
        self.assertFalse(await self.cache.aadd("key", 2))
        self.assertEqual(await self.cache.aget("key"), 1)
        self.assertTrue(0 < self.ttl("key") <= 60)

    async def test_aadd_zero_timeout(self) -> None:
        """
        Test that aadd() with a zero timeout reports whether it could have added, but stores
        nothing.
        """
        self.assertTrue(await self.cache.aadd("key", 1, timeout=0))
        self.assertFalse(await self.cache.ahas_key("key"))
        await self.cache.aset("key", 1)
        self.assertFalse(await self.cache.aadd("key", 2, timeout=0))
        self.assertEqual(await self.cache.aget("key"), 1)

    async def test_versions_and_prefix(self) -> None:
        """
        Test that the key prefix and versions build the same keys as on the sync side.
        """
        cache = make_cache(KEY_PREFIX="site", VERSION=3)
        self.addCleanup(close_sync_pools, cache)
        await cache.aset("key", "v3")
        await cache.aset("key", "v1", version=1)
        self.assertEqual(cache.get("key"), "v3")
        self.assertEqual(cache.get("key", version=1), "v1")
        self.assertEqual(await cache.aget("key", version=1), "v1")
        self.assertTrue(cache._cache.get_client().exists("site:3:key"))


class KeysTestCase(CacheTestCase):
    """
    tests for ``atouch()``, ``adelete()`` and ``ahas_key()`` of
    :py:class:`~action0.django_acache.backend.RedisCache`
    """

    async def test_atouch(self) -> None:
        """
        Test that atouch() sets a new timeout on an existing key.
        """
        await self.cache.aset("key", 1, timeout=None)
        self.assertTrue(await self.cache.atouch("key", 60))
        self.assertTrue(0 < self.ttl("key") <= 60)

    async def test_atouch_persist(self) -> None:
        """
        Test that atouch() with None removes the timeout.
        """
        await self.cache.aset("key", 1, timeout=60)
        self.assertTrue(await self.cache.atouch("key", None))
        self.assertEqual(self.ttl("key"), -1)

    async def test_atouch_missing(self) -> None:
        """
        Test that atouch() reports a missing key.
        """
        self.assertFalse(await self.cache.atouch("missing", 60))
        self.assertFalse(await self.cache.atouch("missing", None))

    async def test_adelete(self) -> None:
        """
        Test that adelete() removes a key and reports whether there was one.
        """
        await self.cache.aset("key", 1)
        self.assertTrue(await self.cache.adelete("key"))
        self.assertFalse(await self.cache.adelete("key"))
        self.assertIsNone(await self.cache.aget("key"))

    async def test_ahas_key(self) -> None:
        """
        Test that ahas_key() tells present from missing keys, even ones holding None.
        """
        await self.cache.aset("key", None)
        self.assertTrue(await self.cache.ahas_key("key"))
        self.assertFalse(await self.cache.ahas_key("missing"))


class ManyTestCase(CacheTestCase):
    """
    tests for ``aget_many()``, ``aset_many()`` and ``adelete_many()`` of
    :py:class:`~action0.django_acache.backend.RedisCache`
    """

    async def test_aget_many(self) -> None:
        """
        Test that aget_many() maps the given keys to their values and leaves out missing ones.
        """
        await self.cache.aset("a", 1)
        await self.cache.aset("b", None)
        self.assertEqual(await self.cache.aget_many(["a", "b", "missing"]), {"a": 1, "b": None})

    async def test_aget_many_empty(self) -> None:
        """
        Test that aget_many() of no keys is empty.
        """
        self.assertEqual(await self.cache.aget_many([]), {})

    async def test_aget_many_version(self) -> None:
        """
        Test that aget_many() reads the given version.
        """
        await self.cache.aset("a", "v2", version=2)
        self.assertEqual(await self.cache.aget_many(["a"]), {})
        self.assertEqual(await self.cache.aget_many(["a"], version=2), {"a": "v2"})

    async def test_aset_many(self) -> None:
        """
        Test that aset_many() stores every value with the timeout, and reports no failures.
        """
        self.assertEqual(await self.cache.aset_many({"a": 1, "b": [2]}, timeout=60), [])
        self.assertEqual(self.cache.get_many(["a", "b"]), {"a": 1, "b": [2]})
        for key in ("a", "b"):
            self.assertTrue(0 < self.ttl(key) <= 60)

    async def test_aset_many_no_timeout(self) -> None:
        """
        Test that aset_many() with None stores the values without expiry.
        """
        await self.cache.aset_many({"a": 1}, timeout=None)
        self.assertEqual(self.ttl("a"), -1)

    async def test_aset_many_empty(self) -> None:
        """
        Test that aset_many() of nothing does nothing.
        """
        self.assertEqual(await self.cache.aset_many({}), [])

    async def test_adelete_many(self) -> None:
        """
        Test that adelete_many() removes all given keys, whether they exist or not.
        """
        await self.cache.aset_many({"a": 1, "b": 2, "c": 3})
        await self.cache.adelete_many(["a", "b", "missing"])
        self.assertEqual(await self.cache.aget_many(["a", "b", "c"]), {"c": 3})

    async def test_adelete_many_empty(self) -> None:
        """
        Test that adelete_many() of no keys does nothing — also for an empty generator.
        """
        await self.cache.aset("a", 1)
        await self.cache.adelete_many([])
        no_keys: list[str] = []
        await self.cache.adelete_many(key for key in no_keys)
        self.assertEqual(await self.cache.aget("a"), 1)

    async def test_aclear(self) -> None:
        """
        Test that aclear() empties the cache.
        """
        await self.cache.aset_many({"a": 1, "b": 2})
        await self.cache.aclear()
        self.assertEqual(await self.cache.aget_many(["a", "b"]), {})


class CounterTestCase(CacheTestCase):
    """
    tests for ``aincr()`` and ``adecr()`` of
    :py:class:`~action0.django_acache.backend.RedisCache`
    """

    async def test_aincr(self) -> None:
        """
        Test that aincr() adds the delta and returns the new value.
        """
        await self.cache.aset("n", 1)
        self.assertEqual(await self.cache.aincr("n"), 2)
        self.assertEqual(await self.cache.aincr("n", 10), 12)
        self.assertEqual(self.cache.get("n"), 12)

    async def test_adecr(self) -> None:
        """
        Test that adecr() subtracts the delta.
        """
        await self.cache.aset("n", 10)
        self.assertEqual(await self.cache.adecr("n", 3), 7)

    async def test_missing(self) -> None:
        """
        Test that aincr() of a missing key raises ValueError, like incr().
        """
        with self.assertRaisesRegex(ValueError, "not found"):
            await self.cache.aincr("missing")

    async def test_atomic(self) -> None:
        """
        Test that concurrent increments never get lost — each is one INCR on the server.
        """
        await self.cache.aset("n", 0)
        results = await asyncio.gather(*(self.cache.aincr("n") for _ in range(50)))
        self.assertEqual(sorted(results), list(range(1, 51)))
        self.assertEqual(await self.cache.aget("n"), 50)


class GenericTestCase(CacheTestCase):
    """
    tests for Django's generic async methods on top of
    :py:class:`~action0.django_acache.backend.RedisCache`
    """

    async def test_aget_or_set(self) -> None:
        """
        Test that aget_or_set() stores the default once and returns the stored value.
        """
        self.assertEqual(await self.cache.aget_or_set("key", lambda: "first"), "first")
        self.assertEqual(await self.cache.aget_or_set("key", "second"), "first")

    async def test_aincr_version(self) -> None:
        """
        Test that aincr_version() moves the value to the next version.
        """
        await self.cache.aset("key", "value")
        self.assertEqual(await self.cache.aincr_version("key"), 2)
        self.assertIsNone(await self.cache.aget("key"))
        self.assertEqual(await self.cache.aget("key", version=2), "value")
