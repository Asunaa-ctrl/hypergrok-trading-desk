#!/usr/bin/env python3
"""Synthetic engineering tests for the local paper simulator. Not market evidence."""
from __future__ import annotations

import copy
import hashlib
import json
import socket
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import paper_sim as sim

ROOT = Path(__file__).resolve().parents[1]
HOUR = sim.HOUR_MS
JUDGE = "a9a5e3d6-724d-44ab-bb6f-ce99f55ac11b"


def base_config():
    return {
        "schema_version": 1,
        "mode": "local_paper",
        "run_id": "run-fixture",
        "evidence_class": "synthetic_engineering_fixture",
        "claims_market_evidence": False,
        "source_network": "offline",
        "requires_complete_coverage": False,
        "research_bundle": None,
        "market": {
            "coin": "ETH", "dex": None, "product": "linear_perp",
            "margin_model": "cash_secured_no_leverage", "quote": "USDC",
            "sz_decimals": 4, "min_notional_usd": "10", "allow_dust_reduce_only": False,
        },
        "capital": {"starting_cash": "10000", "currency": "USDC"},
        "risk": {"max_position_base": "5", "max_notional_usd": "20000", "max_loss_usd": "500"},
        "fees": {
            "taker_rate": "0.00045", "maker_rate": "0.00015", "scenario_id": "fixture-rate",
            "scenario_note": "synthetic explicit rate, not an account tier",
        },
        "execution": {
            "latency_ms": 0, "stale_book_ms": 1000, "participation_ratio": "1", "cancel_latency_ms": 0,
            "maker_model": "conservative_back_of_visible_queue", "incomplete_fill": "partial_then_cancel",
            "funding_requirement": "exact_oracle_only",
        },
        "evaluation": {
            "train": {"start_ms": 0, "end_ms": 10_000_000},
            "validation": {"start_ms": 20_000_000, "end_ms": 30_000_000},
            "holdout": {"start_ms": 40_000_000, "end_ms": 50_000_000},
            "embargo_ms": 0, "trial_id": "trial-fixture", "prior_trial_count": 2,
            "prior_holdout_access_count": 0,
        },
        "strategy": {
            "id": "fixture-rules", "rules_ref": "synthetic rules label, not opened",
            "rules_hash": "a" * 64, "interface": "tape_intents",
        },
        "protection": {
            "selected": True, "mode": "recorded_exits_only", "policy_id": "fixture-exits",
            "priority": "before_discretionary", "audit": "journal_before_action",
            "risk_treatment": "hard_limits_still_bind",
        },
        "judge": {"declared_id": JUDGE, "role_ref": "desk-lead", "authority": "unauthenticated_string"},
        "decision_source": "recorded_envelopes",
    }


def tape(events):
    return {"schema_version": 1, "evidence_class": "synthetic_engineering_fixture", "label": "synthetic engineering fixture, not market evidence", "events": events}


def book(seq, time_ms, bids, asks, depth="l2"):
    return {"sequence": seq, "type": "book", "exchange_time_ms": time_ms, "local_receipt_ms": time_ms, "coin": "ETH", "depth": depth,
            "bids": [{"px": px, "sz": sz} for px, sz in bids], "asks": [{"px": px, "sz": sz} for px, sz in asks]}


def intent(seq, time_ms, decision_id="D1", side="buy", px="101", sz="1", tif="ioc", reduce_only=False, client="c1", expire=None):
    return {
        "sequence": seq, "type": "intent", "exchange_time_ms": time_ms, "local_receipt_ms": time_ms,
        "decision_time_ms": time_ms, "decision_id": decision_id, "client_id": client, "coin": "ETH",
        "side": side, "time_in_force": tif, "order_type": "limit", "px": px, "sz": sz,
        "reduce_only": reduce_only, "expire_ms": expire,
    }


def envelope(cfg, document, event, *, verdict="APPROVE", risk="PASS", state=None, as_of=1, evidence=None, expires=None, unavailable=None, research=None, explanation="Synthetische Entscheidung, kein Marktbeleg.", reason_codes=None, freshness="pass"):
    state = state or sim.binding_hash(Decimal("10000"), Decimal(0), None, None, [], [], [])
    return sim.build_envelope(
        config=cfg, tape=document, intent=event, verdict=verdict, risk_verdict=risk,
        risk_reason="synthetic risk note", risk_gates=["synthetic-gate"], risk_freshness=freshness,
        risk_reservation=None, expected_state_hash=state, as_of_sequence=as_of,
        decision_id=event["decision_id"], proposal_id="P-1", parent_id=None,
        kind="exit" if event["reduce_only"] else "entry", explanation=explanation,
        reason_codes=reason_codes or (["gates_passed"] if verdict == "APPROVE" else ["recorded_reject"]),
        research=research or {"status": "not_applicable", "reason": "fixture rules declare research irrelevant"},
        expires_at_ms=event["decision_time_ms"] + 10_000 if expires is None else expires,
        unavailable=[] if unavailable is None else unavailable,
        evidence_sequences=[] if evidence is None else evidence,
    )


def run(events, envs, cfg=None, journal=None):
    cfg = cfg or base_config()
    document = tape(events)
    if callable(envs):
        envs = envs(cfg, document)
    return sim.replay(cfg, document, sim.decisions_document(cfg, envs), journal=journal)


def cash_of(report):
    return Decimal(report["ledger"]["cash"])


class SimulatorTest(unittest.TestCase):
    def test_schema_and_cli_are_offline(self):
        self.assertEqual(sim.schema()["network_access"], "none")
        self.assertFalse(sim.schema()["recommended_role"]["runtime_integration"])
        self.assertFalse(sim.schema()["recommended_role"]["authenticated"])
        with patch("socket.socket", side_effect=AssertionError("socket")), patch("socket.create_connection", side_effect=AssertionError("socket")), patch("sys.stdout"):
            self.assertEqual(sim.main(["--schema"]), 0)

    def test_missing_capital_risk_and_protection_fail_closed(self):
        for key in ("capital", "risk", "fees", "protection", "judge"):
            cfg = base_config()
            cfg.pop(key)
            with self.assertRaises(sim.PaperError) as caught:
                sim.validate_config(cfg)
            self.assertEqual(caught.exception.code, "config_incomplete")
            self.assertIn(key, str(caught.exception))

    def test_unsupported_margin_product_and_live_judge_are_rejected(self):
        cfg = base_config()
        cfg["market"]["margin_model"] = "cross"
        with self.assertRaises(sim.PaperError) as caught:
            sim.validate_config(cfg)
        self.assertEqual(caught.exception.code, "unsupported")
        cfg = base_config()
        cfg["market"]["product"] = "spot"
        with self.assertRaises(sim.PaperError) as caught:
            sim.validate_config(cfg)
        self.assertEqual(caught.exception.code, "unsupported")
        cfg = base_config()
        cfg["decision_source"] = "live_bot"
        with self.assertRaises(sim.PaperError) as caught:
            sim.validate_config(cfg)
        self.assertIn("live", str(caught.exception))
        cfg = base_config()
        cfg["judge"]["authority"] = "authenticated"
        with self.assertRaises(sim.PaperError) as caught:
            sim.validate_config(cfg)
        self.assertEqual(caught.exception.code, "unsupported")

    def test_network_guard_refuses_exchange_account_and_signing_before_transport(self):
        opener = unittest.mock.Mock() if False else self._opener()
        client = sim.PublicInfoClient("https://api.hyperliquid.xyz")
        with self.assertRaises(sim.NetworkDenied):
            client.submit("/" + "exchange", {"type": "meta"}, opener)
        self.assertFalse(opener.called)
        for payload in (
            {"type": "clearinghouseState", "user": "0xabc"},
            {"type": "userFills", "user": "0xabc"},
            {"type": "meta", "signature": "00"},
            {"type": "l2Book", "coin": "ETH", "user": "0xabc"},
        ):
            with self.assertRaises(sim.NetworkDenied):
                client.submit("/info", payload, opener)
        self.assertFalse(opener.called)
        custom = sim.PublicInfoClient("https://example.invalid")
        with self.assertRaises(sim.NetworkDenied):
            custom.submit("/info", {"type": "meta"}, opener)
        self.assertFalse(opener.called)
        client.submit("/info", {"type": "meta"}, opener)
        self.assertEqual(opener.call_args.args[0], "https://api.hyperliquid.xyz/info")
        with self.assertRaises(sim.NetworkDenied):
            sim.validate_subscription({"type": "userEvents", "user": "0xabc"})
        with self.assertRaises(sim.NetworkDenied):
            sim.validate_subscription({"type": "allMids"})
        self.assertEqual(sim.validate_subscription({"type": "l2Book", "coin": "ETH"}), "l2Book")
        with self.assertRaises(sim.PaperError):
            sim.normalize_public_trade({"coin": "ETH", "side": "B", "px": "1", "sz": "1", "users": ["0xsecret"]})

    def _opener(self):
        return unittest.mock.Mock(return_value={"ok": True})

    def test_replay_does_not_touch_the_network_or_environment(self):
        source = Path(sim.__file__).read_text(encoding="utf-8")
        for banned in ("os.environ", "getenv", "eth_account", "websocket"):
            self.assertNotIn(banned, source)
        sentinel = "SENTINEL-SECRET-VALUE"
        import os
        os.environ["HYPERLIQUID_PRIVATE_KEY"] = sentinel
        try:
            result = self._buy()
        finally:
            os.environ.pop("HYPERLIQUID_PRIVATE_KEY", None)
        blob = json.dumps(result["report"])
        self.assertNotIn(sentinel, blob)
        self.assertFalse(result["report"]["judge_provenance"]["authenticated"])
        self.assertFalse(result["report"]["runtime_bot_integration"])
        self.assertEqual(result["report"]["judge_provenance"]["recommended_bot_id"], JUDGE)
        self.assertTrue(result["report"]["judge_provenance"]["declared_id_matches_recommendation"])
        self.assertIn("does not authenticate", result["report"]["judge_provenance"]["note"])

    def _buy(self, **kwargs):
        events = [book(1, 1000, [("99", "5")], [("101", "5")]), intent(2, 1000, **kwargs)]
        return run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1])])

    def test_buy_and_sell_use_the_touching_side_and_walk_depth(self):
        buy = self._buy()
        self.assertEqual(buy["report"]["orders"]["filled"], 1)
        self.assertEqual(buy["report"]["ledger"]["position"], "1")
        self.assertEqual(Decimal(buy["report"]["fees"]), Decimal("101") * Decimal("0.00045"))
        self.assertNotEqual(buy["report"]["decisions"][0]["reason_de"], "gates_passed")
        self.assertIn("keine Strategievalidierung", buy["report"]["decisions"][0]["reason_de"])
        events = [book(1, 1000, [("100", "1"), ("99", "1")], [("102", "1"), ("103", "2")]), intent(2, 1000, px="103", sz="3")]
        walked = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1])])
        fills = [item for item in json.loads(walked["files"]["orders.json"]) if item["client_id"] == "c1"][0]["fills"]
        self.assertEqual([item["px"] for item in fills], ["102", "103"])
        self.assertEqual(sum((Decimal(item["qty"]) for item in fills), Decimal(0)), Decimal(3))
        sell_events = [book(1, 1000, [("100", "2"), ("99", "2")], [("105", "2")]), intent(2, 1000, side="sell", px="99", sz="3", decision_id="Dshort")]
        # Opening a short is an entry. Use a long exit after a buy instead for the bid walk.
        events = [
            book(1, 1000, [("100", "4")], [("101", "4")]),
            intent(2, 1000, px="101", sz="2"),
            book(3, 2000, [("100", "1"), ("99", "2")], [("110", "2")]),
            intent(4, 2000, side="sell", px="99", sz="2", reduce_only=True, decision_id="DX", client="c2"),
        ]
        def envs(cfg, document):
            first = envelope(cfg, document, events[1], evidence=[1])
            state = sim.binding_hash(Decimal("10000") - Decimal("202") - Decimal("202") * Decimal("0.00045"), Decimal(2), Decimal("101"), None, [], ["D1"], [])
            second = envelope(cfg, document, events[3], state=state, as_of=3, evidence=[3])
            return [first, second]
        result = run(events, envs)
        sale = [item for item in json.loads(result["files"]["orders.json"]) if item["client_id"] == "c2"][0]
        self.assertEqual([item["px"] for item in sale["fills"]], ["100", "99"])
        self.assertTrue(result["report"]["reconciliation"]["ok"])
        self.assertEqual(result["report"]["ledger"]["position"], "0")

    def test_partial_reject_cancel_participation_and_latency(self):
        events = [book(1, 1000, [("99", "1")], [("101", "1"), ("102", "1")]), intent(2, 1000, px="102", sz="3")]
        partial = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1])])
        order = json.loads(partial["files"]["orders.json"])[0]
        self.assertEqual(order["status"], "cancelled")
        self.assertEqual(len(order["fills"]), 2)
        self.assertEqual(sum(Decimal(item["fee"]) for item in order["fills"]), Decimal("101") * Decimal("0.00045") + Decimal("102") * Decimal("0.00045"))
        cfg = base_config()
        cfg["execution"]["incomplete_fill"] = "reject_if_incomplete"
        rejected = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1])], cfg=cfg)
        self.assertEqual(json.loads(rejected["files"]["orders.json"])[0]["status"], "rejected")
        self.assertEqual(rejected["report"]["ledger"]["cash"], "10000")
        cfg = base_config()
        cfg["execution"]["participation_ratio"] = "0.5"
        events = [book(1, 1000, [("99", "1")], [("101", "4")]), intent(2, 1000, px="101", sz="4")]
        participated = run(events, lambda c, document: [envelope(c, document, events[1], evidence=[1])], cfg=cfg)
        taken = sum(Decimal(item["qty"]) for item in json.loads(participated["files"]["orders.json"])[0]["fills"])
        self.assertEqual(taken, Decimal(2))
        cfg = base_config()
        cfg["execution"]["latency_ms"] = 500
        events = [
            book(1, 1000, [("99", "2")], [("101", "2")]),
            intent(2, 1000, px="130"),
            book(3, 1600, [("99", "2")], [("110", "2")]),
        ]
        later = run(events, lambda c, document: [envelope(c, document, events[1], evidence=[1], expires=5000)], cfg=cfg)
        self.assertEqual(json.loads(later["files"]["orders.json"])[0]["fills"][0]["px"], "110")
        events = [book(1, 1000, [("99", "2")], [("101", "2")]), intent(2, 1000, client="x", decision_id="C"), {"sequence": 3, "type": "cancel", "exchange_time_ms": 1000, "local_receipt_ms": 1000, "decision_time_ms": 1000, "client_id": "x"}]
        # IOC fills before the cancel at the same time because activation runs before cancels.
        events = [
            book(1, 1000, [("90", "2")], [("110", "2")]),
            intent(2, 1000, px="90", tif="gtc", client="x", decision_id="C"),
            {"sequence": 3, "type": "cancel", "exchange_time_ms": 1100, "local_receipt_ms": 1100, "decision_time_ms": 1100, "client_id": "x"},
        ]
        cancelled = run(events, lambda c, document: [envelope(c, document, events[1], evidence=[1])])
        self.assertEqual(json.loads(cancelled["files"]["orders.json"])[0]["status"], "cancelled")
        self.assertEqual(json.loads(cancelled["files"]["orders.json"])[0]["fills"], [])

    def test_stale_gap_reconnect_duplicate_and_out_of_order(self):
        events = [book(1, 0, [("99", "2")], [("101", "2")]), intent(2, 2000, px="101")]
        stale = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1], expires=5000)])
        self.assertEqual(stale["report"]["decisions"][0]["reason_code"], "stale_book")
        self.assertEqual(stale["report"]["ledger"]["position"], "0")
        events = [book(1, 1000, [("99", "2")], [("101", "2")]), {"sequence": 2, "type": "gap", "exchange_time_ms": 1000, "local_receipt_ms": 1000, "start_ms": 1000, "end_ms": 1000}, intent(3, 1000, px="101")]
        gapped = run(events, lambda cfg, document: [envelope(cfg, document, events[2], evidence=[1])])
        self.assertEqual(gapped["report"]["decisions"][0]["reason_code"], "book_untrusted")
        self.assertEqual(gapped["report"]["data_quality"]["gaps"], 1)
        events = [book(1, 1000, [("99", "2")], [("101", "2")]), {"sequence": 2, "type": "reconnect", "exchange_time_ms": 1000, "local_receipt_ms": 1000}, intent(3, 1000, px="101")]
        reconnect = run(events, lambda cfg, document: [envelope(cfg, document, events[2], evidence=[1])])
        self.assertEqual(reconnect["report"]["decisions"][0]["reason_code"], "book_untrusted")
        self.assertEqual(reconnect["report"]["data_quality"]["reconnects"], 1)
        first = book(1, 1000, [("99", "2")], [("101", "2")])
        events = [first, dict(first, sequence=2, local_receipt_ms=1001), intent(3, 1000, px="101")]
        duplicate = run(events, lambda cfg, document: [envelope(cfg, document, events[2], evidence=[1])])
        self.assertEqual(duplicate["report"]["data_quality"]["duplicates"], 1)
        self.assertEqual(duplicate["report"]["ledger"]["position"], "1")
        events = [book(1, 2000, [("99", "2")], [("101", "2")]), book(2, 1000, [("50", "2")], [("60", "2")]), intent(3, 2000, px="101")]
        ordered = run(events, lambda cfg, document: [envelope(cfg, document, events[2], evidence=[1], expires=5000)])
        self.assertEqual(ordered["report"]["data_quality"]["out_of_order"], 1)
        self.assertEqual(json.loads(ordered["files"]["orders.json"])[0]["fills"][0]["px"], "101")

    def test_precision_min_notional_reduce_only_and_capacity(self):
        events = [book(1, 1000, [("99", "2")], [("101", "2")]), intent(2, 1000, px="101.123", sz="1")]
        bad_px = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1])])
        self.assertEqual(bad_px["report"]["decisions"][0]["reason_code"], "precision")
        events = [book(1, 1000, [("99", "2")], [("100", "2")]), intent(2, 1000, px="100", sz="0.05")]
        small = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1])])
        self.assertEqual(small["report"]["decisions"][0]["reason_code"], "min_notional")
        events = [book(1, 1000, [("99", "2")], [("101", "2")]), intent(2, 1000, side="sell", px="99", sz="1", reduce_only=True, decision_id="DX")]
        flat = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1])])
        self.assertEqual(flat["report"]["decisions"][0]["reason_code"], "reduce_only")
        cfg = base_config()
        cfg["risk"]["max_position_base"] = "0.5"
        cfg["execution"]["incomplete_fill"] = "reject_if_incomplete"
        events = [book(1, 1000, [("99", "2")], [("101", "2")]), intent(2, 1000, px="101", sz="1")]
        capped = run(events, lambda c, document: [envelope(c, document, events[1], evidence=[1])], cfg=cfg)
        self.assertEqual(capped["report"]["ledger"]["position"], "0")
        self.assertIn(capped["report"]["decisions"][0]["reason_code"], ("capacity", "insufficient_depth", "insufficient_cash"))

    def test_funding_sign_position_change_and_missing_oracle_or_hour(self):
        events = [
            book(1, 1000, [("99", "2")], [("100", "2")]),
            intent(2, 1000, px="100", sz="2"),
            {"sequence": 3, "type": "funding", "exchange_time_ms": HOUR + 20, "local_receipt_ms": HOUR + 20, "coin": "ETH", "funding_rate": "0.0001", "oracle_px": "100", "premium": "0.0001"},
        ]
        paid = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1], expires=HOUR + 50)])
        self.assertEqual(paid["report"]["funding"]["status"], "exact")
        self.assertEqual(paid["report"]["funding"]["posted"], "-0.02")
        events[2]["funding_rate"] = "-0.0001"
        # rebuild via a fresh list so the envelope matches the tape
        events = [
            book(1, 1000, [("99", "2")], [("100", "2")]),
            intent(2, 1000, px="100", sz="1"),
            {"sequence": 3, "type": "funding", "exchange_time_ms": HOUR + 20, "local_receipt_ms": HOUR + 20, "coin": "ETH", "funding_rate": "-0.0001", "oracle_px": "50", "premium": "0"},
        ]
        credit = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1], expires=HOUR + 50)])
        self.assertEqual(credit["report"]["funding"]["posted"], "0.005")
        events = [
            book(1, 1000, [("99", "2")], [("100", "2")]),
            intent(2, 1000, px="100"),
            {"sequence": 3, "type": "funding", "exchange_time_ms": HOUR + 20, "local_receipt_ms": HOUR + 20, "coin": "ETH", "funding_rate": "0.0001", "oracle_px": None, "premium": None},
        ]
        missing = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1], expires=HOUR + 50)])
        self.assertEqual(missing["report"]["funding"]["status"], "unavailable")
        self.assertIsNone(missing["report"]["funding"]["claimed"])
        self.assertEqual(missing["report"]["funding"]["posted"], "0")
        self.assertFalse(missing["accepted"])
        events = [book(1, 1000, [("99", "2")], [("100", "2")]), intent(2, 1000, px="100"), book(3, HOUR + 1000, [("99", "2")], [("100", "2")])]
        absent = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1], expires=HOUR + 5000)])
        self.assertEqual(absent["report"]["funding"]["status"], "unavailable")
        self.assertIsNone(absent["report"]["funding"]["claimed"])
        self.assertNotEqual(absent["report"]["funding"]["claimed"], "0")

    def test_no_lookahead_open_candle_or_intrabar_fill(self):
        events = [book(1, 5000, [("99", "2")], [("101", "2")]), intent(2, 1000, px="101")]
        with self.assertRaises(sim.PaperError) as caught:
            run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1], expires=9000)])
        self.assertEqual(caught.exception.code, "lookahead")
        events = [
            book(1, 1000, [("99", "2")], [("101", "2")]),
            {"sequence": 2, "type": "candle", "exchange_time_ms": 1000, "local_receipt_ms": 1000, "coin": "ETH", "t": 0, "T": 999, "o": "50", "h": "50", "l": "50", "c": "50", "v": "1", "n": 1, "closed": False},
            intent(3, 1000, px="101"),
        ]
        result = run(events, lambda cfg, document: [envelope(cfg, document, events[2], evidence=[1])])
        self.assertEqual(result["report"]["data_quality"]["open_candles_ignored"], 1)
        self.assertEqual(json.loads(result["files"]["orders.json"])[0]["fills"][0]["px"], "101")
        events = [
            book(1, 1000, [("100", "5")], [("110", "5")]),
            intent(2, 1000, px="100", tif="gtc"),
            {"sequence": 3, "type": "candle", "exchange_time_ms": 2000, "local_receipt_ms": 2000, "coin": "ETH", "t": 1000, "T": 1999, "o": "100", "h": "130", "l": "70", "c": "90", "v": "3", "n": 2, "closed": True},
        ]
        touched = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1], expires=5000)])
        self.assertGreaterEqual(touched["report"]["data_quality"]["resting_orders_touched_by_candle_range"], 1)
        self.assertEqual(touched["report"]["data_quality"]["intrabar_path"], "not_observed")
        self.assertEqual(json.loads(touched["files"]["orders.json"])[0]["fills"], [])
        events = [book(1, 1000, [("99", "2")], [("101", "2")]), intent(2, 1000, px="101"), book(3, 5000, [("99", "2")], [("50", "2")])]
        leaked = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[3], as_of=3, expires=9000)])
        self.assertEqual(leaked["report"]["decisions"][0]["reason_code"], "lookahead")
        self.assertEqual(leaked["report"]["ledger"]["position"], "0")

    def test_decision_gates_risk_expiry_duplicate_race_and_journal(self):
        events = [book(1, 1000, [("99", "2")], [("101", "2")]), intent(2, 1000, px="101")]
        veto = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1], risk="REJECT")])
        self.assertEqual(veto["report"]["decisions"][0]["recorded_verdict"], "APPROVE")
        self.assertEqual(veto["report"]["decisions"][0]["reason_code"], "risk_veto")
        self.assertIn("Risiko-Veto", veto["report"]["decisions"][0]["reason_de"])
        self.assertEqual(veto["report"]["ledger"]["position"], "0")
        rejected = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1], verdict="REJECT")])
        self.assertEqual(rejected["report"]["decisions"][0]["recorded_verdict"], "REJECT")
        self.assertFalse(rejected["report"]["decisions"][0]["action_taken"])
        missing = run(events, lambda cfg, document: [])
        self.assertEqual(missing["report"]["decisions"][0]["reason_code"], "missing_decision")
        self.assertIn("kein neues Urteil", missing["report"]["decisions"][0]["reason_de"])
        expired = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1], expires=500)])
        self.assertEqual(expired["report"]["decisions"][0]["reason_code"], "expired")
        unavailable = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1], unavailable=["oracle"])])
        self.assertEqual(unavailable["report"]["decisions"][0]["reason_code"], "unavailable_inputs")
        events = [
            book(1, 1000, [("99", "5")], [("101", "5")]),
            intent(2, 1000, px="101", decision_id="D1", client="c1"),
            intent(3, 1000, px="101", decision_id="D2", client="c2"),
        ]
        def raced(cfg, document):
            state = sim.binding_hash(Decimal("10000"), Decimal(0), None, None, [], [], [])
            return [envelope(cfg, document, events[1], evidence=[1], state=state), envelope(cfg, document, events[2], evidence=[1], state=state)]
        race = run(events, raced)
        self.assertEqual(race["report"]["ledger"]["position"], "1")
        codes = [item["reason_code"] for item in race["report"]["decisions"]]
        self.assertIn("state_changed", codes)
        events = [book(1, 1000, [("99", "5")], [("101", "5")]), intent(2, 1000, decision_id="D1", client="c1"), intent(3, 1100, decision_id="D1", client="c2", px="101")]
        duplicate = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1])])
        self.assertEqual(duplicate["report"]["ledger"]["position"], "1")
        self.assertTrue(any(item["reason_code"] == "duplicate_intent" for item in duplicate["report"]["decisions"]))
        failed = run([book(1, 1000, [("99", "2")], [("101", "2")]), intent(2, 1000, px="101")], lambda cfg, document: [envelope(cfg, document, intent(2, 1000, px="101"), evidence=[1])], journal=sim.Journal(fail_before={"pre_action:D1"}))
        self.assertEqual(failed["report"]["ledger"]["cash"], "10000")
        self.assertEqual(failed["report"]["ledger"]["position"], "0")
        self.assertTrue(any(item["reason_code"] == "journal_failure" for item in failed["report"]["decisions"]))
        self.assertIn("Journal", failed["report"]["decisions"][0]["reason_de"])
        with self.assertRaises(sim.PaperError):
            sim.replay(base_config(), tape([]), lambda: None)

    def test_protection_precedes_discretion_without_a_judge_call(self):
        cfg = base_config()
        cfg["protection"] = {
            "selected": True, "mode": "stop_mark", "policy_id": "fixture-stop", "priority": "before_discretionary",
            "audit": "journal_before_action", "risk_treatment": "hard_limits_still_bind", "side": "long",
            "trigger_mark": "90", "size": "full_position", "depth": "visible_book_only", "max_book_age_ms": 1000,
        }
        events = [
            book(1, 1000, [("99", "2")], [("100", "2")]),
            intent(2, 1000, px="100"),
            book(3, 2000, [("89", "2")], [("95", "2")]),
            {"sequence": 4, "type": "asset_ctx", "exchange_time_ms": 2000, "local_receipt_ms": 2000, "coin": "ETH", "mark_px": "90", "oracle_px": "90"},
            intent(5, 2000, px="95", decision_id="D2", client="c2"),
        ]
        def envs(config, document):
            first = envelope(config, document, events[1], evidence=[1])
            state = sim.binding_hash(Decimal("10000") - Decimal("100") - Decimal("0.045"), Decimal(1), Decimal("100"), None, [], ["D1"], [])
            second = envelope(config, document, events[4], state=state, as_of=3, evidence=[3])
            return [first, second]
        result = run(events, envs, cfg=cfg)
        self.assertEqual(result["report"]["ledger"]["position"], "0")
        self.assertEqual(result["source"].lookups, 2)
        kinds = [json.loads(line)["kind"] for line in result["journal"].text().splitlines()]
        self.assertLess(kinds.index("policy_evaluation"), kinds.index("refused"))
        self.assertTrue(any(item["kind"] == "protection" and item["judge_called"] is False for item in result["report"]["decisions"]))
        blocked = run(events[:4], envs, cfg=cfg, journal=sim.Journal(fail_before={"protection_pre_action"}))
        self.assertEqual(blocked["report"]["ledger"]["position"], "1")
        cfg_plain = base_config()
        cfg_plain["protection"] = {"selected": False}
        naked = run([book(1, 1000, [("99", "2")], [("101", "2")]), intent(2, 1000, px="101")], lambda c, document: [envelope(c, document, intent(2, 1000, px="101"), evidence=[1])], cfg=cfg_plain)
        self.assertEqual(naked["report"]["decisions"][0]["reason_code"], "protection_policy_absent")
        self.assertEqual(naked["report"]["ledger"]["position"], "0")

    def test_maker_queue_is_conservative_and_can_be_disabled(self):
        events = [
            book(1, 1000, [("100", "5")], [("110", "5")]),
            intent(2, 1000, px="100", tif="gtc"),
            {"sequence": 3, "type": "trade", "exchange_time_ms": 1500, "local_receipt_ms": 1500, "coin": "ETH", "px": "100", "sz": "5", "aggressor": "sell"},
            {"sequence": 4, "type": "trade", "exchange_time_ms": 1600, "local_receipt_ms": 1600, "coin": "ETH", "px": "100", "sz": "1", "aggressor": "sell"},
        ]
        modelled = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1], expires=5000)])
        fills = json.loads(modelled["files"]["orders.json"])[0]["fills"]
        self.assertEqual(fills[0]["liquidity"], "maker_modelled")
        self.assertFalse(fills[0]["observed"])
        self.assertEqual(Decimal(modelled["report"]["fees"]), Decimal("0.015"))
        cfg = base_config()
        cfg["execution"]["maker_model"] = "maker_unsupported"
        untouched = run(events, lambda c, document: [envelope(c, document, events[1], evidence=[1], expires=5000)], cfg=cfg)
        self.assertEqual(json.loads(untouched["files"]["orders.json"])[0]["fills"], [])
        self.assertEqual(untouched["report"]["ledger"]["position"], "0")

    def test_deterministic_hashes_holdout_and_incomplete_bundle(self):
        events = [book(1, 1000, [("99", "2")], [("101", "2")]), intent(2, 1000, px="101")]
        first = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1])])
        second = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1])])
        self.assertEqual(first["files"], second["files"])
        self.assertEqual(first["report"]["evaluation"]["trial_index"], 3)
        cfg = base_config()
        cfg["evaluation"]["train"] = {"start_ms": 0, "end_ms": 100}
        cfg["evaluation"]["validation"] = {"start_ms": 100, "end_ms": 400}
        cfg["evaluation"]["holdout"] = {"start_ms": 500, "end_ms": 5000}
        held = run(events, lambda c, document: [envelope(c, document, events[1], evidence=[1])], cfg=cfg)
        self.assertTrue(held["report"]["evaluation"]["holdout_touched"])
        self.assertEqual(held["report"]["evaluation"]["holdout_access_count"], 1)
        self.assertFalse(held["report"]["claims_demonstrated_edge"])
        import research_dataset as dataset
        from test_research_dataset import START, parameters, write_bundle, complete_reader, bar
        with self.subtest_bundle(dataset, parameters, write_bundle, complete_reader, bar) as bundle:
            captured = base_config()
            captured["evidence_class"] = "captured_public_replay"
            captured["source_network"] = "mainnet"
            captured["requires_complete_coverage"] = True
            captured["claims_market_evidence"] = True
            captured["research_bundle"] = str(bundle)
            document = tape([book(1, 1000, [("99", "2")], [("101", "2")])])
            document["evidence_class"] = "captured_public_replay"
            with self.assertRaises(sim.PaperError) as caught:
                sim.replay(captured, document, sim.decisions_document(captured, []))
            self.assertEqual(caught.exception.code, "incomplete_evidence")

    def subtest_bundle(self, dataset, parameters, write_bundle, complete_reader, bar):
        import tempfile
        return _Bundle(write_bundle, parameters, complete_reader, bar)

    def test_verify_run_and_write_are_stable(self):
        import tempfile
        events = [book(1, 1000, [("99", "2")], [("101", "2")]), intent(2, 1000, px="101")]
        result = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1])])
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "run"
            sim.write_run(directory, result)
            manifest = sim.verify_run(directory)
            self.assertIn("experiment_accepted", manifest)
            (directory / "report.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(sim.PaperError):
                sim.verify_run(directory)

    def test_exchange_write_gate_does_not_flag_the_simulator(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("gate", ROOT / "hooks" / "exchange_write_gate.py")
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        for name in ("paper_sim.py", "paper_dashboard.py", "test_paper_sim.py", "test_paper_dashboard.py"):
            text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertIsNone(gate.find_write(text), name)


class _Bundle:
    def __init__(self, write_bundle, parameters, complete_reader, bar):
        self.write_bundle = write_bundle
        self.parameters = parameters
        self.complete_reader = complete_reader
        self.bar = bar
        self.temporary = None

    def __enter__(self):
        import tempfile
        self.temporary = tempfile.TemporaryDirectory()
        path = Path(self.temporary.name) / "bundle"
        path.mkdir()
        reader = self.complete_reader(hours=3)
        reader.responses[1] = [self.bar(0)]
        self.write_bundle(path, self.parameters(), reader)
        return path

    def __exit__(self, *args):
        self.temporary.cleanup()


class RecordingResult(unittest.TextTestResult):
    def __init__(self, stream, descriptions, verbosity, status_dir=None):
        super().__init__(stream, descriptions, verbosity)
        self.status_dir = status_dir

    def addSuccess(self, test):
        super().addSuccess(test)
        self._write(test, "passed")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._write(test, "failed")

    def addError(self, test, err):
        super().addError(test, err)
        self._write(test, "error")

    def _write(self, test, status):
        if not self.status_dir:
            return
        path = Path(self.status_dir)
        path.mkdir(parents=True, exist_ok=True)
        record = {
            "schema_version": 1, "kind": "engineering_test", "name": test.id(), "status": status,
            "evidence_class": "synthetic_engineering_fixture", "claims_market_evidence": False,
            "claims_strategy_validation": False,
        }
        with (path / "engineering-tests.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    status = None
    if "--status-dir" in argv:
        index = argv.index("--status-dir")
        status = argv[index + 1]
        del argv[index:index + 2]
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(resultclass=lambda stream, descriptions, verbosity: RecordingResult(stream, descriptions, verbosity, status)).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
