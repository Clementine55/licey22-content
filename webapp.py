#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
        running = _running
    return jsonify({"running": running, "last_run": status})


@app.route("/api/update-now", methods=["POST"])
def api_update_now():
    global _running
    with _run_lock:
        if _running:
            return jsonify({"started": False, "reason": "already_running"}), 409
        _running = True
    thread = threading.Thread(target=_run_publish_in_background, daemon=True)
    thread.start()
    return jsonify({"started": True})


if __name__ == "__main__":
    # host="0.0.0.0" — доступно из локальной сети (школьный сервер),
    # не только с самой машины. Порт можно поменять при необходимости.
    app.run(host="0.0.0.0", port=8420)
