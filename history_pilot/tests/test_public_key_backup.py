import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from public_key_backup import public_key_b64, encrypt_rows, decrypt_rows

TEST_KEY = "synthetic-test-passphrase-not-for-use-in-production-2026"
def sample():
    return {"market": "TWSE", "code": "2330", "day": "2026-10-13", "version": 1,
            "open": "100", "high": "103", "low": "99", "close": "101",
            "volume_shares": 1000, "turnover_twd": 101000, "note": "",
            "fingerprint": "a" * 64, "fetched_at": "2026-10-14T05:20:47+00:00",
            "approved": True, "review_reason": ""}

class TestWeeklyPublicBackup(unittest.TestCase):
    def test_valid_encryption_with_recipient_public_key(self):
        rows = [sample()]
        encrypted = encrypt_rows(rows, public_key_b64(TEST_KEY))
        self.assertNotIn(b'"close"', encrypted)
        self.assertEqual(decrypt_rows(encrypted, TEST_KEY), rows)

    def test_reject_wrong_recovery_key(self):
        encrypted = encrypt_rows([sample()], public_key_b64(TEST_KEY))
        with self.assertRaises(ValueError):
            decrypt_rows(encrypted, "another-synthetic-long-passphrase-for-2026")

    def test_reject_tampered_ciphertext(self):
        blob = bytearray(encrypt_rows([sample()], public_key_b64(TEST_KEY)))
        blob[-1] ^= 1
        with self.assertRaises(Exception):
            decrypt_rows(bytes(blob), TEST_KEY)

    def test_reject_duplicate_day_version(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            encrypt_rows([sample(),sample()], public_key_b64(TEST_KEY))

    def test_reject_empty_market_snapshot(self):
        with self.assertRaises(ValueError):
            encrypt_rows([], public_key_b64(TEST_KEY))

if __name__ == "__main__":
    unittest.main()
