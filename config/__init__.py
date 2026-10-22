"""Пакет конфигурации SportMemo."""

# Celery app импортируется при загрузке настроек: иначе `shared_task`
# уйдёт в приложение по умолчанию без настроек Django и задачи
# не увидят ни брокер, ни базу.
from .celery import app as celery_app

__all__ = ("celery_app",)
