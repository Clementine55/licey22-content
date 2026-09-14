#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import fcntl
import subprocess
import sys
import threading
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

import config
from utils import load_json

REPO_DIR = Path(__file__).resolve().parent
STATIC_DIR = REPO_DIR / "webapp_static"

app = Flask(__name__, static_folder=None)

_run_lock = threading.Lock()
_running = False


def is_publish_running() -> bool:
    """Проверяет РЕАЛЬНОЕ состояние publish.lock — того же файла, который
    берёт publish_to_github.py. В отличие от _running (которая знает только
    про запуски, стартовавшие через кнопку на этой же панели), это видит
    и плановые прогоны от scheduler.py — это отдельный процесс, у него нет
    доступа к памяти webapp.py, но лок-файл на диске общий для всех."""
    config.STATE_DIR.mkdir(exist_ok=True)
    try:
        with open(config.STATE_DIR / "publish.lock", "w") as fp:
            fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fp, fcntl.LOCK_UN)  # смогли взять — значит свободно, сразу отпускаем
            return False
    except OSError:
        return True  # кто-то другой (плановый прогон или другая кнопка) уже держит лок


def _run_publish_in_background():
    global _running
    try:
        subprocess.run(
            [sys.executable, "-u", str(REPO_DIR / "publish_to_github.py")],
            cwd=REPO_DIR,
        )
    finally:
        with _run_lock:
            _running = False


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
    with _run_lock:
        own_run = _running
    running = own_run or is_publish_running()
    return jsonify({"running": running, "last_run": status})


@app.route("/api/update-now", methods=["POST"])
def api_update_now():
    global _running
    with _run_lock:
        if _running or is_publish_running():
            return jsonify({"started": False, "reason": "already_running"}), 409
        _running = True
    thread = threading.Thread(target=_run_publish_in_background, daemon=True)
    thread.start()
    return jsonify({"started": True})


if __name__ == "__main__":
    # host="0.0.0.0" — доступно из локальной сети (школьный сервер),
    # не только с самой машины. Порт можно поменять при необходимости.
    app.run(host="0.0.0.0", port=8420)
