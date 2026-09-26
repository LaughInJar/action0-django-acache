# API reference

Everything public is importable from the package root:

```python
from action0.django_acache import (
    PoolRegistry,
    RedisCache,
    RedisCacheClient,
    aclose_pools,
    registry,
)
```

## Backend

```{eval-rst}
.. automodule:: action0.django_acache.backend
   :members:
```

## Client

```{eval-rst}
.. automodule:: action0.django_acache.client
   :members: RedisCacheClient
```

## Pools

```{eval-rst}
.. automodule:: action0.django_acache.pools
   :members: aclose_pools, registry, PoolRegistry
```

## Options

```{eval-rst}
.. automodule:: action0.django_acache.options
   :members:
```
