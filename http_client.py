"""Загрузка страниц с повторной попыткой и логом прогресса."""

import sys
import time

import requests

import config
from utils import now

_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(max_retries=0)
_session.mount("http://", _adapter)
_session.mount("https://", _adapter)

# HTML уже скачанных страниц. discover_section_pages() скачивает каждую
# страницу, чтобы найти на ней ссылки — тот же самый HTML нужен потом ещё
# раз, чтобы разобрать содержимое. Без кэша это был бы двойной запрос на
# каждую страницу; с кэшем — ровно один.
_html_cache: dict[str, str] = {}


def fetch(url: str):
    """Скачивает страницу. При сбое пробует ещё раз один раз, затем сдаётся."""
    start = time.monotonic()
    for attempt in (1, 2):
        try:
            resp = _session.get(url, headers=config.HEADERS, timeout=config.TIMEOUT)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or resp.encoding
            elapsed = time.monotonic() - start
            print(f"  [{now()}] загружено за {elapsed:.1f}с: {url}", flush=True)
            _html_cache[url] = resp.text
            return resp
        except requests.RequestException as e:
            if attempt == 1:
                print(f"  [{now()}] [!] попытка {attempt} не удалась ({e}) — пробую ещё раз: {url}", file=sys.stderr, flush=True)
                continue
            elapsed = time.monotonic() - start
            print(f"  [{now()}] [!] не удалось загрузить за {elapsed:.1f}с: {url}: {e}", file=sys.stderr, flush=True)
            return None


def get_cached_html(url: str):
    """Забирает ранее скачанный HTML этой страницы, если он есть (и удаляет
    его из кэша — второй раз он не понадобится)."""
    return _html_cache.pop(url, None)

