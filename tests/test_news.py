import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import news_scraper as news


def listing(*ids):
    return '<div class="newsList">' + ''.join(
        f'<div class="fullitem"><div class="link-title"><a href="/news/{i}.html">News {i}</a></div>'
        '<div class="datetime" title="19 октября 2026 г."></div></div>' for i in ids) + '</div>'


DETAIL = '<h1>Title</h1><div class="newsDetail"><div class="cb cb-text"><div class="content"><p>Body</p></div></div></div>'


class NewsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.out = Path(temp.name)
        for name in ('progress',):
            mock = patch.object(news, name)
            mock.start()
            self.addCleanup(mock.stop)
        mock = patch.object(news.time, 'sleep')
        mock.start()
        self.addCleanup(mock.stop)
        self.seed()

    def seed(self):
        self.index = self.out / 'news_index.json'
        self.full = self.out / 'news_full.json'
        self.index.write_text(json.dumps([{'id': 1, 'title': 'Old', 'date': '2020-01-01'}]))
        self.full.write_text(json.dumps({'1': {'body': 'Old', 'gallery': []}}))

    def scrape(self, pages, **kwargs):
        with patch.object(news, 'fetch', side_effect=lambda session, path: pages[path]):
            return news.scrape(self.out, refresh_last=0, **kwargs)

    def test_incremental_add_preserves_old_news(self):
        result = self.scrape({'/news/archive/': listing(2, 1),
                              '/news/archive/page_2.html': listing(1), '/news/2.html': DETAIL})
        self.assertEqual(result['count'], 2)
        self.assertEqual([i['id'] for i in json.loads(self.index.read_text())], [2, 1])
        self.assertEqual(json.loads(self.full.read_text())['1']['body'], 'Old')

    def test_repairs_missing_old_detail(self):
        self.full.write_text('{}')
        result = self.scrape({'/news/archive/': listing(1), '/news/1.html': DETAIL})
        self.assertEqual(result['count'], 1)
        self.assertIn('Body', json.loads(self.full.read_text())['1']['body'])

    def test_failure_preserves_both_files(self):
        before = [p.read_bytes() for p in (self.index, self.full)]
        with patch.object(news, 'fetch', side_effect=RuntimeError('network failure')):
            with self.assertRaises(RuntimeError):
                news.scrape(self.out)
        self.assertEqual([p.read_bytes() for p in (self.index, self.full)], before)
        for invalid in ('<html>Gateway error</html>', None):
            with self.assertRaises(RuntimeError):
                self.scrape({'/news/archive/': invalid})
        self.assertEqual([p.read_bytes() for p in (self.index, self.full)], before)

    def test_detail_parse_failure_does_not_write_partial_archive(self):
        before = [p.read_bytes() for p in (self.index, self.full)]
        with self.assertRaises(RuntimeError):
            self.scrape({'/news/archive/': listing(2, 1), '/news/archive/page_2.html': listing(1),
                         '/news/2.html': '<html>Invalid detail</html>'})
        self.assertEqual([p.read_bytes() for p in (self.index, self.full)], before)

    def test_corrupted_local_archive_is_not_replaced(self):
        self.full.write_text('broken')
        with self.assertRaises(RuntimeError):
            self.scrape({})
        self.assertEqual(self.full.read_text(), 'broken')

    def test_max_pages_does_not_publish_truncated_full_archive(self):
        before = [p.read_bytes() for p in (self.index, self.full)]
        with self.assertRaisesRegex(RuntimeError, 'лимит'):
            self.scrape({'/news/archive/': listing(2)}, full_refresh=True, max_pages=1)
        self.assertEqual([p.read_bytes() for p in (self.index, self.full)], before)

    def test_sanitizer_removes_scripts_handlers_and_unsafe_protocols(self):
        result = news.sanitize('<script>bad()</script><p onclick="bad()">Text '
                              '<a href="java\nscript:bad()">bad</a><a href="/files/a.pdf">PDF</a>'
                              '<img src="javascript:bad()"><img src="/img.jpg" onerror="bad()">'
                              '<iframe src="https://evil.test/embed"></iframe>'
                              '<iframe src="https://vkvideo.ru/video_ext.php?id=1"></iframe></p>')
        self.assertNotIn('script', result)
        self.assertNotIn('onclick', result)
        self.assertNotIn('onerror', result)
        self.assertNotIn('evil.test', result)
        self.assertIn('https://s3454.nubex.ru/files/a.pdf', result)
        self.assertIn('https://vkvideo.ru/', result)

    def test_gallery_and_emoji(self):
        detail = news.parse_detail('<h1>Title</h1><div class="newsDetail"><div class="cb cb-text">'
                                  '<div class="content"><p><img src="https://vk.ru/emoji/a" alt="🙂"> Text</p></div></div>'
                                  '<div class="cb cb-gallery"><a class="item" href="/photo.jpg"><img src="/thumb.jpg"></a></div></div>')
        self.assertIn('🙂', detail['body'])
        self.assertEqual(detail['gallery'][0]['full'], news.BASE + '/photo.jpg')

    def test_cover_prefers_gallery_then_body_then_thumbnail(self):
        item = {'thumb': '/thumb.jpg'}
        self.assertEqual(news.cover_url(item, {'gallery': [{'full': '/large.jpg'}], 'body': '<img src="/body.jpg">'}), news.BASE + '/large.jpg')
        self.assertEqual(news.cover_url(item, {'gallery': [], 'body': '<img src="javascript:bad()"><img src="/body.jpg">'}), news.BASE + '/body.jpg')
        self.assertEqual(news.cover_url(item, {'body': ''}), news.BASE + '/thumb.jpg')
        self.assertEqual(news.cover_url({}, {'body': ''}), '')


if __name__ == '__main__':
    unittest.main()
