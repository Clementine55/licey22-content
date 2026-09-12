"""Всё, что связано с URL: какие домены свои, как выглядит "красивая" ссылка
на странице, и словари link_map.json / emails_map.json.

link_map.json и emails_map.json пополняются по ходу разбора, но пишутся на
диск не на каждую новую запись, а один раз — вызовом save_maps() в конце
main(). Это единственная причина, почему сохранение здесь не сразу: чтобы
не дёргать диск лишний раз на сотнях ссылок за прогон.
"""

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import config
from utils import load_json, save_json

CUSTOM_LINKS = load_json(config.LINK_MAP_FILE, {})
EMAIL_MAP = load_json(config.EMAILS_MAP_FILE, {})

_link_map_dirty = False
_email_map_dirty = False

RU_MIRROR_HOSTS = ("лицей22.рф", "xn--22-mlclgj2f.xn--p1ai", "архив.лицей22.рф")


def is_blocked(url: str) -> bool:
    low = url.lower()
    return any(s in low for s in config.BLOCKED_URL_SUBSTRINGS)


def is_allowed_page(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in config.ALLOWED_PAGE_DOMAINS


def canonical_key(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path
    if path == "/ru":
        path = "/"
    elif path.startswith("/ru/"):
        path = path[3:]
    path = path.rstrip("/") or "/"
    return f"{config.CANONICAL_HOST}{path.lower()}"


def get_extension(url: str) -> str:
    path = urlparse(url).path
    m = re.search(r"\.[a-zA-Z0-9]{1,5}$", path)
    return m.group(0).lower() if m else ""


def clean_filename(url: str) -> str:
    return unquote(urlparse(url).path.rsplit("/", 1)[-1])


def get_beautiful_path(url: str) -> str:
    path = urlparse(url).path
    path = re.sub(r"\.(html|htm|php)$", "", path, flags=re.IGNORECASE)

    if path.startswith("/ru/"):
        path = path[3:]
    elif path == "/ru":
        path = "/"

    path = path.replace("/sveden/employees/programs/", "/employees/")
    path = path.replace("/sveden/education/", "/education/")
    path = path.replace("/sveden/", "/")
    path = path.replace("/6184/", "/")

    path = re.sub(r"/+", "/", path).rstrip("/")
    return path or "/"


def resolve_url(full_url: str) -> str:
    """Превращает сырую ссылку старого сайта в постоянный "красивый" путь,
    заводя новую запись в link_map.json при первой встрече."""
    global _link_map_dirty

    parsed = urlparse(full_url)
    if parsed.hostname in RU_MIRROR_HOSTS:
        full_url = full_url.replace(parsed.hostname, config.CANONICAL_HOST)
        parsed = urlparse(full_url)

    if not is_allowed_page(full_url) or get_extension(full_url) in config.FILE_EXTENSIONS:
        return full_url

    base_url_key = full_url.split("#")[0].rstrip("/")

    if base_url_key not in CUSTOM_LINKS:
        beautiful_path = get_beautiful_path(base_url_key)
        CUSTOM_LINKS[base_url_key] = beautiful_path
        _link_map_dirty = True
        print(f"  [+] Добавлена новая красивая ссылка в link_map.json: {beautiful_path}")

    target_path = CUSTOM_LINKS[base_url_key]

    if parsed.fragment:
        return f"{target_path}#{parsed.fragment}"
    return target_path


def slugify(url: str) -> str:
    target_path = resolve_url(url).split("#")[0]
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", target_path.strip("/"))
    return slug or "index"


def resolve_email(query_key: str):
    """query_key — например '?p=...'. Возвращает голый email, если адрес уже
    известен, иначе регистрирует ключ как "неизвестный" и возвращает None."""
    global _email_map_dirty

    email = EMAIL_MAP.get(query_key, "").strip()
    if email:
        return email.removeprefix("mailto:")

    if query_key not in EMAIL_MAP:
        EMAIL_MAP[query_key] = ""
        _email_map_dirty = True
    return None


def save_maps():
    """Сбрасывает link_map.json / emails_map.json на диск, если что-то
    поменялось за прогон. Вызывать один раз, в конце main()."""
    global _link_map_dirty, _email_map_dirty
    if _link_map_dirty:
        save_json(config.LINK_MAP_FILE, CUSTOM_LINKS)
        _link_map_dirty = False
    if _email_map_dirty:
        save_json(config.EMAILS_MAP_FILE, EMAIL_MAP)
        _email_map_dirty = False
