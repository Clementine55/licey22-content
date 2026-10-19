import subprocess
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import publish_to_github as publish


class PublishingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        for name, content in {'code.py': 'old', 'pages_content/a.json': '{}',
                              'pages_content/a.md': 'old', 'news_data/news_index.json': '[]',
                              'news_data/news_full.json': '{}'}.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.git('add', '.')
        self.git('commit', '-qm', 'initial')
        for name, value in (('REPO_DIR', self.repo), ('NEWS_OK', True)):
            mock = patch.object(publish, name, value)
            mock.start()
            self.addCleanup(mock.stop)

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo, stderr=subprocess.DEVNULL).decode().strip()

    def test_only_explicit_content_paths_are_staged(self):
        for name, content in {'code.py': 'changed', 'random-secret.txt': 'must stay local',
                              'news_data/debug.json': '{}', 'news_data/news_index.json': '[1]',
                              'pages_content/a.json': '{"new":true}', 'sources.json': '[]'}.items():
            (self.repo / name).write_text(content)
        (self.repo / 'pages_content/a.md').unlink()
        publish.stage_content()
        self.assertEqual(set(self.git('diff', '--cached', '--name-only').splitlines()),
                         {'news_data/news_index.json', 'pages_content/a.json', 'pages_content/a.md', 'sources.json'})

    def test_unexpected_staged_file_aborts_publication(self):
        (self.repo / 'code.py').write_text('changed')
        self.git('add', 'code.py')
        with self.assertRaisesRegex(RuntimeError, 'посторонние'):
            publish.stage_content()
        self.assertEqual(self.git('diff', '--cached', '--name-only'), 'code.py')

    def test_failed_news_are_not_staged_but_pages_are(self):
        (self.repo / 'news_data/news_index.json').write_text('[1]')
        (self.repo / 'pages_content/a.json').write_text('{"new":true}')
        with patch.object(publish, 'NEWS_OK', False):
            publish.stage_content()
        self.assertEqual(self.git('diff', '--cached', '--name-only'), 'pages_content/a.json')

    def test_symlink_cannot_publish_external_file(self):
        (self.repo / 'pages_content/secret.json').symlink_to(self.repo / 'code.py')
        with self.assertRaisesRegex(RuntimeError, 'символическая'):
            publish.stage_content()
        self.assertFalse(self.git('diff', '--cached', '--name-only'))

    def test_news_disabled_does_not_run_scraper(self):
        with patch.object(publish.config, 'NEWS_ENABLED', False), patch.object(publish, 'run') as run:
            self.assertTrue(publish.run_news_pipeline())
            run.assert_not_called()

    def test_news_failure_keeps_last_good_count(self):
        path = self.repo / 'news_status.json'
        path.write_text(json.dumps({'ok': True, 'count': 761, 'checked_at': 'last good'}))
        with patch.object(publish.config, 'NEWS_ENABLED', True), patch.object(publish.config, 'NEWS_STATUS_FILE', path), patch.object(publish, 'run', return_value=False):
            self.assertFalse(publish.run_news_pipeline())
        status = json.loads(path.read_text())
        self.assertFalse(status['ok'])
        self.assertEqual(status['count'], 761)
        self.assertEqual(status['checked_at'], 'last good')

    def test_commit_error_never_pushes(self):
        def command(*args, **kwargs):
            if args[0] == 'commit':
                raise subprocess.CalledProcessError(1, ['git', 'commit'])
            return b'pages_content/a.json'
        with patch.object(publish, 'stage_content'), patch.object(publish, 'git_checked', side_effect=command), patch.object(publish, 'write_status') as status, patch.object(publish.subprocess, 'run') as run:
            with self.assertRaises(SystemExit):
                publish.git_publish()
            run.assert_not_called()
            self.assertEqual(status.call_args.kwargs['stage'], 'git')


if __name__ == '__main__':
    unittest.main()
