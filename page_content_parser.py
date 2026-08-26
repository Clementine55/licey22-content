#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
TARGET_ROOTS = [
    "https://архив.лицей22.рф/sveden/",
    "https://архив.лицей22.рф/6184/17478/",
    "https://архив.лицей22.рф/6184/20427/",
    "https://архив.лицей22.рф/5933/",
    "https://архив.лицей22.рф/16451/",
]

ALLOWED_PAGE_DOMAINS = {
    "архив.лицей22.рф", 
    "xn--80a1acny.xn--22-mlclgj2f.xn--p1ai", 
    "лицей22.рф", 
    "xn--22-mlclgj2f.xn--p1ai"
}
CANONICAL_HOST = "архив.лицей22.рф"

BLOCKED_URL_SUBSTRINGS = ["/news/", "printmode=yes", "/_data/"]
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

# Загружаем/создаем карту почт
EMAILS_MAP_FILE = Path("emails_map.json")
if EMAILS_MAP_FILE.exists():
    EMAIL_MAP = json.loads(EMAILS_MAP_FILE.read_text(encoding="utf-8"))
else:
    EMAIL_MAP = {}

# Загружаем/создаем карту ссылок (АВТОМАТИЗАЦИЯ!)
LINK_MAP_FILE = Path("link_map.json")
if LINK_MAP_FILE.exists():
    CUSTOM_LINKS = json.loads(LINK_MAP_FILE.read_text(encoding="utf-8"))
else:
    CUSTOM_LINKS = {}

_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(max_retries=0)
_session.mount("http://", _adapter)
_session.mount("https://", _adapter)


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")

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

# --- ЛОГИКА ГЕНЕРАЦИИ ИДЕАЛЬНЫХ ССЫЛОК ---
def get_beautiful_path(url: str) -> str:
    """Генерирует идеальный путь для Тильды из старой ссылки."""
    path = urlparse(url).path
    path = re.sub(r"\.(html|htm|php)$", "", path, flags=re.IGNORECASE)
    
    # Наши правила идеальных ссылок (можно дополнять)
    path = path.replace("/sveden/employees/programs/", "/employees/")
    path = path.replace("/sveden/education/", "/education/")
    path = path.replace("/sveden/", "/")
    
    # Убираем двойные слэши и хвосты
    path = re.sub(r"/+", "/", path).rstrip("/")
    return path or "/index"

def resolve_url(full_url: str) -> str:
    """Умная подмена ссылки с использованием link_map.json"""
    parsed = urlparse(full_url)
    # Приводим к архивному хосту, если ссылка жестко зашита на старый домен
    if parsed.hostname in ("лицей22.рф", "xn--22-mlclgj2f.xn--p1ai"):
        full_url = full_url.replace(parsed.hostname, CANONICAL_HOST)
        parsed = urlparse(full_url)
        
    if not is_allowed_page(full_url) or get_extension(full_url) in FILE_EXTENSIONS:
        return full_url

    # Ключ для словаря — ссылка без якоря и без слэша на конце
    base_url_key = full_url.split('#')[0].rstrip('/')
    
    # Если ссылки еще нет в нашем словаре — генерируем её и сохраняем!
    if base_url_key not in CUSTOM_LINKS:
        beautiful_path = get_beautiful_path(base_url_key)
        CUSTOM_LINKS[base_url_key] = beautiful_path
        LINK_MAP_FILE.write_text(json.dumps(CUSTOM_LINKS, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [+] Добавлена новая красивая ссылка в link_map.json: {beautiful_path}")
        
    target_path = CUSTOM_LINKS[base_url_key]
    
    if parsed.fragment:
        return f"{target_path}#{parsed.fragment}"
    return target_path

def slugify(url: str) -> str:
    """Имя JSON файла теперь генерируется строго из красивой ссылки."""
    target_path = resolve_url(url).split('#')[0] 
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", target_path.strip("/"))
    return slug or "index"
# ----------------------------------------------------

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
                print(f"  [{now()}] [!] попытка {attempt} не удалась ({e}) — пробую ещё раз: {url}", file=sys.stderr, flush=True)
                continue
            elapsed = time.monotonic() - start
            print(f"  [{now()}] [!] не удалось загрузить за {elapsed:.1f}с: {url}: {e}", file=sys.stderr, flush=True)
            return None

def get_content_root(soup: BeautifulSoup) -> Tag:
    for sel in CONTENT_SELECTOR_CANDIDATES:
        node = soup.select_one(sel)
        if node is not None:
            return node
    return soup

def discover_section_pages(prefix: str, start_url: str):
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
            
            parsed_url = urlparse(full_url)
            if parsed_url.hostname in ("лицей22.рф", "xn--22-mlclgj2f.xn--p1ai"):
                full_url = full_url.replace(parsed_url.hostname, CANONICAL_HOST)

            if is_blocked(full_url) or not is_allowed_page(full_url):
                continue
            if get_extension(full_url) in FILE_EXTENSIONS:  
                continue
            
            full_path = urlparse(full_url).path
            if full_path.startswith(prefix) and canonical_key(full_url) not in visited_keys:
                queue.append(full_url)

    return found


BOLD_TAGS = {"b", "strong"}
ITALIC_TAGS = {"i", "em"}

def render_inline_text(tag: Tag, page_url: str = "", plain_links: bool = False) -> str:
    def is_hidden(node: Tag) -> bool:
        style = (node.get("style") or "").replace(" ", "").lower()
        return "display:none" in style

    def collect(node) -> str:
        if isinstance(node, NavigableString):
            return str(node)
        if not isinstance(node, Tag):
            return ""
            
        if node.get_text(strip=True).lower() in ["не указан", "не указаны"]:
            return ""

        if is_hidden(node):
            return ""

        if node.name == "img":
            title = node.get("title", "").strip()
            alt = node.get("alt", "").strip()
            if title or alt:
                return title or alt

            src = node.get("src", "")
            if "email" in src.lower() or "mail" in src.lower():
                query = urlparse(src).query
                if query:
                    key = "?" + query
                    if key in EMAIL_MAP and EMAIL_MAP[key]:
                        email = EMAIL_MAP[key]
                        if "@" in email and not email.startswith("mailto:"):
                            email = "mailto:" + email
                        return email
                    else:
                        if key not in EMAIL_MAP:
                            EMAIL_MAP[key] = ""
                            EMAILS_MAP_FILE.write_text(json.dumps(EMAIL_MAP, ensure_ascii=False, indent=2), encoding="utf-8")
                            print(f"\n[ВНИМАНИЕ] Найдена новая зашифрованная почта: {key}")
                        return f"[НЕИЗВЕСТНАЯ ПОЧТА: {key}]"
            return ""

        if node.name == "br":
            return "\n"
        if node.name in ("p", "div"):
            inner = "".join(collect(c) for c in node.children).strip()
            return f"\n{inner}\n" if inner else ""
            
        if node.name in BOLD_TAGS:
            inner = "".join(collect(c) for c in node.children).strip()
            return f"**{inner}**" if inner else ""
        if node.name in ITALIC_TAGS:
            inner = "".join(collect(c) for c in node.children).strip()
            return f"*{inner}*" if inner else ""
            
        if node.name == "a" and node.get("href"):
            href = node["href"].strip()
            inner = "".join(collect(c) for c in node.children).strip()
            if not href or href.startswith(("javascript:")):
                return inner
            if plain_links:
                return inner
                
            full_url = urljoin(page_url, href) if page_url else href
            
            # --- ИСПОЛЬЗУЕМ УМНУЮ МАРШРУТИЗАЦИЮ ИЗ link_map.json ---
            resolved_url = resolve_url(full_url)
                    
            return f"[{inner}]({resolved_url})" if inner else ""
            
        return "".join(collect(c) for c in node.children)

    text = "".join(collect(c) for c in tag.children)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n", text).strip()
    return text


def extract_table(table: Tag, page_url: str) -> dict:
    headers = []
    rows = []
    trs = table.find_all("tr")
    if not trs:
        return {"type": "table", "headers": [], "rows": []}
        
    first_tr = trs[0]
    first_cells = first_tr.find_all(["th", "td"], recursive=False)
    is_first_row_th = any(c.name == "th" for c in first_cells)
    
    start_idx = 0
    if is_first_row_th or table.find("thead"):
        headers = [render_inline_text(c, page_url) for c in first_cells]
        start_idx = 1
        
    for tr in trs[start_idx:]:
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
    root = get_content_root(soup)
    blocks = []
    seen_tags = set()

    def tag_id(tag): return id(tag)

    childdocs = root.select_one("div.childdocs")
    if childdocs is not None:
        child_pages = []
        for a in childdocs.find_all("a", href=True):
            seen_tags.add(tag_id(a))
            full_url = urljoin(page_url, a["href"].strip())
            
            # --- ИСПОЛЬЗУЕМ УМНУЮ МАРШРУТИЗАЦИЮ ИЗ link_map.json ---
            target_url = resolve_url(full_url)
                
            child_pages.append({"title": a.get_text(strip=True), "url": target_url})
            
        for li in childdocs.find_all("li"): seen_tags.add(tag_id(li))
        for ul in childdocs.find_all("ul"): seen_tags.add(tag_id(ul))
        if child_pages:
            blocks.append({"type": "child_pages", "items": child_pages})

    def has_file_link(container: Tag):
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
            items = []
            for li in node.find_all("li", recursive=False):
                file_links = has_file_link(li)
                if len(file_links) == 1:
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
                    if t: items.append(t)
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
            continue

        if node.name in ("p", "div"):
            file_links = has_file_link(node)
            if len(file_links) == 1:
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
                text = render_inline_text(node, page_url)
                if text:
                    blocks.append({"type": "paragraph", "text": text})
                mark_seen_recursive(node)
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
            pages = {root_url}
        print(f"[{now()}] Раздел {prefix}: найдено страниц — {len(pages)}", flush=True)
        target_pages.extend(sorted(pages))

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

        (OUTPUT_DIR / f"{slug}.json").write_text(json.dumps(page_data, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUTPUT_DIR / f"{slug}.md").write_text(blocks_to_markdown(page_title, url, blocks), encoding="utf-8")

        toc.append({
            "url": url,
            "title": page_title,
            "slug": slug,
            "headings": headings,
            "file_count": file_count,
        })

    (OUTPUT_DIR / "_toc.json").write_text(json.dumps(toc, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[{now()}] Готово. Разобрано страниц: {len(toc)}")
    print(f"Результат в папке: {OUTPUT_DIR.resolve()}")

if __name__ == "__main__":
    main()