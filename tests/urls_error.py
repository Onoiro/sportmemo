"""Маршруты только для тестов страницы 500.

Django рендерит `500.html` лишь тогда, когда представление падает
с исключением, поэтому нужно представление, которое действительно падает.
Основные маршруты подключаются тоже: шаблон страницы ошибки наследует
`base.html`, а тот строит ссылки через `{% url %}`.
"""

from django.urls import include, path


def boom(request):
    raise RuntimeError("намеренная ошибка для теста 500")


urlpatterns = [
    path("boom/", boom, name="boom"),
    path("", include("config.urls")),
]
