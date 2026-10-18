from pathlib import Path
from tempfile import TemporaryDirectory
from hashlib import sha256
import json,sqlite3,unittest
from adviser_portal import domestic_catalog_candidates as m

def catalog(rows=None):
    return {'checked_at_utc':'2026-10-16T14:48:00+00:00','instruments':rows or [
      {'uid':'one','source':'sitca_domestic_funds','category':'domestic_fund_share_class','code':'CLASS_A','name':'基金A累積型','currency':'新台幣'}]}
def page(names=None):
    names=names or [('T0001A','taxA','基金A累積型<br>(本基金配息來源可能為本金)','TWD')]
    head=''.join('<td>'+h+'</th>' for h in m.HEADERS)
    rows=''.join('<tr>'+''.join('<td>'+v+'</td>' for v in ['AJ2','A0001','公司',cert,tax,name,curr,'10','9','1'])+'</tr>' for cert,tax,name,curr in names)
    return (f'<input name="{m.DATE_FIELD}" value="20261014"><table><tr>'+head+'</tr>'+rows+'</table>').encode()

class CandidateTests(unittest.TestCase):
    def test_only_explicit_linebreak_separates_regulatory_note(self):
        found,review,count,day=m.current_candidates(page(),catalog(),minimum_rows=1)
        self.assertEqual(found[0]['legal_name'],'基金A累積型');self.assertFalse(found[0]['identity_approved'])
        raw=page([('T0001A','taxA','基金A累積型(本基金配息來源可能為本金)','TWD')])
        self.assertFalse(m.current_candidates(raw,catalog(),minimum_rows=1)[0])
    def test_class_or_currency_difference_never_matches(self):
        for name,curr in [('基金A配息型','TWD'),('基金A累積型','USD'),('基金A<br>(B累積級別)','TWD')]:
            self.assertFalse(m.current_candidates(page([('T0001A','taxA',name,curr)]),catalog(),minimum_rows=1)[0])
    def test_duplicate_source_candidates_reject_both(self):
        raw=page([('T0001A','taxA','基金A累積型','TWD'),('T0001B','taxB','基金A累積型','TWD')])
        found,review,_,_=m.current_candidates(raw,catalog(),minimum_rows=1)
        self.assertEqual(found,[]);self.assertEqual(len(review),2)
    def test_current_catalog_requires_nearby_dated_source(self):
        raw=page().replace(b'20261014',b'20231014')
        with self.assertRaisesRegex(ValueError,'DATE_MISMATCH'):m.current_candidates(raw,catalog(),minimum_rows=1)
    def test_metadata_linking_never_rewrites_prices_or_approves_identity(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);database=root/'research.sqlite';raw=page();digest=sha256(raw).hexdigest()
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE source_snapshots(raw_sha256 TEXT PRIMARY KEY,requested_date TEXT)')
                db.execute('INSERT INTO source_snapshots VALUES(?,?)',(digest,'2026-10-14'))
                db.execute('CREATE TABLE research_observations(raw_sha256 TEXT,source_class_key TEXT,certificate_code TEXT,tax_id TEXT,currency TEXT,status TEXT,nav_per_unit TEXT)')
                db.execute('INSERT INTO research_observations VALUES(?,?,?,?,?,?,?)',(digest,'key','T0001A','taxA','TWD','UNAPPROVED_SOURCE_OBSERVATION','10.0000'))
            r=m.build_candidates(database,raw,json.dumps(catalog()).encode(),root,minimum_rows=1)
            self.assertEqual(r['candidate_linked_observations'],1);self.assertEqual(r['identities_approved'],0)
            self.assertEqual(r['historical_price_rows_rewritten'],0)
            with sqlite3.connect(database) as db:self.assertEqual(db.execute('SELECT nav_per_unit FROM research_observations').fetchone()[0],'10.0000')
            with self.assertRaisesRegex(ValueError,'NOT_BOUND'):m.build_candidates(database,raw.replace(b'taxA',b'taxB'),json.dumps(catalog()).encode(),root,minimum_rows=1)

if __name__=='__main__':unittest.main()
