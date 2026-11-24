"""Тесты ``scripts/backup.sh`` без Docker, PostgreSQL и сети.

Скрипт бэкапа ломается молча: пустой дамп, опечатка в переменной,
секрет в журнале. Проверить настоящий ``pg_dump`` без Docker нельзя,
поэтому тесты держат dry-run и конфигурацию, а восстановление
проверяется руками на стейдже (шаг 8 плана).
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "backup.sh"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
GITIGNORE = REPO_ROOT / ".gitignore"

TOKEN_VALUE = "123456789:AAH-secret-token-value"

# Переменные, которые скрипт читает из .env. Новая переменная без
# строки в .env.example даёт красный тест: иначе на сервере её
# никто не заведёт, а dry-run пройдёт на пустом значении.
SCRIPT_VARIABLES = (
    "POSTGRES_DB",
    "POSTGRES_USER",
    "USE_S3",
    "S3_BUCKET",
    "S3_PRIVATE_BUCKET",
    "BACKUP_COMPOSE_FILE",
    "BACKUP_RCLONE_REMOTE",
    "BACKUP_BUCKET",
    "BACKUP_KEEP_LOCAL",
    "BACKUP_RETENTION_DAYS",
    "BACKUP_MIN_FREE_MB",
    "RCLONE_BIN",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_PROXY",
)


@pytest.fixture
def backup_env(tmp_path):
    """Полный временный .env и существующий compose-файл."""
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n")
    env = tmp_path / ".env"
    env.write_text(
        "POSTGRES_DB=sportmemo\n"
        "POSTGRES_USER=sportmemo\n"
        "USE_S3=False\n"
        f"BACKUP_COMPOSE_FILE={compose}\n"
        "BACKUP_RCLONE_REMOTE=cloudru\n"
        "BACKUP_BUCKET=sportmemo-backup\n"
        "BACKUP_KEEP_LOCAL=7\n"
        "BACKUP_RETENTION_DAYS=30\n"
        # Порог опущен: тест не должен зависеть от свободного места
        # на машине разработчика и на CI.
        "BACKUP_MIN_FREE_MB=1\n"
        f"TELEGRAM_BOT_TOKEN={TOKEN_VALUE}\n"
        "TELEGRAM_CHAT_ID=-1001234567890\n"
        "TELEGRAM_PROXY=http://127.0.0.1:8888\n"
    )
    return env


@pytest.fixture
def repo_media():
    """Скрипт проверяет ``media/`` даже в dry-run.

    В чистом клоне каталога нет (он в .gitignore). Создаём пустой
    и убираем, только если создали сами.
    """
    media = REPO_ROOT / "media"
    created = not media.exists()
    if created:
        media.mkdir()
    yield media
    if created and media.exists():
        media.rmdir()


@pytest.fixture
def fake_bin(tmp_path):
    """Заглушки docker/rclone/curl: пишут маркер при запуске.

    Так проверяется «dry-run ничего не выполняет» без наблюдения
    за процессами: отсутствие маркера — доказательство.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    markers = {}
    for name in ("docker", "rclone", "curl"):
        marker = tmp_path / f"{name}.called"
        markers[name] = marker
        script = bindir / name
        script.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 0\n')
        script.chmod(0o755)
    return bindir, markers


def _run(env_file, *args, path=None, env_extra=None):
    env = os.environ.copy()
    env["DRY_RUN"] = "0"
    env["BACKUP_ENV_FILE"] = str(env_file)
    # Скрипт доверяет окружению больше, чем файлу (так работают разовый
    # запуск и `Environment=` в юните systemd). Но `config/settings.py`
    # кладёт значения `.env` в `os.environ` при импорте Django, и без
    # очистки тест проверял бы не свой файл, а локальный `.env`.
    for name in SCRIPT_VARIABLES:
        env.pop(name, None)
    if path is not None:
        # Заглушки идут первыми, системный PATH остаётся: без него
        # скрипт не найдёт bash, awk, df.
        env["PATH"] = f"{path}:/usr/local/bin:/usr/bin:/bin"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=60,
    )


def _output(result):
    return result.stdout + result.stderr


def test_script_has_valid_syntax():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_dry_run_prints_plan_and_runs_nothing(backup_env, fake_bin, repo_media):
    bindir, markers = fake_bin

    result = _run(backup_env, "--dry-run", path=bindir)

    assert result.returncode == 0, _output(result)
    output = _output(result)
    assert "pg_dump" in output
    assert "rclone copy" in output
    assert not markers["docker"].exists()
    assert not markers["rclone"].exists()


def test_dry_run_with_s3_media_prints_two_copies(backup_env, fake_bin):
    bindir, _ = fake_bin
    with backup_env.open("a") as handle:
        handle.write("USE_S3=True\n")
        handle.write("S3_BUCKET=sportmemo\n")
        handle.write("S3_PRIVATE_BUCKET=sportmemo-private\n")

    result = _run(backup_env, "--dry-run", path=bindir)

    assert result.returncode == 0, _output(result)
    output = _output(result)
    assert "sportmemo-private" in output
    assert "sportmemo-backup/originals/" in output
    assert "sportmemo-backup/public/" in output


def test_missing_database_name_gives_config_error(backup_env, fake_bin):
    bindir, _ = fake_bin
    text = backup_env.read_text()
    backup_env.write_text(
        "\n".join(
            line for line in text.splitlines() if not line.startswith("POSTGRES_DB=")
        )
    )

    result = _run(backup_env, "--dry-run", path=bindir)

    assert result.returncode == 2
    assert "POSTGRES_DB" in _output(result)


def test_missing_compose_file_gives_config_error(backup_env, fake_bin):
    bindir, _ = fake_bin
    text = backup_env.read_text()
    missing = backup_env.parent / "missing.yml"
    lines = []
    for line in text.splitlines():
        if line.startswith("BACKUP_COMPOSE_FILE="):
            lines.append(f"BACKUP_COMPOSE_FILE={missing}")
        else:
            lines.append(line)
    backup_env.write_text("\n".join(lines) + "\n")

    result = _run(backup_env, "--dry-run", path=bindir)

    assert result.returncode == 2
    assert "compose file not found" in _output(result)


def test_dry_run_does_not_print_telegram_token(backup_env, fake_bin, repo_media):
    bindir, _ = fake_bin

    result = _run(backup_env, "--dry-run", path=bindir)

    assert result.returncode == 0, _output(result)
    assert TOKEN_VALUE not in _output(result)


def test_gitignore_excludes_backups_directory():
    lines = GITIGNORE.read_text().splitlines()

    assert "/backups/" in lines


def test_env_example_lists_every_script_variable():
    text = ENV_EXAMPLE.read_text()
    script_text = SCRIPT.read_text()

    for name in SCRIPT_VARIABLES:
        assert f"env_get {name}" in script_text, name
        assert re.search(rf"^#?\s*{name}=", text, re.MULTILINE), name


def test_environment_overrides_env_file(backup_env, fake_bin, repo_media):
    """Явно заданная переменная важнее строки в `.env`.

    Так работает разовый запуск (`RCLONE_BIN=... ./scripts/backup.sh`)
    и `Environment=` в юните systemd. Без этого явное значение молча
    игнорировалось бы, и на стейдже скрипт писал «rclone is not
    installed» при установленном rclone: каталог `~/.local/bin`
    не попадает в PATH неинтерактивной ssh-сессии.
    """
    bindir, _ = fake_bin
    # В файле значение не задано вовсе — берётся только окружение.
    text = backup_env.read_text()
    backup_env.write_text(
        "\n".join(
            line for line in text.splitlines() if not line.startswith("RCLONE_BIN=")
        )
        + "\n"
    )

    result = _run(
        backup_env,
        "--dry-run",
        path=bindir,
        env_extra={"RCLONE_BIN": "/opt/custom/rclone"},
    )

    assert result.returncode == 0, _output(result)
    assert "/opt/custom/rclone" in _output(result)
