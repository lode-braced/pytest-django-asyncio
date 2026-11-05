# pytest-django-asyncio

`pytest-django-asyncio` is a small temporary compatibility plugin for projects that need async Django ORM access to
work correctly with `pytest-django` database fixtures before the equivalent upstream support lands.

It backports the async database fixture behavior proposed
in [pytest-django PR #1223](https://github.com/pytest-dev/pytest-django/pull/1223) and exposes it as an installed pytest
plugin through `pytest11`, so downstream projects get the fix automatically after installation.

I'll deprecate it from pypi once the linked MR gets merged into pytest-django.

## What does this fix/why do tests fail in async contexts?

`pytest-django`'s normal `db` fixture wraps each test in a transaction and rolls it back afterwards. In async tests,
Django's async ORM work runs via `sync_to_async` on a dedicated thread, so the transaction setup and teardown also need
to happen on that thread or database state can escape the rollback boundary.

This package backports the fixture split and async helper approach
from [pytest-django PR #1223](https://github.com/pytest-dev/pytest-django/pull/1223) so async tests marked with
`@pytest.mark.asyncio` can use Django DB fixtures safely when this plugin is installed as a normal dependency.

## Installation

```bash
pip install pytest-django-asyncio
```

You still need the usual pytest stack in the consuming project:

- `pytest`
- `pytest-django`
- `pytest-asyncio`
- `Django`

## Supported versions

| Package          | Minimum | Tested up to |
|------------------|---------|--------------|
| Python           | 3.10    | 3.14         |
| Django           | 4.2     | 6.1          |
| asgiref          | 3.9.1   | 3.13         |
| pytest           | 8.2     | 9.2          |
| pytest-django    | 4.11.1  | 4.14         |
| pytest-asyncio   | 1.1.0   | 1.4          |

asgiref 3.13 needs Python 3.11 or newer, and Django 6.x needs Python 3.12 or newer. CI runs the oldest supported
combination, recent Django and asgiref pairs, and Django 6.1 on PostgreSQL.

## Usage

After installation, pytest auto-loads the plugin through its `pytest11` entry point. That means it should
be ready to go as long as you have the package installed in your python environment.

Typical usage:

```python
import pytest


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_async_orm_works():
    ...
```

The same backport also works when tests request `db` or `transactional_db` directly.

## Caveats

- If a project disables pytest plugin auto-loading, it must explicitly add `pytest_django_asyncio.pytest_plugin` to its
  plugin list.

## How It Works

The package exposes a thin `pytest_plugin.py` entry module. During `pytest_configure`, it applies the small
`DjangoDbBlocker.unblock(async_only=...)` backport needed by the async path and then registers the real fixture override
module late with `@pytest.hookimpl(trylast=True)`.

That late registration lets this package's `_django_db_helper` override win over `pytest-django`.

The sync path delegates back to `pytest-django`; the async path uses a separate helper modeled
on [pytest-django PR #1223](https://github.com/pytest-dev/pytest-django/pull/1223): the transaction start/stop run in
the same `sync_to_async` worker thread that executes the test's ORM calls.

For non-transactional async tests, the main thread and the `sync_to_async` worker thread share one set of database
connections: the main thread's regular connections, so the test transaction covers ORM calls from either thread. During
the test, `connections._connections` is replaced by a small stand-in that hands those connections to the two threads,
and Django's `inc_thread_sharing()` allows their use across threads. Other threads keep their own connections and are
still blocked from database access.

Transactional async tests close the worker thread's connections after each test, so PostgreSQL can drop the test
database at the end of the session.

## Development

Run the test suite against a specific dependency combination with `uv`:

```bash
uv run --isolated --no-project --python 3.13 --with-editable . \
  --with "Django~=6.1.0" --with "asgiref~=3.13.0" pytest
```

Set `POSTGRES_HOST` (and optionally `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`) and add
`--with "psycopg[binary]"` to run against PostgreSQL instead of SQLite. The `compat` job in
`.github/workflows/ci.yml` lists the combinations CI runs.
