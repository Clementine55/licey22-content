"""Обход раздела старого сайта по ссылкам — находит все страницы внутри
заданного префикса пути (BFS от корня раздела)."""

import time
from collections import deque
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

import config
import links
from http_client import fetch


def discover_section_pages(prefix: str, start_url: str) -> set:
    found = set()
    visited_keys = set()
    queue = deque([start_url])

    while queue and len(found) < config.MAX_PAGES_PER_SECTION:
        url = queue.popleft()
        if not links.is_allowed_page(url) or links.is_blocked(url):
            continue
        key = links.canonical_key(url)
        if key in visited_keys:
            continue
        visited_keys.add(key)

        path = urlparse(url).path
        if not path.startswith(prefix) and url != start_url:
            continue

        resp = fetch(url)
        time.sleep(config.REQUEST_DELAY)
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
            if parsed_url.hostname in links.RU_MIRROR_HOSTS:
                full_url = full_url.replace(parsed_url.hostname, config.CANONICAL_HOST)

            if links.is_blocked(full_url) or not links.is_allowed_page(full_url):
                continue
            if links.get_extension(full_url) in config.FILE_EXTENSIONS:
                continue

            full_path = urlparse(full_url).path
            if full_path.startswith("/ru/"):
                full_url = full_url.replace("/ru/", "/", 1)
                full_path = urlparse(full_url).path
            elif full_path == "/ru":
                full_url = full_url.replace("/ru", "/", 1)
                full_path = "/"

            if full_path.startswith(prefix) and links.canonical_key(full_url) not in visited_keys:
                queue.append(full_url)

    return found
