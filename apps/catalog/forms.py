"""Формы каталога: создание и правка команды."""

from django import forms
from django.db.models import Case, Value, When
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Team
from apps.core.forms import BootstrapFormMixin
from apps.core.models import Status
from apps.core.shortcuts import default_sport

# Год основания: раньше бейсбол в России не появился, а будущий год —
# заведомая опечатка.
MIN_FOUNDED_YEAR = 1800
MAX_DESCRIPTION_LENGTH = 2000
MAX_TEAM_LOGO_SIZE = 2 * 1024 * 1024
ALLOWED_LOGO_EXTENSIONS = ("jpg", "jpeg", "png", "webp")

NAME_HELP_TEXT = _(
    "Если у клуба несколько команд, заводите их отдельными записями: "
    "«Русстар», «Русстар-2», «Русстар (юноши 1992 г. р.)». "
    "Основную команду указывайте без уточнения."
)


class TeamForm(BootstrapFormMixin, forms.ModelForm):
    """Создание и правка команды.

    Вид спорта и статус в форму не входят: продукт односпортный, а публикует
    команду модератор. Их ставит представление.

    Название не блокируется жёстко: «Соколы» есть в разных городах, и решение
    «дубликат или однофамилец» принимает человек. При совпадении показываем
    предупреждение и просим подтвердить, что это другая команда; настоящие
    дубликаты сливает модератор.
    """

    confirm_duplicate = forms.BooleanField(
        label=_("Я проверил(а), это другая команда"),
        required=False,
    )

    class Meta:
        model = Team
        fields = ["name", "city", "founded_year", "description", "logo"]
        labels = {
            "name": _("Название"),
            "city": _("Город"),
            "founded_year": _("Год основания"),
            "description": _("Описание"),
            "logo": _("Эмблема"),
        }
        help_texts = {"name": NAME_HELP_TEXT}
        widgets = {
            "name": forms.TextInput(attrs={"autocomplete": "off"}),
            "city": forms.TextInput(attrs={"autocomplete": "address-level2"}),
            "founded_year": forms.NumberInput(attrs={"min": MIN_FOUNDED_YEAR}),
            "description": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, sport=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Вид спорта нужен для поиска дубликатов: одинаковые названия
        # в разных видах спорта — не дубликаты.
        self.sport = sport or getattr(self.instance, "sport", None) or default_sport()
        self.duplicates = self.find_duplicates()

    def _current_name(self) -> str:
        """Название, введённое сейчас: для поиска дубликатов до отправки."""
        if self.is_bound:
            return self.data.get(self.add_prefix("name"), "") or ""
        return self.initial.get("name") or getattr(self.instance, "name", "") or ""

    def find_duplicates(self, name=None):
        """Команды с тем же названием в том же виде спорта, кроме своей.

        Ищем по менеджеру по умолчанию, а не по `public`: черновик другого
        пользователя — тоже повод предупредить, иначе за время модерации
        появятся три одинаковые команды. Мягко удалённые исключаем: модератор
        их уже убрал.
        """
        value = (name if name is not None else self._current_name()).strip()
        if not value or self.sport is None:
            return Team.objects.none()

        queryset = Team.objects.filter(
            sport=self.sport, name__iexact=value, deleted_at__isnull=True
        )
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        # Опубликованные — первыми: на них можно дать ссылку, а чужой черновик
        # показываем только названием и статусом.
        return queryset.order_by(
            Case(
                When(status=Status.PUBLISHED, then=Value(0)),
                default=Value(1),
            ),
            "name",
        )

    def clean_founded_year(self):
        year = self.cleaned_data["founded_year"]
        if year is None:
            return year
        current_year = timezone.localdate().year
        if year < MIN_FOUNDED_YEAR or year > current_year:
            raise forms.ValidationError(
                _("Год основания должен быть между %(min)s и %(max)s.")
                % {"min": MIN_FOUNDED_YEAR, "max": current_year}
            )
        return year

    def clean_description(self):
        description = self.cleaned_data["description"]
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise forms.ValidationError(
                _("Описание не должно быть длиннее %(limit)s символов.")
                % {"limit": MAX_DESCRIPTION_LENGTH}
            )
        return description

    def clean_logo(self):
        """Проверяем размер и расширение.

        Содержимое файла не разбираем: настоящая обработка изображений
        появится вместе с общим пайплайном (этап 3).
        """
        logo = self.cleaned_data["logo"]
        if not logo or not hasattr(logo, "size"):
            return logo
        if logo.size > MAX_TEAM_LOGO_SIZE:
            raise forms.ValidationError(
                _("Эмблема не должна быть больше %(limit)s МБ.")
                % {"limit": MAX_TEAM_LOGO_SIZE // (1024 * 1024)}
            )
        extension = logo.name.rsplit(".", 1)[-1].lower() if "." in logo.name else ""
        if extension not in ALLOWED_LOGO_EXTENSIONS:
            raise forms.ValidationError(
                _("Поддерживаются форматы: %(formats)s.")
                % {"formats": ", ".join(ALLOWED_LOGO_EXTENSIONS)}
            )
        return logo

    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get("name")
        if not name:
            return cleaned_data

        self.duplicates = self.find_duplicates(name)
        if self.duplicates and not cleaned_data.get("confirm_duplicate"):
            self.add_error(
                "name",
                _(
                    "Команда с таким названием уже есть в архиве. "
                    "Проверьте список выше."
                ),
            )
        return cleaned_data
