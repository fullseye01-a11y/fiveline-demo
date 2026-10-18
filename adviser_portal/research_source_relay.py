"""Disabled-by-default authenticated relay for one fixed public source/date.

Moves compression close to the official source. No customer records, upload,
DB writes, arbitrary URLs or public price endpoint. Credentials stay server-side.
"""
from datetime import date, timedelta
from threading import BoundedSemaphore,Lock
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode
from urllib.request import Request,urlopen
import gzip
import hmac
from io import BytesIO
import os
import re

from .sitca_history_capture import make_public_fetcher,CaptureError,MAX_BYTES

RELAY_URL='https://fiveline-adviser-alpha-20261018.onrender.com/api/operator/research-source'
_lock=BoundedSemaphore(2)
_pool_lock=Lock()
_fetcher_pool=[]


class RelayError(ValueError):
    def __init__(self,code,status):super().__init__(code);self.status=status


def relay_source(query,authorization,*,fetcher=None,today=None):
    secret=os.environ.get('ADVISER_RESEARCH_RELAY_TOKEN','')
    if (len(secret)<48 or not isinstance(authorization,str) or len(authorization)>200 or
            not hmac.compare_digest(authorization.encode(),('Bearer '+secret).encode())):
        raise RelayError('OPERATOR_ACCESS_REQUIRED',403)
    if (set(query)!={'date'} or len(query['date'])!=1 or
            not re.fullmatch(r'\d{4}-\d{2}-\d{2}',query['date'][0])):
        raise RelayError('EXACT_SOURCE_DATE_REQUIRED',400)
    try:day=date.fromisoformat(query['date'][0])
    except ValueError:raise RelayError('INVALID_SOURCE_DATE',400) from None
    asof=today or date.today()
    if day>asof or day<asof-timedelta(days=2000):raise RelayError('SOURCE_DATE_OUTSIDE_WINDOW',400)
    if not _lock.acquire(blocking=False):raise RelayError('RESEARCH_SOURCE_BUSY',503)
    reusable=None
    try:
        if fetcher is None:
            with _pool_lock:
                reusable=_fetcher_pool.pop() if _fetcher_pool else make_public_fetcher()
            fetcher=reusable
        raw=fetcher(day)
        if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_BYTES:
            raise RelayError('SOURCE_RESPONSE_SIZE_INVALID',502)
        return gzip.compress(raw,compresslevel=6,mtime=0)
    except HTTPError as exc:
        raise RelayError('OFFICIAL_SOURCE_HTTP_FAILURE',exc.code if exc.code in (401,403,429) else 502) from None
    except (URLError,OSError,TimeoutError,CaptureError):
        raise RelayError('OFFICIAL_SOURCE_UNAVAILABLE',502) from None
    finally:
        if reusable is not None:
            with _pool_lock:_fetcher_pool.append(reusable)
        _lock.release()


def make_relay_fetcher(token):
    if not isinstance(token,str) or not 48<=len(token)<=128:raise ValueError('RELAY_TOKEN_INVALID')
    def fetch(day):
        request=Request(RELAY_URL+'?'+urlencode({'date':day.isoformat()}),headers={
            'Authorization':'Bearer '+token,'Accept':'application/gzip','Accept-Encoding':'identity'})
        with urlopen(request,timeout=90) as response:
            if response.status!=200 or response.geturl()!=request.full_url:
                raise CaptureError('OPERATOR_RELAY_TRANSPORT_MISMATCH')
            compressed=response.read(MAX_BYTES+1)
        if not 0<len(compressed)<=MAX_BYTES:raise CaptureError('OPERATOR_RELAY_SIZE_INVALID')
        try:
            with gzip.GzipFile(fileobj=BytesIO(compressed)) as reader:raw=reader.read(MAX_BYTES+1)
        except (OSError,EOFError):raise CaptureError('OPERATOR_RELAY_GZIP_INVALID') from None
        if not 0<len(raw)<=MAX_BYTES:raise CaptureError('OPERATOR_RELAY_EXPANSION_INVALID')
        return raw
    return fetch
