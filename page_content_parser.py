#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""

Задача: избавить от ручного копирования текста подзаголовков и абзацев
со старого сайта при переносе на новый (Тильда). Результат — готовый
JSON/Markdown "слепок" каждой страницы, по которому легко верстать блоки
"подзаголовок -> текст/список -> файлы -> подзаголовок -> ..." 1-в-1.

Какие страницы обрабатываются:
    1. Раздел "Сведения об ОО" (/sveden/) — обходится ЦЕЛИКОМ: скрипт сам
       находит все подстраницы раздела (общие сведения, документы,
       образование, питание и т.д.) и разбирает каждую.
    2. Отдельные страницы, заданные явно в EXTRA_PAGES (Проекты, Безопасность,
       Отдых детей и оздоровление) — разбираются как есть, без обхода вглубь.

Формат одного блока в JSON:
    {"type": "heading", "level": "h2", "text": "..."}
    {"type": "paragraph", "text": "..."}
    {"type": "list", "items": ["...", "..."]}
    {"type": "file", "link_text": "...", "file_name": "...",
     "extension": "pdf", "file_url": "..."}

Установка зависимостей:
    pip install requests beautifulsoup4

Запуск:
    python3 page_content_parser.py

Результат:
    pages_content/<слаг-страницы>.json   — полная структура блоков
    pages_content/<слаг-страницы>.md     — то же самое, читаемо глазами,
                                            для быстрой сверки вручную
    pages_content/_toc.json              — сводка по всем страницам:
                                            url, заголовок, список подзаголовков
                                            (чтобы окинуть взглядом весь раздел
                                            и ничего не забыть перенести)

"""

import json
import re
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup, Tag, NavigableString

# ----------------------------- НАСТРОЙКИ -----------------------------------

# Корневые страницы разделов. Для КАЖДОЙ скрипт сам проверяет, есть ли у неё
# подстраницы (div.childdocs, как на /sveden/, /5933/, /16451/), и если да —
# обходит их все, ограничиваясь путём этого раздела (см. discover_section_pages).
# Если у страницы подстраниц нет — просто разбирается она одна.
TARGET_ROOTS = [
    "https://лицей22.рф/sveden/",        # Сведения об ОО
    "https://лицей22.рф/6184/17478/",    # Региональная инновационная площадка
    "https://лицей22.рф/6184/20427/",    # Стажировочная площадка инклюзивного образования
    "https://лицей22.рф/5933/",          # Безопасность
    "https://лицей22.рф/16451/",         # Отдых детей и их оздоровление
]

ALLOWED_PAGE_DOMAINS = {"лицей22.рф", "xn--22-mlclgj2f.xn--p1ai"}
CANONICAL_HOST = "xn--22-mlclgj2f.xn--p1ai"

BLOCKED_URL_SUBSTRINGS = ["/news/", "printmode=yes", "/_data/"]

# Кандидаты на контейнер основного контента — берётся первый найденный.
# Проверено на реальной странице: контент лежит в div.siteContent.
CONTENT_SELECTOR_CANDIDATES = ["div.siteContent", "div.content", "body"]

HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
FILE_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".rtf", ".odt", ".ods", ".zip", ".rar", ".7z",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff",
}

MAX_PAGES_PER_SECTION = 500
REQUEST_DELAY = 0.15
TIMEOUT = 8
USER_AGENT = "Mozilla/5.0 (compatible; Lyceum22ContentBot/1.0)"
HEADERS = {"User-Agent": USER_AGENT}

OUTPUT_DIR = Path("pages_content")

# Сессия requests с ОТКЛЮЧЕННЫМИ скрытыми повторами urllib3. Без этого при
# сетевых сбоях (обрывы SSL и т.п.) urllib3 внутри себя молча повторяет
# запрос по несколько раз перед тем как вернуть ошибку — снаружи это
# выглядит как "скрипт завис", хотя на самом деле просто идут невидимые
# попытки. Явный контроль повторов (см. fetch()) с логами понятнее.
_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(max_retries=0)
_session.mount("http://", _adapter)
_session.mount("https://", _adapter)


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")

# ----------------------------- ВСПОМОГАТЕЛЬНОЕ ------------------------------


def is_blocked(url: str) -> bool:
    low = url.lower()
    return any(s in low for s in BLOCKED_URL_SUBSTRINGS)


def is_allowed_page(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ALLOWED_PAGE_DOMAINS


def canonical_key(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path
    if path == "/ru":
        path = "/"
    elif path.startswith("/ru/"):
        path = path[3:]
    path = path.rstrip("/") or "/"
    return f"{CANONICAL_HOST}{path.lower()}"


def get_extension(url: str) -> str:
    path = urlparse(url).path
    m = re.search(r"\.[a-zA-Z0-9]{1,5}$", path)
    return m.group(0).lower() if m else ""


def clean_filename(url: str) -> str:
    return unquote(urlparse(url).path.rsplit("/", 1)[-1])


def slugify(url: str) -> str:
    path = urlparse(url).path.strip("/") or "index"
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", path)
    return slug or "index"


def fetch(url: str):
    start = time.monotonic()
    for attempt in (1, 2):
        try:
            resp = _session.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or resp.encoding
            elapsed = time.monotonic() - start
            print(f"  [{now()}] загружено за {elapsed:.1f}с: {url}", flush=True)
            return resp
        except requests.RequestException as e:
            if attempt == 1:
                print(f"  [{now()}] [!] попытка {attempt} не удалась ({e}) — пробую ещё раз: {url}",
                      file=sys.stderr, flush=True)
                continue
            elapsed = time.monotonic() - start
            print(f"  [{now()}] [!] не удалось загрузить за {elapsed:.1f}с: {url}: {e}",
                  file=sys.stderr, flush=True)
            return None


def get_content_root(soup: BeautifulSoup) -> Tag:
    for sel in CONTENT_SELECTOR_CANDIDATES:
        node = soup.select_one(sel)
        if node is not None:
            return node
    return soup


# ----------------------------- ОБХОД РАЗДЕЛА /sveden/ -----------------------


def discover_section_pages(prefix: str, start_url: str):
    """Обходит все страницы сайта, чей путь начинается с prefix, и
    возвращает множество их URL. Используется только для "живых" разделов
    вроде /sveden/, где заранее не известно, сколько там подстраниц."""
    found = set()
    visited_keys = set()
    queue = deque([start_url])

    while queue and len(found) < MAX_PAGES_PER_SECTION:
        url = queue.popleft()
        if not is_allowed_page(url) or is_blocked(url):
            continue
        key = canonical_key(url)
        if key in visited_keys:
            continue
        visited_keys.add(key)

        path = urlparse(url).path
        if not path.startswith(prefix) and url != start_url:
            continue

        resp = fetch(url)
        time.sleep(REQUEST_DELAY)
        if resp is None:
            continue
        found.add(url)

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            full_url = urljoin(url, href)
            if is_blocked(full_url) or not is_allowed_page(full_url):
                continue
            if get_extension(full_url):  # файл, не страница
                continue
            full_path = urlparse(full_url).path
            if full_path.startswith(prefix) and canonical_key(full_url) not in visited_keys:
                queue.append(full_url)

    return found


# ----------------------------- ИЗВЛЕЧЕНИЕ БЛОКОВ СО СТРАНИЦЫ -----------------


BOLD_TAGS = {"b", "strong"}
ITALIC_TAGS = {"i", "em"}


def render_inline_text(tag: Tag, page_url: str = "", plain_links: bool = False) -> str:
    """Собирает текст внутри тега, сохраняя:
    - жирный/курсивный текст как **bold** / *italic*
    - обычные гиперссылки (не файлы) как [текст](url) — раньше они
      просто теряли href и превращались в невзрачный текст.

    plain_links=True — не оборачивать ссылки в markdown-разметку, а просто
    взять их текст как есть. Нужно, когда мы уже отдельно строим "file"-блок
    из этого же тега и не хотим задвоенную markdown-ссылку внутри подписи."""

    def is_hidden(node: Tag) -> bool:
        style = (node.get("style") or "").replace(" ", "").lower()
        return "display:none" in style

    def collect(node) -> str:
        if isinstance(node, NavigableString):
            return str(node)
        if not isinstance(node, Tag):
            return ""
        if is_hidden(node):
            # напр. <span style="display:none">Не указан</span> — на старом
            # сайте это скрытая заглушка, показывать её на новом не нужно
            return ""
        if node.name == "br":
            return " "
        if node.name in BOLD_TAGS:
            inner = "".join(collect(c) for c in node.children).strip()
            return f"**{inner}**" if inner else ""
        if node.name in ITALIC_TAGS:
            inner = "".join(collect(c) for c in node.children).strip()
            return f"*{inner}*" if inner else ""
        if node.name == "a" and node.get("href"):
            href = node["href"].strip()
            inner = "".join(collect(c) for c in node.children).strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                return inner
            if plain_links:
                return inner
            full_url = urljoin(page_url, href) if page_url else href
            return f"[{inner}]({full_url})" if inner else ""
        return "".join(collect(c) for c in node.children)

    text = "".join(collect(c) for c in tag.children)
    # схлопываем пробелы/переносы, как это делает get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_table(table: Tag, page_url: str) -> dict:
    """Разбирает <table> в блок {"type": "table", "headers": [...], "rows": [...]}.
    Строка с одной ячейкой на colspan (частый паттерн — жирный подзаголовок
    разбивающий таблицу на группы, напр. "Структурные подразделения...")
    помечается отдельно (span_all), чтобы на Тильде её можно было отрисовать
    протянутой на всю ширину, а не сикось-накось по колонкам."""
    headers = []
    thead = table.find("thead")
    if thead:
        for th in thead.find_all(["th", "td"]):
            headers.append(render_inline_text(th, page_url))

    if thead:
        thead_tr_ids = {id(tr) for tr in thead.find_all("tr")}
    else:
        thead_tr_ids = set()

    tbody = table.find("tbody")
    trs = tbody.find_all("tr", recursive=False) if tbody else [
        tr for tr in table.find_all("tr") if id(tr) not in thead_tr_ids
    ]

    rows = []
    for tr in trs:
        cells = tr.find_all(["td", "th"], recursive=False)
        if not cells:
            continue
        if len(cells) == 1 and cells[0].has_attr("colspan"):
            text = render_inline_text(cells[0], page_url)
            if text:
                rows.append({"span_all": True, "text": text})
            continue
        rows.append({"span_all": False, "cells": [render_inline_text(c, page_url) for c in cells]})

    return {"type": "table", "headers": headers, "rows": rows}


def extract_blocks(soup: BeautifulSoup, page_url: str):
    """Проходит по содержимому страницы сверху вниз и строит список блоков:
    подзаголовки, абзацы, списки, файлы — в том порядке, в котором они
    расположены на странице."""
    root = get_content_root(soup)
    blocks = []
    seen_tags = set()  # чтобы не разбирать один и тот же <p>/<li> дважды

    def tag_id(tag):
        return id(tag)

    # На страницах-разделах (/sveden/, /5933/, /16451/...) вверху лежит
    # div.childdocs — список ссылок на подстраницы раздела. Это НЕ обычный
    # текстовый список, а навигация, поэтому выносим её в отдельный тип
    # блока "child_pages" и исключаем из общего разбора, чтобы не путать
    # с настоящим content-списком (как в примере с "Безопасность", где
    # ниже childdocs идёт ещё и реальный текст/файлы).
    childdocs = root.select_one("div.childdocs")
    if childdocs is not None:
        child_pages = []
        for a in childdocs.find_all("a", href=True):
            seen_tags.add(tag_id(a))
            full_url = urljoin(page_url, a["href"].strip())
            child_pages.append({"title": a.get_text(strip=True), "url": full_url})
        for li in childdocs.find_all("li"):
            seen_tags.add(tag_id(li))
        for ul in childdocs.find_all("ul"):
            seen_tags.add(tag_id(ul))
        if child_pages:
            blocks.append({"type": "child_pages", "items": child_pages})

    def has_file_link(container: Tag):
        """Возвращает (a_tag, ext, full_url), если внутри container ровно ОДНА
        ссылка на файл — тогда весь текст container'а (включая соседний текст
        типа "(рабочая тетрадь)" или "(Корпус на Чаплыгина, 59)") можно взять
        как подпись файла. Если ссылок на файлы несколько или нет — None."""
        found = []
        for a in container.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            full_url = urljoin(page_url, href)
            ext = get_extension(full_url)
            if ext in FILE_EXTENSIONS:
                found.append((a, ext, full_url))
        return found

    def mark_seen_recursive(container: Tag):
        seen_tags.add(tag_id(container))
        for child in container.find_all(True):
            seen_tags.add(tag_id(child))

    def has_seen_ancestor(node: Tag) -> bool:
        for anc in node.parents:
            if tag_id(anc) in seen_tags:
                return True
        return False

    for node in root.descendants:
        if not isinstance(node, Tag):
            continue

        if tag_id(node) in seen_tags or has_seen_ancestor(node):
            continue

        if node.name in HEADING_TAGS:
            text = render_inline_text(node, page_url)
            if text:
                blocks.append({"type": "heading", "level": node.name, "text": text})
            continue

        if node.name in ("ul", "ol"):
            # список целиком обрабатываем здесь и помечаем вложенные li,
            # чтобы не продублировать их как отдельные абзацы
            items = []
            for li in node.find_all("li", recursive=False):
                file_links = has_file_link(li)
                if len(file_links) == 1:
                    # весь <li> — по сути один файл (как findex.xlsx в
                    # <li><div><span><a>...</a>(13 КБ)</span></div></li>) —
                    # выносим отдельным file-блоком, а не строкой списка,
                    # чтобы файл не показывался на странице дважды
                    a, ext, full_url = file_links[0]
                    link_text = render_inline_text(li, page_url, plain_links=True)
                    blocks.append({
                        "type": "file",
                        "link_text": link_text or a.get_text(strip=True) or clean_filename(full_url),
                        "file_name": clean_filename(full_url),
                        "extension": ext.lstrip("."),
                        "file_url": full_url,
                    })
                    mark_seen_recursive(li)
                else:
                    mark_seen_recursive(li)
                    t = render_inline_text(li, page_url)
                    if t:
                        items.append(t)
            if items:
                blocks.append({"type": "list", "items": items})
            continue

        if node.name == "table":
            table_block = extract_table(node, page_url)
            if table_block["headers"] or table_block["rows"]:
                blocks.append(table_block)
            mark_seen_recursive(node)
            continue

        if node.name == "div" and node.find(["p", "div", "ul", "ol", "table"]):
            # это просто обёртка (внутри есть другие блоковые теги, в т.ч.
            # таблица) — не разбираем её целиком, дадим дойти обходу до
            # вложенных тегов по отдельности, иначе рискуем задвоить файлы
            continue

        if node.name in ("p", "div"):
            file_links = has_file_link(node)
            if len(file_links) == 1:
                # весь блок — один файл (+ возможно поясняющий текст рядом,
                # напр. "(рабочая тетрадь)" или "(Корпус на Чаплыгина, 59)")
                a, ext, full_url = file_links[0]
                link_text = render_inline_text(node, page_url, plain_links=True)
                blocks.append({
                    "type": "file",
                    "link_text": link_text or a.get_text(strip=True) or clean_filename(full_url),
                    "file_name": clean_filename(full_url),
                    "extension": ext.lstrip("."),
                    "file_url": full_url,
                })
                mark_seen_recursive(node)
            elif len(file_links) > 1:
                # несколько файлов в одном блоке — редкий случай, разносим
                # каждый отдельным file-блоком по его собственному тексту
                for a, ext, full_url in file_links:
                    blocks.append({
                        "type": "file",
                        "link_text": a.get_text(strip=True) or clean_filename(full_url),
                        "file_name": clean_filename(full_url),
                        "extension": ext.lstrip("."),
                        "file_url": full_url,
                    })
                mark_seen_recursive(node)
            elif node.name == "p":
                # обычный текстовый абзац — обычные (не файловые) ссылки
                # сохраняются как [текст](url) внутри текста
                text = render_inline_text(node, page_url)
                if text:
                    blocks.append({"type": "paragraph", "text": text})
                mark_seen_recursive(node)
            # пустой <div> без файлов — не блок, отдаём его детей на обход дальше
            continue

    return blocks


def blocks_to_markdown(page_title: str, page_url: str, blocks) -> str:
    lines = [f"# {page_title}", f"_{page_url}_", ""]
    for b in blocks:
        if b["type"] == "heading":
            hashes = "#" * min(int(b["level"][1]) + 1, 6)
            lines.append(f"{hashes} {b['text']}")
        elif b["type"] == "paragraph":
            lines.append(b["text"])
        elif b["type"] == "list":
            for item in b["items"]:
                lines.append(f"- {item}")
        elif b["type"] == "file":
            lines.append(f"📎 [{b['link_text']}]({b['file_url']}) ({b['extension']})")
        elif b["type"] == "child_pages":
            lines.append("**Подстраницы раздела:**")
            for item in b["items"]:
                lines.append(f"- [{item['title']}]({item['url']})")
        elif b["type"] == "table":
            if b["headers"]:
                lines.append("| " + " | ".join(b["headers"]) + " |")
                lines.append("|" + "|".join(["---"] * len(b["headers"])) + "|")
            for row in b["rows"]:
                if row.get("span_all"):
                    lines.append(f"**{row['text']}**")
                else:
                    lines.append("| " + " | ".join(c.replace("|", "\\|") for c in row["cells"]) + " |")
        lines.append("")
    return "\n".join(lines)


# ----------------------------- ОСНОВНОЙ СЦЕНАРИЙ ----------------------------


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    target_pages = []

    for root_url in TARGET_ROOTS:
        prefix = urlparse(root_url).path
        if not prefix.endswith("/"):
            prefix += "/"
        print(f"[{now()}] Обхожу раздел {prefix} (корень: {root_url}) ...", flush=True)
        pages = discover_section_pages(prefix, root_url)
        if not pages:
            # на случай сетевых сбоев — не терять хотя бы корневую страницу
            pages = {root_url}
        print(f"[{now()}] Раздел {prefix}: найдено страниц — {len(pages)}", flush=True)
        target_pages.extend(sorted(pages))

    # дедуп по каноническому ключу, сохраняя порядок
    seen_keys = set()
    unique_pages = []
    for url in target_pages:
        k = canonical_key(url)
        if k not in seen_keys:
            seen_keys.add(k)
            unique_pages.append(url)

    toc = []

    for i, url in enumerate(unique_pages, 1):
        print(f"[{now()}] [{i}/{len(unique_pages)}] Разбираю {url}", flush=True)
        resp = fetch(url)
        time.sleep(REQUEST_DELAY)
        if resp is None:
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        title_tag = soup.find("h1") or soup.find("title")
        page_title = title_tag.get_text(strip=True) if title_tag else url

        blocks = extract_blocks(soup, url)
        headings = [b["text"] for b in blocks if b["type"] == "heading"]
        file_count = sum(1 for b in blocks if b["type"] == "file")

        slug = slugify(url)
        page_data = {
            "url": url,
            "title": page_title,
            "blocks": blocks,
        }

        (OUTPUT_DIR / f"{slug}.json").write_text(
            json.dumps(page_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (OUTPUT_DIR / f"{slug}.md").write_text(
            blocks_to_markdown(page_title, url, blocks), encoding="utf-8"
        )

        toc.append({
            "url": url,
            "title": page_title,
            "slug": slug,
            "headings": headings,
            "file_count": file_count,
        })

    (OUTPUT_DIR / "_toc.json").write_text(
        json.dumps(toc, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n[{now()}] Готово. Разобрано страниц: {len(toc)}")
    print(f"Результат в папке: {OUTPUT_DIR.resolve()}")
    print("Смотрите _toc.json для быстрого обзора всех страниц и подзаголовков.")


if __name__ == "__main__":
    main()