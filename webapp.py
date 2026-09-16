#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import fcntl
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

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
