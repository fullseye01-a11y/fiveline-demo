import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r2_backup import validate_endpoint, r2_config

class TestR2Config(unittest.TestCase):
    def test_expected_endpoint(self):
        self.assertEqual(
            validate_endpoint("https://123456789abcdef01234567890abcdef.r2.cloudflarestorage.com"),
            "https://123456789abcdef01234567890abcdef.r2.cloudflarestorage.com")

    def test_wrong_destination_rejected(self):
        for value in (
            "https://evil.example", "http://abc.r2.cloudflarestorage.com",
            "https://abc.r2.cloudflarestorage.com.evil.example",
            "https://abc.r2.cloudflarestorage.com/file",
            "https://admin:pass@abc.r2.cloudflarestorage.com",
        ):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                validate_endpoint(value)

    def test_missing_secret_refuses_backup(self):
        with patch.dict("os.environ", {"R2_ENDPOINT":"https://abc.r2.cloudflarestorage.com"}, clear=True):
            with self.assertRaises(RuntimeError):
                r2_config()

    def test_passphrase_requirement(self):
        env={"R2_ENDPOINT":"https://abc.r2.cloudflarestorage.com",
             "R2_BUCKET":"fiveline-private-archive",
             "R2_ACCESS_KEY_ID":"example", "R2_SECRET_ACCESS_KEY":"example",
             "PILOT_BACKUP_PASSPHRASE":"short"}
        with patch.dict("os.environ",env,clear=True):
            with self.assertRaises(RuntimeError):
                r2_config()

if __name__=="__main__":
    unittest.main()
