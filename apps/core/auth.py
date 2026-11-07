"""Вход: куда вести анонима, нажавшего кнопку POST.

`login_required` на POST-представлении отправляет анонима на страницу
входа с `next`, равным адресу самого POST. После входа allauth ведёт туда
GET-ом, представление отвечает 405, страница входа предлагает войти снова —
человек попадает в петлю, из которой не выйти без правки адреса в строке
браузера.

Возврат должен вести на страницу, которую можно открыть GET-ом: данные
формы к этому моменту потеряны, и кнопку всё равно нажимать заново.
"""

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import resolve_url
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST


def post_action(fallback):
    """Действие только для POST, и аноним возвращается на GET-адрес.

    Порядок проверок: метод, потом вход. Анонимный GET получает 405, а не
    редирект на вход: адрес решения ничего не раскрывает, а лишний переход
    по кнопке «обновить» не должен уводить со страницы.

    `next` берётся из скрытого поля формы и проверяется от открытого
    редиректа — тот же приём фишинга, что в `_safe_next` у форм.
    Небезопасный или пустой заменяется `fallback`: это имя маршрута списка,
    а не адрес самого действия, иначе вернулась бы та же петля 405.
    """

    def decorator(view):
        @require_POST
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                value = request.POST.get("next", "")
                if value and url_has_allowed_host_and_scheme(
                    value,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    return redirect_to_login(value)
                return redirect_to_login(resolve_url(fallback))
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
