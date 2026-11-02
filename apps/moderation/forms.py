"""Формы модерации: жалоба на ошибку в данных и причина отклонения."""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BootstrapFormMixin
from apps.moderation.models import ReportReason

MAX_REPORT_COMMENT_LENGTH = 2000


class ModerationRejectForm(forms.Form):
    """Причина отклонения: одна на всю выборку объектов.

    Одна причина, а не поле у каждого объекта: отклоняют обычно по общей
    причине («не тот год», «дубль»), и автору нужен текст, а не отметка.

    Bootstrap-классы не навешиваем: страница живёт в админке, и её поля
    оформляет сама админка — `form-control` там смотрелся бы чужим.
    """

    reason = forms.CharField(
        label=_("Причина отклонения"),
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean_reason(self):
        """Пробельная причина — та же пустая: автор увидел бы пустоту."""
        value = self.cleaned_data["reason"].strip()
        if not value:
            raise forms.ValidationError(_("Опишите причину: автор её увидит."))
        return value


class QueueRejectForm(BootstrapFormMixin, ModerationRejectForm):
    """Та же причина отклонения, но с оформлением публичной страницы.

    Правило «причина обязательна и обрезается» живёт в
    `ModerationRejectForm.clean_reason`: второй экземпляр разошёлся бы
    с админкой при первой же правке. Здесь добавлены только классы полей.
    """


class ReportResolutionForm(BootstrapFormMixin, forms.Form):
    """Текст решения по жалобе: один на оба исхода.

    Одна форма с двумя кнопками (`resolve` и `reject`) вместо двух форм:
    текст решения одинаков, а две формы с одним полем дали бы дублирующиеся
    `id` и вторую копию правила «текст обязателен».

    Пробельный текст считается пустым: заявителю нечего было бы прочитать.
    Сервис проверяет то же, но форма отвечает раньше и человеческим текстом.
    """

    resolution = forms.CharField(
        label=_("Решение"),
        max_length=MAX_REPORT_COMMENT_LENGTH,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean_resolution(self):
        """Пробельный текст — та же пустота."""
        value = self.cleaned_data["resolution"].strip()
        if not value:
            raise forms.ValidationError(_("Текст решения обязателен"))
        return value


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
