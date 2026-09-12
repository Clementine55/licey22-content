"""Хэш РАЗОБРАННОГО содержимого каждой страницы, сохранённый между запусками.

Парсим страницу всегда — сеть и CPU дешёвые. Хэш нужен только для одного:
решить, стоит ли перезаписывать .json/.md на диске (а значит — попадёт ли
страница в git diff). Хэшируем то, что вышло из extract_blocks(), а не
сырой HTML: в сыром HTML старого сайта (Nubex) есть шум, меняющийся от
запроса к запросу (счётчики, токены форм, версии статики), из-за которого
хэш сырого HTML "прыгает" даже без реальных изменений на странице.
"""

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
