from __future__ import annotations

import threading

import pytest

from tests.project.sample_app.models import Item


@pytest.fixture
def sync_item_fixture(db: None, run_number: int) -> str:
    del db

    item = Item.objects.create(name=f"sync-fixture-{run_number}")
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
