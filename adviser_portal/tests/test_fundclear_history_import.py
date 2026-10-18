import json, tempfile, unittest, sqlite3
from pathlib import Path
from unittest.mock import patch
from adviser_portal import fundclear_history_import as m

class HistoryTests(unittest.TestCase):
    def raw(self,rows=None,currency='USD'):
        return json.dumps({'fundCurr':currency,'tableList':rows if rows is not None else [{'navTxnDate':'2023/10/18','navValue':'7.791000'}]}).encode()
    def test_preserves_decimal_precision_and_date(self):
        self.assertEqual(m.parse_history(self.raw(),'USD'),[('2023-10-18','7.791000')])
    def test_currency_mismatch_quarantined(self):
        with self.assertRaisesRegex(ValueError,'CURRENCY_MISMATCH'):m.parse_history(self.raw(),'EUR')
    def test_dates_cannot_escape_request_or_repeat(self):
        for rows in [[{'navTxnDate':'2023/10/17','navValue':'1'}],[{'navTxnDate':'2023/10/18','navValue':'1'}]*2]:
            with self.assertRaises(ValueError):m.parse_history(self.raw(rows),'USD')
    def test_invalid_values_never_become_prices(self):
        for value in ('0','-1','NaN','Infinity','','1e2','1,000'):
            with self.assertRaises(ValueError):m.parse_history(self.raw([{'navTxnDate':'2023/10/18','navValue':value}]),'USD')
    def test_exact_isin_checks(self):
        self.assertTrue(m.valid_isin('LU2156566916'))
        self.assertFalse(m.valid_isin('LU2156566917'))
        self.assertFalse(m.valid_isin(''))
    def test_import_is_idempotent_and_bound_to_class_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'tdcc-basic.csv').write_bytes(b'official-test-catalog')
            queue=[{'class_key':'first','status':'QUEUED','code':'LU2156566916','organize_code':'043','currency':'USD','basic_source_sha256':m.sha(b'official-test-catalog')}]
            (root/'queue.json').write_text(json.dumps(queue))
            with patch.object(m,'fetch',return_value=(200,self.raw())) as source:
                r=m.import_queue(root,workers=1)
                again=m.import_queue(root,workers=1)
            self.assertEqual(source.call_count,1)
            self.assertEqual(r['research_nav_rows_persisted'],1)
            self.assertEqual(again['formal_three_year_approved_funds'],0)
            self.assertFalse(again['commercial_rights_verified'])
            with sqlite3.connect(root/'research.sqlite') as db:
                self.assertEqual(db.execute('SELECT status FROM research_observations').fetchone()[0],m.STATUS)
                metadata=json.loads(db.execute('SELECT metadata_json FROM source_snapshots').fetchone()[0])
            self.assertEqual(metadata['request']['fundClassCode'],'LU2156566916')
            self.assertEqual(metadata['request']['organizeCode'],'043')
            raw_file=root/metadata['raw_file']
            self.assertEqual(m.sha(m.gzip.decompress(raw_file.read_bytes())),metadata['raw_sha256'])
            self.assertEqual(m.audit_store(root)['actual_nav_rows_verified'],1)
            with sqlite3.connect(root/'research.sqlite') as db:
                db.execute("UPDATE research_observations SET nav_per_unit='7.791001'")
            with self.assertRaisesRegex(ValueError,'PRICE_ROWS_DIFFER_FROM_RAW'):m.audit_store(root)
    def test_auth_block_stops_without_sending_more_classes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'queue.json').write_text(json.dumps([{'class_key':str(i),'status':'QUEUED','code':str(i),'organize_code':'043','currency':'USD'} for i in range(5)]))
            with patch.object(m,'fetch',return_value=(429,b'{"message":"limit"}')) as source:
                r=m.import_queue(root,workers=1)
            self.assertEqual(source.call_count,1)
            self.assertEqual(r['research_nav_rows_persisted'],0)
    def test_batch_cannot_exceed_one_hundred(self):
        with self.assertRaises(ValueError):m.import_queue(Path('/does-not-exist'),limit=101)
    def test_paused_job_does_not_make_any_source_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'pause-requested').touch()
            (root/'queue.json').write_text(json.dumps([{'class_key':'first','status':'QUEUED','code':'LU2156566916','organize_code':'043','currency':'USD'}]))
            with patch.object(m,'fetch',side_effect=AssertionError('paused source must not be fetched')) as source:r=m.import_queue(root)
            self.assertEqual(source.call_count,0)
            self.assertEqual(r['paused_reason'],'OPERATOR_PAUSE')
    def test_resume_cannot_replace_catalog_under_existing_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'queue.json').write_text('[]');(root/'tdcc-basic.csv').write_bytes(b'original')
            changed=root/'changed.csv';changed.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'CATALOG_SNAPSHOT_CHANGED'):m.build_queue(root,changed)
    def test_recent_feed_reports_value_conflict_without_revising_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);basic='基金代碼,基金名稱,基金ISIN_CODE,計價幣別\nLU2156566916,Fund,LU2156566916,美元\n'.encode()
            (root/'tdcc-basic.csv').write_bytes(basic)
            q={'class_key':'first','status':'QUEUED','code':'LU2156566916','organize_code':'043','currency':'USD','name':'Fund','isin':'LU2156566916','basic_source_sha256':m.sha(basic)}
            (root/'queue.json').write_text(json.dumps([q]))
            with patch.object(m,'fetch',return_value=(200,self.raw())):m.import_queue(root,workers=1)
            recent=root/'recent-input.csv';recent.write_text('基金代碼,基金名稱,ISINCODE,計價幣別,日期,基金淨值(金額)\nLU2156566916,Fund,LU2156566916,美元,20231018,7.791\n')
            self.assertEqual(m.reconcile_recent_source(root,recent)['same_class_same_date_equal_values'],1)
            with sqlite3.connect(root/'research.sqlite') as db:db.execute("UPDATE research_observations SET nav_per_unit='7.792000'")
            result=m.reconcile_recent_source(root,recent)
            self.assertEqual(result['conflicting_values'],1)
            self.assertEqual(result['rows_modified_or_backfilled'],0)
            with sqlite3.connect(root/'research.sqlite') as db:self.assertEqual(db.execute('SELECT nav_per_unit FROM research_observations').fetchone()[0],'7.792000')

if __name__=='__main__':unittest.main()
