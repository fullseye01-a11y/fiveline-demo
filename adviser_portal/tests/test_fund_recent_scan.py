from datetime import timedelta
from unittest.mock import patch
import json
import unittest
from adviser_portal.fund_recent_scan import audit_recent_market
from adviser_portal.tests.test_fund_batch_intake import snapshot, OFF, DOM, TODAY


class RecentMarketScanTests(unittest.TestCase):
    def setUp(self):
        self.basic=('基金代碼,基金ISIN_CODE,基金名稱,計價幣別\n'
                    f'{OFF["code"]},{OFF["isin"]},{OFF["name"]},USD\n').encode()
        self.head='基金代碼,ISINCODE,基金名稱,計價幣別,日期,基金淨值(金額)\n'
        self.row=f'{OFF["code"]},{OFF["isin"]},{OFF["name"]},USD,{TODAY.isoformat()},10.123\n'
        p=patch('adviser_portal.offshore_evidence.MIN_RECORDS',1)
        p.start();self.addCleanup(p.stop)

    def run_scan(self, recent=None, snap=None, today=None):
        return audit_recent_market(snap or snapshot(),self.basic,
                                   (self.head+(recent or self.row)).encode(),today=today or TODAY)

    def test_all_funds_counted_with_domestic_explicitly_pending(self):
        r=self.run_scan()
        self.assertEqual(r['catalog_fund_records'],2)
        self.assertEqual(r['fund_status_counts']['DOMESTIC_SOURCE_NOT_SUPPLIED'],1)
        self.assertEqual(r['verified_recent_fund_date_observations'],1)
        self.assertEqual(r['three_year_history_imported_rows'],0)
        self.assertNotIn('10.123',json.dumps(r))

    def test_same_date_conflicting_nav_rejected(self):
        r=self.run_scan(self.row+self.row.replace('10.123','11.123'))
        self.assertEqual(r['fund_status_counts']['REVIEW_REQUIRED'],1)
        self.assertEqual(r['verified_recent_fund_date_observations'],0)

    def test_same_date_duplicate_never_double_counted(self):
        self.assertEqual(self.run_scan(self.row+self.row)['verified_recent_fund_date_observations'],1)

    def test_wrong_currency_or_name_cannot_attach_to_catalog(self):
        for bad in (self.row.replace(',USD,',',EUR,'), self.row.replace(OFF['name'],'別的基金')):
            self.assertEqual(self.run_scan(bad)['verified_recent_fund_date_observations'],0)

    def test_shared_isin_across_catalog_records_remains_review(self):
        r=self.run_scan(snap=snapshot([OFF,dict(OFF,uid='o2')]))
        self.assertEqual(r['fund_status_counts']['SHARED_ISIN_IDENTITY_REVIEW'],2)

    def test_stale_and_future_observations_never_count_as_current(self):
        stale=self.run_scan(today=TODAY+timedelta(days=15))
        self.assertEqual(stale['fund_status_counts']['STALE_RECENT_NAV_EVIDENCE_ONLY'],1)
        self.assertEqual(stale['verified_recent_fund_date_observations'],0)
        future=self.run_scan(today=TODAY-timedelta(days=1))
        self.assertEqual(future['verified_recent_fund_date_observations'],0)

    def test_official_currency_name_aliases_match_same_iso_class_only(self):
        for source_name, iso in (('紐西蘭幣','NZD'),('新加坡幣','SGD'),('瑞典幣','SEK')):
            row=dict(OFF,currency=iso)
            basic=self.basic.decode().replace(',USD',','+source_name).encode()
            recent=(self.head+self.row.replace(',USD,',','+iso+',')).encode()
            r=audit_recent_market(snapshot([row]),basic,recent,today=TODAY)
            self.assertEqual(r['fund_status_counts']['OFFSHORE_RECENT_NAV_EVIDENCE_ONLY'],1)
            bad=audit_recent_market(snapshot([dict(row,currency='USD')]),basic,recent,today=TODAY)
            self.assertEqual(bad['verified_recent_fund_date_observations'],0)

    def test_missing_and_invalid_isin_report_distinct_actionable_causes(self):
        for isin, code in (('','MISSING_ISIN'),('LU2156566917','INVALID_ISIN')):
            r=self.run_scan(snap=snapshot([dict(OFF,isin=isin)]))
            self.assertEqual(r['blocker_counts'][code],1)

    def test_absent_isin_and_different_class_are_not_merged(self):
        absent=self.run_scan(self.row.replace(OFF['isin'],'LU2156566917'))
        self.assertEqual(absent['blocker_counts']['RECENT_ISIN_ABSENT'],1)
        renamed=self.run_scan(self.row.replace(OFF['name'],'另一級別'))
        self.assertEqual(renamed['blocker_counts']['RECENT_CLASS_KEY_MISMATCH'],1)


if __name__=='__main__':
    unittest.main()
