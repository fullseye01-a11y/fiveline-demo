from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import sqlite3
import unittest
from adviser_portal.sitca_history_capture import parse_capture,capture_days,CaptureError,HEADERS,DATE_FIELD

DAY=date(2023,10,18)
def page(day=DAY,nav='12.34',certificate='T001',currency='TWD',duplicate=False):
    header='<tr>'+''.join('<td>'+x+'</th>' for x in HEADERS)+'</tr>'
    values=['AA1','A0001','測試投信',certificate,'12345A','測試基金',currency,nav,'12','0.34']
    row='<tr>'+''.join('<td>'+x+'</td>' for x in values)+'</tr>'
    return ('<html><input name="'+DATE_FIELD+'" value="'+day.strftime('%Y%m%d')+'">'
            '<table>'+header+row+(row if duplicate else '')+'</table></html>').encode()


class HistoryCaptureTests(unittest.TestCase):
    def test_per_unit_rows_are_unapproved_with_requested_date_binding(self):
        r=parse_capture(page(),DAY,minimum_rows=1)
        self.assertEqual(r['numeric_nav_observations'],1)
        self.assertEqual(r['observations'][0]['nav_per_unit'],'12.34')
        self.assertFalse(r['independent_row_date_verified'])
        self.assertFalse(r['three_year_history_ready'])
        self.assertFalse(r['class_identity_approved'])

    def test_wrong_echo_and_missing_table_rejected(self):
        for raw in (page(day=date(2024,1,1)),page().replace('淨值'.encode(),'總額'.encode())):
            with self.assertRaises(CaptureError):parse_capture(raw,DAY,minimum_rows=1)

    def test_tiny_source_fails_default_complete_source_threshold(self):
        with self.assertRaisesRegex(CaptureError,'COVERAGE_TOO_SMALL'):parse_capture(page(),DAY)

    def test_missing_certificate_is_flagged_without_fabricating_it(self):
        r=parse_capture(page(certificate=''),DAY,minimum_rows=1)
        self.assertEqual(r['missing_certificate_code_rows'],1)
        self.assertEqual(r['observations'][0]['certificate_code'],'')

    def test_notes_zero_nan_and_negative_never_become_numeric_nav(self):
        for nav in ('(註1)','0','NaN','-12','Infinity'):
            r=parse_capture(page(nav=nav),DAY,minimum_rows=1)
            self.assertEqual(r['unavailable_or_nonpositive_rows'],1)
            self.assertEqual(r['numeric_nav_observations'],0)

    def test_duplicate_source_class_and_bad_currency_rejected(self):
        for raw in (page(duplicate=True),page(currency='???')):
            with self.assertRaises(CaptureError):parse_capture(raw,DAY,minimum_rows=1)

    def test_restart_reuses_snapshot_without_refetch_or_duplicate_sql_rows(self):
        with TemporaryDirectory() as root:
            first=capture_days([DAY],root,fetcher=lambda day:page(),minimum_rows=1)
            second=capture_days([DAY],root,fetcher=lambda day:self.fail('must reuse'),minimum_rows=1)
            self.assertTrue(second['snapshots'][0]['reused_local_snapshot'])
            with sqlite3.connect(Path(root)/'research.sqlite') as db:
                self.assertEqual(db.execute('SELECT count(*) FROM research_observations').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT status FROM research_observations').fetchone()[0],'UNAPPROVED_SOURCE_OBSERVATION')
            self.assertEqual(first['official_history_approved_rows'],0)
            self.assertNotIn('12.34',json.dumps(second))

    def test_corrupt_local_snapshot_cannot_resume(self):
        with TemporaryDirectory() as root:
            capture_days([DAY],root,fetcher=lambda day:page(),minimum_rows=1)
            (Path(root)/(DAY.isoformat()+'.html')).write_bytes(b'broken')
            with self.assertRaises(CaptureError):capture_days([DAY],root,minimum_rows=1)

    def test_no_source_observations_written_on_parse_failure(self):
        with TemporaryDirectory() as root:
            with self.assertRaises(CaptureError):capture_days([DAY],root,fetcher=lambda d:b'bad',minimum_rows=1)
            with sqlite3.connect(Path(root)/'research.sqlite') as db:
                self.assertEqual(db.execute('SELECT count(*) FROM research_observations').fetchone()[0],0)

    def test_duplicate_and_unbounded_date_batches_rejected(self):
        for dates in ([],[DAY,DAY],[DAY]*32,['2023-10-18']):
            with TemporaryDirectory() as root,self.assertRaises(CaptureError):
                capture_days(dates,root,minimum_rows=1)

    def test_endpoint_echoing_date_but_reusing_same_table_is_rejected(self):
        with TemporaryDirectory() as root:
            capture_days([DAY],root,fetcher=lambda d:page(d),minimum_rows=1)
            other=date(2024,10,18)
            with self.assertRaisesRegex(CaptureError,'IDENTICAL_TABLE'):
                capture_days([other],root,fetcher=lambda d:page(d),minimum_rows=1)


if __name__=='__main__':unittest.main()
