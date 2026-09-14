import hashlib
import json

import config
from utils import load_json, save_json

PAGE_STATE = load_json(config.STATE_FILE, {})


def blocks_hash(page_data: dict) -> str:
    canonical = json.dumps(page_data, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def has_changed(url: str, page_data: dict) -> bool:
    return PAGE_STATE.get(url) != blocks_hash(page_data)


def record(url: str, page_data: dict) -> None:
    PAGE_STATE[url] = blocks_hash(page_data)


def prune(valid_urls: set) -> None:
    """Убирает из состояния страницы, которых больше нет на сайте."""
    for url in list(PAGE_STATE):
        if url not in valid_urls:
            del PAGE_STATE[url]


def save() -> None:
    save_json(config.STATE_FILE, PAGE_STATE)
