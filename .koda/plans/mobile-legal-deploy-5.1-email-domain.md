# SportMemo — подэтап 5.1: почта и домен

Дата создания: 2026-11-15
Статус: **утверждён** (владелец, 2026-11-15)
Мастер-план: [`mobile-legal-deploy.md`](mobile-legal-deploy.md) — подэтап **5.1**,
первый в этапе 5
Бэклог: [`.koda/backlog.md`](../backlog.md) — раздел «Этап 5», задача `P5-5`
Ветка: `feature/email-domain` (создаётся на шаге 1 от `master`)
База: `master`, версия 0.32.0, 1764 теста
Финал: bump **0.32.0 → 0.33.0 (minor)** + `uv lock`, строка
`Bump version: 0.32.0 → 0.33.0 (minor)` в теле коммита

> **Для новой сессии.** Подэтап 5.0 (мастер-план и документация) уже в `master`.
> Решения владельца по почте приняты 2026-11-02 и уточнены 2026-11-15 (см. мастер-план,
> раздел «5.1»). Работа начинается с шага 1. Шаги идут строго по порядку, следующий
> начинается только после зелёной проверки текущего. Файл плана поддерживается
> в актуальном состоянии: `[ ]` → `[x]`. Отмечать шаг выполненным только после
> `grep`-проверки, что правка реально есть в целевых файлах, и после зелёного прогона
> тестов затронутых файлов.
>
> **Секреты в план и в git не попадают.** Креды Postbox переданы владельцем в чате
> 2026-11-15 и живут только в `.env` (он в `.gitignore`, строка 14). В плане, в
> `.env.example`, в README и в сообщении коммита — только имена переменных
> и объяснение, откуда значения берутся.

---

## 1. Цель и границы

**Цель.** Сайт отправляет письма от `info@sportmemo.ru`, человек может ответить
на любое письмо, а заявитель жалобы узнаёт решение по своей жалобе.

**Почему это первым в этапе.** Без почты не работает регистрация: подтверждение
адреса обязательно (`ACCOUNT_EMAIL_VERIFICATION = "mandatory"`), и без SMTP новый
человек застревает на «проверьте почту», которой не будет. Модераторы не узнают
о жалобах, заявитель не узнаёт решение. Всё остальное в этапе 5 опирается на
работающую почту: стейдж (5.3) проверяет регистрацию, прод (5.7) — восстановление
пароля.

**Что входит в подэтап:**

- настройки почты в `config/settings.py`: `EMAIL_TIMEOUT`, `DEFAULT_FROM_EMAIL`
  с отображаемым именем, `SPORTMEMO_CONTACT_EMAIL`;
- `.env.example` и `docker-compose.yml` — блок почты в рабочем виде;
- команда `manage.py mail_check` — печатает настройки без пароля и (опционально)
  отправляет тестовое письмо: пригодится на стейдже и на проде;
- общий хелпер `send_user_email()` в `apps/core/emails.py` — письмо человеку,
  ошибка не глотается (в отличие от писем модераторам);
- письмо заявителю о решении жалобы: шаблон, функция `notify_reporter_decision()`,
  Celery-задача `send_report_decision_email_task`, единственная точка постановки
  `enqueue_report_decision_email()`;
- вызов письма из `resolve_report` / `reject_report` через `transaction.on_commit`;
- **решения по жалобам в админке идут через сервис**: сейчас два действия
  админки пишут статус через `queryset.update()` в обход `services` — без этого
  письмо из админки не уйдёт, а журнал не пополнится (дефект найден при
  планировании, см. раздел 3);
- предупреждение модератору рядом с полем решения: текст уходит заявителю;
- строка анонимам с адресом `info@sportmemo.ru` — на форме жалобы и в сообщении
  после отправки;
- тесты: письмо заявителю, права, `on_commit`, админка, команда `mail_check`;
- DNS-инструкция и фактическое состояние зоны в `README.md`;
- локализация, документация проекта, версия.

**Что не входит:**

| Отложено | Куда / почему |
|---|---|
| Страница «мои жалобы» | отказ владельца (мастер-план): достаточно письма с текстом решения |
| Поле email у анонимной жалобы | вектор спама; анонимам — адрес `info@` |
| Троттлинг подачи жалоб | `P2-17` |
| `rua`/`ruf` в DMARC-отчётах | не требуется для запуска; включается позже одной строкой в зоне |
| Настройка A-записи домена | 5.7 (деплой): сайта ещё нет, домен пока только для почты |
| Закрытие регистрации (`ACCOUNT_ALLOW_REGISTRATION`) | дефект мастер-плана № 1, закрывается на стейдже (5.3) и на проде (5.7) |
| Письма об ошибках 500 (`ADMINS`) | нужен рабочий прод; 5.10 (мониторинг) |
| Форма обратной связи | 5.11 |
| Английские тексты писем | юридические и служебные тексты — только русский (решение владельца) |

---

## 2. Решения

### 2.1 Два сервиса, две роли

**Приём — VK WorkSpace** (MX на `emx.mail.ru`), ящик `info@sportmemo.ru`.
**Отправка — Yandex Cloud Postbox** (`postbox.cloud.yandex.net`): DKIM, статистика
доставки, транзакционные письма. Postbox **не принимает входящие** — MX-записей
в его инструкции нет вовсе.

Следствие: `From` и `Reply-To` — `info@sportmemo.ru`. Ответ на любое письмо
приходит в VK WorkSpace, а не в никуда. Ящика `noreply@` не существует
и не появится.

**Проверено опытом 2026-11-15** (мастер-план): Postbox разрешает отправку
с любого ящика на подтверждённом домене, отдельный адрес под каждый ящик
заводить не нужно. Отдельно проверено при планировании этого подэтапа:
SMTP-логин по API-ключу проходит на портах **465 (SMTPS)** и **587 (STARTTLS)**,
AUTH-механизмы `PLAIN` и `LOGIN`. Берём **465 + `EMAIL_USE_SSL=True`**: это
текущий дефолт проекта, и он не требует менять настройки на 587/TLS.

### 2.2 Ключ Postbox: что чем является

Сервисный аккаунт — `postbox-user`, роль `postbox.sender`, ключ с областью
действия `yc.postbox.send`. Ключ выдан на отправку и только на неё.

| Переменная | Что это |
|---|---|
| `EMAIL_HOST` | `postbox.cloud.yandex.net` |
| `EMAIL_PORT` | `465` |
| `EMAIL_USE_SSL` | `True` |
| `EMAIL_HOST_USER` | **идентификатор API-ключа**, не адрес ящика |
| `EMAIL_HOST_PASSWORD` | **секретная часть API-ключа**, не пароль от ящика |

Две самые частые ошибки при настройке — вписать в `EMAIL_HOST_USER` адрес
`info@sportmemo.ru` и в `EMAIL_HOST_PASSWORD` пароль от ящика VK. Оба дают
`535 Authentication failed`, и текст ошибки не подсказывает, что именно не так.
Поэтому го́тча записывается в `.env.example` комментарием и в README.

**Риск, который надо проверить руками:** если у API-ключа задан срок действия,
письма однажды перестанут уходить молча (ошибка уйдёт в лог, а регистрация
встанет). В шаге 3.3 ключ проверяется в консоли: срок действия должен быть
«бессрочно». Если срок задан — дата записывается в план, и в 5.10 добавляется
напоминание.

### 2.3 Настройки: три новых значения

```python
# --- Почта ---
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=10)
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL", default="SportMemo <info@sportmemo.ru>"
)
SERVER_EMAIL = DEFAULT_FROM_EMAIL
# Контакт для обращений. В 5.5 из этой же переменной возьмётся
# `LEGAL_CONTACT_EMAIL` оператора ПДн: второй адрес не заводим.
SPORTMEMO_CONTACT_EMAIL = env("CONTACT_EMAIL", default="info@sportmemo.ru")
```

Обоснования:

- **`EMAIL_TIMEOUT`** — сейчас не задан, и `smtplib` ждёт соединения до системного
  таймаута (минуты). Без него недоступный Postbox держал бы gunicorn-воркер
  и Celery-задачу минутами, а на проде это кончилось бы 504 у пользователя.
- **`DEFAULT_FROM_EMAIL` с отображаемым именем** — `SportMemo <info@…>`, а не
  голый адрес: в списке писем человек видит отправителя, а не строку.
  `SERVER_EMAIL` равен ему же: второго адреса у проекта нет.
- **`SPORTMEMO_CONTACT_EMAIL`** — адрес для обращений. Сейчас нужен строке
  анонимам; в 5.5 станет `LEGAL_CONTACT_EMAIL` оператора ПДн. Заводить две
  настройки на один адрес значит получить два места, которые разойдутся.

### 2.4 Письмо человеку — отдельный хелпер

`notify_moderators()` **глотает** ошибку SMTP: падение почты не должно отменять
действие пользователя. Для письма заявителю правило другое: это единственный
канал, которым он узнаёт решение, и молча потерять письмо хуже, чем повторить
попытку.

```python
# apps/core/emails.py
def send_user_email(subject, template, context, recipient):
    """Письмо человеку. Ошибку не глотает — вызывающий решает, что делать.

    `notify_moderators()` устроен наоборот: там падение почты не должно
    отменять действие пользователя. Здесь письмо — единственный способ
    узнать решение, поэтому сбой уходит наверх: Celery повторит попытку,
    а окончательный провал попадёт в лог.
    """
```

### 2.5 Письмо заявителю: три правила

1. **Только `is_active`.** После анонимизации аккаунта (5.6) email станет
   `deleted-<uuid>@invalid`, а `is_active=False`. Наивный код отправил бы письмо
   в никуда и получил бы отказ SMTP на каждое решение. Проверка живёт
   в `notify_reporter_decision()`, а не в задаче: она про данные, а не про очередь.
2. **Через `transaction.on_commit` и фоновую задачу.** `report_decide` держит
   `select_for_update` внутри `transaction.atomic()`; SMTP-соединение занимает
   секунды, и держать блокировку строки всё это время нельзя. `on_commit`
   гарантирует и обратное: если транзакция откатилась, письма не будет.
3. **Текст нейтральный.** «По вашей жалобе на объект принято решение: …»,
   без формулировок «на вас пожаловались» и без упоминания модератора.
   Отклонение жалобы — тоже решение, и текст решения объясняет причину.

### 2.6 Админка: решения по жалобам идут через сервис

Найдено при планировании (раздел 3, дефект 1): `ReportAdmin.action_resolve`
и `action_reject_report` меняют статус через `queryset.update()`, минуя
`services.resolve_report` / `reject_report`. Последствия:

- в журнал `ModerationAction` не попадает ничего — нарушение инварианта № 6;
- заявитель не получает письмо, если жалобу разобрали в админке;
- право по области не проверяется: модератор с `is_staff` разберёт жалобу
  чужого вида спорта.

Поэтому два пакетных действия заменяются одним — **«Решить с текстом»**:
оно открывает страницу с тем же полем решения, что и публичный интерфейс,
и двумя кнопками («Решена» / «Отклонена»). Текст обязателен, потому что его
прочитает заявитель.

Заодно `status`, `resolution`, `resolved_by` и `resolved_at` становятся
`readonly` в форме жалобы: иначе суперадмин поменяет статус руками, и это снова
обойдёт журнал и письмо. Остаётся один путь решения — сервис.

### 2.7 DNS: одна SPF, ничего не дублировать

Фактическое состояние зоны `sportmemo.ru` (замерено 2026-11-15, `dig @8.8.8.8`):

| Запись | Значение | Состояние |
|---|---|---|
| MX | `10 emx.mail.ru.` | **есть**, VK WorkSpace принимает |
| TXT (SPF) | `v=spf1 include:_spf.mail.ru include:spf.postbox.yandexcloud.net ~all` | **есть**, одна |
| TXT `_dmarc` | `v=DMARC1; p=none` | **есть** |
| TXT `mailru._domainkey` | `v=DKIM1; k=rsa; p=MIGf…` | **есть**, DKIM VK для ответов и пересылки |
| A `sportmemo.ru` | — | нет (это 5.7) |

**Го́тча, выясненная при планировании.** Две CNAME DKIM Postbox (`Easy DKIM`)
перебором вероятных селекторов (`postbox`, `mail`, `s1`, `s2`, `yc`, `default`,
`dkim1`, `dkim2`, …) **не находятся**. Имена выдаёт только консоль Postbox
в блоке «Настройка подписи писем (DKIM)», и угадывать их бессмысленно.
Проверять надо иначе: статус `Success` на странице адреса в консоли и заголовки
реального письма (`Authentication-Results` → `dkim=pass header.d=sportmemo.ru`,
`spf=pass`, `dmarc=pass`). Это записывается в README, чтобы следующая сессия
не тратила время на `dig`.

**Две TXT с `v=spf1` дают `PermError`,** и почта перестаёт проходить SPF целиком.
При правке зоны сначала смотрится существующая запись, и только потом
добавляется новая.

**Особенности R01 (из мастер-плана, опыт 2026-11-15):** поле «Хост» — только
поддомен, для корня пустое (`@` не принимается); значения MX и CNAME — с точкой
на конце (`emx.mail.ru.`), иначе R01 приклеивает зону и получается
`emx.mail.ru.sportmemo.ru.`; имя DKIM-CNAME — без домена, значение — с точкой;
`_dmarc` с подчёркиванием форма принимает.

---

## 3. Факты и дефекты, проверенные при планировании

Каждый пункт подтверждён кодом, консолью или `dig`, а не предположен.

1. **Админка решает жалобы в обход сервиса.** `apps/moderation/admin.py:39-56`:
   `queryset.update(status=..., resolved_by=..., resolved_at=...)`. Журнал
   не пишется, право по области не проверяется, письмо не уходит. Лечится
   в шаге 3.9.
2. **`EMAIL_USE_SSL` не пробрасывается в контейнер.** `docker-compose.yml`
   передаёт `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`,
   `DEFAULT_FROM_EMAIL` — и не передаёт `EMAIL_USE_SSL`. Работает только на
   дефолте из `settings.py`; явная переменная в compose нужна, иначе смена
   порта на 587 в `.env` даст молчаливый сбой.
3. **`DEFAULT_FROM_EMAIL` по умолчанию — `noreply@sportmemo.ru`** (settings.py:277,
   `.env.example:41`, `docker-compose.yml:86`). Ящика не существует, ответы
   на письма отбивались бы. Меняется в трёх местах сразу.
4. **`EMAIL_TIMEOUT` не задан.** `smtplib` ждёт до системного таймаута.
5. **Теста `test_resolve_report_action` закрепляет текущее поведение админки**
   (`tests/test_admin.py:446`): проверяет только статус, а не журнал. Тест
   переписывается вместе с шагом 3.9.
6. **Письмо модераторам о новой жалобе уже работает** (`apps/moderation/emails.py`,
   `notify_moderators_new_report`) и меняется только адресом отправителя.
7. **`Report.reporter` — `SET_NULL`,** поэтому заявитель может быть `None`
   (аноним или удалённый аккаунт). Проверка «есть кому писать» обязательна
   в одном месте.
8. **`pytest-django` 4.14** — фикстура `django_capture_on_commit_callbacks`
   доступна, отдельный пакет не нужен. Без неё `on_commit` в тестах
   не выполняется: тест обёрнут в транзакцию.
9. **SMTP Postbox отвечает** на 465 и 587 по API-ключу (проверено вживую),
   `AUTH PLAIN LOGIN`. Значит блокер «а вдруг провайдер не пустит» снят
   и в шагах ничего не зависит от внешнего сервиса, кроме самой отправки.

---

## 4. Шаги

### 4.1 Ветка и версия

- [ ] **`feature/email-domain` от `master`**

  ```bash
  git switch master && git pull --ff-only
  git switch -c feature/email-domain
  ```

  Проверка: `git branch --show-current` → `feature/email-domain`,
  `git status --short` пустой.

### 4.2 Настройки, пример окружения, compose

- [ ] **`EMAIL_TIMEOUT`, `DEFAULT_FROM_EMAIL` с именем, `SPORTMEMO_CONTACT_EMAIL`**

  1. `config/settings.py`, блок «Почта»: три значения из 2.3. Docstring/комментарии —
     почему таймаут и почему контакт один.
  2. `.env.example`, блок «Почта» — рабочий пример вместо пустых значений:

     ```dotenv
     # --- Почта (Yandex Cloud Postbox) ---
     # Приём писем — VK WorkSpace (MX на emx.mail.ru), отправка — Postbox.
     # Postbox не принимает входящие: MX-записей у него нет вовсе.
     EMAIL_HOST=postbox.cloud.yandex.net
     EMAIL_PORT=465
     EMAIL_USE_SSL=True
     EMAIL_TIMEOUT=10
     # EMAIL_HOST_USER — идентификатор API-ключа сервисного аккаунта
     # postbox-user, а НЕ адрес ящика. Роль postbox.sender, область
     # действия ключа yc.postbox.send.
     EMAIL_HOST_USER=
     # EMAIL_HOST_PASSWORD — секретная часть API-ключа, а НЕ пароль
     # от ящика. Хранится только в .env, в git не попадает.
     EMAIL_HOST_PASSWORD=
     # Отправитель с именем: ящика noreply@ не существует, ответы
     # на письма приходят в info@sportmemo.ru (VK WorkSpace).
     DEFAULT_FROM_EMAIL=SportMemo <info@sportmemo.ru>
     # Контакт для обращений. В 5.5 станет LEGAL_CONTACT_EMAIL.
     CONTACT_EMAIL=info@sportmemo.ru
     ```

  3. `docker-compose.yml`, `django_env`: `EMAIL_USE_SSL`, `EMAIL_TIMEOUT`,
     `DEFAULT_FROM_EMAIL` (значение в кавычках — в YAML `<` и `>`), `CONTACT_EMAIL`.
     Дефолт `DEFAULT_FROM_EMAIL` → `SportMemo <info@sportmemo.ru>`.

  Проверка:

  ```bash
  grep -n "EMAIL_TIMEOUT\|SPORTMEMO_CONTACT_EMAIL" config/settings.py
  grep -n "EMAIL_USE_SSL\|EMAIL_TIMEOUT\|CONTACT_EMAIL" docker-compose.yml .env.example
  uv run python -c "from django.conf import settings; print(settings.EMAIL_TIMEOUT, settings.DEFAULT_FROM_EMAIL)"
  ```

  Файлы:
  - `config/settings.py`
  - `.env.example`
  - `docker-compose.yml`

### 4.3 Локальные креды и живая проверка отправки

- [ ] **Вписать креды в `.env` и отправить настоящее письмо**

  Это единственный шаг, который не оставляет следа в git: значения переданы
  владельцем в чате и пишутся только в `.env`.

  1. `.env`: `EMAIL_HOST=postbox.cloud.yandex.net`, `EMAIL_PORT=465`,
     `EMAIL_USE_SSL=True`, `EMAIL_TIMEOUT=10`, `EMAIL_HOST_USER=<идентификатор
     API-ключа>`, `EMAIL_HOST_PASSWORD=<секретная часть ключа>`,
     `DEFAULT_FROM_EMAIL=SportMemo <info@sportmemo.ru>`, `CONTACT_EMAIL=info@sportmemo.ru`.
     Старая строка `EMAIL_HOST=` пустой и `DEFAULT_FROM_EMAIL=noreply@…` уходят.
  2. В консоли Yandex Cloud проверить у ключа: роль `postbox.sender`, область
     `yc.postbox.send`, **срок действия «бессрочно»**. Если срок задан — записать
     дату в раздел 7 этого плана.
  3. Отправить тестовое письмо командой (появится в шаге 4.4; до неё — вручную
     через `manage.py shell` и `send_mail`), получатель — личный ящик владельца.
     Письмо должно прийти во «Входящие», а не в спам.
  4. Владелец присылает заголовки полученного письма («Показать оригинал»).
     Записать в план фактические значения `spf`, `dkim`, `dmarc` и `From`.

  **Почему это до кода.** Если провайдер не пустит или ключ окажется просроченным,
  остальные шаги теряют смысл: письмо заявителю некуда отправить, а тесты
  с `locmem` этого не покажут.

  Проверка: письмо пришло, в заголовках `spf=pass`, `dkim=pass`,
  `dmarc=pass`, `From: SportMemo <info@sportmemo.ru>`. Ответ на письмо
  открывается в VK WorkSpace (`info@`).

  Файлы:
  - `.env` (не коммитится)

### 4.4 Команда `mail_check`

- [ ] **`manage.py mail_check [адрес]`**

  `apps/core/management/commands/mail_check.py`:

  1. Печатает `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_SSL`,
     `EMAIL_TIMEOUT`, `DEFAULT_FROM_EMAIL` и `EMAIL_HOST_USER`. **Пароль
     не печатается никогда** — ни целиком, ни частично.
  2. Если передан адрес — отправляет короткое письмо через `send_mail` и печатает
     результат: «отправлено» или текст ошибки.
  3. Ошибка отправки не превращается в трейсбек: команда печатает сообщение
     и завершается с `CommandError`, чтобы в скрипте деплоя это было видно.

  Зачем: на стейдже (5.3) и на проде (5.7) нужна проверка «почта жива» одной
  командой, без `shell` и без риска вписать секрет в историю команд. Мастер-план
  требует проверки отправки — команда делает её повторяемой.

  Проверка:

  ```bash
  uv run python manage.py mail_check                 # настройки, без отправки
  uv run python manage.py mail_check test@example.com
  uv run python manage.py mail_check | grep -c "EMAIL_HOST_PASSWORD"  # 0 — пароля нет
  ```

  Файлы:
  - `apps/core/management/commands/mail_check.py`

### 4.5 Хелпер письма человеку

- [ ] **`send_user_email()` в `apps/core/emails.py`**

  Код и docstring — из 2.4. Рядом, в том же модуле, остаётся `notify_moderators`
  без изменений: два разных правила должны стоять рядом и объяснять разницу.

  Проверка: `grep -n "def send_user_email\|def notify_moderators" apps/core/emails.py`.

  Файлы:
  - `apps/core/emails.py`

### 4.6 Письмо заявителю: шаблон и функция

- [ ] **`notify_reporter_decision(report)`**

  1. `templates/moderation/email/report_decision.txt` — текст из 2.5:

     ```text
     {% load i18n %}{% trans "Решение по вашей жалобе" %}

     {% trans "Объект" %}: {{ target }}
     {% trans "Решение" %}: {{ resolution }}

     {% trans "Если остались вопросы, напишите на" %} {{ contact_email }}.
     ```

     Без «на вас пожаловались», без имени модератора, без ссылки на служебные
     страницы: заявителю нужен ответ, а не маршрут по админке.

  2. `apps/moderation/emails.py`:

     ```python
     def notify_reporter_decision(report):
         """Письмо заявителю о решении жалобы.

         Только активный пользователь: после анонимизации (5.6) адрес
         становится `deleted-…@invalid`, и письмо ушло бы в никуда.
         Анонимной жалобе писать некуда — возвращаем 0.

         Ошибку не глотает: `send_user_email` устроен иначе, чем письмо
         модераторам, и повтор попытки делает задача.
         """
     ```

     Тема: `_("Решение по вашей жалобе: %(reason)s")` с подписью причины.
     `target` берётся из `report.content_object` (может быть `None` — тогда «—»).

  Проверка: `grep -n "def notify_reporter_decision" apps/moderation/emails.py`
  и наличие файла шаблона.

  Файлы:
  - `apps/moderation/emails.py`
  - `templates/moderation/email/report_decision.txt`

### 4.7 Задача Celery и единственная точка постановки

- [ ] **`apps/moderation/tasks.py`**

  По образцу `apps/photos/tasks.py`: одна точка постановки, ретраи, ошибка
  наружу не выходит.

  ```python
  RETRY_BACKOFF = 60

  @shared_task(bind=True, max_retries=3)
  def send_report_decision_email_task(self, report_id):
      """Письмо заявителю о решении жалобы."""
      report = Report.objects.select_related("reporter").filter(pk=report_id).first()
      if report is None:
          return
      try:
          notify_reporter_decision(report)
      except Exception as exc:
          if self.request.retries >= self.max_retries:
              logger.exception("Письмо заявителю по жалобе #%s не ушло", report_id)
              return
          raise self.retry(
              exc=exc, countdown=RETRY_BACKOFF * 2**self.request.retries
          ) from exc

  def enqueue_report_decision_email(report) -> None:
      """Поставить письмо в очередь. Единственная точка постановки."""
      send_report_decision_email_task.delay(report.pk)
  ```

  Обоснования в docstring: почему не в представлении, почему `on_commit`
  на стороне сервиса, почему три попытки и почему после них — лог, а не
  исключение (решение уже принято и записано, отменять его нельзя).
  Дубликат письма при потере ответа SMTP принимается осознанно: два письма
  лучше, чем ни одного.

  Проверка: `grep -n "def send_report_decision_email_task\|def enqueue_report_decision_email" apps/moderation/tasks.py`.

  Файлы:
  - `apps/moderation/tasks.py`

### 4.8 Сервис: письмо после коммита

- [ ] **`resolve_report` и `reject_report` ставят задачу через `on_commit`**

  В обеих функциях после `log_action(...)`:

  ```python
  transaction.on_commit(
      lambda pk=report.pk: enqueue_report_decision_email(report)
  )
  ```

  Импорты: `from django.db import transaction`,
  `from apps.moderation.tasks import enqueue_report_decision_email`.

  Комментарий в коде: письмо не ставится внутри транзакции, потому что
  `report_decide` держит `select_for_update`, а SMTP-соединение занимает секунды;
  и потому что откат транзакции не должен оставить письмо о решении, которого нет.

  Проверка: `grep -n "on_commit" apps/moderation/services.py` — два вхождения.

  Файлы:
  - `apps/moderation/services.py`

### 4.9 Админка: решение через сервис

- [ ] **Одно действие «Решить с текстом» вместо двух пакетных**

  1. `apps/moderation/admin.py`, `ReportAdmin`:
     - `actions = ("action_decide",)`;
     - `action_decide` открывает страницу решения (метод `get_urls` +
       `decide_view` по образцу `ModerationActionsMixin.reject_view`):
       GET — форма `ReportResolutionForm`, список выбранных жалоб;
       POST — цикл по жалобам, `services.resolve_report` или
       `services.reject_report` по полю `action`, подсчёт решённых
       и пропущенных (нет области, уже разобрана), сообщение через
       `message_user`, редирект в список;
     - `PermissionDenied` от сервиса ловится и считается пропуском:
       так же ведёт себя миксин объектов;
     - `readonly_fields` дополняется `status`, `resolution`, `resolved_by`,
       `resolved_at` — единственный путь решения остаётся через сервис;
     - старые `action_resolve` и `action_reject_report` удаляются.
  2. `templates/admin/moderation/report_decide.html` — форма с полем решения
     и двумя кнопками («Решена» / «Отклонена»), в оформлении админки.
     Текст решения обязателен: его прочитает заявитель.

  Проверка:

  ```bash
  grep -n "action_decide\|readonly_fields\|def decide_view" apps/moderation/admin.py
  grep -rn "action_resolve\|action_reject_report" apps/ tests/   # пусто
  ```

  Файлы:
  - `apps/moderation/admin.py`
  - `templates/admin/moderation/report_decide.html`

### 4.10 Интерфейс: предупреждение и строка анонимам

- [ ] **Модератор видит, что текст уходит заявителю; аноним видит адрес**

  1. `templates/moderation/partials/report_resolution_form.html`: под полем
     решения строка-подсказка `form-text`:
     «Текст решения уйдёт заявителю. Напишите так, чтобы его можно было
     прочитать без пояснений.» Без JavaScript и без изменения формы.
  2. `templates/moderation/report_form.html`: в подписи формы (там, где сейчас
     «Жалоба не требует регистрации…») добавить второе предложение:
     «Если вы не вошли, ответить вам мы не сможем — напишите на
     {{ contact_email }}.» Адрес берётся из контекста, а не хардкодом.
  3. `apps/moderation/views.py`, `report_add`: `"contact_email":
     settings.SPORTMEMO_CONTACT_EMAIL` в контекст; после успешной отправки
     анонимной жалобы — `messages.info(request, _("Если жалоба о приватности
     или правах, ответить вам мы сможем только по адресу %(email)s.") % ...)`.
     Вошедшему это сообщение не нужно: у него есть аккаунт, и письмо придёт
     на его адрес.

  Проверка:

  ```bash
  grep -n "contact_email" apps/moderation/views.py templates/moderation/report_form.html
  grep -n "уйдёт заявителю" templates/moderation/partials/report_resolution_form.html
  ```

  Файлы:
  - `templates/moderation/partials/report_resolution_form.html`
  - `templates/moderation/report_form.html`
  - `apps/moderation/views.py`

### 4.11 Тесты

- [ ] **`tests/test_report_email.py` и правка `tests/test_admin.py`**

  Сценарии:

  1. **Решение уходит письмом.** `resolve_report` и `reject_report` через
     `django_capture_on_commit_callbacks(execute=True)` кладут в `mail.outbox`
     одно письмо; получатель — `report.reporter.email`.
  2. **Текст нейтральный и содержательный.** В теле есть текст решения
     и заголовок объекта; нет «на вас пожаловались»; тема содержит причину.
  3. **Анонимной жалобе письма нет.** `reporter=None` → `mail.outbox` пуст,
     решение сохранено.
  4. **Неактивному аккаунту письма нет.** `is_active=False` →
     `mail.outbox` пуст (это состояние после анонимизации, 5.6).
  5. **Взятие в работу письма не отправляет.** `take_report` → `mail.outbox` пуст.
  6. **Гонка.** Второй POST по разобранной жалобе даёт «Уже обработано»
     и второго письма не появляется.
  7. **Письмо из админки.** Действие `action_decide` (страница + POST):
     статус меняется, `ModerationAction` записан, письмо ушло; без текста
     решения — ошибка формы, статус не меняется.
  8. **Админка и область.** Модератор с `is_staff` без области на чужую жалобу
     получает пропуск, а не решение.
  9. **Отправитель.** `mail.outbox[0].from_email == settings.DEFAULT_FROM_EMAIL`
     (сравнение с настройкой, а не литералом: `.env` разработчика может
     переопределить адрес).
  10. **`send_user_email` поднимает ошибку.** Мок `send_mail` с `OSError` →
      исключение уходит наверх; `notify_moderators` при том же сбое
      по-прежнему возвращает 0 (существующий тест остаётся зелёным).
  11. **`mail_check`.** Без адреса — печатает настройки и не отправляет;
      с адресом — письмо в `outbox`; пароль из `.env` не попадает в вывод.
  12. **Пример окружения.** `.env.example` содержит
      `DEFAULT_FROM_EMAIL=SportMemo <info@sportmemo.ru>` и `EMAIL_HOST_USER=`
      без значения: дефект «в примере снова noreply» должен ловиться тестом,
      а не глазами.

  В `tests/test_admin.py` тест `test_resolve_report_action` переписывается
  под `action_decide` и дополняется проверкой `ModerationAction` — сейчас он
  закрепляет поведение без журнала.

  Проверка: `uv run pytest tests/test_report_email.py tests/test_admin.py -q`.

  Файлы:
  - `tests/test_report_email.py`
  - `tests/test_admin.py`

### 4.12 DNS и README

- [ ] **Раздел «Почта и домен» с фактическим состоянием зоны**

  `README.md`, новый раздел после «Регистрация и вход»:

  1. Схема «приём VK WorkSpace + отправка Postbox» и почему так (Postbox
     не принимает входящие).
  2. Таблица записей из 2.7 с пометкой «проверено 2026-11-15» и командами
     проверки (`dig +short MX sportmemo.ru @8.8.8.8` и т. д.).
  3. Го́тчи: одна SPF на домен; значения MX/CNAME с точкой на конце для R01;
     имена DKIM-CNAME выдаёт только консоль Postbox — проверять статусом
     `Success` и заголовками письма, а не перебором селекторов.
  4. Что чем является в env (`EMAIL_HOST_USER` — идентификатор ключа,
     `EMAIL_HOST_PASSWORD` — секретная часть ключа), и что ключ живёт в `.env`.
  5. Как проверить: `manage.py mail_check <адрес>` и заголовки письма
     (`spf=pass`, `dkim=pass`, `dmarc=pass`).
  6. Обновить раздел «Состояние»: версия 0.33.0, число тестов, «этап 5,
     подэтап 5.1 закрыт, следующий — 5.2».

  Проверка: `grep -n "Почта и домен" README.md`.

  Файлы:
  - `README.md`

### 4.13 Локализация

- [ ] **`make i18n`, снять `fuzzy`, заполнить английский каталог**

  Новые строки: подписи письма заявителю, тема письма, предупреждение
  модератору, строка анонимам, сообщения команды `mail_check`.

  1. `make i18n`.
  2. `locale/ru/LC_MESSAGES/django.po`: снять `fuzzy`, выровнять пустые `msgstr`
     по `msgid`.
  3. `locale/en/LC_MESSAGES/django.po` — заполнить вручную (автоперевод
     не используем).
  4. `make compile` и `git diff --stat locale/` — только ожидаемые строки.

  Файлы:
  - `locale/ru/LC_MESSAGES/django.po`
  - `locale/en/LC_MESSAGES/django.po`

### 4.14 Документация проекта

- [ ] **`KODA.md` / `AGENTS.md`, бэклог, мастер-план**

  Синхронно в `KODA.md` и `AGENTS.md`:

  1. «Правила для кода»:
     - письмо человеку (`send_user_email`) ошибку не глотает, письмо модераторам
       (`notify_moderators`) — глотает; два правила стоят рядом и объясняют разницу;
     - письмо заявителю ставится только из сервиса через `transaction.on_commit`
       и фоновую задачу; внутри блокировки строки SMTP не вызывается;
     - письмо заявителю уходит только активному пользователю с непустым email;
     - решения по жалобам идут только через `services.resolve_report` /
       `reject_report`, включая админку: `status` и `resolution` в админке
       только для чтения;
     - `DEFAULT_FROM_EMAIL` — `SportMemo <info@sportmemo.ru>`; ящика `noreply@`
       не существует, ответы приходят в VK WorkSpace;
     - `EMAIL_HOST_USER` — идентификатор API-ключа, а не адрес; креды только
       в `.env`;
     - одна SPF-запись на домен; имена DKIM-селекторов берутся из консоли.
  2. «Аккаунты, команды, комментарии» → абзац «Почта (5.1)»: схема двух сервисов,
     письмо заявителю, строка анонимам, команда `mail_check`.
  3. «Текущее состояние»: 5.1 закрыт, версия 0.33.0, следующий — 5.2.

  `.koda/backlog.md`: таблица этапа 5 — 5.1 «готово», `P5-5` отмечена,
  `P5-9` (ящик) — «сделано в 5.1, форма — 5.11».

  Мастер-план: подраздел 5.1 получает «**Сделано** (дата)» с коротким итогом
  и фактическими значениями заголовков письма из шага 4.3.

  Файлы:
  - `KODA.md`
  - `AGENTS.md`
  - `.koda/backlog.md`
  - `.koda/plans/mobile-legal-deploy.md`

### 4.15 Проверка и коммит

- [ ] **Линтер, тесты, версия**

  ```bash
  make lint
  make test
  uv run python manage.py makemigrations --check --dry-run
  uv run python manage.py mail_check
  git status --short
  ```

  Миграций нет: поля и `choices` не меняются. Semver всё равно **minor**:
  новая возможность (письмо заявителю) и новый модуль задач. `pyproject.toml` →
  `0.33.0`, затем `uv lock`; в `uv.lock` меняется только строка версии проекта.

  Коммит:

  ```
  feat: send report decision email and set up postbox

  A person who files a report never learns what happened to it. Now the
  service sends a short letter with the decision text after a moderator
  resolves or rejects the report. The letter goes through Celery after
  the transaction commits, so the row lock is not held during SMTP.

  Mail now leaves from info@sportmemo.ru with a display name. The old
  default was noreply@ and no such mailbox exists, so replies bounced.
  Admin actions for reports also go through the service now: before this
  change they changed the status directly and wrote no journal entry.

  Bump version: 0.32.0 → 0.33.0 (minor)
  ```

  После коммита `git status` чистый, `.env` в коммит не попал
  (`git show --stat HEAD | grep -c ".env$"` → 0).

  Файлы:
  - `pyproject.toml`
  - `uv.lock`

---

## 5. Риски и как их ловим

| Риск | Как проверяем |
|---|---|
| API-ключ с ограниченным сроком — письма перестанут уходить молча | Шаг 4.3: срок действия проверяется в консоли, дата записывается в план |
| Провайдер не пустит отправку с `info@` без отдельного ящика | Снят: проверено 2026-11-15 (мастер-план) и повторно при планировании (SMTP-логин) |
| Секрет попал в git | `.env` в `.gitignore`; шаг 4.15 проверяет `git show --stat HEAD`; в плане, `.env.example` и README только имена переменных |
| Письмо отправляется внутри транзакции и держит `select_for_update` | `on_commit` + задача; в коде комментарий, в тестах — `django_capture_on_commit_callbacks` |
| Письмо уходит после отката транзакции | `on_commit` не вызывается при откате; тест 6 (гонка) |
| Письмо уходит анониму или удалённому аккаунту | Проверка `reporter and is_active and email` в одной функции; тесты 3–4 |
| Админка обходит сервис и письмо не уходит | Действия переведены на сервис, `status`/`resolution` только для чтения; тест 7 |
| Модератор пишет внутренним тоном, а текст читает заявитель | Предупреждение рядом с полем решения (4.10) |
| Вторая SPF-запись ломает домен целиком | В README: сначала смотреть существующую TXT, потом добавлять; состояние зоны зафиксировано |
| Смена порта на 587 в `.env` даёт молчаливый сбой | `EMAIL_USE_SSL` пробрасывается в compose явно; в README указано, что 587 требует `EMAIL_USE_TLS` |
| SMTP недоступен и запрос ждёт минутами | `EMAIL_TIMEOUT=10` + отправка только в фоновой задаче |
| Тесты зависят от `.env` разработчика | Сравнение с `settings.DEFAULT_FROM_EMAIL`, а не с литералом |
| Дубликат письма при потере ответа SMTP | Принимается осознанно: два письма лучше, чем ни одного |

---

## 6. Проверка подэтапа

- `make lint && make test` — зелено, число тестов выросло.
- `makemigrations --check --dry-run` — «No changes detected».
- `uv run pytest tests/test_report_email.py tests/test_admin.py tests/test_moderation_reports_ui.py -q` — зелено.
- `manage.py mail_check` печатает настройки без пароля.
- Живое письмо с `info@sportmemo.ru` приходит во «Входящие» личного ящика;
  в заголовках `spf=pass`, `dkim=pass`, `dmarc=pass`; ответ на письмо
  открывается в VK WorkSpace.
- Заявитель вошедшей жалобы получает письмо с текстом решения;
  аноним — нет; удалённый аккаунт — нет.
- Решение по жалобе из админки пишет `ModerationAction` и отправляет письмо.
- Версия 0.33.0 в `pyproject.toml` и `uv.lock`; `.env` не в коммите.

---

## 7. Открытые вопросы и замеры

Заполняется по ходу работы.

1. **Срок действия API-ключа Postbox** — проверить в консоли (шаг 4.3).
   Бессрочно или дата: ______.
2. **Заголовки тестового письма** (шаг 4.3): `spf=____`, `dkim=____`,
   `dmarc=____`, `From: ____`.
3. **`rua` в DMARC** — нужен ли отчёт о доставке на `info@`. Сейчас `p=none`
   без отчётов; включается одной правкой зоны, к запуску не требуется.
4. **Пересылка `info@` на личную почту владельца** — нужна ли: если да,
   настраивается в VK WorkSpace и в код не попадает.
