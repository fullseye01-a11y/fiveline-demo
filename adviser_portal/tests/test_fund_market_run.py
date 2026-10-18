"""Full-market offline execution: resume, changed evidence and cross-batch reuse."""
from datetime import timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from adviser_portal.fund_market_run import run_market
from adviser_portal.catalog import validate_catalog, ALLOWED_SOURCES
from adviser_portal.fund_batch_queue import _identity_index
from adviser_portal.fund_batch_intake import BatchIntakeError
from adviser_portal.tests.test_fund_batch_intake import OFF, DOM, submission, TODAY


class MarketRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.doc = {
            "schema":"fiveline_market_universe_v1",
            "checked_at_utc":"2026-10-18T03:00:00Z",
            "covered_sources":list(ALLOWED_SOURCES),
            "catalog_complete_for_configured_sources":True,
            "total_rows":2,
            "category_counts":{"domestic_fund_share_class":1,"offshore_fund_share_class":1},
            "instruments":[dict(OFF),dict(DOM)],
        }
        self.catalog = self.root / "catalog.json"
        self.catalog.write_text(json.dumps(self.doc))
        self.snapshot = validate_catalog(self.doc)
        self.checkpoint = self.root / "checkpoint.json"
        self.names = []
        for n, row in enumerate((OFF, DOM)):
            self.add_batch(n, submission(row, self.snapshot))

    def add_batch(self, n, bundle):
        entry={"uid":bundle["uid"]}
        for key, ext in (("manifest","json"),("raw_csv","csv"),("sessions","json"),("identity_evidence","json")):
            if key not in bundle:
                continue
            name=f"{n}-{key}.{ext}"
            path=self.root/name
            if key=="raw_csv":
                path.write_bytes(bundle[key])
            else:
                path.write_text(json.dumps(bundle[key]))
            entry["csv" if key=="raw_csv" else key]=name
        name=f"batch-{n}.json"
        (self.root/name).write_text(json.dumps({
            "schema":"fiveline-private-nav-batch-file-index-v1",
            "catalog_snapshot_sha256":_identity_index(self.snapshot)[1],
            "entries":[entry],
        }))
        self.names.append(name)

    def run_it(self, **kwargs):
        return run_market(self.catalog, self.root, self.names, self.checkpoint,
                          today=kwargs.pop("today",TODAY), **kwargs)

    def test_stop_resume_and_idempotency(self):
        first=self.run_it(max_batches=1)
        self.assertEqual(first["audited_funds"],1)
        self.assertIsNone(first["provisional_pass_funds"])
        second=self.run_it(max_batches=1)
        self.assertEqual(second["executed_batches"],1)
        self.assertEqual(second["reused_batches"],1)
        self.assertEqual(second["provisional_pass_funds"],2)
        again=self.run_it()
        self.assertEqual(again["executed_batches"],0)
        self.assertEqual(again["audited_funds"],2)
        self.assertFalse(again["all_fund_3y_nav_ready"])
        self.assertEqual(again["official_nav_3y_persisted_rows"],0)

    def test_changed_raw_file_invalidates_successful_checkpoint(self):
        self.run_it()
        path=self.root/"0-raw_csv.csv"
        path.write_bytes(path.read_bytes()+b"\n")
        result=self.run_it()
        self.assertEqual(result["executed_batches"],1)
        self.assertEqual(result["provisional_pass_funds"],1)

    def test_validation_day_change_reaudits_both_batches(self):
        self.run_it()
        result=self.run_it(today=TODAY+timedelta(days=1))
        self.assertEqual(result["executed_batches"],2)

    def test_cross_batch_reuse_rejects_both_including_cached_first(self):
        self.run_it(max_batches=1)
        path=self.root/"1-raw_csv.csv"
        path.write_bytes((self.root/"0-raw_csv.csv").read_bytes())
        result=self.run_it()
        self.assertEqual(result["provisional_pass_funds"],0)
        for item in result["results"]:
            self.assertIn("RAW_NAV_FILE_REUSED_ACROSS_MARKET_BATCHES",item["blockers"])

    def test_duplicate_uid_across_batches_rejected_before_any_audit(self):
        path=self.root/self.names[1]
        doc=json.loads(path.read_text())
        doc["entries"][0]["uid"]=OFF["uid"]
        path.write_text(json.dumps(doc))
        with self.assertRaisesRegex(BatchIntakeError,"DUPLICATE_UID"):
            self.run_it()
        self.assertFalse(self.checkpoint.exists())

    def test_catalog_change_cannot_resume_previous_snapshot(self):
        self.run_it()
        self.doc["instruments"][0]["name"]="changed class"
        self.catalog.write_text(json.dumps(self.doc))
        with self.assertRaisesRegex(BatchIntakeError,"CHECKPOINT_CATALOG"):
            self.run_it()

    def test_no_bundles_is_missing_data_not_history_completion(self):
        self.names=[]
        report=self.run_it()
        self.assertEqual(report["funds_without_history_bundle"],2)
        self.assertEqual(report["submitted_funds"],0)
        self.assertFalse(report["all_fund_3y_nav_ready"])

    def test_removed_batch_does_not_survive_from_checkpoint(self):
        self.run_it()
        self.names=self.names[:1]
        report=self.run_it()
        self.assertEqual(report["audited_funds"],1)
        self.assertEqual(report["funds_without_history_bundle"],1)

    def test_path_traversal_refused(self):
        self.names=["../outside.json"]
        with self.assertRaisesRegex(BatchIntakeError,"TRAVERSAL"):
            self.run_it()

    def test_checkpoint_contains_no_nav_price_columns(self):
        self.run_it()
        self.assertNotIn("nav_per_unit",self.checkpoint.read_text())
        self.assertNotIn("raw_csv",self.checkpoint.read_text())


if __name__=="__main__":
    unittest.main()
