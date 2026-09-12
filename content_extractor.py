"""Разбор HTML-страницы в список блоков (заголовки, абзацы, списки, таблицы,
файлы, подстраницы) — то, что в итоге попадает в JSON и Markdown."""

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

import config
import links


def get_content_root(soup: BeautifulSoup) -> Tag:
    for sel in config.CONTENT_SELECTOR_CANDIDATES:
        node = soup.select_one(sel)
        if node is not None:
            return node
    return soup


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
                    email = links.resolve_email(key)
                    return email if email else f"[НЕИЗВЕСТНАЯ ПОЧТА: {key}]"

            if src:
                if plain_links:
                    return title or alt or "Изображение"
                full_src = urljoin(page_url, src)
                resolved_src = links.resolve_url(full_src)
                alt_text = title or alt or "Изображение"
                return f"\n![{alt_text}]({resolved_src})\n"
            return ""

        if node.name == "br":
            return "\n"
        if node.name in ("p", "div"):
            inner = "".join(collect(c) for c in node.children).strip()
            return f"\n{inner}\n" if inner else ""

        if node.name in config.BOLD_TAGS:
            inner = "".join(collect(c) for c in node.children).strip()
            return f"**{inner}**" if inner else ""
        if node.name in config.ITALIC_TAGS:
            inner = "".join(collect(c) for c in node.children).strip()
            return f"*{inner}*" if inner else ""

        if node.name == "a" and node.get("href"):
            href = node["href"].strip()
            inner = "".join(collect(c) for c in node.children).strip()
            if not href or href.startswith("javascript:"):
                return inner
            if plain_links:
                return inner
            full_url = urljoin(page_url, href) if page_url else href
            resolved_url = links.resolve_url(full_url)
            return f"[{inner}]({resolved_url})" if inner else ""

        return "".join(collect(c) for c in node.children)

    if tag.name == "img":
        return collect(tag).strip()

    text = "".join(collect(c) for c in tag.children)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n", text).strip()
    return text


def build_file_block(container: Tag, anchor: Tag, ext: str, full_url: str, page_url: str) -> dict:
    """Достаёт из блока-обёртки вокруг ссылки на файл: название, размер/дату
    (meta) и саму ссылку, отделяя технические хвосты вроде '(pdf, 1.2 МБ)'."""
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
        link_text = anchor.get_text(strip=True) or links.clean_filename(full_url)

    return {
        "type": "file",
        "link_text": link_text,
        "meta": meta,
        "file_name": links.clean_filename(full_url),
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


def extract_blocks(soup: BeautifulSoup, page_url: str) -> list:
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

    def tag_id(tag):
        return id(tag)

    childdocs = root.select_one("div.childdocs")
    if childdocs is not None:
        child_pages = []
        for a in childdocs.find_all("a", href=True):
            seen_tags.add(tag_id(a))
            full_url = urljoin(page_url, a["href"].strip())
            target_url = links.resolve_url(full_url)
            child_pages.append({"title": a.get_text(strip=True), "url": target_url})
        for li in childdocs.find_all("li"):
            seen_tags.add(tag_id(li))
        for ul in childdocs.find_all("ul"):
            seen_tags.add(tag_id(ul))
        if child_pages:
            blocks.append({"type": "child_pages", "items": child_pages})

    def has_file_link(container: Tag):
        found = []
        for a in container.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            full_url = urljoin(page_url, href)
            ext = links.get_extension(full_url)
            if ext in config.FILE_EXTENSIONS:
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

        if node.name in config.HEADING_TAGS:
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

        # div-контейнер с блочными детьми — сам по себе не блок, спускаемся внутрь
        if node.name == "div" and node.find(["p", "div", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6"]):
            continue

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
                        "link_text": a.get_text(strip=True) or links.clean_filename(full_url),
                        "meta": "",
                        "file_name": links.clean_filename(full_url),
                        "extension": ext.lstrip("."),
                        "file_url": full_url,
                    })
                mark_seen_recursive(node)
            else:
                text = render_inline_text(node, page_url)
                if text:
                    blocks.append({"type": "paragraph", "text": text})
                mark_seen_recursive(node)
            continue

    return blocks
