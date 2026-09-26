"""
Checks that the async pools get async redis-py classes and objects.

The async pools inherit ``OPTIONS``, so an option naming a sync redis-py class or object reaches
them unless ``ASYNC_OPTIONS`` overrides it. Some of those would only fail on first use; a sync
:py:class:`redis.retry.Retry` even fails silently: the async connection accepts it, and it never
retries — its ``call_with_retry()`` returns the connect coroutine instead of awaiting it. So
these options are checked when the cache client is created, and a wrong one is an error that
names the fix.
"""

from collections.abc import Callable
from collections.abc import Mapping
from typing import Any
from typing import NamedTuple

import redis.asyncio
from django.core.exceptions import ImproperlyConfigured
from redis.asyncio.connection import AbstractConnection
from redis.asyncio.retry import Retry


class Rule(NamedTuple):
    """What the value of an option must be on the async side."""

    #: whether a value is fine for the async pools
    accepts: Callable[[Any], bool]
    #: what the value should be instead, for the error message
    expected: str


def _subclass_of(base: type) -> Callable[[Any], bool]:
    """A check for classes derived from ``base``."""
    return lambda value: isinstance(value, type) and issubclass(value, base)


#: the options that come in a sync and an async variant, and what the async pools need
RULES: dict[str, Rule] = {
    "pool_class": Rule(
        _subclass_of(redis.asyncio.ConnectionPool), "a redis.asyncio connection pool class"
    ),
    "connection_class": Rule(_subclass_of(AbstractConnection), "a redis.asyncio connection class"),
    "retry": Rule(
        lambda value: value is None or isinstance(value, Retry), "a redis.asyncio.retry.Retry"
    ),
}


def check_async_options(options: Mapping[str, Any], overrides: Mapping[str, Any]) -> None:
    """
    Raise if an option of the async pools is a sync redis-py class or object.

    >>> from redis.backoff import NoBackoff
    >>> from redis.retry import Retry
    >>> check_async_options({"retry": Retry(NoBackoff(), 3)}, {})
    Traceback (most recent call last):
    ...
    django.core.exceptions.ImproperlyConfigured: OPTIONS['retry'] is a redis.retry.Retry, which the async side cannot use: set ASYNC_OPTIONS['retry'] to a redis.asyncio.retry.Retry

    :param options: the async pool options, ``pool_class`` included
    :param overrides: the cache's ``ASYNC_OPTIONS``, which tell where a value came from
    :raises ImproperlyConfigured: naming the option, where it was set, and what it should be
    """
    for key, rule in RULES.items():
        if key in options and not rule.accepts(options[key]):
            raise ImproperlyConfigured(
                _message(key, options[key], rule.expected, overridden=key in overrides)
            )


def _message(key: str, value: Any, expected: str, *, overridden: bool) -> str:
    """The error message for an option whose value doesn't work on the async side."""
    if overridden:
        return f"ASYNC_OPTIONS[{key!r}] must be {expected}, not {_describe(value)}"
    return (
        f"OPTIONS[{key!r}] is {_describe(value)}, which the async side cannot use: "
        f"set ASYNC_OPTIONS[{key!r}] to {expected}"
    )


def _describe(value: Any) -> str:
    """A class by its dotted path, anything else by the dotted path of its class."""
    if isinstance(value, type):
        return f"{value.__module__}.{value.__qualname__}"
    kind = type(value)
    return f"a {kind.__module__}.{kind.__qualname__}"
