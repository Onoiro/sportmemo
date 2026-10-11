from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "Ядро"

    def ready(self):
        """Зарегистрировать юникодный конвертер slug.

        Регистрация идёт в `ready()`, потому что `register_converter`
        работает с реестром URL, который Django создаёт при загрузке
        приложений. Конвертер нужен до первого разбора адреса.
        """
        from django.urls import register_converter

        from apps.core.converters import UnicodeSlugConverter

        register_converter(UnicodeSlugConverter, "uslug")
