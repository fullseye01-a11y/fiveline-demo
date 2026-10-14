"""Small-pilot backup envelope (X25519 + HKDF-SHA256 + AES-256-GCM).

Allows a scheduled agent to ENCRYPT weekly market-data archives with a public
recipient key, without ever accessing the original recovery passphrase.
This is not a backup scheduler nor a replacement for PostgreSQL pg_dump.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization

MAGIC = b'FIVELINE-PUBLIC-BACKUP-v1\x00'
MAX_ROWS = 5000
MAX_PLAINTEXT = 8 * 1024 * 1024
MAX_CIPHERTEXT = 10 * 1024 * 1024
FIELD_ORDER = (
    'market', 'code', 'day', 'version', 'open', 'high', 'low', 'close',
    'volume_shares', 'turnover_twd', 'note', 'fingerprint', 'fetched_at',
    'approved', 'review_reason'
)


def private_from_passphrase(passphrase: str) -> X25519PrivateKey:
    if len(passphrase) < 20:
        raise ValueError('Recovery passphrase is too short')
    seed = hashlib.scrypt(passphrase.encode('utf-8'),
                          salt=b'FIVELINE-HISTORY-X25519-RECOVERY-v1',
                          n=2**15, r=8, p=1, dklen=32, maxmem=128*1024*1024)
    return X25519PrivateKey.from_private_bytes(seed)


def public_key_b64(passphrase: str) -> str:
    public = private_from_passphrase(passphrase).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.urlsafe_b64encode(public).decode().rstrip('=')


def _decode_public_key(text: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(text + '=' * ((-len(text)) % 4))
        if len(raw) != 32:
            raise ValueError
        X25519PublicKey.from_public_bytes(raw)
        return raw
    except Exception as err:
        raise ValueError('Invalid recipient public key') from err


def _key(shared: bytes, salt: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                info=b'fiveline-weekly-archive-v1').derive(shared)


def _validate_rows(rows: list) -> None:
    if not isinstance(rows, list) or not (1 <= len(rows) <= MAX_ROWS):
        raise ValueError('Rows must be nonempty and within pilot limit')
    keyset = set()
    for r in rows:
        if not isinstance(r, dict) or set(r) != set(FIELD_ORDER):
            raise ValueError('Unexpected row schema')
        if r['market'] not in ('TWSE','TPEX') or not str(r['code']).isalnum():
            raise ValueError('Invalid code or exchange')
        k = (r['market'],r['code'],r['day'],r['version'])
        if k in keyset:
            raise ValueError('Duplicate bar version')
        keyset.add(k)
        if not isinstance(r['fingerprint'],str) or len(r['fingerprint']) != 64:
            raise ValueError('Missing row fingerprint')
        if type(r['approved']) is not bool:
            raise ValueError('Approval flag must be boolean')


def encrypt_rows(rows: list[dict], recipient_public_b64: str, *, recorded_at: str | None=None) -> bytes:
    _validate_rows(rows)
    doc = {'format': 1, 'schema': 'history_stage.bar_versions',
           'created_at': recorded_at or datetime.now(timezone.utc).isoformat(),
           'row_count': len(rows), 'rows': rows}
    payload = json.dumps(doc, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()
    if len(payload) > MAX_PLAINTEXT:
        raise ValueError('Backup exceeds pilot plaintext limit')
    raw_public = _decode_public_key(recipient_public_b64)
    ephemeral = X25519PrivateKey.generate()
    eph_pub = ephemeral.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    header = MAGIC + raw_public + eph_pub + salt + nonce
    secret = ephemeral.exchange(X25519PublicKey.from_public_bytes(raw_public))
    ciphertext = AESGCM(_key(secret,salt)).encrypt(nonce, gzip.compress(payload), header)
    result = header + ciphertext
    if len(result) > MAX_CIPHERTEXT:
        raise ValueError('Backup exceeds pilot ciphertext limit')
    return result


def decrypt_rows(blob: bytes, passphrase: str) -> list[dict]:
    hdr_len = len(MAGIC) + 32 + 32 + 16 + 12
    if len(blob) > MAX_CIPHERTEXT or len(blob) < hdr_len + 16 or not blob.startswith(MAGIC):
        raise ValueError('Bad backup magic/length')
    header = blob[:hdr_len]
    p = len(MAGIC)
    recipient_public = blob[p:p+32]
    ephemeral_public = blob[p+32:p+64]
    salt = blob[p+64:p+80]
    nonce = blob[p+80:p+92]
    private = private_from_passphrase(passphrase)
    actual_public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if recipient_public != actual_public:
        raise ValueError('Recovery key does not match backup recipient')
    secret = private.exchange(X25519PublicKey.from_public_bytes(ephemeral_public))
    compressed = AESGCM(_key(secret,salt)).decrypt(nonce,blob[hdr_len:],header)
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as gz:
        content = gz.read(MAX_PLAINTEXT+1)
    if len(content) > MAX_PLAINTEXT:
        raise ValueError('Decompressed backup exceeds safe limit')
    doc = json.loads(content)
    if doc.get('format') != 1 or doc.get('schema') != 'history_stage.bar_versions' or \
            type(doc.get('row_count')) is not int or doc['row_count'] != len(doc.get('rows',[])):
        raise ValueError('Unexpected backup manifest')
    _validate_rows(doc['rows'])
    return doc['rows']


def load_passphrase_from_key_file(file: Path) -> str:
    lines = file.read_text(encoding='utf-8').splitlines()
    match=[line.split('=',1)[1].strip() for line in lines if line.startswith('RECOVERY_PASSPHRASE=')]
    if len(match) != 1:
        raise ValueError('Missing recovery passphrase entry')
    return match[0]


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Small history snapshot public-key encryption')
    parser.add_argument('action',choices=('public-key','encrypt','verify'))
    parser.add_argument('--recovery-key-file',type=Path)
    parser.add_argument('--public-key')
    parser.add_argument('--source-json',type=Path)
    parser.add_argument('--backup',type=Path)
    args=parser.parse_args()
    if args.action in ('public-key','verify') and not args.recovery_key_file:
        parser.error('Recovery key file required')
    if args.action == 'public-key':
        print(public_key_b64(load_passphrase_from_key_file(args.recovery_key_file)))
    if args.action == 'encrypt':
        if not (args.public_key and args.source_json and args.backup):
            parser.error('Public key, JSON file, destination needed')
        doc = json.loads(args.source_json.read_text())
        args.backup.write_bytes(encrypt_rows(doc['rows'],args.public_key))
        print(json.dumps({'encrypted_bytes':args.backup.stat().st_size,'rows':len(doc['rows'])}))
    if args.action == 'verify':
        if not args.backup: parser.error('Backup path needed')
        rows=decrypt_rows(args.backup.read_bytes(),load_passphrase_from_key_file(args.recovery_key_file))
        print(json.dumps({'decrypted':True,'rows':len(rows),'no_public_prices':True}))
