"""Same pattern with transactional django_db — should pass with pytest-django-asyncio."""

import pytest


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_widget_transactional_db(widget):
    assert widget.pk is not None
