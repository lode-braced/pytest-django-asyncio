from __future__ import annotations

import pytest

from tests.project.sample_app.models import Item


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


def test_late_registered_db_helper_override_wins(request: pytest.FixtureRequest) -> None:
    fixturedefs = request._fixturemanager.getfixturedefs("_django_db_helper", request.node)

    assert fixturedefs is not None
    assert fixturedefs[-1].func.__module__ == "pytest_django_async_db._pytest_plugin_fixtures"
