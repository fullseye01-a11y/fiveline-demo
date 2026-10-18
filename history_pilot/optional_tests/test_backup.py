import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from encrypted_backup import encrypt_blob,decrypt_blob,verify_file
import tempfile

class TestBackup(unittest.TestCase):
    def test_authenticated_roundtrip(self):
        data=b'private pilot example'
        blob=encrypt_blob(data,'long-secret-passphrase-123456')
        self.assertNotIn(data,blob)
        self.assertEqual(decrypt_blob(blob,'long-secret-passphrase-123456'),data)

    def test_tamper_rejected(self):
        payload=bytearray(encrypt_blob(b'test','long-secret-passphrase-123456'))
        payload[-1]^=1
        with self.assertRaises(Exception):
            decrypt_blob(bytes(payload),'long-secret-passphrase-123456')

    def test_password_rejected(self):
        payload=encrypt_blob(b'data','long-secret-passphrase-123456')
        with self.assertRaises(Exception):
            decrypt_blob(payload,'long-secret-passphrase-654321')

    def test_plaintext_backup_not_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'test.enc'
            f.write_text('hello')
            with self.assertRaises(ValueError):
                verify_file(f,'long-secret-passphrase-123456')

if __name__=='__main__':
    unittest.main()
