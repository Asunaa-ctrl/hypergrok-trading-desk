#!/usr/bin/env python3
"""Dashboard checks against simulator artifacts written to disk."""
from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path
import threading
import unittest

import paper_dashboard as dash
import paper_sim as sim
from test_paper_sim import base_config, book, envelope, intent, run

ROOT = Path(__file__).resolve().parents[1]


def artifact_root(tmp):
    root = Path(tmp)
    events = [book(1, 1000, [("99", "2")], [("101", "2")]), intent(2, 1000, px="101")]
    result = run(events, lambda cfg, document: [envelope(cfg, document, events[1], evidence=[1], verdict="REJECT", explanation="Synthetische Ablehnung <kein html>")])
    sim.write_run(root / "runs" / "synthetic-reject", result)
    (root / "engineering-tests.jsonl").write_text(
        "\n".join(json.dumps(item, sort_keys=True) for item in (
            {"schema_version": 1, "kind": "engineering_test", "name": "test_alpha", "status": "passed", "evidence_class": "synthetic_engineering_fixture", "claims_market_evidence": False, "claims_strategy_validation": False},
            {"schema_version": 1, "kind": "engineering_test", "name": "test_beta", "status": "failed", "evidence_class": "synthetic_engineering_fixture", "claims_market_evidence": False, "claims_strategy_validation": False},
        )) + "\n",
        encoding="utf-8",
    )
    return root


class DashboardTest(unittest.TestCase):
    def test_empty_root_has_the_exact_paper_sentence_and_no_balance(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            status = dash.scan(tmp)
        self.assertFalse(status["paper"]["started"])
        self.assertEqual(status["paper"]["message_de"], "Noch kein Paper-Durchlauf gestartet")
        self.assertEqual(status["paper"]["runs"], [])
        self.assertEqual(status["engineering"]["counts"]["recorded"], 0)
        self.assertNotIn("percent", json.dumps(status))
        self.assertEqual(status["bind"], "127.0.0.1")

    def test_saved_synthetic_run_is_counted_from_files_and_not_as_paper(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = artifact_root(tmp)
            status = dash.scan(root)
            report = json.loads((root / "runs" / "synthetic-reject" / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(status["engineering"]["counts"], {"recorded": 2, "passed": 1, "failed": 1, "errors": 0})
        self.assertFalse(status["paper"]["started"])
        self.assertEqual(status["paper"]["message_de"], "Noch kein Paper-Durchlauf gestartet")
        synthetic = status["engineering"]["synthetic_runs"]
        self.assertEqual(len(synthetic), 1)
        self.assertEqual(synthetic[0]["ledger"]["cash"], report["ledger"]["cash"])
        self.assertEqual(synthetic[0]["ledger"]["position"], report["ledger"]["position"])
        self.assertEqual(synthetic[0]["fees"], report["fees"])
        self.assertFalse(synthetic[0]["claims_market_evidence"])
        self.assertFalse(synthetic[0]["claims_demonstrated_edge"])
        decision = synthetic[0]["decisions"][0]
        self.assertEqual(decision["recorded_verdict"], "REJECT")
        self.assertIn("REJECT", decision["reason_de"])
        self.assertFalse(decision["judge_authenticated"])
        self.assertIn("<kein html>", decision["explanation"])

    def test_http_is_local_read_only_and_serves_the_german_shell(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = artifact_root(tmp)
            server = dash.serve(root, 0)
            self.assertEqual(server.server_address[0], "127.0.0.1")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
                connection.request("GET", "/api/status")
                status = json.loads(connection.getresponse().read().decode())
                self.assertEqual(status["paper"]["message_de"], "Noch kein Paper-Durchlauf gestartet")
                connection.request("GET", "/")
                page = connection.getresponse().read().decode()
                self.assertIn("Noch kein Paper-Durchlauf gestartet", page)
                self.assertIn("keine Strategie", page)
                self.assertNotIn("type=\"password\"", page)
                self.assertNotIn("wallet", page.lower())
                connection.request("POST", "/api/status")
                self.assertEqual(connection.getresponse().status, 405)
                connection.request("GET", "/../paper_sim.py")
                self.assertEqual(connection.getresponse().status, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
        html = (ROOT / "scripts" / "paper_dashboard_static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("textContent", html)
        self.assertNotIn("innerHTML", html)

    def test_captured_replay_is_the_only_paper_phase(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = artifact_root(tmp)
            cfg = base_config()
            cfg["evidence_class"] = "captured_public_replay"
            cfg["source_network"] = "mainnet"
            events = [book(1, 1000, [("99", "2")], [("101", "2")])]
            document = {"schema_version": 1, "evidence_class": "captured_public_replay", "label": "captured label without a market-evidence claim", "events": events}
            result = sim.replay(cfg, document, sim.decisions_document(cfg, []))
            sim.write_run(root / "runs" / "captured-empty", result)
            status = dash.scan(root)
            self.assertTrue(status["paper"]["started"])
            self.assertIsNone(status["paper"]["message_de"])
            self.assertEqual(status["paper"]["runs"][0]["ledger"]["cash"], "10000")
            self.assertFalse(status["paper"]["runs"][0]["claims_market_evidence"])
            self.assertFalse(status["paper"]["runs"][0]["judge_provenance"]["authenticated"])
            self.assertEqual(len(status["engineering"]["synthetic_runs"]), 1)
            report_path = root / "runs" / "captured-empty" / "report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["evidence_class"] = "synthetic_engineering_fixture"
            report["judge_provenance"]["authenticated"] = True
            report["judge_provenance"]["runtime_integration"] = True
            report_path.write_text(json.dumps(report), encoding="utf-8")
            forged = dash.scan(root)
        self.assertFalse(forged["paper"]["started"])
        self.assertEqual(forged["paper"]["message_de"], "Noch kein Paper-Durchlauf gestartet")
        self.assertEqual(forged["engineering"]["conflicts"][0]["note_de"], "Widersprüchliche Dateien. Kein Kontostand aus diesem Ordner.")
        self.assertNotIn("ledger", forged["engineering"]["conflicts"][0])


if __name__ == "__main__":
    unittest.main()
