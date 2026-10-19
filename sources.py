"""Корни обхода: постоянное хранилище, общее для панели и парсера."""

import fcntl
import json
import os
import tempfile
from urllib.parse import unquote, urlsplit, urlunsplit

import config


class SourcesError(ValueError):
    pass


class DuplicateSource(SourcesError):
    pass


def normalize_source(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourcesError("Введите ссылку на раздел старого сайта.")
    value = value.strip()
    if len(value) > 2048 or any(ord(c) < 33 for c in value) or "\\" in value:
        raise SourcesError("В ссылке есть недопустимые символы.")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        port = parsed.port
    except (ValueError, UnicodeError):
        raise SourcesError("Некорректная ссылка.") from None
    allowed = {h.encode("idna").decode("ascii").lower() for h in config.ALLOWED_PAGE_DOMAINS}
    if parsed.scheme not in {"http", "https"} or host not in allowed:
        raise SourcesError("Разрешены только ссылки HTTP/HTTPS на старый сайт лицея.")
    if parsed.username is not None or parsed.password is not None or port is not None:
        raise SourcesError("Ссылка не должна содержать логин, пароль или порт.")
    if parsed.query or parsed.fragment:
        raise SourcesError("Уберите параметры и якорь из ссылки на раздел.")
    path = parsed.path
    if path == "/ru":
        path = "/"
    elif path.startswith("/ru/"):
        path = path[3:]
    if "." not in path.rstrip("/").rsplit("/", 1)[-1]:
        path = path.rstrip("/") + "/"
    decoded = unquote(path).lower()
    if "%" in decoded or "\\" in decoded or any(c.isspace() for c in decoded):
        raise SourcesError("Некорректный путь страницы.")
    if any(p in {".", "..", ""} for p in decoded.strip("/").split("/")):
        raise SourcesError("Укажите конкретный раздел, без пустых частей пути.")
    if not path.strip("/"):
        raise SourcesError("Укажите конкретный раздел, а не главную страницу сайта.")
    if any(s in decoded for s in config.BLOCKED_URL_SUBSTRINGS):
        raise SourcesError("Новости, версии для печати и технические файлы не добавляются в разделы.")
    suffix = decoded.rstrip("/").rsplit("/", 1)[-1]
    if any(suffix.endswith(ext) for ext in config.FILE_EXTENSIONS):
        raise SourcesError("Нужна страница сайта, а не ссылка на файл.")
    return urlunsplit(("https", config.CANONICAL_HOST, path, "", ""))


def load_sources() -> list:
    if not config.SOURCES_FILE.exists():
        return list(config.TARGET_ROOTS)
    try:
        data = json.loads(config.SOURCES_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list) or not data:
            raise SourcesError("Ожидался непустой список ссылок.")
        roots = [normalize_source(url) for url in data]
        if len(set(roots)) != len(roots):
            raise SourcesError("В списке есть повторы.")
        return roots
    except (OSError, ValueError, TypeError) as exc:
        raise SourcesError("Не удалось прочитать sources.json. Исправьте файл перед обновлением.") from exc


def contains_page(root: str, url: str) -> bool:
    root_path = urlsplit(root).path
    path = urlsplit(url).path
    return path == root_path or (root_path.endswith("/") and path.startswith(root_path))


def add_source(value: str) -> str:
    root = normalize_source(value)
    config.STATE_DIR.mkdir(exist_ok=True)
    with (config.STATE_DIR / "sources.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        roots = load_sources()
        if any(contains_page(existing, root) for existing in roots):
            raise DuplicateSource("Эта страница уже отслеживается внутри добавленного раздела.")
        roots.append(root)
        # Атомарная замена: воркеры gunicorn и парсер видят целый список.
        fd, temp_name = tempfile.mkstemp(prefix=".sources-", dir=config.SOURCES_FILE.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                json.dump(roots, fp, ensure_ascii=False, indent=2)
                fp.write("\n")
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(temp_name, config.SOURCES_FILE)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    return root
