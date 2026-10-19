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
NEWS_OK = True


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


def run_news_pipeline() -> bool:
    if not config.NEWS_ENABLED:
        return True
    if not run(
        [sys.executable, "-u", str(REPO_DIR / "news_scraper.py"),
         "--out-dir", str(config.NEWS_OUTPUT_DIR)],
        cwd=REPO_DIR,
        timeout=config.NEWS_PARSER_TIMEOUT_SECONDS,
    ):
        previous = {}
        try:
            previous = json.loads(config.NEWS_STATUS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        previous.update(ok=False, attempted_at=datetime.now().isoformat(timespec="seconds"),
                        message="Не удалось обновить новости. Сохранена предыдущая версия.")
        config.NEWS_STATUS_FILE.write_text(json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8")
        return False
    return True


def run_pipeline() -> None:
    global NEWS_OK
    print(
        f"[{datetime.now():%H:%M:%S}] "
        f"Запускаю page_content_parser.py (обход старого сайта)...",
        flush=True,
    )

    if not run(
        [
            sys.executable,
            "-u",
            str(REPO_DIR / "page_content_parser.py"),
        ],
        cwd=REPO_DIR,
        timeout=config.PARSER_TIMEOUT_SECONDS,
    ):
        print(
            "Парсер страниц завершился с ошибкой — публикацию прерываю.",
            file=sys.stderr,
            flush=True,
        )
        write_status(
            started_at=started_at,
            finished_at=datetime.now().isoformat(timespec="seconds"),
            ok=False,
            stage="parser",
            message="Парсер страниц завершился с ошибкой.",
        )
        sys.exit(1)

    NEWS_OK = run_news_pipeline()

    print()


def is_publishable_path(name: str) -> bool:
    parts = Path(name).parts
    if name == "sources.json":
        return True
    if len(parts) != 2:
        return False
    if parts[0] == "pages_content":
        return Path(name).suffix in {".json", ".md"} and not parts[1].startswith(".")
    return NEWS_OK and name in {"news_data/news_index.json", "news_data/news_full.json"}


def git_checked(*args, capture=False):
    return subprocess.run(["git", *args], cwd=REPO_DIR, check=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def stage_content() -> None:
    staged = git_checked("diff", "--cached", "--name-only", "-z", capture=True).decode().split("\0")
    unexpected = [name for name in staged if name and not is_publishable_path(name)]
    if unexpected:
        raise RuntimeError("В индексе есть посторонние файлы; автопубликация отменена: " + ", ".join(unexpected))
    tracked = git_checked("ls-files", "-z", capture=True).decode().split("\0")
    candidates = {name for name in tracked if name and is_publishable_path(name)}
    for folder in ("pages_content", "news_data"):
        candidates.update(p.relative_to(REPO_DIR).as_posix() for p in (REPO_DIR / folder).glob("*")
                          if p.is_file() and is_publishable_path(p.relative_to(REPO_DIR).as_posix()))
    if (REPO_DIR / "sources.json").is_file():
        candidates.add("sources.json")
    if any((REPO_DIR / name).is_symlink() for name in candidates):
        raise RuntimeError("В публикуемых данных обнаружена символическая ссылка.")
    if candidates:
        git_checked("add", "-A", "--", *sorted(candidates))


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
    try:
        stage_content()
        status = git_checked("diff", "--cached", "--name-only", capture=True).strip()
        if status:
            print("Найдены изменения контента, сохраняю (commit)...")
            commit_message = f"Обновление данных сайта — {datetime.now():%Y-%m-%d %H:%M}"
            git_checked("commit", "-m", commit_message)
        else:
            print("Изменений контента нет. Проверяем очередь на отправку...")
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        write_status(started_at=started_at, finished_at=datetime.now().isoformat(timespec="seconds"),
                     ok=False, stage="git", message=f"Автопубликация остановлена: {exc}")
        sys.exit(1)

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
            started_at=started_at, finished_at=finished_at, ok=NEWS_OK,
            stage="done" if NEWS_OK else "news",
            message="Успешно обновлено и отправлено на GitHub." if NEWS_OK else
                    "Страницы опубликованы. Новости не обновились — сохранена предыдущая версия.",
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
