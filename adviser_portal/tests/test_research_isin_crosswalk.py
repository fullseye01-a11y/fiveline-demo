from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from adviser_portal.research_isin_crosswalk import parse_registry,build_crosswalk


def raw(name='安聯台灣科技基金',isin='TW000T3604Y3',twice=False):
    row='<tr><td>T3604Y　'+name+'</td><td>'+isin+'</td><td>1995/04/20</td><td>CIOGEU</td><td></td></tr>'
    return ('<table><tr><td>有價證券代號及名稱</td><td>國際證券辨識號碼(ISIN Code)</td><td>發行日</td><td>CFICode</td><td>備註</td></tr>'+row+(row if twice else '')+'</table>').encode()


class CrosswalkTests(unittest.TestCase):
    def run_crosswalk(self,source):
        with TemporaryDirectory() as root:
            dbfile=Path(root)/'research.sqlite'
            with sqlite3.connect(dbfile) as db:
                db.execute('CREATE TABLE research_observations (source_class_key TEXT,certificate_code TEXT,name TEXT,currency TEXT)')
                db.executemany('INSERT INTO research_observations VALUES (?,?,?,?)',[
                    ('exact','T3604Y','安聯台灣科技基金','TWD'),('old','T3604Y','原有基金名稱','TWD'),
                    ('none','','安聯台灣科技基金','TWD'),('unlisted','T00000','未列名基金','USD')])
            r=build_crosswalk(dbfile,source,minimum_records=1)
            with sqlite3.connect(dbfile) as db:
                rows=db.execute('SELECT source_class_key,candidate_isin,identity_approved FROM research_isin_candidates').fetchall()
            return r,rows
    def test_exact_code_name_only_yields_unapproved_candidate(self):
        r,rows=self.run_crosswalk(raw())
        self.assertEqual(r['candidate_status_counts']['EXACT_CERTIFICATE_AND_NORMALIZED_NAME_CANDIDATE'],1)
        self.assertEqual(dict((k,isin) for k,isin,approved in rows)['exact'],'TW000T3604Y3')
        self.assertTrue(all(approved==0 for k,isin,approved in rows))
        self.assertFalse(r['fund_inception_inferred_from_issue_date'])
    def test_name_match_without_certificate_never_infers_isin(self):
        r,rows=self.run_crosswalk(raw())
        self.assertIsNone(dict((k,isin) for k,isin,a in rows)['none'])
    def test_duplicate_registry_certificate_stays_ambiguous(self):
        r,rows=self.run_crosswalk(raw(twice=True))
        self.assertEqual(r['candidate_status_counts']['AMBIGUOUS_REGISTRY_CERTIFICATE'],2)
        self.assertTrue(all(isin is None for k,isin,a in rows))
    def test_invalid_checksum_and_partial_registry_rejected(self):
        with self.assertRaisesRegex(ValueError,'CHECKSUM'):parse_registry(raw(isin='TW000T3604Y4'),minimum_records=1)
        with self.assertRaisesRegex(ValueError,'COVERAGE'):parse_registry(raw())
if __name__=='__main__':unittest.main()
