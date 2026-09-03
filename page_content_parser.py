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

TARGET_ROOTS = [
    "https://s3454.nubex.ru/sveden/",
    "https://s3454.nubex.ru/6184/",
    "https://s3454.nubex.ru/5933/",
    "https://s3454.nubex.ru/16451/",
]

ALLOWED_PAGE_DOMAINS = {
    "s3454.nubex.ru",
    "архив.лицей22.рф", 
    "xn--80a1acny.xn--22-mlclgj2f.xn--p1ai", 
    "лицей22.рф", 
    "xn--22-mlclgj2f.xn--p1ai"
}
CANONICAL_HOST = "s3454.nubex.ru"

BLOCKED_URL_SUBSTRINGS = ["/news/", "printmode=yes", "/_data/"]
CONTENT_SELECTOR_CANDIDATES = ["div.siteContent", "div.content", "body"]

HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
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

OUTPUT_DIR = Path("pages_content")

EMAILS_MAP_FILE = Path("emails_map.json")
if EMAILS_MAP_FILE.exists():
    EMAIL_MAP = json.loads(EMAILS_MAP_FILE.read_text(encoding="utf-8"))
else:
    EMAIL_MAP = {}

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
    parsed = urlparse(full_url)
    if parsed.hostname in ("лицей22.рф", "xn--22-mlclgj2f.xn--p1ai", "архив.лицей22.рф"):
        full_url = full_url.replace(parsed.hostname, CANONICAL_HOST)
        parsed = urlparse(full_url)
        
    if not is_allowed_page(full_url) or get_extension(full_url) in FILE_EXTENSIONS:
        return full_url

    base_url_key = full_url.split('#')[0].rstrip('/')
    
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
    target_path = resolve_url(url).split('#')[0] 
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", target_path.strip("/"))
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
            if parsed_url.hostname in ("лицей22.рф", "xn--22-mlclgj2f.xn--p1ai", "архив.лицей22.рф"):
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

        if is_hidden(node):
            return ""

        if node.name == "img":
            title = node.get("title", "").strip()
            alt = node.get("alt", "").strip()
            src = node.get("src", "").strip()

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
                        return f"[НЕИЗВЕСТНАЯ ПОЧТА: {key}]"

            # --- ЕСЛИ ЭТО ОБЫЧНАЯ КАРТИНКА ---
            if src:
                # Если нам нужен голый текст (например, для названия файла), картинки скипаем
                if plain_links:
                    return title or alt or "Изображение"
                
                full_src = urljoin(page_url, src)
                resolved_src = resolve_url(full_src)
                alt_text = title or alt or "Изображение"
                # Возвращаем красивую Markdown разметку для картинки
                return f"\n![{alt_text}]({resolved_src})\n"
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
            resolved_url = resolve_url(full_url)
            return f"[{inner}]({resolved_url})" if inner else ""
            
        return "".join(collect(c) for c in node.children)

    # Если мы передали саму картинку как корень
    if tag.name == "img":
        return collect(tag).strip()

    text = "".join(collect(c) for c in tag.children)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n", text).strip()
    return text


def build_file_block(container: Tag, anchor: Tag, ext: str, full_url: str, page_url: str) -> dict:
    link_text = render_inline_text(anchor, page_url, plain_links=True)
    full_text = render_inline_text(container, page_url, plain_links=True)

    meta = full_text
    if link_text and link_text in meta:
        meta = meta.replace(link_text, "", 1)

    size_pattern = re.compile(r'([\(,\s]*\d+[\.,]?\d*\s*(?:КБ|МБ|Б|KB|MB|B)[^\)]*\)?)$', re.IGNORECASE)
    match_inside_link = size_pattern.search(link_text)
    
    if match_inside_link:
        extracted_meta = match_inside_link.group(1).strip()
        link_text = link_text[:match_inside_link.start()].strip()
        meta = extracted_meta + (" " + meta if meta else "")
    else:
        fallback_match = re.search(r'(\([^\)]+\))$', link_text)
        if fallback_match and fallback_match.start() > 0:
            extracted_meta = fallback_match.group(1).strip()
            link_text = link_text[:fallback_match.start()].strip()
            meta = extracted_meta + (" " + meta if meta else "")

    if ext:
        ext_regex = re.compile(rf'\.{ext.lstrip(".")}(?=[\s,\)]|$)', re.IGNORECASE)
        link_text = ext_regex.sub('', link_text).strip()
        meta = ext_regex.sub('', meta).strip()

    meta = re.sub(r"\s+", " ", meta).strip()
    if meta:
        meta = re.sub(r'\)\s*\(', ', ', meta) 
        meta = re.sub(r'[()]', '', meta)       
        meta = re.sub(r'^[,.\s]+', '', meta).strip() 
        meta = re.sub(r'[,.\s]+$', '', meta).strip() 

    link_text = re.sub(r'[,.\s\(\)]+$', '', link_text).strip()

    if not link_text:
        link_text = anchor.get_text(strip=True) or clean_filename(full_url)

    return {
        "type": "file",
        "link_text": link_text,
        "meta": meta,
        "file_name": clean_filename(full_url),
        "extension": ext.lstrip("."),
        "file_url": full_url,
    }

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
    
    for unwanted in root.find_all(class_=["path", "hidden"]):
        unwanted.decompose()
        
    for unwanted in root.find_all(style=lambda s: s and "display:none" in s.replace(" ", "").lower()):
        unwanted.decompose()
        
    for a in root.find_all("a", href=True):
        if "printmode=yes" in a["href"].lower() or "версия для печати" in a.get_text(strip=True).lower():
            a.decompose()

    blocks = []
    seen_tags = set()

    def tag_id(tag): return id(tag)

    childdocs = root.select_one("div.childdocs")
    if childdocs is not None:
        child_pages = []
        for a in childdocs.find_all("a", href=True):
            seen_tags.add(tag_id(a))
            full_url = urljoin(page_url, a["href"].strip())
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
                    blocks.append(build_file_block(li, a, ext, full_url, page_url))
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

        # --- ДОБАВИЛИ ЗАГОЛОВКИ В ПРОПУСК! ТЕПЕРЬ ОНИ НЕ БУДУТ ТЕКСТОМ ---
        if node.name == "div" and node.find(["p", "div", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6"]):
            continue

        # --- ТЕПЕРЬ ПАРСЕР ВИДИТ И КАРТИНКИ БЕЗ ТЕКСТА ---
        if node.name in ("p", "div", "img", "figure"):
            file_links = has_file_link(node)
            if len(file_links) == 1:
                a, ext, full_url = file_links[0]
                blocks.append(build_file_block(node, a, ext, full_url, page_url))
                mark_seen_recursive(node)
            elif len(file_links) > 1:
                for a, ext, full_url in file_links:
                    blocks.append({
                        "type": "file",
                        "link_text": a.get_text(strip=True) or clean_filename(full_url),
                        "meta": "",
                        "file_name": clean_filename(full_url),
                        "extension": ext.lstrip("."),
                        "file_url": full_url,
                    })
                mark_seen_recursive(node)
            elif node.name in ("p", "div", "img", "figure"):
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
            meta_str = f" — _{b['meta']}_" if b.get('meta') else ""
            lines.append(f"📎 [{b['link_text']}]({b['file_url']}) ({b['extension']}){meta_str}")
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