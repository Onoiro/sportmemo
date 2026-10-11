/* SportMemo — переключение темы.
 *
 * Bootstrap понимает только light и dark, поэтому «сепия» — это
 * data-bs-theme="light" плюс класс theme-sepia. Выбор хранится в
 * localStorage; при первом визите берётся системная схема.
 * Этот же выбор повторяет инлайн-скрипт в <head>, чтобы не было мигания.
 */

(function () {
    "use strict";

    var STORAGE_KEY = "sportmemo-theme";
    var SEPIA = "sepia";
    var DARK = "dark";

    function storedTheme() {
        try {
            return window.localStorage.getItem(STORAGE_KEY);
        } catch (error) {
            return null;
        }
    }

    function applyTheme(theme) {
        var root = document.documentElement;
        var isDark = theme === DARK;
        root.setAttribute("data-bs-theme", isDark ? "dark" : "light");
        root.classList.toggle("theme-sepia", !isDark);

        var button = document.querySelector("[data-theme-toggle]");
        if (button) {
            var icon = button.querySelector("[data-theme-toggle-icon]");
            if (icon) {
                icon.className = isDark
                    ? "bi bi-sun"
                    : "bi bi-moon-stars";
            }
            button.setAttribute("aria-pressed", isDark ? "true" : "false");
        }
    }

    function currentTheme() {
        return document.documentElement.classList.contains("theme-sepia")
            ? SEPIA
            : DARK;
    }

    function toggleTheme() {
        var next = currentTheme() === DARK ? SEPIA : DARK;
        try {
            window.localStorage.setItem(STORAGE_KEY, next);
        } catch (error) {
            /* приватный режим: тема работает до перезагрузки */
        }
        applyTheme(next);
    }

    function init() {
        applyTheme(currentTheme());

        document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
            button.addEventListener("click", toggleTheme);
        });

        if (window.matchMedia) {
            var query = window.matchMedia("(prefers-color-scheme: dark)");
            query.addEventListener("change", function (event) {
                if (storedTheme()) {
                    return; /* выбор пользователя важнее системного */
                }
                applyTheme(event.matches ? DARK : SEPIA);
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
