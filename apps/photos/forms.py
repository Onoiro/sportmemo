"""Форма загрузки фотографии: проверка файла и разбор полей."""

import io
import warnings

from django import forms
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from PIL import Image, UnidentifiedImageError

from apps.catalog.models import Event, Team
from apps.core.forms import BootstrapFormMixin
from apps.core.models import Status
from apps.core.shortcuts import default_sport
from apps.photos import services
from apps.photos.processing import (
    ALLOWED_UPLOAD_FORMATS,
    MAX_SIDE_PX,
    MAX_UPLOAD_BYTES,
    MIN_SIDE_PX,
)

# Раньше бейсбола в России не было, а будущий год — заведомая опечатка.
MIN_PHOTO_YEAR = 1850


class PhotoUploadForm(BootstrapFormMixin, forms.Form):
    """Загрузка фото: файл и то, что человек знает о снимке.

    Не `ModelForm`: объект собирает представление, и статус с автором туда
    нельзя подставить через POST. Штатный `ImageField` тоже не подходит —
    он проверяет файл через `Image.verify()` и прячет причину отказа
    в общее «некорректное изображение», а человеку нужно знать, что именно
    не так.

    Источник (`Source`) в форму не входит: его заводит модератор, а выбор
    из списка источников в форме из одного поля стал бы справочником.

    Команда и событие вводятся текстом: подсказку даёт нативное `<datalist>`,
    а сервер резолвит введённое в объект. Без JavaScript поле работает как
    обычный текст.
    """

    original = forms.FileField(
        label=_("Фото"),
        widget=forms.ClearableFileInput(
            attrs={"accept": "image/jpeg,image/png,image/webp,image/heic,image/heif"}
        ),
    )
    title = forms.CharField(
        label=_("Заголовок"),
        max_length=300,
        required=False,
        help_text=_("Одна строка о снимке. Можно оставить пустым."),
    )
    caption = forms.CharField(
        label=_("Подпись"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_("Кто на фото, что происходит, чем снимок важен."),
    )
    year = forms.IntegerField(
        label=_("Год"),
        required=False,
        help_text=_("Если знаете. Год из файла — только подсказка."),
    )
    taken_date = forms.DateField(
        label=_("Дата съёмки"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    team_name = forms.CharField(
        label=_("Команда"),
        max_length=300,
        required=False,
        widget=forms.TextInput(attrs={"list": "team-options", "autocomplete": "off"}),
    )
    event_name = forms.CharField(
        label=_("Событие"),
        max_length=300,
        required=False,
        widget=forms.TextInput(attrs={"list": "event-options", "autocomplete": "off"}),
    )

    def __init__(self, *args, user=None, sport=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        # Вид спорта сужает резолв: продукт односпортный, но схема
        # мультиспортная, и «Соколы» из другого вида спорта — не наша команда.
        self.sport = sport if sport is not None else default_sport()

    @property
    def team_options(self):
        """Подсказки `<datalist>` для команды.

        Список строится из той же выборки, что и `clean`: подсказка чужого
        черновика привела бы человека к ошибке «не удалось найти».
        """
        return [team.full_name for team in self._queryset(Team)]

    @property
    def event_options(self):
        """Подсказки `<datalist>` для события."""
        return [str(event) for event in self._queryset(Event)]

    def clean_original(self):
        """Проверить файл: размер, формат, стороны, пиксели, декодирование.

        Формат определяется по содержимому, а не по расширению: переименованный
        `.pdf` не должен пройти, а HEIC без расширения — пройти.
        """
        upload = self.cleaned_data["original"]
        if upload.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                _("Файл больше %(limit)s МБ.")
                % {"limit": MAX_UPLOAD_BYTES // (1024 * 1024)}
            )

        # Pillow закрывает переданный ему файловый объект вместе
        # с изображением, а после проверки файл ещё нужно сохранить:
        # разбираем копию в памяти.
        buffer = io.BytesIO(upload.read())
        try:
            image = self._open_image(buffer)
            try:
                self._check_format(image)
                self._check_side(image)
                self._decode(image)
            finally:
                image.close()
        finally:
            # `Image.open` и `load()` читают файл до конца: без перемотки
            # сохранение получило бы пустой файл.
            upload.seek(0)
        return upload

    def clean(self):
        cleaned_data = super().clean()
        self._check_daily_limit()
        cleaned_data["team"] = self._resolve(
            Team, "team_name", cleaned_data, lambda obj: obj.full_name
        )
        cleaned_data["event"] = self._resolve(Event, "event_name", cleaned_data, str)
        self._check_year(cleaned_data)
        return cleaned_data

    @staticmethod
    def _open_image(buffer):
        """Открыть файл, превратив предупреждение Pillow в отказ формы.

        `Image.MAX_IMAGE_PIXELS` (60 Мпикс) даёт `DecompressionBombWarning`,
        а ошибку Pillow бросает лишь при двойном превышении. Предупреждение
        поэтому тоже должно остановить загрузку: иначе файл на 100 Мпикс
        прошёл бы проверку и упал по памяти уже в обработке.
        """
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                return Image.open(buffer)
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise forms.ValidationError(_pixels_message()) from None
        except UnidentifiedImageError:
            raise forms.ValidationError(
                _(
                    "Не удалось прочитать изображение. "
                    "Подходят файлы JPEG, PNG, WebP и HEIC."
                )
            ) from None
        except OSError:
            raise forms.ValidationError(
                _("Не удалось прочитать изображение: файл повреждён.")
            ) from None

    @staticmethod
    def _check_format(image):
        image_format = (image.format or "").upper()
        if image_format not in ALLOWED_UPLOAD_FORMATS:
            raise forms.ValidationError(
                _("Поддерживаются форматы: %(formats)s.")
                % {"formats": ", ".join(ALLOWED_UPLOAD_FORMATS)}
            )

    @staticmethod
    def _check_side(image):
        width, height = image.size
        if min(width, height) < MIN_SIDE_PX:
            raise forms.ValidationError(
                _(
                    "Слишком маленькое изображение: %(width)s×%(height)s px, "
                    "меньшая сторона должна быть не меньше %(min)s px."
                )
                % {"width": width, "height": height, "min": MIN_SIDE_PX}
            )
        if max(width, height) > MAX_SIDE_PX:
            raise forms.ValidationError(
                _(
                    "Слишком большое изображение: %(width)s×%(height)s px, "
                    "большая сторона может быть до %(max)s px."
                )
                % {"width": width, "height": height, "max": MAX_SIDE_PX}
            )

    @staticmethod
    def _decode(image):
        """Прочитать пиксели целиком: у обрезанного файла заголовок цел.

        Без `load()` файл, повреждённый после заголовка, прошёл бы форму
        и упал бы в обработке уже после сохранения.
        """
        try:
            image.load()
        except (OSError, ValueError):
            raise forms.ValidationError(
                _("Файл повреждён: изображение не удалось прочитать целиком.")
            ) from None

    def _check_daily_limit(self):
        """Лимит суток на автора, а не на файл: квота принадлежит человеку."""
        if self.user is None or not self.user.is_authenticated:
            return
        if services.upload_limit_reached(self.user):
            raise forms.ValidationError(
                _(
                    "За сутки можно загрузить не больше %(limit)s фотографий. "
                    "Попробуйте позже."
                )
                % {"limit": services.DAILY_UPLOAD_LIMIT}
            )

    def _resolve(self, model, field_name, cleaned_data, label):
        """Резолв названия в объект с понятной ошибкой при промахе.

        Молча потерять привязку нельзя: человек решит, что фото ушло
        в команду. Неоднозначное название просит уточнить город, а не
        угадывается — фото не на той команде хуже пустого поля.
        """
        value = cleaned_data.get(field_name)
        if not value:
            return None
        resolved = services.resolve_by_name(model, value, self._queryset(model), label)
        if resolved is None:
            message = _("Не удалось найти «%(name)s». Проверьте название.")
            self.add_error(field_name, message % {"name": value})
        return resolved

    def _queryset(self, model):
        """Что видно при резолве: опубликованное и своё.

        Свой черновик нужен: команду заводят прямо перед загрузкой фото
        в неё, и модератор ещё не успел её опубликовать.
        """
        if self.sport is None:
            return model.objects.none()
        visible = Q(status=Status.PUBLISHED)
        if self.user is not None and self.user.is_authenticated:
            visible |= Q(created_by=self.user)
        return model.objects.filter(sport=self.sport, deleted_at__isnull=True).filter(
            visible
        )

    def _check_year(self, cleaned_data):
        """Год в пределах истории спорта и согласован с датой съёмки."""
        year = cleaned_data.get("year")
        taken_date = cleaned_data.get("taken_date")
        if year is None:
            return
        current_year = timezone.localdate().year
        if year < MIN_PHOTO_YEAR or year > current_year:
            self.add_error(
                "year",
                _("Год должен быть между %(min)s и %(max)s.")
                % {"min": MIN_PHOTO_YEAR, "max": current_year},
            )
            return
        if taken_date is not None and year != taken_date.year:
            self.add_error(
                "taken_date",
                _("Дата съёмки не совпадает с годом %(year)s.") % {"year": year},
            )


def _pixels_message():
    """Сообщение о превышении числа пикселей.

    Лимит берётся у Pillow, а не из своей константы: проверку делает он,
    и два числа разошлись бы при смене настройки.
    """
    megapixels = (Image.MAX_IMAGE_PIXELS or 0) // 1_000_000
    return _("Изображение слишком большое: больше %(mpix)s Мпикс.") % {
        "mpix": megapixels
    }
