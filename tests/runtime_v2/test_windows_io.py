"""Offline portability regressions; these do not replace native Windows tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from oneil_runtime_v2.preflight import inspect
from tools.build_monitor_v2_mock import build

ROOT = Path(__file__).resolve().parents[2]


class WindowsIOTests(unittest.TestCase):
    def test_missing_timezone_database_is_reported_without_core_import(self):
        with patch('oneil_runtime_v2.preflight.ZoneInfo', side_effect=ZoneInfoNotFoundError):
            result = inspect(ROOT)
        self.assertIn('KST_TIMEZONE_DATA_MISSING', result['reasons'])
        self.assertFalse(result['kst_timezone_available'])
        self.assertFalse(result['ready_to_trade'])

    def test_response_pipe_is_locale_independent_ascii_json(self):
        env = {**os.environ, 'PYTHONIOENCODING': 'cp949'}
        result = subprocess.run([sys.executable, str(ROOT/'tools/monitor_v2_test_response.py')],
                                cwd=ROOT, env=env, capture_output=True, check=True)
        payload = json.loads(result.stdout.decode('ascii'))
        self.assertEqual(payload['status'], 200)
        self.assertFalse(payload['body']['live_capable'])

    def test_mock_html_uses_explicit_utf8_despite_cp949_default(self):
        original_read, original_write = Path.read_text, Path.write_text

        def read(path, *args, **kwargs):
            kwargs.setdefault('encoding', 'cp949')
            return original_read(path, *args, **kwargs)

        def write(path, data, *args, **kwargs):
            kwargs.setdefault('encoding', 'cp949')
            return original_write(path, data, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, 'read_text', read), patch.object(Path, 'write_text', write):
                build(tmp)
            html = (Path(tmp)/'dist/index.html').read_text(encoding='utf-8')
            self.assertIn('인증 동작 모의 검증', html)
            self.assertIn('data-monitor-mode="mock"', html)


if __name__ == '__main__':
    unittest.main()
