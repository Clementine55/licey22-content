#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ----------------------------- НАСТРОЙКИ -----------------------------------

# Папка, где лежит этот скрипт — она же и есть git-репозиторий, и она же
# папка парсера. Всё определяется автоматически, руками путь прописывать
# не нужно.
REPO_DIR = Path(__file__).resolve().parent

BRANCH = "main"
COMMIT_MESSAGE = f"Обновление данных сайта — {datetime.now():%Y-%m-%d %H:%M}"

# Запускать ли парсер и сборку библиотеки перед публикацией. Выключите
# (False), если хотите иногда публиковать вручную без повторного обхода
# старого сайта (например, если просто правите текст в pages_content/ руками).
RUN_PIPELINE_FIRST = True

# -----------------------------------------------------------------------


def run(cmd, cwd):
    """Запускает команду, отдавая её вывод в терминал СРАЗУ по мере
    поступления (не дожидаясь завершения процесса). Раньше вывод
    буферизовался целиком и печатался одним куском в конце — из-за этого
    долгий парсер выглядел как зависший, хотя честно печатал прогресс."""
    print(f"  $ {' '.join(str(c) for c in cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=cwd)  # без capture_output — вывод идёт напрямую в терминал
    return result.returncode == 0


def run_pipeline():
    """Запускает page_content_parser.py и apply_link_map.py перед публикацией.
    Оба скрипта ожидаются в этой же папке и сами кладут результат сюда же —
    копировать никуда не нужно.

    apply_link_map.py идёт СРАЗУ после парсера — это важно: парсер каждый
    раз перезаписывает pages_content/ с нуля (обходя старый сайт заново),
    так что замены ссылок нужно накатывать заново при каждом запуске.

    combine_library.py больше не используется и не запускается.

    Флаг -u (unbuffered) важен не только для живого терминала, но и для
    крона: если вывод перенаправлен в файл (>> publish.log), Python по
    умолчанию буферизует его большими кусками и лог обновляется рывками —
    с -u каждая строка пишется сразу."""
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


def main():
    if RUN_PIPELINE_FIRST:
        run_pipeline()

    if not (REPO_DIR / ".git").exists():
        print(f"В папке {REPO_DIR} нет .git — это не подключённый git-репозиторий.")
        print("Проверьте, что вы клонировали сюда репозиторий (git clone ...), а не")
        print("просто создали папку руками.")
        sys.exit(1)

    print("\nОбновляю git...")
    import subprocess
    import datetime
    
    # Добавляем все файлы
    subprocess.run(["git", "add", "-A"])
    
    # Проверяем, есть ли изменения
    status = subprocess.getoutput("git status --porcelain")
    
    if status:
        print("Найдены изменения, сохраняю (commit)...")
        time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        subprocess.run(["git", "commit", "-m", f"Обновление данных сайта — {time_str}"])
    else:
        print("Локальных изменений нет. Проверяем очередь на отправку...")

    # ВАЖНО: Отправляем на сервер В ЛЮБОМ СЛУЧАЕ!
    print("Синхронизирую с GitHub...")
    push_process = subprocess.run(["git", "push", "origin", "main"])
    
    if push_process.returncode != 0:
        print("\n[!] ОШИБКА: Не удалось отправить данные на GitHub (проблема с сетью).")
        print("[!] Не переживайте: данные сохранены локально и будут отправлены при следующем запуске.")
    else:
        print("\n[+] Синхронизация с сервером успешно завершена!")

if __name__ == "__main__":
    main()