"""Настройки Django для SportMemo.

Единый файл настроек: значения читаются из окружения (`.env` в dev).
Версия проекта живёт в `pyproject.toml` и подхватывается отсюда.
"""

from pathlib import Path
from tomllib import load as toml_load

import environ
from PIL import Image

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    USE_S3=(bool, False),
    S3_USE_SSL=(bool, False),
    EMAIL_USE_SSL=(bool, True),
)

environ.Env.read_env(BASE_DIR / ".env")

# --- Версия проекта (единственный источник истины — pyproject.toml) ---
with (BASE_DIR / "pyproject.toml").open("rb") as f:
    VERSION = toml_load(f)["project"]["version"]

# --- Безопасность ---
SECRET_KEY = env("SECRET_KEY", default="dev-secret-key-change-me")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Имена cookie в dev — с префиксом проекта. Cookie не различаются по порту:
# `localhost:8000` и `localhost:8001` делят один `sessionid` на домене
# `localhost`, и соседний проект на другом порту гасит нашу сессию, потому
# что не находит ключ в своей базе и стирает cookie. На боевом сервере сайт
# один, поэтому имя по умолчанию остаётся: смена имени разлогинила бы всех.


def cookie_name(explicit, *, debug, prod, dev):
    """Имя cookie: из env, иначе своё в dev и штатное на сервере.

    Вынесено в функцию, чтобы правило проверялось тестом: настройки
    вычисляются при импорте, и подменить `DEBUG` в готовом модуле нельзя.
    """
    if explicit:
        return explicit
    return dev if debug else prod


SESSION_COOKIE_NAME = cookie_name(
    env("SESSION_COOKIE_NAME", default=""),
    debug=DEBUG,
    prod="sessionid",
    dev="sportmemo_sessionid",
)
CSRF_COOKIE_NAME = cookie_name(
    env("CSRF_COOKIE_NAME", default=""),
    debug=DEBUG,
    prod="csrftoken",
    dev="sportmemo_csrftoken",
)

# --- Приложения ---
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # Нужно ради штатных шаблонов `sitemap.xml` и `sitemap_index.xml`:
    # без приложения в `INSTALLED_APPS` Django их не найдёт.
    "django.contrib.sitemaps",
    # Регистрирует `SearchVectorField`, `GinIndex` и лукапы `trigram_similar`
    # и `SearchLookup`: без приложения поиск по `tsvector` и триграммам
    # не соберётся.
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "allauth",
    "allauth.account",
    "simple_history",
    "imagekit",
    "storages",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.catalog",
    "apps.photos",
    "apps.comments",
    "apps.moderation",
    # Моделей у приложения нет: поля поиска живут в моделях каталога и фото,
    # а здесь — расширение базы, сборка индекса, сигналы и переиндексация.
    # Стоит после `catalog` и `photos`: сигналы подключаются к их моделям.
    "apps.search",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Вторым, сразу после SecurityMiddleware: заголовок `X-Robots-Tag`
    # должен попасть на все ответы, включая ошибки и `/healthz/`.
    "apps.core.middleware.NoindexAllMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "apps.core.context_processors.project_info",
                "apps.core.context_processors.seo_meta",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- База данных ---
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://sportmemo:sportmemo@localhost:5432/sportmemo",
    )
}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Аутентификация ---
AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
        )
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

SITE_ID = 1

# --- django-allauth: вход по email с обязательным подтверждением ---
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS = 3
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_SESSION_REMEMBER = True
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_LOGOUT_ON_GET = True
ACCOUNT_RATE_LIMITS = {
    "login_failed": "5/5m/key",
    "signup": "10/h/ip",
    "confirm_email": "1/3m/key",
}
ACCOUNT_ADAPTER = "apps.accounts.adapters.AccountAdapter"
# Формы переопределяются ради Bootstrap-классов у полей: шаблоны allauth
# рендерят виджеты как есть.
ACCOUNT_FORMS = {
    "signup": "apps.accounts.forms.SignupForm",
    "login": "apps.accounts.forms.LoginForm",
    "reset_password": "apps.accounts.forms.ResetPasswordForm",
    "reset_password_from_key": "apps.accounts.forms.ResetPasswordKeyForm",
    "change_password": "apps.accounts.forms.ChangePasswordForm",
    "set_password": "apps.accounts.forms.SetPasswordForm",
    "add_email": "apps.accounts.forms.AddEmailForm",
    "reauthenticate": "apps.accounts.forms.ReauthenticateForm",
}

# --- Интернационализация ---
LANGUAGE_CODE = env("DJANGO_LANGUAGE_CODE", default="ru")
LANGUAGES = [
    ("ru", "Русский"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = env("TIME_ZONE", default="Europe/Moscow")
USE_I18N = True
USE_TZ = True

# --- Статика ---
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# --- Файлы: лимиты и хранилища (S3) ---
# Предел распаковки задаёт Pillow, а не форма: файл может прийти мимо формы
# (админка, команда, воркер), и тогда защита от «бомбы сжатия» нужна в одном
# месте. 60 Мпикс — 48 МП телефона плюс запас; скан A4 600 dpi даёт 35 Мпикс.
Image.MAX_IMAGE_PIXELS = 60_000_000

# Задел под пакетную загрузку (10 файлов + запас): Django считает число
# файлов в одном запросе и отклоняет запрос целиком.
DATA_UPLOAD_MAX_NUMBER_FILES = 12

# Два файловых алиаса, а не один: оригинал и превью должны разъехаться
# по разным бакетам, а правило «что где лежит» задаёт модель, не настройка.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "originals": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

USE_S3 = env("USE_S3")
if USE_S3:
    # Ключи `OPTIONS` — имена, которые читает `django-storages`; наши
    # переменные окружения называются `S3_*`. Общая часть вынесена, чтобы
    # два бакета не разошлись в способе подключения.
    _S3_COMMON = {
        "access_key": env("S3_ACCESS_KEY"),
        "secret_key": env("S3_SECRET_KEY"),
        "endpoint_url": env("S3_ENDPOINT"),
        "region_name": env("S3_REGION", default="us-east-1"),
        "use_ssl": env("S3_USE_SSL"),
        "file_overwrite": False,
        "default_acl": None,
    }
    # Публичное чтение и адрес без подписи: подписанная ссылка менялась
    # при каждом показе, ломала `og:image` и кеш браузера.
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            **_S3_COMMON,
            "bucket_name": env("S3_BUCKET", default="sportmemo"),
            "querystring_auth": False,
            "object_parameters": {"CacheControl": "max-age=86400"},
        },
    }
    # Приватный бакет: ссылка на оригинал подписана и живёт час. Политика
    # бакета задаётся на бакет, а не на префикс, поэтому это второй бакет,
    # а не папка внутри первого.
    STORAGES["originals"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            **_S3_COMMON,
            "bucket_name": env("S3_PRIVATE_BUCKET"),
            "querystring_auth": True,
            "querystring_expire": 3600,
        },
    }

# --- Кеш и превью ---
# База `/1`, а не `/0`: нулевая занята брокером Celery. Общая база дала бы
# `cache.clear()`, стирающий очередь задач.
CACHE_URL = env("CACHE_URL", default="redis://localhost:6379/1")
if DEBUG:
    # `make run` не должен требовать Redis: в dev задачи Celery идут синхронно,
    # и один воркер, так что LocMem ведёт себя так же.
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": CACHE_URL,
            "KEY_PREFIX": "sportmemo",
            # Состояние превью не должно истекать: иначе imagekit снова начнёт
            # проверять существование файла в S3 при каждом показе.
            "TIMEOUT": None,
            # Недоступный Redis должен падать быстро, а не держать воркер
            # gunicorn до системного таймаута сокета.
            "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
        }
    }

# Превью строятся по сигналу сохранения источника, а не по требованию:
# стратегия `JustInTime` делала HEAD в S3 на каждом показе до первого
# попадания в кеш. Потерянный файл превью восстанавливает `photos_previews`.
IMAGEKIT_DEFAULT_CACHEFILE_STRATEGY = "imagekit.cachefiles.strategies.Optimistic"

# --- Почта ---
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend"
    if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=465)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_SSL = env("EMAIL_USE_SSL")
# Без таймаута smtplib ждёт соединения до системного предела (минуты).
# Недоступный Postbox держал бы воркер gunicorn и задачу Celery, а на проде
# это кончилось бы 504 у человека.
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=10)
# Отображаемое имя, а не голый адрес: в списке писем человек видит
# отправителя. Ящика noreply@ не существует, ответы идут в info@.
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="SportMemo <info@sportmemo.ru>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
# Контакт для обращений. В 5.5 из этой же переменной возьмётся
# LEGAL_CONTACT_EMAIL оператора ПДн: второй адрес не заводим.
SPORTMEMO_CONTACT_EMAIL = env("CONTACT_EMAIL", default="info@sportmemo.ru")

# --- Celery ---
CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("REDIS_URL", default="redis://localhost:6379/0")
# В dev задачи идут синхронно: Redis ради одной задачи обработки фото
# поднимать не обязательно, а поведение остаётся тем же. На боевом сервере
# `DEBUG=False`, и обработка уходит в воркер. `EAGER_PROPAGATES` здесь
# не ставится: в dev исключение задачи должно уйти в лог, а не в запрос.
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=DEBUG)
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60
# Два воркера, а не по числу ядер: декодирование 48 Мпикс в RGB — это ~144 МБ
# на фото, и на VPS с 4 ГБ памяти воркер по числу ядер падал бы по OOM.
CELERY_WORKER_CONCURRENCY = 2

# --- Логирование ---
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": env("LOG_LEVEL", default="INFO"),
    },
    "loggers": {
        "django.db.backends": {
            "level": "WARNING",
            "handlers": ["console"],
            "propagate": False,
        },
    },
}

# --- Правила проекта (используются в шаблонах и UI) ---
# Фамилии несовершеннолетних не публикуются —
# управляется Person.name_visibility.
SPORTMEMO_PRIVACY_POLICY_VERSION = env("PRIVACY_POLICY_VERSION", default="2026-10-01")
SPORTMEMO_TERMS_VERSION = env("TERMS_VERSION", default="2026-10-01")
SPORTMEMO_CONSENT_PD_VERSION = env("CONSENT_PD_VERSION", default="2026-10-01")
# Регистрация. На стейдже выключена: сервер в Нидерландах, и реальные
# ПДн россиян там хранить нельзя, а без пароля зарегистрируется кто угодно.
SPORTMEMO_SIGNUP_OPEN = env.bool("SIGNUP_OPEN", default=True)
# Закрыть от индексации все ответы: заголовок `X-Robots-Tag` в middleware
# и `Disallow: /` в `robots.txt`. Нужно стейджу, на проде выключено.
SPORTMEMO_NOINDEX_ALL = env.bool("NOINDEX_ALL", default=False)
