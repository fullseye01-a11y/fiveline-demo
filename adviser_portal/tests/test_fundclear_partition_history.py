import json
import sqlite3
import tempfile
import unittest
from http.client import IncompleteRead
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch
from adviser_portal import fundclear_history_import as m
from adviser_portal import fundclear_partition_history as p


class PartitionTests(unittest.TestCase):
    def setup_root(self,tmp):
        root=Path(tmp);basic=b'official-frozen-basic';(root/'tdcc-basic.csv').write_bytes(basic)
        q={'class_key':m.sha(b'class'),'status':'QUEUED','code':'LU2156566916',
           'isin':'LU2156566916','name':'Fund','organize_code':'043','currency':'USD',
           'basic_source_sha256':m.sha(basic),'identity_approved':False}
        (root/'queue.json').write_text(json.dumps([q]));m.init_db(root/'research.sqlite')
        with sqlite3.connect(root/'research.sqlite') as db:
            db.execute('INSERT INTO acquisition VALUES(?,?,?)',(q['class_key'],'SOURCE_REQUIRES_REVIEW',json.dumps({**q,'http_status':500})))
        return root,q

    def source(self,path,body,spacing=1):
        start=date.fromisoformat(body['startDate'].replace('/','-'));end=date.fromisoformat(body['endDate'].replace('/','-'))
        if (end-start).days+1>120:return 500,b'{"message":"long range"}'
        return 200,json.dumps({'fundCurr':'USD','tableList':[{'navTxnDate':body['startDate'],'navValue':'7.791000'}]}).encode()

    def test_partition_import_audit_and_idempotent_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,q=self.setup_root(tmp)
            with patch.object(m,'fetch',side_effect=self.source) as fetch:
                result=p.import_one(root,q);calls=fetch.call_count;again=p.import_one(root,q)
            self.assertGreater(calls,3);self.assertGreater(result['rows_added'],3)
            self.assertTrue(result['source_request_coverage_complete']);self.assertEqual(again['rows_added'],0)
            audit=m.audit_store(root)
            self.assertEqual(audit['actual_nav_rows_verified'],result['rows_added'])
            self.assertEqual(audit['imported_classes_verified'],1)
            self.assertEqual(audit['fewest_observations_per_imported_class'],result['rows_added'])
            self.assertEqual(audit['temporal_review_classes'],1)
            self.assertEqual(audit['partition_manifests_verified'],1)
            with sqlite3.connect(root/'research.sqlite') as db:
                self.assertEqual(db.execute('SELECT DISTINCT nav_per_unit FROM research_observations').fetchall(),[('7.791000',)])
                self.assertEqual(db.execute('SELECT DISTINCT status FROM research_observations').fetchall(),[(m.STATUS,)])
            self.assertFalse(audit['calendar_completeness_approved'])

    def test_manifest_gap_or_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,q=self.setup_root(tmp)
            with patch.object(m,'fetch',side_effect=self.source):result=p.import_one(root,q)
            target=root/result['manifest'];target.write_bytes(target.read_bytes()+b' ')
            with self.assertRaisesRegex(ValueError,'PARTITION_MANIFEST_FINGERPRINT_MISMATCH'):m.audit_store(root)

    def test_failed_small_range_is_explicitly_reviewed_without_filling_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,q=self.setup_root(tmp)
            def source(path,body,spacing=1):
                if body['startDate']=='2023/10/18':return 500,b'{"message":"date range unavailable"}'
                return self.source(path,body,spacing)
            with patch.object(m,'fetch',side_effect=source):r=p.import_one(root,q)
            self.assertGreater(r['rows_added'],0);self.assertFalse(r['source_request_coverage_complete'])
            a=m.audit_store(root);self.assertEqual(a['classes_with_partition_source_review_ranges'],1)
            review=json.loads((root/'temporal-review.json').read_text())[0]
            self.assertEqual(review['source_request_review_ranges'][0]['from'],'2023-10-18')
            self.assertEqual(review['source_request_review_ranges'][0]['reason'],'HTTP_500')
            with sqlite3.connect(root/'research.sqlite') as db:
                self.assertEqual(db.execute("SELECT count(*) FROM research_observations WHERE valuation_date='2023-10-18'").fetchone()[0],0)

    def test_larger_ranges_can_bisect_only_within_original_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,q=self.setup_root(tmp)
            with patch.object(m,'fetch',side_effect=self.source) as source:plan,relative,fingerprint=p.acquire(root,q,initial_days=365)
            self.assertGreater(source.call_count,len(plan['leaves']))
            self.assertEqual(plan['leaves'][0]['start'],m.START.isoformat())
            self.assertEqual(plan['leaves'][-1]['end'],m.END.isoformat())
            self.assertTrue(all(leaf['status']=='READY' for leaf in plan['leaves']))

    def test_intervals_cover_scope_once_without_overlap(self):
        ranges=list(p.intervals());cursor=m.START
        for start,end in ranges:
            self.assertEqual(start,cursor);self.assertLessEqual((end-start).days+1,365);cursor=end+timedelta(days=1)
        self.assertEqual(cursor,m.END+timedelta(days=1))
        with self.assertRaises(ValueError):list(p.intervals(days=0))

    def test_overlapping_recovery_process_cannot_send_source_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,q=self.setup_root(tmp)
            with p.exclusive_run(root),patch.object(m,'fetch',side_effect=AssertionError('must not fetch')):
                with self.assertRaisesRegex(p.SourceStop,'PARTITION_RECOVERY_ALREADY_RUNNING'):p.recover(root)

    def test_currency_failure_keeps_entire_class_out_of_price_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,q=self.setup_root(tmp)
            def source(path,body,spacing=1):
                return 200,json.dumps({'fundCurr':'EUR' if body['startDate']=='2023/10/18' else 'USD','tableList':[{'navTxnDate':body['startDate'],'navValue':'1.000000'}]}).encode()
            with patch.object(m,'fetch',side_effect=source):r=p.import_one(root,q)
            self.assertEqual(r['rows_added'],0);self.assertIn('CURRENCY_MISMATCH',r['reasons'])
            with sqlite3.connect(root/'research.sqlite') as db:self.assertEqual(db.execute('SELECT count(*) FROM research_observations').fetchone()[0],0)

    def test_source_auth_failure_stops_without_import_or_extra_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,q=self.setup_root(tmp)
            with patch.object(m,'fetch',return_value=(429,b'{"message":"limit"}')) as source:
                with self.assertRaisesRegex(p.SourceStop,'HTTP_429'):p.import_one(root,q)
            self.assertEqual(source.call_count,1)
            with sqlite3.connect(root/'research.sqlite') as db:self.assertEqual(db.execute('SELECT count(*) FROM research_observations').fetchone()[0],0)

    def test_persistent_transient_failures_remain_bounded_and_do_not_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,q=self.setup_root(tmp)
            with patch.object(m,'fetch',return_value=(503,b'upstream reset')) as source,patch.object(p.time,'sleep'):
                with self.assertRaisesRegex(p.SourceStop,'CONSECUTIVE_SOURCE_TRANSPORT_FAILURES'):p.import_one(root,q)
            self.assertLessEqual(source.call_count,27)
            with sqlite3.connect(root/'research.sqlite') as db:self.assertEqual(db.execute('SELECT count(*) FROM research_observations').fetchone()[0],0)

    def test_checkpoint_reuses_raw_responses_after_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,q=self.setup_root(tmp);first=True
            def source(path,body,spacing=1):
                nonlocal first
                value=self.source(path,body,spacing)
                if first:first=False;(root/'pause-requested').touch()
                return value
            with patch.object(m,'fetch',side_effect=source) as original:
                with self.assertRaisesRegex(p.SourceStop,'OPERATOR_PAUSE'):p.import_one(root,q)
            cached=original.call_args.args[1];(root/'pause-requested').unlink()
            with patch.object(m,'fetch',side_effect=self.source) as resumed:r=p.import_one(root,q)
            self.assertGreater(r['rows_added'],0)
            self.assertNotIn(cached,[call.args[1] for call in resumed.call_args_list])

    def test_truncated_http_body_is_retried_without_using_partial_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,q=self.setup_root(tmp);failed=False
            def source(path,body,spacing=1):
                nonlocal failed
                if not failed:failed=True;raise IncompleteRead(b'truncated-untrusted')
                return self.source(path,body,spacing)
            with patch.object(m,'fetch',side_effect=source),patch.object(p.time,'sleep'):
                result=p.import_one(root,q)
            self.assertGreater(result['rows_added'],0)
            self.assertEqual(m.audit_store(root)['actual_nav_rows_verified'],result['rows_added'])

    def test_parent_code_must_have_exact_official_class_selector_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,q=self.setup_root(tmp);body={'all':False,'organizeCode':q['organize_code'],'fundCode':'OFFICIAL_PARENT'}
            raw=json.dumps([{'value':q['code'],'name':q['name']}]).encode()
            selector=m.save_raw(root,'/api/offshore/common-select/fund-class-selection',body,200,raw)
            (root/'parent-history-review.json').write_text(json.dumps([{'code':q['code'],'class_key':q['class_key'],'parent_class_selector_source':selector}]))
            with patch.object(m,'fetch',side_effect=self.source) as source:r=p.import_one(root,q)
            self.assertTrue(all(call.args[1]['fundCode']=='OFFICIAL_PARENT' for call in source.call_args_list))
            self.assertEqual(m.audit_store(root)['actual_nav_rows_verified'],r['rows_added'])
            with self.assertRaisesRegex(ValueError,'PARENT_SELECTOR_REQUEST_MISMATCH'):p.verify_parent(root,q,'GUESSED_PARENT',selector)

    def test_recent_currency_review_contains_both_sources_without_rewriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,q=self.setup_root(tmp)
            basic='基金代碼,基金名稱,基金ISIN_CODE,計價幣別\nLU2156566916,Fund,LU2156566916,美元\n'.encode()
            (root/'tdcc-basic.csv').write_bytes(basic);q['basic_source_sha256']=m.sha(basic);(root/'queue.json').write_text(json.dumps([q]))
            with patch.object(m,'fetch',side_effect=self.source):p.import_one(root,q)
            recent=root/'input.csv';recent.write_text('基金代碼,基金名稱,ISINCODE,計價幣別,日期,基金淨值(金額)\nLU2156566916,Fund,LU2156566916,JPY,20231018,7.791\n')
            report=m.reconcile_recent_source(root,recent);self.assertEqual(report['identity_review_rows'],1)
            details=json.loads((root/'recent-reconciliation.json').read_text())['identity_review_details']
            self.assertEqual(details[0]['expected']['currency'],'USD');self.assertEqual(details[0]['recent']['currency'],'JPY')
            self.assertFalse(details[0]['prices_changed'])


if __name__=='__main__':unittest.main()
