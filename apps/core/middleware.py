"""Middleware режима «закрыто от индексации» (стейдж)."""

from django.conf import settings


class NoindexAllMiddleware:
    """`X-Robots-Tag` на всех ответах при `SPORTMEMO_NOINDEX_ALL`.

    Флаг читается в `__call__`, а не в `__init__`: middleware создаётся
    один раз при старте, и значение из `override_settings` иначе
    не подхватилось бы в тестах.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if settings.SPORTMEMO_NOINDEX_ALL:
            response["X-Robots-Tag"] = "noindex, nofollow"
        return response
