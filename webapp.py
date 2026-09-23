#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import fcntl
import os
import secrets
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_from_directory

import config
from utils import load_json

REPO_DIR = Path(__file__).resolve().parent
STATIC_DIR = REPO_DIR / "webapp_static"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")

# Пароль для кнопки "Обновить сейчас". Берётся из переменной окружения
# (в systemd-юните — Environment=LICEY22_PANEL_TOKEN=...). Если не задан,
# панель работает в режиме "только просмотр": журнал виден всем в школьной
# сети, но запустить обновление нельзя. Так безопаснее по умолчанию: без
# настройки нельзя случайно оставить открытую кнопку, дёргающую git push.
PANEL_TOKEN = os.environ.get("LICEY22_PANEL_TOKEN", "").strip()

# Минимальный интервал между запусками через кнопку, секунд. Защищает от
# «задолбить кнопку» — лок и так не даст двум прогонам идти параллельно,
# но без этого можно наплодить процессов, которые стартуют и сразу умирают.
MIN_SECONDS_BETWEEN_MANUAL_RUNS = 30
RATE_LIMIT_FILE = config.STATE_DIR / "last_manual_run"


def is_publish_running() -> bool:
    """Проверяет РЕАЛЬНОЕ состояние publish.lock — того же файла, который
    берёт publish_to_github.py.

    Единственный источник правды о том, идёт ли прогон. Раньше рядом жила
    ещё и переменная _running в памяти процесса — от неё пришлось
    отказаться: она не видела плановые прогоны от scheduler.py (отдельный
    процесс, чужая память) и окончательно сломалась бы под gunicorn с
    несколькими воркерами, где у каждого воркера своя копия памяти.
    Файловый лок же общий для всех процессов на машине."""
    config.STATE_DIR.mkdir(exist_ok=True)
    try:
        with open(config.STATE_DIR / "publish.lock", "w") as fp:
            fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fp, fcntl.LOCK_UN)  # смогли взять — значит свободно, сразу отпускаем
            return False
    except OSError:
        return True  # кто-то другой (плановый прогон или другая вкладка) уже держит лок


def _rate_limited() -> bool:
    """True, если с прошлого ручного запуска прошло слишком мало времени."""
    try:
        last = float(RATE_LIMIT_FILE.read_text())
    except (OSError, ValueError):
        return False
    return (time.time() - last) < MIN_SECONDS_BETWEEN_MANUAL_RUNS


def _mark_manual_run() -> None:
    config.STATE_DIR.mkdir(exist_ok=True)
    RATE_LIMIT_FILE.write_text(str(time.time()))


def _run_publish_detached() -> None:
    """Запускает publish_to_github.py как самостоятельный фоновый процесс.

    Раньше это делалось через threading + subprocess.run, и поток жил
    внутри веб-процесса: при перезапуске/падении панели (или при рестарте
    воркера gunicorn) прогон обрывался на середине. Теперь процесс
    отвязан (start_new_session) — панель может перезапускаться, прогон
    продолжается сам."""
    subprocess.Popen(
        [sys.executable, "-u", str(REPO_DIR / "publish_to_github.py")],
        cwd=REPO_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/changelog")
def api_changelog():
    entries = load_json(config.CHANGELOG_FILE, [])
    return jsonify(list(reversed(entries)))


# Порог, начиная с которого повторяющийся заголовок в _toc.json считаем
# не настоящим названием страницы, а заглушкой движка старого сайта (Nubex
# у части страниц вместо <h1> отдаёт название всего сайта). Держим порог,
# а не жёстко зашитую строку — заглушка не всегда одна и та же, но она
# всегда встречается заметно чаще, чем реальное совпадающее название двух
# разных страниц.
GENERIC_TITLE_MIN_COUNT = 3


def _pages_by_section() -> dict:
    """Раскладывает pages_content/_toc.json по разделам старого сайта
    (config.TARGET_ROOTS) — тем самым списком, который реально обходит
    crawler.py. Нужно для веб-панели: показать сотруднику, какие разделы
    старого сайта вообще проверяются автоматически, а какие — нет (см.
    README, "Веб-панель обновлений")."""
    toc = load_json(config.TOC_FILE, [])

    title_counts = Counter(e.get("title", "") for e in toc)
    generic_title = None
    if title_counts:
        top_title, top_count = title_counts.most_common(1)[0]
        if top_count >= GENERIC_TITLE_MIN_COUNT:
            generic_title = top_title

    buckets = {root: [] for root in config.TARGET_ROOTS}
    other = []
    for entry in toc:
        url = entry.get("url", "")
        root = next((r for r in config.TARGET_ROOTS if url.startswith(r)), None)
        (buckets[root] if root is not None else other).append(entry)

    def page_view(entry: dict) -> dict:
        title = entry.get("title") or entry["url"]
        return {
            "url": entry["url"],
            "title": title,
            # заглушка вместо реального заголовка — фронтенд в этом случае
            # покажет путь страницы вместо повторяющегося названия сайта
            "generic_title": generic_title is not None and title == generic_title,
            "file_count": entry.get("file_count", 0),
        }

    sections = []
    for root in config.TARGET_ROOTS:
        entries = sorted(buckets[root], key=lambda e: e["url"])
        root_entry = next((e for e in entries if e["url"] == root), None)
        label = None
        if root_entry:
            root_title = root_entry.get("title") or ""
            if root_title and root_title != generic_title:
                label = root_title
        sections.append({
            "root": root,
            "path": urlparse(root).path,
            "label": label,
            "pages": [page_view(e) for e in entries],
        })

    if other:
        # Страницы, которые есть в _toc.json, но не попали ни под один
        # текущий TARGET_ROOTS — обычно значит, что список разделов в
        # config.py поменяли, а _toc.json ещё от прошлого прогона.
        sections.append({
            "root": None,
            "path": None,
            "label": "Прочее (вне текущих разделов)",
            "pages": [page_view(e) for e in sorted(other, key=lambda e: e["url"])],
        })

    return {"total": len(toc), "sections": sections}


@app.route("/api/pages")
def api_pages():
    return jsonify(_pages_by_section())


@app.route("/api/status")
def api_status():
    status = load_json(config.RUN_STATUS_FILE, None)
    running = is_publish_running()
    # прогресс отдаём, только пока реально идёт прогон — иначе можно
    # случайно показать цифры от прошлого завершённого запуска
    progress = load_json(config.PROGRESS_FILE, None) if running else None
    return jsonify({
        "running": running,
        "progress": progress,
        "last_run": status,
        # фронтенд прячет кнопку, если запуск не настроен — чтобы не
        # показывать кнопку, которая заведомо ответит 403
        "can_trigger": bool(PANEL_TOKEN),
    })


@app.route("/api/update-now", methods=["POST"])
def api_update_now():
    if not PANEL_TOKEN:
        return jsonify({
            "started": False,
            "reason": "disabled",
            "message": "Запуск с панели не настроен (нет LICEY22_PANEL_TOKEN).",
        }), 403

    provided = (request.headers.get("X-Panel-Token") or "").strip()
    # secrets.compare_digest — сравнение за постоянное время, чтобы по
    # скорости ответа нельзя было подбирать токен посимвольно
    if not provided or not secrets.compare_digest(provided, PANEL_TOKEN):
        return jsonify({"started": False, "reason": "forbidden",
                        "message": "Неверный пароль."}), 403

    if _rate_limited():
        return jsonify({
            "started": False,
            "reason": "rate_limited",
            "message": f"Слишком часто — подождите {MIN_SECONDS_BETWEEN_MANUAL_RUNS} сек.",
        }), 429

    if is_publish_running():
        return jsonify({"started": False, "reason": "already_running",
                        "message": "Обновление уже идёт."}), 409

    _mark_manual_run()
    _run_publish_detached()

    # Popen() возвращается мгновенно, но дочерний процесс (запуск python,
    # импорты) реально берёт publish.lock не сразу — может пройти
    # 100-300мс. Если ответить браузеру ДО этого момента, его первый же
    # запрос /api/status ещё увидит running: false (лок пока не взят) и
    # решит, что обновление уже закончилось, хотя оно только стартовало.
    # Ждём здесь (недолго, с запасом) реального появления лока, чтобы
    # к моменту ответа браузеру статус был гарантированно верным.
    for _ in range(40):  # 40 x 50мс = 2 секунды максимум
        if is_publish_running():
            break
        time.sleep(0.05)

    return jsonify({"started": True})


if __name__ == "__main__":
    # Локальная разработка. На сервере панель поднимается через gunicorn
    # (см. deploy/licey22-webapp.service и README) — встроенный сервер
    # Flask для постоянной работы не предназначен.
    app.run(host="127.0.0.1", port=8420)
