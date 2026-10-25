"""Формы каталога: создание и правка команды и события."""

from django import forms
from django.db.models import Case, Value, When
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Event, Team
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

EVENT_NAME_HELP_TEXT = _(
    "Пишите так, как событие называют в архиве: «Кубок России», "
    "«Первенство Москвы». Один и тот же турнир разных лет — отдельные "
    "записи: название совпадает, год разный."
)

EVENT_YEAR_HELP_TEXT = _("Если знаете. Из даты начала год подставится сам.")


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


class EventForm(BootstrapFormMixin, forms.ModelForm):
    """Создание и правка события.

    Вид спорта и статус в форму не входят: продукт односпортный, а публикует
    событие модератор. Их ставит представление.

    Год — отдельное поле, а не только производная от дат: чаще всего известен
    именно год («Кубок России 1994»), а числа и месяцы — нет. Если год пуст,
    `Event.save()` подставит его из даты начала.

    Дубликатом считается событие с тем же названием **и тем же годом**:
    «Кубок России» повторяется каждый год, и это нормальные разные записи.
    """

    confirm_duplicate = forms.BooleanField(
        label=_("Я проверил(а), это другое событие"),
        required=False,
    )

    class Meta:
        model = Event
        fields = [
            "name",
            "event_type",
            "date_from",
            "date_to",
            "year",
            "city",
            "country",
            "description",
        ]
        labels = {
            "name": _("Название"),
            "event_type": _("Тип события"),
            "date_from": _("Дата начала"),
            "date_to": _("Дата окончания"),
            "year": _("Год"),
            "city": _("Город"),
            "country": _("Страна"),
            "description": _("Описание"),
        }
        help_texts = {
            "name": EVENT_NAME_HELP_TEXT,
            "year": EVENT_YEAR_HELP_TEXT,
        }
        widgets = {
            "name": forms.TextInput(attrs={"autocomplete": "off"}),
            "date_from": forms.DateInput(attrs={"type": "date"}),
            "date_to": forms.DateInput(attrs={"type": "date"}),
            "year": forms.NumberInput(attrs={"min": MIN_FOUNDED_YEAR}),
            "city": forms.TextInput(attrs={"autocomplete": "address-level2"}),
            "country": forms.TextInput(attrs={"autocomplete": "country-name"}),
            "description": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, sport=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.sport = sport or getattr(self.instance, "sport", None) or default_sport()
        self.duplicates = self.find_duplicates()

    def _current_name(self) -> str:
        """Название, введённое сейчас: для поиска дубликатов до отправки."""
        if self.is_bound:
            return self.data.get(self.add_prefix("name"), "") or ""
        return self.initial.get("name") or getattr(self.instance, "name", "") or ""

    def _current_year(self):
        """Год, введённый сейчас, или `None`."""
        value = (
            self.data.get(self.add_prefix("year"))
            if self.is_bound
            else self.initial.get("year") or getattr(self.instance, "year", None)
        )
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def find_duplicates(self, name=None, year=None):
        """События с тем же названием и годом, кроме своего.

        Менеджер по умолчанию, а не `public`: черновик другого автора — тоже
        повод предупредить, иначе за время модерации появятся две одинаковые
        записи. Мягко удалённые исключаем: модератор их уже убрал.
        """
        value = (name if name is not None else self._current_name()).strip()
        if not value or self.sport is None:
            return Event.objects.none()

        event_year = year if year is not None else self._current_year()
        queryset = Event.objects.filter(
            sport=self.sport, name__iexact=value, deleted_at__isnull=True
        )
        # Год — часть «того же события»: без него «Кубок России» за все годы
        # выглядел бы дубликатом самого себя.
        if event_year is None:
            queryset = queryset.filter(year__isnull=True)
        else:
            queryset = queryset.filter(year=event_year)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        return queryset.order_by(
            Case(
                When(status=Status.PUBLISHED, then=Value(0)),
                default=Value(1),
            ),
            "-year",
        )

    def clean_year(self):
        year = self.cleaned_data["year"]
        if year is None:
            return year
        current_year = timezone.localdate().year
        if year < MIN_FOUNDED_YEAR or year > current_year:
            raise forms.ValidationError(
                _("Год должен быть между %(min)s и %(max)s.")
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

    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get("name")
        if not name:
            return cleaned_data

        # Год мог прийти только из даты начала — модель подставит его
        # на сохранении, а дубликаты ищем уже с ним.
        year = cleaned_data.get("year") or self._year_from_dates(cleaned_data)
        self.duplicates = self.find_duplicates(name, year)
        if self.duplicates and not cleaned_data.get("confirm_duplicate"):
            self.add_error(
                "name",
                _(
                    "Событие с таким названием и годом уже есть в архиве. "
                    "Проверьте список выше."
                ),
            )
        return cleaned_data

    @staticmethod
    def _year_from_dates(cleaned_data):
        """Год из даты начала: то же правило, что в `Event.save()`."""
        value = cleaned_data.get("date_from") or cleaned_data.get("date_to")
        return value.year if value else None
