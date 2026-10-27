"""Тесты правил кропа: размер кадра, нормализация и масштаб координат.

Чистые функции без базы: кроп приходит из браузера, и мусор в нём
не должен ронять ни форму, ни воркер. Отдельная группа тестов проверяет
пайплайн: координаты браузера живут в полном размере кадра, а сервер
кроит рабочую копию, поэтому без масштаба кроп уезжает.
"""

import io
import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.photos.forms import PhotoFileForm
from apps.photos.models import Photo
from apps.photos.processing import (
    CROP_VERSION,
    MIN_CROP_SIDE,
    apply_crop,
    normalize_crop_data,
    oriented_size,
    scale_crop_data,
)
from apps.photos.services import process_photo
from tests.utils import (
    QUADRANT_COLORS,
    make_image_with_exif_orientation,
    make_quadrant_image,
    make_upload,
)

VALID_CROP = {
    "version": CROP_VERSION,
    "x": 100,
    "y": 0,
    "width": 100,
    "height": 100,
}

RED = QUADRANT_COLORS["top_left"]
YELLOW = QUADRANT_COLORS["bottom_right"]


def _crop(**overrides):
    """Валидный кроп с заменёнными полями."""
    return {**VALID_CROP, **overrides}


class TestOrientedSize:
    def test_orientation_1_keeps_sides(self):
        image = make_image_with_exif_orientation(1, size=(200, 100))

        assert oriented_size(image) == (200, 100)

    def test_orientation_3_keeps_sides(self):
        """Поворот на 180 стороны не меняет."""
        image = make_image_with_exif_orientation(3, size=(200, 100))

        assert oriented_size(image) == (200, 100)

    @pytest.mark.parametrize("orientation", [5, 6, 7, 8])
    def test_quarter_turn_swaps_sides(self, orientation):
        image = make_image_with_exif_orientation(orientation, size=(200, 100))

        assert oriented_size(image) == (100, 200)

    def test_broken_tag_falls_back_to_file_size(self):
        """Битый `Orientation` — не повод падать на загрузке фото."""
        image = Image.new("RGB", (200, 100))
        image.getexif()[274] = "abc"

        assert oriented_size(image) == (200, 100)

    def test_missing_tag_falls_back_to_file_size(self):
        assert oriented_size(Image.new("RGB", (200, 100))) == (200, 100)


class TestNormalizeCropData:
    def test_valid_crop_is_kept(self):
        result = normalize_crop_data(VALID_CROP)

        assert result == {
            "version": CROP_VERSION,
            "x": 100,
            "y": 0,
            "width": 100,
            "height": 100,
            "rotate": 0,
        }

    def test_empty_data_is_no_crop(self):
        assert normalize_crop_data({}) == {}
        assert normalize_crop_data(None) == {}

    @pytest.mark.parametrize("crop_data", [[], "кроп", 42, [1, 2, 3]])
    def test_not_a_dict_is_no_crop(self, crop_data):
        assert normalize_crop_data(crop_data) == {}

    def test_foreign_version_is_no_crop(self):
        assert normalize_crop_data(_crop(version=2)) == {}

    @pytest.mark.parametrize("field", ["x", "y", "width", "height"])
    def test_missing_coordinate_is_no_crop(self, field):
        crop_data = _crop()
        del crop_data[field]

        assert normalize_crop_data(crop_data) == {}

    def test_not_numeric_coordinate_is_no_crop(self):
        assert normalize_crop_data(_crop(x="abc")) == {}

    def test_floats_from_browser_become_ints(self):
        """Cropper присылает float: в базе координаты — целые пиксели."""
        result = normalize_crop_data(_crop(x=100.6, width=199.4))

        assert result["x"] == 100
        assert result["width"] == 199

    def test_negative_coordinates_are_clamped_to_zero(self):
        result = normalize_crop_data(_crop(x=-50, y=-50, width=200, height=200))

        assert (result["x"], result["y"]) == (0, 0)

    def test_crop_beyond_borders_is_clamped(self):
        result = normalize_crop_data(
            _crop(x=100, y=0, width=500, height=500), size=(200, 200)
        )

        assert (result["x"], result["y"]) == (100, 0)
        assert (result["width"], result["height"]) == (100, 200)

    def test_crop_starting_outside_frame_is_no_crop(self):
        assert normalize_crop_data(_crop(x=500, y=500), size=(200, 200)) == {}

    def test_crop_smaller_than_minimum_is_no_crop(self):
        crop_data = _crop(width=MIN_CROP_SIDE - 1, height=MIN_CROP_SIDE - 1)

        assert normalize_crop_data(crop_data) == {}

    def test_crop_smaller_than_minimum_after_clamping_is_no_crop(self):
        """Рамка внутри кадра, но после клампинга остаётся полоска."""
        crop_data = _crop(x=150, y=0, width=200, height=200)

        assert normalize_crop_data(crop_data, size=(200, 200)) == {}

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(90, 90), (180, 180), (270, 270), (360, 0), (-90, 270), (450, 90)],
    )
    def test_rotate_is_normalized(self, raw, expected):
        assert normalize_crop_data(_crop(rotate=raw))["rotate"] == expected

    @pytest.mark.parametrize("raw", [45, "abc", None, 30])
    def test_unsupported_rotate_falls_back_to_zero(self, raw):
        assert normalize_crop_data(_crop(rotate=raw))["rotate"] == 0

    def test_source_size_is_kept_when_numeric(self):
        result = normalize_crop_data(_crop(source_width=6000, source_height=4000))

        assert result["source_width"] == 6000
        assert result["source_height"] == 4000

    def test_garbage_source_size_is_dropped(self):
        result = normalize_crop_data(_crop(source_width="abc", source_height=None))

        assert "source_width" not in result
        assert "source_height" not in result


class TestScaleCropData:
    def test_crop_is_scaled_to_working_copy(self):
        """6000 px → 2500 px: кроп пересчитывается, а не берётся как есть."""
        crop_data = _crop(x=1200, y=300, width=3600, height=2400, version=1)
        crop_data["source_width"] = 6000
        crop_data["source_height"] = 4000

        result = scale_crop_data(crop_data, 2500 / 6000)

        assert (result["x"], result["y"]) == (500, 125)
        assert (result["width"], result["height"]) == (1500, 1000)

    def test_scale_keeps_rotation(self):
        result = scale_crop_data(_crop(width=400, height=400, rotate=90), 0.5)

        assert result["rotate"] == 90

    def test_crop_is_clamped_after_scaling(self):
        crop_data = _crop(x=0, y=0, width=6000, height=4000)

        result = scale_crop_data(crop_data, 0.5, size=(2500, 2000))

        assert (result["width"], result["height"]) == (2500, 2000)

    def test_crop_below_minimum_after_scaling_is_no_crop(self):
        """Кроп в 150 px полного кадра на рабочей копии — уже мусор."""
        crop_data = _crop(x=5900, y=3900, width=150, height=150)

        assert scale_crop_data(crop_data, 2500 / 6000) == {}

    def test_identity_scale_keeps_coordinates(self):
        result = scale_crop_data(VALID_CROP, 1.0)

        assert (result["x"], result["y"]) == (100, 0)
        assert (result["width"], result["height"]) == (100, 100)

    @pytest.mark.parametrize("factor", [0, -0.5, "abc", None])
    def test_bad_factor_is_no_crop(self, factor):
        assert scale_crop_data(VALID_CROP, factor) == {}

    def test_garbage_is_no_crop(self):
        assert scale_crop_data(_crop(version=2), 0.5) == {}


class TestApplyCropUsesSameRules:
    def test_crop_is_applied(self):
        result = apply_crop(make_quadrant_image(), VALID_CROP)

        assert result.size == (100, 100)

    def test_normalized_data_crops_the_same(self):
        """Форма и сервис пользуются одним правилом: результат совпадает."""
        image = make_quadrant_image()

        result = apply_crop(image, normalize_crop_data(VALID_CROP, size=image.size))

        assert result.size == (100, 100)


def _upload(image) -> SimpleUploadedFile:
    """Собрать файл для `Photo.original` из готовой картинки.

    EXIF переносится в файл явно: `save()` сам тег не пишет, а тест
    с `Orientation` проверяет именно серверную нормализацию.
    """
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95, exif=image.info.get("exif", b""))
    buffer.seek(0)
    return SimpleUploadedFile("crop.jpg", buffer.read(), content_type="image/jpeg")


def _photo(baseball, user, image, crop_data) -> Photo:
    return Photo.objects.create(
        sport=baseball,
        original=_upload(image),
        crop_data=crop_data,
        uploaded_by=user,
    )


def _display_image(photo) -> Image.Image:
    with photo.display.storage.open(photo.display.name, "rb") as handle:
        return Image.open(io.BytesIO(handle.read()))


def _center(image) -> tuple[int, int, int]:
    """Цвет в центре `display`.

    Углы не годятся: JPEG размазывает цвет на границе квадрантов, и угол
    рамки уходит от чистого цвета на десятки единиц.
    """
    width, height = image.size
    return image.convert("RGB").getpixel((width // 2, height // 2))


def _is_color(actual, expected, tolerance=12) -> bool:
    """Сравнить цвета с допуском: JPEG сжимает с потерями."""
    return all(abs(a - b) <= tolerance for a, b in zip(actual, expected, strict=True))


@pytest.mark.django_db
class TestProcessPhotoCrop:
    """Кроп из браузера приходит в полном размере кадра, а сервер кроит
    рабочую копию — без масштаба координаты уезжают на фото больше 2500 px.
    """

    def test_top_left_quadrant_of_large_photo(self, baseball, user):
        photo = _photo(
            baseball,
            user,
            make_quadrant_image(size=(4000, 4000)),
            {
                "version": CROP_VERSION,
                "x": 0,
                "y": 0,
                "width": 2000,
                "height": 2000,
                "source_width": 4000,
                "source_height": 4000,
            },
        )

        result = process_photo(photo)

        display = _display_image(result)
        assert display.size == (1250, 1250)
        assert _is_color(_center(display), RED)

    def test_bottom_right_quadrant_of_large_photo(self, baseball, user):
        photo = _photo(
            baseball,
            user,
            make_quadrant_image(size=(4000, 4000)),
            {
                "version": CROP_VERSION,
                "x": 2000,
                "y": 2000,
                "width": 2000,
                "height": 2000,
                "source_width": 4000,
                "source_height": 4000,
            },
        )

        result = process_photo(photo)

        display = _display_image(result)
        assert _is_color(_center(display), YELLOW)

    def test_scale_one_keeps_previous_result(self, baseball, user):
        """Фото меньше рабочей копии: масштаб 1.0, поведение прежнее."""
        photo = _photo(
            baseball,
            user,
            make_quadrant_image(size=(400, 400)),
            {
                "version": CROP_VERSION,
                "x": 0,
                "y": 0,
                "width": 200,
                "height": 200,
            },
        )

        result = process_photo(photo)

        display = _display_image(result)
        assert display.size == (200, 200)
        assert _is_color(_center(display), RED)

    def test_crop_is_applied_after_exif_transposition(self, baseball, user):
        """Файл 400×200 с `Orientation=6` показывается как 200×400.

        Координаты кропа считаются в системе «как видит человек»: красный
        верхний левый квадрант файла после поворота на 90° оказывается
        справа сверху, и рамка x=100 должна его и вырезать.
        """
        image = make_image_with_exif_orientation(6, size=(400, 200))
        photo = _photo(
            baseball,
            user,
            image,
            {
                "version": CROP_VERSION,
                "x": 100,
                "y": 0,
                "width": 100,
                "height": 200,
                "source_width": 200,
                "source_height": 400,
            },
        )

        result = process_photo(photo)

        display = _display_image(result)
        assert display.size == (100, 200)
        assert _is_color(_center(display), RED)


def _form(user, crop_data=None, files=None):
    """Форма первого шага с `crop_data` из скрытого поля."""
    data = {}
    if crop_data is not None:
        data["crop_data"] = crop_data
    if files is None:
        files = {"original": make_upload(size=(400, 400))}
    return PhotoFileForm(data=data, files=files, user=user)


@pytest.mark.django_db
class TestPhotoFileFormCropData:
    """Скрытое поле шага 1: JSON из браузера или пустая строка.

    Кроп необязателен, поэтому мусор в поле не должен отказывать
    в загрузке: человек уже выбрал файл, а обрезку можно не делать.
    """

    def test_valid_json_is_kept(self, user, baseball):
        form = _form(user, json.dumps(_crop(width=200, height=200)))

        assert form.is_valid(), form.errors
        assert form.cleaned_data["crop_data"]["width"] == 200
        assert form.cleaned_data["crop_data"]["version"] == CROP_VERSION

    def test_broken_json_is_no_crop(self, user, baseball):
        form = _form(user, "{не json")

        assert form.is_valid(), form.errors
        assert form.cleaned_data["crop_data"] == {}

    def test_empty_field_is_no_crop(self, user, baseball):
        form = _form(user, "")

        assert form.is_valid(), form.errors
        assert form.cleaned_data["crop_data"] == {}

    def test_missing_field_is_no_crop(self, user, baseball):
        form = _form(user)

        assert form.is_valid(), form.errors
        assert form.cleaned_data["crop_data"] == {}

    def test_crop_beyond_borders_is_clamped(self, user, baseball):
        """Размер кадра берётся из файла, а не из присланных чисел."""
        form = _form(user, json.dumps(_crop(x=300, y=0, width=500, height=500)))

        assert form.is_valid(), form.errors
        data = form.cleaned_data["crop_data"]
        assert (data["x"], data["y"]) == (300, 0)
        assert (data["width"], data["height"]) == (100, 400)

    def test_crop_smaller_than_minimum_is_no_crop(self, user, baseball):
        form = _form(user, json.dumps(_crop(width=50, height=50)))

        assert form.is_valid(), form.errors
        assert form.cleaned_data["crop_data"] == {}

    def test_crop_without_file_is_ignored(self, user, baseball):
        """Без файла размер кадра неизвестен: координаты не привязать."""
        form = _form(user, json.dumps(_crop(width=200, height=200)), files={})

        assert not form.is_valid()
        assert form.cleaned_data["crop_data"] == {}

    def test_size_is_oriented(self, user, baseball):
        """Файл 600×400 с `Orientation=6` — кадр 400×600.

        Без ориентации клампинг срезал бы высоту до 400: координаты
        браузера живут в системе «как видит человек».
        """
        image = make_image_with_exif_orientation(6, size=(600, 400))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", exif=image.info.get("exif", b""))
        buffer.seek(0)
        upload = SimpleUploadedFile(
            "turn.jpg", buffer.read(), content_type="image/jpeg"
        )

        form = _form(
            user,
            json.dumps(_crop(x=0, y=0, width=400, height=600)),
            files={"original": upload},
        )

        assert form.is_valid(), form.errors
        data = form.cleaned_data["crop_data"]
        assert (data["width"], data["height"]) == (400, 600)


@pytest.mark.django_db
def test_upload_saves_crop_data(client, user, baseball):
    """Кроп доходит до базы: инструкция обработки, а не новый файл."""
    client.force_login(user)
    crop_data = json.dumps(_crop(width=200, height=200))

    client.post(
        "/photos/upload/",
        {"original": make_upload(size=(400, 400)), "crop_data": crop_data},
    )

    photo = Photo.objects.get()
    assert photo.crop_data["width"] == 200
    assert photo.crop_data["height"] == 200
    # Оригинал остаётся целым: кроп — поле, а не перезапись файла.
    assert photo.original.name


@pytest.mark.django_db
def test_hidden_field_is_rendered(client, user, baseball):
    """Поле есть в разметке: без него скрипту некуда положить координаты."""
    client.force_login(user)

    html = client.get("/photos/upload/").content.decode()

    assert 'type="hidden"' in html
    assert 'name="crop_data"' in html


@pytest.mark.django_db
class TestCropOverlayInAdmin:
    """Рамка кропа в админке: модератор видит, что осталось за кадром.

    `display` уже обрезан, и по нему не понять, что вырезали. Оригинал
    в админке показывает браузер, сам применяя EXIF-ориентацию, поэтому
    проценты считаются от транспонированного кадра.
    """

    def test_crop_is_drawn_as_percentages(self, client, admin_user, photo):
        photo.crop_data = {
            "version": CROP_VERSION,
            "x": 1000,
            "y": 500,
            "width": 2000,
            "height": 1000,
            "source_width": 4000,
            "source_height": 2000,
        }
        photo.save(update_fields=["crop_data"])
        client.force_login(admin_user)

        html = client.get(
            reverse("admin:photos_photo_change", args=[photo.pk])
        ).content.decode()

        assert "left:25.0%" in html
        assert "top:25.0%" in html
        assert "width:50.0%" in html
        assert "height:50.0%" in html
        assert "Затемнено то, что осталось за кадром." in html

    def test_photo_without_crop_shows_note(self, client, admin_user, photo):
        photo.crop_data = {}
        photo.save(update_fields=["crop_data"])
        client.force_login(admin_user)

        html = client.get(
            reverse("admin:photos_photo_change", args=[photo.pk])
        ).content.decode()

        assert "Без кропа: показывается весь кадр" in html

    def test_garbage_crop_data_does_not_break_page(self, client, admin_user, photo):
        """Мусор в поле не должен ронять страницу админки."""
        photo.crop_data = {"x": "мусор"}
        photo.save(update_fields=["crop_data"])
        client.force_login(admin_user)

        response = client.get(reverse("admin:photos_photo_change", args=[photo.pk]))

        assert response.status_code == 200
        assert "Без кропа: показывается весь кадр" in response.content.decode()

    def test_mirrored_orientation_adds_note(self, client, admin_user, photo):
        """Отражение по процентам не выразить — говорим об этом прямо."""
        photo.crop_data = {
            "version": CROP_VERSION,
            "x": 0,
            "y": 0,
            "width": 100,
            "height": 100,
            "source_width": 400,
            "source_height": 400,
        }
        photo.exif = {"Orientation": 5}
        photo.save(update_fields=["crop_data", "exif"])
        client.force_login(admin_user)

        html = client.get(
            reverse("admin:photos_photo_change", args=[photo.pk])
        ).content.decode()

        assert "Зеркальная ориентация: рамка может быть неточной." in html

    def test_rotation_is_turned_back_for_the_frame(self, client, admin_user, photo):
        """Кроп хранится в повёрнутой системе, а оригинал показан как есть.

        Кадр 400×200 после поворота на 90° по часовой — 200×400, и кроп
        x=0, y=0, w=100, h=400 занимает его левую половину. Левая сторона
        повёрнутого кадра — это низ исходного, поэтому в системе оригинала
        рамка лежит в нижней половине: x=0, y=100, w=400, h=100.
        """
        photo.crop_data = {
            "version": CROP_VERSION,
            "x": 0,
            "y": 0,
            "width": 100,
            "height": 400,
            "rotate": 90,
            "source_width": 200,
            "source_height": 400,
        }
        photo.save(update_fields=["crop_data"])
        client.force_login(admin_user)

        html = client.get(
            reverse("admin:photos_photo_change", args=[photo.pk])
        ).content.decode()

        assert "left:0.0%" in html
        assert "top:50.0%" in html
        assert "width:100.0%" in html
        assert "height:50.0%" in html
        assert "Кадр повёрнут на 90°." in html

    def test_missing_source_size_shows_note(self, client, admin_user, photo):
        """Старая запись без размера кадра: процентов не из чего считать."""
        photo.crop_data = {
            "version": CROP_VERSION,
            "x": 0,
            "y": 0,
            "width": 100,
            "height": 100,
        }
        photo.save(update_fields=["crop_data"])
        client.force_login(admin_user)

        html = client.get(
            reverse("admin:photos_photo_change", args=[photo.pk])
        ).content.decode()

        assert "Рамка недоступна: в данных кропа нет размера кадра" in html


@pytest.mark.django_db
def test_crop_overlay_on_add_page_does_not_fail(client, admin_user, baseball):
    """Страница добавления: объекта ещё нет, а поле уже рисуется."""
    client.force_login(admin_user)

    response = client.get(reverse("admin:photos_photo_add"))

    assert response.status_code == 200


@pytest.mark.django_db
class TestOriginalIsIntact:
    """Оригинал — неприкосновенен: кроп только инструкция обработки.

    Проверяется байтами, а не размером: перезапись файла «почти тем же»
    уже нарушила бы инвариант № 4, а по размеру такое не увидеть.
    """

    def test_bytes_are_the_same_after_processing(self, baseball, user):
        photo = _photo(
            baseball,
            user,
            make_quadrant_image(size=(400, 400)),
            {
                "version": CROP_VERSION,
                "x": 100,
                "y": 100,
                "width": 200,
                "height": 200,
                "source_width": 400,
                "source_height": 400,
            },
        )
        before = _original_bytes(photo)

        result = process_photo(photo)

        assert _original_bytes(result) == before
        # `display` при этом получился другой: кроп всё-таки применён.
        assert result.width == 200

    def test_display_is_a_separate_file(self, baseball, user):
        photo = _photo(baseball, user, make_quadrant_image(), {})

        result = process_photo(photo)

        assert result.display.name != result.original.name


def _original_bytes(photo) -> bytes:
    """Содержимое файла оригинала в хранилище."""
    with photo.original.storage.open(photo.original.name, "rb") as handle:
        return handle.read()


@pytest.mark.django_db
class TestCropAndUserRotation:
    """Кроп и поворот пользователя работают вместе.

    Порядок фиксирован: EXIF → поворот пользователя → кроп. Координаты
    кропа приходят в системе повёрнутого кадра, поэтому сервер поворачивает
    до того, как резать.
    """

    def test_crop_of_rotated_frame(self, baseball, user):
        """Поворот на 90°: красный квадрант уходит вправо вверх.

        Кроп x=0, y=0, 200×200 в повёрнутой системе — это синий квадрант:
        левый нижний угол исходного кадра встал на его место.
        """
        photo = _photo(
            baseball,
            user,
            make_quadrant_image(size=(400, 400)),
            {
                "version": CROP_VERSION,
                "x": 0,
                "y": 0,
                "width": 200,
                "height": 200,
                "rotate": 90,
                "source_width": 400,
                "source_height": 400,
            },
        )

        result = process_photo(photo)

        display = _display_image(result)
        assert _is_color(_center(display), QUADRANT_COLORS["bottom_left"])

    def test_rotation_without_crop_turns_the_frame(self, baseball, user):
        """Поворот без обрезки: стороны те же, содержимое повёрнуто."""
        photo = _photo(
            baseball,
            user,
            make_quadrant_image(size=(400, 400)),
            {
                "version": CROP_VERSION,
                "x": 0,
                "y": 0,
                "width": 400,
                "height": 400,
                "rotate": 270,
                "source_width": 400,
                "source_height": 400,
            },
        )

        result = process_photo(photo)

        display = _display_image(result)
        assert display.size == (400, 400)
        # Поворот на 270° (или −90°): красный квадрант уходит влево вниз.
        assert _is_color(display.convert("RGB").getpixel((50, 350)), RED)

    def test_exif_orientation_and_user_rotation_do_not_conflict(self, baseball, user):
        """Порядок EXIF → поворот → кроп: две нормализации не складываются.

        Файл 400×200 с `Orientation=6` показывается как 200×400, где красный
        квадрант стоит справа сверху. Поворот пользователя на 90° переносит
        его в правый нижний угол кадра 400×200 — там его и должен взять кроп.
        Если бы сервер кроил до поворота, из этого места вышел бы жёлтый.
        """
        image = make_image_with_exif_orientation(6, size=(400, 200))
        photo = _photo(
            baseball,
            user,
            image,
            {
                "version": CROP_VERSION,
                "x": 200,
                "y": 100,
                "width": 200,
                "height": 100,
                "rotate": 90,
                "source_width": 400,
                "source_height": 200,
            },
        )

        result = process_photo(photo)

        display = _display_image(result)
        assert display.size == (200, 100)
        assert _is_color(_center(display), RED)


@pytest.mark.django_db
def test_form_explains_that_crop_needs_javascript(client, user, baseball):
    """Без JavaScript обрезки нет — об этом должна быть подпись.

    Подпись стоит вне скрытого блока: внутри `hidden`-контейнера
    `<noscript>` не увидел бы никто.
    """
    client.force_login(user)

    html = client.get("/photos/upload/").content.decode()

    assert "<noscript>" in html
    assert "Обрезать и поворачивать кадр можно только с включённым JavaScript" in html
    # Кнопки поворота скрипт показывает сам: без него они крутили бы пустоту.
    assert 'id="photo-crop-tools" hidden' in html
