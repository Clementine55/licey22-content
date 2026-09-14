#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
import time
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

import config
import http_client
import links
import state
import changelog
from content_extractor import extract_blocks
from crawler import discover_section_pages
from http_client import fetch
from markdown_export import blocks_to_markdown
from utils import now, load_json


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


def remove_orphaned_files(expected_slugs: set) -> list:
    """Удаляет .json/.md страниц, которых больше нет среди текущих
    unique_pages (раздел убрали/переименовали на старом сайте).
    Возвращает список slug'ов удалённых страниц (для журнала изменений)."""
    removed_slugs = []
    for path in config.OUTPUT_DIR.glob("*.json"):
        if path == config.TOC_FILE:
            continue
        if path.stem not in expected_slugs:
            path.unlink()
            removed_slugs.append(path.stem)
    for path in config.OUTPUT_DIR.glob("*.md"):
        if path.stem not in expected_slugs:
            path.unlink()
    return removed_slugs


def main():
    config.OUTPUT_DIR.mkdir(exist_ok=True)

    previously_known_urls = set(state.PAGE_STATE.keys())
    previously_known = len(previously_known_urls)
    previous_toc_by_slug = {
        entry["slug"]: entry for entry in load_json(config.TOC_FILE, [])
    }

    unique_pages = discover_all_pages()

    if not unique_pages:
        print(f"\n[{now()}] [!] Не нашёл ни одной страницы — похоже, сайт недоступен "
              f"(нет сети или сайт лёг). Ничего не удаляю и не перезаписываю, выхожу.",
              file=sys.stderr, flush=True)
        sys.exit(1)

    crawl_looks_healthy = (
        previously_known == 0
        or len(unique_pages) >= previously_known * config.MIN_HEALTHY_CRAWL_RATIO
    )
    if not crawl_looks_healthy:
        print(f"\n[{now()}] [!] Нашёл заметно меньше страниц, чем в прошлый раз "
              f"({len(unique_pages)} вместо {previously_known}) — похоже на частичный сбой "
              f"обхода, а не на реальное удаление страниц с сайта. Пропускаю чистку "
              f"устаревших файлов и перезапись _toc.json в этом прогоне; найденные "
              f"страницы всё равно проверю и обновлю как обычно.", file=sys.stderr, flush=True)

    toc = []
    skipped = 0
    added_log, updated_log = [], []

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

        md_path = config.OUTPUT_DIR / f"{slug}.md"
        new_md = blocks_to_markdown(page_data["title"], url, page_data["blocks"])

        entry = {"url": url, "title": page_data["title"], "slug": slug}
        if url not in previously_known_urls:
            added_log.append(entry)
        else:
            old_md = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
            added_lines, removed_lines = changelog.diff_snippet(old_md, new_md)
            updated_log.append({**entry, "added_lines": added_lines, "removed_lines": removed_lines})

        (config.OUTPUT_DIR / f"{slug}.json").write_text(
            json.dumps(page_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        md_path.write_text(new_md, encoding="utf-8")
        state.record(url, page_data)

    removed_log = []
    if crawl_looks_healthy:
        state.prune(set(unique_pages))
        removed_slugs = remove_orphaned_files({links.slugify(u) for u in unique_pages})
        removed_log = [
            previous_toc_by_slug.get(slug, {"slug": slug, "title": slug, "url": ""})
            for slug in removed_slugs
        ]
        config.TOC_FILE.write_text(json.dumps(toc, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(f"[{now()}] _toc.json и чистка устаревших файлов пропущены (см. предупреждение выше).", flush=True)

    state.save()
    links.save_maps()
    changelog.record_run(
        added=added_log, updated=updated_log, removed=removed_log,
        healthy=crawl_looks_healthy, pages_found=len(unique_pages),
        pages_known_before=previously_known,
    )

    print(f"\n[{now()}] Готово. Разобрано страниц: {len(toc)} "
          f"(без изменений пропущено: {skipped}, добавлено: {len(added_log)}, "
          f"обновлено: {len(updated_log)}, удалено устаревших файлов: {len(removed_log)})")
    print(f"Результат в папке: {config.OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
