# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`action0-django-acache` is a Django cache backend: Django's own Redis backend (`django.core.cache.backends.redis.RedisCache`) subclassed so that its async methods (`aget`, `aset`, ...) run natively on `redis.asyncio` instead of wrapping the sync methods in `sync_to_async`. It ships the `action0.django_acache` package (`action0` is a PEP 420 namespace package) from a `src/` layout, is built with hatchling, and uses `uv` for environment/dependency management. Runtime dependencies: `django>=5.2` and `redis>=5.0.1`; the `hiredis` extra passes through to `redis[hiredis]`. Settings use it as `"BACKEND": "action0.django_acache.RedisCache"`.

## Rules

- **Never commit without asking.** Also never push, tag, or publish on your own.
- **Branches + PRs.** All changes go through feature branches and GitHub pull requests that Simon reviews and merges — never commit to `main` directly. (Only the initial implementation is built directly on `main`; once that phase is over, this applies without exception.)
- **Discuss first.** Always present the plan and the intended edits and get agreement before changing files.
- Every code change comes with: tests, docstrings, inline comments where the code isn't self-explanatory, and updated usage examples in `README.md` and the Sphinx docs (`docs/usage.md`).
- Before considering work done, run ruff, mypy, pyright, ty and pytest (commands below) and fix what they report.
- Supported Python versions: 3.11 up to the latest release. Don't use syntax or stdlib features introduced after 3.11 (no PEP 695 `type` aliases or generics — use `TypeAlias`), and don't rely on behavior removed in newer versions.
- Prefer many small modules and short functions over large ones.

## Commands

`uv run` syncs the environment automatically (the dev dependency group is installed by default), so no separate install step is needed.

```sh
uv run pytest                                              # all tests (fakeredis)
uv run pytest tests/action0/django_acache/test_pools.py   # one file
uv run pytest tests/action0/django_acache/test_pools.py::LifecycleTestCase::test_threads  # one test
REDIS_URL=redis://localhost:6379/15 uv run pytest          # against a real server (flushes that db!)

uv run --with "django==5.2.*" pytest   # the oldest supported Django (the lock has 6.x on 3.12+)
uv run --with "redis==5.0.1" pytest    # the oldest supported redis-py

uv run ruff check      # lint (add --fix to autofix)
uv run ruff format     # format
uv run mypy            # type-check (strict; files are configured in pyproject.toml)
uv run pyright         # type-check
uv run ty check        # type-check

uv run --group docs sphinx-build -W --keep-going -b html docs docs/_build/html  # build docs

uv build               # build sdist + wheel into dist/
```

`pytest` also runs the `>>>` examples in the docstrings as doctests (`--doctest-modules` over `src/`), so docstring examples must produce their shown output exactly. pytest turns **every warning into an error** (`filterwarnings = ["error"]`) — deliberately, because the `ResourceWarning`s redis-py emits for unclosed connections are exactly what the pool lifecycle must prevent.

## Architecture

Modules under `src/action0/django_acache/`, from the leaves up (`options.py` uses `validation.py`):

- `options.py` — `async_pool_config(pool_options, overrides)`: derives the async pool class and pool kwargs from the kwargs Django built for its sync pools (`RedisCacheClient._pool_options`) plus the cache's `ASYNC_OPTIONS`. `pool_class`/`parser_class` default to the `redis.asyncio` variants (never the sync ones from `OPTIONS`) and accept dotted paths; `serializer` may not be overridden (both sides must read each other's values).
- `validation.py` — `check_async_options(options, overrides)`, called by `async_pool_config`: rejects sync redis-py values that would reach the async pools (`RULES`: `pool_class` must subclass `redis.asyncio.ConnectionPool`, `connection_class` `redis.asyncio.connection.AbstractConnection`, `retry` must be a `redis.asyncio.retry.Retry` or `None`) with an `ImproperlyConfigured` naming the key, whether it came from `OPTIONS` or `ASYNC_OPTIONS`, and the fix. Simon's decision; the motivating case: a sync `Retry` on an async connection is accepted silently and never retries (measured: 1 connect attempt instead of 4). Runs when the client is built, i.e. on the cache's first use — sync use included.
- `pools.py` — `PoolRegistry` and the module-level `registry`: one shared `redis.asyncio.Redis` client (and its pool) per running event loop and per configuration (pool class, URL, options). Lookup compares options by identity, then equality — not by hash, because Django passes an unhashable `redis.DriverInfo` dataclass. On a loop's first use the registry starts an async generator on it (`_close_at_shutdown`) whose `finally` closes that loop's pools; `loop.shutdown_asyncgens()` (called by `asyncio.run`, `asyncio.Runner`, uvicorn, asgiref's `async_to_sync`, `IsolatedAsyncioTestCase`) triggers it. `aclose()`/`aclose_pools()` close the running loop's pools early by closing that generator, i.e. through the same path. Loops closed without shutting down their async generators are purged when the next new loop registers.
- `client.py` — `RedisCacheClient(django RedisCacheClient)`: an async twin (`aadd`, `aget`, ...) of every sync method, line by line, plus `aget_client()` which asks the registry. Takes `async_options` in addition to Django's client arguments.
- `backend.py` — `RedisCache(django RedisCache)`: reads `ASYNC_OPTIONS` from the cache params, builds the client with it (`_cache`), and overrides every async method Django would otherwise hand to a thread: `aadd aget aset atouch adelete aget_many ahas_key aincr aset_many adelete_many aclear aclose`. The rest (`adecr`, `aget_or_set`, `aincr_version`, `adecr_version`) are Django's generic `BaseCache` versions, which build on these natively — `test_async_methods_never_use_the_sync_client` guards that. `get_backend_timeout` is overridden only to type the result as `int | None`.

Deliberate decisions worth keeping, all confirmed by Simon — don't reopen them without a new reason:

- **Pools shared per event loop, not per cache instance.** Under ASGI Django creates a cache instance per request (the cache handler stores instances in an asgiref context-local; a task only sees instances its parent context already created). Per-instance pools would open a connection per request and leak them. Rejected alternative: per-instance pools closed by an async `request_finished` receiver — a TCP connect per request, and code outside requests (management commands, `async_to_sync`, Celery) would still need the loop-shutdown hook.
- **One class for sync + async**, extending Django's backend; the sync side stays Django's untouched code.
- **`ASYNC_OPTIONS`** as a top-level cache setting next to `OPTIONS`, overriding keys for the async side. Rejected alternatives: nested `OPTIONS["async"]` and `async_*` prefixed keys — both would make the settings invalid for Django's stock backend, whereas a top-level key keeps them switchable.
- **One shared `redis.asyncio.Redis` per pool** instead of one per call like Django's sync side: constructing one costs ~47µs, about a local round trip.
- `aclose()` (and Django's per-request `close()`) do not close the shared pools.
- Tests: fakeredis locally (sync and async pools sharing one `FakeServer`), plus a CI job running the same suite against real Redis and Valkey containers via `REDIS_URL`.

Conventions:

- The version is single-sourced as `__version__` in `src/action0/django_acache/__init__.py`; hatch extracts it with the regex in `[tool.hatch.version]`. Bump it only there.
- Releases: pushing a `vX.Y.Z` tag triggers `.github/workflows/release.yml`, which re-runs all checks, verifies the tag matches `__version__`, builds, and publishes to PyPI via trusted publishing (environment `pypi`). Never bump the version, tag, or publish on your own — releasing is the user's call.
- CI (`ci.yml`): the checks matrix (Python 3.11–3.14, experimental 3.15) against fakeredis; the `servers` job runs pytest against `redis:8`, `redis:7`, `valkey/valkey:9`, `valkey/valkey:8` service containers; docs build + Pages deploy.
- The dev group pins `django>=6.0; python_version >= '3.12'` so the lock forks: Django 5.2 on 3.11, the latest Django on 3.12+. CI's 3.11 job is therefore the Django 5.2 test.
- Django and redis-py private attributes the code relies on (`_servers`, `_serializer`, `_pool_options`, `_pools`, `_get_connection_pool_index`, `_options`) are declared as annotations in the subclasses, because django-stubs doesn't list them. The async `DefaultParser` class depends on the redis-py version and on hiredis — never assert its concrete name.
- Tests mirror the `src/` layout under `tests/action0/django_acache/` (a regular package, for the relative imports of `support.py`) and are `unittest.TestCase`/`IsolatedAsyncioTestCase` classes, executed via pytest. `support.py` configures Django settings and provides `cache_settings()`, `make_cache()`, `async_server()` and `CacheTestCase`. Tests that use the sync side must close its pools (`close_sync_pools`), or a real server's sockets raise `ResourceWarning`. Cache keys in tests must be memcached-safe (no spaces), or Django's `CacheKeyWarning` fails them. Real-server tests: the Django cache handler shares instances with child tasks once the parent context touched them — simulate separate requests without touching `caches` in the parent.
- When an ignore is unavoidable, silence each checker with its own syntax (`# type: ignore[code]  # ty: ignore[code]`). ty picks redis-py 8's sync overload for some async commands (e.g. `incr`), hence one `ty: ignore[invalid-await]`.
- Ruff enforces one import per line (isort `force-single-line`), line length 99, `action0` as first-party. Ruff only honours `.gitignore` inside a git repository.
- Docs live in `docs/` (Sphinx + Furo, MyST Markdown pages, autodoc for the API reference, intersphinx to Python, Django and redis-py). Docstrings are Sphinx-reST (`:param:`, `:py:func:` roles). CI builds them with `-W` on every run and deploys to GitHub Pages on pushes to `main`. Examples in `docs/usage.md` and `README.md` must stay truthful.
- The GitHub Pages site must be enabled once per repo before `deploy-docs` can run: `gh api repos/LaughInJar/action0-django-acache/pages -X POST -f build_type=workflow`.
- The README carries an AI-usage disclosure section — keep it accurate when the development workflow changes.
