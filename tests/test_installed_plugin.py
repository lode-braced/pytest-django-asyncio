from __future__ import annotations


def _make_downstream_project(pytester, *, manual_load: bool) -> None:
    pytester.makeini(
        """
        [pytest]
        DJANGO_SETTINGS_MODULE = settings
        pythonpath = .
        """
    )
    if manual_load:
        pytester.makeconftest('pytest_plugins = ["pytest_django_asyncio.pytest_plugin"]\n')

    package_dir = pytester.path / "sample_app"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "apps.py").write_text(
        """
from django.apps import AppConfig


class SampleAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sample_app"
""".lstrip(),
        encoding="utf-8",
    )
    (package_dir / "models.py").write_text(
        """
from django.db import models


class Item(models.Model):
    name = models.CharField(max_length=100)
""".lstrip(),
        encoding="utf-8",
    )

    (pytester.path / "settings.py").write_text(
        """
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
SECRET_KEY = "tests"
USE_TZ = True
ROOT_URLCONF = "urls"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "sample_app",
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
MIGRATION_MODULES = {"sample_app": None}
MIDDLEWARE = []
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "DIRS": [],
        "OPTIONS": {},
    }
]
""".lstrip(),
        encoding="utf-8",
    )
    (pytester.path / "urls.py").write_text("urlpatterns = []\n", encoding="utf-8")
    (pytester.path / "test_plugin_usage.py").write_text(
        """
import pytest

def test_plugin_auto_loaded(pytestconfig, request):
    pluginmanager = pytestconfig.pluginmanager
    fixtures_plugins = [
        name
        for name, _plugin in pluginmanager.list_name_plugin()
        if name == "pytest_django_asyncio._pytest_plugin_fixtures"
    ]

    fixturedefs = request._fixturemanager.getfixturedefs("_django_db_helper", request.node)

    assert pluginmanager.hasplugin("pytest_django_asyncio.pytest_plugin")
    assert len(fixtures_plugins) == 1
    assert fixturedefs is not None
    assert fixturedefs[-1].func.__module__ == "pytest_django_asyncio._pytest_plugin_fixtures"


@pytest.mark.parametrize("run_number", [1, 2])
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_async_db_works_without_manual_plugin_loading(run_number):
    from django.apps import apps

    Item = apps.get_model("sample_app", "Item")

    assert await Item.objects.acount() == 0
    await Item.objects.acreate(name=f"item-{run_number}")
    assert await Item.objects.acount() == 1
""".lstrip(),
        encoding="utf-8",
    )


def test_installed_plugin_autoloads(pytester) -> None:
    _make_downstream_project(pytester, manual_load=False)

    result = pytester.runpytest_subprocess("-q")

    result.assert_outcomes(passed=3)


def test_manual_plugin_load_does_not_duplicate_fixture_registration(pytester) -> None:
    _make_downstream_project(pytester, manual_load=True)

    result = pytester.runpytest_subprocess("-q")

    result.assert_outcomes(passed=3)
