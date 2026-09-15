#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import fcntl
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import config

REPO_DIR = Path(__file__).resolve().parent
BRANCH = "main"
started_at = None
LOCK_FILE = config.STATE_DIR / "publish.lock"
_lock_fp = None  # держим файл открытым на весь процесс — ОС снимет блокировку сама, даже если процесс упадёт


def acquire_lock_or_exit() -> None:
    """Не даёт двум прогонам (например, часовой таймер и кнопка "Обновить
    сейчас" на веб-панели) одновременно писать в один git-репозиторий.
    Если кто-то уже держит блокировку — тихо выходим, это не ошибка."""
    global _lock_fp
    config.STATE_DIR.mkdir(exist_ok=True)
    _lock_fp = open(LOCK_FILE, "w")
    try:
        fcntl.flock(_lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Другой прогон уже выполняется (плановый или кнопка на панели) — "
              "пропускаю этот запуск, ничего не трогаю.")
        sys.exit(0)

# False — публиковать вручную то, что уже лежит в pages_content/, без
# повторного обхода старого сайта.
RUN_PIPELINE_FIRST = True


def write_status(**fields) -> None:
    """Пишет state/run_status.json — его читает веб-панель, чтобы показать
    время последнего запуска и был ли он успешным."""
    config.STATE_DIR.mkdir(exist_ok=True)
    config.RUN_STATUS_FILE.write_text(
        json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run(cmd, cwd, timeout=None) -> bool:
    """Выводит команду в терминал сразу по мере поступления, а не одним
    куском в конце (важно и для крона: см. флаг -u в run_pipeline).

    timeout — предохранитель от зависшего намертво прогона. Без него
    достаточно, чтобы старый сайт начал отвечать по байту в минуту (не
    попадая в per-request TIMEOUT), и процесс повиснет навсегда, держа
    publish.lock: плановые прогоны перестанут запускаться, а панель
    навсегда покажет «идёт проверка»."""
    print(f"  $ {' '.join(str(c) for c in cmd)}", flush=True)
    try:
        result = subprocess.run(cmd, cwd=cwd, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"[!] Превышен лимит времени ({timeout} с) — процесс прерван.",
              file=sys.stderr, flush=True)
        return False
    return result.returncode == 0


def run_pipeline() -> None:
    print(f"[{datetime.now():%H:%M:%S}] Запускаю page_content_parser.py (обход старого сайта)...", flush=True)
    if not run([sys.executable, "-u", str(REPO_DIR / "page_content_parser.py")],
               cwd=REPO_DIR, timeout=config.PARSER_TIMEOUT_SECONDS):
        print("Парсер завершился с ошибкой — публикацию прерываю.", file=sys.stderr)
        write_status(
            started_at=started_at, finished_at=datetime.now().isoformat(timespec="seconds"),
            ok=False, stage="parser", message="Парсер завершился с ошибкой (см. лог).",
        )
        sys.exit(1)
    print()


def git_publish() -> None:
    if not (REPO_DIR / ".git").exists():
        print(f"В папке {REPO_DIR} нет .git — это не подключённый git-репозиторий.")
        print("Проверьте, что вы клонировали сюда репозиторий (git clone ...), а не")
        print("просто создали папку руками.")
        write_status(
            started_at=started_at, finished_at=datetime.now().isoformat(timespec="seconds"),
            ok=False, stage="git", message="В папке нет .git — репозиторий не подключён.",
        )
        sys.exit(1)

    print("\nОбновляю git...")
    subprocess.run(["git", "add", "-A"], cwd=REPO_DIR)

    status = subprocess.getoutput(f"git -C \"{REPO_DIR}\" status --porcelain")
    if status:
        print("Найдены изменения, сохраняю (commit)...")
        commit_message = f"Обновление данных сайта — {datetime.now():%Y-%m-%d %H:%M}"
        subprocess.run(["git", "commit", "-m", commit_message], cwd=REPO_DIR)
    else:
        print("Локальных изменений нет. Проверяем очередь на отправку...")

    # Пушим в любом случае: если предыдущая отправка сорвалась по сети,
    # локальный коммит остался и должен уйти сейчас.
    print("Синхронизирую с GitHub...")
    push_process = subprocess.run(["git", "push", "origin", BRANCH], cwd=REPO_DIR)

    finished_at = datetime.now().isoformat(timespec="seconds")
    if push_process.returncode != 0:
        print("\n[!] ОШИБКА: не удалось отправить данные на GitHub (проблема с сетью).")
        print("[!] Данные сохранены локально и будут отправлены при следующем запуске.")
        write_status(
            started_at=started_at, finished_at=finished_at, ok=False, stage="push",
            message="Не удалось отправить на GitHub — сохранено локально, уйдёт при следующем запуске.",
        )
    else:
        print("\n[+] Синхронизация с сервером успешно завершена!")
        write_status(
            started_at=started_at, finished_at=finished_at, ok=True, stage="done",
            message="Успешно обновлено и отправлено на GitHub.",
        )


def main():
    global started_at
    acquire_lock_or_exit()
    started_at = datetime.now().isoformat(timespec="seconds")
    if RUN_PIPELINE_FIRST:
        run_pipeline()
    git_publish()


if __name__ == "__main__":
    main()
