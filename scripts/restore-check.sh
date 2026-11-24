#!/usr/bin/env bash
# Проверка восстановления из бэкапа: дамп → временная БД → сверка счётчиков.
#
# Ворота подэтапа 5.4: бэкап без проверенного восстановления — не бэкап.
# Запускается руками на стейдже и никогда на проде: флаг
# BACKUP_ALLOW_RESTORE_CHECK разрешает создавать и удалять базу, а на
# боевом сервере его нет.
set -Eeuo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR="backups"
STEP="start"
DUMP=""
FROM_BUCKET=0
CREATED=0

usage() {
  printf 'usage: %s [--dump FILE | --from-bucket]\n' "$0" >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dump)
      [ $# -ge 2 ] || usage
      DUMP="$2"
      shift 2
      ;;
    --from-bucket)
      FROM_BUCKET=1
      shift
      ;;
    *) usage ;;
  esac
done

BACKUP_ENV_FILE="${BACKUP_ENV_FILE:-.env}"

# `.env` нельзя исполнять через `source`: строка
# `DEFAULT_FROM_EMAIL=SportMemo <info@sportmemo.ru>` — это редирект
# ввода для bash, и остаток файла молча теряется.
env_get() {
  local key="$1" fallback="${2-}" value=""
  # Окружение важнее файла: разовый запуск и `Environment=` в юните
  # systemd должны работать так же, как строка в `.env`.
  value="${!key-}"
  if [ -z "$value" ] && [ -f "$BACKUP_ENV_FILE" ]; then
    value=$(grep -E "^${key}=" "$BACKUP_ENV_FILE" \
      | tail -n 1 | cut -d= -f2- || true)
  fi
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
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

# 1 — проверка не прошла, 2 — ошибка конфигурации: разные поломки,
# и глазами человека, и для таймера это разные ответы.
config_error() {
  trap - ERR
  log "ERROR: $*"
  exit 2
}

fail() {
  trap - ERR
  log "ERROR: $*"
  exit 1
}

on_error() {
  local code=$?
  trap - ERR
  log "ERROR: step failed: $STEP (exit $code)"
  exit 1
}
trap on_error ERR

POSTGRES_DB="$(env_get POSTGRES_DB)"
POSTGRES_USER="$(env_get POSTGRES_USER)"
COMPOSE_FILE="$(env_get BACKUP_COMPOSE_FILE)"
REMOTE="$(env_get BACKUP_RCLONE_REMOTE)"
BUCKET="$(env_get BACKUP_BUCKET)"
RCLONE_BIN="$(env_get RCLONE_BIN rclone)"
ALLOW="$(env_get BACKUP_ALLOW_RESTORE_CHECK 0)"
# Имя временной базы не настраивается: проверка шаблона ниже — это
# защита от выстрела в живую базу, и обходить её переменной нельзя.
RESTORE_DB="${POSTGRES_DB}_restore_check"

STEP="config"
if [ "$ALLOW" != "1" ]; then
  config_error "BACKUP_ALLOW_RESTORE_CHECK is not 1 (stage only)"
fi
for pair in "POSTGRES_DB=$POSTGRES_DB" \
  "POSTGRES_USER=$POSTGRES_USER" \
  "BACKUP_COMPOSE_FILE=$COMPOSE_FILE" \
  "BACKUP_RCLONE_REMOTE=$REMOTE" \
  "BACKUP_BUCKET=$BUCKET"; do
  [ -n "${pair#*=}" ] || config_error "${pair%%=*} is not set"
done
[ -f "$COMPOSE_FILE" ] \
  || config_error "compose file not found: $COMPOSE_FILE"

# Защита от выстрела в живую базу. Проверка имени стоит до createdb
# и до dropdb: опечатка в переменной не должна удалить боевую базу.
case "$RESTORE_DB" in
  *_restore_check) ;;
  *) config_error "temporary database must be named *_restore_check" ;;
esac
if [ "$RESTORE_DB" = "$POSTGRES_DB" ]; then
  config_error "temporary database must differ from the live one"
fi

STEP="pick dump"
if [ "$FROM_BUCKET" = "1" ]; then
  command -v "$RCLONE_BIN" > /dev/null 2>&1 \
    || config_error "$RCLONE_BIN is not installed"
  # Сортируем по времени объекта, а не по имени: имя с датой нужно
  # человеку, а порядок задаёт провайдер.
  latest=$("$RCLONE_BIN" lsf "$REMOTE:$BUCKET/db/" --files-only \
    --format tp 2> /dev/null | sort | tail -n 1 | cut -d';' -f2- || true)
  [ -n "$latest" ] \
    || fail "no dumps in $REMOTE:$BUCKET/db/"
  log "downloading $latest from $REMOTE:$BUCKET/db/"
  mkdir -p "$BACKUP_DIR"
  "$RCLONE_BIN" copy "$REMOTE:$BUCKET/db/$latest" "$BACKUP_DIR/" \
    || fail "dump download failed"
  DUMP="$BACKUP_DIR/$latest"
elif [ -z "$DUMP" ]; then
  DUMP=$({ ls -1t "$BACKUP_DIR"/*.dump 2> /dev/null || true; } | head -n 1)
  [ -n "$DUMP" ] \
    || config_error "no local dumps in $BACKUP_DIR" \
      "(use --dump or --from-bucket)"
fi
[ -f "$DUMP" ] || config_error "dump not found: $DUMP"
[ -s "$DUMP" ] || fail "dump is empty"
log "checking $DUMP"

# Временная БД не должна пережить проверку: иначе следующий прогон
# упадёт на существующем имени, а на диске останется копия ПДн.
cleanup() {
  local code=$?
  if [ "$CREATED" = "1" ]; then
    if ! docker compose -f "$COMPOSE_FILE" exec -T db \
      dropdb -U "$POSTGRES_USER" --if-exists "$RESTORE_DB" > /dev/null; then
      log "WARN: could not drop $RESTORE_DB"
    fi
  fi
  exit "$code"
}
trap cleanup EXIT

STEP="create database"
# Остаток от оборванного прогона удаляем сами: имя уже проверено
# шаблоном `*_restore_check`, живой базы оно не касается.
docker compose -f "$COMPOSE_FILE" exec -T db \
  dropdb -U "$POSTGRES_USER" --if-exists "$RESTORE_DB" > /dev/null 2>&1 || true
docker compose -f "$COMPOSE_FILE" exec -T db \
  createdb -U "$POSTGRES_USER" "$RESTORE_DB"
CREATED=1

STEP="restore"
# `-U` обязателен: `docker exec` запускает команду от root, и без него
# libpq берёт роль `root`, которой в контейнере нет. Ошибка выглядит
# как «connection to server ... failed: role "root" does not exist»,
# а не как проблема дампа.
docker compose -f "$COMPOSE_FILE" exec -T db \
  pg_restore -U "$POSTGRES_USER" --no-owner --no-privileges \
  -d "$RESTORE_DB" < "$DUMP" \
  || fail "pg_restore failed"

count_rows() {
  docker compose -f "$COMPOSE_FILE" exec -T db \
    psql -U "$POSTGRES_USER" -d "$1" -Atc "select count(*) from $2;"
}

STEP="compare"
PHOTOS=0
TEAMS=0
PEOPLE=0
mismatch=0
printf '%-20s %8s %8s\n' "таблица" "живая" "восстановленная"
for table in photos_photo catalog_team catalog_person django_migrations; do
  live=$(count_rows "$POSTGRES_DB" "$table")
  restored=$(count_rows "$RESTORE_DB" "$table")
  printf '%-20s %8s %8s\n' "$table" "$live" "$restored"
  [ "$live" = "$restored" ] || mismatch=1
  case "$table" in
    photos_photo) PHOTOS=$restored ;;
    catalog_team) TEAMS=$restored ;;
    catalog_person) PEOPLE=$restored ;;
  esac
done
if [ "$mismatch" = "1" ]; then
  fail "counts differ between live and restored database"
fi

log "restore verified: ${PHOTOS} photos, ${TEAMS} teams, ${PEOPLE} people"
exit 0
