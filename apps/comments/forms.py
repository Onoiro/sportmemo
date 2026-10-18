"""Форма добавления комментария."""

from django import forms
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.comments.models import Comment
from apps.core.forms import BootstrapFormMixin
from apps.core.models import Status

MAX_COMMENT_LENGTH = 2000


class CommentForm(BootstrapFormMixin, forms.Form):
    """Комментарий или ответ на комментарий.

    Цель приходит скрытыми полями, а не берётся из адреса: форма стоит
    на трёх страницах, и один маршрут `/comments/add/` обслуживает все.
    Разрешение объекта (опубликован, виден пользователю) проверяет
    представление до создания формы — здесь только текст и ответ.

    `parent` необязателен: ответы одноуровневые, и ответ на ответ форма
    поднимает до корневого комментария.
    """

    target = forms.CharField(widget=forms.HiddenInput)
    object_id = forms.IntegerField(widget=forms.HiddenInput)
    parent = forms.IntegerField(required=False, widget=forms.HiddenInput)
    next = forms.CharField(required=False, widget=forms.HiddenInput)

    text = forms.CharField(
        label=_("Комментарий"),
        max_length=MAX_COMMENT_LENGTH,
        # `required=False`: пустое поле проверяет `clean_text` — иначе Django
        # выдал бы своё «Обязательное поле», а нам нужен понятный текст.
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": _("Что известно об этом фото или команде?"),
            }
        ),
    )

    def __init__(self, *args, target_obj=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Цель нужна для проверки `parent`: ответ обязан относиться к тому же
        # объекту, что и сам комментарий.
        self.target_obj = target_obj
        self.user = user
        self.parent_comment = None

    def clean_text(self):
        """Пробельный текст — не текст.

        Иначе « » проходило бы проверку длины и публиковалось пустым.
        """
        text = self.cleaned_data["text"].strip()
        if not text:
            raise forms.ValidationError(_("Напишите текст комментария."))
        return text

    def clean_parent(self):
        """Ответ на комментарий, поднятый до корня.

        Висячий ответ не создаётся: родитель обязан быть видимым — то же
        правило, по которому строится список.
        """
        parent_id = self.cleaned_data.get("parent")
        if not parent_id:
            return None

        query = Q(status=Status.PUBLISHED)
        if self.user is not None and self.user.is_authenticated:
            query |= Q(status=Status.DRAFT, author=self.user)

        parent = (
            Comment.public.filter(pk=parent_id)
            .filter(query)
            .select_related("parent")
            .first()
        )
        if parent is None or not self._belongs_to_target(parent):
            raise forms.ValidationError(
                _("Комментарий, на который вы отвечаете, недоступен.")
            )

        # Ответы одноуровневые: ответ на ответ прикрепляется к корню.
        while parent.parent_id is not None:
            parent = parent.parent
        self.parent_comment = parent
        return parent.pk

    def _belongs_to_target(self, comment) -> bool:
        """Относится ли комментарий к той же цели, что и форма."""
        if self.target_obj is None:
            return False
        content_type = ContentType.objects.get_for_model(self.target_obj)
        return (
            comment.object_id == self.target_obj.pk
            and comment.content_type_id == content_type.pk
        )
