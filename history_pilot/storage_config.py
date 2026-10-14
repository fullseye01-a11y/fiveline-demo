"""Strict provider selection for the restricted history pilot.

Do not silently fall back to expiring Render PostgreSQL, stale credentials,
or ephemeral SQLite when Neon was selected.
"""
from __future__ import annotations
import os
from urllib.parse import urlsplit, parse_qs


def validate_neon_url(dsn: str) -> None:
    if not dsn:
        raise RuntimeError("Neon not connected: NEON_DATABASE_URL is missing")
    try:
        u = urlsplit(dsn)
        host = (u.hostname or "").lower()
        ssl = parse_qs(u.query).get("sslmode", [])
        if (u.scheme not in {"postgres", "postgresql"}
                or not (host.endswith(".neon.tech") and host != ".neon.tech")
                or not u.username or not u.password or not u.path.strip("/")
                or ssl not in (["require"], ["verify-full"])):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Invalid Neon DSN; require a Neon host and sslmode=require") from None


def configured_dsn() -> str | None:
    backend = os.getenv("PILOT_STORAGE_BACKEND", "disabled").lower().strip()
    if backend == "disabled":
        return None
    if backend == "neon":
        dsn = os.getenv("NEON_DATABASE_URL", "")
        validate_neon_url(dsn)
        return dsn
    raise RuntimeError("Unsupported storage backend; only 'neon' or 'disabled' permitted")
