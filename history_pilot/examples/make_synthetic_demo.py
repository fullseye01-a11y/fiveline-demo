#!/usr/bin/env python3
"""Create an entirely synthetic private SQLite demonstration; NO official data."""
from __future__ import annotations
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from history_pilot import make_bar, open_db, stage_batch
from quality_report import summarize

def generate(path: Path):
    if path.exists():
        raise FileExistsError("Demo refuses to overwrite existing SQLite data")
    with open_db(path) as conn:
        for market, code, anchor in (("TWSE", "2330", 1000), ("TWSE", "0050", 120),
                                     ("TPEX", "8069", 100), ("TPEX", "6488", 200)):
            bars = []
            for d in (12, 13):
                v = anchor + (d - 12) * 2
                bars.append(make_bar(market, code, f"115/10/{d}", v, v + 1, v - 1, v, 10000, v * 10000))
            stage_batch(conn, market, f"synthetic:{code}", bars, [])
            stage_batch(conn, market, f"synthetic-replay:{code}", bars, [])
        revision = make_bar("TWSE", "2330", "115/10/13", 1002, 1004, 1001, 1003, 10000, 10030000)
        stage_batch(conn, "TWSE", "synthetic-revision:2330", [revision], [])
        bad_unit = make_bar("TPEX", "6488", "115/10/14", 201, 204, 200, 203, 1000, 203000000)
        stage_batch(conn, "TPEX", "synthetic-unit-outlier:6488", [bad_unit], [])
    return summarize(path)

if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / "private_data" / "synthetic_demo.sqlite3"
    print(json.dumps(generate(path), ensure_ascii=False, indent=2))
