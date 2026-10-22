"""Тесты разбора EXIF: белый список, GPS, нормализация, год съёмки."""

import io

import pytest
from PIL import ExifTags, Image
from PIL.TiffImagePlugin import IFDRational

from apps.photos.exif import _normalize, extract_exif, taken_year

# Имя тега → номер: так тесты читаются по имени, а не по магическому числу.
TAGS = {name: number for number, name in ExifTags.TAGS.items()}


def _image_with_exif(exif: Image.Exif, size=(60, 40)):
    """Прогнать изображение через JPEG: EXIF живёт в файле, а не в памяти."""
    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, format="JPEG", exif=exif)
    buffer.seek(0)
    return Image.open(buffer)


def _exif(**tags) -> Image.Exif:
    exif = Image.Exif()
    for name, value in tags.items():
        exif[TAGS[name]] = value
    return exif


class TestWhitelist:
    def test_make_and_model_are_kept(self):
        result = extract_exif(_image_with_exif(_exif(Make="Canon", Model="EOS 5D")))

        assert result["Make"] == "Canon"
        assert result["Model"] == "EOS 5D"

    def test_artist_and_copyright_are_kept(self):
        """Подсказка модератору: кто снимал и кому принадлежит кадр."""
        exif = _exif(Artist="Ivanov", Copyright="Soviet Sport")

        result = extract_exif(_image_with_exif(exif))

        assert result["Artist"] == "Ivanov"
        assert result["Copyright"] == "Soviet Sport"

    def test_unknown_tags_are_dropped(self):
        exif = _exif(ImageDescription="Final")
        exif[0x9286] = b"secret comment"  # UserComment

        result = extract_exif(_image_with_exif(exif))

        assert result["ImageDescription"] == "Final"
        assert 0x9286 not in result
        assert "UserComment" not in result

    def test_image_without_exif_gives_empty_dict(self):
        buffer = io.BytesIO()
        Image.new("RGB", (10, 10), "red").save(buffer, format="JPEG")
        buffer.seek(0)

        assert extract_exif(Image.open(buffer)) == {}


class TestGpsPrivacy:
    def test_gps_is_absent(self):
        """GPS IFD не читается вовсе: домашний адрес не должен попасть
        в JSONB ни одним ключом.
        """
        exif = _exif(Make="Apple")
        gps = exif.get_ifd(0x8825)
        gps[1] = "N"
        gps[2] = (55.0, 45.0, 0.0)

        result = extract_exif(_image_with_exif(exif))

        assert not any("GPS" in key for key in result)
        assert all("55" not in str(value) for value in result.values())


class TestNormalization:
    def test_rational_becomes_float(self):
        exif = _exif()
        exif.get_ifd(ExifTags.IFD.Exif)[TAGS["ExposureTime"]] = IFDRational(1, 250)

        result = extract_exif(_image_with_exif(exif))

        assert isinstance(result["ExposureTime"], float)
        assert result["ExposureTime"] == pytest.approx(0.004)

    def test_ascii_bytes_become_text(self):
        assert _normalize(b"Adobe Photoshop\x00") == "Adobe Photoshop"

    def test_non_ascii_bytes_are_dropped(self):
        assert _normalize("Фотошоп".encode("cp1251")) is None

    def test_trailing_zeros_are_stripped_from_text(self):
        assert _normalize("Final\x00") == "Final"

    def test_tuple_becomes_list(self):
        assert _normalize((300, 1)) == [300, 1]

    def test_unknown_type_is_dropped(self):
        assert _normalize(object()) is None


class TestTakenYear:
    def test_valid_date(self):
        assert taken_year({"DateTimeOriginal": "1985:07:14 12:00:00"}) == 1985

    def test_missing_date(self):
        assert taken_year({}) is None

    def test_scanner_date_without_original_is_ignored(self):
        """У скана бумажного снимка дата принадлежит сканеру: в архиве
        она дала бы 2026 год всему альбому.
        """
        assert taken_year({"DateTime": "2026:10:22 09:00:00"}) is None

    def test_date_before_archive_range(self):
        assert taken_year({"DateTimeOriginal": "1849:01:01 00:00:00"}) is None

    def test_date_in_future(self):
        assert taken_year({"DateTimeOriginal": "2999:01:01 00:00:00"}) is None

    def test_broken_format(self):
        assert taken_year({"DateTimeOriginal": "не дата"}) is None

    def test_impossible_day(self):
        assert taken_year({"DateTimeOriginal": "1985:02:31 12:00:00"}) is None

    def test_non_string_value(self):
        assert taken_year({"DateTimeOriginal": 1985}) is None
