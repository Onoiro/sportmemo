"""Демонстрационный архив для локальной разработки.

Публичного UI пока нет, а смотреть вёрстку и галереи нужно уже сейчас.
Команда создаёт вымышленный архив: команды, людей, события и фотографии
с сгенерированными изображениями. Данные намеренно «неудобные» — черновики,
скрытые фамилии, фото без подтверждённых прав, события без даты, — чтобы UI
проверялся сразу на крайних случаях.

Повторный запуск обновляет демо-объекты, а не создаёт дубли. Флаг `--flush`
удаляет демо-объекты вместе с файлами изображений.
"""

import io
import random
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont

from apps.accounts.models import User, UserRole
from apps.catalog.models import (
    Event,
    EventTeam,
    EventType,
    NameKind,
    NameVisibility,
    Person,
    PersonName,
    Role,
    Team,
    TeamMembership,
)
from apps.core.models import Sport, Status
from apps.photos.models import (
    Photo,
    PhotoPerson,
    PhotoPersonRole,
    Source,
    SourceKind,
)
from apps.photos.services import process_photo

DEMO_EMAIL = "demo@example.com"
DEMO_MODERATOR_EMAIL = "demo-moderator@example.com"
DEMO_PASSWORD = "demo-password-12345"
DEMO_SEED = 20261011

# Размеры намеренно разные: галерея должна проверяться на всех пропорциях.
PHOTO_SIZES = [(1200, 800), (800, 1200), (1000, 1000)]

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]

POSITIONS = [
    "слева первый",
    "слева второй",
    "в центре",
    "справа первый",
    "справа второй",
    "сидит в первом ряду",
    "стоит за спиной",
]

TEAMS = [
    {
        "slug": "sokoly",
        "name": "Соколы",
        "city": "Москва",
        "founded_year": 1985,
        "status": Status.PUBLISHED,
        "description": (
            "Одна из старейших любительских команд Москвы. Выросла из "
            "дворовой секции при стадионе «Крылья»."
        ),
    },
    {
        "slug": "baltiyskaya-volna",
        "name": "Балтийская волна",
        "city": "Санкт-Петербург",
        "founded_year": 1990,
        "status": Status.PUBLISHED,
        "description": (
            "Команда ленинградских студентов, перебравшаяся в 1990-е "
            "на городской стадион."
        ),
    },
    {
        "slug": "uralskie-medvedi",
        "name": "Уральские медведи",
        "city": "Екатеринбург",
        "founded_year": 1993,
        "status": Status.PUBLISHED,
        "description": "Молодая команда из Свердловска, сильная подача.",
    },
    {
        "slug": "tihookeanskie-kraby",
        "name": "Тихоокеанские крабы",
        "city": "Владивосток",
        "founded_year": 1998,
        "status": Status.PUBLISHED,
        "description": "Дальневосточная команда, играет на выездах по всему региону.",
    },
    {
        "slug": "stepnye-orly",
        "name": "Степные орлы",
        "city": "Ростов-на-Дону",
        "founded_year": 2005,
        # Черновик: проверяем, что UI не показывает неопубликованное.
        "status": Status.DRAFT,
        "description": "Команда создана, но модератор её ещё не публиковал.",
    },
]

PERSONS = [
    {
        "slug": "gromov-aleksey",
        "first_name": "Алексей",
        "last_name": "Громов",
        "middle_name": "Петрович",
        "birth_year": 1958,
        "bio": "Тренер и играющий капитан «Соколов», позже руководитель клуба.",
    },
    {
        "slug": "veselov-igor",
        "first_name": "Игорь",
        "last_name": "Веселов",
        "birth_year": 1962,
        "bio": "Ветеран московского бейсбола, играл до 2010 года.",
        "names": [("Весельчак", NameKind.NICKNAME)],
    },
    {
        "slug": "kuznetsov-pyotr",
        "first_name": "Пётр",
        "last_name": "Кузнецов",
        "middle_name": "Ильич",
        "birth_year": 1955,
        "death_year": 2019,
        "bio": "Судья республиканской категории, стоял за кассетой до 2005 года.",
        "names": [("Кузнецов П. И.", NameKind.ALT_SPELLING)],
    },
    {
        "slug": "romanov-nikolay",
        "first_name": "Николай",
        "last_name": "Романов",
        "birth_year": 1960,
        "bio": "Первый тренер «Балтийской волны».",
    },
    {
        "slug": "pankratov-sergey",
        "first_name": "Сергей",
        "last_name": "Панкратов",
        "middle_name": "Владимирович",
        "birth_year": 1965,
        "bio": "Играл в «Соколах» и в «Уральских медведях».",
    },
    {
        "slug": "tihonov-vladimir",
        "first_name": "Владимир",
        "last_name": "Тихонов",
        "birth_year": 1957,
        "bio": "Питчер, автор первого ноу-хиттера команды.",
    },
    {
        "slug": "astahov-dmitriy",
        "first_name": "Дмитрий",
        "last_name": "Астахов",
        "middle_name": "Сергеевич",
        "birth_year": 1968,
    },
    {
        "slug": "balashov-andrey",
        "first_name": "Андрей",
        "last_name": "Балашов",
        "birth_year": 1970,
        "names": [("Балаш", NameKind.NICKNAME)],
    },
    {
        "slug": "krylov-mihail",
        "first_name": "Михаил",
        "last_name": "Крылов",
        "birth_year": 1963,
        "bio": "Ловил за «Балтийскую волну» больше десяти лет.",
    },
    {
        "slug": "savelev-yuriy",
        "first_name": "Юрий",
        "last_name": "Савельев",
        "birth_year": 1959,
        "death_year": 2008,
    },
    {
        "slug": "ershov-viktor",
        "first_name": "Виктор",
        "last_name": "Ершов",
        "middle_name": "Анатольевич",
        "birth_year": 1972,
    },
    {
        "slug": "solovev-oleg",
        "first_name": "Олег",
        "last_name": "Соловьёв",
        "birth_year": 1974,
        "names": [("Соловей", NameKind.NICKNAME)],
    },
    {
        "slug": "dyachenko-roman",
        "first_name": "Роман",
        "last_name": "Дьяченко",
        "middle_name": "Алексеевич",
        "birth_year": 1976,
        "names": [("Дьяченко Р. А.", NameKind.ALT_SPELLING)],
    },
    {
        "slug": "melnik-konstantin",
        "first_name": "Константин",
        "last_name": "Мельник",
        "birth_year": 1978,
        "names": [("Мельник Костя", NameKind.NICKNAME)],
    },
    {
        "slug": "shevchenko-artem",
        "first_name": "Артём",
        "last_name": "Шевченко",
        "birth_year": 1980,
    },
    {
        "slug": "kovalev-denis",
        "first_name": "Денис",
        "last_name": "Ковалёв",
        "middle_name": "Игоревич",
        "birth_year": 1982,
    },
    {
        "slug": "zverev-egor",
        "first_name": "Егор",
        "last_name": "Зверев",
        "birth_year": 1984,
    },
    {
        "slug": "naumov-pavel",
        "first_name": "Павел",
        "last_name": "Наумов",
        "birth_year": 1986,
    },
    {
        "slug": "gordeev-maksim",
        "first_name": "Максим",
        "last_name": "Гордеев",
        "middle_name": "Петрович",
        "birth_year": 1988,
        "names": [("Гордеев М. П.", NameKind.ALT_SPELLING)],
    },
    {
        "slug": "lebedev-anton",
        "first_name": "Антон",
        "last_name": "Лебедев",
        "birth_year": 1990,
    },
    {
        "slug": "vorobev-ilya",
        "first_name": "Илья",
        "last_name": "Воробьёв",
        "birth_year": 1992,
    },
    {
        "slug": "samoylov-nikita",
        "first_name": "Никита",
        "last_name": "Самойлов",
        "middle_name": "Олегович",
        "birth_year": 1994,
    },
    # Несовершеннолетние: фамилия не публикуется.
    {
        "slug": "kirill-prohorov",
        "first_name": "Кирилл",
        "last_name": "Прохоров",
        "birth_year": 1995,
        "name_visibility": NameVisibility.FIRST_NAME_ONLY,
        "bio": "Воспитанник юношеской секции.",
        "names": [("Кирюха", NameKind.NICKNAME)],
    },
    {
        "slug": "timur-aslanov",
        "first_name": "Тимур",
        "last_name": "Асланов",
        "birth_year": 1996,
        "name_visibility": NameVisibility.FIRST_NAME_ONLY,
    },
    {
        "slug": "zahar-panteleev",
        "first_name": "Захар",
        "last_name": "Пантелеев",
        "birth_year": 1997,
        "name_visibility": NameVisibility.FIRST_NAME_ONLY,
    },
    {
        "slug": "vadim-osipov",
        "first_name": "Вадим",
        "last_name": "Осипов",
        "birth_year": 1993,
        "name_visibility": NameVisibility.FIRST_NAME_ONLY,
    },
    {
        "slug": "gleb-morozov",
        "first_name": "Глеб",
        "last_name": "Морозов",
        "birth_year": 1991,
        "name_visibility": NameVisibility.FIRST_NAME_ONLY,
    },
    # Черновики: проверяем, что неопубликованный человек не виден публично.
    {
        "slug": "barsukov-lev",
        "first_name": "Лев",
        "last_name": "Барсуков",
        "middle_name": "Сергеевич",
        "birth_year": 1979,
        "status": Status.DRAFT,
    },
    {
        "slug": "stepanov-ivan",
        "first_name": "Иван",
        "last_name": "Степанов",
        "birth_year": 1975,
        "status": Status.DRAFT,
    },
    # Скрытое имя: в публичном контексте — нейтральная подпись.
    {
        "slug": "yudin-stanislav",
        "first_name": "Станислав",
        "last_name": "Юдин",
        "middle_name": "Аркадьевич",
        "birth_year": 1969,
        "name_visibility": NameVisibility.HIDDEN,
    },
]

EVENTS = [
    {
        "slug": "pervenstvo-sssr-1980",
        "name": "Первенство СССР среди юношей",
        "event_type": EventType.TOURNAMENT,
        "year": 1980,
        "date_from": date(1980, 7, 12),
        "date_to": date(1980, 7, 20),
        "city": "Москва",
        "country": "СССР",
        "attributes": {"stage": "финал", "teams": 8},
        "description": "Первый крупный турнир, в котором участвовали «Соколы».",
        "status": Status.PUBLISHED,
        "teams": [("sokoly", "2 место"), ("baltiyskaya-volna", "участие")],
    },
    {
        "slug": "kubok-baltiki-1988",
        "name": "Кубок Балтики",
        "event_type": EventType.TOURNAMENT,
        "year": 1988,
        "date_from": date(1988, 6, 4),
        "date_to": date(1988, 6, 12),
        "city": "Ленинград",
        "country": "СССР",
        "description": "Домашний турнир «Балтийской волны».",
        "status": Status.PUBLISHED,
        "teams": [
            ("baltiyskaya-volna", "1 место"),
            ("sokoly", "3 место"),
        ],
    },
    {
        "slug": "sokoly-volna-1992",
        "name": "Товарищеская встреча «Соколы» — «Балтийская волна»",
        "event_type": EventType.FRIENDLY,
        "year": 1992,
        "date_from": date(1992, 8, 15),
        "city": "Москва",
        "country": "Россия",
        "attributes": {"score": "5:3", "innings": 9},
        "status": Status.PUBLISHED,
        "teams": [("sokoly", "победа"), ("baltiyskaya-volna", "поражение")],
    },
    {
        "slug": "chempionat-rossii-1994",
        "name": "Чемпионат России",
        "event_type": EventType.TOURNAMENT,
        "year": 1994,
        "date_from": date(1994, 5, 1),
        "date_to": date(1994, 5, 9),
        "city": "Екатеринбург",
        "country": "Россия",
        "attributes": {"stage": "финал", "result": "1 место", "teams": 6},
        "description": "Первый чемпионат, где играли команды из четырёх городов.",
        "status": Status.PUBLISHED,
        "teams": [
            ("uralskie-medvedi", "1 место"),
            ("sokoly", "проиграли в финале"),
            ("baltiyskaya-volna", "3 место"),
        ],
    },
    {
        "slug": "pervenstvo-urala-1999",
        "name": "Первенство Урала",
        "event_type": EventType.SERIES,
        "year": 1999,
        # Даты нет: только год. UI должен показывать «1999», а не «—».
        "city": "Екатеринбург",
        "country": "Россия",
        "status": Status.PUBLISHED,
        "teams": [("uralskie-medvedi", "1 место"), ("tihookeanskie-kraby", "")],
    },
    {
        "slug": "turne-dalniy-vostok-2003",
        "name": "Турне по Дальнему Востоку",
        "event_type": EventType.TRIP,
        "year": 2003,
        "date_from": date(2003, 7, 21),
        "date_to": date(2003, 8, 3),
        "city": "Владивосток",
        "country": "Россия",
        "description": "Выездная серия из шести матчей.",
        "status": Status.PUBLISHED,
        "teams": [
            ("tihookeanskie-kraby", "4 победы из 6"),
            ("uralskie-medvedi", ""),
        ],
    },
    {
        "slug": "otkrytie-stadiona-2007",
        "name": "Церемония открытия стадиона",
        "event_type": EventType.CEREMONY,
        # Ни года, ни даты — событие попадает в блок «год не указан».
        "city": "Ростов-на-Дону",
        "country": "Россия",
        "description": "Открытие нового поля и показательный матч.",
        "status": Status.DRAFT,
        "teams": [("stepnye-orly", "")],
    },
    {
        "slug": "kubok-tihogo-okeana-2010",
        "name": "Кубок Тихого океана",
        "event_type": EventType.TOURNAMENT,
        "year": 2010,
        "date_from": date(2010, 9, 10),
        "date_to": date(2010, 9, 14),
        "city": "Владивосток",
        "country": "Россия",
        "attributes": {"stage": "групповой этап"},
        "status": Status.DRAFT,
        "teams": [("tihookeanskie-kraby", ""), ("sokoly", "")],
    },
]

SOURCES = [
    {
        "kind": SourceKind.PAPER_PHOTO,
        "title": "Семейный альбом Соколовых",
        "holder": "семейный архив Соколовых",
        "date": date(1990, 5, 1),
        "notes": "Картонный альбом, 12 листов.",
    },
    {
        "kind": SourceKind.NEWSPAPER,
        "title": "Вырезка о финале первенства",
        "publication": "Советский спорт",
        "date": date(1980, 7, 22),
        "page": "4",
        "holder": "семейный архив Громовых",
    },
    {
        "kind": SourceKind.NEGATIVE,
        "title": "Плёнка из архива клуба",
        "holder": "архив клуба «Балтийская волна»",
        "notes": "35 мм, 36 кадров, часть кадров повреждена.",
    },
    {
        "kind": SourceKind.PROGRAM,
        "title": "Программа чемпионата России 1994",
        "date": date(1994, 5, 1),
        "page": "2",
        "holder": "личный архив А. Балашова",
    },
    {
        "kind": SourceKind.DIGITAL,
        "title": "Цифровой архив «Тихоокеанских крабов»",
        "holder": "клубный компьютер",
    },
    {
        "kind": SourceKind.TICKET,
        "title": "Билет на товарищескую встречу 1992 года",
        "date": date(1992, 8, 15),
        "page": "",
        "holder": "личный архив П. Наумова",
    },
]

# Особые случаи идут первыми: черновики и фото без подтверждённых прав.
# Остальные фото генерируются детерминированно до нужного количества.
EXPLICIT_PHOTOS = [
    {
        "title": "Соколы на сборах",
        "year": 1985,
        "team": "sokoly",
        "source": "Семейный альбом Соколовых",
        "size": (1200, 800),
        "rights": True,
        "status": Status.PUBLISHED,
        "caption": "Первая команда «Соколов» на сборах под Москвой.",
    },
    {
        "title": "Финал первенства СССР",
        "year": 1980,
        "team": "sokoly",
        "event": "pervenstvo-sssr-1980",
        "source": "Вырезка о финале первенства",
        "size": (800, 1200),
        "rights": True,
        "status": Status.PUBLISHED,
        "caption": "Кадр из газетной вырезки: финал юношеского первенства.",
    },
    {
        "title": "Матч «Соколы» — «Балтийская волна»",
        "year": 1992,
        "team": "sokoly",
        "event": "sokoly-volna-1992",
        "source": "Билет на товарищескую встречу 1992 года",
        "size": (1000, 1000),
        "rights": True,
        "status": Status.PUBLISHED,
        "caption": "Товарищеская встреча 1992 года, девятый иннинг.",
    },
    {
        "title": "Газетная вырезка: финал 1994",
        "year": 1994,
        "team": "uralskie-medvedi",
        "event": "chempionat-rossii-1994",
        "source": "Программа чемпионата России 1994",
        "size": (1200, 800),
        # Права не подтверждены — значит черновик. Правило «без прав
        # не публикуется» отменено (решение 2.1), но опубликованное фото
        # без прав в демо-архиве выглядело бы бессмысленно.
        "rights": False,
        "status": Status.DRAFT,
        "caption": "Вырезка без указания правообладателя.",
    },
    {
        "title": "Турне по Дальнему Востоку",
        "year": 2003,
        "team": "tihookeanskie-kraby",
        "event": "turne-dalniy-vostok-2003",
        "source": "Цифровой архив «Тихоокеанских крабов»",
        "size": (1200, 800),
        "rights": False,
        "status": Status.DRAFT,
        "caption": "Цифровой кадр без подтверждённых прав.",
    },
    {
        "title": "Разбор негативов",
        "year": 1988,
        "team": "baltiyskaya-volna",
        "source": "Плёнка из архива клуба",
        "size": (800, 1200),
        "rights": False,
        "status": Status.DRAFT,
        "caption": "Кадр с плёнки, права уточняются.",
    },
    {
        "title": "Неизвестный матч",
        "year": 1999,
        # Без команды и события: только год.
        "size": (1200, 800),
        "rights": True,
        "status": Status.DRAFT,
        "caption": "Кто на кадре — пока не установлено.",
    },
    {
        "title": "Плёнка из архива клуба, кадр 7",
        "year": 1990,
        "team": "baltiyskaya-volna",
        "source": "Плёнка из архива клуба",
        "size": (1000, 1000),
        "rights": True,
        "status": Status.DRAFT,
    },
    {
        "title": "Показательный матч на открытии стадиона",
        "year": 2007,
        "team": "stepnye-orly",
        "event": "otkrytie-stadiona-2007",
        "size": (1200, 800),
        "rights": True,
        "status": Status.DRAFT,
        "caption": "Черновик: ждём разрешения от клуба.",
    },
]


def _load_font(size):
    """Системный шрифт, если он есть, иначе встроенный растровый."""
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _wrap_text(text, draw, font, max_width, max_lines):
    """Разбить подпись на строки по фактической ширине в пикселях.

    Возвращает `None`, если текст не влезает в отведённые строки:
    вызывающий код уменьшает кегль и пробует снова.
    """
    lines = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        return None
    if any(draw.textlength(line, font=font) > max_width for line in lines):
        return None
    return lines


def _make_demo_image(title, year, size, seed):
    """Сгенерировать «архивное» изображение: сепийный градиент и подпись.

    Генерация воспроизводима: те же аргументы дают те же байты.
    """
    rng = random.Random(seed)
    width, height = size
    top = (243, 230, 205)
    bottom = (146, 118, 82)

    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)

    for y in range(height):
        ratio = y / max(height - 1, 1)
        color = tuple(round(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)

    # Зернистость: имитация старой печати.
    for _ in range(500):
        x = rng.randrange(width)
        y = rng.randrange(height)
        ratio = y / max(height - 1, 1)
        shade = rng.randint(-22, 22)
        color = tuple(
            max(0, min(255, round(top[i] + (bottom[i] - top[i]) * ratio + shade)))
            for i in range(3)
        )
        draw.point((x, y), fill=color)

    inset = max(min(size) // 40, 6)
    draw.rectangle(
        [inset, inset, width - inset - 1, height - inset - 1],
        outline=(92, 70, 46),
        width=max(inset // 3, 2),
    )

    year_font = _load_font(max(height // 26, 12))
    # Подпись не должна вылезать за рамку: кегль подбирается по ширине,
    # потому что у портретных кадров ширина меньше высоты.
    max_text_width = width - inset * 6
    lines = None
    font_size = max(height // 16, 14)
    while font_size >= 12:
        title_font = _load_font(font_size)
        lines = _wrap_text(title, draw, title_font, max_text_width, max_lines=3)
        if lines:
            break
        font_size -= 2
    if not lines:
        # Даже мелким кеглем не влезло: обрезаем по пиксельной ширине.
        title_font = _load_font(12)
        short = title
        while short and draw.textlength(short + "…", font=title_font) > max_text_width:
            short = short[:-1]
        lines = [f"{short}…"] if short else [title[:1]]
        font_size = 12

    line_height = max(font_size * 3 // 2, 16)
    block_height = line_height * len(lines) + line_height
    y = max((height - block_height) // 2, inset * 3)

    for line in lines:
        text_width = draw.textlength(line, font=title_font)
        draw.text(
            ((width - text_width) / 2, y), line, font=title_font, fill=(70, 52, 34)
        )
        y += line_height

    year_text = str(year) if year else "год не указан"
    year_size = max(height // 26, 12)
    year_font = _load_font(year_size)
    while (
        year_size > 12 and draw.textlength(year_text, font=year_font) > max_text_width
    ):
        year_size -= 2
        year_font = _load_font(year_size)
    year_width = draw.textlength(year_text, font=year_font)
    draw.text(
        ((width - year_width) / 2, y + line_height // 3),
        year_text,
        font=year_font,
        fill=(104, 80, 54),
    )

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


class Command(BaseCommand):
    help = "Создать демонстрационный архив для локальной разработки"

    def add_arguments(self, parser):
        parser.add_argument(
            "--photos",
            type=int,
            default=40,
            help="Сколько фотографий создать (по умолчанию 40)",
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Удалить демо-объекты и их файлы вместо создания",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Разрешить запуск при DEBUG=False (например, на боевой базе)",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Команда предназначена для разработки и работает только "
                "при DEBUG=True. Если вы уверены, добавьте --force."
            )

        if options["flush"]:
            self._flush()
            return

        self.rng = random.Random(DEMO_SEED)
        self.sport = Sport.objects.get(slug="baseball")
        self.demo_user = self._ensure_user(DEMO_EMAIL, "Демо-автор", UserRole.USER)
        self.demo_moderator = self._ensure_user(
            DEMO_MODERATOR_EMAIL, "Демо-модератор", UserRole.MODERATOR
        )

        teams = self._seed_teams()
        persons = self._seed_persons()
        self._seed_memberships(teams, persons)
        events = self._seed_events(teams)
        sources = self._seed_sources()
        photos = self._seed_photos(teams, events, sources, options["photos"])
        self._seed_photo_persons(photos, persons)

        self.stdout.write(
            self.style.SUCCESS(
                f"Демо-архив готов: команд — {len(teams)}, людей — "
                f"{len(persons)}, событий — {len(events)}, источников — "
                f"{len(sources)}, фото — {len(photos)}."
            )
        )
        self.stdout.write(
            f"Пользователи: {DEMO_EMAIL} (автор), "
            f"{DEMO_MODERATOR_EMAIL} (модератор), пароль {DEMO_PASSWORD}."
        )

    # --- пользователи -------------------------------------------------

    def _ensure_user(self, email, display_name, role):
        now = timezone.now()
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "display_name": display_name,
                "role": role,
                "terms_accepted_at": now,
                "privacy_accepted_at": now,
                "consent_pd_at": now,
                "consent_pd_version": settings.SPORTMEMO_CONSENT_PD_VERSION,
            },
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save(update_fields=["password"])
        return user

    # --- команды, люди, события ---------------------------------------

    def _seed_teams(self):
        teams = {}
        for spec in TEAMS:
            team, created = Team.objects.update_or_create(
                sport=self.sport,
                slug=spec["slug"],
                defaults={
                    "name": spec["name"],
                    "city": spec["city"],
                    "founded_year": spec["founded_year"],
                    "description": spec["description"],
                    "created_by": self.demo_user,
                    "attributes": {"demo": True},
                },
            )
            self._publish(team, spec["status"])
            teams[spec["slug"]] = team
        return teams

    def _seed_persons(self):
        persons = {}
        for spec in PERSONS:
            person, created = Person.objects.update_or_create(
                slug=spec["slug"],
                defaults={
                    "first_name": spec["first_name"],
                    "last_name": spec.get("last_name", ""),
                    "middle_name": spec.get("middle_name", ""),
                    "birth_year": spec.get("birth_year"),
                    "death_year": spec.get("death_year"),
                    "name_visibility": spec.get("name_visibility", NameVisibility.FULL),
                    "bio": spec.get("bio", ""),
                    "created_by": self.demo_user,
                    "attributes": {"demo": True},
                },
            )
            self._publish(person, spec.get("status", Status.PUBLISHED))
            for value, kind in spec.get("names", []):
                PersonName.objects.update_or_create(
                    person=person,
                    value=value,
                    defaults={"kind": kind},
                )
            persons[spec["slug"]] = person
        return persons

    def _seed_memberships(self, teams, persons):
        """Состав команд: детерминированная генерация по seed."""
        pool = list(persons.values())
        for team in teams.values():
            roster = self.rng.sample(pool, k=self.rng.randint(7, 11))
            first_year = max(team.founded_year or 1985, 1985)
            for index, person in enumerate(roster):
                year_from = self.rng.randint(first_year, 2006)
                role = Role.COACH if index == 0 else Role.PLAYER
                if index == 0:
                    year_from = max(first_year - 2, 1980)
                year_to = year_from + self.rng.randint(0, 6)
                if self.rng.random() < 0.25:
                    year_to = None
                TeamMembership.objects.update_or_create(
                    team=team,
                    person=person,
                    role=role,
                    year_from=year_from,
                    defaults={
                        "year_to": year_to,
                        "position": "" if role == Role.COACH else "питчер",
                        "created_by": self.demo_user,
                    },
                )

    def _seed_events(self, teams):
        events = {}
        for spec in EVENTS:
            event, created = Event.objects.update_or_create(
                sport=self.sport,
                slug=spec["slug"],
                defaults={
                    "name": spec["name"],
                    "event_type": spec["event_type"],
                    "date_from": spec.get("date_from"),
                    "date_to": spec.get("date_to"),
                    "year": spec.get("year"),
                    "city": spec.get("city", ""),
                    "country": spec.get("country", ""),
                    "description": spec.get("description", ""),
                    "created_by": self.demo_user,
                    "attributes": {"demo": True, **spec.get("attributes", {})},
                },
            )
            self._publish(event, spec["status"])
            for team_slug, result_note in spec.get("teams", []):
                EventTeam.objects.update_or_create(
                    event=event,
                    team=teams[team_slug],
                    defaults={
                        "result_note": result_note,
                        "created_by": self.demo_user,
                    },
                )
            events[spec["slug"]] = event
        return events

    # --- источники и фото ---------------------------------------------

    def _seed_sources(self):
        sources = {}
        for spec in SOURCES:
            source, created = Source.objects.update_or_create(
                kind=spec["kind"],
                title=spec["title"],
                defaults={
                    "publication": spec.get("publication", ""),
                    "date": spec.get("date"),
                    "page": spec.get("page", ""),
                    "holder": spec.get("holder", ""),
                    "notes": spec.get("notes", ""),
                    "owner": self.demo_user,
                },
            )
            sources[spec["title"]] = source
        return sources

    def _photo_specs(self, count):
        """Спецификации фото: особые случаи, затем типовые."""
        specs = [dict(spec) for spec in EXPLICIT_PHOTOS]
        team_slugs = [spec["slug"] for spec in TEAMS]
        event_slugs = [spec["slug"] for spec in EVENTS]
        source_titles = [spec["title"] for spec in SOURCES]
        captions = [
            "Командное фото после матча.",
            "Разминка перед игрой.",
            "Кадр из архива семьи игрока.",
            "Игровой момент, подача.",
            "Тренер объясняет схему.",
            "Болельщики на трибуне.",
        ]
        while len(specs) < count:
            year = self.rng.randint(1985, 2010)
            team_slug = self.rng.choice(team_slugs)
            team_name = next(
                spec["name"] for spec in TEAMS if spec["slug"] == team_slug
            )
            # Номер кадра делает заголовок уникальным: иначе поиск
            # существующего фото по заголовку нашёл бы чужой кадр.
            specs.append(
                {
                    "title": f"{team_name}, {year} — кадр {len(specs) + 1}",
                    "year": year,
                    "team": team_slug,
                    "event": self.rng.choice([None, *event_slugs]),
                    "source": self.rng.choice(source_titles),
                    "size": self.rng.choice(PHOTO_SIZES),
                    "rights": True,
                    "status": Status.PUBLISHED,
                    "caption": self.rng.choice(captions),
                }
            )
        return specs[:count]

    def _seed_photos(self, teams, events, sources, count):
        photos = []
        for index, spec in enumerate(self._photo_specs(count)):
            team = teams.get(spec.get("team")) if spec.get("team") else None
            event = events.get(spec.get("event")) if spec.get("event") else None
            source = sources.get(spec.get("source")) if spec.get("source") else None
            now = timezone.now()
            defaults = {
                "sport": self.sport,
                "team": team,
                "event": event,
                "source": source,
                "year": spec["year"],
                "title": spec["title"],
                "caption": spec.get("caption", ""),
                "rights_confirmed": spec["rights"],
                "rights_confirmed_at": now if spec["rights"] else None,
                "uploaded_by": self.demo_user,
            }
            photo = Photo.objects.filter(
                title=spec["title"], uploaded_by=self.demo_user
            ).first()
            if photo is None:
                photo = Photo.objects.create(
                    **defaults,
                    original=ContentFile(
                        _make_demo_image(
                            spec["title"],
                            spec["year"],
                            spec["size"],
                            DEMO_SEED + index,
                        ),
                        name=f"demo-{index:03d}.jpg",
                    ),
                )
            else:
                for key, value in defaults.items():
                    setattr(photo, key, value)
                photo.save()
            # Демо-архив проходит тот же пайплайн, что и настоящая загрузка:
            # размеры, `display` и `phash` считает обработка, а не команда.
            process_photo(photo)
            self._publish(photo, spec["status"])
            photos.append(photo)
        return photos

    def _seed_photo_persons(self, photos, persons):
        pool = list(persons.values())
        roles = list(PhotoPersonRole.values)
        for photo in photos:
            count = self.rng.randint(1, 4)
            for person in self.rng.sample(pool, k=min(count, len(pool))):
                PhotoPerson.objects.update_or_create(
                    photo=photo,
                    person=person,
                    defaults={
                        "role": self.rng.choice(roles),
                        "position": self.rng.choice(POSITIONS),
                        "added_by": self.demo_user,
                    },
                )

    # --- служебное -----------------------------------------------------

    def _publish(self, obj, status):
        """Привести статус к нужному, публикуя от имени демо-модератора."""
        if obj.status == status:
            return
        if status == Status.PUBLISHED:
            obj.publish(moderator=self.demo_moderator, reason="демо-данные")
        elif status == Status.DRAFT:
            obj.status = Status.DRAFT
            obj.moderated_by = None
            obj.moderated_at = None
            obj.save(
                update_fields=["status", "moderated_by", "moderated_at", "updated_at"]
            )

    # --- удаление ------------------------------------------------------

    def _flush(self):
        """Удалить демо-объекты и их файлы, не задевая обычные данные."""
        demo_users = User.objects.filter(email__in=[DEMO_EMAIL, DEMO_MODERATOR_EMAIL])
        photos = Photo.objects.filter(uploaded_by__in=demo_users)
        photo_count = photos.count()
        for photo in photos:
            if photo.original:
                photo.original.storage.delete(photo.original.name)
        # Фото первыми: Photo.uploaded_by защищён PROTECT, а аватар человека
        # ссылается на фото через SET_NULL.
        deleted_photos, _ = photos.delete()

        sources = Source.objects.filter(owner__in=demo_users)
        source_count = sources.count()
        sources.delete()

        teams = Team.objects.filter(attributes__demo=True)
        persons = Person.objects.filter(attributes__demo=True)
        events = Event.objects.filter(attributes__demo=True)
        team_count, person_count, event_count = (
            teams.count(),
            persons.count(),
            events.count(),
        )
        # Каскад забирает TeamMembership, PersonName, EventTeam, PhotoPerson.
        teams.delete()
        persons.delete()
        events.delete()

        user_count, _ = demo_users.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Удалено: фото — {photo_count}, источников — {source_count}, "
                f"команд — {team_count}, людей — {person_count}, "
                f"событий — {event_count}, пользователей — {user_count}."
            )
        )
