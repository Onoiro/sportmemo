/* SportMemo — блок комментариев.
 *
 * htmx заменяет блок целиком (`hx-swap="outerHTML"`), поэтому слушатели
 * висят на документе: обработчик на самой форме жил бы до первой отправки.
 * Работа скрипта — только обратная связь для скринридера (`aria-busy`
 * и сообщение в `aria-live`). Без JavaScript форма отправляется обычным POST.
 *
 * Тексты приходят из шаблона через data-атрибуты: строки интерфейса
 * остаются в gettext, а не в скрипте — то же правило, что в gallery.js.
 */

(function () {
    "use strict";

    var BLOCK = "#comments-block";

    function block() {
        return document.querySelector(BLOCK);
    }

    function message(name, fallback) {
        var root = block();
        var text = root && root.dataset ? root.dataset[name] : "";
        return text || fallback || "";
    }

    function announce(text) {
        var region = document.querySelector("#comments-status");
        if (!region || !text) {
            return;
        }
        /* Пустая строка перед текстом заставляет скринридер прочитать
           сообщение, даже если текст не изменился. */
        region.textContent = "";
        region.textContent = text;
    }

    function isCommentRequest(event) {
        var target = event.detail && event.detail.elt;
        return Boolean(target && target.closest && target.closest(BLOCK));
    }

    function setBusy(event, busy) {
        var form = event.detail && event.detail.elt;
        if (!form || !form.setAttribute) {
            return;
        }
        var button = form.querySelector("button[type='submit']");
        if (!button) {
            return;
        }
        if (busy) {
            button.setAttribute("aria-busy", "true");
            button.disabled = true;
        } else {
            button.removeAttribute("aria-busy");
            button.disabled = false;
        }
    }

    document.addEventListener("htmx:beforeRequest", function (event) {
        if (isCommentRequest(event)) {
            setBusy(event, true);
        }
    });

    document.addEventListener("htmx:afterSwap", function (event) {
        if (!isCommentRequest(event)) {
            return;
        }
        var count = block() ? block().dataset.commentsCount : "";
        announce(message("sentText").replace("{count}", String(count || "")));
    });

    document.addEventListener("htmx:responseError", function (event) {
        if (!isCommentRequest(event)) {
            return;
        }
        setBusy(event, false);
        announce(message("errorText"));
    });
})();
