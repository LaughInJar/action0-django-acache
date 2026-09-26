# Action0-Django-ACache

[![CI](https://github.com/LaughInJar/action0-django-acache/actions/workflows/ci.yml/badge.svg)](https://github.com/LaughInJar/action0-django-acache/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/action0-django-acache)](https://pypi.org/project/action0-django-acache/)

Django's Redis cache backend with async methods that are actually async: they
talk to the server through `redis.asyncio` instead of running the sync methods
in a worker thread.

Requires Python 3.11 or newer, Django 5.2 or newer and redis-py 5.0.1 or newer.
Works with Redis and Valkey.

Full documentation including the API reference:
<https://laughinjar.github.io/action0-django-acache/>

**Status:** early. Every async cache method is native and covered by tests
(against fakeredis, and in CI against Redis 7/8 and Valkey 8/9); the API may
still move.

## Installation

```shell
pip install action0-django-acache            # or: uv add action0-django-acache
pip install "action0-django-acache[hiredis]"  # with the C parser
```

## Usage

Swap the backend in your settings — everything else stays as with Django's
Redis backend:

```python
CACHES = {
    "default": {
        "BACKEND": "action0.django_acache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379",
    },
}
```

And await the cache in async code as usual:

```python
from django.core.cache import cache


async def weather(request, city):
    report = await cache.aget(f"weather:{city}")
    if report is None:
        report = await fetch_weather(city)
        await cache.aset(f"weather:{city}", report, timeout=300)
    ...
```

Django's own `RedisCache` runs every one of these calls in a thread via
`sync_to_async` (and on Django 5.2 its `aincr` is not even atomic: a get, then
a set). Here `aget`, `aset`, `aadd`, `atouch`, `adelete`, `ahas_key`,
`aget_many`, `aset_many`, `adelete_many`, `aincr` and `aclear` are coroutines
on `redis.asyncio`, and `aincr` is a single `INCRBY` on the server.

The sync methods are Django's, untouched, and both sides write the same keys
with the same serializer: sync and async code share one cache.

A few redis-py options come in a sync and an async variant. `ASYNC_OPTIONS`
overrides `OPTIONS` for the async side only — and a sync class or `Retry` that
would still reach the async side is an `ImproperlyConfigured` error naming the
fix:

```python
CACHES = {
    "default": {
        "BACKEND": "action0.django_acache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379",
        "OPTIONS": {
            "pool_class": "redis.BlockingConnectionPool",
            "max_connections": 50,
        },
        "ASYNC_OPTIONS": {
            "pool_class": "redis.asyncio.BlockingConnectionPool",
        },
    },
}
```

Under ASGI, Django creates a cache instance per request. The async connection
pools are therefore shared per event loop — all requests reuse the same
connections — and closed when the loop shuts down. `aclose_pools()` closes
them earlier:

```python
from action0.django_acache import aclose_pools

await aclose_pools()
```

See the [usage guide](https://laughinjar.github.io/action0-django-acache/usage.html)
for the details: which method sends which commands, every `ASYNC_OPTIONS` key,
the pool lifecycle, and a fakeredis setup for your tests.

The `action0` namespace is simply the one the author likes to use for
personal projects.

## Development

```shell
uv run pytest          # tests (incl. doctests in src/), against fakeredis
uv run ruff check      # lint
uv run ruff format     # format
uv run mypy            # type-check (strict)
uv run pyright         # type-check
uv run ty check        # type-check
```

`REDIS_URL=redis://localhost:6379/15 uv run pytest` runs the tests against a
real server instead — they flush that database.

## AI disclosure

This library is developed with heavy use of AI coding tools: the code,
tests, and documentation are largely written by
[Claude Code](https://claude.com/claude-code), working from the author's
design brief and reviewed by the author. If that changes how much you want
to rely on this package, that's a fair call — read the source, it's small.

## License

MIT — see [LICENSE](LICENSE).
