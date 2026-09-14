#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import time
from pathlib import Path

import config
from utils import now

REPO_DIR = Path(__file__).resolve().parent


def run_once() -> None:
    print(f"[{now()}] Запускаю плановый прогон publish_to_github.py", flush=True)
    subprocess.run(
        [sys.executable, "-u", str(REPO_DIR / "publish_to_github.py")],
        cwd=REPO_DIR,
    )


def main() -> None:
    while True:
        run_once()
        interval_seconds = config.PUBLISH_INTERVAL_MINUTES * 60
        print(f"[{now()}] Следующий плановый прогон через "
              f"{config.PUBLISH_INTERVAL_MINUTES} мин.", flush=True)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
