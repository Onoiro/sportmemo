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
#   --rescue          правки оборванной сессии закоммитить как
#                     незавершённые и снять отметку [x] у шага
#   --max-turns N     предел ходов koda на шаг (по умолчанию 200). Агент
#                     останавливается сам и успевает закоммитить, вместо
#                     обрыва по лимиту модели без коммита.
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
MAX_TURNS=200
ALLOW_DIRTY=0
DRY_RUN=0
RESCUE=0
STEP_PID=""
HB_PID=""
BASE_SHA=""

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

# Ctrl+C останавливает шаг, но не оставляет неопределённости: дочерний
# процесс koda получает сигнал вместе с нами, а мы говорим, был ли коммит.
on_interrupt() {
    printf '\n' >&2
    warn "прервано пользователем"
    [ -n "$HB_PID" ] && kill "$HB_PID" 2>/dev/null
    if [ -n "$STEP_PID" ]; then
        kill -- "-$(ps -o pgid= "$STEP_PID" 2>/dev/null | tr -d ' ')" 2>/dev/null \
            || kill "$STEP_PID" 2>/dev/null
        STEP_PID=""
    fi
    if [ -n "$BASE_SHA" ]; then
        local head_sha
        head_sha=$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)
        if [ "$head_sha" != "$BASE_SHA" ]; then
            log "шаг успел закоммититься: $(git -C "$REPO_ROOT" log -1 --format='%h %s')"
        else
            log "коммита нет: шаг не завершён. Правки, если были, лежат в \`git status\`"
            [ "$RESCUE" -eq 1 ] && log "повторный запуск с --rescue сохранит их"
        fi
    fi
    exit 130
}

# Сердцебиение: headless-режим печатает только финальный ответ, и без
# отметок шаг выглядит зависшим. Пока идёт koda, раз в 30 секунд пишем,
# что процесс жив, и показываем, появился ли коммит.
heartbeat() {
    local start=$SECONDS
    while sleep 30; do
        local elapsed=$((SECONDS - start))
        local head_sha
        head_sha=$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)
        if [ "$head_sha" != "$1" ]; then
            log "  [$(printf '%d:%02d' $((elapsed / 60)) $((elapsed % 60)))] есть новый коммит, шаг завершает работу…"
        else
            log "  [$(printf '%d:%02d' $((elapsed / 60)) $((elapsed % 60)))] koda работает…"
        fi
    done
}

usage() {
    # Печатается блок комментариев шапки до строки «Выход»: номера строк
    # в sed сломались бы от любой правки описания параметров.
    sed -n '2,/^# Выход/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
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

# Номера выполненных шагов в переданном файле плана.
steps_done_in() {
    local PLAN_FILE="$1"
    list_steps | awk -F'\t' '$2 == "x" || $2 == "X" { print $1 }'
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

# Снимает отметку шага: нужна, когда сессия оборвалась и отметила шаг
# раньше, чем появился коммит. Иначе шаг не попадёт в повторный прогон.
unmark_step_done() {
    local num="$1" line
    sed -i -E "s/^### ${num}\. \[[xX]\] /### ${num}. [ ] /" "$PLAN_FILE"
    line=$(progress_line "$num")
    if [ -n "$line" ]; then
        sed -i -E "${line}s/\[[xX]\][[:space:]]*\|[[:space:]]*$/\[ ] |/" "$PLAN_FILE"
    fi
    log "  отметка шага $num снята — шаг снова в работе"
}

# Находит шаг, отмеченный [x] в рабочем дереве, но не в HEAD: предыдущая
# сессия оборвалась до коммита (лимит запросов, сеть, Ctrl+C).
dangling_step() {
    local plan_rel tmp cur n
    plan_rel=$(realpath --relative-to="$REPO_ROOT" "$PLAN_FILE")
    # Сравнение с HEAD, а не с индексом: git add без commit тоже оставляет
    # план изменённым, и обычный diff такой случай не видит.
    git -C "$REPO_ROOT" diff --quiet HEAD -- "$PLAN_FILE" && return 0
    tmp=$(mktemp) || return 0
    if ! git -C "$REPO_ROOT" show "HEAD:$plan_rel" >"$tmp" 2>/dev/null; then
        rm -f "$tmp"
        return 0
    fi
    cur=$(steps_done_in "$PLAN_FILE")
    local head_done
    head_done=$(steps_done_in "$tmp")
    rm -f "$tmp"
    for n in $cur; do
        case " $head_done " in
            *" $n "*) ;;
            *) printf '%s\n' "$n"; return 0 ;;
        esac
    done
}

# Сохраняет правки оборванной сессии, чтобы они не пропали и чтобы дерево
# стало чистым для следующего запуска. Шаг остаётся невыполненным.
rescue_dangling() {
    local num="$1"
    unmark_step_done "$num"
    # Отметка могла быть единственной правкой: тогда после её снятия дерево
    # снова чистое и коммитить нечего — шаг просто вернулся в работу.
    if [ -z "$(git -C "$REPO_ROOT" status --porcelain)" ]; then
        log "других правок нет — шаг $num снова в очереди"
        return 0
    fi
    # Список печатается до коммита: git add -A заберёт всё дерево, и человек
    # должен видеть, что именно уйдёт в коммит «незавершённый шаг».
    log "  в коммит уйдёт:"
    git -C "$REPO_ROOT" status --short
    git -C "$REPO_ROOT" add -A
    git -C "$REPO_ROOT" commit -q \
        -m "step $num: keep the work left by the interrupted session" \
        -m "The session stopped before the step was complete. This commit
saves the work so the next session can finish the step." \
        || return 1
    warn "шаг $num сохранён как незавершённый, отметка снята"
    log "повторите запуск — следующая сессия доделает шаг $num"
    return 0
}

# Разбор прерванного шага: показываем правки и говорим, что делать.
# Возвращает 0, только если правки сохранены через --rescue.
interrupted_step() {
    local num="$1" marked="$2" dirty="$3"
    log "  правки в рабочем дереве:"
    git -C "$REPO_ROOT" status --short
    if [ "$marked" = "1" ]; then
        warn "шаг $num отмечен [x], но коммита нет: сессия оборвалась до коммита"
    else
        warn "шаг $num дал правки, но не дал коммита"
    fi
    if [ "$RESCUE" -eq 1 ] && [ "$dirty" = "1" ]; then
        rescue_dangling "$num" && return 0
        warn "не удалось сохранить правки — разбирайтесь вручную"
        return 1
    fi
    log "  что делать:"
    log "    --rescue            сохранить правки и повторить шаг $num"
    log "    git checkout -- .   отбросить правки и повторить шаг $num"
    return 1
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
            --max-turns)   MAX_TURNS="${2:-}"; shift ;;
            --allow-dirty) ALLOW_DIRTY=1 ;;
            --rescue)      RESCUE=1 ;;
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
# эти режимы ничего не меняют и не должны упираться в состояние дерева.
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

    if ! (exec 3<>/dev/tcp/localhost/5432) 2>/dev/null; then
        warn "PostgreSQL на localhost:5432 не отвечает — тесты внутри шага упадут"
    fi
}

# Дерево должно быть чистым, иначе новый шаг наложится на чужие правки.
# Правки шага, отмеченного без коммита, разбирает вызывающий код.
require_clean_tree() {
    [ "$ALLOW_DIRTY" -eq 1 ] && return 0
    local dirty
    dirty=$(git -C "$REPO_ROOT" status --porcelain)
    [ -z "$dirty" ] && return 0
    printf '%s\n' "$dirty" >&2
    die "рабочее дерево грязное. Закоммитьте или откатите изменения,
       либо запустите с --allow-dirty"
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

Главное правило: шаг выполнен только тогда, когда есть коммит.
Порядок: правки кода → отметки [x] в плане → \`git add -A\` → \`git commit\`.
Отметки и код должны попасть в ОДИН коммит. Не отмечай шаг отдельной
правкой до коммита: если сессия оборвётся, план и код разойдутся.

Требования:

1. Прочитай план целиком и выполни ТОЛЬКО шаг $num. Остальные шаги не трогай:
   их выполнят следующие сессии, каждая в своём процессе.
2. Ветка \`$branch\` уже активна. Новую ветку не создавай, master не трогай,
   merge и squash не делай — их выполняет владелец.
3. Шаг мог быть начат предыдущей сессией: посмотри \`git log --oneline -3\`
   и \`git show --stat HEAD\`. Если часть работы уже есть — заверши её,
   не дублируя сделанное.
4. Соблюдай правила проекта из KODA.md: длина строк 88/79, gettext для строк
   интерфейса, семантическое версионирование, комментарии только «почему».
5. Проверки: выполни критерий шага. Полный \`make test\` идёт 6–7 минут —
   запускай его только если шаг этого требует (шаги «Проверка», «Тесты»);
   в остальных случаях достаточно целевых тестов файла.
6. Коммит: заголовок на простом английском, в повелительном наклонении,
   до 72 символов, с префиксом \`step $num:\`. В теле объясни «почему»,
   а не «что». После коммита \`git status\` обязан быть чистым.
7. Если чувствуешь, что не успеваешь (много запросов, долгая проверка) —
   закоммить сделанное БЕЗ отметки [x] и напиши в отчёте, что осталось.
   Незакоммиченная работа теряется.
8. Если для шага нужно решение владельца (выбор из вариантов, противоречие
   в плане) — не решай сам: план не трогай, без коммита, опиши вопрос
   в финальном ответе и выйди.
9. В конце выведи отчёт: что сделано, какие файлы изменены, хеш коммита.

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
    BASE_SHA="$base_sha"

    local args=(-p "$(build_prompt "$num" "$title" "$plan_rel" "$branch")"
                --approval-mode yolo
                --max-turns "$MAX_TURNS"
                --show-usage
                --checkpointing)
    [ -n "$MODEL" ] && args+=(-m "$MODEL")

    log "  запускаю koda (таймаут ${STEP_TIMEOUT}s), вывод появится в конце…"
    local step_start=$SECONDS

    ( cd "$REPO_ROOT" && exec timeout "$STEP_TIMEOUT" koda "${args[@]}" ) &
    STEP_PID=$!

    heartbeat "$base_sha" &
    HB_PID=$!

    local koda_status=0
    wait "$STEP_PID" || koda_status=$?
    STEP_PID=""
    kill "$HB_PID" 2>/dev/null
    wait "$HB_PID" 2>/dev/null
    HB_PID=""

    local elapsed=$((SECONDS - step_start))
    log "  koda завершилась за $(printf '%d:%02d' $((elapsed / 60)) $((elapsed % 60))) (код $koda_status)"
    [ "$koda_status" -eq 124 ] \
        && warn "шаг прерван по таймауту ${STEP_TIMEOUT}s"
    [ "$koda_status" -ne 0 ] && [ "$koda_status" -ne 124 ] \
        && log "  koda завершилась с ненулевым кодом"

    head_sha=$(git -C "$REPO_ROOT" rev-parse HEAD)

    if [ "$head_sha" = "$base_sha" ]; then
        local marked=0 dirty=0
        [ "$(step_done "$num")" = "1" ] && marked=1
        [ -n "$(git -C "$REPO_ROOT" status --porcelain)" ] && dirty=1
        if [ "$marked" -eq 1 ] || [ "$dirty" -eq 1 ]; then
            interrupted_step "$num" "$marked" "$dirty"
            return 1
        fi
        warn "шаг $num не дал ни коммита, ни правок:
       агент остановился сам — вероятно, ему нужно решение владельца"
        return 1
    fi

    if ! git -C "$REPO_ROOT" merge-base --is-ancestor "$base_sha" HEAD; then
        warn "история переписана: $base_sha не предок HEAD. Разбирайтесь вручную"
        return 1
    fi

    # Коммит только с отметкой в плане — это не выполненный шаг, а забытая
    # работа: сессия отметила шаг и упала до правок. Документационные шаги
    # тоже меняют другие файлы, поэтому признак узкий: изменён ровно план.
    local touched
    touched=$(git -C "$REPO_ROOT" diff --name-only "$base_sha" HEAD)
    if [ "$touched" = "$plan_rel" ]; then
        warn "шаг $num закоммитил только отметку в плане, работы в коммите нет.
       Проверьте \`git show --stat HEAD\` и выполните шаг заново"
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
    log "Ходов на шаг: $MAX_TURNS"
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

    # Разбор висящего шага и проверка чистоты идут до переключения ветки:
    # git switch перенёс бы чужие правки в новую ветку, и они попали бы
    # в коммит шага. Правки оборванного шага и есть причина грязи,
    # поэтому сначала их предлагается спасти.
    local dangle marked=0 dirty=0
    dangle=$(dangling_step)
    [ -n "$(git -C "$REPO_ROOT" status --porcelain)" ] && dirty=1
    if [ -n "$dangle" ]; then
        [ "$(step_done "$dangle")" = "1" ] && marked=1
        interrupted_step "$dangle" "$marked" "$dirty" || return 1
    fi

    require_clean_tree
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

trap on_interrupt INT TERM

main "$@"
