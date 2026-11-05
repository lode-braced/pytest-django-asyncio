from __future__ import annotations

import threading

import pytest
import pytest_asyncio
from asgiref.sync import sync_to_async
from django.db import connections
from django.db.backends.base.base import BaseDatabaseWrapper

from pytest_django_asyncio._pytest_plugin_fixtures import (
    _share_connections_between_allowed_threads,
)
from tests.project.sample_app.models import Item


@pytest.fixture
def sync_item_fixture(db: None, run_number: int) -> str:
    del db

    item = Item.objects.create(name=f"sync-fixture-{run_number}")
    return item.name


@pytest_asyncio.fixture
async def async_item_fixture(db: None, run_number: int) -> str:
    del db

    item = await Item.objects.acreate(name=f"async-fixture-{run_number}")
    return item.name


@pytest.mark.parametrize("run_number", [1, 2])
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_async_db_rolls_back_between_runs(run_number: int) -> None:
    assert await Item.objects.acount() == 0
    await Item.objects.acreate(name=f"async-{run_number}")
    assert await Item.objects.acount() == 1


@pytest.mark.parametrize("run_number", [1, 2])
@pytest.mark.asyncio
async def test_async_transactional_db_flushes_between_runs(
    transactional_db: None,
    run_number: int,
) -> None:
    del transactional_db

    assert await Item.objects.acount() == 0
    await Item.objects.acreate(name=f"transactional-{run_number}")
    assert await Item.objects.acount() == 1


@pytest.mark.parametrize("run_number", [1, 2])
def test_sync_db_rolls_back_between_runs(db: None, run_number: int) -> None:
    del db

    assert Item.objects.count() == 0
    Item.objects.create(name=f"sync-{run_number}")
    assert Item.objects.count() == 1


@pytest.mark.parametrize("run_number", [1, 2])
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_async_db_allows_sync_fixture_orm_access(
    sync_item_fixture: str,
    run_number: int,
) -> None:
    assert sync_item_fixture == f"sync-fixture-{run_number}"
    assert await Item.objects.acount() == 1

    item = await Item.objects.afirst()

    assert item is not None
    assert item.name == sync_item_fixture


@pytest.mark.parametrize("run_number", [1, 2])
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_async_db_allows_async_fixture_orm_access(
    async_item_fixture: str,
    run_number: int,
) -> None:
    assert async_item_fixture == f"async-fixture-{run_number}"
    assert await Item.objects.acount() == 1


@pytest.fixture
def main_thread_connection() -> BaseDatabaseWrapper:
    return connections["default"]


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_async_db_runs_on_main_thread_connection_inside_test_transaction(
    main_thread_connection: BaseDatabaseWrapper,
) -> None:
    executor_connection = await sync_to_async(lambda: connections["default"])()

    assert executor_connection is main_thread_connection
    assert connections["default"] is main_thread_connection
    assert main_thread_connection.in_atomic_block

    third_thread_connections: list[BaseDatabaseWrapper] = []
    thread = threading.Thread(
        target=lambda: third_thread_connections.append(connections["default"])
    )
    thread.start()
    thread.join()

    assert third_thread_connections[0] is not main_thread_connection


@pytest.mark.parametrize("run_number", [1, 2])
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_async_db_main_thread_orm_access_shares_test_transaction(
    monkeypatch: pytest.MonkeyPatch,
    run_number: int,
) -> None:
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

    assert await Item.objects.acount() == 0
    await Item.objects.acreate(name=f"executor-{run_number}")
    assert Item.objects.count() == 1
    Item.objects.create(name=f"main-{run_number}")
    assert await Item.objects.acount() == 2


def test_connection_sharing_ends_with_the_block(
    main_thread_connection: BaseDatabaseWrapper,
) -> None:
    thread_id = threading.get_ident()
    original_connections_local = connections._connections

    with _share_connections_between_allowed_threads(
        {"default": main_thread_connection},
        main_thread_id=thread_id,
        executor_thread_id=thread_id,
    ):
        assert main_thread_connection.allow_thread_sharing

    assert not main_thread_connection.allow_thread_sharing
    assert connections._connections is original_connections_local


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_async_db_blocks_sync_orm_access_from_third_thread() -> None:
    exceptions: list[Exception] = []

    def attempt_orm_access() -> None:
        try:
            Item.objects.count()
        except Exception as exc:
            exceptions.append(exc)

    thread = threading.Thread(target=attempt_orm_access)
    thread.start()
    thread.join()

    assert len(exceptions) == 1
    assert str(exceptions[0]) == (
        "Database access is only allowed in an async context, "
        "modify your test fixtures to be async or use the transactional_db fixture."
    )


def test_late_registered_db_helper_override_wins(request: pytest.FixtureRequest) -> None:
    fixturedefs = request._fixturemanager.getfixturedefs("_django_db_helper", request.node)

    assert fixturedefs is not None
    assert fixturedefs[-1].func.__module__ == "pytest_django_asyncio._pytest_plugin_fixtures"
