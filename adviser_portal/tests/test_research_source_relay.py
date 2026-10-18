from datetime import date
from unittest.mock import patch
import gzip
import unittest
from adviser_portal.research_source_relay import relay_source,RelayError,_lock
TOKEN='test-only-operator-secret-'+'a'*48
TODAY=date(2026,10,18)
class RelayTests(unittest.TestCase):
    def setUp(self):
        p=patch.dict('os.environ',{'ADVISER_RESEARCH_RELAY_TOKEN':TOKEN});p.start();self.addCleanup(p.stop)
    def call(self,query=None,authorization=None,fetcher=None):
        return relay_source(query or {'date':['2023-10-18']},authorization or 'Bearer '+TOKEN,
                            fetcher=fetcher or (lambda d:b'<html>original public response</html>'),today=TODAY)
    def test_unauthorized_never_fetches_source(self):
        for auth in ('wrong','Bearer '+TOKEN+'x','Bearer 中文'):
            with self.assertRaises(RelayError) as e:self.call(authorization=auth,fetcher=lambda d:self.fail('no fetch'))
            self.assertEqual(e.exception.status,403)
    def test_unconfigured_relay_stays_disabled(self):
        with patch.dict('os.environ',{'ADVISER_RESEARCH_RELAY_TOKEN':''}),self.assertRaises(RelayError):self.call()
    def test_exact_original_bytes_are_returned_as_gzip(self):
        self.assertEqual(gzip.decompress(self.call()),b'<html>original public response</html>')
    def test_arbitrary_urls_duplicate_dates_and_future_dates_rejected(self):
        for q in ({'date':['2023-10-18'],'url':['https://other.invalid']},
                  {'date':['2023-10-18','2023-10-19']},{'date':['2026-10-19']},
                  {'date':['2010-01-01']},{'date':['bad']}):
            with self.assertRaises(RelayError) as e:self.call(query=q,fetcher=lambda d:self.fail('no fetch'))
            self.assertEqual(e.exception.status,400)
    def test_upstream_errors_do_not_echo_raw_content(self):
        def fail(d):raise TimeoutError('sensitive upstream text')
        with self.assertRaises(RelayError) as e:self.call(fetcher=fail)
        self.assertEqual(e.exception.status,502)
        self.assertNotIn('sensitive',str(e.exception))
    def test_only_two_source_requests_can_run_at_once(self):
        _lock.acquire();_lock.acquire()
        try:
            with self.assertRaises(RelayError) as e:self.call(fetcher=lambda d:self.fail('busy'))
            self.assertEqual(e.exception.status,503)
        finally:_lock.release();_lock.release()
if __name__=='__main__':unittest.main()
