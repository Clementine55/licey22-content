#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from pathlib import Path

PAGES_DIR = Path("pages_content")
OUT_JSON = Path("library.json")
OUT_MD = Path("library.md")


def blocks_to_markdown(page: dict) -> str:
    lines = [f"# {page.get('title', page['url'])}", f"_{page['url']}_", f"_(файл: {page['slug']}.json)_", ""]
    for b in page.get("blocks", []):
        if b["type"] == "heading":
            hashes = "#" * min(int(b["level"][1]) + 1, 6)
            lines.append(f"{hashes} {b['text']}")
        elif b["type"] == "paragraph":
            lines.append(b["text"])
        elif b["type"] == "list":
            for item in b["items"]:
                lines.append(f"- {item}")
        elif b["type"] == "file":
            meta = f" — _{b['meta']}_" if b.get("meta") else ""
            lines.append(f"📎 [{b['link_text']}]({b['file_url']}) ({b['extension']}){meta}")
        elif b["type"] == "child_pages":
            lines.append("**Подстраницы раздела:**")
            for item in b["items"]:
                lines.append(f"- [{item['title']}]({item['url']})")
        lines.append("")
    return "\n".join(lines)


def main():
    if not PAGES_DIR.exists():
        print(f"Не нашёл папку {PAGES_DIR.resolve()} — запустите скрипт рядом с ней.")
        return

    toc_path = PAGES_DIR / "_toc.json"
    order = []
    if toc_path.exists():
        toc = json.loads(toc_path.read_text(encoding="utf-8"))
        order = [item["slug"] for item in toc]

    json_files = sorted(PAGES_DIR.glob("*.json"))
    by_slug = {}
    for jf in json_files:
        if jf.stem == "_toc":
            continue
        data = json.loads(jf.read_text(encoding="utf-8"))
        by_slug[jf.stem] = data

    # порядок: сначала как в _toc.json (порядок обхода), потом всё остальное на всякий случай
    slugs_in_order = [s for s in order if s in by_slug]
    slugs_in_order += [s for s in by_slug if s not in slugs_in_order]

    library = []
    md_parts = []
    for slug in slugs_in_order:
        page = by_slug[slug]
        page["slug"] = slug
        library.append(page)
        md_parts.append(blocks_to_markdown(page))

    OUT_JSON.write_text(json.dumps(library, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_MD.write_text("\n\n---\n\n".join(md_parts), encoding="utf-8")

    print(f"Готово: {OUT_JSON.resolve()} ({len(library)} страниц)")
    print(f"Готово: {OUT_MD.resolve()} — можно открыть и читать/править всё разом")


if __name__ == "__main__":
    main()