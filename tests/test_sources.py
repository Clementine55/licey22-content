import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import config
import page_content_parser
import sources
import webapp


class SourcesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.roots_file = self.directory / "sources.json"
        for name, value in {
            "SOURCES_FILE": self.roots_file,
            "STATE_DIR": self.directory / "state",
            "TOC_FILE": self.directory / "_toc.json",
        }.items():
            mock = patch.object(config, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        mock = patch.object(webapp, "PANEL_TOKEN", "test-only")
        mock.start()
        self.addCleanup(mock.stop)
        self.client = webapp.app.test_client()

    def test_initial_roots_and_persistence(self):
        self.assertEqual(sources.load_sources(), config.TARGET_ROOTS)
        root = sources.add_source("http://архив.лицей22.рф/ru/98765")
        self.assertEqual(root, "https://s3454.nubex.ru/98765/")
        self.assertEqual(sources.load_sources(), config.TARGET_ROOTS + [root])
        self.assertEqual(json.loads(self.roots_file.read_text()), sources.load_sources())
        self.assertFalse(list(self.directory.glob(".sources-*")))

    def test_reject_invalid_and_untrusted_urls(self):
        for url in [None, 3, "", "javascript:alert(1)", "https://example.com/123/",
                    "https://s3454.nubex.ru.evil.test/123/", "https://s3454.nubex.ru/",
                    "https://u:p@s3454.nubex.ru/123/", "https://s3454.nubex.ru:443/123/",
                    "https://s3454.nubex.ru/news", "https://s3454.nubex.ru/news/1.html",
                    "https://s3454.nubex.ru/_data/1/", "https://s3454.nubex.ru/123/?printmode=yes",
                    "https://s3454.nubex.ru/123/#test", "https://s3454.nubex.ru/123/file.pdf",
                    "https://s3454.nubex.ru/123/../news/", "https://s3454.nubex.ru/%2e%2e/",
                    "https://s3454.nubex.ru/%252e%252e/", "https://s3454.nubex.ru/a\nb/"]:
            with self.subTest(url=url), self.assertRaises(sources.SourcesError):
                sources.add_source(url)
        self.assertFalse(self.roots_file.exists())

    def test_duplicate_and_covered_page(self):
        for url in ["https://s3454.nubex.ru/5933/", "https://лицей22.рф/ru/5933/123/"]:
            with self.assertRaises(sources.DuplicateSource):
                sources.add_source(url)
        self.assertFalse(self.roots_file.exists())

    def test_concurrent_writes_preserve_both_roots(self):
        urls = ["https://s3454.nubex.ru/98765/", "https://s3454.nubex.ru/98766/"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(sources.add_source, urls))
        self.assertEqual(set(sources.load_sources()), set(config.TARGET_ROOTS + urls))

    def test_corrupted_store_stops_parser(self):
        for data in ['{broken', '{}', '[]', '["https://example.com/123/"]']:
            self.roots_file.write_text(data)
            with self.assertRaises(sources.SourcesError):
                page_content_parser.discover_all_pages()
            self.assertEqual(self.roots_file.read_text(), data)

    def test_authorization_and_api(self):
        payload = {"url": "https://s3454.nubex.ru/98765/"}
        self.assertEqual(self.client.post("/api/sources", json=payload).status_code, 403)
        headers = {"X-Panel-Token": "test-only"}
        self.assertEqual(self.client.post("/api/sources", json=[], headers=headers).status_code, 400)
        response = self.client.post("/api/sources", json=payload, headers=headers)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["root"], payload["url"])
        self.assertEqual(self.client.post("/api/sources", json=payload, headers=headers).status_code, 409)
        data = self.client.get("/api/pages").json
        self.assertIn(payload["url"], [s["root"] for s in data["sections"]])
        with patch.object(webapp, "PANEL_TOKEN", ""):
            self.assertEqual(self.client.post("/api/sources", json=payload, headers=headers).status_code, 403)

    def test_api_returns_slug_and_pending_section(self):
        config.TOC_FILE.write_text(json.dumps([{
            "url": "https://s3454.nubex.ru/5933/", "title": "Test", "slug": "5933",
        }]))
        result = self.client.get("/api/pages")
        self.assertEqual(result.status_code, 200)
        section = next(s for s in result.json["sections"] if s["path"] == "/5933/")
        self.assertEqual(section["pages"][0]["slug"], "5933")
        self.roots_file.write_text("not json")
        self.assertEqual(self.client.get("/api/pages").status_code, 503)

    def test_failed_section_aborts_before_cleanup(self):
        with patch.object(page_content_parser, "discover_section_pages", return_value=set()):
            with self.assertRaisesRegex(RuntimeError, "Публикация и чистка отменены"):
                page_content_parser.discover_all_pages()

    def test_crawler_uses_fresh_sources(self):
        root = sources.add_source("https://s3454.nubex.ru/98765/")
        with patch.object(page_content_parser, "discover_section_pages", side_effect=lambda prefix, url: {url}) as crawl:
            self.assertIn(root, page_content_parser.discover_all_pages())
        self.assertEqual(crawl.call_count, len(config.TARGET_ROOTS) + 1)


if __name__ == "__main__":
    unittest.main()
