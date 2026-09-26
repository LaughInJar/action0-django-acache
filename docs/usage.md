# Usage

## Setup

Install the package (with `hiredis` for the faster C parser, if you like):

```shell
pip install action0-django-acache            # or: uv add action0-django-acache
pip install "action0-django-acache[hiredis]"
```

Then point a cache at the backend. Everything Django's
[Redis backend](https://docs.djangoproject.com/en/stable/topics/cache/#redis)
accepts works the same way: one or several servers in `LOCATION`, `TIMEOUT`,
`KEY_PREFIX`, `VERSION`, and the redis-py connection options in `OPTIONS`.

```python
CACHES = {
    "default": {
        "BACKEND": "action0.django_acache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379",
        "TIMEOUT": 600,
        "OPTIONS": {
            "socket_timeout": 2,
        },
    },
}
```

## Async methods

Use the cache's async methods in async views, middleware, consumers or tasks,
exactly as with any Django cache:

```python
from django.core.cache import cache
from django.http import JsonResponse


async def weather(request, city):
    report = await cache.aget(f"weather:{city}")
    if report is None:
        report = await fetch_weather(city)
        await cache.aset(f"weather:{city}", report, timeout=300)
    return JsonResponse(report)
```

Django's own backend implements these by running its sync methods in a worker
thread (asgiref's `sync_to_async`), which costs a thread hop per call and
limits concurrency to the thread pool. Here they are coroutines that talk to
the server through {py:mod}`redis.asyncio`:

| Method                                             | Implementation                             |
|----------------------------------------------------|--------------------------------------------|
| `aget`, `aset`, `aadd`, `atouch`, `adelete`, `ahas_key` | native, one command each (`aadd` with a zero timeout: two) |
| `aget_many`                                        | native, one `MGET`                         |
| `aset_many`                                        | native, one pipeline: `MSET` plus an `EXPIRE` per key |
| `adelete_many`                                     | native, one `DEL`                          |
| `aincr`, `adecr`                                   | native, `EXISTS` then an atomic `INCRBY`   |
| `aclear`                                           | native, `FLUSHDB`                          |
| `aget_or_set`, `aincr_version`, `adecr_version`    | Django's generic versions, built on the native ones above |
| `aclose`                                           | does nothing — see [Pools](#pools)         |

The sync methods are Django's, untouched: they use the sync redis-py client
as before, and write the same keys with the same serializer. Sync and async
code therefore share one cache — a value stored with `cache.set()` is found by
`await cache.aget()` and vice versa. Django's own cache users (the cache
middleware, `cache_page`, the cache session backend) call the sync methods and
keep working unchanged.

Two small differences to the sync methods: `aclear()` returns `None` (as
Django's base class declares) rather than the server's reply, and
`adelete_many()` of an empty generator does nothing instead of sending an
empty `DEL`.

## `ASYNC_OPTIONS`

`OPTIONS` apply to the async connection pools, too. A few redis-py options,
though, name classes that come in a sync and an async variant, and the sync
one does not work on the async side. `ASYNC_OPTIONS`, next to `OPTIONS`,
overrides keys for the async pools only:

```python
from redis.asyncio.retry import Retry as AsyncRetry
from redis.backoff import ExponentialBackoff
from redis.retry import Retry

CACHES = {
    "default": {
        "BACKEND": "action0.django_acache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379",
        "OPTIONS": {
            "pool_class": "redis.BlockingConnectionPool",
            "max_connections": 50,
            "retry": Retry(ExponentialBackoff(), 3),
        },
        "ASYNC_OPTIONS": {
            "pool_class": "redis.asyncio.BlockingConnectionPool",
            "retry": AsyncRetry(ExponentialBackoff(), 3),
        },
    },
}
```

| Option             | Sync side (`OPTIONS`)                     | Async side                                               |
|--------------------|-------------------------------------------|----------------------------------------------------------|
| `pool_class`       | default `redis.ConnectionPool`            | default `redis.asyncio.ConnectionPool`, never the sync one |
| `parser_class`     | default redis-py's sync parser            | default redis-py's async parser, never the sync one      |
| `connection_class`, `retry` | as given                         | as in `OPTIONS`, but a sync class or `Retry` is an error — override it |
| `serializer`       | as given                                  | always the same as the sync side; not allowed in `ASYNC_OPTIONS` |
| everything else    | as given                                  | as in `OPTIONS`                                          |

As in `OPTIONS`, `pool_class` and `parser_class` may be dotted import paths.

A sync class or object that would reach the async pools, whether inherited from
`OPTIONS` or given in `ASYNC_OPTIONS`, raises `ImproperlyConfigured` as soon as
the cache is first used — by a sync method, too. Some of them would otherwise
only fail on the first async call; a sync `Retry` would even be accepted
silently and simply never retry. The message names the fix:

```text
django.core.exceptions.ImproperlyConfigured: OPTIONS['retry'] is a redis.retry.Retry, which the async side cannot use: set ASYNC_OPTIONS['retry'] to a redis.asyncio.retry.Retry
```

## Pools

A {py:mod}`redis.asyncio` connection belongs to the event loop it was opened
on. And under ASGI, Django usually creates a new cache instance for every
request (its cache handler keeps instances per async context). Connection
pools kept on the cache instance would mean a new connection to the server for
every request.

So the async pools are kept in one process-wide registry instead, per event
loop and per configuration:

- A pool is created on a loop's first async cache call, for one server URL,
  pool class and set of options. Its connections are opened on demand.
- All cache instances with equal settings share it — whichever request
  created it. `max_connections` therefore limits the connections of the whole
  loop, not of one request.
- Every loop gets pools of its own: each `asyncio.run()`, each thread running
  a loop, each `async_to_sync()` call made outside an event loop.
- A loop's pools are closed when the loop shuts down.
  {py:func}`asyncio.run` and {py:class}`asyncio.Runner` (which ASGI servers
  like uvicorn use), asgiref's `async_to_sync` and
  {py:class}`unittest.IsolatedAsyncioTestCase` all shut down a loop's async
  generators before closing it, and that is the moment the registry uses.
- Django calls every cache's `close()` after each request. That does not
  touch the async pools, and neither does `aclose()`: other requests are
  still using them.

To close the running loop's pools earlier, e.g. at the end of a test that
checks for leaked connections, call
{py:func}`~action0.django_acache.pools.aclose_pools`. A later cache call simply
creates new pools.

```python
from action0.django_acache import aclose_pools

await aclose_pools()
```

A loop that is closed *without* shutting down its async generators (by calling
`loop.close()` yourself) cannot have its pools closed: redis-py reports the
open connections with a `ResourceWarning`, and the registry drops them the next
time a new loop asks for a pool.

## Testing with fakeredis

[fakeredis](https://github.com/cunla/fakeredis-py) can stand in for the server
in your test settings. Give the sync and the async side the connection class
for their side, and the same `FakeServer`, so that both see the same data:

```python
import fakeredis

CACHES = {
    "default": {
        "BACKEND": "action0.django_acache.RedisCache",
        "LOCATION": "redis://localhost:6379/0",
        "OPTIONS": {
            "connection_class": fakeredis.FakeRedisConnection,
            "server": fakeredis.FakeServer(),
        },
        "ASYNC_OPTIONS": {
            "connection_class": fakeredis.FakeAsyncRedisConnection,
        },
    },
}
```

This library's own tests run exactly like that, and in CI additionally against
real Redis and Valkey servers.
