#!/usr/bin/env bash
# Бэкап SportMemo: дамп БД, медиа в отдельный бакет, алерт при сбое.
#
# Запускается таймером systemd и руками (`make backup`). Шаги идут от
# дешёвого к дорогому: ранний сбой не должен тратить диск и трафик.
# Секреты живут только в .env на сервере и в git не попадают.
set -Eeuo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR="backups"
LOG_FILE="$BACKUP_DIR/backup.log"
LOG_MAX_LINES=2000
# Источник медиа на проде — remote из rclone.conf. Прод-ветка (S3 → S3)
# проверяется в 5.7: бакетов Yandex на момент 5.4 ещё нет.
MEDIA_S3_REMOTE="yandex"

DRY_RUN="${DRY_RUN:-0}"
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *)
      printf 'unknown argument: %s\n' "$arg" >&2
      exit 2
      ;;
  esac
done

BACKUP_ENV_FILE="${BACKUP_ENV_FILE:-.env}"
STEP="start"

# `.env` нельзя исполнять через `source`: строка
# `DEFAULT_FROM_EMAIL=SportMemo <info@sportmemo.ru>` — это редирект
# ввода для bash, и остаток файла молча теряется.
env_get() {
  local key="$1" fallback="${2-}" value=""
  if [ -f "$BACKUP_ENV_FILE" ]; then
    value=$(grep -E "^${key}=" "$BACKUP_ENV_FILE" \
      | tail -n 1 | cut -d= -f2- || true)
  fi
  # Кавычки и хвостовые пробелы снимаем: docker и rclone должны
  # получить чистое значение, а не `"значение" `.
  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  value="${value%"${value##*[![:space:]]}"}"
  if [ -n "$value" ]; then
    printf '%s' "$value"
  else
    printf '%s' "$fallback"
  fi
}

log() {
  local line
  line="$(date '+%Y-%m-%d %H:%M:%S') $*"
  printf '%s\n' "$line"
  if [ "$DRY_RUN" = "1" ] || [ ! -d "$BACKUP_DIR" ]; then
    return 0
  fi
  printf '%s\n' "$line" >> "$LOG_FILE"
  # Журнал читают руками, а не grep-ом по гигабайту: держим границу.
  if [ "$(wc -l < "$LOG_FILE")" -gt "$LOG_MAX_LINES" ]; then
    tail -n "$LOG_MAX_LINES" "$LOG_FILE" > "$LOG_FILE.tmp"
    mv "$LOG_FILE.tmp" "$LOG_FILE"
  fi
}

# Текст алерта — без данных: канал чужой (Telegram), и в нём должен
# быть только факт сбоя, без путей, имён файлов и сообщений БД.
alert() {
  local step="$1" text conf
  if [ "$DRY_RUN" = "1" ]; then
    log "dry-run: would send alert: $step"
    return 0
  fi
  if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
    log "WARN: telegram is not configured, failure stays silent"
    return 0
  fi
  text="SportMemo: бэкап не выполнен — $step"
  # URL с токеном уходит через конфиг в stdin: в `ps` его быть не должно.
  conf=$(printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' \
    "$TELEGRAM_BOT_TOKEN")
  if [ -n "$TELEGRAM_PROXY" ]; then
    conf=$(printf '%s\nproxy = "%s"\n' "$conf" "$TELEGRAM_PROXY")
  fi
  if ! printf '%s\n' "$conf" | curl -sS -K - --max-time 15 --retry 2 \
    --data-urlencode "chat_id=$TELEGRAM_CHAT_ID" \
    --data-urlencode "text=$text" > /dev/null; then
    log "WARN: alert was not delivered"
  fi
  return 0
}

# 1 — сбой шага, 2 — ошибка конфигурации. Разделение нужно таймеру
# и тестам: «нет переменной» и «упал pg_dump» — разные поломки.
fail() {
  trap - ERR
  log "ERROR: $*"
  alert "$STEP"
  exit 1
}

config_error() {
  trap - ERR
  log "ERROR: $*"
  # В алерт имя переменной не уходит: это тоже деталь конфигурации.
  alert "config"
  exit 2
}

on_error() {
  local code=$?
  trap - ERR
  log "ERROR: step failed: $STEP (exit $code)"
  alert "$STEP"
  exit 1
}
trap on_error ERR

require_var() {
  if [ -z "$2" ]; then
    config_error "$1 is not set (see .env.example)"
  fi
}

is_true() {
  case "$1" in
    [Tt][Rr][Uu][Ee] | 1 | [Yy][Ee][Ss]) return 0 ;;
    *) return 1 ;;
  esac
}

count_remote() {
  "$RCLONE_BIN" size --json "$1" 2> /dev/null \
    | sed -n 's/.*"count":\([0-9][0-9]*\).*/\1/p' || true
}

POSTGRES_DB="$(env_get POSTGRES_DB)"
POSTGRES_USER="$(env_get POSTGRES_USER)"
USE_S3="$(env_get USE_S3 False)"
S3_BUCKET="$(env_get S3_BUCKET)"
S3_PRIVATE_BUCKET="$(env_get S3_PRIVATE_BUCKET)"
COMPOSE_FILE="$(env_get BACKUP_COMPOSE_FILE)"
REMOTE="$(env_get BACKUP_RCLONE_REMOTE)"
BUCKET="$(env_get BACKUP_BUCKET)"
KEEP_LOCAL="$(env_get BACKUP_KEEP_LOCAL 7)"
RETENTION_DAYS="$(env_get BACKUP_RETENTION_DAYS 30)"
MIN_FREE_MB="$(env_get BACKUP_MIN_FREE_MB 1024)"
RCLONE_BIN="$(env_get RCLONE_BIN rclone)"
TELEGRAM_BOT_TOKEN="$(env_get TELEGRAM_BOT_TOKEN)"
TELEGRAM_CHAT_ID="$(env_get TELEGRAM_CHAT_ID)"
TELEGRAM_PROXY="$(env_get TELEGRAM_PROXY)"

STEP="config"
require_var POSTGRES_DB "$POSTGRES_DB"
require_var POSTGRES_USER "$POSTGRES_USER"
require_var BACKUP_COMPOSE_FILE "$COMPOSE_FILE"
require_var BACKUP_RCLONE_REMOTE "$REMOTE"
require_var BACKUP_BUCKET "$BUCKET"
[ -f "$COMPOSE_FILE" ] \
  || config_error "compose file not found: $COMPOSE_FILE"

# В dry-run окружение только проверяется: отсутствующий бинарник
# в тестах — норма, а опечатка в .env должна ловиться всегда.
check_binary() {
  if command -v "$1" > /dev/null 2>&1; then
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    log "dry-run: WARN: $1 is not installed"
    return 0
  fi
  config_error "$1 is not installed"
}

STEP="environment"
check_binary docker
check_binary "$RCLONE_BIN"
check_binary curl

STEP="disk check"
FREE_MB=$(df -Pk . | awk 'NR == 2 {print int($4 / 1024)}')
if [ "$FREE_MB" -lt "$MIN_FREE_MB" ]; then
  if [ "$DRY_RUN" = "1" ]; then
    log "dry-run: WARN: free ${FREE_MB} MB below ${MIN_FREE_MB} MB"
  else
    fail "free ${FREE_MB} MB below ${MIN_FREE_MB} MB"
  fi
fi

if [ "$DRY_RUN" = "1" ]; then
  log "dry-run: mkdir -p $BACKUP_DIR && chmod 700 $BACKUP_DIR"
else
  # В дампе ПДн: каталог не должен быть читаем соседям по серверу.
  mkdir -p "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"
fi

STEP="dump"
STAMP=$(date +%Y%m%d_%H%M%S)
DUMP="$BACKUP_DIR/sportmemo_${STAMP}.dump"

if [ "$DRY_RUN" = "1" ]; then
  log "dry-run: docker compose -f $COMPOSE_FILE exec -T db pg_dump" \
    "-U $POSTGRES_USER -Fc $POSTGRES_DB > $DUMP"
else
  # `-Fc` пишет файл напрямую, а не в конвейер: `pg_dump | gzip`
  # вернул бы код gzip и «успешно» сохранил пустоту.
  if ! docker compose -f "$COMPOSE_FILE" exec -T db \
    pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" > "$DUMP"; then
    rm -f "$DUMP"
    fail "pg_dump failed"
  fi
fi

STEP="dump check"
if [ "$DRY_RUN" = "1" ]; then
  log "dry-run: test -s $DUMP"
  log "dry-run: docker compose -f $COMPOSE_FILE exec -T db" \
    "pg_restore --list < $DUMP"
else
  # Оглавление читается внутри того же контейнера: битый архив падает
  # здесь, а не через месяц при восстановлении. Пустой и битый файл
  # удаляем, иначе он занял бы место в локальной ротации.
  if [ ! -s "$DUMP" ]; then
    rm -f "$DUMP"
    fail "dump is empty"
  fi
  if ! docker compose -f "$COMPOSE_FILE" exec -T db \
    pg_restore --list < "$DUMP" > /dev/null; then
    rm -f "$DUMP"
    fail "dump is broken"
  fi
fi

STEP="upload dump"
if [ "$DRY_RUN" = "1" ]; then
  log "dry-run: $RCLONE_BIN copy $DUMP $REMOTE:$BUCKET/db/" \
    "--no-traverse"
else
  "$RCLONE_BIN" copy "$DUMP" "$REMOTE:$BUCKET/db/" --no-traverse \
    || fail "dump upload failed"
fi

STEP="rotate dumps"
if [ "$DRY_RUN" = "1" ]; then
  log "dry-run: keep $KEEP_LOCAL newest $BACKUP_DIR/*.dump, delete rest"
  log "dry-run: $RCLONE_BIN delete $REMOTE:$BUCKET/db/" \
    "--min-age ${RETENTION_DAYS}d"
else
  if [ "$KEEP_LOCAL" -gt 0 ]; then
    { ls -1t "$BACKUP_DIR"/*.dump 2> /dev/null || true; } \
      | tail -n "+$((KEEP_LOCAL + 1))" \
      | while read -r old; do rm -f -- "$old"; done
  fi
  # `--min-age` считает от даты объекта: имя с датой нужно человеку,
  # а не ротации.
  "$RCLONE_BIN" delete "$REMOTE:$BUCKET/db/" \
    --min-age "${RETENTION_DAYS}d" \
    || fail "bucket rotation failed"
fi

STEP="media"
if is_true "$USE_S3"; then
  require_var S3_PRIVATE_BUCKET "$S3_PRIVATE_BUCKET"
  require_var S3_BUCKET "$S3_BUCKET"
  # `copy`, а не `sync`: sync удаляет в бакете то, чего нет в источнике,
  # и повторяет ошибочное удаление уже в единственной копии.
  if [ "$DRY_RUN" = "1" ]; then
    log "dry-run: $RCLONE_BIN copy $MEDIA_S3_REMOTE:$S3_PRIVATE_BUCKET" \
      "$REMOTE:$BUCKET/originals/ --transfers 4 --checksum"
    log "dry-run: $RCLONE_BIN copy $MEDIA_S3_REMOTE:$S3_BUCKET" \
      "$REMOTE:$BUCKET/public/ --transfers 4 --checksum"
    MEDIA_COUNT="-"
  else
    "$RCLONE_BIN" copy "$MEDIA_S3_REMOTE:$S3_PRIVATE_BUCKET" \
      "$REMOTE:$BUCKET/originals/" --transfers 4 --checksum \
      || fail "originals copy failed"
    "$RCLONE_BIN" copy "$MEDIA_S3_REMOTE:$S3_BUCKET" \
      "$REMOTE:$BUCKET/public/" --transfers 4 --checksum \
      || fail "public media copy failed"
    MEDIA_COUNT="$(count_remote "$MEDIA_S3_REMOTE:$S3_PRIVATE_BUCKET")"
    MEDIA_COUNT="${MEDIA_COUNT:--}"
  fi
else
  [ -d media ] || fail "media directory is missing"
  if [ "$DRY_RUN" = "1" ]; then
    log "dry-run: $RCLONE_BIN copy ./media $REMOTE:$BUCKET/media/" \
      "--transfers 4 --checksum"
    MEDIA_COUNT="-"
  else
    "$RCLONE_BIN" copy ./media "$REMOTE:$BUCKET/media/" \
      --transfers 4 --checksum \
      || fail "media copy failed"
    MEDIA_COUNT=$(find media -type f | wc -l)
  fi
fi

STEP="summary"
DUMP_SIZE=$(du -h "$DUMP" 2> /dev/null | cut -f1 || true)
log "OK: dump=${DUMP_SIZE:--} media=${MEDIA_COUNT:-?} exit=0"
exit 0
