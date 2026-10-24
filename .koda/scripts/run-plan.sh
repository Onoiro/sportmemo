#!/usr/bin/env bash
# Выполняет шаги плана подэтапа: один шаг — одна новая сессия Koda.
#
# Зачем: длинная сессия упирается в лимит запросов и теряет контекст.
# Разбивка по шагам даёт чистый контекст на каждом шаге и точку отката
# после каждого коммита.
#
# Использование:
#   run-plan.sh PLAN --list              показать шаги и статусы
#   run-plan.sh PLAN --all               выполнить все невыполненные шаги
#   run-plan.sh PLAN --from N            выполнить начиная с шага N
#   run-plan.sh PLAN --step N            выполнить один шаг
#   run-plan.sh PLAN --all --dry-run     показать, что будет выполнено
#
# Параметры:
#   --branch NAME     ветка для работы (создаётся от master, если нет)
#   --model NAME      модель Koda (по умолчанию из settings.json)
#   --timeout SEC     таймаут одного шага, по умолчанию 5400
#   --allow-dirty     не требовать чистого рабочего дерева
#
# Выход: 0 — все запрошенные шаги выполнены, 1 — ошибка или провал шага.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$REPO_ROOT/.koda/logs"

PLAN_FILE=""
MODE=""
STEP_NUM=""
FROM_NUM=""
BRANCH=""
MODEL=""
STEP_TIMEOUT=5400
ALLOW_DIRTY=0
DRY_RUN=0

log() {
    printf '%s\n' "$*"
}

die() {
    printf 'ОШИБКА: %s\n' "$*" >&2
    exit 1
}

warn() {
    printf 'ВНИМАНИЕ: %s\n' "$*" >&2
}

usage() {
    sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 1
}

# Печатает номер строки в файле, где шаг указан в таблице «Прогресс».
# Поиск ограничен секцией: в планах есть другие таблицы с колонкой номера,
# и совпадение по одной первой ячейке дало бы ложный результат.
progress_line() {
    awk -v n="$1" '
        /^## / { in_progress = ($0 ~ /Прогресс/); next }
        in_progress && $0 ~ "^\\|[[:space:]]*" n "[[:space:]]*\\|" { print NR; exit }
    ' "$PLAN_FILE"
}

# Печатает «1», если шаг отмечен выполненным в таблице «Прогресс».
progress_done() {
    local line
    line=$(progress_line "$1")
    [ -n "$line" ] || { echo 0; return; }
    if sed -n "${line}p" "$PLAN_FILE" | grep -qE '\[[xX]\]'; then
        echo 1
    else
        echo 0
    fi
}

# Печатает шаги плана строками «номер<TAB>статус<TAB>заголовок».
# Статус берётся из заголовка, а если там [ ] — из таблицы «Прогресс»:
# в ранних планах (3.1) отмечена только таблица, заголовки остались [ ].
list_steps() {
    local num state title
    grep -E '^### [0-9]+\. \[[ xX]\] ' "$PLAN_FILE" \
        | sed -E 's/^### ([0-9]+)\. \[([ xX])\] (.*)$/\1\t\2\t\3/' \
        | while IFS=$'\t' read -r num state title; do
            if [ "$state" = " " ] && [ "$(progress_done "$num")" = "1" ]; then
                state="x"
            fi
            printf '%s\t%s\t%s\n' "$num" "$state" "$title"
        done
}

step_exists() {
    list_steps | cut -f1 | grep -qx "$1"
}

# Печатает «1», если шаг отмечен выполненным.
step_done() {
    list_steps | awk -F'\t' -v n="$1" \
        '$1 == n { print ($2 == "x" || $2 == "X") ? 1 : 0 }'
}

step_title() {
    list_steps | awk -F'\t' -v n="$1" '$1 == n { print $3 }'
}

print_steps() {
    local num state title mark
    while IFS=$'\t' read -r num state title; do
        if [ "$state" = " " ]; then mark="[ ]"; else mark="[x]"; fi
        printf '  %2s. %s %s\n' "$num" "$mark" "$title"
    done < <(list_steps)
}

# Печатает номера шагов, которые попадут в работу при текущем режиме.
selected_steps() {
    local num state title
    while IFS=$'\t' read -r num state title; do
        [ "$state" = " " ] || continue
        case "$MODE" in
            step) [ "$num" = "$STEP_NUM" ] || continue ;;
            from) [ "$num" -ge "$FROM_NUM" ] || continue ;;
        esac
        printf '%s\n' "$num"
    done < <(list_steps)
}

mark_step_done() {
    local num="$1" line
    # Отметка в заголовке: ### N. [ ] Заголовок -> ### N. [x] Заголовок
    sed -i -E "s/^### ${num}\. \[ \] /### ${num}. [x] /" "$PLAN_FILE"
    # Отметка в таблице прогресса: | N | ... | [ ] | -> | N | ... | [x] |
    line=$(progress_line "$num")
    if [ -n "$line" ]; then
        sed -i -E "${line}s/\[ \][[:space:]]*\|[[:space:]]*$/\[x] |/" "$PLAN_FILE"
    fi
    log "  шаг $num отмечен выполненным в плане"
}

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --list)        MODE="list" ;;
            --all)         MODE="all" ;;
            --step)        MODE="step"; STEP_NUM="${2:-}"; shift ;;
            --from)        MODE="from"; FROM_NUM="${2:-}"; shift ;;
            --dry-run)     DRY_RUN=1 ;;
            --branch)      BRANCH="${2:-}"; shift ;;
            --model)       MODEL="${2:-}"; shift ;;
            --timeout)     STEP_TIMEOUT="${2:-}"; shift ;;
            --allow-dirty) ALLOW_DIRTY=1 ;;
            -h|--help)     usage ;;
            -*)            die "неизвестный параметр: $1" ;;
            *)
                [ -z "$PLAN_FILE" ] || die "лишний аргумент: $1"
                PLAN_FILE="$1"
                ;;
        esac
        shift
    done

    [ -n "$PLAN_FILE" ] || die "не указан файл плана"
    [ -n "$MODE" ] || die "не указан режим: --list, --step, --from или --all"

    case "$MODE" in
        step|from)
            local n="$STEP_NUM"
            [ "$MODE" = "from" ] && n="$FROM_NUM"
            case "$n" in
                ''|*[!0-9]*) die "режиму --$MODE нужен номер шага числом" ;;
            esac
            ;;
    esac
}

# Проверки перед работой. Для --list и --dry-run достаточно самого плана:
# эти режимы ничего не меняют и не должны упираться в грязное дерево.
preflight() {
    local read_only="$1"

    [ -f "$PLAN_FILE" ] || die "файл плана не найден: $PLAN_FILE"
    [ -s "$PLAN_FILE" ] || die "файл плана пуст: $PLAN_FILE"

    local total
    total=$(list_steps | wc -l)
    [ "$total" -gt 0 ] \
        || die "в плане не найдено шагов вида «### N. [ ] Заголовок»"

    [ "$read_only" -eq 1 ] && return 0

    command -v koda >/dev/null 2>&1 || die "команда koda не найдена в PATH"
    command -v git >/dev/null 2>&1 || die "команда git не найдена в PATH"

    if [ "$ALLOW_DIRTY" -eq 0 ]; then
        local dirty
        dirty=$(git -C "$REPO_ROOT" status --porcelain)
        if [ -n "$dirty" ]; then
            printf '%s\n' "$dirty" >&2
            die "рабочее дерево грязное. Закоммитьте или откатите изменения,
       либо запустите с --allow-dirty"
        fi
    fi

    if ! (exec 3<>/dev/tcp/localhost/5432) 2>/dev/null; then
        warn "PostgreSQL на localhost:5432 не отвечает — тесты внутри шага упадут"
    fi
}

ensure_branch() {
    local current
    current=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)

    if [ -n "$BRANCH" ]; then
        if git -C "$REPO_ROOT" rev-parse --verify --quiet "refs/heads/$BRANCH" >/dev/null; then
            git -C "$REPO_ROOT" switch "$BRANCH" >/dev/null 2>&1 \
                || die "не удалось переключиться на ветку $BRANCH"
        else
            git -C "$REPO_ROOT" switch -c "$BRANCH" master >/dev/null 2>&1 \
                || die "не удалось создать ветку $BRANCH от master"
            log "создана ветка $BRANCH от master"
        fi
        current="$BRANCH"
    fi

    case "$current" in
        master|main)
            die "работа в ветке $current запрещена: укажите --branch feature/<имя>
       или создайте ветку вручную перед запуском"
            ;;
    esac
    log "ветка: $current"
}

build_prompt() {
    local num="$1" title="$2" plan_rel="$3" branch="$4"
    cat <<EOF
Ты выполняешь ОДИН шаг плана подэтапа и завершаешь работу.

План: $plan_rel
Шаг: $num. $title

Требования:

1. Прочитай план целиком и выполни ТОЛЬКО шаг $num. Остальные шаги не трогай:
   их выполнят следующие сессии, каждая в своём процессе.
2. Ветка \`$branch\` уже создана и активна. Новую ветку не создавай,
   master не трогай.
3. Соблюдай правила проекта из KODA.md: длина строк 88/79, gettext для строк
   интерфейса, семантическое версионирование, комментарии только «почему».
4. Проверки: выполни критерий шага. Полный \`make test\` идёт 6 минут —
   запускай его только если шаг этого требует (шаги «Проверка», «Тесты»);
   в остальных случаях достаточно целевых тестов файла.
5. Отметь выполнение: заголовок \`### $num. [ ] $title\` замени на
   \`### $num. [x] $title\`, и в таблице «Прогресс» поставь [x] в строке $num.
6. Закоммить изменения. Заголовок коммита — на простом английском,
   в повелительном наклонении, до 72 символов, с префиксом \`step $num:\`.
   В теле объясни «почему», а не «что».
7. ЗАПРЕЩЕНО: git push, git reset, git rebase, git checkout -- ., git clean,
   rm -rf, docker compose down -v, удаление чужих файлов, правка master.
8. Если для шага нужно решение владельца (выбор из вариантов, противоречие
   в плане) — не решай сам: опиши вопрос, заверши работу без коммита, выйди.
9. В конце выведи отчёт: что сделано, какие файлы изменены, что проверено.

Показывай ход работы коротко: без пересказа плана и без пересказа кода.
EOF
}

run_one_step() {
    local num="$1"
    local title plan_rel branch base_sha head_sha
    title=$(step_title "$num")
    [ -n "$title" ] || die "шаг $num не найден в плане"
    plan_rel=$(realpath --relative-to="$REPO_ROOT" "$PLAN_FILE")
    branch=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)

    log ""
    log "=============================================================="
    log "шаг $num: $title"
    log "=============================================================="

    base_sha=$(git -C "$REPO_ROOT" rev-parse HEAD)

    local args=(-p "$(build_prompt "$num" "$title" "$plan_rel" "$branch")"
                --approval-mode yolo
                --checkpointing)
    [ -n "$MODEL" ] && args+=(-m "$MODEL")

    ( cd "$REPO_ROOT" && timeout "$STEP_TIMEOUT" koda "${args[@]}" ) \
        || log "  koda завершилась с ненулевым кодом"

    head_sha=$(git -C "$REPO_ROOT" rev-parse HEAD)

    if [ "$head_sha" = "$base_sha" ]; then
        if [ "$(step_done "$num")" = "1" ]; then
            warn "шаг $num отмечен [x], но коммита нет: план разошёлся с кодом.
       Проверьте \`git status\` и \`git diff\`, затем решите: закоммитить или откатить"
            return 1
        fi
        warn "шаг $num не дал коммита. Смотрите \`git status\`:
       агент либо не смог выполнить шаг, либо задал вопрос владельцу"
        return 1
    fi

    if ! git -C "$REPO_ROOT" merge-base --is-ancestor "$base_sha" HEAD; then
        warn "история переписана: $base_sha не предок HEAD. Разбирайтесь вручную"
        return 1
    fi

    if [ "$(step_done "$num")" != "1" ]; then
        warn "агент не отметил шаг $num в плане — отмечаю сам"
        mark_step_done "$num"
        git -C "$REPO_ROOT" add "$PLAN_FILE"
        git -C "$REPO_ROOT" commit -m "step $num: mark the step as done in the plan" \
            >/dev/null 2>&1 || warn "не удалось закоммитить отметку шага"
    fi

    log "  готово: $(git -C "$REPO_ROOT" log -1 --format='%h %s')"
    return 0
}

show_dry_run() {
    log "План: $PLAN_FILE"
    print_steps
    [ -n "$MODEL" ] && log "Модель: $MODEL"
    log "Таймаут шага: ${STEP_TIMEOUT}s"
    local nums n
    nums=$(selected_steps)
    if [ -z "$nums" ]; then
        log "Невыполненных шагов в выбранном режиме нет."
        return 0
    fi
    log "Будут выполнены (каждый шаг — новая сессия koda):"
    for n in $nums; do
        printf '  %2s. %s\n' "$n" "$(step_title "$n")"
    done
}

main() {
    parse_args "$@"

    case "$PLAN_FILE" in
        /*) ;;
        *) PLAN_FILE="$REPO_ROOT/$PLAN_FILE" ;;
    esac

    if [ "$MODE" = "list" ]; then
        preflight 1
        log "План: $PLAN_FILE"
        print_steps
        return 0
    fi

    if [ "$MODE" = "step" ] && ! step_exists "$STEP_NUM"; then
        die "шаг $STEP_NUM не найден в плане: $PLAN_FILE"
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        preflight 1
        show_dry_run
        return 0
    fi

    preflight 0
    ensure_branch

    mkdir -p "$LOG_DIR"
    local log_file
    log_file="$LOG_DIR/plan-run-$(date +%Y%m%d-%H%M%S).log"
    exec > >(tee -a "$log_file") 2>&1
    log "лог: $log_file"

    local nums done_count=0 failed=0 n
    nums=$(selected_steps)

    if [ -z "$nums" ]; then
        log "невыполненных шагов нет"
        return 0
    fi

    for n in $nums; do
        if ! run_one_step "$n"; then
            failed=1
            break
        fi
        done_count=$((done_count + 1))
    done

    log ""
    if [ "$failed" -ne 0 ]; then
        log "остановлено на ошибке. Выполнено шагов: $done_count"
        log "разберитесь и запустите снова — выполненные шаги пропускаются"
        return 1
    fi

    log "готово. Выполнено шагов: $done_count"
    log "проверьте: git log --oneline master..HEAD"
}

main "$@"
