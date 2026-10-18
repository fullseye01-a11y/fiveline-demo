"""Licensed NAV *multi-fund* offline intake: 15 fail-closed synthetic tests."""
from datetime import date
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from adviser_portal import fund_batch_intake as b
from adviser_portal.fund_batch_queue import _identity_index
from adviser_portal.tests.test_nav_history_audit import sample,bundle,TODAY

OFF={"uid":"o1","source":"tdcc_offshore_funds",
     "category":"offshore_fund_share_class","code":"LU2156566916",
     "name":"富達全球優質債券 A 股月配","currency":"USD","isin":"LU2156566916"}
DOM={"uid":"d1","source":"sitca_domestic_funds",
     "category":"domestic_fund_share_class","code":"DIOB3",
     "name":"瀚亞多重收益 B 美元","currency":"美元","isin":""}
STOCK={"uid":"s1","source":"twse_listed_stocks",
       "category":"listed_stock","code":"2330","name":"台積電","currency":"TWD","isin":""}


def snapshot(rows=None):
    return {"items":rows if rows is not None else [OFF,DOM,STOCK],
            "checked_at_utc":"2026-10-18T03:00:00Z"}


def submission(row, snap=None, *, isin=None, rights=True, reviewed=True, identity=True):
    snap=snap or snapshot()
    isin=isin or row["isin"] or "TW000T0759D2"
    raw,days=sample(isin=isin,currency="USD")
    meta,sessions=bundle(raw,days,rights=rights,reviewed=reviewed,
                          isin=isin,currency="USD")
    meta.update({
        "catalog_snapshot_sha256":_identity_index(snap)[1],
        "source_uid":row["uid"],"source":row["source"],
        "catalog_code":row["code"],"catalog_name":row["name"],
        "category":row["category"],
    })
    out={"uid":row["uid"],"manifest":meta,"raw_csv":raw,"sessions":sessions}
    if identity and not row.get("isin"):
        out["identity_evidence"]={
            "uid":row["uid"],"source":row["source"],
            "category":row["category"],"code":row["code"],
            "name":row["name"],"isin":isin,"currency":"USD",
            "reviewer_approved":True,
            "issuer_identity_document_url":"https://issuer.example.org/proof.pdf",
            "source_document_sha256":sha256(b"issuer source").hexdigest(),
        }
    return out


class BatchIntakeTests(unittest.TestCase):
    def setUp(self):
        self.snap=snapshot()

    def audit(self,entries,snap=None):
        return b.audit_intake_batch(snap or self.snap,entries,today=TODAY)

    def test_two_different_funds_one_batch_both_are_only_provisional(self):
        report=self.audit([submission(OFF),submission(DOM)])
        self.assertEqual(report["catalog_fund_records"],2)
        self.assertEqual(report["batch_submissions"],2)
        self.assertEqual(report["provisional_source_date_quality_pass_count"],2)
        self.assertEqual(report["rejected_or_review_count"],0)
        self.assertTrue(all(x["provisional_nav_coverage_pass"] for x in report["results"]))
        self.assertEqual(report["results"][1]["identity_gate"],
                         "REVIEWER_ATTESTED_ISSUER_ISIN_PENDING_INDEPENDENT_CHECK")
        self.assertEqual(report["official_nav_3y_persisted_funds"],0)
        self.assertEqual(report["official_nav_3y_persisted_rows"],0)
        self.assertFalse(report["historical_total_return_approved"])
        self.assertFalse(report["portfolio_or_six_strategies_modified"])
        self.assertFalse(report["production_database_modified"])

    def test_missing_catalog_isin_without_document_rejected(self):
        e=submission(DOM,identity=False)
        r=self.audit([e])
        self.assertEqual(r["rejected_or_review_count"],1)
        self.assertEqual(r["results"][0]["blockers"],["MISSING_ISIN_REQUIRES_ISSUER_DOCUMENT"])

    def test_catalog_identity_matches_exact_isin(self):
        e=submission(OFF)
        e["manifest"]["isin"]="TW000T0759D2"
        e["manifest"]["source_sha256"]=sha256(e["raw_csv"]).hexdigest()
        r=self.audit([e])
        self.assertIn("CATALOG_ISIN_CONFLICT",r["results"][0]["blockers"])

    def test_different_currency_cannot_be_cross_used(self):
        e=submission(OFF)
        e["manifest"]["currency"]="EUR"
        r=self.audit([e])
        self.assertIn("CATALOG_CURRENCY_CONFLICT",r["results"][0]["blockers"])

    def test_same_source_code_reuse_never_enables_name_guessing(self):
        extra={**DOM,"uid":"d2","name":"另一檔美元級別"}
        s=snapshot([DOM,extra])
        e=submission(DOM,snap=s)
        # Attempt to use d1 data as d2 must fail exact name.
        e["uid"]="d2"
        e["manifest"].update(source_uid="d2",catalog_name=extra["name"])
        r=self.audit([e],s)
        self.assertIn("IDENTITY_EVIDENCE_MISMATCH",r["results"][0]["blockers"])

    def test_same_isin_on_two_catalog_uids_blocks_both_history_inputs(self):
        twin={**OFF,"uid":"o2","code":"LU2156566916-SECOND",
              "name":"名稱不同但共用相同 ISIN 的基金級別"}
        other_snapshot=snapshot([OFF,twin])
        one=submission(OFF,snap=other_snapshot)
        two=submission(twin,snap=other_snapshot)
        # These MUST be distinct CSV bytes, otherwise the duplicate-export
        # gate (a separate guard) masks the shared-ISIN identity rejection.
        two["raw_csv"]=two["raw_csv"].replace(b"10.0000",b"10.0001",1)
        two["manifest"]["source_sha256"]=sha256(two["raw_csv"]).hexdigest()
        # Even distinct raw files cannot be assigned until the
        # duplicative ISIN/currency/name catalog identity is reconciled.
        r=self.audit([one,two],other_snapshot)
        self.assertEqual(r["provisional_source_date_quality_pass_count"],0)
        self.assertTrue(all(
            "SHARED_ISIN_CLASS_REQUIRES_REVIEW" in x["blockers"]
            for x in r["results"]))

    def test_untrusted_invalid_url_port_error_is_safe_constant(self):
        bad=submission(DOM)
        bad["identity_evidence"]["issuer_identity_document_url"]="https://issuer.example.org:SECRETPORT/nav"
        r=self.audit([bad])
        self.assertEqual(r["results"][0]["blockers"],
                         ["IDENTITY_OFFICIAL_HTTPS_URL_REQUIRED"])
        self.assertNotIn("SECRETPORT",str(r))

    def test_identity_evidence_fake_unapproved_flag_is_not_accepted(self):
        bad=submission(DOM)
        bad["identity_evidence"]["reviewer_approved"]=False
        r=self.audit([bad])
        self.assertEqual(r["results"][0]["blockers"],
                         ["ISIN_REVIEWER_ATTESTATION_REQUIRED"])

    def test_rights_not_reviewed_cannot_be_provisional(self):
        r=self.audit([submission(OFF,rights=False)])
        self.assertEqual(r["provisional_source_date_quality_pass_count"],0)
        self.assertIn("DATA_LICENSE_REVIEW_NOT_APPROVED",r["results"][0]["blockers"])

    def test_unreviewed_calendar_cannot_pass(self):
        r=self.audit([submission(OFF,reviewed=False)])
        self.assertIn("ISSUER_SESSION_CALENDAR_NOT_REVIEWED",r["results"][0]["blockers"])

    def test_one_bad_file_does_not_hide_good_file(self):
        a=submission(OFF)
        bad=submission(DOM)
        bad["raw_csv"]=bad["raw_csv"].replace(b"10.0000",b"11.0000",1)
        r=self.audit([a,bad])
        self.assertEqual(r["provisional_source_date_quality_pass_count"],1)
        self.assertEqual(r["rejected_or_review_count"],1)
        self.assertEqual(r["results"][1]["status"],"REJECTED_NEEDS_REVIEW")
        self.assertFalse(r["all_fund_3y_nav_ready"])

    def test_catalog_changed_after_csv_preparation_fails_binding(self):
        e=submission(OFF)
        other={**DOM,"name":"新版基金名"}
        r=self.audit([e],snapshot([OFF,other]))
        self.assertEqual(r["results"][0]["blockers"],["SOURCE_SNAPSHOT_BINDING_REQUIRED"])

    def test_duplicate_uid_rejected_for_entire_batch(self):
        e=submission(OFF)
        with self.assertRaisesRegex(b.BatchIntakeError,"DUPLICATE"):
            self.audit([e,e])

    def test_reused_csv_digest_for_two_uids_invalidates_both(self):
        e1=submission(OFF);e2=submission(DOM)
        e2["raw_csv"]=e1["raw_csv"]
        r=self.audit([e1,e2])
        self.assertEqual(r["provisional_source_date_quality_pass_count"],0)
        self.assertEqual(r["results"][0]["blockers"],["RAW_NAV_FILE_REUSED_FOR_DIFFERENT_UID"])
        self.assertEqual(r["results"][1]["blockers"],["RAW_NAV_FILE_REUSED_FOR_DIFFERENT_UID"])

    def test_unknown_fund_excluded_without_crashing(self):
        e=submission(OFF)
        e["uid"]="fake-fund"
        r=self.audit([e])
        self.assertEqual(r["results"][0]["blockers"],["UID_NOT_IN_VERIFIED_FUND_DIRECTORY"])

    def test_snapshot_token_mismatch_is_fatal(self):
        e=submission(OFF)
        with self.assertRaisesRegex(b.BatchIntakeError,"SNAPSHOT_CHANGED"):
            b.audit_intake_batch(self.snap,[e],today=TODAY,expected_snapshot="x"*64)

    def test_batch_limit_and_binary_input(self):
        with self.assertRaisesRegex(b.BatchIntakeError,"BATCH_SIZE"):
            self.audit([])
        with self.assertRaisesRegex(b.BatchIntakeError,"BATCH_SIZE"):
            self.audit([{"uid":str(i),"raw_csv":b"123"} for i in range(101)])
        e=submission(OFF);e["raw_csv"]="not binary"
        with self.assertRaisesRegex(b.BatchIntakeError,"RAW_NAV_CSV_BYTES"):
            self.audit([e])

    def test_private_paths_block_traversal_and_symlink(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)
            (root/"safe.csv").write_bytes(b"x")
            self.assertEqual(b._safe_file(root,"safe.csv"),root/"safe.csv")
            for attempt in ("/etc/passwd","../safe.csv","./safe.csv","foo/../../evil",
                            "bad file.csv"):
                with self.subTest(attempt=attempt),self.assertRaises(b.BatchIntakeError):
                    b._safe_file(root,attempt)
            (root/"link").symlink_to("/etc/passwd")
            with self.assertRaises(b.BatchIntakeError):
                b._safe_file(root,"link")

    def test_all_market_9767_are_in_scope_even_if_one_file_submitted(self):
        allrows=[]
        for category,source,prefix,count in (
            ("domestic_fund_share_class","sitca_domestic_funds","d",4472),
            ("offshore_fund_share_class","tdcc_offshore_funds","o",5295)):
            for n in range(count):
                allrows.append({
                    "uid":f"{prefix}-{n:05d}","source":source,
                    "category":category,"code":f"{prefix}{n:05d}",
                    "name":f"基金-{prefix}-{n}","currency":"USD",
                    "isin":"",
                })
        s=snapshot(allrows)
        chosen=allrows[0]
        e=submission(chosen,snap=s)
        r=self.audit([e],s)
        self.assertEqual(r["catalog_fund_records"],9767)
        self.assertEqual(r["funds_without_submitted_history_bundle"],9766)
        self.assertEqual(r["provisional_source_date_quality_pass_count"],1)
        self.assertEqual(r["official_nav_3y_persisted_funds"],0)


if __name__=="__main__":
    unittest.main()
