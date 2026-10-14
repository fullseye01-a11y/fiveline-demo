import os
import unittest
from unittest.mock import patch
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from storage_config import configured_dsn, validate_neon_url


SAMPLE="postgresql://test_user:test-pass@ep-sample.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"

class TestStorageConfig(unittest.TestCase):
    def test_neon_url_rejects_render_or_no_tls(self):
        for url in [
            "postgresql://user:pass@dpg-example.render.com/db?sslmode=require",
            "postgresql://user:pass@ep-test.neon.tech/db",
            "postgresql://user:pass@ep-test.neon.tech/db?sslmode=disable",
            "http://user:pass@ep-test.neon.tech/db?sslmode=require",
        ]:
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                validate_neon_url(url)

    def test_neon_url_accepts_neon_tls(self):
        self.assertIsNone(validate_neon_url(SAMPLE))

    def test_neon_mode_requires_secret(self):
        with patch.dict(os.environ, {"PILOT_STORAGE_BACKEND":"neon"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "not connected"):
                configured_dsn()

    def test_neon_mode_resolves_explicitly(self):
        with patch.dict(os.environ, {"PILOT_STORAGE_BACKEND":"neon",
                                      "NEON_DATABASE_URL":SAMPLE,
                                      "DATABASE_URL":"postgresql://old:old@render.invalid/old"}, clear=True):
            self.assertEqual(configured_dsn(),SAMPLE)

    def test_disabled_never_falls_back_to_old_render_url(self):
        with patch.dict(os.environ, {"PILOT_STORAGE_BACKEND":"disabled",
                                      "DATABASE_URL":"postgresql://old:old@render.invalid/old"}, clear=True):
            self.assertIsNone(configured_dsn())
