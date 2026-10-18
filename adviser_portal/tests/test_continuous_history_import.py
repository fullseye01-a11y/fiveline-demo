from datetime import date,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
import sqlite3
import unittest
from adviser_portal.continuous_history_import import import_range,import_range_parallel
from adviser_portal.tests.test_sitca_history_capture import page,DAY


class ContinuousImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        p=patch('adviser_portal.continuous_history_import.time.sleep');p.start();self.addCleanup(p.stop)
    def run_range(self,**kwargs):
        return import_range(DAY,DAY+timedelta(days=2),self.root,minimum_rows=1,
                            fetcher=kwargs.pop('fetcher',lambda d:page(d,nav=str(d.toordinal()))),**kwargs)
    def test_contiguous_calendar_dates_really_persist_prices_and_archives(self):
        r=self.run_range()
        self.assertEqual(r['research_nav_rows_persisted'],3)
        self.assertTrue(r['every_calendar_date_attempted'])
        self.assertEqual(r['acquisition_status_counts']['IMPORTED_UNAPPROVED_RESEARCH'],3)
        self.assertEqual(len(list(self.root.glob('*.html.gz'))),3)
        self.assertEqual(r['official_three_year_complete_funds'],0)
        with sqlite3.connect(self.root/'research.sqlite') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM research_observations').fetchone()[0],3)
    def test_interrupted_budget_resumes_without_fetching_imported_dates(self):
        first=self.run_range(max_dates=1)
        self.assertEqual(first['paused_reason'],'DATE_BUDGET_REACHED')
        requested=[]
        second=self.run_range(fetcher=lambda d:(requested.append(d) or page(d,nav=str(d.toordinal()))))
        self.assertEqual(requested,[DAY+timedelta(days=1),DAY+timedelta(days=2)])
        self.assertEqual(second['research_nav_rows_persisted'],3)
        third=self.run_range(fetcher=lambda d:self.fail('must not fetch'))
        self.assertEqual(third['executed_dates_this_invocation'],0)
    def test_failed_table_is_unresolved_not_assumed_holiday_or_zero_nav(self):
        r=self.run_range(fetcher=lambda d:b'<html>No data</html>' if d==DAY+timedelta(days=1) else page(d,nav=str(d.toordinal())))
        self.assertEqual(r['research_nav_rows_persisted'],2)
        self.assertEqual(r['acquisition_status_counts']['SOURCE_TABLE_REQUIRES_REVIEW'],1)
        self.assertEqual(r['unattempted_calendar_dates'],0)
        self.assertEqual(len(list((self.root/'rejected').glob('*.gz'))),1)
    def test_three_network_failures_pause_without_claiming_remaining_dates(self):
        def broken(d):raise TimeoutError()
        r=import_range(DAY,DAY+timedelta(days=9),self.root,minimum_rows=1,fetcher=broken)
        self.assertEqual(r['paused_reason'],'CONSECUTIVE_SOURCE_TRANSPORT_FAILURES')
        self.assertEqual(r['unattempted_calendar_dates'],7)
        self.assertEqual(r['research_nav_rows_persisted'],0)
    def test_rate_limit_or_access_denial_stops_immediately(self):
        for code in (401,403,429):
            with TemporaryDirectory() as root:
                def denied(d):raise HTTPError('https://public.invalid',code,'denied',{},None)
                r=import_range(DAY,DAY+timedelta(days=3),root,fetcher=denied)
                self.assertEqual(r['paused_reason'],'HTTP_'+str(code))
                self.assertEqual(r['attempted_calendar_dates'],1)
    def test_explicit_retry_increments_failed_attempt_and_imports(self):
        self.run_range(fetcher=lambda d:b'bad')
        skipped=self.run_range(fetcher=lambda d:self.fail('requires explicit retry'))
        self.assertEqual(skipped['executed_dates_this_invocation'],0)
        recovered=self.run_range(retry_failures=True)
        self.assertEqual(recovered['research_nav_rows_persisted'],3)
        with sqlite3.connect(self.root/'research.sqlite') as db:
            self.assertEqual(db.execute('SELECT min(attempts) FROM acquisition_dates').fetchone()[0],2)
    def test_missing_archived_source_cannot_be_silently_reused(self):
        self.run_range()
        (self.root/(DAY.isoformat()+'.html.gz')).unlink()
        with self.assertRaisesRegex(ValueError,'ARCHIVE_MISSING'):self.run_range()
    def test_operator_pause_is_resumable_without_source_request(self):
        (self.root/'pause-requested').touch()
        r=self.run_range(fetcher=lambda d:self.fail('paused'))
        self.assertEqual(r['attempted_calendar_dates'],0)
        self.assertEqual(r['paused_reason'],'OPERATOR_PAUSE')
    def test_repeated_table_on_different_days_never_imported_twice(self):
        r=self.run_range(fetcher=lambda d:page(d,nav='12'))
        self.assertEqual(r['research_nav_rows_persisted'],1)
        self.assertEqual(r['acquisition_status_counts']['SOURCE_TABLE_REQUIRES_REVIEW'],2)

    def test_parallel_prefetch_writes_in_date_order_and_preserves_resume(self):
        with patch('adviser_portal.continuous_history_import.make_public_fetcher',
                   return_value=lambda d:page(d,nav=str(d.toordinal()))):
            r=import_range_parallel(DAY,DAY+timedelta(days=5),self.root,workers=3,minimum_rows=1)
        self.assertEqual(r['research_nav_rows_persisted'],6)
        self.assertTrue(r['every_calendar_date_attempted'])
        again=self.run_range(fetcher=lambda d:self.fail('already imported'))
        self.assertEqual(again['executed_dates_this_invocation'],0)

    def test_parallel_cached_raw_snapshot_does_not_require_network_result(self):
        (self.root/(DAY.isoformat()+'.html')).write_bytes(page(DAY,nav=str(DAY.toordinal())))
        requested=[]
        def source(d):requested.append(d);return page(d,nav=str(d.toordinal()))
        with patch('adviser_portal.continuous_history_import.make_public_fetcher',return_value=source):
            r=import_range_parallel(DAY,DAY+timedelta(days=2),self.root,workers=2,minimum_rows=1)
        self.assertNotIn(DAY,requested)
        self.assertEqual(r['research_nav_rows_persisted'],3)

    def test_transient_storage_reopen_reuses_received_bytes_without_refetch(self):
        from adviser_portal.sitca_history_capture import capture_days as actual_capture
        calls=[];attempts=[]
        def source(d):calls.append(d);return page(d,nav=str(d.toordinal()))
        def transient(days,root,**kwargs):
            attempts.append(days[0])
            if len(attempts)==1:
                kwargs['fetcher'](days[0])
                raise sqlite3.OperationalError('attempt to write a readonly database')
            return actual_capture(days,root,**kwargs)
        with patch('adviser_portal.continuous_history_import.capture_days',side_effect=transient):
            r=self.run_range(fetcher=source)
        self.assertEqual(len(calls),3)
        self.assertEqual(r['research_nav_rows_persisted'],3)
        self.assertEqual(attempts.count(DAY),2)

    def test_database_file_replacement_between_import_and_ledger_reopens_connection(self):
        from adviser_portal.sitca_history_capture import capture_days as actual_capture
        import shutil,os
        replaced=[]
        def swap(days,root,**kwargs):
            r=actual_capture(days,root,**kwargs)
            if not replaced:
                original=Path(root)/'research.sqlite'
                other=Path(root)/'replacement.sqlite'
                shutil.copyfile(original,other)
                os.replace(other,original)
                replaced.append(True)
            return r
        with patch('adviser_portal.continuous_history_import.capture_days',side_effect=swap):
            r=self.run_range()
        self.assertEqual(r['attempted_calendar_dates'],3)
        self.assertEqual(r['research_nav_rows_persisted'],3)


if __name__=='__main__':unittest.main()
