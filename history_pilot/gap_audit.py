"""Candidate gaps based on peer-observed dates, NOT verified exchange holidays."""
from __future__ import annotations
import argparse
import json
import sqlite3
from pathlib import Path


def observed_gap_candidates(db_path: Path) -> dict:
    """Interior gaps vs peers in SAME market only; never assert confirmed gaps."""
    with sqlite3.connect(f'file:{db_path.resolve()}?mode=ro', uri=True) as db:
        market_days={}
        for market,day in db.execute('SELECT DISTINCT market,day FROM approved_bars'):
            market_days.setdefault(market, set()).add(day)
        results=[]
        for market, code, mind, maxd in db.execute("""SELECT market,code,MIN(day),MAX(day)
                FROM approved_bars GROUP BY market,code ORDER BY market,code"""):
            present={d for (d,) in db.execute('SELECT day FROM approved_bars WHERE market=? AND code=?', (market,code))}
            other_days={d for d in market_days[market] if mind<d<maxd and d not in present}
            if other_days:
                results.append({'market':market,'code':code,'candidate_missing_dates':sorted(other_days),
                                'confirmed_missing':False})
    return {'status':'CANDIDATES_ONLY', 'calendar_verified':False,
            'candidate_missing_total':sum(len(x['candidate_missing_dates']) for x in results),
            'symbols':results,
            'limitations':['Suspensions, holidays, trading calendars and listing dates require official verification',
                           'Missing leading/trailing dates cannot be inferred from observed market days']}


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--db',type=Path,required=True)
    args=p.parse_args()
    print(json.dumps(observed_gap_candidates(args.db),ensure_ascii=False,indent=2))
