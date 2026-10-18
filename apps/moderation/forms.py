"""Форма жалобы на ошибку в данных."""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BootstrapFormMixin
from apps.moderation.models import ReportReason

MAX_REPORT_COMMENT_LENGTH = 2000


class ReportForm(BootstrapFormMixin, forms.Form):
    """Жалоба на объект: причина и необязательное пояснение.

    Цель приходит скрытыми полями — форма стоит на трёх страницах и доступна
    анонимно. Доступность объекта проверяет представление.

    Honeypot-поле `website`: его не видит человек (скрыто стилем), а бот
    заполняет всё подряд. Заполнено — считаем заявку автоматической
    и показываем благодарность, ничего не записывая: отвечать боту отказом
    значит подсказывать, что его раскусили.
    """

    target = forms.CharField(widget=forms.HiddenInput)
    object_id = forms.IntegerField(widget=forms.HiddenInput)
    next = forms.CharField(required=False, widget=forms.HiddenInput)

    reason = forms.ChoiceField(
        label=_("Причина"),
        choices=ReportReason.choices,
        initial=ReportReason.WRONG_DATA,
    )
    comment = forms.CharField(
        label=_("Что не так"),
        required=False,
        max_length=MAX_REPORT_COMMENT_LENGTH,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": _(
                    "Опишите ошибку: что указано неверно и как правильно."
                ),
            }
        ),
    )
    # Поле-ловушка: имя приманки, а не смысловое. Стиль скрывает его
    # от человека, но не от бота — те видят только разметку.
    website = forms.CharField(required=False, label="")

    def clean_comment(self):
        return self.cleaned_data["comment"].strip()

    def is_bot(self) -> bool:
        """Заполнена ли приманка."""
        return bool(self.cleaned_data.get("website"))
