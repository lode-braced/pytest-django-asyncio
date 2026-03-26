from __future__ import annotations

import contextvars
import inspect

import pytest

_FIXTURES_PLUGIN_NAME = "pytest_django_asyncio._pytest_plugin_fixtures"
_DB_FIXTURE_NAMES = frozenset(
    {
        "db",
        "transactional_db",
        "django_db_reset_sequences",
        "django_db_serialized_rollback",
        "_django_db_helper",
        "_sync_django_db_helper",
        "_async_django_db_helper",
    }
)


def _needs_backport() -> bool:
    import pytest_django.fixtures
    import pytest_django.plugin

    return not (
        hasattr(pytest_django.fixtures, "_async_django_db_helper")
        and "async_only"
        in inspect.signature(pytest_django.plugin.DjangoDbBlocker.unblock).parameters
    )


def _install_backport() -> None:
    import pytest_django.plugin

    if "async_only" in inspect.signature(pytest_django.plugin.DjangoDbBlocker.unblock).parameters:
        return

    from ._pytest_plugin_fixtures import install_async_only_unblock_patch

    install_async_only_unblock_patch(pytest_django.plugin.DjangoDbBlocker)


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
    if not _needs_backport():
        return

    _install_backport()

    if config.pluginmanager.hasplugin(_FIXTURES_PLUGIN_NAME):
        return

    from . import _pytest_plugin_fixtures

    config.pluginmanager.register(_pytest_plugin_fixtures, name=_FIXTURES_PLUGIN_NAME)


def _get_asyncio_runner_fixture_id(request: pytest.FixtureRequest) -> str | None:
    asyncio_marker = request.node.get_closest_marker("asyncio")
    if asyncio_marker is None:
        return None

    loop_scope = (
        asyncio_marker.kwargs.get("loop_scope")
        or asyncio_marker.kwargs.get("scope")
        or request.config.getini("asyncio_default_test_loop_scope")
        or "function"
    )
    return f"_{loop_scope}_scoped_runner"


def _should_wrap_sync_db_fixture(
    fixturedef: pytest.FixtureDef[object],
    request: pytest.FixtureRequest,
) -> bool:
    if fixturedef.argname in _DB_FIXTURE_NAMES:
        return False

    if inspect.iscoroutinefunction(fixturedef.func) or inspect.isasyncgenfunction(fixturedef.func):
        return False

    if inspect.isgeneratorfunction(fixturedef.func):
        return False

    if (
        not request.node.get_closest_marker("django_db")
        and "transactional_db" not in request.fixturenames
    ):
        return False

    return any(argname in _DB_FIXTURE_NAMES for argname in fixturedef.argnames)


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(
    fixturedef: pytest.FixtureDef[object],
    request: pytest.FixtureRequest,
) -> object | None:
    runner_fixture_id = _get_asyncio_runner_fixture_id(request)
    if runner_fixture_id is None or not _should_wrap_sync_db_fixture(fixturedef, request):
        return (yield)

    from asgiref.sync import sync_to_async

    original_fixture = fixturedef.func

    def synchronized_fixture(*args: object, **kwargs: object) -> object:
        runner = request.getfixturevalue(runner_fixture_id)
        context = contextvars.copy_context()

        async def setup() -> object:
            return await sync_to_async(original_fixture, thread_sensitive=True)(*args, **kwargs)

        return runner.run(setup(), context=context)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(fixturedef, "func", synchronized_fixture)
        return (yield)
