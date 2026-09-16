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

# Если новых страниц найдено заметно меньше, чем было в прошлый раз —
# это, скорее всего, не "страницы удалили с сайта", а сбой сети или сайт
# временно лёг. В таком случае разрушительные операции (удаление
# "осиротевших" файлов, обрезание page_state.json, перезапись _toc.json)
# лучше пропустить, чем случайно снести весь локальный кэш.
MIN_HEALTHY_CRAWL_RATIO = 0.7

# Интервал планового прогона для scheduler.py, в минутах. Поменять — просто
# отредактировать эту строку и перезапустить сервис (systemctl restart
# licey22-scheduler), без правки systemd-юнитов и daemon-reload.
PUBLISH_INTERVAL_MINUTES = 60

MAX_PAGES_PER_SECTION = 500

# Предохранитель: сколько максимум может длиться один прогон парсера.
# Обычный прогон занимает ~1-2 минуты; если упёрлись в этот лимит —
# что-то пошло не так (сайт отвечает крайне медленно, но не настолько,
# чтобы сработал TIMEOUT отдельного запроса), процесс прерывается, чтобы
# не держать publish.lock вечно.
PARSER_TIMEOUT_SECONDS = 30 * 60

REQUEST_DELAY = 0.15
TIMEOUT = 8
USER_AGENT = "Mozilla/5.0 (compatible; Lyceum22ContentBot/1.0)"
HEADERS = {"User-Agent": USER_AGENT}

# Пути привязаны к папке, где лежит САМ этот файл (config.py), а не к
# текущей рабочей директории процесса. Раньше было Path("pages_content")/
# Path("state") — относительные пути, которые resolve'ятся от os.getcwd()
# в момент обращения. Это работало, пока скрипт запускался ровно так, как
# задумано (publish_to_github.py сам делает cwd=REPO_DIR перед стартом
# парсера) — но стоило запустить файл вручную не из той папки (например,
# для отладки: `sudo -u site-svc .../python /opt/licey22-content/
# publish_to_github.py`, стоя в /home/site) — и все файлы состояния начинали
# искаться там, куда как раз запускали, а не там, где реально лежит проект.
# У вас это всплыло как "Permission denied" — потому что /home/site
# сервисному пользователю и на чтение-то недоступен, не то что на запись.
BASE_DIR = Path(__file__).resolve().parent

OUTPUT_DIR = BASE_DIR / "pages_content"
STATE_DIR = BASE_DIR / "state"
STATE_FILE = STATE_DIR / "page_state.json"
LINK_MAP_FILE = STATE_DIR / "link_map.json"
EMAILS_MAP_FILE = STATE_DIR / "emails_map.json"
CHANGELOG_FILE = STATE_DIR / "changelog.json"
RUN_STATUS_FILE = STATE_DIR / "run_status.json"
PROGRESS_FILE = STATE_DIR / "progress.json"
CHANGELOG_MAX_ENTRIES = 3000  # аварийный потолок на случай очень частого расписания — см. changelog.py
CHANGELOG_KEEP_DAYS = 7  # сколько дней истории показывать в веб-панели
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

