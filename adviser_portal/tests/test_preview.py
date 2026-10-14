import io
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from adviser_portal.engine import (
    SAMPLE_POSITIONS, allocation_plan, retirement_projection, risk_assessment,
)
from adviser_portal.server import Handler


class TestRiskAssessment(unittest.TestCase):
    def test_both_dimensions_limit_rr(self):
        result = risk_assessment({
            "horizon": 3, "emergency_fund": 3, "income_stability": 3,
            "loss_tolerance": 0, "reaction": 0, "knowledge": 0,
        })
        self.assertEqual(result["capacity_score"], 100)
        self.assertEqual(result["willingness_score"], 0)
        self.assertEqual(result["rr"], 1)

    def test_standard_demo_is_rr3(self):
        r = risk_assessment({
            "horizon": 2, "emergency_fund": 2, "income_stability": 2,
            "loss_tolerance": 2, "reaction": 2, "knowledge": 2,
        })
        self.assertEqual(r["rr"], 3)
        self.assertEqual(r["capacity_score"], 67)
        self.assertEqual(r["willingness_score"], 60)
        self.assertAlmostEqual(sum(r["target_allocation"].values()), 1)

    def test_out_of_range_is_rejected(self):
        form = {"horizon": 8, "emergency_fund": 2, "income_stability": 2,
                "loss_tolerance": 2, "reaction": 2, "knowledge": 2}
        with self.assertRaises(ValueError):
            risk_assessment(form)


class TestAllocation(unittest.TestCase):
    def test_no_sales_for_gradual_and_balanced_immediate(self):
        a = allocation_plan({"rr":3, "positions":SAMPLE_POSITIONS, "new_money":100000})
        self.assertEqual(a["total_existing"],500000)
        self.assertEqual(a["new_money"],100000)
        self.assertAlmostEqual(sum(a["gradual_buy_only"].values()),100000)
        self.assertTrue(all(v >= -0.005 for v in a["gradual_buy_only"].values()))
        self.assertAlmostEqual(sum(a["immediate_net_trades"].values()),100000)
        self.assertAlmostEqual(sum(a["target_weights_pct"].values()),100)
        self.assertEqual(a["global_equity_pct_of_existing"],28)
        self.assertEqual(a["tw_equity_pct_of_existing"],18)
        # Technology is a subset of equities, not another asset bucket.
        self.assertLess(a["tech_overlap_pct_of_existing"],50)

    def test_rebalance_zero_cash_flow_sums_to_zero(self):
        a=allocation_plan({"rr":1, "positions":SAMPLE_POSITIONS,"new_money":0})
        self.assertAlmostEqual(sum(a["immediate_net_trades"].values()),0)

    def test_negative_position_rejected(self):
        positions=[dict(SAMPLE_POSITIONS[0],value=-1)]
        with self.assertRaises(ValueError):
            allocation_plan({"rr":3,"positions":positions,"new_money":100})

    def test_unknown_asset_rejected(self):
        positions=[dict(SAMPLE_POSITIONS[0],asset="bitcoin")]
        with self.assertRaises(ValueError):
            allocation_plan({"rr":3,"positions":positions,"new_money":100})

    def test_invalid_tech_for_bond_rejected(self):
        positions=[dict(SAMPLE_POSITIONS[2],tech_pct=90)]
        with self.assertRaises(ValueError):
            allocation_plan({"rr":3,"positions":positions,"new_money":100})


class TestRetirement(unittest.TestCase):
    def test_three_scenarios_and_fees(self):
        a = retirement_projection({
            "initial":500000,"monthly_contribution":12000,"monthly_withdrawal":0,
            "years":10,"target":12000000,"inflation_pct":2,"annual_fee_pct":0.6,
        })
        values=[x["ending_nominal"] for x in a["scenarios"]]
        self.assertEqual(len(values),3)
        self.assertTrue(values[0] < values[1] < values[2])
        self.assertTrue(all(s["ending_today_value"]<s["ending_nominal"] for s in a["scenarios"]))
        self.assertEqual(len(a["scenarios"][0]["yearly_nominal"]),10)

    def test_withdrawals_can_exhaust(self):
        a=retirement_projection({"initial":10000,"monthly_contribution":0,
                                  "monthly_withdrawal":5000,"years":5,"target":200000})
        self.assertTrue(all(x["ending_nominal"]==0 for x in a["scenarios"]))
        self.assertTrue(all(x["exhausted_year"] is not None for x in a["scenarios"]))

    def test_finite_numbers_required(self):
        with self.assertRaises(ValueError):
            retirement_projection({"initial":"Infinity","monthly_contribution":100,
                                   "monthly_withdrawal":0,"years":10,"target":1e7})


class TestHttpPreview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
        cls.base = "http://127.0.0.1:"+str(cls.server.server_address[1])
        cls.thread = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_health_has_no_secret_data(self):
        with urlopen(self.base+"/health",timeout=3) as r:
            payload=json.load(r)
            self.assertEqual(payload["data_mode"],"synthetic_only")
            self.assertFalse(payload["client_records"])
            self.assertEqual(r.headers["X-Frame-Options"],"DENY")

    def test_demo_page_and_assets(self):
        for path in ("/","/app.js","/style.css"):
            with self.subTest(path=path),urlopen(self.base+path,timeout=3) as r:
                self.assertEqual(r.status,200)
                self.assertGreater(len(r.read()),1000)

    def test_client_records_rejected(self):
        for path in ("/api/clients","/api/portfolio/1"):
            with self.subTest(path=path):
                with self.assertRaises(HTTPError) as err:
                    urlopen(self.base+path,timeout=3)
                self.assertEqual(err.exception.code,403)
        req=Request(self.base+"/api/auth/login",data=b'{}',headers={"Content-Type":"application/json"},method="POST")
        with self.assertRaises(HTTPError) as err:
            urlopen(req,timeout=3)
        self.assertEqual(err.exception.code,403)

    def test_live_api_json(self):
        obj={"rr":3,"positions":SAMPLE_POSITIONS,"new_money":50000}
        req=Request(self.base+"/api/allocation",data=json.dumps(obj).encode(),
                    headers={"Content-Type":"application/json"},method="POST")
        with urlopen(req,timeout=3) as r:
            x=json.load(r)
        self.assertEqual(x["after_total"],550000)

    def test_bad_method_or_json_must_not_pass(self):
        req=Request(self.base+"/api/risk",data=b'no',headers={"Content-Type":"text/plain"},method="POST")
        with self.assertRaises(HTTPError) as err:
            urlopen(req,timeout=3)
        self.assertEqual(err.exception.code,415)


if __name__=="__main__":
    unittest.main()
