"""Opt-in AES-GCM encrypted archive to a PRIVATE Cloudflare R2 bucket.

Never creates public buckets/URLs, never stores credentials in source, and
never uploads raw exchange OHLCV. This is a *small-pilot* backup only.
"""
from __future__ import annotations
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

if __package__:
    from .encrypted_backup import backup_to_file, verify_file
    from .recovery_drill import restore_drill
else:
    from encrypted_backup import backup_to_file, verify_file
    from recovery_drill import restore_drill

MAX_OBJECT_BYTES = 8 * 1024 * 1024  # Budget guardrail, not a quota guarantee.


def validate_endpoint(endpoint: str) -> str:
    try:
        parsed = urlsplit(endpoint)
        hostname = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or not hostname.endswith(".r2.cloudflarestorage.com")
                or hostname == ".r2.cloudflarestorage.com"
                or parsed.username or parsed.password or parsed.path not in ("", "/")
                or parsed.port not in (None, 443) or parsed.query or parsed.fragment):
            raise ValueError
        return endpoint.rstrip("/")
    except (ValueError, TypeError):
        raise RuntimeError("R2_ENDPOINT must be a private HTTPS Cloudflare R2 S3 endpoint") from None


def r2_config() -> dict:
    required = ("R2_ENDPOINT", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                "PILOT_BACKUP_PASSPHRASE")
    values = {name: os.getenv(name, "").strip() for name in required}
    if any(not values[name] for name in required):
        raise RuntimeError("R2 backup disabled: one or more private backup settings are missing")
    values["R2_ENDPOINT"] = validate_endpoint(values["R2_ENDPOINT"])
    bucket = values["R2_BUCKET"]
    if not (3 <= len(bucket) <= 63 and all(ch.islower() or ch.isdigit() or ch == "-" for ch in bucket)
            and bucket[0].isalnum() and bucket[-1].isalnum()):
        raise RuntimeError("Invalid private R2 bucket name")
    if len(values["PILOT_BACKUP_PASSPHRASE"]) < 20:
        raise RuntimeError("Backup passphrase must be at least 20 characters")
    return values


def upload_and_verify(dsn: str) -> dict:
    """Must be invoked explicitly in an authenticated server environment.

    Performs a remote GET and AES-GCM verification before reporting success.
    Never deletes backups, never serves them publicly.
    """
    cfg = r2_config()
    if not dsn:
        raise RuntimeError("Durable Neon connection is missing")
    import boto3
    client = boto3.client(
        "s3", region_name="auto", endpoint_url=cfg["R2_ENDPOINT"],
        aws_access_key_id=cfg["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=cfg["R2_SECRET_ACCESS_KEY"],
    )
    now = datetime.now(timezone.utc)
    key = "history-pilot/" + now.strftime("%Y/%m/%d/%H%M%SZ-") + uuid.uuid4().hex + ".enc"
    with tempfile.TemporaryDirectory(prefix="fiveline-enc-") as td:
        target = Path(td) / "backup.enc"
        backup = backup_to_file(target, dsn, cfg["PILOT_BACKUP_PASSPHRASE"])
        encrypted = target.read_bytes()
        if len(encrypted) > MAX_OBJECT_BYTES:
            raise RuntimeError("Backup exceeds pilot size guardrail; no upload attempted")
        digest = hashlib.sha256(encrypted).hexdigest()
        client.put_object(
            Bucket=cfg["R2_BUCKET"], Key=key, Body=encrypted,
            ContentType="application/octet-stream",
            Metadata={"sha256": digest, "format": "fiveline-encrypted-v1"},
        )
        response = client.get_object(Bucket=cfg["R2_BUCKET"], Key=key)
        returned = response["Body"].read(MAX_OBJECT_BYTES + 1)
        if len(returned) > MAX_OBJECT_BYTES or hashlib.sha256(returned).hexdigest() != digest:
            raise RuntimeError("Remote backup integrity check failed")
        retrieved = Path(td) / "retrieved.enc"
        retrieved.write_bytes(returned)
        verified = verify_file(retrieved, cfg["PILOT_BACKUP_PASSPHRASE"])
        if not verified["valid_aead_and_schema"] or verified["rows"] != backup["rows"]:
            raise RuntimeError("Remote backup failed authenticated content verification")
        drill = restore_drill(retrieved, cfg["PILOT_BACKUP_PASSPHRASE"])
        if not drill["verified"] or drill["restored_versions"] != backup["rows"]:
            raise RuntimeError("Remote backup failed isolated restoration drill")
    return {"uploaded": True, "verified_by_readback": True,
            "isolated_sqlite_recovery_drill": True,
            "encrypted": True, "rows": backup["rows"],
            "backup_key": key, "external_backup": True,
            "restore_into_new_postgres_tested": False}


def main():
    from .storage_config import configured_dsn
    dsn = configured_dsn()
    if not dsn:
        raise SystemExit("Neon durable storage must be configured before backup")
    result = upload_and_verify(dsn)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
