#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Публикует pages_content/ в GitHub: у каждого файла постоянный URL, а
содержимое под ним обновляется при каждом запуске парсера.

Всё лежит в одной папке: сам парсер, этот скрипт и git-репозиторий — это
одна и та же папка (licey22-content), поэтому копировать файлы никуда не
нужно, только закоммитить и запушить.

Запуск:
    python3 publish_to_github.py

Что делает по порядку:
    1. page_content_parser.py (обходит старый сайт заново)
    2. apply_link_map.py, если он есть рядом (замена ссылок по словарю)
    3. git add -A / commit / push

Подробности — в README.md. Пример строки для крона там же.
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
BRANCH = "main"

# False — публиковать вручную то, что уже лежит в pages_content/, без
# повторного обхода старого сайта.
RUN_PIPELINE_FIRST = True


def run(cmd, cwd) -> bool:
    """Выводит команду в терминал сразу по мере поступления, а не одним
    куском в конце (важно и для крона: см. флаг -u в run_pipeline)."""
    print(f"  $ {' '.join(str(c) for c in cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=cwd)
    return result.returncode == 0


def run_pipeline() -> None:
    print(f"[{datetime.now():%H:%M:%S}] Запускаю page_content_parser.py (обход старого сайта)...", flush=True)
    if not run([sys.executable, "-u", str(REPO_DIR / "page_content_parser.py")], cwd=REPO_DIR):
        print("Парсер завершился с ошибкой — публикацию прерываю.", file=sys.stderr)
        sys.exit(1)

    link_map_script = REPO_DIR / "apply_link_map.py"
    if link_map_script.exists():
        print(f"\n[{datetime.now():%H:%M:%S}] Запускаю apply_link_map.py (замена ссылок по словарю)...", flush=True)
        if not run([sys.executable, "-u", str(link_map_script)], cwd=REPO_DIR):
            print("Замена ссылок завершилась с ошибкой — публикацию прерываю.", file=sys.stderr)
            sys.exit(1)
    else:
        print("\n(apply_link_map.py не найден рядом — пропускаю замену ссылок)", flush=True)
    print()


def git_publish() -> None:
    if not (REPO_DIR / ".git").exists():
        print(f"В папке {REPO_DIR} нет .git — это не подключённый git-репозиторий.")
        print("Проверьте, что вы клонировали сюда репозиторий (git clone ...), а не")
        print("просто создали папку руками.")
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

    if push_process.returncode != 0:
        print("\n[!] ОШИБКА: не удалось отправить данные на GitHub (проблема с сетью).")
        print("[!] Данные сохранены локально и будут отправлены при следующем запуске.")
    else:
        print("\n[+] Синхронизация с сервером успешно завершена!")


def main():
    if RUN_PIPELINE_FIRST:
        run_pipeline()
    git_publish()


if __name__ == "__main__":
    main()
