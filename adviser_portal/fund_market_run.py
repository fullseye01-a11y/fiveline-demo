"""Offline full-catalog scheduler for existing <=100-class history audits.

Checkpoints contain audit metadata only, never prices or approval to invest.
Local checkpoints are operator-controlled work records, not signed evidence.
No network, upload endpoint, client access, or production database writes.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import os
import tempfile

from .catalog import validate_catalog
from .fund_batch_queue import _identity_index
from .fund_batch_intake import run_private_batch, _safe_file, BatchIntakeError

SCHEMA = "fiveline_full_market_offline_run_v1"


def _digest_file(path, cap):
    if path.stat().st_size > cap:
        raise BatchIntakeError("RUN_INPUT_TOO_LARGE")
    return sha256(path.read_bytes()).hexdigest()


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".nav-run-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_market(catalog_file, root_folder, batch_indexes, checkpoint, *, today=None,
               max_batches=None):
    """Resume unchanged bundles; recompute global duplicate decisions each run.

    batch_indexes is a list of existing private batch-index paths, not NAV URLs.
    Rehash every file before using a cached audit. Changing any source file or
    the validation date invalidates its cached audit. A changed catalog requires
    a new checkpoint. Only a fully scanned run can have final coverage counts.
    """
    today = today or date.today()
    root = Path(root_folder)
    catalog_path = Path(catalog_file)
    _digest_file(catalog_path, 8_000_000)
    snapshot = validate_catalog(json.loads(catalog_path.read_text(encoding="utf-8")))
    rows, digest, _, _ = _identity_index(snapshot)
    known = {r["uid"] for r in rows}
    if (not isinstance(batch_indexes, list) or len(batch_indexes) > 24000 or
            any(not isinstance(x, str) for x in batch_indexes) or
            len(batch_indexes) != len(set(batch_indexes))):
        raise BatchIntakeError("RUN_BATCH_INDEX_LIST_INVALID")
    if max_batches is not None and (type(max_batches) is not int or max_batches < 1):
        raise BatchIntakeError("RUN_BATCH_BUDGET_INVALID")
    state = {"schema": SCHEMA, "catalog_snapshot_sha256": digest, "batches": {}}
    cp = Path(checkpoint)
    if cp.exists():
        _digest_file(cp, 48_000_000)
        state = json.loads(cp.read_text(encoding="utf-8"))
        if (state.get("schema") != SCHEMA or
                state.get("catalog_snapshot_sha256") != digest or
                not isinstance(state.get("batches"), dict)):
            raise BatchIntakeError("CHECKPOINT_CATALOG_OR_SCHEMA_CHANGED")
    # Preflight all index identities, even when this invocation has a budget.
    specs = []
    seen = set()
    for name in batch_indexes:
        index_path = _safe_file(root, name)
        index_hash = _digest_file(index_path, 120_000)
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if (not isinstance(index, dict) or
                index.get("schema") != "fiveline-private-nav-batch-file-index-v1" or
                index.get("catalog_snapshot_sha256") != digest):
            raise BatchIntakeError("RUN_INDEX_SNAPSHOT_OR_SCHEMA_INVALID")
        entries = index.get("entries")
        if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
            raise BatchIntakeError("RUN_INDEX_SIZE_INVALID")
        fingerprint = [SCHEMA, today.isoformat(), index_hash]
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("uid") not in known:
                raise BatchIntakeError("RUN_UID_NOT_IN_CATALOG")
            uid = entry["uid"]
            if uid in seen:
                raise BatchIntakeError("RUN_DUPLICATE_UID_ACROSS_BATCHES")
            seen.add(uid)
            if set(entry) - {"uid", "manifest", "csv", "sessions", "identity_evidence"}:
                raise BatchIntakeError("RUN_INDEX_ENTRY_INVALID")
            for key in ("manifest", "csv", "sessions", "identity_evidence"):
                relative = entry.get(key)
                if relative is None:
                    if key in ("manifest", "csv"):
                        raise BatchIntakeError("RUN_REQUIRED_SOURCE_MISSING")
                    continue
                file = _safe_file(root, relative)
                fingerprint.append([key, relative, _digest_file(
                    file, 8_000_000 if key == "csv" else 4_000_000)])
        token = sha256(json.dumps(fingerprint, separators=(",", ":")).encode()).hexdigest()
        specs.append((name, token))
    completed = []
    executed = reused = 0
    pending_batches = []
    # Keep only indices supplied for this run; removed batches cannot survive.
    state["batches"] = {n: state["batches"][n] for n, _ in specs if n in state["batches"]}
    for name, token in specs:
        prior = state["batches"].get(name)
        if isinstance(prior, dict) and prior.get("input_sha256") == token:
            report = prior["report"]
            reused += 1
        elif max_batches is not None and executed >= max_batches:
            pending_batches.append(name)
            continue
        else:
            report = run_private_batch(catalog_file, root, name, today=today)
            executed += 1
            state["batches"][name] = {"input_sha256": token, "report": report}
            _atomic_json(cp, state)
        # Copy: cross-batch rejection must not mutate the cached batch audit.
        completed.extend(json.loads(json.dumps(report["results"])))
    by_hash = {}
    for item in completed:
        by_hash.setdefault(item["source_sha256"], []).append(item)
    for members in by_hash.values():
        if len(members) > 1:
            for item in members:
                item.update(status="REJECTED_NEEDS_REVIEW", provisional_nav_coverage_pass=False,
                            blockers=["RAW_NAV_FILE_REUSED_ACROSS_MARKET_BATCHES"])
    final = not pending_batches
    counts = Counter(x["status"] for x in completed)
    return {
        "schema": SCHEMA, "catalog_snapshot_sha256": digest,
        "as_of": today.isoformat(), "catalog_fund_records": len(rows),
        "catalog_batches_at_100": (len(rows) + 99) // 100,
        "provided_batches": len(specs), "executed_batches": executed,
        "reused_batches": reused, "pending_batches": len(pending_batches),
        "submitted_funds": len(seen), "audited_funds": len(completed),
        "funds_without_history_bundle": len(known - seen),
        "scan_complete_for_supplied_batches": final,
        "status": ("PARTIAL_RUN_RESUME_REQUIRED" if not final else
                   "AWAITING_HISTORY_BUNDLES" if known - seen else "SOURCE_AUDIT_COMPLETE"),
        "provisional_pass_funds": sum(x["provisional_nav_coverage_pass"] for x in completed) if final else None,
        "audit_status_counts": dict(counts), "results": completed,
        "official_nav_3y_persisted_funds": 0, "official_nav_3y_persisted_rows": 0,
        "all_fund_3y_nav_ready": False, "portfolio_or_six_strategies_modified": False,
        "production_database_modified": False,
    }


def _cli():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--indexes", required=True, help="Private JSON list of relative batch-index paths")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--max-batches", type=int)
    args = parser.parse_args()
    index_list = Path(args.indexes)
    _digest_file(index_list, 4_000_000)
    report = run_market(args.catalog, args.root,
                        json.loads(index_list.read_text(encoding="utf-8")),
                        args.checkpoint, max_batches=args.max_batches)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # An empty or partial delivery must not be reported as successful intake.
    return 0 if (report["scan_complete_for_supplied_batches"] and
                 report["funds_without_history_bundle"] == 0 and
                 report["provisional_pass_funds"] == report["catalog_fund_records"]) else 2


if __name__ == "__main__":
    raise SystemExit(_cli())
