import json
import sqlite3
import tempfile
import unittest
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch
from adviser_portal import fundclear_history_import as m
from adviser_portal import fundclear_partition_history as p
from adviser_portal import fundclear_gap_history as g


class GapTests(unittest.TestCase):
    def setup_store(self,tmp):
        root=Path(tmp);basic=b'official-frozen-basic';(root/'tdcc-basic.csv').write_bytes(basic)
        q={'class_key':m.sha(b'class'),'status':'QUEUED','code':'LU2156566916','isin':'LU2156566916',
           'name':'Fund','organize_code':'043','currency':'USD','basic_source_sha256':m.sha(basic)}
        (root/'queue.json').write_text(json.dumps([q]));m.init_db(root/'research.sqlite')
        with sqlite3.connect(root/'research.sqlite') as db:db.execute('INSERT INTO acquisition VALUES(?,?,?)',(q['class_key'],'SOURCE_REQUIRES_REVIEW',json.dumps({**q,'http_status':500})))
        def source(path,body,spacing=1):
            if body['startDate']=='2023/10/18':return 500,b'{"message":"unavailable"}'
            return 200,json.dumps({'fundCurr':'USD','tableList':[{'navTxnDate':body['startDate'],'navValue':'7.791000'}]}).encode()
        with patch.object(m,'fetch',side_effect=source):result=p.import_one(root,q)
        return root,q,result

    def prices(self,root):
        with sqlite3.connect(root/'research.sqlite') as db:return db.execute('SELECT * FROM research_observations ORDER BY valuation_date').fetchall()

    def probe(self,root,q,result,valid=3,currency='USD',incomplete=False):
        plan=json.loads((root/result['manifest']).read_text());leaf=next(x for x in plan['leaves'] if x['status']!='READY')
        start,end=date.fromisoformat(leaf['start']),date.fromisoformat(leaf['end']);entries=[]
        for i in range((end-start).days+1):
            day=start+timedelta(days=i);body=p.request_body(q,day,day)
            raw=json.dumps({'fundCurr':currency,'tableList':[{'navTxnDate':body['startDate'],'navValue':'7.791000'}]}).encode() if i<valid else b'{"message":"unavailable"}'
            meta=m.save_raw(root,p.PATH,body,200 if i<valid else 404,raw)
            entries.append({'class_key':q['class_key'],'code':q['code'],'parent_manifest':result['manifest'],'date':day.isoformat(),'source':meta})
            if incomplete:break
        (root/'one-day-gap-probe.json').write_text(json.dumps({'results':entries}));return entries

    def test_append_preserves_old_prices_and_manifests_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,q,result=self.setup_store(tmp);before=self.prices(root);old=(root/result['manifest']).read_bytes()
            self.probe(root,q,result);report=g.apply_probe(root)
            self.assertEqual(report['rows_added'],3);self.assertEqual(report['old_prices_rewritten'],0)
            self.assertEqual((root/result['manifest']).read_bytes(),old)
            after=self.prices(root);self.assertEqual(len(after),len(before)+3);self.assertTrue(set(before)<=set(after))
            audit=m.audit_store(root);self.assertEqual(audit['actual_nav_rows_verified'],len(after));self.assertEqual(audit['imported_classes_verified'],1)
            self.assertEqual(audit['partition_manifests_verified'],2);self.assertFalse(audit['calendar_completeness_approved'])
            review=json.loads((root/'temporal-review.json').read_text())[0]
            self.assertTrue(all(x['from']>'2023-10-20' for x in review['source_request_review_ranges']))
            self.assertEqual(g.apply_probe(root)['rows_added'],0);self.assertEqual(self.prices(root),after)

    def test_incomplete_refinement_does_not_claim_or_import_unqueried_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,q,result=self.setup_store(tmp);before=self.prices(root)
            self.probe(root,q,result,incomplete=True)
            self.assertEqual(g.apply_probe(root)['rows_added'],0);self.assertEqual(self.prices(root),before)

    def test_conflicting_currency_cannot_modify_existing_research(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,q,result=self.setup_store(tmp);before=self.prices(root)
            self.probe(root,q,result,currency='EUR')
            with self.assertRaisesRegex(ValueError,'CURRENCY_MISMATCH'):g.apply_probe(root)
            self.assertEqual(self.prices(root),before)

    def test_probe_outside_recorded_gap_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,q,result=self.setup_store(tmp);before=self.prices(root)
            entries=self.probe(root,q,result);entries[0]['date']='2026-10-15'
            (root/'one-day-gap-probe.json').write_text(json.dumps({'results':entries}))
            with self.assertRaisesRegex(ValueError,'DAY_NOT_IN_SOURCE_REVIEW_RANGE'):g.apply_probe(root)
            self.assertEqual(self.prices(root),before)

    def test_audit_rejects_stale_current_plan_after_new_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,q,result=self.setup_store(tmp)
            with sqlite3.connect(root/'research.sqlite') as db:old=db.execute('SELECT detail_json FROM acquisition').fetchone()[0]
            self.probe(root,q,result);g.apply_probe(root)
            with sqlite3.connect(root/'research.sqlite') as db:db.execute('UPDATE acquisition SET detail_json=?',(old,))
            with self.assertRaisesRegex(ValueError,'CURRENT_PARTITION'):m.audit_store(root)

    def test_offline_gap_batch_also_cannot_exceed_one_hundred_classes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,q,result=self.setup_store(tmp)
            (root/'one-day-gap-probe.json').write_text(json.dumps({'results':[{'class_key':str(i)} for i in range(101)]}))
            with self.assertRaisesRegex(ValueError,'BATCH_LIMIT'):g.apply_probe(root)


if __name__=='__main__':unittest.main()
