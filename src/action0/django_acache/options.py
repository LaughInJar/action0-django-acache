"""
The configuration of the async connection pools.

Django's :py:class:`~django.core.cache.backends.redis.RedisCacheClient` turns the cache's
``OPTIONS`` into keyword arguments for its connection pools. Nearly all of them (``db``,
``password``, ``socket_timeout``, ...) mean the same to :py:mod:`redis.asyncio`, so the async
pools start out with the very same ones. A few name redis-py classes that come in a sync and an
async variant, though: ``pool_class``, ``parser_class``, ``connection_class``, ``retry``. The
cache setting ``ASYNC_OPTIONS`` overrides keys for the async pools only.
"""

from collections.abc import Mapping
from typing import Any
from typing import TypeAlias

import redis.asyncio
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from redis.asyncio.connection import DefaultParser

AsyncPoolClass: TypeAlias = type[redis.asyncio.ConnectionPool]

#: ``OPTIONS`` keys that must stay the same for the sync and the async side: values written by
#: one side have to be readable by the other
SHARED_ONLY = frozenset({"serializer"})


def async_pool_config(
    pool_options: Mapping[str, Any], overrides: Mapping[str, Any]
) -> tuple[AsyncPoolClass, dict[str, Any]]:
    """
    Derive the async pool class and pool options from the sync ones.

    ``pool_class`` and ``parser_class`` default to their :py:mod:`redis.asyncio` variants —
    never to the sync ones configured in ``OPTIONS`` — and, like in ``OPTIONS``, both may be
    given as dotted import paths.

    >>> from redis.connection import DefaultParser as SyncParser
    >>> pool_class, options = async_pool_config(
    ...     {"db": 1, "parser_class": SyncParser},
    ...     {"pool_class": "redis.asyncio.BlockingConnectionPool"},
    ... )
    >>> pool_class.__name__, options["db"], options["parser_class"] is DefaultParser
    ('BlockingConnectionPool', 1, True)

    :param pool_options: the keyword arguments Django built for its sync pools
    :param overrides: the cache's ``ASYNC_OPTIONS``
    :return: the pool class and the keyword arguments for its ``from_url()``
    :raises ImproperlyConfigured: if ``overrides`` contains a key that must be shared
    """
    if shared := sorted(SHARED_ONLY & overrides.keys()):
        raise ImproperlyConfigured(
            f"ASYNC_OPTIONS cannot override {', '.join(shared)}: "
            "the sync and the async side must read each other's values"
        )
    options = {**pool_options, "parser_class": DefaultParser, **overrides}
    pool_class = _load(options.pop("pool_class", redis.asyncio.ConnectionPool))
    options["parser_class"] = _load(options["parser_class"])
    return pool_class, options


def _load(value: Any) -> Any:
    """Import ``value`` if it is a dotted path, like Django does for ``OPTIONS``."""
    return import_string(value) if isinstance(value, str) else value
