"""
Repro: @pytest.mark.asyncio + @pytest.mark.django_db + sync pytest-factoryboy model fixture.

With pytest-django-asyncio installed, default django_db uses async_only DB access.
pytest-factoryboy creates ``widget`` synchronously during fixture setup → RuntimeError.

Expected failure (before workaround):
    Database access is only allowed in an async context ...
"""

import pytest


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_widget_from_sync_factory_fixture(widget):
    """Requests the sync-generated ``widget`` model fixture from register(WidgetFactory)."""
    assert widget.pk is not None
    assert widget.name.startswith("widget-")
