# action0-django-acache

Django's Redis cache backend with async methods that are actually async: they
talk to the server through {py:mod}`redis.asyncio` instead of running the sync
methods in a worker thread.

```shell
uv add action0-django-acache    # not on PyPI yet — install from GitHub for now
```

Swap the backend in your settings:

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


async def dashboard(request):
    stats = await cache.aget("stats")
    ...
```

**Highlights**:

- A drop-in replacement for Django's
  {py:class}`~django.core.cache.backends.redis.RedisCache`: same settings, same
  sync methods (they *are* Django's), same keys and serialized values, so sync
  and async code share one cache.
- Every async method Django's backend hands to a thread — `aget`, `aset`,
  `aadd`, `atouch`, `adelete`, `ahas_key`, `aget_many`, `aset_many`,
  `adelete_many`, `aincr`, `aclear` — is a native coroutine. `aincr` stays a
  single, atomic `INCR` on the server.
- Connection pools shared per event loop: under ASGI, Django creates a cache
  instance per request, but all of them reuse the same connections. Pools close
  themselves when their loop shuts down.
- `ASYNC_OPTIONS` for the few redis-py options that differ between the sync
  and the async client (pool, parser and connection classes, retry objects).
- Tested against Redis and Valkey. Django 5.2+, redis-py 5.0.1+.
- Fully typed (checked with mypy strict, pyright and ty), Python 3.11+.

The `action0` namespace is simply the one the author likes to use for personal
projects.

```{toctree}
:maxdepth: 2

usage
api
```
