# SportMemo — подэтап 5.3: стейдж, репетиция деплоя

Дата создания: 2026-11-16
Статус: **черновик на утверждение** (владелец)
Мастер-план: [`mobile-legal-deploy.md`](mobile-legal-deploy.md) — подэтап **5.3**,
четвёртый в этапе 5
Бэклог: [`.koda/backlog.md`](../backlog.md) — раздел «Этап 5», подэтап 5.3, задача `P5-14`
Ветка: `feature/stage-deploy` (создаётся на шаге 1 от `master`)
База: `master`, версия 0.34.0, 1807 тестов
Финал: bump **0.34.0 → 0.34.1 (patch)** + `uv lock`, строка
`Bump version: 0.34.0 → 0.34.1 (patch)` в теле коммита

> **Для новой сессии.** Подэтапы 5.0, 5.1 и 5.2 в `master`. Работа начинается с шага 1.
> Шаги 1–8 — это код и коммиты; операции на сервере вынесены в раздел 6 отдельно
> (они коммита не дают, и скрипт `run-plan.sh` их не увидит). Отмечать шаг выполненным
> только после `grep`-проверки, что правка реально есть в целевых файлах, и после
> зелёного прогона тестов затронутых файлов.
>
> **Секреты в план и в git не попадают.** `SECRET_KEY` стейджа, пароль basic auth
> и ключи почты живут только в `.env` на сервере. В плане, `.env.example`, README
> и сообщении коммита — только имена переменных и откуда значения берутся.

---

## 1. Цель и границы

**Цель.** Пройти весь путь деплоя на сервере, который не жалко, до того как трогать
боевой. Стейдж — это не «вторая копия для красоты»: на нём проверяется, что
`docker-compose` поднимается на чужом сервере рядом с чужими проектами, что
`collectstatic` даёт рабочую статику, что gunicorn и Celery живут в лимитах памяти,
что загрузка фото доходит до превью, а сервер не падает по OOM.

**Что входит в подэтап:**

- `stage.sportmemo.ru` на `taskman-stage` (89.125.130.69), `DEBUG=False`, `USE_S3=False`;
- `docker-compose.stage.yml` — самодостаточный файл, без наследования dev-файла;
- **basic auth в nginx на весь сайт**: стейдж в интернете, и без пароля там
  зарегистрируется кто угодно со своим реальным email — это ПДн на зарубежном сервере;
- `Disallow: /` в `robots.txt` + заголовок `X-Robots-Tag: noindex, nofollow` на всех
  ответах приложения;
- **регистрация выключена** — через адаптер allauth, а не через несуществующую настройку;
- nginx-эталон в репозитории (`docker/nginx/sportmemo-stage.conf`);
- `scripts/deploy-stage.sh` — воспроизводимый порядок деплоя (сборка, миграции,
  `collectstatic`, запуск) и Makefile-цель для запуска с рабочей машины;
- замеры для ворот: память, диск, время сборки образа, отзывчивость под нагрузкой;
- документация: README (раздел «Стейдж»), `KODA.md` / `AGENTS.md` (правило
  «на стейдже только синтетические данные»), бэклог.

**Что не входит:**

| Отложено | Куда / почему |
|---|---|
| HSTS, `SECURE_SSL_REDIRECT` в Django | 5.7: редирект делает nginx, HSTS включается на боевом домене |
| `gunicorn` в `Dockerfile` (`-w 2`) | 5.7: на стейдже воркеры переопределяются `command:` в compose |
| Свой `postgres:17-alpine` с лимитами и без портов как на проде | 5.3 делает это для стейджа, но политика прода — 5.7 |
| S3 (Yandex Object Storage) | 5.2 код готов, бакеты заводятся при деплое прода (5.7, `P5-13`) |
| Бэкапы и восстановление | 5.4 |
| `collectstatic` внутри `Dockerfile` | `P5-14`: механизм появляется здесь, перенос в образ — 5.7 |
| Мониторинг и алерты | 5.10 |
| Второй стейдж для проверки миграций | не нужен: один сервер, один прогон |

---

## 2. Решения

### 2.1 Стейдж — на существующем `taskman-stage`, отдельным проектом compose

Второй VPS для стейджа **не заводится**: мастер-план уже сказал «второй VPS (Нидерланды,
adminvps.ru)», и это существующий `taskman-stage` (89.125.130.69). На нём уже крутят
TaskMan stage, Lex stage и прод radio_andrews.

**Изоляция от соседей — три уровня:**

1. **Имя проекта compose** `sportmemo-stage` (`name:` в файле): контейнеры получают
   имена `sportmemo-stage-web-1` и не пересекаются с `taskman_web` / `lex-*`.
2. **Порт 8005 на loopback** (`127.0.0.1:8005:8000`): порт наружу не открывается,
   единственная точка входа — nginx на 80/443. Проверено: 8005 на сервере свободен,
   заняты 80, 443, 8001, 8004 (loopback), 8888, 22222.
3. **`mem_limit` у каждого сервиса**: без лимита OOM-киллер на 1.96 ГБ убьёт
   соседний контейнер, а не наш.

**Каталог на сервере:** `~/sportmemo` (рядом с `~/taskman` и `~/lex`).

### 2.2 `docker-compose.stage.yml` — самодостаточный, без `-f docker-compose.yml`

Два способа собрать конфигурацию стейджа:

- **(а) override** `docker compose -f docker-compose.yml -f docker-compose.stage.yml` —
  коротко, но убрать сервис `minio` из базового файла override не умеет: `profiles`
  его не отключает при явном `up -d`, а `deploy.replicas: 0` в compose-режиме без
  swarm не работает. MinIO на стейдже не нужен (`USE_S3=False`) и стоит ~100 МБ RAM,
  которых там нет.
- **(б) самодостаточный файл** — сервисы `db`, `redis`, `web`, `worker` объявлены
  целиком.

**Выбрано (б).** Цена — дублирование сервисных блоков; выгода — стейдж читается
как отдельная конфигурация, и в нём нет сервисов, которых на стейдже быть не должно.
Правило в README: «при изменении сервисов правьте оба файла» — то же, что у
`docker-compose.yml` и будущего `docker-compose.prod.yml` в 5.7.

### 2.3 `USE_S3=False`: локальный `media/` на стейдже

Мастер-план требует именно так: заодно проверяется файловый путь хранилища,
который в dev почти не используется (там MinIO).

**Следствие, которое надо назвать вслух:** приватности оригиналов на стейдже нет.
`Photo.original` лежит в `media/photos/original/` и раздаётся nginx по `/media/`.
Защита — только basic auth и отсутствие реальных данных. Записывается в README,
как и честная строка про dev в 5.2.

### 2.4 Регистрация выключается адаптером, а не настройкой

Мастер-план называет `ACCOUNT_ALLOW_REGISTRATION=False`. **Такой настройки в
`django-allauth` 65.19.7 нет** — проверено `grep` по `.venv/.../allauth/account/`
(совпадений ноль). Штатная точка — `AccountAdapter.is_open_for_signup(request)`
(`allauth/account/adapter.py:304`), её вызывает `CloseableSignupMixin.is_open()`
(`allauth/account/mixins.py:146`).

Реализация:

```python
def is_open_for_signup(self, request):
    return settings.SPORTMEMO_SIGNUP_OPEN
```

`SPORTMEMO_SIGNUP_OPEN` — наша настройка (`SIGNUP_OPEN` в env, по умолчанию `True`).
Она же уходит в контекст шаблонов, чтобы на странице входа не висела ссылка
«Зарегистрироваться», ведущая в тупик.

**Почему своя настройка, а не чтение `settings.ACCOUNT_*` напрямую:** имя
`ACCOUNT_*` — пространство пакета, и в следующей версии allauth может появиться
настоящая `ACCOUNT_ALLOW_REGISTRATION` с другим поведением. Своё имя не конфликтует.

### 2.5 Noindex — middleware плюс `robots.txt`, а не правка каждого представления

Два независимых механизма, потому что закрывают разные вещи:

- **`X-Robots-Tag: noindex, nofollow`** на всех ответах приложения — middleware
  `apps/core/middleware.py::NoindexAllMiddleware` при `SPORTMEMO_NOINDEX_ALL=True`.
  Покрывает и HTML, и статику, и JSON `/healthz/`, и ответы, которых нет в шаблонах.
- **`Disallow: /` в `robots.txt`** — вежливая просьба не ходить вовсе, без `Sitemap:`
  (карта стейджа поисковику не нужна и вредна).

**Почему не через `page_meta(noindex=True)` в каждом представлении:** их больше
двадцати, и новое представление забыло бы флаг. Одна точка на все ответы надёжнее.
`page_meta` не трогаем: у него своя задача — осмысленный `noindex` отдельных страниц.

Флаг читается **внутри `__call__`**, а не в `__init__`: иначе тест с
`override_settings` не смог бы его включить (middleware создаётся один раз при старте).

### 2.6 `collectstatic` — обязательный шаг деплоя, а не улучшение

Проверено фактически при `DEBUG=False`:

```
ValueError: Missing staticfiles manifest entry for 'css/sportmemo.css'
```

`CompressedManifestStaticFilesStorage` требует собранный манифест, а `collectstatic`
не запускается ни в `Dockerfile`, ни в `docker-compose.yml` — это дефект `P5-14`
в бэклоге. На стейдже он блокирующий: без манифеста любая страница с `{% static %}`
отдаёт 500.

**Решение для 5.3:** `collectstatic` вызывается в `scripts/deploy-stage.sh` после
миграций, пишет в смонтированный `./staticfiles`, и nginx раздаёт статику с хоста.
**Почему не в `Dockerfile`:** тогда образ нельзя собрать без `STATIC_ROOT` и без
манифеста внутри, а пересборка статики на каждое изменение CSS шла бы через
пересборку образа. Перенос в образ/entrypoint — решение 5.7, вместе с чек-листом
деплоя (`P5-14` остаётся открытой).

### 2.7 Почта на стейдже настоящая

Мастер-план 5.1 оставил открытым пункт «явная проверка `dmarc=pass` — на стейдже
(5.3)». Значит письма на стейдже **не** переводятся в console: `DEBUG=False` уже
выбирает SMTP-бэкенд, и `manage.py mail_check <адрес>` отправит настоящее письмо.

Риск назван и принят: письма уходят с боевого домена `sportmemo.ru`. Регистрация
закрыта, пользователей нет, а значит единственный источник писем — ручной `mail_check`
и тестовая жалоба. Проверка делается на личный ящик владельца.

### 2.9 Две находки, объяснённые без терминов

Раздел для владельца: обе находки меняют не только код, но и то, что записано
в мастер-плане.

**(а) «Регистрация выключается не той настройкой, которую назвал мастер-план».**
Мастер-план говорит: поставить `ACCOUNT_ALLOW_REGISTRATION = False`. Такой
настройки в нашей версии библиотеки входов (`django-allauth` 65.19.7) **нет** —
если её написать, Django не упадёт, но и ничего не выключит: настройка просто
останется лежать в файле, а регистрация продолжит работать. Кнопка «выключить
регистрацию» в этой версии — метод `is_open_for_signup` в нашем адаптере
(`apps/accounts/adapters.py`). Мы его переопределяем и возвращаем значение
нашей настройки `SPORTMEMO_SIGNUP_OPEN` (в env — `SIGNUP_OPEN`).

Что это значит практически: на проде `SIGNUP_OPEN=True`, на стейдже `False`,
и страница регистрации отвечает «Регистрация закрыта», а в базе не появляется
ни одного человека. Без этой правки на стейдже через сутки были бы чужие
реальные email — то есть ПДн на зарубежном сервере.

**(б) «Сайт без `collectstatic` отдаёт 500 на каждой странице».**
В бою (`DEBUG=False`) Django отдаёт статику — CSS, шрифты, Bootstrap — по
адресам с отпечатком содержимого: `sportmemo.38e69a557cfb.css`. Отпечаток
нужен, чтобы браузер мог кешировать файл навсегда: изменился файл — изменился
адрес. Список «файл → адрес с отпечатком» называется манифестом, и его создаёт
команда `collectstatic`. Сейчас эта команда не запускается нигде: ни в
`Dockerfile`, ни в `docker-compose.yml` (это и есть дефект `P5-14` в бэклоге).

Что происходит без неё (проверено запуском, не предположено):

```
ValueError: Missing staticfiles manifest entry for 'css/sportmemo.css'
```

Шаблон не может построить адрес CSS → страница падает с 500. То есть стейдж
не «выглядел бы некрасиво», а **не открылся бы вообще**. Поэтому `collectstatic`
стоит в скрипте деплоя отдельным обязательным шагом.

Почему не кладём его в `Dockerfile` сразу: тогда образ нельзя собрать без
собранной статики, а каждая правка CSS требовала бы пересборки образа. Перенос
в образ и чек-лист деплоя — задача 5.7; `P5-14` остаётся открытой, но на стейдже
дыры уже нет.

### 2.10 `mem_limit` — потолок, а не резервация

Сумма лимитов стейджа (`db 256 + redis 96 + web 384 + worker 512 = 1248 МБ`)
больше доступной памяти (968 МБ). Это осознанно: `mem_limit` — верхняя граница
для одного контейнера, а не занятая память. В простое четыре сервиса занимают
~350–400 МБ. Смысл лимита — не дать одному распухшему контейнеру (например,
воркеру на фото 48 Мпикс) съесть память соседнего проекта. Если контейнер
упирается в свой лимит, OOM-киллер убивает его, а не TaskMan рядом.

### 2.11 Домен сайта на стейдже правится командой, а не миграцией

Data-миграция `core/0003_site_domain` ставит `sportmemo.ru` жёстко, и это правильно:
миграция не должна зависеть от env, иначе база перестанет быть воспроизводимой.
На стейдже домен меняется одной командой в скрипте деплоя:

```bash
manage.py shell -c "from django.contrib.sites.models import Site; \
  Site.objects.filter(pk=1).update(domain='stage.sportmemo.ru', name='SportMemo (stage)')"
```

Без этого `og:url`, `canonical` и ссылки в письмах вели бы на прод-домен, то есть
стейдж проверял бы не то, что показывает.

---

## 3. Факты, проверенные при планировании

Каждый пункт подтверждён кодом, чтением библиотеки или запуском команды.

1. **[x] `stage.sportmemo.ru` резолвится. Проверено 2026-11-16.** `dig +short
   stage.sportmemo.ru A` → `89.125.130.69`. NS — `ns1.r01.ru`, `ns2.r01.ru`.
   A-запись создана без точки на конце: R01 принимает такой формат (уточнение
   го́тчи 5.1 — точка требовалась для MX и CNAME, для A-записи это не так).
2. **`ACCOUNT_ALLOW_REGISTRATION` в allauth 65.19.7 отсутствует** (`grep` по пакету →
   ноль совпадений). Точка выключения — `is_open_for_signup()`. Разбор простыми
   словами — решение 2.9(а).
3. **Шаблон закрытой регистрации есть в пакете** (`account/signup_closed.html`),
   русский перевод тоже: «Регистрация закрыта» / «Мы сожалеем, но в текущий момент
   регистрация закрыта.» Проверено `gettext`-ом на каталоге пакета. Свой шаблон
   всё равно нужен: пакетный не наследует наш каркас.
4. **`collectstatic` не запускается нигде** — `P5-14`. При `DEBUG=False` без манифеста
   `static()` бросает `ValueError: Missing staticfiles manifest entry`. Разбор
   простыми словами — решение 2.9(б).
5. **`static()` возвращает `/static/css/sportmemo.<hash>.css`** — ведущий слэш есть,
   несмотря на `STATIC_URL = "static/"`. Значит nginx-локация `/static/` совпадёт.
6. **Сервер `taskman-stage`:** 1 vCPU, 1963 МБ RAM (занято 785, available 968),
   swap 1023 МБ (занято 203, свободно 820), диск 15 ГБ — свободно **3.0 ГБ (79%)**.
7. **Порты на stage заняты:** 80, 443, 8001 (TaskMan), 8004 (Lex, loopback),
   8888 (tinyproxy), 22222 (SSH). **8005 свободен.**
8. **Инструменты на stage:** `docker 29.6.1`, `docker compose v5.3.1`, `git`, `make`,
   `certbot`, `openssl` (`passwd -apr1` работает). **`htpasswd` нет**, `rclone` нет,
   `pg_dump` нет (последние два — 5.4).
9. **`sudo` на stage требует пароль** (`sudo: a password is required`). Значит правка
   `/etc/nginx/` и `certbot` — действия владельца, а не шага плана.
10. **Доступы с сервера:** GitHub по SSH отвечает (`Hi Onoiro!`), `gitverse.ru` → 307,
    `ghcr.io` и `registry-1.docker.io` → 401 (это норма: реестр отвечает, анонимного
    доступа нет). Образ `python:3.12-slim` и `ghcr.io/astral-sh/uv` скачаются.
11. **Кеш сборки Docker на stage — 1.228 ГБ, образы 1.601 ГБ (468 МБ reclaimable).**
    `docker builder prune -f` освободит ~1.2 ГБ и это безопасно для работающих
    контейнеров; соседние проекты пересоберутся медленнее в следующий раз.
12. **`uid` пользователя `abo` = 1000**, совпадает с `appuser` в `Dockerfile`. Значит
    файлы, созданные контейнером в смонтированных `media/` и `staticfiles/`, будут
    принадлежать `abo` — без `chown` и без гонки прав.
13. **`/home/abo` имеет права `drwx--x--x`**: `x` для others есть, поэтому nginx
    (`www-data`) сможет пройти по известному пути в `~/sportmemo/staticfiles`.
    Файлам всё равно нужен `o+rX` — в скрипте деплоя стоит `chmod -R o+rX`
    (та же готча, что в TaskMan).
14. **Лимиты загрузки:** `MAX_UPLOAD_BYTES = 25 МБ`, `MAX_SIDE_PX = 10 000`. Значит
    `client_max_body_size 30m` в nginx обязателен: дефолт 1 МБ даёт 413.
15. **`seed_demo` прогоняет фото через настоящий `process_photo()`** (строка 1007):
    демо-архив на стейдже проверяет пайплайн, а не только рендеринг.
16. **Демо-креды публично известны:** `demo@example.com` / `demo-password-12345`.
    За basic auth это приемлемо, но записывается в README явно.
17. **`check --deploy` при `DEBUG=False` даёт три предупреждения:** HSTS не задан,
    `SECURE_SSL_REDIRECT` не True, `SECRET_KEY` короткий. Первые два — осознанно
    (nginx + 5.7), третье лечится настоящим ключом в `.env` стейджа.

---

## 4. Что требуется лично от владельца

Раздел вынесен отдельно, потому что эти действия не выполняет агент: DNS, `sudo`
и пароли — зона владельца.

### 4.1 До начала реализации (блокеры)

1. **[x] A-запись `stage.sportmemo.ru` → `89.125.130.69` в панели r01.ru. Сделано
   2026-11-16.** Хост — `stage`, значение — IP **без точки на конце**: R01 принимает
   такой формат и дописывает зону сам; вариант с точкой форма не пропускает
   (уточнение го́тчи 5.1, где точка требовалась для MX и CNAME — для A-записи
   это не так). Проверено: `dig +short stage.sportmemo.ru A` → `89.125.130.69`.

2. **[x] Кеш сборки Docker на stage чистить не нужно. Проверено 2026-11-16.**
   `docker builder prune -f` → `Total: 0B`, а `docker system df` показывает
   `Build Cache 1.228GB, RECLAIMABLE 0B`: весь кеш помечен используемым, и prune
   его не отдаёт. Идём с 3.0 ГБ свободных. Если сборка упрётся в диск — см.
   риск «диск кончился» в разделе 8.

3. **[x] Порт 8005 и каталог `~/sportmemo` на `taskman-stage`. Проверено
   2026-11-16.** Порт свободен (заняты 80, 443, 8001, 8004 loopback, 8888, 22222).
   **Правило в `ufw` не нужно:** контейнер публикует порт как `127.0.0.1:8005:8000`,
   то есть слушает только loopback и снаружи недоступен; наружу открыты 80/443,
   и `ufw` (активен, `ENABLED=yes`) их уже пропускает. **`mkdir` не нужен:**
   каталог создаёт `git clone` на шаге 6.1.

4. **[x] Подтверждено владельцем 2026-11-16:** реальных ПДн в базе SportMemo
   на стейдже не будет, только `seed_demo`.

5. **[x] Логин и пароль basic auth получены 2026-11-16** (логин `sportmemo`).
   В план, README и git пароль **не записывается** — только в файл
   `/etc/nginx/.htpasswd_sportmemo_stage` на сервере. Пароль передан в переписке,
   поэтому считается «засветившимся»: при желании сменить одной командой (6.3).
   Пароль в чат больше не выводим, в командах он подставляется переменной
   `STAGE_PASS`.

6. **[x] `.env` стейджа создаёт владелец.** Шаблон значений — раздел 6.0,
   секретов в нём нет, кроме `SECRET_KEY`, который владелец генерирует сам
   (`openssl rand -hex 32`).

### 4.2 В процессе (одноразовые команды с `sudo`)

7. **Создать файл паролей basic auth** (после первого клонирования, шаг 6.1):

   ```bash
   printf 'sportmemo:%s\n' "$(openssl passwd -apr1 'ВАШ_ПАРОЛЬ')" \
     | sudo tee /etc/nginx/.htpasswd_sportmemo_stage >/dev/null
   sudo chmod 640 /etc/nginx/.htpasswd_sportmemo_stage
   sudo chown root:www-data /etc/nginx/.htpasswd_sportmemo_stage
   ```

8. **Положить nginx-эталон и включить сайт** (эталон берётся из репозитория,
   не пишется руками):

   ```bash
   sudo cp ~/sportmemo/docker/nginx/sportmemo-stage.conf \
            /etc/nginx/conf.d/stage.sportmemo.ru.conf
   sudo nginx -t && sudo systemctl reload nginx
   ```

9. **Выпустить сертификат** — A-запись уже резолвится, блокера нет:

   ```bash
   sudo certbot --nginx -d stage.sportmemo.ru
   ```

   Порядок важен: `certbot --nginx` вставляет свой `location` для http-01, но
   basic auth на уровне `server` закрыл бы и его. В эталоне на 80-м сервере
   `location ^~ /.well-known/acme-challenge/ { auth_basic off; }` стоит заранее —
   без него выпуск провалится с 401. Второй вариант, если `--nginx` капризничает:
   `sudo certbot certonly --webroot -w /home/abo/sportmemo -d stage.sportmemo.ru`
   и прописать пути к сертификату в конфиге руками.

10. **Перезагрузить nginx после выпуска** и убедиться, что редирект на HTTPS
    появился: `sudo nginx -t && sudo systemctl reload nginx`.

11. **Проверить соседние проекты после включения сайта** — это ваша зона
    ответственности, потому что сервер общий:
    `https://staging.taskman.tech`, `https://stage.lextr.ru`,
    `https://andrews.2-way.ru` → 200.

12. **Первый вход в браузере** с basic-auth паролем: главная, страница фото,
    кабинет демо-пользователя, админка. Скриншот или список замечаний в чат.

13. **Загрузить одно фото с телефона** (или из браузера) через `/photos/upload/`:
    проверить кроп, второй шаг, что превью появилось, а плашка в кабинете стала
    «На проверке». Это единственная проверка, которую нельзя сделать curl-ом.

14. **Проверка почты и DMARC:** назвать личный адрес, на который отправить письмо
    (`manage.py mail_check <адрес>`), и прислать заголовки письма — нужны строки
    `spf=pass`, `dkim=pass`, `dmarc=pass`. Это закрывает открытый пункт 5.1.

### 4.3 После реализации (решение по воротам)

15. **Решить ворота «годится ли стейдж и текущий VPS»** по замерам из раздела 7.
    Вопросы: хватает ли памяти при загрузке фото, сколько занимает образ, сколько
    свободного диска осталось, не убило ли соседей. Решение записывается в план
    и в мастер-план: «годится» / «расширить диск стейджа» / «пересмотреть лимиты 5.7».

16. **Утвердить правило про синтетику** (в `KODA.md` / `AGENTS.md`): на стейдже
    никогда не появляются реальные email, имена и фото из личного архива. Если
    появились — стереть и разобраться, как попали.

17. **Решить судьбу доступа к стейджу.** Предложение: basic auth остаётся навсегда,
    а пароль меняется при утечке. Стейдж не индексируется и не должен быть
    публичным, даже когда архив станет большим.

---

## 5. Шаги (код и коммиты)

### 1. [x] Ветка

1. `git status` — дерево чистое (допускается только файл этого плана, он
   коммитится до шага 1).
2. `git switch master`.
3. `git switch -c feature/stage-deploy`.
4. Проверка: `git branch --show-current` → `feature/stage-deploy`.

Файлы: —

### 2. [x] Настройки: закрытая регистрация и noindex-режим

1. `config/settings.py`, рядом с правилами проекта (блок `SPORTMEMO_*` в конце):
   ```python
   # Регистрация. На стейдже выключена: сервер в Нидерландах, и реальные
   # ПДн россиян там хранить нельзя, а без пароля зарегистрируется кто угодно.
   SPORTMEMO_SIGNUP_OPEN = env.bool("SIGNUP_OPEN", default=True)
   # Закрыть от индексации все ответы: заголовок `X-Robots-Tag` в middleware
   # и `Disallow: /` в `robots.txt`. Нужно стейджу, на проде выключено.
   SPORTMEMO_NOINDEX_ALL = env.bool("NOINDEX_ALL", default=False)
   ```
2. В `MIDDLEWARE` добавить `"apps.core.middleware.NoindexAllMiddleware"` **вторым**,
   сразу после `SecurityMiddleware`: заголовок должен попасть на все ответы,
   включая те, что ниже по стеку, и на ответы об ошибках.
3. Проверка: `uv run python manage.py check` → без ошибок.

Файлы: `config/settings.py`

### 3. [x] Закрытая регистрация

1. `apps/accounts/adapters.py`:
   ```python
   def is_open_for_signup(self, request):
       """Открыта ли регистрация.

       В allauth 65 настройки `ACCOUNT_ALLOW_REGISTRATION` нет: точка
       выключения — этот метод (`allauth/account/mixins.py:146`).
       """
       return settings.SPORTMEMO_SIGNUP_OPEN
   ```
   Импорт `from django.conf import settings` — на уровне модуля.
2. `templates/account/signup_closed.html` — новый шаблон на нашем каркасе:
   ```django
   {% extends "account/base_entrance.html" %}
   {% load i18n allauth %}
   {% block head_title %}{% trans "Регистрация закрыта" %}{% endblock %}
   {% block content %}
       <div class="page-header">
           {% element h1 %}{% trans "Регистрация закрыта" %}{% endelement %}
           {% element p %}
               {% trans "Сейчас регистрация на сайте закрыта. Если у вас есть архив, напишите нам — мы свяжемся и поможем." %}
           {% endelement %}
       </div>
       <div class="auth-links">
           <a href="{% url 'account_login' %}">{% trans "Войти" %}</a>
           <a href="{% url 'core:home' %}">{% trans "На главную" %}</a>
       </div>
   {% endblock content %}
   ```
   Адрес для связи берётся из `SPORTMEMO_CONTACT_EMAIL`? **Нет**: `mailto:` в тексте
   через `{% trans %}` не собрать, а хардкод адреса в шаблоне разошёлся бы с env.
   Формулировка «напишите нам» ведёт на `/rules/`, где адрес уже выводится из
   настроек. Правка: ссылка на `core:rules`, текст «напишите нам — контакты в правилах».
3. `apps/core/context_processors.py`, `project_info`: добавить
   `"SPORTMEMO_SIGNUP_OPEN": settings.SPORTMEMO_SIGNUP_OPEN`.
4. `templates/account/login.html`: ссылку «Зарегистрироваться» обернуть в
   `{% if SPORTMEMO_SIGNUP_OPEN %}…{% endif %}`. Ссылка «Забыли пароль?» остаётся:
   сброс пароля работает и при закрытой регистрации.
5. Проверка: `grep -n "is_open_for_signup" apps/accounts/adapters.py`;
   `grep -n "SPORTMEMO_SIGNUP_OPEN" templates/account/login.html`.

Файлы: `apps/accounts/adapters.py`, `templates/account/signup_closed.html`,
`apps/core/context_processors.py`, `templates/account/login.html`

### 4. [x] Noindex-режим

1. `apps/core/middleware.py` — новый модуль:
   ```python
   """Middleware режима «закрыто от индексации» (стейдж)."""

   from django.conf import settings


   class NoindexAllMiddleware:
       """`X-Robots-Tag` на всех ответах при `SPORTMEMO_NOINDEX_ALL`.

       Флаг читается в `__call__`, а не в `__init__`: middleware создаётся
       один раз при старте, и значение из `override_settings` иначе
       не подхватилось бы в тестах.
       """

       def __init__(self, get_response):
           self.get_response = get_response

       def __call__(self, request):
           response = self.get_response(request)
           if settings.SPORTMEMO_NOINDEX_ALL:
               response["X-Robots-Tag"] = "noindex, nofollow"
           return response
   ```
2. `apps/core/views.py`, `robots_txt`: передать в контекст
   `"disallow_all": settings.SPORTMEMO_NOINDEX_ALL` (импорт `settings`).
3. `templates/robots.txt`: при `disallow_all` — только `User-agent: *` и
   `Disallow: /`, без перечисления разделов и **без `Sitemap:`**:
   ```django
   {% if disallow_all %}User-agent: *
   Disallow: /
   {% else %}…прежний текст…{% endif %}
   ```
4. Проверка: `grep -n "disallow_all" templates/robots.txt apps/core/views.py`.

Файлы: `apps/core/middleware.py`, `apps/core/views.py`, `templates/robots.txt`

### 5. [x] Тесты режимов стейджа

1. `tests/test_stage_mode.py` — новый файл, два класса:
   - `TestClosedSignup`:
     - при `SPORTMEMO_SIGNUP_OPEN = False` GET `/accounts/signup/` → 200 и текст
       «Регистрация закрыта»;
     - POST на `/accounts/signup/` не создаёт пользователя и не отправляет письмо;
     - на странице входа нет ссылки «Зарегистрироваться», «Забыли пароль?» есть;
     - при `True` (по умолчанию) регистрация работает — существующий тест
       `test_signup_sends_email_and_creates_user` остаётся зелёным.
   - `TestNoindexAll`:
     - при `SPORTMEMO_NOINDEX_ALL = True` ответ главной несёт
       `X-Robots-Tag: noindex, nofollow`, и `/healthz/` тоже;
     - `robots.txt` содержит `Disallow: /` и **не** содержит `Sitemap:`;
     - при `False` заголовка нет, `robots.txt` прежний (проверка на список разделов).
2. Го́тча: `override_settings` на `SPORTMEMO_SIGNUP_OPEN` работает, потому что
   адаптер читает настройку при каждом запросе. Для `robots.txt` то же.
3. Прогнать: `uv run pytest tests/test_stage_mode.py tests/test_auth_ui.py tests/test_seo.py -q`.

Файлы: `tests/test_stage_mode.py`

### 6. [x] `docker-compose.stage.yml`

1. Новый файл, самодостаточный (`name: sportmemo-stage`), сервисы `db`, `redis`,
   `web`, `worker`. Обязательные отличия от dev-файла:

   | Что | Значение | Почему |
   |---|---|---|
   | `db.ports` | не публикуются | порты наружу не нужны, nginx — единственная точка входа |
   | `db.mem_limit` | `256m` | OOM-киллер на 1.96 ГБ убьёт соседа |
   | `redis.ports` | не публикуются | то же |
   | `redis.mem_limit` | `96m` | кеш превью + брокер |
   | `web.command` | `gunicorn -w 2 --max-requests 500 --timeout 60 -b 0.0.0.0:8000 …` | 1 vCPU: 4 воркера — лишние ~400 МБ |
   | `web.ports` | `127.0.0.1:8005:8000` | только loopback, наружу — nginx |
   | `web.mem_limit` | `384m` | |
   | `web.volumes` | `./media:/app/media`, `./staticfiles:/app/staticfiles` | `USE_S3=False`; статику раздаёт nginx с хоста |
   | `worker.command` | `celery -A config worker -l info --concurrency=1 --max-tasks-per-child=50` | утечки Pillow, 1 vCPU |
   | `worker.mem_limit` | `512m` | пик декодирования 48 Мпикс ~144 МБ + запас |
   | `minio` | сервиса нет | `USE_S3=False` |
   | `env_file` | `.env` | секреты не в git, один файл на сервере |
   | `environment` | `DEBUG=False`, `ALLOWED_HOSTS=stage.sportmemo.ru`, `CSRF_TRUSTED_ORIGINS=https://stage.sportmemo.ru`, `USE_S3=False`, `SIGNUP_OPEN=False`, `NOINDEX_ALL=True`, `REDIS_URL`, `CACHE_URL`, `CELERY_WORKER_CONCURRENCY=1` | репетиция прода |
   | `logging` | `max-size: 10m`, `max-file: 3` | диск 15 ГБ на всех |
   | `restart` | `unless-stopped` | |

2. **`code` внутрь не монтируется** (`.:/app` нет): образ содержит код, а `.env`
   приходит через `env_file`. Иначе правка файла на хосте меняла бы работающий
   контейнер, и «что задеплоено» перестало бы быть известным.
3. Проверка: `docker compose -f docker-compose.stage.yml config >/dev/null` →
   без ошибок подстановки (нужен `.env`; при его отсутствии — проверить с
   `env_file` временно закомментированным, либо скопировать `.env.example`).

Файлы: `docker-compose.stage.yml`

### 7. [x] nginx-эталон

1. `docker/nginx/sportmemo-stage.conf` — один файл, два `server`-блока:
   - **80**: `location ^~ /.well-known/acme-challenge/ { auth_basic off; root …; }`,
     всё остальное — `return 301 https://$host$request_uri;`;
   - **443**: `ssl_certificate`/`ssl_certificate_key` из `/etc/letsencrypt/live/`,
     `client_max_body_size 30m`, `auth_basic "SportMemo stage"`,
     `auth_basic_user_file /etc/nginx/.htpasswd_sportmemo_stage`,
     `location /static/ { alias /home/abo/sportmemo/staticfiles/; expires max; access_log off; }`,
     `location /media/ { alias /home/abo/sportmemo/media/; expires 7d; access_log off; }`,
     `location / { proxy_pass http://127.0.0.1:8005; proxy_set_header Host $host;
     proxy_set_header X-Real-IP $remote_addr; proxy_set_header X-Forwarded-For
     $proxy_add_x_forwarded_for; proxy_set_header X-Forwarded-Proto $scheme;
     proxy_read_timeout 60s; }`;
   - `X-Forwarded-Proto` обязателен: при `DEBUG=False` включён
     `SECURE_PROXY_SSL_HEADER`, и без него Django считает запрос http, а
     `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE` ломают вход и формы.
2. Шапка файла — комментарий: «Эталон. На сервере лежит копия в
   `/etc/nginx/conf.d/stage.sportmemo.ru.conf`, правится здесь и копируется,
   а не наоборот».
3. Проверка: `grep -c "server {" docker/nginx/sportmemo-stage.conf` → 2;
   `grep -n "auth_basic\|client_max_body_size\|X-Forwarded-Proto" …`.

Файлы: `docker/nginx/sportmemo-stage.conf`

### 8. [x] Скрипт деплоя, `.env.example`, Makefile

1. `scripts/deploy-stage.sh` (исполняемый, `chmod +x`):
   ```bash
   #!/usr/bin/env bash
   # Деплой стейджа. Запускается на сервере из корня репозитория.
   set -euo pipefail
   cd "$(dirname "$0")/.."
   DC="docker compose -f docker-compose.stage.yml"

   $DC build
   $DC up -d db redis
   $DC run --rm web python manage.py migrate --noinput
   $DC run --rm web python manage.py collectstatic --noinput
   $DC run --rm web python manage.py shell -c \
     "from django.contrib.sites.models import Site; Site.objects.filter(pk=1).update(domain='stage.sportmemo.ru', name='SportMemo (stage)')"
   $DC up -d web worker
   # nginx работает от www-data: файлы, созданные контейнером, должны читаться
   chmod -R o+rX staticfiles media
   $DC ps
   ```
2. `Makefile`: цель `stage-deploy` — запуск с рабочей машины:
   ```make
   stage-deploy: ## Задеплоить стейдж (git pull + сборка + миграции)
   	ssh taskman-stage 'cd ~/sportmemo && git pull --ff-only && ./scripts/deploy-stage.sh'
   ```
   Добавить в `.PHONY`.
3. `.env.example`: добавить два блока с комментариями (значения dev):
   ```
   # --- Режимы стейджа ---
   # Регистрация: на стейдже False (сервер за границей, реальные ПДн нельзя).
   SIGNUP_OPEN=True
   # Закрыть всё от индексации: X-Robots-Tag и Disallow: / в robots.txt.
   NOINDEX_ALL=False
   ```
4. Проверка: `grep -n "stage-deploy" Makefile`; `bash -n scripts/deploy-stage.sh`.

**Го́тча Makefile.** В этом проекте уже был случай, когда инструмент вставил
пробелы вместо TAB и `make` упал с `missing separator`. Проверить:
`sed -n '/stage-deploy/,+1p' Makefile | cat -A` → строка начинается с `^I`.

Файлы: `scripts/deploy-stage.sh`, `Makefile`, `.env.example`

### 9. [x] Документация, бэклог, проверка, коммит

1. `README.md`, новый раздел «Стейдж» (после «Хранилище», до «Почта и домен»):
   - адрес, сервер, порт 8005, имя проекта compose;
   - **basic auth**: зачем, где файл паролей, как сменить пароль;
   - порядок деплоя (`make stage-deploy`) и что делает скрипт;
   - `USE_S3=False` и честная строка: **приватности оригиналов на стейдже нет**,
     защита — basic auth и отсутствие реальных данных;
   - **только синтетические данные**: `seed_demo`, реальные email, имена и фото
     не попадают никогда (сервер в Нидерландах, 152-ФЗ);
   - демо-креды `demo@example.com` / `demo-password-12345` и почему это приемлемо;
   - `collectstatic` обязателен при `DEBUG=False` (иначе 500 на `{% static %}`),
     и это же — причина, по которой `P5-14` остаётся открытой;
   - регистрация закрыта адаптером `is_open_for_signup` — не
     `ACCOUNT_ALLOW_REGISTRATION` (такой настройки в allauth 65 нет);
   - `NOINDEX_ALL=True`: `X-Robots-Tag` и `Disallow: /`;
   - почта настоящая: `mail_check <адрес>`, проверка `dmarc=pass`.
2. `KODA.md` / `AGENTS.md`, «Правила для кода» — четыре правила:
   - «На стейдже только синтетические данные: реальные ПДн россиян на зарубежном
     сервере нарушают 152-ФЗ. Демо-архив создаётся `seed_demo`»;
   - «Регистрация выключается `AccountAdapter.is_open_for_signup`, а не настройкой
     `ACCOUNT_ALLOW_REGISTRATION`: в allauth 65 её нет»;
   - «`collectstatic` обязателен при `DEBUG=False`: `CompressedManifestStaticFilesStorage`
     без манифеста даёт 500 на любой `{% static %}`»;
   - «Стейдж закрыт basic auth в nginx и `X-Robots-Tag: noindex`: доступ и индексация
     задаются на сервере, а не в коде страниц».
3. `.koda/backlog.md`: 5.3 → «готово (0.34.1)»; `P5-14` — дописать, что механизм
   появился в 5.3 (шаг деплоя), а перенос в образ/чек-лист остаётся за 5.7.
4. `make lint` → чисто. `make test` → все зелёные (было 1807, станет больше).
5. Активировать навык `semver-checker`: bump **0.34.0 → 0.34.1 (patch)**, `uv lock`.
6. Коммит: `feat: add stage deployment setup with closed signup and noindex`
   + тело с причиной (стейдж нужен до прода; без закрытой регистрации на зарубежном
   сервере появятся реальные ПДн; без `collectstatic` сайт отдаёт 500)
   + строка версии.
7. Отметить 5.3 в мастер-плане и в таблице бэклога, записать итог и замеры.

Файлы: `README.md`, `KODA.md`, `AGENTS.md`, `.koda/backlog.md`,
`.koda/plans/mobile-legal-deploy.md`, `pyproject.toml`, `uv.lock`

---

## 6. Операции на сервере (после шага 9, коммита не дают)

Выполняются агентом по SSH от `abo` там, где не нужен `sudo`, и владельцем — где нужен.

### 6.0 Шаблон `.env` для стейджа (создаёт владелец)

Файл `~/sportmemo/.env` на сервере. Секретов в шаблоне нет, кроме `SECRET_KEY`,
который владелец генерирует сам. Значения, помеченные `<…>`, подставляет владелец.

```dotenv
# --- Django ---
DEBUG=False
# openssl rand -hex 32
SECRET_KEY=<сгенерировать>
ALLOWED_HOSTS=stage.sportmemo.ru
CSRF_TRUSTED_ORIGINS=https://stage.sportmemo.ru
DJANGO_LANGUAGE_CODE=ru

# --- Режимы стейджа ---
# Регистрация выключена: сервер в Нидерландах, реальные ПДн россиян там нельзя.
SIGNUP_OPEN=False
# Всё закрыто от индексации: X-Robots-Tag и Disallow: / в robots.txt.
NOINDEX_ALL=True

# --- База данных (имена сервисов compose: db, redis) ---
DATABASE_URL=postgres://sportmemo:<пароль_бд>@db:5432/sportmemo
REDIS_URL=redis://redis:6379/0
CACHE_URL=redis://redis:6379/1
POSTGRES_DB=sportmemo
POSTGRES_USER=sportmemo
POSTGRES_PASSWORD=<пароль_бд>
# openssl rand -hex 16
CONN_MAX_AGE=60

# --- Хранилище: на стейдже локальный media/, без S3 ---
USE_S3=False

# --- Почта: настоящая, через Postbox (проверка dmarc=pass) ---
EMAIL_HOST=postbox.cloud.yandex.net
EMAIL_PORT=465
EMAIL_USE_SSL=True
EMAIL_TIMEOUT=10
# Идентификатор API-ключа сервисного аккаунта postbox-user, НЕ адрес ящика.
EMAIL_HOST_USER=<идентификатор_ключа>
# Секретная часть API-ключа, НЕ пароль от ящика.
EMAIL_HOST_PASSWORD=<секрет_ключа>
DEFAULT_FROM_EMAIL=SportMemo <info@sportmemo.ru>
CONTACT_EMAIL=info@sportmemo.ru

# --- Логи ---
LOG_LEVEL=INFO
```

Проверка после создания: `grep -c "=" ~/sportmemo/.env` и что `SECRET_KEY` длиннее
50 символов (`manage.py check --deploy` не должен ругаться на `security.W009`).

### 6.1 Первый подъём

**[x] Выполнено 2026-11-18.** Что выяснилось на практике и чего в плане не было:

- **Два образа вместо одного.** У `web` и `worker` стоял `build: .`, и Docker
  собрал один и тот же образ дважды: 3.3 ГБ вместо 1.65. На диске 15 ГБ первая
  сборка упала с `no space left on device`. Исправлено (0.34.2): `web` собирает,
  `worker` переиспользует через `image: sportmemo-stage:latest`.
- **`docker builder prune -f` не освобождает ничего** (`Total: 0B`, кеш помечен
  используемым). Освобождает `docker builder prune -af`: 2.7 ГБ в первый раз
  и 1.59 ГБ после сборки. После деплоя кеш накапливается снова, поэтому чистка
  после сборки — часть ритуала, а не разовое действие.
- **`seed_demo` требует `--force` при `DEBUG=False`** — штатная защита от запуска
  на боевой базе. На стейдже: `manage.py seed_demo --force`.
- **Время сборки образа:** ~6 минут (1 vCPU), из них 190 с — экспорт слоёв.
- **`manage.py` не в PATH** на сервере: все команды через
  `docker compose -f docker-compose.stage.yml run --rm web python manage.py …`.

1. `ssh taskman-stage` → `cd ~` → `git clone git@github.com:Onoiro/sportmemo.git`
   (ключ уже настроен: GitHub отвечает `Hi Onoiro!`). Каталог `~/sportmemo`
   создаётся клоном, `mkdir` не нужен.
2. Владелец кладёт `~/sportmemo/.env` по шаблону 6.0.
3. `cd ~/sportmemo && time ./scripts/deploy-stage.sh` — **время записать**, это
   замер для ворот.
4. Владелец: файл паролей basic auth, копия nginx-эталона, `nginx -t`, `reload`,
   `certbot --nginx -d stage.sportmemo.ru`, ещё раз `reload` (раздел 4.2, пункты 7–10).

### 6.2 Наполнение синтетикой

6. `docker compose -f docker-compose.stage.yml run --rm web python manage.py seed_demo`
   — 10 фото проходят настоящий `process_photo()`, то есть проверяется пайплайн.
7. `… run --rm web python manage.py createsuperuser` — для админки (логин владельца).
8. `… run --rm web python manage.py photos_previews --dry-run` → 0 к пересборке.

### 6.3 Проверки

Пароль подставляется переменной, в план и в историю терминала он не попадает:
`read -rs STAGE_PASS` (или экспорт из менеджера паролей). Дальше `-u sportmemo:$STAGE_PASS`.

| Что | Команда | Ожидание |
|---|---|---|
| HTTP → HTTPS | `curl -sI http://stage.sportmemo.ru` | `301`, `Location: https://…` |
| Basic auth | `curl -sI https://stage.sportmemo.ru` | `401` |
| С паролем | `curl -sI -u sportmemo:$STAGE_PASS https://stage.sportmemo.ru` | `200` |
| Noindex-заголовок | `curl -sI -u … https://stage.sportmemo.ru \| grep -i x-robots` | `noindex, nofollow` |
| robots | `curl -s -u … https://stage.sportmemo.ru/robots.txt` | `Disallow: /`, без `Sitemap:` |
| Здоровье | `… /healthz/` | `{"app": "ok", "db": "ok"}` |
| Регистрация закрыта | `… /accounts/signup/ \| grep -c "Регистрация закрыта"` | `1` |
| Статика | `… /static/css/sportmemo.<hash>.css` | `200`, `text/css` |
| Медиа | страница фото в браузере | превью на месте |
| Почта | `… run --rm web python manage.py mail_check <адрес>` | письмо дошло, `dmarc=pass` |
| Превью | `… photos_previews --dry-run` | 0 |

**Смена пароля basic auth** (если понадобится):

```bash
read -rs NEW_PASS
printf 'sportmemo:%s\n' "$(openssl passwd -apr1 "$NEW_PASS")" \
  | sudo tee /etc/nginx/.htpasswd_sportmemo_stage >/dev/null
sudo nginx -t && sudo systemctl reload nginx
```

### 6.4 Замеры для ворот

9. `free -m`, `docker stats --no-stream`, `df -h /`, `docker system df` — записать.
10. **Нагрузка:** десять параллельных запросов главной с паролем, замерить время:
    ```bash
    time (for i in $(seq 10); do curl -s -o /dev/null -u sportmemo:$STAGE_PASS \
      https://stage.sportmemo.ru/ & done; wait)
    ```
11. **Соседние проекты живы:** `staging.taskman.tech`, `stage.lextr.ru`,
    `andrews.2-way.ru` → 200 (владелец подтверждает).
12. **Загрузка фото через UI** (владелец): кроп → второй шаг → превью → статус
    «На проверке» → публикация модератором из очереди. Пока загружается, смотреть
    `docker stats` — пик памяти воркера.

---

## 7. Ворота подэтапа

Вопрос мастер-плана: **годится ли текущий VPS и этот стейдж.**

| Замер | Порог «годится» | Если нет | Факт 2026-11-18 |
|---|---|---|---|
| Свободно на диске после деплоя | ≥ 1 ГБ | расширить диск стейджа | **1.6 ГБ (89%)** — на границе |
| Время первой сборки образа | ≤ 10 мин (1 vCPU) | терпимо, но отметить в 5.7 | **~6 мин** |
| RAM занято всеми контейнерами SportMemo в простое | ≤ 700 МБ | урезать `mem_limit`, проверить утечки | **294 МБ** (web 148 + worker 95 + db 47 + redis 4) |
| Пик RAM воркера при загрузке фото | без OOM и без убийства соседей | `CELERY_WORKER_CONCURRENCY=1` уже стоит; расширять память | ожидает проверки владельцем (6.4 п. 12) |
| Ответ главной под 10 параллельными запросами | ≤ 2 с на запрос | 2 воркера gunicorn — потолок; отметить | ожидает проверки |
| Соседние проекты после деплоя | все 200 | откатить: `docker compose -f docker-compose.stage.yml down` | **все 200** |

**Предварительный вывод по воротам:** стейдж годится, но диск — узкое место.
После чистки кеша свободно 1.6 ГБ, и каждый деплой оставляет ~1.6 ГБ кеша сборки.
На проде (2 vCPU, диск 29 ГБ, свободно 16 ГБ) это не проблема; на стейдже перед
каждой сборкой нужен `docker builder prune -af`.

**Решение записывается в мастер-план и в бэклог** строкой: «5.3: стейдж годится /
не годится, потому что …».

---

## 8. Риски

| Риск | Что делаем |
|---|---|
| OOM-киллер убил соседний контейнер | `mem_limit` у всех сервисов; `--concurrency=1`; проверка `docker stats` при загрузке фото |
| Диск кончился на сборке (3.0 ГБ) | лимиты логов 10 МБ × 3; `docker builder prune -f` **не помогает** (проверено: `Total: 0B`, кеш помечен используемым) — тогда `docker image prune -a` только после согласования с владельцем, иначе расширять диск |
| `certbot --nginx` упал на basic auth | `location ^~ /.well-known/acme-challenge/ { auth_basic off; }` в эталоне; запасной путь — `certonly --webroot` |
| Сайт отдаёт 500 на статике | `collectstatic` в скрипте деплоя; проверка `/static/css/sportmemo.<hash>.css` |
| Реальные ПДн попали на стейдж | регистрация закрыта, basic auth, правило в `KODA.md`; при появлении — стереть и разобраться |
| Письма со стейджа уходят с боевого домена | регистрация закрыта, пользователей нет; `mail_check` — вручную на личный ящик |
| `Host` не совпал → 400 | `ALLOWED_HOSTS=stage.sportmemo.ru`; заходить по домену, не по IP |
| Правка файла на хосте не видна контейнеру | код в образе, `.:/app` не монтируется; изменения — только через `git pull` + деплой |
| Пароль basic auth утёк | файл `/etc/nginx/.htpasswd_sportmemo_stage` пересоздаётся одной командой, `reload` |

---

## 9. Проверка подэтапа

- `make lint && make test` на шаге 9, semver по навыку `semver-checker` (patch).
- Ручная проверка: таблица 6.3 целиком, плюс загрузка фото с телефона (6.4, п. 12).
- Замеры 6.4 записаны в план, решение по воротам — в мастер-план.
- После подэтапа: README знает стейдж, `KODA.md`/`AGENTS.md` — четыре правила,
  бэклог — 5.3 закрыт, `P5-14` уточнена.
