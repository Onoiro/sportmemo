# Frontend vendor libraries

Файлы скачаны вручную и лежат в репозитории. CDN не используем: архив должен открываться
без интернета, а версии не должны меняться под ногами. Обновляет их whitenoise-манифест,
поэтому версия в имя папки не выносится.

Дата загрузки: 2026-10-11. Источник — jsDelivr (`cdn.jsdelivr.net`), файлы взяты из npm-пакетов.

| Библиотека | Версия | Путь | Лицензия |
|---|---|---|---|
| Bootstrap (CSS) | 5.3.8 | `bootstrap/css/bootstrap.min.css` | MIT |
| Bootstrap (JS bundle) | 5.3.8 | `bootstrap/js/bootstrap.bundle.min.js` | MIT |
| Bootstrap Icons (CSS) | 1.13.2 | `bootstrap-icons/bootstrap-icons.min.css` | MIT |
| Bootstrap Icons (шрифты) | 1.13.2 | `bootstrap-icons/fonts/` | MIT |
| htmx | 2.0.11 | `htmx/htmx.min.js` | BSD-2-Clause |
| Alpine.js | 3.17.4 | `alpine/alpine.min.js` | MIT |

## Важно: шрифты иконок

`bootstrap-icons.min.css` ссылается на шрифты **относительным** путём
(`url("fonts/bootstrap-icons.woff2?...")`). Поэтому шрифты обязаны лежать в
`bootstrap-icons/fonts/` рядом с CSS. Если перенести CSS в другое место, иконки пропадут.

## Контрольные суммы (sha256)

```
d85327d99c7a3ee1f9b5d0500d1370acea3ad2db39c163c2f51f232baedbdede  bootstrap/css/bootstrap.min.css
e4fd49181388c48ec5040bd3fe66f57c29c8e67fcd8502b3354b96ec7ab47cc7  bootstrap/js/bootstrap.bundle.min.js
79c99c1a1e0e937f808a58165728b7ed00800f02ef5efcfcd10dfe71464e675d  bootstrap-icons/bootstrap-icons.min.css
2b7689746f87e0beeae25338bc021dd2515c2f6f003f3d3d064d878d380b2150  bootstrap-icons/fonts/bootstrap-icons.woff2
24929e8a44da027588f833d4e82240ee734bd65bf962b08b0545af58a9ce80fa  bootstrap-icons/fonts/bootstrap-icons.woff
d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717  htmx/htmx.min.js
232519394c6c8fdba6f362b1d9da16106db513cdbf899011f00daab4051df31c  alpine/alpine.min.js
```

## Как обновить

1. Проверить актуальную версию на [getbootstrap.com](https://getbootstrap.com/),
   [icons.getbootstrap.com](https://icons.getbootstrap.com/), [htmx.org](https://htmx.org/),
   [alpinejs.dev](https://alpinejs.dev/).
2. Скачать файлы из npm-пакета через jsDelivr, например:

   ```bash
   curl -fsSL -o static/vendor/bootstrap/css/bootstrap.min.css \
       https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css
   ```

3. Пересчитать суммы (`sha256sum static/vendor/**/*`) и обновить таблицу и блок выше.
4. Прогнать `make test` и открыть сайт: сломанный CSS или пропавшие иконки видно сразу.
