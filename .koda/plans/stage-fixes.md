# SportMemo — исправления по итогам проверки стейджа

Дата создания: 2026-11-19
Статус: **выполнен 2026-11-22** (шаги 1–5, 0.34.6 → 0.34.8)
Основание: браузерная проверка `https://stage.sportmemo.ru` с телефона 2026-11-19
Мастер-план: [`mobile-legal-deploy.md`](mobile-legal-deploy.md) — этап 5
Связанный план: [`mobile-legal-deploy-5.3-stage.md`](mobile-legal-deploy-5.3-stage.md) —
разделы 6.3 и 6.4 выполнены, здесь записаны найденные при них дефекты
База: `master`, версия 0.34.6
Ветка: `fix/stage-browser-fixes`
Финал: фактический bump — **0.34.6 → 0.34.8** двумя patch-коммитами (сделано)

> **Для новой сессии.** Владелец зашёл на стейдж с телефона и нашёл пять
> проблем. Этот план их закрывает: три правки в коде, две операции на сервере
> (`sudo`, зона владельца). Решения по портам 465/587 и по диску приняты
> владельцем и записаны в разделе «Что в план не входит» — обсуждать их заново
> не нужно.
>
> **Секретов в план и в git не попадает.** Пароль basic auth подставляется
> переменной `STAGE_PASS`, логин `sportmemo`.

---

## 1. Что нашли и что решаем

| № | Находка | Как установлено | Решение |
|---|---|---|---|
| 1 | Статика отдаётся несжатой | `bootstrap.min.css` уходит 232 066 Б, а рядом лежит `.gz` на 30 813 Б | `gzip_static on` в nginx |
| 2 | CSS и JS не сжимаются вовсе | `gzip_types` в `/etc/nginx/nginx.conf` закомментирован, сжимается только HTML | задать типы в конфиге сайта |
| 3 | HTTP/2 работает «по наследству» | `listen 443 ssl;` без `http2`, но ALPN отвечает `h2` | добавить `http2` явно |
| 4 | Две ссылки «Забыли пароль» | `help_text` поля от allauth плюс наша в `.auth-links` | очистить `help_text` |
| 5 | Демо-модератор без области | `moderated_sport_ids() == []`, очередь пуста | `seed_demo` назначает все виды спорта |
| 6 | Диск занят на 90% | 1.5 ГБ свободно из 15 ГБ | удалить старые snap-ревизии и кеш apt |

### 1.1 Замеры, на которых стоит решение

Проверено с рабочей машины 2026-11-19.

- **Канал до стейджа:** 5 МБ за 8.14 с → **614 КБ/с**; `ping` 128 мс среднее,
  до 209 мс; потери 2% (49 из 50 пакетов).
- **Один CSS-файл:** `bootstrap.min.css` 232 066 Б несжатым; `.gz` рядом —
  30 813 Б. Вся статика: 4.06 МБ против 1.05 МБ в `.gz` (284 файла).
- **Главная со всеми ресурсами:** 6.6 с в 9 параллельных потоков; по HTTP/1.1
  последовательно — **73 с**.
- **Приложение не виновато:** с самого сервера главная отдаётся за 0.06–0.12 с,
  10 параллельных — 0.73 с всего.
- **Причина «висит загрузка» в Firefox:** один запрос из ~80 занял **68.4 с**.
  Это тайминг TCP SYN-ретрансмиссий (1+2+4+8+16+32 = 63 с): пакеты не доходили.
  Локально на сервере тот же запрос — 9 мс. Chrome проходит потому, что
  агрессивнее переоткрывает соединение при 401; Firefox ждёт самый медленный
  подзапрос, и страница висит до конца ретрансмиссий.

Вывод: **несжатая статика умножает время загрузки на плохом канале в 5 раз**,
а 2% потерь превращают это в «висит». Сжатие — единственное, что в нашей власти.

---

## 2. Шаг 1. Убрать дубль ссылки «Забыли пароль»

**[x] Сделано 2026-11-22** (коммит `982940b`, 0.34.6 → 0.34.7).
`LoginForm.__init__` очищает `help_text` поля пароля; тест
`test_login_page_has_one_reset_link` держит одну ссылку на странице.

**Файлы:** `apps/accounts/forms.py`, `tests/test_auth_ui.py`

`allauth` в `LoginForm` ставит `show_reset_help=True` и подкладывает ссылку
в `help_text` поля пароля (`allauth/account/fields.py:50`). Наша ссылка стоит
в блоке `.auth-links` шаблона — она лучше, потому что рядом с «Зарегистрироваться»
и не дублирует подсказку под полем.

```python
class LoginForm(BootstrapFormMixin, allauth_forms.LoginForm):
    """Форма входа без подсказки сброса под полем пароля.

    `allauth` кладёт ссылку «Забыли пароль?» в `help_text` поля, а вторая
    стоит в блоке `.auth-links` шаблона — две одинаковые ссылки на одной
    странице. Оставляем ту, что рядом с «Зарегистрироваться».
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password"].help_text = ""
```

`super().__init__()` вызывается первым: allauth добавляет поля в своём
`__init__`, и обращение к `self.fields["password"]` до него упало бы `KeyError`.

**Тест** в `tests/test_auth_ui.py`, рядом с `test_login_page_links_to_reset_and_signup`:

```python
def test_login_page_has_one_reset_link(self, client):
    html = client.get(LOGIN_URL).content.decode()

    assert html.count(reverse("account_reset_password")) == 1
```

**Проверка:** `uv run pytest tests/test_auth_ui.py tests/test_stage_mode.py -q`

---

## 3. Шаг 2. Демо-модератор получает область

**[x] Сделано 2026-11-22** (коммит `982940b`). `seed_demo` вызывает
`moderated_sports.set(Sport.objects.all())`; тест
`test_demo_moderator_sees_drafts_in_queue` проверяет не данные, а то, что
очередь фото не пуста (6 черновиков).

**Файлы:** `apps/core/management/commands/seed_demo.py`, `tests/test_seed_demo.py`

Сейчас `_ensure_user()` создаёт пользователя с ролью, но `moderated_sports`
не трогает. По правилу 4.1 пустая область значит «не видит чужого ничего»,
поэтому очередь демо-модератора пуста, хотя черновики есть: на стейдже
6 фото, 1 команда и 2 события в `draft` от `demo@example.com`.

После создания пользователей в `handle()`:

```python
self.demo_user = self._ensure_user(DEMO_EMAIL, "Демо-автор", UserRole.USER)
self.demo_moderator = self._ensure_user(
    DEMO_MODERATOR_EMAIL, "Демо-модератор", UserRole.MODERATOR
)
# Роль без области не даёт видеть ничего (4.1): демо-модератор должен
# решать очередь, иначе демонстрация модерации на стейдже пуста.
self.demo_moderator.moderated_sports.set(Sport.objects.all())
```

Все виды спорта, а не только `baseball`: продукт односпортный, но правило
должно работать и когда появится второй (инвариант № 1).

**Тесты** в `tests/test_seed_demo.py`, в классе `TestDemoSet`:

```python
def test_demo_moderator_moderates_all_sports(self, seed):
    seed()

    moderator = User.objects.get(email=DEMO_MODERATOR_EMAIL)

    assert set(moderator.moderated_sports.values_list("slug", flat=True)) == {
        sport.slug for sport in Sport.objects.all()
    }


def test_demo_moderator_sees_drafts_in_queue(self, seed):
    seed()

    moderator = User.objects.get(email=DEMO_MODERATOR_EMAIL)
    photos = pending_queryset(
        QUEUE_MODELS["photo"],
        sport_ids=moderator.moderated_sport_ids(),
        exclude_author=moderator,
    )

    assert photos.count() == 6
```

Второй тест проверяет не данные, а то, ради чего правка: очередь не пуста.
Импорты: `Sport` из `apps.core.models`, `QUEUE_MODELS` и `pending_queryset`
из `apps.moderation.queue`.

**Проверка:** `uv run pytest tests/test_seed_demo.py -q`

---

## 4. Шаг 3. `gzip_static` и `http2` в nginx

**[x] Сделано 2026-11-22** (коммит `2677f85`, 0.34.7 → 0.34.8).
Обе директивы добавлены в `sportmemo-stage.conf` и `sportmemo-stage-bootstrap.conf`.

**Факт проверки после копирования на сервер:**

```
$ curl -s -o /dev/null -D - -u sportmemo:… -H 'Accept-Encoding: gzip' \
    https://stage.sportmemo.ru/static/vendor/bootstrap/css/bootstrap.min.6032a13145bb.css
HTTP/2 200
content-length: 30813
content-encoding: gzip
```

`content-length: 30813` совпал с ожиданием в точности: nginx отдаёт готовый
`.gz`, а не сжимает на лету. До правки тот же файл уходил 232 066 Б.

**Го́тча, которая съела один круг проверки.** Ветка `fix/stage-browser-fixes`
существовала только локально, а на сервере `git switch` в неё молча не сработал:
там остался `master`, и `cp` копировал файл без правок. Симптом: `grep` по
`nginx -T` не находил `gzip_static` вовсе, а `curl` отдавал несжатый CSS.
Поэтому ветку нужно **пушить** (`git push -u origin fix/stage-browser-fixes`)
и проверять содержимое файла на сервере `grep`-ом **до** `cp`.

Второй ложный сигнал: `content-length: 188` и `HTTP/2 403` — это ответ на запрос
каталога (`$F` не была установлена в новом шелле). 188 Б и 403 не связаны
со сжатием; проверять надо полным именем файла.

**Файлы:** `docker/nginx/sportmemo-stage.conf`, `docker/nginx/sportmemo-stage-bootstrap.conf`

`whitenoise` при `collectstatic` кладёт рядом с каждым CSS и JS готовый `.gz`
(проверено на сервере: 284 файла, 1.05 МБ против 4.06 МБ). Nginx их не отдаёт,
потому что `gzip_static` не включён, а `gzip_types` в `/etc/nginx/nginx.conf`
закомментирован — сжимается только `text/html`.

В `server`-блок 443, перед `location /static/`:

```nginx
    # `whitenoise` при `collectstatic` кладёт рядом с каждым CSS и JS готовый
    # `.gz` (4 МБ → 1 МБ). Без `gzip_static` nginx отдаёт несжатый файл:
    # bootstrap.css уходит 232 КБ вместо 31 КБ, и на мобильном канале это
    # разница в секунды. `gzip_types` в системном `nginx.conf` закомментирован,
    # поэтому типы заданы здесь: эталон должен работать на чистой машине.
    gzip on;
    gzip_vary on;
    gzip_static on;
    gzip_types text/css application/javascript application/json image/svg+xml;
```

И в `listen`:

```nginx
    # `http2` указан явно. Сейчас протокол работает «по наследству»: соседний
    # `stage.lextr.ru.conf` объявил `http2` на том же порту 443, и nginx 1.18
    # включает его на порту целиком. Уберут соседний сайт — HTTP/2 пропадёт
    # молча, а по HTTP/1.1 страница грузится в разы дольше.
    listen 443 ssl http2;
```

Те же строки — в bootstrap-конфиг: он используется при перевыпуске сертификата,
и расхождение дало бы другой сайт в момент, когда проверять его сложнее всего.

**Проверка после копирования на сервер:**

```bash
curl -sI -u sportmemo:$STAGE_PASS -H 'Accept-Encoding: gzip' \
  https://stage.sportmemo.ru/static/vendor/bootstrap/css/bootstrap.min.6032a13145bb.css \
  | grep -iE 'content-encoding|content-length'
# ожидание: content-encoding: gzip, content-length: 30813
```

Ожидаемый эффект: главная с девятью ресурсами — было 6.6 с, станет ~1.5–2 с.

---

## 5. Шаг 4. Обновить эталон на сервере (владелец, `sudo`)

**[x] Сделано владельцем 2026-11-22.** `nginx -t` прошёл, `reload` выполнен,
`gzip_static` активен (см. факт в шаге 3). `listen 443 ssl http2` подтверждён
в активной конфигурации: блок нашего сайта содержит `http2` явно.

```bash
cd ~/sportmemo && git pull --ff-only
sudo cp docker/nginx/sportmemo-stage.conf /etc/nginx/conf.d/stage.sportmemo.ru.conf
sudo nginx -t && sudo systemctl reload nginx
```

**Осторожно, порядок важен.** `nginx -t` проверяет установленный файл, поэтому
копия и проверка стоят рядом, а `reload` — только после успешного `-t`.
Невалидный конфиг на диске уронил бы **все четыре сайта** на сервере при первом
же reload или перезагрузке. Если `nginx -t` упал — сразу вернуть прежний файл
(`cd ~/sportmemo && git stash` или копию из `/etc/nginx/`), скопировать заново,
повторить.

**Проверка после:** таблица 6.3 плана 5.3 заново — главная `200`, статика
с `gzip`, `ALPN h2`, сайт открывается в Firefox с телефона.

---

## 6. Шаг 5. Освободить диск (владелец, `sudo`)

**[x] Сделано владельцем 2026-11-22.** `df -h /` → **15 ГБ, занято 11 ГБ,
свободно 3.0 ГБ (79%)** — лучше планового порога ≥ 1.9 ГБ. Диск перестал
быть узким местом стейджа.

```bash
# 305 МБ: старые отключённые ревизии
sudo snap remove core20 --revision=2922
sudo snap remove core22 --revision=2955
sudo snap remove lxd    --revision=40911
sudo snap remove snapd  --revision=27738

# 137 МБ: кеш скачанных пакетов apt
sudo apt clean

# 60 МБ: журналы, оставить 100 МБ
sudo journalctl --vacuum-size=100M
```

Итого ~500 МБ: свободно станет ~2.0 ГБ вместо 1.5 ГБ.

**Осторожно:** `snap remove --revision` удаляет **только указанную** ревизию,
активная остаётся. Если команда ответит ошибкой про активную ревизию — ревизия
не отключена, удалять её нельзя, и это записывается в план как «не удалилось».
`lxd` не удаляется целиком (`snap remove lxd` без `--revision`), потому что
пакет может понадобиться; уходит только старая ревизия.

**Проверка:** `df -h /` → ожидание ≥ 1.9 ГБ свободно, 87%. Затем
`docker compose -f docker-compose.stage.yml ps` (все четыре сервиса `Up`)
и три соседних сайта — все `200`.

---

## 7. Шаг 6. Документация и коммиты

**Правки:**

1. `.koda/plans/stage-fixes.md` — этот план с фактическими результатами.
2. `.koda/plans/mobile-legal-deploy-5.3-stage.md` — в шапку строку «Находки
   браузерной проверки и их исправление — в `stage-fixes.md`»; в таблицу 6.3
   строку про `gzip`.
3. `README.md`, раздел «Стейдж» — абзац: `gzip_static` обязателен, `.gz` создаёт
   `whitenoise`, без директивы статика уходит в пять раз больше.
4. `KODA.md` / `AGENTS.md`, «Правила для кода» — правило:

   > **`gzip_static` в nginx обязателен.** `whitenoise` кладёт `.gz` рядом
   > с CSS и JS, но без директивы nginx отдаёт несжатый файл (232 КБ вместо
   > 31 КБ). `gzip_types` в системном `nginx.conf` закомментирован, поэтому
   > типы задаются в конфиге сайта.

5. `.koda/backlog.md` — 5.3: «закрыто (0.34.8), ворота пройдены»; новая строка
   `P5-16` — «проверить `dmarc=pass` при деплое прода: на стейдже исходящий SMTP
   закрыт хостером» (`P5-15` занят задачей про кеш при недоступном Redis).

**Коммиты:**

| № | Сообщение | Версия |
|---|---|---|
| 1 | `fix: remove the duplicate password reset link and give the demo moderator all sports` | 0.34.6 → 0.34.7 (patch) |
| 2 | `fix: serve precompressed static files from nginx` | 0.34.7 → 0.34.8 (patch) |

Перед каждым: `make lint && make test`. После изменения версии — `uv lock`.
Тело второго коммита объясняет причину: «The browser test on the phone showed
that the page takes many seconds to open. The cause was not the server: nginx
sent CSS and JS without compression, although the `.gz` files were already
there.»

**Ветка.** Работа идёт в `fix/stage-browser-fixes`, затем squash-merge в `master`.
Правки мелкие, но меняется код и тесты, а правило проекта требует ветку в этом
случае. После merge `git branch -d` не сработает — сначала
`git diff --stat fix/stage-browser-fixes master` (пусто), затем `git branch -D`.

---

## 8. Что в план не входит

| Отложено | Почему |
|---|---|
| Открытие портов 465/587 | решение владельца: `dmarc` проверяется на проде (5.7), стейдж почту не использует |
| Увеличение диска | решение владельца: сначала чистка snap, расширение — когда понадобится |
| `favicon.ico` отдаёт 404 | мелочь, лишний запрос на каждый визит; отдельная задача, не блокирует |
| Причины 2% потерь пакетов | маршрут до Нидерландов, вне нашего контроля |
| HSTS и `SECURE_SSL_REDIRECT` | 5.7, решает мастер-план |

**Принято владельцем 2026-11-19:**

- Порты 465 и 587 на стейдже **не открываются**. Проверка `dmarc=pass` уходит
  на прод: там российский хостер, SMTP работает. Запись — `P5-16` в бэклоге.
- Диск **не увеличивается** сейчас. Сначала чистка snap и кеша apt; если после
  неё деплой упрётся в диск — вернуться к вопросу с хостером.

---

## 9. Порядок для новой сессии

1. Ветка `fix/stage-browser-fixes`.
2. Шаг 1 (ссылка) и шаг 2 (область модератора) → тесты → `make lint && make test`
   → bump 0.34.7 → коммит 1.
3. Шаг 3 (`gzip_static`, `http2`) → `make lint && make test` → bump 0.34.8
   → коммит 2.
4. Шаги 4 и 5 — владелец на сервере (`sudo`), затем проверка таблицы 6.3.
5. Шаг 6 — записать результаты, закрыть 5.3 в бэклоге, squash-merge в `master`.

---

## 10. Проверка подэтапа

- `make lint && make test` перед каждым коммитом; semver по навыку
  `semver-checker` (два patch-бампа).
- На сервере: статика с `content-encoding: gzip` и `content-length: 30813`;
  `ALPN h2`; главная открывается в Firefox с телефона за секунды, а не висит.
- Демо-модератор видит очередь: 6 фото, 1 команда, 2 события.
- На странице входа одна ссылка «Забыли пароль?».
- `df -h /` после чистки: ≥ 1.9 ГБ свободно; соседние сайты живы.
- Замеры и результаты записаны в этот план, 5.3 закрыт в бэклоге.
