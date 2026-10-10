"""Служебные представления приложения core."""

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@require_GET
@never_cache
def healthz(request):
    """Проверка работоспособности: приложение и соединение с БД."""
    checks = {"app": "ok"}
    status = 200

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["db"] = "ok"
    except Exception as exc:  # noqa: BLE001 — healthcheck не должен падать
        checks["db"] = f"error: {exc.__class__.__name__}"
        status = 503

    return JsonResponse(checks, status=status)
