#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Публикует pages_content/ и library.json в GitHub, чтобы у каждого файла
был ПОСТОЯННЫЙ URL (не меняется между запусками), а содержимое под ним
обновлялось при каждом запуске парсера.

Эта версия — под структуру, где ВСЁ лежит в одной папке: сам парсер,
publish_to_github.py и git-репозиторий — это одна и та же папка
(licey22-content). Никакого копирования файлов из другого места не
происходит — page_content_parser.py и combine_library.py и так пишут
результат прямо сюда же, остаётся только закоммитить и запушить.

Запуск (из этой же папки, или откуда угодно — пути не важны):
    python3 publish_to_github.py

Что делает по порядку:
    1. Запускает page_content_parser.py (обходит старый сайт заново)
    2. Запускает combine_library.py (собирает library.json/.md)
    3. git add -A / git commit / git push — с сообщением коммита с датой

В кроне — раз в день ночью, например:
    0 3 * * * cd "/home/clementine/Visual Studio Code/Python/licey22-content" && "/home/clementine/Visual Studio Code/Python/licey22-content/.venv/bin/python" publish_to_github.py >> publish.log 2>&1
"""

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
    """Запускает page_content_parser.py, apply_link_map.py и combine_library.py
    перед публикацией. Все скрипты ожидаются в этой же папке и сами кладут
    результат сюда же — копировать никуда не нужно.

    apply_link_map.py идёт СРАЗУ после парсера и ДО combine_library.py —
    это важно: парсер каждый раз перезаписывает pages_content/ с нуля
    (обходя старый сайт заново), так что замены ссылок нужно накатывать
    заново при каждом запуске, а library.json должен собираться уже из
    исправленных ссылок, а не из сырых.

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

    print(f"\n[{datetime.now():%H:%M:%S}] Запускаю combine_library.py (сборка library.json/.md)...", flush=True)
    if not run([sys.executable, "-u", str(REPO_DIR / "combine_library.py")], cwd=REPO_DIR):
        print("Сборка библиотеки завершилась с ошибкой — публикацию прерываю.", file=sys.stderr)
        sys.exit(1)
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