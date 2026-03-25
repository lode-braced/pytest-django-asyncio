# Repro: pytest-django-asyncio + pytest-factoryboy (sync model fixtures)

Minimal Django project showing why **async tests** that use **pytest-factoryboy `register()` model fixtures** fail when **`pytest-django-asyncio`** is installed.

## Setup

From the **learnskin-v3** repo root:

```bash
cd tmp/pytest_factory_async_repro
```

Create a venv and install (use **`uv`** if your system `python3 -m venv` lacks `ensurepip`):

```bash
uv venv .venv
uv pip install -r requirements.txt
# or: .venv/bin/pip install -r requirements.txt
```

## Reproduce the error

```bash
.venv/bin/python -m pytest tests/test_sync_fixture_on_async_test.py -v --create-db
```

Expected: **`RuntimeError: Database access is only allowed in an async context`** during setup of the `widget` fixture.

## Show the workaround

```bash
.venv/bin/python -m pytest tests/test_workaround_transactional.py -v --create-db
```

Expected: **passes** (`@pytest.mark.django_db(transaction=True)` uses the synchronous DB helper).

## Uninstall plugin (control)

```bash
.venv/bin/pip uninstall -y pytest-django-asyncio
.venv/bin/python -m pytest tests/test_sync_fixture_on_async_test.py -v --create-db
```

Behavior may differ by pytest-django version; without the plugin the first test often passes again.
