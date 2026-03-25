import factory

from repro_app.models import Widget


class WidgetFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Widget

    name = factory.Sequence(lambda n: f"widget-{n}")
