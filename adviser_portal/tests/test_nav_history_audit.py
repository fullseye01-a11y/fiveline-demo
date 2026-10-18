"""Synthetic-only offline historical NAV provenance/date gate tests."""
import csv
from datetime import date,timedelta
from hashlib import sha256
from io import StringIO
import json
import unittest

from adviser_portal import nav_history_audit as a

TODAY=date(2026,10,18)
ISIN="LU2156566916"

def sample(days=None,currency="USD",isin=ISIN,duplicate=None):
    if days is None:
        day=date(2023,10,16)
        days=[]
        while day<=date(2026,10,16):
            # Synthetic dates, NOT exchange or issuer dealing-day evidence.
            if day.weekday()<5: days.append(day.isoformat())
            day+=timedelta(days=1)
    output=StringIO()
    writer=csv.writer(output)
    writer.writerow(a.REQUIRED_COLUMNS)
    for i,day in enumerate(days):
        writer.writerow([day,isin,currency,f"{(10+i/1000):.4f}"])
    if duplicate is not None:
        writer.writerow(duplicate)
    return output.getvalue().encode("utf-8"),days

def bundle(raw,days,rights=True,reviewed=True,isin=ISIN,currency="USD"):
    proof=sha256("\n".join(sorted(days)).encode()).hexdigest()
    manifest={
        "schema":"fiveline-raw-fund-nav-history-v1",
        "isin":isin,"currency":currency,
        "nav_type":"raw_unadjusted_per_unit",
        "provider_name":"Synthetic licensed source fixture",
        "source_url":"https://example.org/fund/nav.csv",
        "source_basis":"issuer_official",
        "source_sha256":sha256(raw).hexdigest(),
        "session_evidence_sha256":proof,
        "rights_reviewer_approved":rights,
        "issuer_identity_reviewed":True,
    }
    sessions={
        "schema":"issuer-share-class-nav-days-v1",
        "isin":isin,"currency":currency,
        "expected_nav_dates":days,
        "source_sha256":proof,"reviewer_approved":reviewed,
    }
    return manifest,sessions

class HistoricalNavAuditTests(unittest.TestCase):
    def setUp(self):
        self.raw,self.days=sample()
        self.meta,self.sessions=bundle(self.raw,self.days)
    def audit(self,raw=None,meta=None,sessions=None):
        return a.audit_history(meta if meta is not None else self.meta,
                               raw if raw is not None else self.raw,
                               sessions if sessions is not None else self.sessions,
                               today=TODAY)

    def test_full_synthetic_dates_pass_only_provisional_coverage(self):
        x=self.audit()
        self.assertEqual(x["status"],"PROVISIONAL_HISTORY_SOURCE_COVERAGE_ONLY")
        self.assertGreater(x["unique_nav_dates"],700)
        self.assertEqual(x["missing_expected_nav_count"],0)
        self.assertEqual(x["unexpected_nav_date_count"],0)
        self.assertTrue(x["historical_date_span_three_years"])
        self.assertTrue(x["reviewed_issuer_nav_calendar"])
        self.assertTrue(x["calendar_coverage_provisional_pass"])
        self.assertFalse(x["ready_for_total_return"])
        self.assertFalse(x["ready_for_portfolio"])
        self.assertFalse(x["ready_for_six_strategies"])
        self.assertFalse(x["database_modified"])
        self.assertNotIn("annualized_return",x)
    def test_no_calendar_fail_closed_even_if_three_year_span(self):
        x=a.audit_history(self.meta,self.raw,today=TODAY)
        self.assertFalse(x["calendar_coverage_provisional_pass"])
        self.assertIn("ISSUER_SESSION_CALENDAR_NOT_SUPPLIED",x["blockers"])
    def test_calendar_unreviewed_cannot_claim_provisional(self):
        sess={**self.sessions,"reviewer_approved":False}
        x=self.audit(sessions=sess)
        self.assertFalse(x["calendar_coverage_provisional_pass"])
        self.assertIn("ISSUER_SESSION_CALENDAR_NOT_REVIEWED",x["blockers"])
    def test_missing_one_synthetic_nav_day_is_reported(self):
        raws,days=sample(days=self.days[:100]+self.days[101:])
        meta={**self.meta,"source_sha256":sha256(raws).hexdigest()}
        x=self.audit(raws,meta)
        self.assertEqual(x["missing_expected_nav_count"],1)
        self.assertEqual(x["sample_missing_dates"],[self.days[100]])
        self.assertIn("MISSING_EXPECTED_NAV_DATES",x["blockers"])
        self.assertFalse(x["calendar_coverage_provisional_pass"])
    def test_extra_day_fails_against_reviewed_calendar(self):
        add="2026-10-17"
        raw,days=sample(self.days+[add])
        meta={**self.meta,"source_sha256":sha256(raw).hexdigest()}
        x=self.audit(raw,meta)
        self.assertEqual(x["unexpected_nav_date_count"],1)
        self.assertFalse(x["calendar_coverage_provisional_pass"])
    def test_two_year_source_is_not_three_year_history(self):
        partial=[d for d in self.days if d>="2024-10-18"]
        raw,days=sample(partial)
        meta,sessions=bundle(raw,days)
        x=self.audit(raw,meta,sessions)
        self.assertFalse(x["historical_date_span_three_years"])
        self.assertIn("LESS_THAN_THREE_YEARS_OF_NAV",x["blockers"])
    def test_source_bytes_change_requires_new_digest(self):
        corrupt=self.raw.replace(b"10.0000",b"11.0000",1)
        with self.assertRaisesRegex(a.HistoryAuditError,"SHA-256"):
            self.audit(corrupt)
    def test_session_days_change_requires_new_hash(self):
        sess={**self.sessions,"expected_nav_dates":self.days[1:]}
        with self.assertRaisesRegex(a.HistoryAuditError,"hash"):
            self.audit(sessions=sess)
    def test_mixed_share_class_rejected(self):
        raw,_=sample(self.days,duplicate=["2026-10-17","LU2156566917","USD","10.5"])
        meta={**self.meta,"source_sha256":sha256(raw).hexdigest()}
        with self.assertRaisesRegex(a.HistoryAuditError,"Mixed share"):
            self.audit(raw,meta)
    def test_mixed_currency_rejected(self):
        raw,_=sample(self.days,duplicate=["2026-10-17",ISIN,"EUR","10.5"])
        meta={**self.meta,"source_sha256":sha256(raw).hexdigest()}
        with self.assertRaisesRegex(a.HistoryAuditError,"currencies"):
            self.audit(raw,meta)
    def test_same_day_conflicting_values_rejected(self):
        raw,_=sample(self.days,duplicate=[self.days[0],ISIN,"USD","99.99"])
        meta={**self.meta,"source_sha256":sha256(raw).hexdigest()}
        with self.assertRaisesRegex(a.HistoryAuditError,"Conflicting NAV"):
            self.audit(raw,meta)
    def test_identical_duplicate_does_not_silently_pass(self):
        raw,_=sample(self.days,duplicate=[self.days[0],ISIN,"USD","10.0000"])
        meta={**self.meta,"source_sha256":sha256(raw).hexdigest()}
        x=self.audit(raw,meta)
        self.assertEqual(x["identical_duplicate_count"],1)
        self.assertIn("DUPLICATED_SAME_NAV_DATE",x["blockers"])
        self.assertFalse(x["calendar_coverage_provisional_pass"])
    def test_unapproved_license_is_visible_blocker_not_data_approval(self):
        m={**self.meta,"rights_reviewer_approved":False}
        x=self.audit(meta=m)
        self.assertIn("DATA_LICENSE_REVIEW_NOT_APPROVED",x["blockers"])
        self.assertFalse(x["ready_for_portfolio"])
    def test_wrong_nav_type_and_total_rejected(self):
        for v in ("adjusted_nav","total_nav_aggregate","total_return_nav"):
            with self.subTest(value=v),self.assertRaises(a.HistoryAuditError):
                self.audit(meta={**self.meta,"nav_type":v})
    def test_fabricated_zero_negative_or_nan_rejected(self):
        for value in ("0","-1","NaN","Infinity","10.001234567890"):
            raw=self.raw.replace(b"10.0000",value.encode(),1)
            m={**self.meta,"source_sha256":sha256(raw).hexdigest()}
            with self.subTest(value=value),self.assertRaises(a.HistoryAuditError):
                self.audit(raw,m)
    def test_future_nav_date_rejected(self):
        raw,_=sample(self.days,duplicate=["2026-11-01",ISIN,"USD","10.2000"])
        m={**self.meta,"source_sha256":sha256(raw).hexdigest()}
        with self.assertRaisesRegex(a.HistoryAuditError,"Future"):
            self.audit(raw,m)
    def test_wrong_schema_or_no_https_source_rejected(self):
        for bad in ({"schema":"source-v0"},
                    {"source_url":"http://example.org/nav.csv"},
                    {"source_url":"https://user:password@example.org/secret.csv"}):
            with self.subTest(bad=bad),self.assertRaises(a.HistoryAuditError):
                self.audit(meta={**self.meta,**bad})
    def test_calendar_not_inferred_from_weekdays_if_missing(self):
        # The fixture has weekday NAV observations; that does not supply
        # actual issuer NAV dates / closure evidence.
        x=a.audit_history(self.meta,self.raw,today=TODAY)
        self.assertIsNone(x["issuer_expected_nav_dates_count"])
        self.assertFalse(x["reviewed_issuer_nav_calendar"])
    def test_date_feb29_three_year_cutoff(self):
        self.assertEqual(a._three_year_cutoff(date(2024,2,29)),date(2021,2,28))

if __name__=="__main__":
    unittest.main()
