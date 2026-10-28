# SportMemo — подэтап 3.6: слияние дубликатов фото

Дата создания: 2026-10-28
Статус: **утверждён** (владелец, 2026-10-28)
Мастер-план: [`photo-processing.md`](photo-processing.md) (подэтап 3.6, решения 2.4)
Бэклог: [`.koda/backlog.md`](../backlog.md) — задача P3-6
Ветка: `feature/photo-merge-duplicates` (создаётся на шаге 1)
База: `master`, версия 0.22.0, 1322 теста
Финал: bump **0.22.0 → 0.23.0 (minor)** + `uv lock`

> **Для новой сессии.** Работа начинается с шага 1: `git switch -c feature/photo-merge-duplicates`.
> Реализацию вести строго по шагам, не переходя к следующему до проверки текущего.
> Файл плана поддерживается в актуальном состоянии: `[ ]` → `[x]` по мере выполнения.
> Отмечать шаг выполненным только после `grep`-проверки, что правки реально есть
> в целевых файлах.

---

## 1. Цель и границы

**Цель.** Дубликат не исчезает и не ломает ссылки: он сливается в канон, старая ссылка
отдаёт 301 на канон, связи (люди на фото, комментарии, жалобы, просмотры) переезжают
в канон. Решение принимает модератор — расстояние по хешу остаётся подсказкой (3.5).

**Что уже есть и используется:**

- `MergeableModel` (`apps/core/models.py`): `merged_into`, `is_merged`, `merge_into()`,
  `_merge_relations()` — точка расширения. Реализован у `Team`, `Person`, `Event`.
- `MergeActionsMixin` (`apps/core/admin_mixins.py`): действие `action_merge`, страница
  `merge/` с выбором цели, запись `ModerationAction(MERGE)`, редирект на карточку цели.
- `ModerationActionsMixin`, `BaseHistoryAdmin`, `MERGE_ACTIONS`.
- `apps/photos/similar.py`: `candidate_pool()`, `similar_photos()`, пороги 32/48 из 256.
- Страница `admin:photos_photo_similar` с таблицей кандидатов и **пустым местом
  под кнопку слияния** (комментарий-заглушка в шаблоне).
- `get_visible_or_404()` (`apps/core/shortcuts.py`): публичная выборка + показ
  неопубликованного автору и модератору, остальным 404.
- `Photo.duplicate_of` — самодельное FK на себя с constraint
  `photo_duplicate_of_not_self`, нигде не используется в логике.

**Что входит в подэтап:**

- `Photo` наследует `MergeableModel`; `duplicate_of` → `merged_into` двумя миграциями
  с переносом данных;
- `Photo._merge_relations(target)`: `PhotoPerson`, `Comment`, `Report`, `views_count`,
  `Person.avatar`, заполнение пустых полей канона;
- `MergeableModel.get_canonical()` — резолв цепочки с защитой от циклов;
- `merge_into()` резолвит цель до канона (цепочки запрещены);
- `RedirectToCanonical` и флаг `follow_merged` у `get_visible_or_404`, обработка
  в `photo_detail` → 301;
- `PhotoAdmin`: `MergeActionsMixin`, `merged_into` в readonly, кандидаты сужены
  до вида спорта;
- кнопка «Слить в это фото» на странице похожих;
- тесты, локализация, документация, версия.

**Что не входит:**

| Отложено | Куда |
|---|---|
| Перекроп уже загруженного фото | **P3-18** (нужен эндпоинт оригинала для автора) |
| Поиск дубликатов, повёрнутых на 90/180/270 | **P3-19** (четыре хеша на фото) |
| Автоматическое слияние по расстоянию | инвариант № 5: решает человек |
| Пакетная загрузка | **3.7** |
| Сборка мусора производных файлов слитого фото | **P3-11** |
| Пересчёт `display`/`phash` канона после слияния | модератор делает действием «Обработать заново» |
| Слияние источников (`Source`) | отдельная задача, если появится поток |

---

## 2. Решения

### 2.1 Что переносим, а что нет

| Объект | Решение |
|---|---|
| `PhotoPerson` | Переносим **по одной** с проверкой `(photo, person)`: `UniqueConstraint` иначе даст `IntegrityError` |
| `Comment` (generic FK) | Переносим одним `update()` по `content_type`/`object_id` |
| `Report` (generic FK) | **Переносим.** Жалоба «это я» на слитом снимке иначе повисла бы вне карточки канона |
| `ModerationAction` | **Не переносим.** Журнал фиксирует решение по конкретному объекту, история не переписывается (то же у команд) |
| `views_count` | Складываем |
| `Person.avatar` | Переводим на канон, иначе главное фото человека станет битой ссылкой |
| `source`, `year`, `team`, `event`, `taken_date` | Заполняем **пустые** у канона, не перетирая |
| `title`, `caption` | **Не трогаем** у канона: текст дубликата остаётся в истории |
| `display`, `phash`, `width`, `height`, `crop_data`, `exif` | **Не переносим:** они относятся к своему оригиналу, подмена нарушила бы связь `display` ← `original` |
| Файлы (`original`, `display`) | Остаются у слитого фото: сборка мусора — P3-11 |

**Почему `Report` переносим, а `ModerationAction` нет.** Жалоба — открытое дело
о конкретном снимке, и после слияния модератор должен видеть её в карточке канона.
Журнал — уже принятое решение: он отвечает на вопрос «что сделали с объектом #N»,
и переписывать его значит терять аудит.

**Почему производные поля не переносим.** `display` — производная от конкретного
оригинала. Подставить чужой `display` под свой `original` значит соврать и в админке
(рамка кропа), и в публичной карточке. Если у канона нет `display`, модератор жмёт
«Обработать заново» — кнопка уже есть с 3.3.

### 2.2 Слияние связей: `PhotoPerson` по одной

`UniqueConstraint(photo, person)` не даёт просто сделать `update(photo=target)`:
если человек уже отмечен на каноне, база отклонит запрос целиком, и часть связей
потеряется вместе с ошибкой. Поэтому:

```python
for link in self.photo_persons.select_related("person"):
    exists = PhotoPerson.objects.filter(photo=target, person_id=link.person_id).exists()
    if exists:
        link.delete()
    else:
        link.photo = target
        link.save(update_fields=["photo", "updated_at"])
```

Что теряется при совпадении: `role`, `position`, `note` дубликата. Это осознанно —
запись канона уже заполнена, а перетирать её данными дубликата значит менять то,
что модератор проверил. Примечание остаётся в истории `PhotoPerson`.

### 2.3 Цепочки слияний запрещены

`merge_into(target)` резолвит цель до канона **до** всех проверок: `target.get_canonical()`
или `ValueError`, если цепочка битая (цикл, глубина). В `merge_view` кандидаты
и проверка POST идут по одному queryset с `merged_into__isnull=True`, поэтому
«слить в уже слитое» нельзя и через подделанный POST.

`get_canonical()`:

```python
def get_canonical(self, max_depth=10):
    seen = {self.pk}
    current = self
    for _ in range(max_depth):
        if current.merged_into_id is None:
            return current
        if current.merged_into_id in seen:
            logger.warning("Цикл слияний у %s #%s", ...)
            return None
        seen.add(current.merged_into_id)
        current = current.merged_into
    logger.warning("Слишком длинная цепочка слияний у %s #%s", ...)
    return None
```

Один запрос на шаг цепочки: `select_related("merged_into")` здесь не годится —
цепочка разрешается в цикле, и заранее её длину не знают. Цепочки короткие
(их запрещено создавать), поэтому N запросов на разрешение приемлемы.

### 2.4 301 на канон, а не 404

Фотографиями делятся в мессенджерах: старая ссылка не должна вести на мёртвую
страницу. Три исхода `get_visible_or_404`:

| Ситуация | Результат |
|---|---|
| Объект в `public` и публичный | объект (как сейчас) |
| Объект в `public`, но черновик/скрытый | объект автору и модератору, остальным 404 |
| Объекта нет в `public` (удалён или слит) | **с `follow_merged=True`**: ищем в `model.objects`, резолвим канон; канон публичный → `RedirectToCanonical`; иначе 404 |
| Без флага `follow_merged` | 404 (поведение команд, событий и людей не меняется) |

Поиск канона идёт через `model.objects`, а не `public`: `merge_into()` вызывает
`soft_delete()`, и слитое фото в `public` отсутствует. Канон проверяется тем же
`is_published` и отсутствием `deleted_at` — иначе редирект вёл бы на 404.

`RedirectToCanonical` — исключение с атрибутом `url`; `photo_detail` ловит его
и отдаёт `redirect(url, permanent=True)`. Вьюха остаётся функцией без обёрток:
другие страницы флаг не передают и про исключение не знают.

### 2.5 Кандидаты в админке: без слитых, удалённых и чужих видов спорта

`MergeActionsMixin` сейчас отдаёт в кандидаты `model.objects.exclude(pk__in=source_pks)`
и принимает любую цель из POST. Для фото это неверно: слитое или удалённое фото
каноном быть не может, а фото другого вида спорта — тем более.

Решение: хук `merge_candidates(queryset, sources)` в миксине, по умолчанию — базовый
фильтр `merged_into__isnull=True, deleted_at__isnull=True`. `PhotoAdmin` переопределяет
хук и добавляет `sport_id__in=...`. Тот же queryset используется для валидации POST —
иначе подделанный запрос обошёл бы фильтр.

Для `Team`/`Person`/`Event` поведение меняется в лучшую сторону (слитые и удалённые
больше не предлагаются), но их существующие тесты это не затрагивают: проверки
касаются выбранных объектов, а не полного списка.

### 2.6 Миграция: две, как в мастер-плане

1. `0004_photo_merged_into`: `AddField merged_into` + `RunPython` — перенос
   `duplicate_of_id` → `merged_into_id` в `Photo` и `HistoricalPhoto`,
   затем `soft_delete` у фото, у которых был `duplicate_of` (иначе после
   переименования они остались бы в публичной выдаче).
2. `0005_remove_photo_duplicate_of`: `RemoveConstraint photo_duplicate_of_not_self`,
   `RemoveField duplicate_of`, `AddConstraint photo_merged_into_not_self`
   (`merged_into` не ссылается на себя).

Обратная миграция шага 1 копирует данные назад; `soft_delete` не откатывается —
восстановление удалённого объекта остаётся решением человека (модератор видит
слитые фото в админке по фильтру `deleted_at`).

`HistoricalPhoto` получает поле автоматически: `HistoricalRecords` есть на `Photo`.
Проверить, что `makemigrations` не тянет лишнего.

### 2.7 Мелочи, которые не должны разойтись

- `Photo.__str__`, `get_absolute_url`, `upload_state` не меняются.
- Кабинет автора (`/photos/mine/`) показывает слитое фото как «Удалено» с причиной
  «слито с дубликатом» — `upload_state` уже так работает, плашку не трогаем.
- `Photo.public` уже отсекает слитые (они `deleted_at`), `visible_photos()`,
  `photos_count()`, карта сайта и поиск менять не нужно — проверяется тестами.
- `similar.candidate_pool()` дополнительно отсекает слитые: кандидат с
  `merged_into__isnull=False` не должен предлагаться к слиянию повторно.

---

## 3. Шаги

### 1. [ ] Ветка

- Навыки: koda-coder.
- Файлы: —
- Содержание: `git switch -c feature/photo-merge-duplicates` от `master` (0.22.0).
- Критерий: ветка создана, `git status` чист.

### 2. [ ] Модель `Photo`: `MergeableModel` вместо `duplicate_of`

- Навыки: koda-coder.
- Файлы: `apps/photos/models.py`, `apps/photos/migrations/0004_*.py`,
  `apps/photos/migrations/0005_*.py`, `tests/test_photos.py`.
- Содержание:
  - `Photo(TimeStampedModel, StatusModel, SoftDeleteModel, MergeableModel)`;
    поле `duplicate_of` и его constraint удаляются, `merged_into` приходит из миксина;
  - `Photo._merge_relations(target)` по решению 2.1 и 2.2: `PhotoPerson` по одной,
    `Comment` и `Report` одним `update()` через `content_type`/`object_id`,
    `views_count` сложить, `Person.avatar` перевести на канон, пустые
    `source`/`year`/`team`/`event`/`taken_date` заполнить, `title`/`caption` не трогать;
  - `views_count` пишем через `update()`, а не `save()`: слияние не должно плодить
    записи истории на каждое фото (то же правило, что у счётчика просмотров);
  - docstring класса: «Дубликаты не удаляются, а сливаются» вместо `duplicate_of`;
  - миграции 0004 и 0005 по решению 2.6;
  - в `tests/test_photos.py` класс `TestPhotoDuplicate` переписать на `merged_into`:
    constraint «не на себя», ссылка на другое фото, обратная связь `merged_from`.
- Критерий: `makemigrations --check` пуст; `Photo` наследует `MergeableModel`;
  `photo.merged_into` работает; constraint `photo_merged_into_not_self` есть в миграции.

**Заметка для реализации.** `Report` импортировать внутри `_merge_relations()`,
а не на уровне модуля: `photos` не должен зависеть от `moderation` при загрузке
приложений. `Comment` — так же (или через `apps.get_model`), чтобы не создать цикл.

### 3. [ ] Ядро: `get_canonical()` и резолв цели в `merge_into`

- Навыки: koda-coder.
- Файлы: `apps/core/models.py`, `tests/test_core.py`.
- Содержание:
  - `MergeableModel.get_canonical(max_depth=10)` по решению 2.3, с `logger.warning`
    на цикл и на превышение глубины; возвращает `None` для битой цепочки;
  - `merge_into(target)`: `target = target.get_canonical() or raise ValueError`;
    запрет «сам с собой» и идемпотентность сохраняются; резолв идёт **до**
    `_merge_relations()`, чтобы связи не уехали в слитый объект;
  - docstring: почему цепочки запрещены (мастер-план 2.4, п. 10).
- Критерий: тесты — «A→B, затем B→C: `A.get_canonical() == C`»;
  «слияние A в B, где B уже слит в C, переносит связи в C»; «цикл (искусственно
  выставленные `merged_into`) даёт `None` и не зависает»; «`merge_into` в самого
  себя по-прежнему `ValueError`»; старые тесты `test_core.py` зелёные.

### 4. [ ] 301 на канон: `RedirectToCanonical` и `follow_merged`

- Навыки: koda-coder.
- Файлы: `apps/core/shortcuts.py`, `apps/photos/views.py`,
  `tests/test_visible_or_404.py`.
- Содержание:
  - `class RedirectToCanonical(Exception)` с атрибутом `url`;
  - `get_visible_or_404(model, *, request=None, follow_merged=False, **lookup)`:
    текущая логика без изменений; при `Http404`-исходе и `follow_merged=True` —
    поиск в `model.objects`, резолв канона, проверка «канон опубликован и не удалён»,
    иначе `Http404`;
  - `photo_detail` вызывает хелпер с `follow_merged=True` и ловит исключение:
    `redirect(url, permanent=True)`;
  - `comments/targets.resolve_target` и остальные вызовы флаг не передают.
- Критерий: тесты — «слитое фото отдаёт 301 на `/photos/<канон>/`»; «канон-черновик
  даёт 404»; «удалённый канон даёт 404»; «цепочка A→B→C: A отдаёт 301 сразу на C»;
  «слитая команда по-прежнему 404» (поведение не изменилось).

**Заметка для реализации.** В `photo_detail` после редиректа ничего не выполняется:
`RedirectToCanonical` поднимается до перечитывания объекта и до `F("views_count") + 1`.
Просмотр засчитывается канону — это правильно: человек увидел именно его.

### 5. [ ] Админка фото: действие слияния и кандидаты

- Навыки: koda-coder.
- Файлы: `apps/photos/admin.py`, `apps/core/admin_mixins.py`,
  `apps/photos/similar.py`, `tests/test_admin.py`.
- Содержание:
  - `PhotoAdmin(MergeActionsMixin, ModerationActionsMixin, BaseHistoryAdmin)`;
    `actions = (*MERGE_ACTIONS, *MODERATION_ACTIONS, "action_reprocess")`;
  - `autocomplete_fields`: `duplicate_of` → `merged_into`;
  - `merged_into` в `readonly_fields` и в блоке «Служебное» (не «Производные»):
    это не производная картинки, а судьба записи;
  - `MergeActionsMixin.merge_candidates(queryset, sources)` по решению 2.5;
    `merge_view` берёт кандидатов хуком и валидирует POST тем же queryset;
    при пустом списке кандидатов страница показывает «Нет подходящих объектов»,
    а не пустой `<select>`;
  - `PhotoAdmin.merge_candidates` сужает до `sport_id` источников;
  - `similar.candidate_pool()`: `.filter(merged_into__isnull=True)`.
- Критерий: тесты — «у фото есть действие `action_merge`»; «страница слияния открывается,
  содержит источник и кандидата, не содержит сам источник»; «кандидаты не содержат
  слитых, удалённых и фото другого вида спорта»; «POST с целью из другого вида спорта
  отклоняется и связи не переносятся»; «слияние через админку пишет
  `ModerationAction(MERGE)`»; «старые тесты слияния команд, людей и событий зелёные».

### 6. [ ] Страница «Похожие фото»: кнопка «Слить в это фото»

- Навыки: koda-coder.
- Файлы: `templates/admin/photos/photo/similar.html`,
  `templates/admin/core/merge_form.html`,
  `locale/ru/LC_MESSAGES/django.po`, `locale/en/LC_MESSAGES/django.po`,
  `tests/test_photo_dedup.py`.
- Содержание:
  - в каждой строке кандидата — маленькая POST-форма:
    `action="{% url 'admin:photos_photo_merge' %}"`, `ids=<кандидат>`,
    `target=<текущее фото>`, `csrf_token`; подпись «Слить в это фото» и `confirm`
    через `onclick` не ставим (админка без JS должна работать), рядом `help`-текст
    «кандидат станет дубликатом этого фото»;
  - комментарий-заглушка из 3.5 удаляется;
  - `templates/admin/core/merge_form.html`: текст «Связи (фото, состав, события)»
    заменить нейтральным — шаблон общий для четырёх моделей, а у фото состав
    не переносится;
  - `make i18n`, снять `fuzzy`, выровнять русские `msgstr` по `msgid`,
    английские заполнить вручную, `make compile`.
- Критерий: тесты — «страница похожих содержит кнопку слияния для каждого кандидата»;
  «POST с `ids` кандидата и `target` текущего фото сливает кандидата и редиректит
  на карточку канона»; «`original` по-прежнему не выводится»; `fuzzy`-блоков нет,
  пустых `msgstr` в английском каталоге нет.

### 7. [ ] Тесты подэтапа

- Навыки: koda-coder.
- Файлы: `tests/test_photo_dedup.py`, `tests/test_visible_or_404.py`,
  `tests/test_photos.py`, `tests/test_core.py`, `tests/test_admin.py`.
- Содержание (сводка того, что должно быть покрыто):
  - перенос: `PhotoPerson` без `IntegrityError`, дубль человека, `Comment`, `Report`,
    `views_count`, `Person.avatar`, заполнение пустых полей без перетирания,
    `title`/`caption` канона не изменились;
  - цепочки: A→B→C одним переходом, цикл → 404, канон-черновик → 404,
    удалённый канон → 404, `merge_into` в слитую цель;
  - выдача: слитое фото исчезает из галереи, карты сайта, поиска и `photos_count()`;
    в `/photos/mine/` показывается как «Удалено» с причиной;
  - админка: действие, страница, фильтр кандидатов, подделанная цель;
  - регресс: слияние команд, людей и событий работает как раньше.
- Критерий: `make test` зелёный; новые тесты падают на коде до правок
  (проверить выборочно — слияние, 301, кандидаты).

### 8. [ ] Проверка

- Навыки: koda-coder.
- Содержание:
  - `make lint && make test`; `uv run python manage.py check`;
    `uv run python manage.py makemigrations --check --dry-run`;
  - ручной прогон в админке на демо-архиве: выбрать фото → «Слить дубликаты
    в один объект» → выбрать цель → проверить редирект на канон, историю,
    исчезновение из галереи;
  - проверить старую ссылку слитого фото в браузере: 301 и адрес канона;
  - проверить страницу похожих: кнопка сливает кандидата в текущее фото;
  - проверить, что кабинет автора показывает слитое фото как «Удалено»;
  - `git diff --stat` перед коммитом: только ожидаемые файлы.
- Критерий: критерии готовности мастер-плана (раздел 3.6) выполнены полностью.

### 9. [ ] Документация и версия

- Навыки: semver-checker, koda-coder.
- Файлы: `KODA.md`, `AGENTS.md`, `.koda/backlog.md`,
  `.koda/plans/photo-processing.md`, `README.md`, `pyproject.toml`, `uv.lock`.
- Содержание:
  - `KODA.md` / `AGENTS.md`: правила — слитое фото отдаёт 301 на канон, цепочки
    запрещены (`get_canonical()`), производные поля не переносятся, `Report`
    переносится, а `ModerationAction` нет, кандидаты слияния живут в
    `merge_candidates()`; в «Текущее состояние» — 0.23.0, 3.6 завершён, следующий 3.7;
  - мастер-план: `3.6` → `[x]`, итоги в разделе подэтапа, строка в «Прогресс»,
    закрыть открытые вопросы, если они решены по ходу;
  - бэклог: P3-6 закрыта, итог абзацем;
  - README: раздел про дубликаты — 301 на канон, что переносится, что нет,
    почему производные поля не переносятся; счётчик тестов;
  - semver: **minor** 0.22.0 → 0.23.0 + `uv lock`;
  - коммит в ветке `feature/photo-merge-duplicates`, затем squash-merge в `master`
    и удаление ветки (`git branch -D`, см. KODA.md).
- Критерий: документация совпадает с кодом, версия синхронизирована в `uv.lock`,
  `make lint && make test` зелёные на `master` после merge.

---

## 4. Открытые вопросы

1. **Слитое фото и автор.** `uploaded_by` у слитого фото остаётся; в кабинете оно
   показывается как «Удалено». Если владелец захочет убирать слитые фото из кабинета
   совсем — это одна строка в `my_photos`, решается по опыту.
2. **Счётчик просмотров канона.** Складываем при слиянии; отдельного аудита
   «сколько пришло от дубликатов» не заводим — поле одно.
3. **Жалобы на слитое фото.** После переноса жалоба указывает на канон; в её истории
   остаётся прежний `object_id`. Если модератору понадобится исходный адрес,
   он есть в истории `Report` и в `ModerationAction`.
4. **301 и кэш мессенджеров.** 301 кэшируется надолго: если канон позже скроют,
   старая ссылка всё равно приведёт на него. Для дубликатов это приемлемо
   (решение мастер-плана 2.4).

---

## 5. Прогресс

| # | Шаг | Статус |
|---|---|---|
| 1 | Ветка | [ ] |
| 2 | Модель `Photo`: `MergeableModel` вместо `duplicate_of` | [ ] |
| 3 | Ядро: `get_canonical()` и резолв цели | [ ] |
| 4 | 301 на канон: `RedirectToCanonical` и `follow_merged` | [ ] |
| 5 | Админка фото: действие слияния и кандидаты | [ ] |
| 6 | Страница «Похожие фото»: кнопка слияния | [ ] |
| 7 | Тесты подэтапа | [ ] |
| 8 | Проверка | [ ] |
| 9 | Документация и версия | [ ] |
