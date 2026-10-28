"""Тесты чистых функций обработки: без базы, только PIL и numpy."""

import io

import pytest
from PIL import Image

from apps.photos.processing import (
    CROP_VERSION,
    DISPLAY_MAX_SIDE,
    MIN_CROP_SIDE,
    PHASH_HEX_LENGTH,
    apply_crop,
    apply_rotation,
    encode_display,
    fit_max_side,
    hamming_distance,
    image_info,
    make_display,
    normalize_orientation,
    perceptual_hash,
)
from tests.utils import (
    QUADRANT_COLORS,
    make_image_with_exif_orientation,
    make_quadrant_image,
)

RED = QUADRANT_COLORS["top_left"]
GREEN = QUADRANT_COLORS["top_right"]
BLUE = QUADRANT_COLORS["bottom_left"]
YELLOW = QUADRANT_COLORS["bottom_right"]


def corners(image):
    """Цвета четырёх углов: по ним видно, что именно вырезано."""
    width, height = image.size
    return (
        image.getpixel((0, 0)),
        image.getpixel((width - 1, 0)),
        image.getpixel((0, height - 1)),
        image.getpixel((width - 1, height - 1)),
    )


class TestNormalizeOrientation:
    def test_orientation_6_rotates_and_swaps_sides(self):
        """Телефон записал «снимал боком»: 200×100 должно стать 100×200."""
        image = make_image_with_exif_orientation(6, size=(200, 100))

        result = normalize_orientation(image)

        assert result.size == (100, 200)

    def test_orientation_3_rotates_180(self):
        image = make_image_with_exif_orientation(3, size=(200, 100))

        result = normalize_orientation(image)

        assert result.size == (200, 100)
        # При повороте на 180 верхний левый угол становится жёлтым.
        assert result.getpixel((0, 0)) == YELLOW

    def test_image_without_orientation_is_unchanged(self):
        image = make_quadrant_image()

        result = normalize_orientation(image)

        assert result.size == image.size
        assert result.getpixel((0, 0)) == RED


class TestApplyRotation:
    def test_90_moves_top_left_to_top_right(self):
        result = apply_rotation(make_quadrant_image(), 90)

        assert result.size == (200, 200)
        assert result.getpixel((0, 0)) == BLUE
        assert result.getpixel((199, 0)) == RED

    def test_180_is_double_turn(self):
        result = apply_rotation(make_quadrant_image(), 180)

        assert result.getpixel((0, 0)) == YELLOW
        assert result.getpixel((199, 199)) == RED

    def test_270_equals_minus_90(self):
        result = apply_rotation(make_quadrant_image(), 270)

        assert result.getpixel((0, 0)) == GREEN
        assert result.getpixel((199, 199)) == BLUE

    def test_zero_does_nothing(self):
        result = apply_rotation(make_quadrant_image(), 0)

        assert result.getpixel((0, 0)) == RED

    @pytest.mark.parametrize("degrees", [45, 30, "abc", None])
    def test_unsupported_angle_is_ignored(self, degrees):
        """Произвольный угол оставляет пустые треугольники: не поворачиваем."""
        result = apply_rotation(make_quadrant_image(), degrees)

        assert result.size == (200, 200)
        assert result.getpixel((0, 0)) == RED


class TestApplyCrop:
    def test_crop_takes_expected_quadrant(self):
        crop = {
            "version": CROP_VERSION,
            "x": 100,
            "y": 0,
            "width": 100,
            "height": 100,
        }

        result = apply_crop(make_quadrant_image(), crop)

        assert result.size == (100, 100)
        assert set(corners(result)) == {GREEN}

    def test_empty_crop_returns_same_image(self):
        image = make_quadrant_image()

        assert apply_crop(image, {}) is image

    def test_crop_is_clamped_to_borders(self):
        crop = {
            "version": CROP_VERSION,
            "x": -50,
            "y": -50,
            "width": 300,
            "height": 300,
        }

        result = apply_crop(make_quadrant_image(), crop)

        assert result.size == (200, 200)

    @pytest.mark.parametrize(
        "crop_data",
        [
            None,
            [],
            "кроп",
            {"version": 2, "x": 0, "y": 0, "width": 100, "height": 100},
            {"version": CROP_VERSION, "x": 0, "y": 0},
            {"version": CROP_VERSION, "x": "abc", "y": 0, "width": 100, "height": 100},
        ],
    )
    def test_garbage_is_treated_as_no_crop(self, crop_data):
        """Данные приходят из браузера: сервис обязан не падать."""
        image = make_quadrant_image()

        result = apply_crop(image, crop_data)

        assert result.size == image.size

    def test_too_small_crop_is_treated_as_no_crop(self):
        crop = {
            "version": CROP_VERSION,
            "x": 0,
            "y": 0,
            "width": MIN_CROP_SIDE - 1,
            "height": MIN_CROP_SIDE - 1,
        }

        result = apply_crop(make_quadrant_image(), crop)

        assert result.size == (200, 200)

    def test_crop_after_rotation_uses_user_coordinates(self):
        """Координаты приходят в системе «как видит пользователь»:
        после поворота тот же участок даёт другой цвет.
        """
        rotated = apply_rotation(make_quadrant_image(), 90)
        crop = {
            "version": CROP_VERSION,
            "x": 100,
            "y": 0,
            "width": 100,
            "height": 100,
        }

        result = apply_crop(rotated, crop)

        assert set(corners(result)) == {RED}


class TestFitMaxSide:
    def test_large_image_is_shrunk(self):
        image = Image.new("RGB", (5000, 1000), "red")

        result = fit_max_side(image, 2500)

        assert result.size == (2500, 500)

    def test_small_image_is_not_upscaled(self):
        image = Image.new("RGB", (300, 200), "red")

        result = fit_max_side(image, 2500)

        assert result.size == (300, 200)


class TestMakeDisplay:
    def test_long_side_is_limited(self):
        image = Image.new("RGB", (4000, 2000), "red")

        result = make_display(image)

        assert max(result.size) == DISPLAY_MAX_SIDE
        assert result.mode == "RGB"

    def test_small_image_keeps_size(self):
        image = Image.new("RGB", (800, 600), "red")

        assert make_display(image).size == (800, 600)

    def test_transparent_png_becomes_rgb(self):
        image = Image.new("RGBA", (500, 500), (255, 0, 0, 128))

        result = make_display(image)

        assert result.mode == "RGB"


class TestEncodeDisplay:
    def test_output_is_jpeg_without_exif(self):
        image = make_image_with_exif_orientation(6, size=(400, 200))
        display = make_display(normalize_orientation(image))

        payload = encode_display(display)

        reopened = Image.open(io.BytesIO(payload))
        assert reopened.format == "JPEG"
        assert dict(reopened.getexif()) == {}

    def test_image_info_matches_display(self):
        display = make_display(Image.new("RGB", (4000, 2000), "red"))

        assert image_info(display) == {"width": DISPLAY_MAX_SIDE, "height": 640}


def _noise(seed, size=(256, 256)):
    """Псевдослучайная картинка: у разных seed — разный узор."""
    import numpy as np

    rng = np.random.default_rng(seed)
    data = rng.integers(0, 256, size=(size[1], size[0], 3), dtype=np.uint8)
    return Image.fromarray(data, mode="RGB")


class TestPerceptualHash:
    def test_hash_is_hex_of_64_chars(self):
        result = perceptual_hash(make_quadrant_image())

        assert len(result) == PHASH_HEX_LENGTH
        int(result, 16)

    def test_same_image_in_other_scale_is_close(self):
        image = _noise(1)
        scaled = image.resize((1000, 1000), Image.Resampling.LANCZOS)

        distance = hamming_distance(perceptual_hash(image), perceptual_hash(scaled))

        assert distance <= 32

    def test_same_image_in_other_quality_is_close(self):
        image = _noise(2)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=35)
        buffer.seek(0)
        degraded = Image.open(buffer)

        distance = hamming_distance(perceptual_hash(image), perceptual_hash(degraded))

        assert distance <= 48

    def test_different_images_are_far(self):
        distance = hamming_distance(
            perceptual_hash(_noise(3)), perceptual_hash(_noise(4))
        )

        assert distance > 48

    def test_identical_image_gives_zero_distance(self):
        image = make_quadrant_image()

        assert hamming_distance(perceptual_hash(image), perceptual_hash(image)) == 0

    @pytest.mark.parametrize("broken", ["", "не хеш", None])
    def test_unreadable_hash_is_maximally_far(self, broken):
        assert hamming_distance(perceptual_hash(make_quadrant_image()), broken) == 256

    def test_hashes_of_different_length_are_maximally_far(self):
        """Старый 16-символьный хеш и новый 64-символьный несравнимы:
        XOR дал бы случайное число бит, а не расстояние из 256.
        """
        old = "9191cece3b393686"
        new = perceptual_hash(make_quadrant_image())

        assert hamming_distance(old, new) == 256
        assert hamming_distance(new, old) == 256

    def test_flat_colours_are_not_distinguished(self):
        """Известное ограничение: монотонные заливки алгоритм не различает.

        У однотонной картинки энергия DCT есть только в нулевой частоте,
        остальные биты получаются из шума округления: красная и синяя
        заливки дают 16 из 256, то есть меньше порога «вероятный
        дубликат» (32). Порог не снижаем — пережатый JPEG того же снимка
        даёт до 40 из 256 и перестал бы находиться. Для архива
        ограничение не важно: заливок в нём нет, а решение принимает
        человек. Тест фиксирует поведение, а не желаемое.
        """
        red = perceptual_hash(Image.new("RGB", (600, 400), "red"))
        blue = perceptual_hash(Image.new("RGB", (600, 400), "blue"))

        assert hamming_distance(red, blue) < 32
        assert hamming_distance(red, red) == 0
