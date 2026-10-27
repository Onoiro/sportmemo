/* SportMemo — выбор и обрезка кадра на первом шаге загрузки.
 *
 * Кроп — инструкция обработки (`crop_data`), а не новый файл: оригинал
 * остаётся целым, а сервер вырежет кадр при пересчёте `display`.
 *
 * EXIF нормализует `createImageBitmap(..., {imageOrientation: "from-image"})`,
 * а не `checkOrientation` у Cropper: браузер уже развернул картинку, и вторая
 * нормализация повернула бы кадр ещё раз. Так у браузера и у сервера одна
 * система координат — «как видит человек».
 *
 * Тексты приходят из шаблона через data-атрибуты: строки интерфейса
 * остаются в gettext, а не в скрипте — то же правило, что в gallery.js.
 */

(function () {
    "use strict";

    var ROOT = "#photo-crop";
    var FORM = ".photo-form";
    var FILE_FIELD = "[name='original']";
    var CROP_FIELD = "[name='crop_data']";
    var STAGE = "#photo-crop-stage";
    var CANVAS = "#photo-crop-canvas";
    var PREVIEW = "#photo-crop-preview";
    var NOTE = "#photo-crop-note";
    var ACTIONS = "#photo-crop-actions";
    var TOOLS = "#photo-crop-tools";
    var REMOVE = "#photo-crop-remove";
    var ROTATE_BUTTONS = "[data-crop-action]";

    /* Версия формата `crop_data` повторяет серверную: чужую версию сервер
       считает мусором. Холст ужимается ради памяти телефона — 48 Мпикс
       в canvas мобильный браузер не выделит, а координаты пересчитываются
       обратно в полный размер кадра. */
    var CROP_VERSION = 1;
    var CANVAS_MAX_SIDE = 2000;

    var cropper = null;
    var source = null;
    var previewUrl = null;

    function find(selector) {
        return document.querySelector(selector);
    }

    function root() {
        return find(ROOT);
    }

    function show(element) {
        if (element) {
            element.hidden = false;
        }
    }

    function hide(element) {
        if (element) {
            element.hidden = true;
        }
    }

    function setCropData(value) {
        var field = find(CROP_FIELD);
        if (field) {
            field.value = value;
        }
    }

    function fallbackNote() {
        var box = root();
        return box && box.dataset ? box.dataset.fallbackNote || "" : "";
    }

    function showNote(text) {
        var note = find(NOTE);
        if (!note) {
            return;
        }
        note.textContent = text || "";
        note.hidden = !text;
    }

    /* Снять всё, что относится к выбранному файлу. Вызывается и при замене
       файла, и кнопкой «Убрать фото»: брошенный кропер держал бы в памяти
       прошлый кадр, а его координаты ушли бы с новым файлом. */
    function teardown() {
        if (cropper) {
            cropper.destroy();
            cropper = null;
        }
        source = null;
        if (previewUrl) {
            window.URL.revokeObjectURL(previewUrl);
            previewUrl = null;
        }
        var canvas = find(CANVAS);
        if (canvas) {
            canvas.hidden = true;
            canvas.width = 0;
            canvas.height = 0;
        }
        var preview = find(PREVIEW);
        if (preview) {
            preview.removeAttribute("src");
            preview.hidden = true;
        }
        showNote("");
        hide(find(STAGE));
        hide(find(TOOLS));
        hide(find(ACTIONS));
        /* Пустая рамка над полем файла выглядела бы поломкой формы:
           блок целиком показывает `onFileChange`, когда файл выбран. */
        hide(root());
        setCropData("");
    }

    /* Браузер не декодировал файл — чаще всего это HEIC. Обрезки не будет,
       но отправку блокировать нельзя: фото загрузится целиком. */
    function showFallback(file) {
        var preview = find(PREVIEW);
        show(root());
        show(find(STAGE));
        if (preview && window.URL && window.URL.createObjectURL) {
            previewUrl = window.URL.createObjectURL(file);
            preview.src = previewUrl;
            preview.hidden = false;
        }
        showNote(fallbackNote());
    }

    function drawBitmap(bitmap) {
        var canvas = find(CANVAS);
        var longest = Math.max(bitmap.width, bitmap.height);
        var scale = Math.min(1, CANVAS_MAX_SIDE / longest);
        canvas.width = Math.max(1, Math.round(bitmap.width * scale));
        canvas.height = Math.max(1, Math.round(bitmap.height * scale));
        canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        return {width: bitmap.width, height: bitmap.height};
    }

    function startCropper(size) {
        var canvas = find(CANVAS);
        /* Контейнер Cropper строится в родителе холста и меряет его:
           у скрытого блока размеры нулевые, и рамка получилась бы
           минимальной. Поэтому показываем до инициализации. */
        show(root());
        show(find(STAGE));
        canvas.hidden = false;
        source = size;
        cropper = new window.Cropper(canvas, {
            viewMode: 1,
            autoCropArea: 1,
            aspectRatio: NaN,
            background: false,
            checkOrientation: false,
            scalable: false,
        });
        show(find(TOOLS));
    }

    function onBitmap(bitmap) {
        var size = drawBitmap(bitmap);
        if (typeof bitmap.close === "function") {
            bitmap.close();
        }
        startCropper(size);
    }

    function onFileChange(input) {
        teardown();
        var file = input.files && input.files[0];
        if (!file) {
            return;
        }
        /* Пустой тип бывает там, где система не знает расширения:
           это не повод отказывать, решение остаётся за сервером. */
        if (file.type && file.type.indexOf("image/") !== 0) {
            return;
        }
        /* Блок и кнопка «Убрать фото» нужны сразу, ещё до декодирования:
           большой файл читается заметное время, и без них непонятно,
           что выбор сработал. */
        show(root());
        show(find(ACTIONS));
        if (!window.createImageBitmap || !window.Cropper) {
            showFallback(file);
            return;
        }
        window
            .createImageBitmap(file, {imageOrientation: "from-image"})
            .then(onBitmap)
            .catch(function () {
                showFallback(file);
            });
    }

    /* Рамка остаётся на месте, а кадр под ней поворачивается: человек видит
       на экране ровно то, что уйдёт на сервер, и может поправить рамку. */
    function onAction(action) {
        if (!cropper) {
            return;
        }
        if (action === "reset") {
            cropper.reset();
            return;
        }
        cropper.rotate(action === "rotate-left" ? -90 : 90);
    }

    /* Рамка на весь кадр без поворота — это «без кропа»: `display` станет
       нормализованным оригиналом, лишний пересчёт не нужен. */
    function isWholeFrame(crop) {
        return (
            crop.rotate === 0 &&
            crop.x <= 1 &&
            crop.y <= 1 &&
            crop.width >= crop.source_width - 1 &&
            crop.height >= crop.source_height - 1
        );
    }

    /* Собрать `crop_data` при отправке. `getData()` отдаёт координаты
       в пикселях холста, а при повороте — уже в системе повёрнутого кадра:
       сервер поворачивает до кропа, поэтому и множитель берётся по той
       стороне, вдоль которой лежит координата. */
    function collectCropData() {
        if (!cropper || !cropper.ready || !source) {
            return "";
        }
        var data = cropper.getData(true);
        if (!data.width || !data.height) {
            return "";
        }
        var rotate = ((Math.round(data.rotate || 0) % 360) + 360) % 360;
        var canvas = find(CANVAS);
        var turned = rotate % 180 !== 0;
        /* `source_width`/`source_height` описывают кадр, в котором лежат
           координаты: после поворота стороны меняются местами, и рамка
           в админке считается от них. */
        var frameWidth = turned ? source.height : source.width;
        var frameHeight = turned ? source.width : source.height;
        var scaleX = frameWidth / (turned ? canvas.height : canvas.width);
        var scaleY = frameHeight / (turned ? canvas.width : canvas.height);
        var crop = {
            version: CROP_VERSION,
            x: Math.round(data.x * scaleX),
            y: Math.round(data.y * scaleY),
            width: Math.round(data.width * scaleX),
            height: Math.round(data.height * scaleY),
            rotate: rotate,
            source_width: frameWidth,
            source_height: frameHeight,
        };
        return isWholeFrame(crop) ? "" : JSON.stringify(crop);
    }

    /* Убрать выбранный файл: у поля файла нет нативной кнопки «очистить»,
       и без этого человек, выбравший не тот снимок, ищет нужный заново. */
    function onRemove() {
        var input = find(FILE_FIELD);
        if (!input) {
            return;
        }
        input.value = "";
        teardown();
        /* Фокус возвращаем на поле файла: иначе он остаётся на кнопке,
           которой больше нет в разметке. */
        input.focus();
    }

    function init() {
        var form = find(FORM);
        if (!form) {
            return;
        }
        form.addEventListener("change", function (event) {
            var target = event.target;
            if (target && target.matches && target.matches(FILE_FIELD)) {
                onFileChange(target);
            }
        });
        form.addEventListener("submit", function () {
            setCropData(collectCropData());
        });
        form.addEventListener("click", function (event) {
            var target = event.target;
            var button = target && target.closest
                ? target.closest(ROTATE_BUTTONS)
                : null;
            if (button) {
                onAction(button.dataset.cropAction);
            }
        });
        var remove = find(REMOVE);
        if (remove) {
            remove.addEventListener("click", onRemove);
        }
        /* HEIC не читает большинство браузеров: битая картинка выглядела бы
           поломкой формы. Прячем её и говорим, что файл выбран. */
        var preview = find(PREVIEW);
        if (preview) {
            preview.addEventListener("error", function () {
                preview.hidden = true;
                showNote(fallbackNote());
            });
        }
        if (!find(STAGE)) {
            return;
        }
    }

    init();
})();
