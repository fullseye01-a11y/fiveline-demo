"""Private PostgreSQL archive for independently validated market history."""
from __future__ import annotations
import os
import sqlite3
from decimal import Decimal
from pathlib import Path

SCHEMA = """
CREATE SCHEMA IF NOT EXISTS history_stage;
CREATE TABLE IF NOT EXISTS history_stage.bar_versions (
 market TEXT NOT NULL CHECK (market IN ('TWSE','TPEX')),
 code TEXT NOT NULL,
 day DATE NOT NULL,
 version INTEGER NOT NULL CHECK (version >= 1),
 open NUMERIC NOT NULL, high NUMERIC NOT NULL,
 low NUMERIC NOT NULL, close NUMERIC NOT NULL,
 volume_shares BIGINT NOT NULL CHECK (volume_shares > 0),
 turnover_twd BIGINT CHECK (turnover_twd >= 0),
 note TEXT NOT NULL DEFAULT '',
 fingerprint TEXT NOT NULL,
 fetched_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 approved BOOLEAN NOT NULL DEFAULT FALSE,
 review_reason TEXT NOT NULL DEFAULT '',
 PRIMARY KEY (market,code,day,version),
 CHECK (high >= GREATEST(open,close,low) AND low <= LEAST(open,close,high))
);
CREATE INDEX IF NOT EXISTS hist_stg_symbol_date ON history_stage.bar_versions(market,code,day DESC);
CREATE OR REPLACE VIEW history_stage.approved_bars AS
 SELECT b.* FROM history_stage.bar_versions b WHERE b.approved = TRUE
 AND b.version = (
  SELECT MAX(v.version) FROM history_stage.bar_versions v
  WHERE v.market=b.market AND v.code=b.code AND v.day=b.day AND v.approved=TRUE
 );
CREATE TABLE IF NOT EXISTS history_stage.sync_runs (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 incoming_count INTEGER NOT NULL,
 inserted_count INTEGER NOT NULL,
 unchanged_count INTEGER NOT NULL,
 pending_count INTEGER NOT NULL,
 source TEXT NOT NULL DEFAULT 'sqlite_stage'
);
"""


def plan_revision(last, staged_approved: bool, staged_reason: str):
    """Existing quote changed => hold even if ephemeral SQLite lost its history."""
    if last is not None:
        return False, ';'.join(filter(None, ('data_revised', staged_reason)))
    return bool(staged_approved), staged_reason


def persist_sqlite(db_path: Path, dsn: str | None = None) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    dsn = dsn or os.environ.get('DATABASE_URL')
    if not dsn:
        raise RuntimeError('DATABASE_URL is not configured: durable persistence DISABLED')
    try:
        import psycopg
    except ImportError as ex:
        raise RuntimeError('psycopg[binary] is required for PostgreSQL persistence') from ex
    source = sqlite3.connect(f'file:{db_path.resolve()}?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    counts = {'incoming':0,'inserted':0,'unchanged':0,'pending':0}
    try:
        with psycopg.connect(dsn, connect_timeout=15, application_name='fiveline_history_pilot') as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA)
                rows = source.execute('SELECT * FROM bar_versions ORDER BY market,code,day,version')
                for b in rows:
                    counts['incoming'] += 1
                    key=(b['market'],b['code'],b['day'])
                    cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('|'.join(key),))
                    cur.execute("""SELECT version,fingerprint FROM history_stage.bar_versions
                      WHERE market=%s AND code=%s AND day=%s
                      ORDER BY version DESC""", key)
                    existing=cur.fetchall()
                    # Replays may contain multiple locally staged revisions.
                    # Compare against ALL previously archived fingerprints, not
                    # only the newest version, to avoid endless fake revisions.
                    if any(old_fp == b['fingerprint'] for _, old_fp in existing):
                        counts['unchanged'] += 1
                        continue
                    last=existing[0] if existing else None
                    approved, reason=plan_revision(last, b['approved']==1, b['review_reason'])
                    if last is None:
                        cur.execute("""SELECT close FROM history_stage.approved_bars
                          WHERE market=%s AND code=%s AND day<%s ORDER BY day DESC LIMIT 1""", key)
                        prior=cur.fetchone()
                        if prior and abs(Decimal(b['close'])/Decimal(str(prior[0]))-1)>Decimal('0.30'):
                            approved=False
                            reason=';'.join(filter(None,(reason,'price_jump_over_30pct_review_corporate_action')))
                    cur.execute("""INSERT INTO history_stage.bar_versions
                      (market,code,day,version,open,high,low,close,
                       volume_shares,turnover_twd,note,fingerprint,approved,review_reason)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                      (*key, 1 if last is None else last[0]+1,
                       b['open'],b['high'],b['low'],b['close'],b['volume_shares'],
                       b['turnover_twd'],b['note'],b['fingerprint'],approved,reason))
                    counts['inserted'] += 1
                    if not approved:
                        counts['pending'] += 1
                cur.execute("""INSERT INTO history_stage.sync_runs
                  (incoming_count,inserted_count,unchanged_count,pending_count)
                  VALUES (%s,%s,%s,%s)""", tuple(counts[x] for x in ('incoming','inserted','unchanged','pending')))
    finally:
        source.close()
    return counts


def private_summary(dsn: str | None = None) -> dict:
    dsn = dsn or os.environ.get('DATABASE_URL')
    if not dsn:
        raise RuntimeError('DATABASE_URL is not configured')
    import psycopg
    with psycopg.connect(dsn, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM history_stage.approved_bars')
            approved=cur.fetchone()[0]
            cur.execute('SELECT COUNT(*) FROM history_stage.bar_versions WHERE approved=FALSE')
            pending=cur.fetchone()[0]
            cur.execute('SELECT COUNT(*) FROM history_stage.bar_versions WHERE version>1')
            revisions=cur.fetchone()[0]
    return {'approved_bars':approved, 'pending_review':pending,'revisions':revisions,'store':'postgresql_private'}
