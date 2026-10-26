/* SportMemo — форма загрузки фото.
 *
 * Превью работает только в браузере: сервер о нём не знает, и без
 * JavaScript форма остаётся обычной. Год из EXIF подставляет сервер
 * на втором шаге, поэтому разбирать файл в браузере больше не нужно.
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
    var PREVIEW_NOTE = "#photo-preview-note";
    var PREVIEW_REMOVE = "#photo-preview-remove";

    /* Адрес превью держим, чтобы отозвать его при замене файла:
       иначе каждый выбор оставлял бы в памяти целое изображение. */
    var previewUrl = null;

    function root() {
        return document.querySelector(FORM);
    }

    function showPreview(file) {
        var box = document.querySelector(PREVIEW);
        var image = document.querySelector(PREVIEW_IMAGE);
        if (!box || !image || !window.URL || !window.URL.createObjectURL) {
            return;
        }
        previewUrl = window.URL.createObjectURL(file);
        image.src = previewUrl;
        image.hidden = false;
        hideNote();
        box.hidden = false;
    }

    function showNote() {
        var note = document.querySelector(PREVIEW_NOTE);
        if (note) {
            note.hidden = false;
        }
    }

    function hideNote() {
        var note = document.querySelector(PREVIEW_NOTE);
        if (note) {
            note.hidden = true;
        }
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
        hideNote();
        if (box) {
            box.hidden = true;
        }
    }

    function onFileChange(input) {
        var file = input.files && input.files[0];
        resetPreview();
        if (!file) {
            return;
        }
        /* Пустой тип бывает там, где система не знает расширения:
           это не повод отказывать, решение остаётся за сервером. */
        if (file.type && file.type.indexOf("image/") !== 0) {
            return;
        }
        showPreview(file);
    }

    /* Убрать выбранный файл: у поля файла нет нативной кнопки «очистить»,
       и без этого человек, выбравший не тот снимок, ищет нужный заново. */
    function onRemove() {
        var input = document.querySelector(FILE_FIELD);
        if (!input) {
            return;
        }
        input.value = "";
        resetPreview();
        /* Фокус возвращаем на поле файла: иначе он остаётся на кнопке,
           которой больше нет в разметке. */
        input.focus();
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
        var remove = document.querySelector(PREVIEW_REMOVE);
        if (remove) {
            remove.addEventListener("click", onRemove);
        }
        /* HEIC умеет читать не каждый браузер: битая картинка в превью
           выглядела бы поломкой формы. Прячем саму картинку и говорим,
           что файл выбран: контейнер с кнопкой «Убрать фото» остаётся. */
        var image = document.querySelector(PREVIEW_IMAGE);
        if (image) {
            image.addEventListener("error", function () {
                image.hidden = true;
                showNote();
            });
        }
    }

    init();
})();
