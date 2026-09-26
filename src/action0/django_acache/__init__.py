"""
Django's Redis cache backend with native async methods on :py:mod:`redis.asyncio`.
"""

from .backend import RedisCache
from .client import RedisCacheClient
from .pools import PoolRegistry
from .pools import aclose_pools
from .pools import registry

__version__: str = "0.1.0"

__all__ = [
    "PoolRegistry",
    "RedisCache",
    "RedisCacheClient",
    "aclose_pools",
    "registry",
]
