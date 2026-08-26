#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Применяет словарь замен ссылок (link_map.json) ко всем страницам в
pages_content/ — единый механизм для двух ваших задач:

    1. Обфусцированные email-ссылки старого сайта (?p=...) — заменяются
       на настоящий mailto: с реальным адресом.
    2. Внутренние ссылки на "скрытые" страницы старого сайта (те, которые
       вы тоже спарсили и пересоздали на новом сайте) — заменяются на
       ссылку на соответствующую новую страницу на Тильде.

Правило простое: ключ в link_map.json — то, что ищем, значение — на что
меняем. Если для ссылки нет ключа в словаре ИЛИ значение пустое ("") —
ссылка остаётся как была (без изменений). Так можно держать в словаре
"заготовки" для страниц, которые ещё не пересозданы на Тильде — пустое
значение = "ещё не готово, не трогай".

Формат link_map.json:
    {
      "https://лицей22.рф/sveden/employees/programs/p4885/": "https://новый.лицей22.рф/programs-p4885",
      "https://лицей22.рф/sveden/employees/programs/p5359/": "",
      "?p=JydZAHlWVBZbWlhDTA==": "l_22@edu54.ru"
    }

Для email-адресов (без http:// и без mailto:) скрипт сам добавляет
mailto: — то есть emails_map.json, который у вас уже был, можно просто
скопировать содержимое в link_map.json как есть, ничего не переписывая.

Куда смотрит скрипт:
    - heading.text, paragraph.text, list.items — ищутся markdown-ссылки
      вида [текст](url), меняется только url внутри скобок
    - table: rows[].cells[] и rows[].text (для span_all-строк) — то же самое
    - child_pages: items[].url — меняется напрямую (там url лежит отдельным
      полем, не в markdown-разметке)
    - "file"-блоки (file_url) — НЕ трогаются: это ссылки на настоящие
      документы для скачивания, а не на страницы сайта, подменять их
      словарём страниц смысла нет

Запуск (из папки с pages_content/ и link_map.json):
    python3 apply_link_map.py

Обычно запускается автоматически из publish_to_github.py, между
page_content_parser.py и combine_library.py — руками отдельно гонять не
обязательно.
"""

import json
import re
from pathlib import Path

PAGES_DIR = Path("pages_content")
MAP_PATH = Path("link_map.json")

LINK_RE = re.compile(r"\]\(([^)]+)\)")


def load_link_map() -> dict:
    if not MAP_PATH.exists():
        return {}
    raw = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    # пустые значения = "не менять" — выкидываем сразу, чтобы не проверять их каждый раз
    return {k: v for k, v in raw.items() if v}


def _normalize_value(value: str) -> str:
    """Email без схемы автоматически превращается в mailto: — так старый
    emails_map.json (значения вида "l_22@edu54.ru") работает без переделки."""
    if "@" in value and "://" not in value and not value.startswith("mailto:"):
        return "mailto:" + value
    return value


def remap_url(url: str, link_map: dict) -> str:
    # точное совпадение (с учётом слэша на конце — на старом сайте он не всегда стабилен)
    if url in link_map:
        return _normalize_value(link_map[url])
    stripped = url.rstrip("/")
    for key, value in link_map.items():
        if key.rstrip("/") == stripped:
            return _normalize_value(value)
    # частичное совпадение — нужно для обфусцированных email-ссылок вида
    # "?p=..." (в тексте они встречаются как часть полного URL с доменом)
    for key, value in link_map.items():
        if key in url:
            return _normalize_value(value)
    return url


def remap_text(text: str, link_map: dict) -> str:
    def repl(m):
        return f"]({remap_url(m.group(1), link_map)})"
    return LINK_RE.sub(repl, text)


def remap_block(block: dict, link_map: dict) -> dict:
    t = block.get("type")
    if t in ("heading", "paragraph"):
        block["text"] = remap_text(block["text"], link_map)
    elif t == "list":
        block["items"] = [remap_text(i, link_map) for i in block.get("items", [])]
    elif t == "table":
        for row in block.get("rows", []):
            if row.get("span_all"):
                row["text"] = remap_text(row["text"], link_map)
            else:
                row["cells"] = [remap_text(c, link_map) for c in row.get("cells", [])]
    elif t == "child_pages":
        for item in block.get("items", []):
            item["url"] = remap_url(item["url"], link_map)
    # "file" — сознательно не трогаем, см. описание в шапке файла
    return block


def main():
    link_map = load_link_map()
    if not link_map:
        print("link_map.json пуст, не найден, или все значения в нём пустые — менять нечего.")
        return
    if not PAGES_DIR.exists():
        print(f"Не нашёл папку {PAGES_DIR.resolve()} — запустите рядом с ней.")
        return

    changed_files = 0
    changed_links = 0
    for jf in sorted(PAGES_DIR.glob("*.json")):
        if jf.stem == "_toc":
            continue
        data = json.loads(jf.read_text(encoding="utf-8"))
        before = json.dumps(data, ensure_ascii=False)
        data["blocks"] = [remap_block(b, link_map) for b in data.get("blocks", [])]
        after = json.dumps(data, ensure_ascii=False)
        if before != after:
            jf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            changed_files += 1

    print(f"Готово. Правки внесены в {changed_files} файл(ов) страниц.")


if __name__ == "__main__":
    main()
