# SportMemo — доработки кабинета «Мои фото»: статика и терминология

Дата создания: 2026-11-08
Статус: **на утверждении** — контекст для новой сессии, решения согласованы владельцем
Предыдущий план: [`photo-cabinet-tabs.md`](photo-cabinet-tabs.md) — выполнен полностью
Бэклог: [`.koda/backlog.md`](../backlog.md) — P3-23 (готово), заводится P5-14
Ветка: `fix/cabinet-words-and-static` (создаётся от `master` после вливания
`feature/photo-cabinet-tabs`)
Версия на входе: **0.29.0**, тестов 1594

> **Для новой сессии.** Читать целиком. Работа состоит из двух независимых
> коммитов: (1) починить `collectstatic`, (2) убрать двусмысленное слово
> «архив» из кабинета. Ничего другого делать не нужно.

---

## 0. Состояние на 2026-11-08 (проверено)

```
master                        11f0915  docs: add the plan for cabinet tabs
feature/photo-cabinet-tabs    4a688a5  step 10: document the cabinet tabs and bump the version
```

- `master` опережает `origin/master` на 1 коммит (план вкладок) и **не запушен**;
- ветка `feature/photo-cabinet-tabs` содержит 10 коммитов (шаги 1–10 плана),
  версия в ней **0.29.0**, **в `master` не влита**, на `origin` не отправлена;
- дерево чистое, незакоммиченных правок нет;
- `uv run pytest --collect-only -q` → **1594 теста**;
- миграция `apps/photos/migrations/0006_photo_cabinet_removed_at.py` уже в ветке;
- всё реализованное владелец проверил в браузере, замечаний к поведению нет —
  только два пункта ниже.

**Первое действие новой сессии — решить, вливать ли ветку.** По правилам проекта
это squash-merge в `master` и удаление ветки (`git branch -D` после проверки
`git diff --stat`). Без вливания патчи придётся делать поверх ветки, а она
содержит уже принятую работу. Рекомендация: влить, затем ветвиться для патчей.

---

## 1. Патч 1 — `collectstatic` падает на sourcemap вендора

### Симптом

```
whitenoise.storage.MissingFileError: The file
'vendor/bootstrap/js/bootstrap.bundle.min.js.map' could not be found
```

### Причина (проверено)

`ManifestStaticFilesStorage` (и whitenoise поверх него) обязан переписать
`sourceMappingURL` на хешированное имя и требует, чтобы файл карты существовал
(`django/contrib/staticfiles/storage.py`, паттерны `*.css` и `*.js`). В `static/vendor/`
лежат только минифицированные файлы, карт `.map` нет и не будет.

Задеты два файла:

| Файл | Строка |
|---|---|
| `static/vendor/bootstrap/js/bootstrap.bundle.min.js` | последняя строка: `//# sourceMappingURL=bootstrap.bundle.min.js.map` |
| `static/vendor/bootstrap/css/bootstrap.min.css` | строка 6: `/*# sourceMappingURL=bootstrap.min.css.map */` |

Остальные вендорные файлы (`htmx`, `alpine`, `cropperjs`, `bootstrap-icons`) карт
не упоминают — проверено хвостами файлов.

### Решение — вариант A (выбран владельцем)

Удалить эти две строки целиком (вместе с переводом строки) и пересчитать суммы.

Шаги:

1. Вырезать строку из `bootstrap.bundle.min.js` (последняя строка файла).
2. Вырезать строку `/*# sourceMappingURL=bootstrap.min.css.map */` из
   `bootstrap.min.css`.
3. `sha256sum static/vendor/bootstrap/js/bootstrap.bundle.min.js static/vendor/bootstrap/css/bootstrap.min.css`
4. Обновить блок «Контрольные суммы (sha256)» в `static/vendor/README.md`
   и дописать в раздел «Как обновить» пункт: при обновлении Bootstrap удалять
   строки `sourceMappingURL` (карт в репозитории нет).
5. Проверить: `make collectstatic` проходит без ошибок, `staticfiles/staticfiles.json`
   содержит хешированные имена.

Текущие суммы (до правки, для сверки):

```
e4fd49181388c48ec5040bd3fe66f57c29c8e67fcd8502b3354b96ec7ab47cc7  bootstrap/js/bootstrap.bundle.min.js
d85327d99c7a3ee1f9b5d0500d1370acea3ad2db39c163c2f51f232baedbdede  bootstrap/css/bootstrap.min.css
```

### Отдельный дефект, который надо завести, но не чинить здесь

`collectstatic` **не запускается** ни в `Dockerfile`, ни в CI. При
`CompressedManifestStaticFilesStorage` без манифеста боевой сайт отдаст 500
на любой `{% static %}`. Это часть подэтапа 5.7, не этого патча.

Задача для бэклога: **P5-14** «`collectstatic` в сборке образа и манифест статики»,
приоритет P0, подэтап 5.7 — добавить `python manage.py collectstatic --noinput`
в `Dockerfile` (или в entrypoint) и проверку манифеста в чек-лист деплоя.

### В `dev` собирать статику не нужно

`DEBUG=True` в `.env`, `runserver` отдаёт файлы прямо из `static/`. Именно поэтому
ошибка не всплывала до сих пор. В ответе владельцу это стоит повторить.

---

## 2. Патч 2 — слово «архив» вводит в заблуждение

### Замечание владельца (принято)

Вкладка «В архиве» читается как «заархивировано, не выложено» — то есть наоборот.
Плюс сообщение «Фото убрано из списка. Оно осталось в архиве.» для отклонённого фото
прямо врёт: в публичной части его никогда не было.

### Правило терминологии (зафиксировать в `KODA.md` / `AGENTS.md`)

- **«опубликовано»** — про видимость всем (статус `published`);
- **«в списке»** — про кабинет автора;
- **«архив»** — только про проект целиком («Публичный архив SportMemo»,
  «Поиск по архиву», «лет в архиве»). Как синоним «опубликовано» не использовать.

### Таблица переименований (согласована)

| Было | Стало |
|---|---|
| вкладка «В архиве» | **«Опубликованы»** |
| вкладка «Не в архиве» | **«Не опубликованы»** |
| `?tab=archive` | `?tab=published` |
| `?tab=failed` | `?tab=unpublished` |
| `TAB_ARCHIVE` | `TAB_PUBLISHED` |
| `TAB_FAILED` | `TAB_UNPUBLISHED` |
| «он попадает в архив сразу» (шапка кабинета) | «он публикуется сразу» |
| «он попадёт в архив сразу или после проверки» | «он будет опубликован сразу или после проверки» |
| «Изменения сохранены. Фото остаётся в архиве.» | «Изменения сохранены. Фото остаётся опубликованным.» |
| «Фото убрано из списка. Оно осталось в архиве.» | «Фото убрано из списка. Вернуть его можно на вкладке «Убранные».» |
| «Эти фото остаются в архиве и открываются по ссылке» (подпись под панелью) | «Уборка не удаляет фото: вернуть его в список можно в любой момент.» |
| «Фото, убранные из списка, остаются в архиве и открываются по ссылке.» (пустое состояние) | «Уборка не удаляет фото: вернуть его в список можно в любой момент.» |

Последние две строки — **один и тот же текст**: подпись и пустое состояние должны
говорить одинаково, чтобы термин не разъехался. Один `msgid` на оба места.

`TAB_FAILED` переименовывается в `TAB_UNPUBLISHED` не ради красоты: имя `failed`
совпадает с `ProcessingStatus.FAILED` и читается как «только сломанные», хотя вкладка
собирает ещё `rejected`, `hidden`, `deleted`.

### Файлы и точки правки (номера строк — на 0.29.0, после правок сдвинутся)

Код:

- `apps/photos/cabinet.py` — константы `TAB_ARCHIVE`/`TAB_FAILED` (строки 26, 28),
  `TAB_ORDER` (32), `TAB_LABELS` (36, 38), `TAB_NOTES` (45), `tab_filter` (105, 109),
  docstring модуля (строки 7–10: «Не в архиве»);
- `apps/photos/views.py` — строка 189 (сообщение правки), 295 (сообщение уборки);
- шаблоны: `templates/photos/my_photos.html` (15, 104 — комментарий, 144, 152),
  `templates/photos/partials/cabinet_tabs.html` — править не нужно (подписи приходят
  из `cabinet.py`);
- `templates/catalog/my_teams.html` (17, 47), `templates/catalog/my_events.html` (17, 47) —
  те же «попадает в архив сразу»; без них термин разъедется по трём кабинетам.

Тесты `tests/test_photo_cabinet.py`:

- таблица `upload_state → вкладка` (строки 63–69);
- сумма вкладок (124–126), выборка (216), запросы `?tab=` (331, 347, 392, 394).

Локализация (`make i18n`, fuzzy снять, английский заполнить вручную):

- удаляемые `msgid`: «В архиве», «Не в архиве», «Эти фото остаются в архиве и
  открываются по ссылке», «Фото убрано из списка. Оно осталось в архиве.»,
  «Фото, убранные из списка, остаются в архиве и открываются по ссылке.»,
  «Изменения сохранены. Фото остаётся в архиве.»;
- новые: «Опубликованы», «Не опубликованы», «Уборка не удаляет фото: вернуть его
  в список можно в любой момент.», «Изменения сохранены. Фото остаётся
  опубликованным.», «Фото убрано из списка. Вернуть его можно на вкладке «Убранные».»,
  плюс три шапки кабинетов и две строки пустых состояний;
- `locale/en/LC_MESSAGES/django.po`: сейчас там `"In the archive"` (1363),
  `"Not in the archive"` (1371), `"These photos stay in the archive and open by
  link"` (1379) — заменить на `"Published"`, `"Not published"`,
  `"Removing a photo does not delete it: you can put it back any time."`.

Документация (синхронно):

- `KODA.md`: 298–306 (абзац про вкладки), 736–745 (правила для кода);
- `AGENTS.md`: 307–310, 754–757 (`"In the archive"` → `"Published"`);
- `README.md`: 208 (ключи `?tab=`), 996–1013 (таблица вкладок и пояснение),
  1383–1386 (перечень тестов);
- `.koda/backlog.md`: строка 183 — в описании P3-23 упомянуть новые подписи;
- `.koda/plans/photo-cabinet-tabs.md`: статус «на утверждении» → «завершён»,
  добавить раздел «Результат» с версией и числом тестов (шаг 10 его не дописал).

### Чего в этом патче НЕ делать

- не менять вкладку «Все» и её правило;
- не трогать `Photo.upload_state` и подписи плашек («Опубликовано», «Отклонено» —
  они корректны);
- не переименовывать «архив» в публичных текстах проекта (футер, главная,
  поиск, «лет в архиве») — там слово значит именно коллекцию;
- не менять `P2-12`-решение «название черновика команды выводится текстом».

---

## 3. Проверки (обязательны для обоих коммитов)

```bash
make lint
make test
uv run python manage.py makemigrations --check --dry-run   # схема не меняется
make collectstatic                                        # только патч 1
grep -rn "В архиве\|Не в архиве\|tab=archive\|tab=failed\|TAB_ARCHIVE\|TAB_FAILED" \
    apps/ templates/ tests/ README.md KODA.md AGENTS.md .koda/backlog.md
# после патча 2 — пусто (кроме «В архиве пока нет команд/событий/фотографий»,
# это другие тексты про пустую коллекцию, их не трогаем)
```

Ручная проверка в браузере: `/photos/mine/` — подписи вкладок, счётчики,
сообщение после «Убрать из списка», пустое состояние «Убранных»; `Ctrl+Shift+R`
перед проверкой (CSS кэшируется браузером).

### Semver

Каждый коммит — **patch**: 0.29.0 → 0.29.1 → 0.29.2, навык `semver-checker`,
`uv lock` после каждой правки версии. Сообщения коммитов — на простом английском,
строка `Bump version: ...` в теле.

Черновики:

```
fix: let collectstatic work with vendor files

The vendor Bootstrap files point to source map files. The static file
storage must rewrite these paths and fails when a map is missing.

We do not keep the maps: they are large and only help to debug someone
else's library. The two comment lines are removed instead.

Bump version: 0.29.0 → 0.29.1 (patch)
```

```
fix: use clear words in the photo cabinet

The tab "In the archive" read like "archived, not published", which is
the opposite of what it means. The message after removing a photo said
the photo stayed in the archive, but a rejected photo was never there.

Now "published" means visible to everyone, "in the list" means the
author's cabinet, and "archive" means the whole project only.

Bump version: 0.29.1 → 0.29.2 (patch)
```

---

## 4. Готчи, найденные в сессии 2026-11-08

- **`collectstatic` в dev не нужен**: `DEBUG=True`, `runserver` отдаёт `static/`
  напрямую. Собранная копия в `staticfiles/` (в `.gitignore`) на страницу не влияет.
- **Подчёркнутые вкладки в одну строку — это кэш браузера**, а не ошибка вёрстки.
  Проверено: CSS с классами `.cabinet-tab` отдаётся (200, 50824 байта), стили на
  месте. После `Ctrl+Shift+R` владелец подтвердил, что вид нормальный.
- **Heredoc не работает** в `run_shell_command`: `<<'EOF'` обрывается с
  `unexpected end of file`. Для сообщений коммита писать текст через `write_file`
  в `.git/COMMIT_MSG_TMP.txt`, затем `git commit -F`, затем удалить файл.
- **`browser` `evaluate` выключен** в настройках Koda Desktop; `screenshot` может
  падать по таймауту. Для визуальной проверки хватает `snapshot`.
- **`ALLOWED_HOSTS`** не содержит `testserver`: рендер через `django.test.Client`
  в shell делать с `Client(HTTP_HOST="localhost")`.
- **Порядок вкладок и сумма** держатся тестом паритета, а не дисциплиной: при
  переименовании ключей тест обязан остаться тем же по смыслу.
- `?tab=` не участвует ни в одном адресе, кроме панели, — ломать существующие
  ссылки переименованием ключей не страшно (фича ещё не в проде).

---

## 5. Порядок работы новой сессии

1. Влить `feature/photo-cabinet-tabs` в `master` (squash), удалить ветку
   (`git branch -D` после `git diff --stat`), прогнать `make lint && make test`.
2. Ветка `fix/cabinet-words-and-static` от `master`.
3. Патч 1 (sourcemap) → проверки → коммит.
4. Патч 2 (терминология) → локализация → документация → проверки → коммит.
5. Влить ветку в `master`, удалить, `git status` чистый.
6. Завести `P5-14` (collectstatic в образе) в бэклоге — если не сделано в шаге 3.

---

## 6. Прогресс

| # | Шаг | Статус |
|---|---|---|
| 1 | Влить `feature/photo-cabinet-tabs` в `master` | [ ] |
| 2 | Патч 1: sourcemap и контрольные суммы | [ ] |
| 3 | Патч 2: терминология в коде, шаблонах, тестах | [ ] |
| 4 | Локализация и документация патча 2 | [ ] |
| 5 | Проверки и вливание ветки | [ ] |
| 6 | Завести P5-14 в бэклоге | [ ] |
