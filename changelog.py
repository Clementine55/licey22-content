import difflib
from datetime import datetime, timedelta

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
    """Добавляет запись об этом прогоне в конец журнала и обрезает историю.

    Раньше хранились последние config.CHANGELOG_MAX_ENTRIES записей — при
    частом расписании (например, раз в 5-15 минут) это могло оказаться
    заметно МЕНЬШЕ семи дней, а не "история за неделю", как хотелось.
    Теперь обрезка по времени (config.CHANGELOG_KEEP_DAYS), а не по счётчику
    записей — сколько бы прогонов ни было в сутки, неделя остаётся неделей.
    config.CHANGELOG_MAX_ENTRIES по-прежнему действует как аварийный потолок
    на случай, если расписание однажды поставят совсем частым (раз в
    минуту) — чтобы файл не разросся бесконтрольно."""
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

    cutoff = datetime.now() - timedelta(days=config.CHANGELOG_KEEP_DAYS)
    entries = [e for e in entries if _entry_timestamp(e) >= cutoff]
    entries = entries[-config.CHANGELOG_MAX_ENTRIES:]

    save_json(config.CHANGELOG_FILE, entries)


def _entry_timestamp(entry: dict) -> datetime:
    try:
        return datetime.fromisoformat(entry["timestamp"])
    except (KeyError, ValueError):
        # запись без валидного времени (например, из очень старой версии
        # формата) — считаем её "древней", чтобы обрезалась в первую очередь
        return datetime.min
