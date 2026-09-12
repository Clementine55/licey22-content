#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Обходит старый сайт лицея и сохраняет содержимое каждой страницы в
pages_content/ (JSON + Markdown на страницу) и в pages_content/_toc.json
(оглавление). Устроено так, чтобы можно было гонять его хоть каждый день
из крона: страницы разбираются заново всегда, а на диск перезаписываются
только те, что реально изменились — см. state.py.

Запуск:
    python3 page_content_parser.py

Подробности о том, как это всё устроено вместе с publish_to_github.py —
в README.md.
"""

import json
import time
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

import config
import http_client
import links
import state
from content_extractor import extract_blocks
from crawler import discover_section_pages
from http_client import fetch
from markdown_export import blocks_to_markdown
from utils import now


def discover_all_pages() -> list:
    target_pages = []
    for root_url in config.TARGET_ROOTS:
        prefix = urlparse(root_url).path
        if not prefix.endswith("/"):
            prefix += "/"
        print(f"[{now()}] Обхожу раздел {prefix} (корень: {root_url}) ...", flush=True)
        pages = discover_section_pages(prefix, root_url) or {root_url}
        print(f"[{now()}] Раздел {prefix}: найдено страниц — {len(pages)}", flush=True)
        target_pages.extend(sorted(pages))

    seen_keys = set()
    unique_pages = []
    for url in target_pages:
        key = links.canonical_key(url)
        if key not in seen_keys:
            seen_keys.add(key)
            unique_pages.append(url)
    return unique_pages


def parse_page(url: str) -> Optional[dict]:
    html = http_client.get_cached_html(url)
    if html is None:
        # Не должно случаться в норме — discover_all_pages() уже скачал эту
        # страницу. На всякий случай подстраховываемся отдельным запросом.
        resp = fetch(url)
        time.sleep(config.REQUEST_DELAY)
        if resp is None:
            return None
        html = resp.text

    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    page_title = title_tag.get_text(strip=True) if title_tag else url
    blocks = extract_blocks(soup, url)

    return {"url": url, "title": page_title, "blocks": blocks}


def remove_orphaned_files(expected_slugs: set) -> int:
    """Удаляет .json/.md страниц, которых больше нет среди текущих
    unique_pages (раздел убрали/переименовали на старом сайте)."""
    removed = 0
    for path in config.OUTPUT_DIR.glob("*.json"):
        if path == config.TOC_FILE:
            continue
        if path.stem not in expected_slugs:
            path.unlink()
            removed += 1
    for path in config.OUTPUT_DIR.glob("*.md"):
        if path.stem not in expected_slugs:
            path.unlink()
            removed += 1
    return removed


def main():
    config.OUTPUT_DIR.mkdir(exist_ok=True)

    unique_pages = discover_all_pages()

    toc = []
    skipped = 0

    for i, url in enumerate(unique_pages, 1):
        print(f"[{now()}] [{i}/{len(unique_pages)}] Разбираю {url}", flush=True)
        page_data = parse_page(url)
        if page_data is None:
            continue

        slug = links.slugify(url)
        headings = [b["text"] for b in page_data["blocks"] if b["type"] == "heading"]
        file_count = sum(1 for b in page_data["blocks"] if b["type"] == "file")

        toc.append({
            "url": url,
            "title": page_data["title"],
            "slug": slug,
            "headings": headings,
            "file_count": file_count,
        })

        if not state.has_changed(url, page_data):
            print(f"[{now()}] [{i}/{len(unique_pages)}] без изменений, файл не перезаписываю: {url}", flush=True)
            skipped += 1
            continue

        (config.OUTPUT_DIR / f"{slug}.json").write_text(
            json.dumps(page_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (config.OUTPUT_DIR / f"{slug}.md").write_text(
            blocks_to_markdown(page_data["title"], url, page_data["blocks"]), encoding="utf-8"
        )
        state.record(url, page_data)

    state.prune(set(unique_pages))
    removed = remove_orphaned_files({links.slugify(u) for u in unique_pages})

    config.TOC_FILE.write_text(json.dumps(toc, ensure_ascii=False, indent=2), encoding="utf-8")
    state.save()
    links.save_maps()

    print(f"\n[{now()}] Готово. Разобрано страниц: {len(toc)} "
          f"(без изменений пропущено: {skipped}, удалено устаревших файлов: {removed})")
    print(f"Результат в папке: {config.OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
