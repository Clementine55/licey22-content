"""Настройки парсера. Здесь и только здесь меняются пути, домены и таймауты."""

from pathlib import Path

# Разделы старого сайта, которые обходим
TARGET_ROOTS = [
    "https://s3454.nubex.ru/sveden/",
    "https://s3454.nubex.ru/6184/",
    "https://s3454.nubex.ru/5933/",
    "https://s3454.nubex.ru/16451/",
]

# Домены, которые считаем "своими" (разные зеркала старого сайта)
ALLOWED_PAGE_DOMAINS = {
    "s3454.nubex.ru",
    "архив.лицей22.рф",
    "xn--80a1acny.xn--22-mlclgj2f.xn--p1ai",
    "лицей22.рф",
    "xn--22-mlclgj2f.xn--p1ai",
}
CANONICAL_HOST = "s3454.nubex.ru"

BLOCKED_URL_SUBSTRINGS = ["/news/", "printmode=yes", "/_data/"]
CONTENT_SELECTOR_CANDIDATES = ["div.siteContent", "div.content", "body"]

HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
BOLD_TAGS = {"b", "strong"}
ITALIC_TAGS = {"i", "em"}

FILE_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".rtf", ".odt", ".ods", ".zip", ".rar", ".7z",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff",
    ".mp4", ".avi", ".mov",
}

MAX_PAGES_PER_SECTION = 500
REQUEST_DELAY = 0.15
TIMEOUT = 8
USER_AGENT = "Mozilla/5.0 (compatible; Lyceum22ContentBot/1.0)"
HEADERS = {"User-Agent": USER_AGENT}

# Все пути — относительно текущей папки (парсер всегда запускается из неё)
OUTPUT_DIR = Path("pages_content")
STATE_DIR = Path("state")
STATE_FILE = STATE_DIR / "page_state.json"
LINK_MAP_FILE = STATE_DIR / "link_map.json"
EMAILS_MAP_FILE = STATE_DIR / "emails_map.json"
TOC_FILE = OUTPUT_DIR / "_toc.json"


def _migrate_legacy_file(old: Path, new: Path) -> None:
    """Разовый переезд: если файл раньше лежал прямо в корне репозитория
    (старая раскладка), переносим его в новую папку state/ при первом же
    запуске — руками ничего двигать не нужно."""
    if old.exists() and not new.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)


for _target in (STATE_FILE, LINK_MAP_FILE, EMAILS_MAP_FILE):
    _migrate_legacy_file(Path(_target.name), _target)

