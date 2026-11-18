#!/usr/bin/env bash
# Деплой стейджа: сборка образа, миграции, статика, запуск.
#
# Запускается на сервере из корня репозитория (`make stage-deploy` делает
# это по ssh с рабочей машины). Порядок шагов обязателен: при `DEBUG=False`
# без `collectstatic` нет манифеста статики, и любая страница с
# `{% static %}` отвечает 500.
set -euo pipefail
cd "$(dirname "$0")/.."

DC="docker compose -f docker-compose.stage.yml"

# Каталоги данных создаём сами: отсутствующий bind-mount docker создаёт
# от root, и appuser (uid 1000) не смог бы туда писать ни `display`,
# ни `collectstatic`.
mkdir -p media staticfiles

$DC build
$DC up -d db redis

# `run --rm` выполняет шаг отдельным контейнером: миграции и статика
# не должны идти одновременно из двух воркеров gunicorn.
$DC run --rm web python manage.py migrate --noinput
$DC run --rm web python manage.py collectstatic --noinput

# Домен сайта ставит data-миграция как sportmemo.ru. На стейдже он другой:
# иначе canonical, og:url и ссылки в письмах ведут на боевой сайт, и
# проверяется не то, что показывает стейдж.
$DC run --rm web python manage.py shell -c \
  "from django.contrib.sites.models import Site; \
Site.objects.filter(pk=1).update(domain='stage.sportmemo.ru', \
name='SportMemo (stage)')"

$DC up -d web worker

# nginx работает от www-data и читает файлы прямо с хоста: контейнер
# создаёт их от своего пользователя, и права обязаны пускать чужого
# читателя (та же готча, что в TaskMan).
chmod -R o+rX staticfiles media

$DC ps
