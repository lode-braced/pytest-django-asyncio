from __future__ import annotations

from django.db import models


class Item(models.Model):
    name = models.CharField(max_length=100)
