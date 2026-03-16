from __future__ import annotations

from django.apps import AppConfig


class SampleAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tests.project.sample_app"
