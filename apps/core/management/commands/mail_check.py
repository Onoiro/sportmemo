"""Проверка настроек почты и тестовая отправка.

На стейдже и на проде нужна повторяемая проверка «почта жива»: без неё
приходится открывать `shell` и вписывать туда адрес и секрет. Команда
печатает настройки без пароля и, если передан адрес, отправляет письмо.
"""

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _


class Command(BaseCommand):
    # Строки берутся обычным `gettext`, а не ленивым: `argparse` форматирует
    # `--help` сам и падает на ленивом объекте вместо строки.
    help = _("Показать настройки почты и отправить тестовое письмо")

    def add_arguments(self, parser):
        parser.add_argument(
            "address",
            nargs="?",
            help=_("Адрес, на который отправить тестовое письмо"),
        )

    def handle(self, *args, **options):
        self._print_settings()
        address = options["address"]
        if address:
            self._send_probe(address)

    def _print_settings(self):
        rows = [
            (_("Бэкенд"), settings.EMAIL_BACKEND),
            (_("Сервер"), settings.EMAIL_HOST),
            (_("Порт"), settings.EMAIL_PORT),
            (_("SSL"), settings.EMAIL_USE_SSL),
            (_("Таймаут, с"), settings.EMAIL_TIMEOUT),
            (_("Отправитель"), settings.DEFAULT_FROM_EMAIL),
            (_("Пользователь SMTP"), settings.EMAIL_HOST_USER),
        ]
        for label, value in rows:
            self.stdout.write(f"{label}: {self._show(value)}")
        # Сам пароль не выводится ни целиком, ни частью: команду запускают
        # на сервере, и вывод попадает в логи и в историю терминала.
        state = _("задан") if settings.EMAIL_HOST_PASSWORD else _("не задан")
        self.stdout.write(f"{_('Пароль')}: {state}")

    def _send_probe(self, address):
        body = _(
            "Это проверка настроек почты SportMemo. "
            "Если письмо дошло, отправка работает.\n\n"
            "Контакт для обращений: %(email)s."
        ) % {"email": settings.SPORTMEMO_CONTACT_EMAIL}
        try:
            sent = send_mail(
                subject=_("Проверка почты SportMemo"),
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[address],
                fail_silently=False,
            )
        except Exception as exc:  # noqa: BLE001 — причина нужна человеку
            # Трейсбек тут бесполезен: нужен ответ «почта не работает»
            # и причина, а скрипт деплоя смотрит на код выхода.
            raise CommandError(
                _("Не удалось отправить письмо: %(error)s") % {"error": exc}
            ) from exc
        if not sent:
            raise CommandError(_("Почта не отправила письмо: проверьте настройки"))
        self.stdout.write(
            self.style.SUCCESS(_("Письмо отправлено на %(email)s") % {"email": address})
        )

    @staticmethod
    def _show(value):
        if isinstance(value, bool):
            return _("да") if value else _("нет")
        if value is None or value == "":
            return _("не задан")
        return str(value)
