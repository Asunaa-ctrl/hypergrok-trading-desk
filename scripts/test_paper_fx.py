#!/usr/bin/env python3
"""Offline tests for the public FX reference reader. No live HTTP."""
from __future__ import annotations

import hashlib
import json
import ssl
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import formatdate
from pathlib import Path
import unittest
from unittest.mock import patch

import paper_fx as fx
import paper_sim as sim
from test_paper_sim import ROOT, base_config, book, tape

ECB = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube><Cube time="2026-11-06"><Cube currency="USD" rate="1.1165"/></Cube></Cube>
</gesmes:Envelope>
"""


def iso(epoch):
    moment = datetime.fromtimestamp(float(epoch), timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S") + f".{moment.microsecond:06d}".rstrip("0").rstrip(".") + "Z" if False else fx._stamp(moment)


def fresh_round(**overrides):
    provider = Decimal("1700000000")
    document = {
        "schema_version": 1,
        "kind": "fx_reference_round",
        "as_of_date": "2026-11-08",
        "calls": [
            {
                "url": fx.KRAKEN_TIME_URL,
                "status": 200,
                "headers": [],
                "local_request_utc": iso(provider),
                "local_received_utc": iso(provider + Decimal("0.2")),
                "body": {"error": [], "result": {"unixtime": int(provider), "rfc1123": formatdate(int(provider), usegmt=True)}},
            },
            {
                "url": fx.KRAKEN_TRADES_URL,
                "status": 200,
                "headers": [],
                "local_request_utc": iso(provider + Decimal("0.3")),
                "local_received_utc": iso(provider + Decimal("0.5")),
                "body": {"error": [], "result": {"USDCUSD": [["0.99980000", "10", "1699999980.500", "b", "m", ""]], "last": "1"}},
            },
            {
                "url": fx.ECB_URL,
                "status": 200,
                "headers": [],
                "local_request_utc": iso(provider + Decimal("0.6")),
                "local_received_utc": iso(provider + Decimal("0.9")),
                "body": ECB,
            },
        ],
    }
    for key, value in overrides.items():
        document[key] = value
    return document


class FxReaderTest(unittest.TestCase):
    def test_template_binds_the_selected_plan_and_stores_no_rate(self):
        plan = (ROOT / "scripts" / "fixtures" / "local_paper" / "paper-training-plan.md").read_bytes()
        self.assertEqual(hashlib.sha256(plan).hexdigest(), fx.PLAN_SHA256)
        template = json.loads((ROOT / "scripts" / "fixtures" / "local_paper" / "paper_config_template.json").read_text(encoding="utf-8"))
        self.assertEqual(template["plan_sha256"], fx.PLAN_SHA256)
        report = sim.validate_paper_template(template)
        self.assertFalse(report["ready"])
        self.assertIsNone(report["usable_usdc_quote"])
        self.assertIsNone(template["fx"]["eur_usd"]["rate"])
        self.assertIsNone(template["fx"]["usdc_usd"]["rate"])
        self.assertFalse(template["fx"]["stored_rates"])
        self.assertEqual(template["fx"]["exchange_costs"], "UNMODELLED")
        blob = json.dumps(template)
        self.assertNotIn("1.1165", blob)
        self.assertNotIn("0.9998", blob)

    def test_fresh_round_maps_reference_capital_without_executing_a_conversion(self):
        assessed = fx.assess_round(fresh_round())
        expected = Decimal("100") * Decimal("1.1165") / Decimal("0.9998")
        self.assertEqual(Decimal(assessed["reference_usdc"]), expected)
        self.assertTrue(assessed["carry_forward"])
        self.assertEqual(assessed["carry_forward_days"], 2)
        self.assertFalse(assessed["executable_fx"])
        self.assertFalse(assessed["conversion_executed"])
        self.assertFalse(assessed["accepted_for_run_start"])
        self.assertTrue(assessed["reread_required_at_run_start"])
        self.assertEqual(assessed["exchange_costs"], "UNMODELLED")
        self.assertIsNone(assessed["eur_translation"])
        self.assertIsNone(assessed["usdc_pnl"])
        self.assertEqual(assessed["book_freshness"], "not_implied")
        bound = fx.bind_run_fx(fresh_round(), iso(Decimal("1700000000.4")))
        self.assertTrue(bound["accepted_for_run_start"])
        with self.assertRaises(fx.FxBlocked):
            fx.bind_run_fx(fresh_round(), "2026-11-09T00:00:00Z")

    def test_policy_failures_block_and_drop_the_valuation(self):
        stale = fresh_round()
        stale["calls"][2]["body"] = ECB.replace("2026-11-06", "2026-11-01")
        with self.assertRaises(fx.FxBlocked) as caught:
            fx.assess_round(stale)
        self.assertEqual(caught.exception.code, "reference_stale")
        future = fresh_round()
        future["calls"][2]["body"] = ECB.replace("2026-11-06", "2026-11-09")
        with self.assertRaises(fx.FxBlocked) as caught:
            fx.assess_round(future)
        self.assertEqual(caught.exception.code, "future_reference")
        old_trade = fresh_round()
        old_trade["calls"][1]["body"]["result"]["USDCUSD"][0][2] = "1699999900"
        with self.assertRaises(fx.FxBlocked) as caught:
            fx.assess_round(old_trade)
        self.assertEqual(caught.exception.code, "trade_stale")
        ahead = fresh_round()
        ahead["calls"][1]["body"]["result"]["USDCUSD"][0][2] = "1700000061"
        with self.assertRaises(fx.FxBlocked) as caught:
            fx.assess_round(ahead)
        self.assertEqual(caught.exception.code, "future_trade")
        missing = fresh_round()
        missing["calls"][1]["body"]["result"]["USDCUSD"][0][2] = None
        with self.assertRaises(fx.FxBlocked) as caught:
            fx.assess_round(missing)
        self.assertEqual(caught.exception.code, "time_unknown")
        skewed = fresh_round()
        skewed["calls"][0]["local_request_utc"] = iso(Decimal("1700000006.0"))
        skewed["calls"][0]["local_received_utc"] = iso(Decimal("1700000006.2"))
        skewed["calls"][1]["local_request_utc"] = iso(Decimal("1700000006.3"))
        skewed["calls"][1]["local_received_utc"] = iso(Decimal("1700000006.5"))
        skewed["calls"][2]["local_request_utc"] = iso(Decimal("1700000006.6"))
        skewed["calls"][2]["local_received_utc"] = iso(Decimal("1700000006.8"))
        with self.assertRaises(fx.FxBlocked) as caught:
            fx.assess_round(skewed)
        self.assertEqual(caught.exception.code, "clock_skew")
        slow = fresh_round()
        slow["calls"][2]["local_received_utc"] = iso(Decimal("1700000006"))
        with self.assertRaises(fx.FxBlocked) as caught:
            fx.assess_round(slow)
        self.assertEqual(caught.exception.code, "round_duration")
        ticker = fresh_round()
        ticker["calls"][1]["body"] = {"error": [], "result": {"USDCUSD": {"c": ["0.9998", "1"], "a": ["0.9998", "1", "1"]}}}
        with self.assertRaises(fx.FxBlocked) as caught:
            fx.assess_round(ticker)
        self.assertEqual(caught.exception.code, "not_a_trade")
        book_body = fresh_round()
        book_body["calls"][1]["body"] = {"error": [], "result": {"USDCUSD": {"bids": [["0.9997", "1", 1700000000]], "asks": [["0.9998", "1", 1700000000]]}}}
        with self.assertRaises(fx.FxBlocked) as caught:
            fx.assess_round(book_body)
        self.assertIn(caught.exception.code, {"not_a_trade", "basequote_unknown"})
        other = fresh_round()
        other["calls"][1]["body"] = {"error": [], "result": {"XXBTZUSD": [["1", "1", "1699999990", "b", "m", ""]]}}
        with self.assertRaises(fx.FxBlocked) as caught:
            fx.assess_round(other)
        self.assertEqual(caught.exception.code, "basequote_unknown")
        unsafe = fresh_round()
        unsafe["calls"][2]["body"] = "<!DOCTYPE foo [<!ENTITY x 'y'>]><foo/>"
        with self.assertRaises(fx.FxBlocked) as caught:
            fx.assess_round(unsafe)
        self.assertEqual(caught.exception.code, "xml_unsafe")
        wrong_currency = fresh_round()
        wrong_currency["calls"][2]["body"] = ECB.replace('currency="USD"', 'currency="JPY"')
        with self.assertRaises(fx.FxBlocked) as caught:
            fx.assess_round(wrong_currency)
        self.assertEqual(caught.exception.code, "units_unknown")

    def test_source_manifest_and_network_guard_do_not_become_run_fx(self):
        manifest = ROOT / "scripts" / "fixtures" / "local_paper" / "fx_source_identification_manifest.json"
        with self.assertRaises(fx.FxBlocked) as caught:
            fx.import_round(manifest)
        self.assertEqual(caught.exception.code, "not_run_fx")
        self.assertNotIn("1.1165", str(caught.exception))
        opener = unittest.mock.Mock()
        with self.assertRaises(fx.FxBlocked):
            fx.http_get("https://api.hyperliquid.xyz/info", opener)
        with self.assertRaises(fx.FxBlocked):
            fx.http_get("https://api.kraken.com/0/public/Ticker?pair=USDCUSD", opener)
        with self.assertRaises(fx.FxBlocked):
            fx.http_get(fx.KRAKEN_TRADES_URL, lambda request, timeout: self._response(302, {"Location": "https://evil.example/"}, b"{}"))
        self.assertFalse(opener.called)
        with patch("socket.socket", side_effect=AssertionError("socket")), patch("urllib.request.urlopen", side_effect=AssertionError("urlopen")), patch("sys.stdout"):
            self.assertEqual(fx.main(["--policy"]), 0)
            self.assertEqual(fx.main(["--execute-reference", "--as-of", "2026-11-08", "--output", "unused"]), 2)
        broken = ssl.create_default_context()
        broken.check_hostname = False
        broken.verify_mode = ssl.CERT_NONE
        with patch("paper_fx.ssl.create_default_context", return_value=broken):
            with self.assertRaises(fx.FxBlocked):
                fx.build_opener()

    def _response(self, status, headers, body, url=fx.KRAKEN_TRADES_URL):
        class Response:
            def __init__(self):
                self.status = status
                self.headers = headers
                self._body = body
            def read(self, _limit):
                return self._body
            def geturl(self):
                return url
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
        return Response()

    def test_mocked_execute_and_run_binding_keep_pnl_separate(self):
        bodies = {
            fx.KRAKEN_TIME_URL: json.dumps({"error": [], "result": {"unixtime": 1700000000, "rfc1123": formatdate(1700000000, usegmt=True)}}).encode(),
            fx.KRAKEN_TRADES_URL: json.dumps({"error": [], "result": {"USDCUSD": [["0.99980000", "10", 1699999980.5, "b", "m", ""]], "last": "1"}}).encode(),
            fx.ECB_URL: ECB.encode(),
        }
        def opener(request, timeout):
            self.assertEqual(request.get_method(), "GET")
            self.assertNotIn("Authorization", {key.title(): value for key, value in request.header_items()} if False else request.headers)
            return self._response(200, {}, bodies[request.full_url], request.full_url)
        clock_values = iter([
            datetime.fromtimestamp(1700000000, timezone.utc),
            datetime.fromtimestamp(1700000000.2, timezone.utc),
            datetime.fromtimestamp(1700000000.3, timezone.utc),
            datetime.fromtimestamp(1700000000.5, timezone.utc),
            datetime.fromtimestamp(1700000000.6, timezone.utc),
            datetime.fromtimestamp(1700000000.9, timezone.utc),
        ])
        document = fx.capture_reference(opener, "2026-11-08", lambda: next(clock_values), monotonic=lambda: 0)
        self.assertEqual(Decimal(document["assessment"]["reference_usdc"]), Decimal("100") * Decimal("1.1165") / Decimal("0.9998"))
        self.assertFalse(document["assessment"]["accepted_for_run_start"])
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            args_output = Path(temporary) / "fx-round"
            with patch("socket.socket", side_effect=AssertionError("socket")), patch("urllib.request.urlopen", side_effect=AssertionError("urlopen")), patch("sys.stdout"):
                self.assertEqual(fx.execute_reference("2026-11-08", args_output, opener=opener, clock=lambda: datetime.fromtimestamp(1700000000.2, timezone.utc), monotonic=lambda: 0), 0)
            saved = json.loads((args_output / "reference.json").read_text(encoding="utf-8"))
            self.assertFalse(saved["assessment"]["stored_as_template_default"])
            root = Path(temporary) / "root"
            (root / "fx").mkdir(parents=True)
            (root / "fx" / "reference.json").write_text((args_output / "reference.json").read_text(encoding="utf-8"), encoding="utf-8")
            import paper_dashboard as dash
            status = dash.scan(root)
            self.assertEqual(status["fx"]["reference_usdc"], saved["assessment"]["reference_usdc"])
            self.assertFalse(status["fx"]["accepted_for_run_start"])
            self.assertTrue(status["fx"]["carry_forward"])
            self.assertEqual(status["fx"]["book_freshness"], "not_implied")
            self.assertEqual(status["market"]["candles"], [])
        cfg = base_config()
        cfg["capital"] = {"starting_cash": "100.00", "currency": "EUR"}
        cfg["fx"] = {"status": "reference_valuation", "round": fresh_round(), "run_started_utc": iso(Decimal("1700000000.4"))}
        document = tape([book(1, 1000, [("99", "2")], [("101", "2")])])
        result = sim.replay(cfg, document, sim.decisions_document(cfg, []))
        self.assertEqual(result["report"]["fx"]["status"], "reference_valuation")
        self.assertFalse(result["report"]["fx"]["conversion_executed"])
        self.assertEqual(result["report"]["fx"]["exchange_costs"], "UNMODELLED")
        self.assertIsNone(result["report"]["pnl_breakdown"]["eur_pnl"])
        self.assertIsNone(result["report"]["pnl_breakdown"]["fx_revaluation_eur"])
        self.assertEqual(Decimal(result["report"]["ledger"]["cash"]), Decimal("100") * Decimal("1.1165") / Decimal("0.9998"))
        self.assertEqual(result["report"]["budget"]["currency"], "EUR")
        self.assertEqual(result["report"]["ledger"]["position"], "0")


if __name__ == "__main__":
    unittest.main()
