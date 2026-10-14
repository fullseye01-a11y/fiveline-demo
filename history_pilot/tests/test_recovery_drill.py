import gzip
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from encrypted_backup import encrypt_blob
from recovery_drill import restore_drill

PASSPHRASE = "test-only-passphrase-never-use-in-production-2026"


def synthetic_record():
    values = {"market": "TWSE", "code": "2330", "day": "2026-10-13",
              "open": "100", "high": "103", "low": "99", "close": "101.5",
              "volume_shares": 1000, "turnover_twd": 101500, "note": ""}
    fingerprint = hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return dict(values, version=1, fingerprint=fingerprint,
                fetched_at="2026-10-14T01:00:00+00:00", approved=True, review_reason="")


def save_backup(folder, rows):
    doc = {"format": 1, "created_at": "2026-10-14T01:00:00+00:00",
           "row_count": len(rows), "rows": rows}
    out = Path(folder) / "synthetic.enc"
    out.write_bytes(encrypt_blob(gzip.compress(json.dumps(doc).encode()), PASSPHRASE))
    return out


class TestRecoveryDrill(unittest.TestCase):
    def test_synthetic_backup_restores_all_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = synthetic_record()
            result = restore_drill(save_backup(tmp, [row]), PASSPHRASE)
            self.assertEqual(result["restored_versions"], 1)
            self.assertTrue(result["live_database_untouched"])
            self.assertFalse(result["full_postgres_restore_tested"])

    def test_changed_quote_with_old_fingerprint_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = synthetic_record()
            row["close"] = "102"
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                restore_drill(save_backup(tmp, [row]), PASSPHRASE)

    def test_duplicate_version_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = synthetic_record()
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                restore_drill(save_backup(tmp, [row, row]), PASSPHRASE)

    def test_modified_encrypted_blob_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = save_backup(tmp, [synthetic_record()])
            blob = bytearray(path.read_bytes())
            blob[-1] ^= 1
            path.write_bytes(blob)
            with self.assertRaises(Exception):
                restore_drill(path, PASSPHRASE)

    def test_wrong_password_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Exception):
                restore_drill(save_backup(tmp, [synthetic_record()]), "another-valid-passphrase-long-2026")
