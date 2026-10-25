/* SportMemo — форма загрузки фото.
 *
 * Превью и подсказка года работают только в браузере: сервер о них
 * не знает, и без JavaScript форма остаётся обычной. Год из EXIF —
 * подсказка, а не запись: поле заполняется, только если человек его
 * не заполнил, и рядом появляется пояснение, откуда год взялся.
 *
 * Тексты приходят из шаблона через data-атрибуты: строки интерфейса
 * остаются в gettext, а не в скрипте — то же правило, что в gallery.js.
 */

(function () {
    "use strict";

    var FORM = ".photo-form";
    var FILE_FIELD = "[name='original']";
    var PREVIEW = "#photo-preview";
    var PREVIEW_IMAGE = "#photo-preview-image";
    var YEAR_FIELD = "#id_year";
    var YEAR_HINT = "#year-hint";

    /* Адрес превью держим, чтобы отозвать его при замене файла:
       иначе каждый выбор оставлял бы в памяти целое изображение. */
    var previewUrl = null;

    /* Номер последнего разбора: ответ exifr по старому файлу
       пришёл бы после замены и подставил бы чужой год. */
    var requestId = 0;

    /* Год, подставленный скриптом. При замене файла его нужно убрать:
       иначе в поле остался бы год от другого снимка, а подсказка
       «год взят из файла» уже скрыта — человек не понял бы, откуда он. */
    var autoYear = null;

    function root() {
        return document.querySelector(FORM);
    }

    function text(name) {
        var form = root();
        return form && form.dataset ? form.dataset[name] || "" : "";
    }

    function showPreview(file) {
        var box = document.querySelector(PREVIEW);
        var image = document.querySelector(PREVIEW_IMAGE);
        if (!box || !image || !window.URL || !window.URL.createObjectURL) {
            return;
        }
        previewUrl = window.URL.createObjectURL(file);
        image.src = previewUrl;
        box.hidden = false;
    }

    function resetPreview() {
        var box = document.querySelector(PREVIEW);
        var image = document.querySelector(PREVIEW_IMAGE);
        if (previewUrl) {
            window.URL.revokeObjectURL(previewUrl);
            previewUrl = null;
        }
        if (image) {
            image.removeAttribute("src");
        }
        if (box) {
            box.hidden = true;
        }
    }

    function showYearHint() {
        var hint = document.querySelector(YEAR_HINT);
        var message = text("yearHint");
        if (!hint || !message) {
            return;
        }
        hint.textContent = message;
        hint.hidden = false;
    }

    function resetYearHint() {
        var hint = document.querySelector(YEAR_HINT);
        var year = document.querySelector(YEAR_FIELD);
        /* Убираем только своё значение: год, введённый человеком,
           не наше дело — даже если он совпал с подставленным. */
        if (year && autoYear !== null && year.value === autoYear) {
            year.value = "";
        }
        autoYear = null;
        if (!hint) {
            return;
        }
        hint.textContent = "";
        hint.hidden = true;
    }

    function fillYear(file, token) {
        var year = document.querySelector(YEAR_FIELD);
        if (!year || year.value) {
            return;
        }
        if (!window.exifr || typeof window.exifr.parse !== "function") {
            return;
        }
        window.exifr
            .parse(file, ["DateTimeOriginal"])
            .then(function (exif) {
                if (token !== requestId || !exif) {
                    return;
                }
                var taken = exif.DateTimeOriginal;
                /* Проверка по методу, а не `instanceof Date`: `Date`
                   из другого окна не прошёл бы проверку, хотя это дата. */
                if (!taken || typeof taken.getFullYear !== "function") {
                    return;
                }
                /* Человек мог начать вводить год, пока читался файл:
                   его значение важнее подсказки. */
                if (year.value) {
                    return;
                }
                year.value = String(taken.getFullYear());
                autoYear = year.value;
                showYearHint();
            })
            .catch(function () {
                /* Файл без EXIF или битый — обычное дело. Поле года
                   не трогаем и в консоль не шумим. */
            });
    }

    function onFileChange(input) {
        var file = input.files && input.files[0];
        var token = (requestId += 1);
        resetPreview();
        resetYearHint();
        if (!file) {
            return;
        }
        /* Пустой тип бывает там, где система не знает расширения:
           это не повод отказывать, решение остаётся за сервером. */
        if (file.type && file.type.indexOf("image/") !== 0) {
            return;
        }
        showPreview(file);
        fillYear(file, token);
    }

    function init() {
        var form = root();
        if (!form) {
            return;
        }
        form.addEventListener("change", function (event) {
            var target = event.target;
            if (target && target.matches && target.matches(FILE_FIELD)) {
                onFileChange(target);
            }
        });
        /* HEIC умеет читать не каждый браузер: битая картинка в превью
           выглядела бы поломкой формы, поэтому прячем контейнер. */
        var image = document.querySelector(PREVIEW_IMAGE);
        if (image) {
            image.addEventListener("error", function () {
                var box = document.querySelector(PREVIEW);
                if (box) {
                    box.hidden = true;
                }
            });
        }
    }

    init();
})();
