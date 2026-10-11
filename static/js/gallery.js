/* SportMemo — подгрузка галереи.
 *
 * htmx сам дописывает карточки в сетку. Здесь только то, чего htmx
 * не делает: aria-busy на кнопке и сообщение в aria-live, чтобы
 * подгрузка была заметна скринридеру.
 *
 * Слушатели висят на документе: контейнер кнопки после каждого ответа
 * заменяется целиком (hx-swap-oob), и обработчик на нём жил бы до первого
 * клика. Тексты сообщений приходят из шаблона через data-атрибуты —
 * строки интерфейса остаются в gettext, а не в скрипте.
 */

(function () {
    "use strict";

    var GALLERY = "#photo-gallery";

    function gallery() {
        return document.querySelector(GALLERY);
    }

    function message(name, fallback) {
        var root = gallery();
        var text = root && root.dataset ? root.dataset[name] : "";
        return text || fallback || "";
    }

    function announce(text) {
        var region = document.querySelector("#gallery-status");
        if (!region || !text) {
            return;
        }
        /* Пустая строка перед текстом заставляет скринридер прочитать
           сообщение, даже если текст не изменился. */
        region.textContent = "";
        region.textContent = text;
    }

    function cardCount() {
        var grid = document.querySelector("#photo-grid");
        return grid ? grid.querySelectorAll(".photo-card").length : 0;
    }

    function isGalleryRequest(event) {
        var target = event.detail && event.detail.elt;
        return Boolean(target && target.closest && target.closest(GALLERY));
    }

    function setBusy(event, busy) {
        var button = event.detail && event.detail.elt;
        if (!button || !button.setAttribute) {
            return;
        }
        if (busy) {
            button.setAttribute("aria-busy", "true");
        } else {
            button.removeAttribute("aria-busy");
        }
    }

    document.addEventListener("htmx:beforeRequest", function (event) {
        if (isGalleryRequest(event)) {
            setBusy(event, true);
        }
    });

    document.addEventListener("htmx:afterSwap", function (event) {
        if (!isGalleryRequest(event)) {
            return;
        }
        setBusy(event, false);

        var template = document.querySelector("#gallery-more a")
            ? message("loadedText", "{count}")
            : message("endText");

        announce(template.replace("{count}", String(cardCount())));
    });

    document.addEventListener("htmx:responseError", function (event) {
        if (!isGalleryRequest(event)) {
            return;
        }
        setBusy(event, false);
        announce(message("errorText"));
    });
})();