import unittest

import redis
import redis.asyncio
from django.core.exceptions import ImproperlyConfigured
from redis.asyncio.connection import DefaultParser
from redis.connection import DefaultParser as SyncParser

from action0.django_acache.options import async_pool_config


class AsyncPoolConfigTestCase(unittest.TestCase):
    """
    tests for :py:func:`~action0.django_acache.options.async_pool_config`
    """

    def test_defaults(self) -> None:
        """
        Test that without overrides the pool and parser classes are the redis.asyncio ones.
        """
        pool_class, options = async_pool_config({"parser_class": SyncParser}, {})
        self.assertIs(pool_class, redis.asyncio.ConnectionPool)
        self.assertEqual(options, {"parser_class": DefaultParser})

    def test_shared_options_carry_over(self) -> None:
        """
        Test that the options both sides understand reach the async pools unchanged.
        """
        _, options = async_pool_config(
            {"parser_class": SyncParser, "db": 3, "socket_timeout": 1.5}, {}
        )
        self.assertEqual(options["db"], 3)
        self.assertEqual(options["socket_timeout"], 1.5)

    def test_overrides(self) -> None:
        """
        Test that ASYNC_OPTIONS replace and extend the sync options.
        """
        _, options = async_pool_config(
            {"parser_class": SyncParser, "db": 3}, {"db": 4, "max_connections": 10}
        )
        self.assertEqual(options["db"], 4)
        self.assertEqual(options["max_connections"], 10)

    def test_classes(self) -> None:
        """
        Test that the pool and parser classes may be overridden by class.
        """
        pool_class, options = async_pool_config(
            {"parser_class": SyncParser},
            {"pool_class": redis.asyncio.BlockingConnectionPool, "parser_class": DefaultParser},
        )
        self.assertIs(pool_class, redis.asyncio.BlockingConnectionPool)
        self.assertIs(options["parser_class"], DefaultParser)

    def test_dotted_paths(self) -> None:
        """
        Test that the pool and parser classes may be given as dotted import paths.
        """
        pool_class, options = async_pool_config(
            {"parser_class": SyncParser},
            {
                "pool_class": "redis.asyncio.BlockingConnectionPool",
                "parser_class": "redis.asyncio.connection.DefaultParser",
            },
        )
        self.assertIs(pool_class, redis.asyncio.BlockingConnectionPool)
        self.assertIs(options["parser_class"], DefaultParser)
        self.assertNotIn("pool_class", options)

    def test_serializer_cannot_be_overridden(self) -> None:
        """
        Test that a serializer in ASYNC_OPTIONS is a configuration error.
        """
        with self.assertRaisesRegex(ImproperlyConfigured, "cannot override serializer"):
            async_pool_config({}, {"serializer": "myapp.JSONSerializer"})

    def test_inputs_unchanged(self) -> None:
        """
        Test that neither the sync options nor the overrides are modified.
        """
        pool_options = {"parser_class": SyncParser, "db": 1}
        overrides = {"pool_class": "redis.asyncio.BlockingConnectionPool", "db": 2}
        async_pool_config(pool_options, overrides)
        self.assertEqual(pool_options, {"parser_class": SyncParser, "db": 1})
        self.assertEqual(
            overrides, {"pool_class": "redis.asyncio.BlockingConnectionPool", "db": 2}
        )
