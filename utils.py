"""Мелкие утилиты общего назначения."""

from datetime import datetime


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def load_json(path, default):
    if path.exists():
        import json
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path, data):
    import json
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
