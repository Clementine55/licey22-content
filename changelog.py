import difflib
from datetime import datetime

import config
from utils import load_json, save_json


def diff_snippet(old_text: str, new_text: str, limit: int = 6):
    """Короткий человекочитаемый diff между старым и новым Markdown
    страницы: что добавилось и что убралось, до `limit` строк каждого."""
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    added, removed = [], []
    for line in difflib.unified_diff(old_lines, new_lines, lineterm=""):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith("+"):
            text = line[1:].strip()
            if text:
                added.append(text)
        elif line.startswith("-"):
            text = line[1:].strip()
            if text:
                removed.append(text)
    return added[:limit], removed[:limit]


def record_run(*, added: list, updated: list, removed: list,
               healthy: bool, pages_found: int, pages_known_before: int) -> None:
    """Добавляет запись об этом прогоне в конец журнала и обрезает историю
    до config.CHANGELOG_MAX_ENTRIES самых свежих записей."""
    entries = load_json(config.CHANGELOG_FILE, [])
    entries.append({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "healthy": healthy,
        "pages_found": pages_found,
        "pages_known_before": pages_known_before,
        "added": added,
        "updated": updated,
        "removed": removed,
    })
    entries = entries[-config.CHANGELOG_MAX_ENTRIES:]
    save_json(config.CHANGELOG_FILE, entries)
