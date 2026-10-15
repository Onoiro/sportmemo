from django.apps import AppConfig


class SearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.search"
    verbose_name = "Поиск"

    def ready(self):
        """Подключить сигналы пересчёта поискового индекса.

        В `ready()`, а не на уровне модуля: к этому моменту модели каталога
        и фото загружены, и `post_save` можно вешать на конкретные классы.
        """
        from apps.search import signals

        signals.connect()
