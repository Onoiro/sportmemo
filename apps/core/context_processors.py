"""Контекстные процессоры SportMemo."""

from django.conf import settings


def project_info(request):
    """Версия и имя проекта — доступны во всех шаблонах."""
    return {
        "SPORTMEMO_VERSION": settings.VERSION,
        "SPORTMEMO_SITE_NAME": "SportMemo",
    }
