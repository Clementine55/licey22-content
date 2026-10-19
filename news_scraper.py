#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрапер новостей старого сайта лицея
=====================================

Забирает новости со старого сайта (Nubex) и складывает в два JSON-файла,
которые затем читает блок на Тильде:

    news_index.json  — лёгкий список для страницы «Новости»
                       (id, заголовок, анонс, дата, миниатюра)
    news_full.json   — тексты и галереи, ключ = id новости
                       (грузится блоком только при открытии статьи)

Установка:
    pip install requests beautifulsoup4

Запуск:
    python3 news_scraper.py              # обычное обновление (быстро)
    python3 news_scraper.py --full       # полная перекачка всего архива

Как это работает:
    * Список берётся со страниц /news/archive/ , /news/archive/page_2.html ...
    * В обычном режиме скрипт читает страницы списка, пока на них попадаются
      новые новости, и скачивает только их (плюс перекачивает несколько
      самых свежих — на случай, если их отредактировали). Поэтому ежедневный
      запуск занимает несколько секунд.
    * --full нужен для первого запуска и для случая, когда на старом сайте
      что-то правили в давних новостях.
    * publish_to_github.py публикует news_data/*.json в GitHub. Готовый
      tilda_news_block.html читает их напрямую, без загрузки в Seafile.

В JSON сохраняется уже очищенный HTML: оставлены только безопасные теги,
убраны inline-стили, ссылки и картинки переписаны на абсолютные адреса
старого сайта.
"""

import argparse
import json
import os
import re
import sys
import time
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString
import config
from utils import save_json

# ----------------------------- НАСТРОЙКИ -----------------------------------

# Зеркало старого сайта. Картинки и документы остаются на его сервере,
# поэтому источник должен оставаться доступным.
BASE = "https://s3454.nubex.ru"

LIST_FIRST_PAGE = "/news/archive/"
LIST_PAGE_TEMPLATE = "/news/archive/page_{n}.html"

DELAY = 0.4              # пауза между запросами, сек
TIMEOUT = 20
RETRIES = 3
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Lyceum22NewsBot/1.0)"}

# iframe (видео и т.п.) оставляем только с этих хостов
IFRAME_HOSTS = {
    "vk.com", "vk.ru", "vkvideo.ru", "rutube.ru", "ok.ru",
    "youtube.com", "www.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com",
}

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

# ----------------------------- ОЧИСТКА HTML --------------------------------

def safe_url(value, schemes=("http", "https")):
    value = (value or "").strip()
    if not value or any(ord(c) < 32 for c in value):
        return ""
    result = absolute(value)
    parsed = urlparse(result)
    return result if parsed.scheme.lower() in schemes and not parsed.username and not parsed.password else ""

ALLOWED_TAGS = {
    "p", "br", "strong", "b", "em", "i", "u", "s", "span", "a", "img",
    "ul", "ol", "li", "h2", "h3", "h4", "h5", "h6", "blockquote",
    "table", "thead", "tbody", "tr", "td", "th", "div", "iframe",
    "figure", "figcaption", "sub", "sup", "hr",
}
DROP_TAGS = {
    "script", "style", "noscript", "form", "object", "embed", "link",
    "meta", "button", "input", "select", "textarea", "svg", "base",
}
ALLOWED_ATTRS = {
    "a": {"href"},
    "img": {"src", "alt"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "iframe": {"src", "allowfullscreen"},
}


def absolute(url: str) -> str:
    return urljoin(BASE + "/", url.strip())


def sanitize(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()

    for tag in soup.find_all(True):
        name = tag.name

        # Эмодзи, вставленные картинками (vk.ru/emoji/...), -> обычный символ
        if name == "img" and "/emoji/" in (tag.get("src") or "") and tag.get("alt"):
            tag.replace_with(NavigableString(tag["alt"]))
            continue

        if name not in ALLOWED_TAGS:
            tag.unwrap()
            continue

        allowed = ALLOWED_ATTRS.get(name, set())
        for attr in list(tag.attrs):
            if attr not in allowed:
                del tag.attrs[attr]

        if name == "a":
            href = (tag.get("href") or "").strip()
            cleaned = href if href.startswith("#") else safe_url(href, ("http", "https", "mailto", "tel"))
            if not cleaned:
                tag.unwrap()
                continue
            tag["href"] = cleaned

        elif name == "img":
            src = safe_url(tag.get("src"))
            if not src:
                tag.decompose()
                continue
            tag["src"] = src
            tag.attrs.setdefault("alt", "")

        elif name == "iframe":
            src = safe_url(tag.get("src"), ("https",))
            if not src or (urlparse(src).hostname or "").lower() not in IFRAME_HOSTS:
                tag.decompose()
                continue
            tag["src"] = src

    # Пустые абзацы и «висячие» <br> в начале/конце абзацев
    for p in soup.find_all("p"):
        while p.contents and (
            (isinstance(p.contents[0], NavigableString) and not p.contents[0].strip())
            or getattr(p.contents[0], "name", None) == "br"
        ):
            p.contents[0].extract()
        while p.contents and (
            (isinstance(p.contents[-1], NavigableString) and not p.contents[-1].strip())
            or getattr(p.contents[-1], "name", None) == "br"
        ):
            p.contents[-1].extract()
        if not p.get_text(strip=True) and not p.find(["img", "iframe"]):
            p.decompose()

    return str(soup).strip()


# ----------------------------- РАЗБОР СТРАНИЦ ------------------------------


def parse_date(text: str) -> str:
    """'7 октября 2026 г.' -> '2026-10-07' (или '' если не распознано)."""
    m = re.search(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", text or "", re.I)
    if not m:
        return ""
    month = MONTHS.get(m.group(2).lower())
    if not month:
        return ""
    return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(1)):02d}"


def parse_list(html: str) -> list:
    """Разбирает страницу списка новостей -> список карточек."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for node in soup.select(".newsList .fullitem"):
        link = node.select_one(".link-title a[href]")
        if not link:
            continue
        m = re.search(r"/news/(\d+)\.html", link["href"])
        if not m:
            continue

        notice = node.select_one(".notice")
        dt = node.select_one(".datetime")
        img = node.select_one(".image img[src]")

        items.append({
            "id": int(m.group(1)),
            "title": link.get_text(" ", strip=True),
            "announce": notice.get_text(" ", strip=True) if notice else "",
            "date": parse_date((dt.get("title") or dt.get_text()) if dt else ""),
            "thumb": safe_url(img["src"]) if img else "",
        })
    return items


def parse_detail(html: str):
    """Разбирает страницу новости -> {'title', 'body', 'gallery'} или None."""
    soup = BeautifulSoup(html, "html.parser")
    detail = soup.select_one(".newsDetail")
    if detail is None:
        return None

    h1 = soup.find("h1")
    body_parts, gallery = [], []

    for block in detail.select(".cb"):
        classes = block.get("class", [])
        if "cb-gallery" in classes:
            for a in block.select("a.item[href]"):
                img = a.find("img")
                full_url = safe_url(a["href"])
                if not full_url:
                    continue
                gallery.append({
                    "full": full_url,
                    "thumb": safe_url(img.get("src")) if img else full_url,
                })
        elif "cb-text" in classes:
            content = block.select_one(".content") or block
            cleaned = sanitize(content.decode_contents())
            if cleaned:
                body_parts.append(cleaned)
        else:
            # любые другие блоки (файлы, видео, одиночные картинки)
            cleaned = sanitize(block.decode_contents())
            if cleaned:
                body_parts.append(cleaned)

    return {
        "title": h1.get_text(" ", strip=True) if h1 else "",
        "body": "\n".join(body_parts),
        "gallery": gallery,
    }


# ----------------------------- СЕТЬ ----------------------------------------


def fetch(session: requests.Session, path: str):
    url = absolute(path)
    for attempt in range(1, RETRIES + 1):
        try:
            resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            print(f"  [!] {url}: {e} (попытка {attempt}/{RETRIES})", file=sys.stderr)
            time.sleep(2 * attempt)
    raise RuntimeError(f"Не удалось скачать {path}; прежние файлы новостей сохранены.")


# ----------------------------- ОСНОВНАЯ ЛОГИКА -----------------------------


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Не удалось прочитать {path}; обновление отменено.") from exc
    return default


def progress(stage, current=0, total=0):
    save_json(config.PROGRESS_FILE, {"stage": stage, "current": current, "total": total})


def cover_url(item, detail):
    """Обложка для карточки без скачивания полного архива в браузере."""
    for photo in detail.get("gallery", []):
        value = safe_url(photo.get("full"))
        if value:
            return value
    body = BeautifulSoup(detail.get("body", ""), "html.parser")
    for image in body.select("img[src]"):
        value = safe_url(image.get("src"))
        if value:
            return value
    return safe_url(item.get("thumb"))


def scrape(out_dir, *, full_refresh=False, refresh_last=10, max_pages=200):
    out_dir = Path(out_dir)
    index_path = out_dir / "news_index.json"
    full_path = out_dir / "news_full.json"
    previous_index = load_json(index_path, [])
    previous_full = load_json(full_path, {})
    if not isinstance(previous_index, list) or not isinstance(previous_full, dict):
        raise RuntimeError("Неверный формат сохранённых новостей; обновление отменено.")
    if full_refresh:
        index, full = {}, {}
    else:
        index = {str(i["id"]): i for i in previous_index}
        full = previous_full.copy()

    session = requests.Session()
    seen_in_run = []   # id в порядке обхода списка (от новых к старым)

    # --- 1. Список новостей --------------------------------------------------
    progress("news_discovering")
    for page in range(1, max_pages + 1):
        path = LIST_FIRST_PAGE if page == 1 else LIST_PAGE_TEMPLATE.format(n=page)
        print(f"Список, страница {page}: {absolute(path)}")
        html = fetch(session, path)
        time.sleep(DELAY)
        if html is None:
            if page == 1:
                raise RuntimeError("Архив новостей недоступен; прежние файлы сохранены.")
            break
        items = parse_list(html)
        if not items:
            if page == 1 or BeautifulSoup(html, "html.parser").select_one(".newsList") is None:
                raise RuntimeError("Не удалось разобрать архив новостей; прежние файлы сохранены.")
            break

        new_on_page = 0
        for item in items:
            key = str(item["id"])
            if key not in index:
                new_on_page += 1
            index[key] = item
            seen_in_run.append(item["id"])

        if not full_refresh and new_on_page == 0 and len(seen_in_run) >= refresh_last:
            break
    else:
        raise RuntimeError("Достигнут лимит страниц архива; неполные данные не сохранены.")

    # --- 2. Тексты новостей --------------------------------------------------
    refresh = set(seen_in_run[:max(0, refresh_last)])
    # Восстанавливаем и старые пропущенные статьи, даже если они не на первой странице.
    to_fetch = list(dict.fromkeys([i for i in seen_in_run if str(i) not in full or i in refresh]
                    + [item["id"] for key, item in index.items() if key not in full]))
    print(f"\nНовостей в индексе: {len(index)}; скачать текст: {len(to_fetch)}")

    for n, news_id in enumerate(to_fetch, 1):
        progress("news", n, len(to_fetch))
        print(f"[{n}/{len(to_fetch)}] /news/{news_id}.html")
        html = fetch(session, f"/news/{news_id}.html")
        time.sleep(DELAY)
        if html is None:
            # Явный 404: сохраняем имеющуюся копию, не публикуем новую пустую карточку.
            if str(news_id) not in full:
                index.pop(str(news_id), None)
            continue
        detail = parse_detail(html)
        if detail is None:
            raise RuntimeError(f"Не удалось разобрать новость {news_id}; прежние файлы сохранены.")
        full[str(news_id)] = {"body": detail["body"], "gallery": detail["gallery"]}
        if not index[str(news_id)].get("title") and detail["title"]:
            index[str(news_id)]["title"] = detail["title"]

    # --- 3. Сохранение -------------------------------------------------------
    index_list = sorted(index.values(), key=lambda x: (x.get("date", ""), x["id"]), reverse=True)
    if not index_list or (full_refresh and len(index_list) < len(previous_index) * config.MIN_HEALTHY_CRAWL_RATIO):
        raise RuntimeError("Неполный архив новостей; прежние файлы сохранены.")
    full = {str(item["id"]): full[str(item["id"])] for item in index_list}
    for item in index_list:
        item["cover"] = cover_url(item, full[str(item["id"])])
    out_dir.mkdir(parents=True, exist_ok=True)
    staged = []
    try:
        # Публикация пары файлов в GitHub происходит одним коммитом. На диске
        # каждый JSON заменяется целиком, после успешной загрузки всех статей.
        for path, data in ((full_path, full), (index_path, index_list)):
            serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            if path.exists() and path.read_text(encoding="utf-8") == serialized:
                continue
            fd, temp = tempfile.mkstemp(prefix=".news-", dir=out_dir)
            staged.append((temp, path))
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                fp.write(serialized)
                fp.flush()
                os.fsync(fp.fileno())
        for temp, path in staged:
            os.replace(temp, path)
    finally:
        for temp, _ in staged:
            if os.path.exists(temp):
                os.unlink(temp)

    print(f"\nГотово: {index_path} ({os.path.getsize(index_path) // 1024} КБ), "
          f"{full_path} ({os.path.getsize(full_path) // 1024} КБ)")
    return {"ok": True, "count": len(index_list), "latest_date": index_list[0].get("date", ""),
            "checked_at": datetime.now().isoformat(timespec="seconds")}


def main():
    ap = argparse.ArgumentParser(description="Скрапер новостей старого сайта лицея")
    ap.add_argument("--full", action="store_true", help="перекачать весь архив")
    ap.add_argument("--refresh-last", type=int, default=10)
    ap.add_argument("--max-pages", type=int, default=200)
    ap.add_argument("--out-dir", default=str(config.NEWS_OUTPUT_DIR))
    args = ap.parse_args()
    if args.refresh_last < 0 or args.max_pages < 1:
        ap.error("refresh-last должен быть >= 0, max-pages >= 1")
    try:
        result = scrape(args.out_dir, full_refresh=args.full,
                        refresh_last=args.refresh_last, max_pages=args.max_pages)
    except Exception as exc:
        print(f"[!] {exc}", file=sys.stderr)
        sys.exit(1)
    save_json(config.NEWS_STATUS_FILE, result)


if __name__ == "__main__":
    main()
