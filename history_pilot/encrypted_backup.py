"""Manual authenticated backup for *small* PostgreSQL pilot histories.

Saving a backup on Render /tmp is NOT a backup: move the encrypted file to
private independent storage. No automatic upload or public endpoint exists.
"""
from __future__ import annotations
import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import secrets
from pathlib import Path

MAGIC = b'FIVELINE-HISTORY-BACKUP-V1\x00'
FIELDS = ('market','code','day','version','open','high','low','close',
          'volume_shares','turnover_twd','note','fingerprint','fetched_at','approved','review_reason')
MAX_ROWS = 50000


def encrypt_blob(payload: bytes, passphrase: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(passphrase)<20:
        raise ValueError('Passphrase too short (minimum 20 characters)')
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    key = hashlib.scrypt(passphrase.encode('utf-8'), salt=salt, n=16384, r=8, p=1, dklen=32, maxmem=67108864)
    aad = MAGIC + salt + nonce
    return aad + AESGCM(key).encrypt(nonce,payload,aad)


def decrypt_blob(ciphertext:bytes, passphrase:str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not ciphertext.startswith(MAGIC) or len(ciphertext) < len(MAGIC)+16+12+16:
        raise ValueError('Invalid or truncated backup')
    start=len(MAGIC)
    salt=ciphertext[start:start+16]
    nonce=ciphertext[start+16:start+28]
    aad=ciphertext[:start+28]
    key=hashlib.scrypt(passphrase.encode('utf-8'),salt=salt,n=16384,r=8,p=1,dklen=32,maxmem=67108864)
    return AESGCM(key).decrypt(nonce,ciphertext[start+28:],aad)


def backup_to_file(destination:Path, dsn:str, passphrase:str) ->dict:
    if destination.exists():
        raise FileExistsError(destination)
    if not dsn:
        raise RuntimeError('DATABASE_URL missing; no backup attempted')
    import psycopg
    with psycopg.connect(dsn,connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM history_stage.bar_versions')
            num=cur.fetchone()[0]
            if num>MAX_ROWS:
                raise RuntimeError('Pilot backup row cap exceeded; use streaming backup for full market')
            cur.execute('SELECT '+','.join(FIELDS)+' FROM history_stage.bar_versions ORDER BY market,code,day,version')
            values=cur.fetchall()
    rows=[dict(zip(FIELDS, [str(x) if isinstance(x,(dt.date,dt.datetime)) else x for x in row])) for row in values]
    contents=json.dumps({'format':1,'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),
                         'row_count':num,'rows':rows},ensure_ascii=False,separators=(',',':'),default=str).encode('utf-8')
    encrypted=encrypt_blob(gzip.compress(contents),passphrase)
    destination.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as fout:
            fout.write(encrypted)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {'rows':num,'encrypted_bytes':len(encrypted),'location':str(destination),
            'external_retention_confirmed':False}


def verify_file(source:Path, passphrase:str)->dict:
    raw=decrypt_blob(source.read_bytes(),passphrase)
    data=json.loads(gzip.decompress(raw))
    if data.get('format')!=1 or data.get('row_count')!=len(data.get('rows',[])):
        raise ValueError('Invalid backup metadata')
    if any(set(row)!=set(FIELDS) for row in data['rows']):
        raise ValueError('Invalid backup row schema')
    return {'valid_aead_and_schema':True,'rows':data['row_count'],
            'created_at':data['created_at'],'original_ohlcv_disclosed':False}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('mode',choices=['create','verify'])
    p.add_argument('--path',type=Path,required=True)
    args=p.parse_args()
    secret=os.environ.get('PILOT_BACKUP_PASSPHRASE','')
    if not secret:
        raise SystemExit('PILOT_BACKUP_PASSPHRASE missing')
    if args.mode=='create':
        result=backup_to_file(args.path,os.environ.get('DATABASE_URL',''),secret)
    else:
        result=verify_file(args.path,secret)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
