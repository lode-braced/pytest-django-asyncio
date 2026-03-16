from __future__ import annotations

import inspect

import pytest

_FIXTURES_PLUGIN_NAME = "pytest_django_asyncio._pytest_plugin_fixtures"


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
