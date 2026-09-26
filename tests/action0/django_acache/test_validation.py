import unittest
from typing import Any

import fakeredis
import redis
import redis.asyncio
from django.core.exceptions import ImproperlyConfigured
from redis.asyncio.retry import Retry as AsyncRetry
from redis.backoff import NoBackoff
from redis.retry import Retry as SyncRetry

from action0.django_acache.validation import check_async_options


class CheckAsyncOptionsTestCase(unittest.TestCase):
    """
    tests for :py:func:`~action0.django_acache.validation.check_async_options`
    """

    def assertRejected(
        self, options: dict[str, Any], overrides: dict[str, Any], message: str
    ) -> None:
        """Assert that the options raise ImproperlyConfigured with exactly this message."""
        with self.assertRaises(ImproperlyConfigured) as caught:
            check_async_options(options, overrides)
        self.assertEqual(str(caught.exception), message)

    def test_async_values_pass(self) -> None:
        """
        Test that async classes and objects pass, including subclasses like fakeredis's.
        """
        check_async_options(
            {
                "pool_class": redis.asyncio.BlockingConnectionPool,
                "connection_class": fakeredis.FakeAsyncRedisConnection,
                "retry": AsyncRetry(NoBackoff(), 3),
            },
            {},
        )

    def test_unrelated_options_pass(self) -> None:
        """
        Test that options without a sync and an async variant are not checked.
        """
        check_async_options({"db": 1, "socket_timeout": 2, "retry": None}, {})

    def test_inherited_sync_retry(self) -> None:
        """
        Test that a sync Retry inherited from OPTIONS is rejected, naming the fix.
        """
        self.assertRejected(
            {"retry": SyncRetry(NoBackoff(), 3)},
            {},
            "OPTIONS['retry'] is a redis.retry.Retry, which the async side cannot use: "
            "set ASYNC_OPTIONS['retry'] to a redis.asyncio.retry.Retry",
        )

    def test_inherited_sync_connection_class(self) -> None:
        """
        Test that a sync connection class inherited from OPTIONS is rejected.
        """
        self.assertRejected(
            {"connection_class": redis.SSLConnection},
            {},
            "OPTIONS['connection_class'] is redis.connection.SSLConnection, which the async "
            "side cannot use: set ASYNC_OPTIONS['connection_class'] to a redis.asyncio "
            "connection class",
        )

    def test_sync_values_in_async_options(self) -> None:
        """
        Test that a sync value given in ASYNC_OPTIONS itself is rejected as such.
        """
        pool = redis.BlockingConnectionPool
        self.assertRejected(
            {"pool_class": pool},
            {"pool_class": "redis.BlockingConnectionPool"},
            "ASYNC_OPTIONS['pool_class'] must be a redis.asyncio connection pool class, "
            "not redis.connection.BlockingConnectionPool",
        )
        self.assertRejected(
            {"retry": SyncRetry(NoBackoff(), 3)},
            {"retry": SyncRetry(NoBackoff(), 3)},
            "ASYNC_OPTIONS['retry'] must be a redis.asyncio.retry.Retry, not a redis.retry.Retry",
        )

    def test_not_a_class(self) -> None:
        """
        Test that a class option holding an instance or a function is rejected, too.
        """
        self.assertRejected(
            {"connection_class": print},
            {"connection_class": print},
            "ASYNC_OPTIONS['connection_class'] must be a redis.asyncio connection class, "
            "not a builtins.builtin_function_or_method",
        )
