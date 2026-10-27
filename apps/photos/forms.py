"""Формы загрузки фото: файл отдельно, данные о снимке отдельно.

Две формы вместо одной — решение P3-16. Выбранный файл не переживает
переход на другую страницу, а данные о снимке переживают. Пока обе роли
играла одна форма, ошибка в названии команды или уход на её создание
теряли снимок, и человек выбирал его заново.
"""

import io
import json
import logging
import warnings

from django import forms
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from PIL import Image, UnidentifiedImageError

from apps.catalog.models import Event, Team
from apps.core.forms import BootstrapFormMixin
from apps.core.models import Status
from apps.photos import services
from apps.photos.exif import extract_exif
from apps.photos.models import Photo
from apps.photos.processing import (
    ALLOWED_UPLOAD_FORMATS,
    MAX_SIDE_PX,
    MAX_UPLOAD_BYTES,
    MIN_SIDE_PX,
    normalize_crop_data,
    oriented_size,
)

logger = logging.getLogger(__name__)

# Раньше бейсбола в России не было, а будущий год — заведомая опечатка.
MIN_PHOTO_YEAR = 1850


class PhotoFileForm(BootstrapFormMixin, forms.Form):
    """Первый шаг загрузки: только файл.

    Файл проверяется здесь целиком — размер, формат, стороны, пиксели,
    декодирование, суточный лимит, — и сразу сохраняется. Данные о снимке
    заполняются следующим шагом, поэтому ошибка в названии команды больше
    не отнимает выбранный файл.

    Не `ModelForm`: объект собирает представление, и статус с автором туда
    нельзя подставить через POST. Штатный `ImageField` тоже не подходит —
    он проверяет файл через `Image.verify()` и прячет причину отказа
    в общее «некорректное изображение», а человеку нужно знать, что именно
    не так.
    """

    original = forms.FileField(
        label=_("Фото"),
        widget=forms.ClearableFileInput(
            attrs={"accept": "image/jpeg,image/png,image/webp,image/heic,image/heif"}
        ),
    )
    # Кроп приходит из браузера: рамка и поворот собираются в JSON на
    # отправке формы. Поле скрытое и необязательное — без JavaScript его
    # просто нет, и фото загружается целиком.
    crop_data = forms.CharField(
        label=_("Обрезка кадра"),
        required=False,
        widget=forms.HiddenInput,
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        # Размер кадра «как видит человек» заполняет `clean_original`:
        # координаты кропа приходят в этой системе, и без размера их
        # не к чему клампить.
        self._source_size = None

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
                # Размер «как видит человек» нужен разбору `crop_data`:
                # координаты браузера живут в этой системе. Считаем здесь,
                # пока файл открыт, — второе открытие стоило бы столько же,
                # сколько вся проверка.
                self._source_size = oriented_size(image)
                # EXIF читается здесь же: файл уже открыт и декодирован,
                # и второе открытие ради одного поля стоило бы столько же,
                # сколько вся проверка. В базу метаданные попадут при
                # сохранении фото, но год из них — только подсказка.
                self.cleaned_data["exif"] = extract_exif(image)
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
        cleaned_data["crop_data"] = self._clean_crop_data(cleaned_data)
        return cleaned_data

    def _clean_crop_data(self, cleaned_data):
        """Разобрать скрытое поле: JSON из браузера или пустая строка.

        Битый JSON — не повод отказывать в загрузке: кроп необязателен,
        а фото человек уже выбрал. Правила «что считается кропом» живут
        в `normalize_crop_data()` — второй набор в форме разошёлся бы
        с сервисом.
        """
        raw = (cleaned_data.get("crop_data") or "").strip()
        if not raw:
            return {}
        # Файл не прошёл проверку — размер кадра неизвестен, а без него
        # координаты не к чему привязать.
        if "original" not in cleaned_data:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            logger.warning("crop_data не разбирается как JSON, кроп пропущен")
            return {}
        return normalize_crop_data(data, size=self._source_size)

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


class PhotoDetailsForm(BootstrapFormMixin, forms.ModelForm):
    """Второй шаг: что человек знает о снимке.

    Одна форма служит и вторым шагом, и правкой: два маршрута с одинаковой
    формой разошлись бы в правилах доступа. Все поля необязательные —
    черновик нельзя блокировать, человек вернётся и дополнит позже.

    Источник (`Source`) в форму не входит: его заводит модератор, а выбор
    из списка источников в форме из одного поля стал бы справочником.

    Команда и событие вводятся текстом: подсказку даёт нативное `<datalist>`,
    а сервер резолвит введённое в объект. Без JavaScript поле работает как
    обычный текст.
    """

    team_name = forms.CharField(
        label=_("Команда"),
        max_length=300,
        required=False,
        widget=forms.TextInput(attrs={"list": "team-options", "autocomplete": "off"}),
        help_text=_(
            "Начните вводить — появятся команды из архива. "
            "Если нужной нет, создайте её по ссылке ниже."
        ),
    )
    event_name = forms.CharField(
        label=_("Событие"),
        max_length=300,
        required=False,
        widget=forms.TextInput(attrs={"list": "event-options", "autocomplete": "off"}),
        help_text=_("Начните вводить — появятся события из архива."),
    )

    class Meta:
        model = Photo
        # `team` и `event` сюда не входят: они приходят названием и ставятся
        # в объект в `save()` после резолва.
        fields = ["title", "caption", "year", "taken_date"]
        labels = {
            "title": _("Заголовок"),
            "caption": _("Подпись"),
            "year": _("Год"),
            "taken_date": _("Дата съёмки"),
        }
        help_texts = {
            "title": _("Одна строка о снимке. Можно оставить пустым."),
            "caption": _("Кто на фото, что происходит, чем снимок важен."),
            "year": _("Если знаете. Год из файла — только подсказка."),
        }
        widgets = {
            "caption": forms.Textarea(attrs={"rows": 4}),
            "taken_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        # Названия, которые не нашлись в архиве: шаблон покажет рядом
        # с ошибкой ссылку «создать с таким названием». В текст ошибки
        # ссылку не вставить — он экранируется.
        self.unresolved = {}
        if self.instance.pk and not self.is_bound:
            # Форма правки показывает текущие привязки: пустое поле
            # заставило бы вводить название заново, а человек, стерев его
            # не нарочно, отвязал бы фото от команды.
            if self.instance.team_id:
                self.initial.setdefault("team_name", self.instance.team.full_name)
            if self.instance.event_id:
                self.initial.setdefault("event_name", str(self.instance.event))

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

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data["team"] = self._resolve(
            Team, "team_name", cleaned_data, lambda obj: obj.full_name
        )
        cleaned_data["event"] = self._resolve(Event, "event_name", cleaned_data, str)
        self._check_year(cleaned_data)
        return cleaned_data

    def save(self, commit=True):
        """Сохранить поля и привязки, введённые названием.

        `team` и `event` не входят в `Meta.fields`, поэтому в объект они
        попадают только здесь. Пустое поле отвязывает фото: форма показывает
        текущее название, и стереть его — осознанное действие.
        """
        photo = super().save(commit=False)
        photo.team = self.cleaned_data.get("team")
        photo.event = self.cleaned_data.get("event")
        if commit:
            photo.save()
        return photo

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
            # Шаблон покажет рядом ссылку «создать с таким названием»:
            # без неё человек не знает, что делать дальше.
            self.unresolved[field_name] = value
        return resolved

    def _queryset(self, model):
        """Что видно при резолве: опубликованное, своё и текущая привязка.

        Свой черновик нужен: команду заводят прямо перед загрузкой фото
        в неё, и модератор ещё не успел её опубликовать. Текущая привязка
        входит всегда: правку не должно блокировать название, которое
        человек не менял, иначе модератор не сохранил бы чужой черновик.
        """
        sport = getattr(self.instance, "sport", None)
        if sport is None:
            return model.objects.none()
        visible = Q(status=Status.PUBLISHED)
        if self.user is not None and self.user.is_authenticated:
            visible |= Q(created_by=self.user)
        current = self.instance.team_id if model is Team else self.instance.event_id
        if current:
            visible |= Q(pk=current)
        return model.objects.filter(sport=sport, deleted_at__isnull=True).filter(
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
