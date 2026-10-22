MANAGE := uv run python manage.py
DC := docker compose

.PHONY: help install lint format format-check test test-cov migrate migrations shell superuser \
        run collectstatic messages compile i18n seed-demo seed-demo-flush search-reindex \
        photos-backfill up down down-clean restart build rebuild logs ps

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
