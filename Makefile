MANAGE := uv run python manage.py
DC := docker compose

.PHONY: help install lint format format-check test test-cov migrate migrations shell superuser \
        run collectstatic messages compile i18n seed-demo seed-demo-flush search-reindex \
        photos-backfill up down down-clean restart build rebuild logs ps \
        plan-list plan-dry plan-step plan-from plan-all

help: ## Показать доступные команды
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ======================================================================
# Локальная разработка (без Docker)
# ======================================================================

install: ## Установить зависимости (uv sync)
	uv sync

lint: ## Проверить код линтером и форматирование (ruff)
	uv run ruff check .
	uv run ruff format --check .

format: ## Форматировать код (ruff format)
	uv run ruff format .

test: ## Запустить тесты (pytest)
	uv run pytest

test-cov: ## Запустить тесты с покрытием
	uv run pytest --cov=apps --cov=config --cov-report=term-missing

run: ## Запустить сервер разработки
	$(MANAGE) runserver

migrate: ## Применить миграции
	$(MANAGE) migrate

migrations: ## Создать миграции
	$(MANAGE) makemigrations

shell: ## Открыть Django shell
	$(MANAGE) shell

superuser: ## Создать суперпользователя
	$(MANAGE) createsuperuser

collectstatic: ## Собрать статику
	$(MANAGE) collectstatic --noinput

# ======================================================================
# Демо-данные (только для разработки)
# ======================================================================

seed-demo: ## Создать демонстрационный архив для разработки
	$(MANAGE) seed_demo

seed-demo-flush: ## Удалить демо-данные и их файлы
	$(MANAGE) seed_demo --flush

# ======================================================================
# Поиск
# ======================================================================

search-reindex: ## Пересчитать поисковый индекс архива
	$(MANAGE) search_reindex

# ======================================================================
# Обработка фото
# ======================================================================

photos-backfill: ## Обработать фото без версии для показа и перцептивного хеша
	$(MANAGE) photos_backfill

# ======================================================================
# Локализация
# ======================================================================

messages: ## Извлечь строки для перевода
	$(MANAGE) makemessages -a --ignore=.venv --ignore=.venv313

compile: ## Скомпилировать переводы
	$(MANAGE) compilemessages --ignore=.venv --ignore=.venv313

i18n: messages compile ## Полный цикл локализации

# ======================================================================
# Пошаговое выполнение плана подэтапа (каждый шаг — новая сессия Koda)
#
# PLAN — файл плана, BRANCH — ветка (по умолчанию feature/<имя плана>),
# STEP — номер шага. Примеры:
#   make plan-list PLAN=.koda/plans/photo-processing-3.2-photo-upload.md
#   make plan-step PLAN=... STEP=1
#   make plan-all  PLAN=... BRANCH=feature/photo-upload
# ======================================================================

RUN_PLAN := .koda/scripts/run-plan.sh
PLAN_BRANCH = $(if $(BRANCH),--branch $(BRANCH),)

plan-list: ## Показать шаги плана: PLAN=<файл>
	$(RUN_PLAN) $(PLAN) --list

plan-dry: ## Показать, что выполнит plan-all: PLAN=<файл>
	$(RUN_PLAN) $(PLAN) --all --dry-run $(PLAN_BRANCH)

plan-step: ## Выполнить один шаг: PLAN=<файл> STEP=<номер>
	@test -n "$(PLAN)" || { echo "укажите PLAN=<файл плана>"; exit 1; }
	@test -n "$(STEP)" || { echo "укажите STEP=<номер шага>"; exit 1; }
	$(RUN_PLAN) $(PLAN) --step $(STEP) $(PLAN_BRANCH)

plan-from: ## Выполнить шаги начиная с STEP: PLAN=<файл> STEP=<номер>
	@test -n "$(PLAN)" || { echo "укажите PLAN=<файл плана>"; exit 1; }
	@test -n "$(STEP)" || { echo "укажите STEP=<номер шага>"; exit 1; }
	$(RUN_PLAN) $(PLAN) --from $(STEP) $(PLAN_BRANCH)

plan-all: ## Выполнить все невыполненные шаги: PLAN=<файл> [BRANCH=<ветка>]
	@test -n "$(PLAN)" || { echo "укажите PLAN=<файл плана>"; exit 1; }
	$(RUN_PLAN) $(PLAN) --all $(PLAN_BRANCH)

# ======================================================================
# Docker
# ======================================================================

up: ## Поднять все сервисы
	$(DC) up -d

down: ## Остановить все сервисы
	$(DC) down

down-clean: ## Остановить сервисы и удалить тома (уничтожает данные!)
	$(DC) down -v --remove-orphans

restart: ## Перезапустить все сервисы
	$(DC) restart

build: ## Собрать образы
	$(DC) build

rebuild: ## Пересобрать образы и перезапустить
	$(DC) up -d --build

logs: ## Показать логи всех сервисов
	$(DC) logs -f

ps: ## Показать статус сервисов
	$(DC) ps
