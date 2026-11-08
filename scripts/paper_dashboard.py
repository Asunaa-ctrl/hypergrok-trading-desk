#!/usr/bin/env python3
"""Read-only local dashboard for paper-simulator artifacts. Binds to 127.0.0.1.

The page reads manifests, journals and engineering-test records already on disk.
It does not start a run, sign, or contact an exchange. Based on HyperGrok Trading
Desk by Andrew Wilkinson and Galleon Labs, MIT license.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from urllib.parse import urlsplit

from cli_contract import ReadOnlyParser
from paper_sim import PaperError, validate_paper_template

HOST = "127.0.0.1"
SCHEMA_VERSION = 1
STATIC = Path(__file__).resolve().parent / "paper_dashboard_static"
MAX_BYTES = 2_000_000
PAPER_EMPTY = "Noch kein Paper-Durchlauf gestartet"


def _read_json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise OSError("unreadable artifact")
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path):
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        return [{"status": "error", "name": path.name, "error": "record file is unreadable", "evidence_class": "synthetic_engineering_fixture", "claims_market_evidence": False, "claims_strategy_validation": False}]
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            rows.append({"status": "error", "name": "unreadable-line", "error": "invalid record", "evidence_class": "synthetic_engineering_fixture", "claims_market_evidence": False, "claims_strategy_validation": False})
            continue
        if isinstance(item, dict):
            rows.append({key: item.get(key) for key in ("schema_version", "kind", "name", "status", "evidence_class", "claims_market_evidence", "claims_strategy_validation")})
    return rows


def _run(directory):
    try:
        report = _read_json(directory / "report.json")
        manifest = _read_json(directory / "manifest.json")
        config = _read_json(directory / "config.json")
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        return {"id": directory.name, "readable": False, "error": str(exc), "evidence_class": None, "classification": "unreadable"}
    config_class = config.get("evidence_class") if isinstance(config, dict) else None
    report_class = report.get("evidence_class")
    conflict = config_class != report_class or config_class not in ("synthetic_engineering_fixture", "captured_public_replay")
    provenance = report.get("judge_provenance") if isinstance(report.get("judge_provenance"), dict) else {}
    forged_authority = any(provenance.get(key) is True for key in ("authenticated", "runtime_integration", "bot_contacted"))
    ledger = report.get("ledger") or {}
    quality = report.get("data_quality") or {}
    observed = datetime.fromtimestamp((directory / "manifest.json").stat().st_mtime, timezone.utc)
    errors = _errors(report)
    if conflict:
        errors.insert(0, {"code": "evidence_conflict", "detail_de": "Konfiguration und Bericht widersprechen sich oder die Belegklasse fehlt. Dieser Ordner wird nicht als Paper-Durchlauf angezeigt."})
    if forged_authority:
        errors.insert(0, {"code": "forged_authority", "detail_de": "Der Bericht behauptet eine Authentifizierung oder eine Bot-Verbindung. Die Anzeige stuft das als falsch ein."})
    return {
        "id": directory.name,
        "readable": True,
        "classification": "conflict" if conflict else config_class,
        "evidence_class": None if conflict else config_class,
        "report_evidence_class": report_class,
        "config_evidence_class": config_class,
        "claims_market_evidence": False if conflict else report.get("claims_market_evidence") is True,
        "claims_demonstrated_edge": False,
        "simulated": True,
        "source_network": report.get("source_network"),
        "accepted_experiment": report.get("accepted_experiment"),
        "ledger": {
            "starting_cash": ledger.get("starting_cash"),
            "cash": ledger.get("cash"),
            "equity": ledger.get("equity"),
            "position": ledger.get("position"),
            "average_price": ledger.get("average_price"),
            "mark": ledger.get("mark"),
            "currency": ledger.get("currency"),
        },
        "fees": report.get("fees"),
        "funding": report.get("funding"),
        "slippage": report.get("slippage"),
        "drawdown": report.get("drawdown"),
        "drawdown_complete": report.get("drawdown_complete"),
        "orders": report.get("orders"),
        "orders_detail": _orders(directory),
        "chart": report.get("chart") or {"eligible": False, "points": [], "reason_de": "Keine echte Papierbeobachtung. Eine Kurve wird nicht gezeichnet.", "slippage_included_in_equity": False},
        "fixture_series": report.get("equity_points") or [] if config_class == "synthetic_engineering_fixture" else [],
        "pnl_breakdown": report.get("pnl_breakdown"),
        "budget": report.get("budget"),
        "fx": report.get("fx"),
        "native_decision": report.get("native_decision") or "pending",
        "readiness": report.get("readiness"),
        "decisions": report.get("decisions") or [],
        "data_quality": quality,
        "identifiers": {
            "config_sha256": report.get("config_sha256"),
            "code_sha256": report.get("code_sha256"),
            "data_sha256": report.get("data_sha256"),
            "journal_head": manifest.get("journal_head"),
        },
        "judge_provenance": {
            "authenticated": False,
            "runtime_integration": False,
            "bot_contacted": False,
            "bot_created": False,
            "declared_id": provenance.get("declared_id"),
            "recommendation_status": "role_understanding_acknowledged_not_authenticated",
            "note_de": "Die Kenntnisnahme des Desk Lead ist Rollenverständnis, keine authentifizierte Verbindung und kein Laufzeitzugriff.",
        },
        "file_observed_at_utc": observed.isoformat(timespec="seconds"),
        "equity_points": report.get("equity_points") or [],
        "errors": errors,
    }


def _errors(report):
    found = []
    funding = report.get("funding") or {}
    if funding.get("status") == "unavailable":
        found.append({"code": "funding_unavailable", "detail_de": "Finanzierung ist nicht verfügbar und wird nicht als null geführt."})
    if report.get("reconciliation", {}).get("ok") is False:
        found.append({"code": "reconciliation", "detail_de": "Die lokale Abstimmung ist fehlgeschlagen."})
    if report.get("unresolved_virtual_exposure"):
        found.append({"code": "unresolved_exposure", "detail_de": "Eine virtuelle Position blieb ohne ausgeführte Schutzaktion offen."})
    if report.get("unresolved_virtual_insolvency"):
        found.append({"code": "insolvency", "detail_de": "Negatives virtuelles Cash ist keine Liquidation. Ein Liquidationsmodell ist nicht implementiert."})
    quality = report.get("data_quality") or {}
    for key, label in (("gaps", "Lücken"), ("reconnects", "Reconnects"), ("out_of_order", "Ereignisse außerhalb der Reihenfolge"), ("duplicates", "Duplikate")):
        if quality.get(key):
            found.append({"code": key, "detail_de": f"{label}: {quality.get(key)} beobachtet."})
    return found


def _orders(directory):
    path = directory / "orders.json"
    try:
        rows = _read_json(path)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return []
    return rows if isinstance(rows, list) else []


def _market(root):
    """Candles and books are plotted only from a non-fixture capture. Otherwise they stay empty."""
    last_seen = None
    fixture = False
    for manifest in sorted(root.glob("captures/*/manifest.json")):
        if manifest.is_symlink():
            continue
        try:
            bundle = _read_json(manifest)
        except (OSError, json.JSONDecodeError, UnicodeError):
            continue
        stamp = None
        records = bundle.get("records") if isinstance(bundle, dict) else None
        if isinstance(records, list) and records:
            stamp = records[0].get("retrieved_at_utc")
        if stamp and (last_seen is None or stamp > last_seen):
            last_seen = stamp
        if bundle.get("evidence_class") != "captured_public_replay":
            fixture = True
            continue
        if bundle.get("network_execution") in (None, "not_performed", "loopback_only"):
            fixture = True
            continue
        if bundle.get("claims_market_evidence") is not True:
            continue
        candles, book = _observed_market(bundle)
        return _market_payload(candles, book, last_seen, False)
    return _market_payload([], None, last_seen, fixture)


def _observed_market(bundle):
    candles, book = [], None
    for record in bundle.get("records") or []:
        request = record.get("request") or {}
        body = record.get("body")
        as_of = record.get("retrieved_at_utc")
        if request.get("type") == "candleSnapshot" and isinstance(body, list):
            for row in body:
                if isinstance(row, dict) and all(row.get(key) not in (None, "") for key in ("t", "o", "h", "l", "c")):
                    candles.append({"t": row["t"], "o": row["o"], "h": row["h"], "l": row["l"], "c": row["c"], "as_of": as_of, "source": "captured /info candleSnapshot"})
        if request.get("type") == "l2Book" and isinstance(body, dict) and isinstance(body.get("levels"), list) and len(body["levels"]) == 2:
            book = {"time": body.get("time"), "as_of": as_of, "bids": body["levels"][0], "asks": body["levels"][1], "source": "captured /info l2Book"}
    return candles, book


def _market_payload(candles, book, last_seen, fixture):
    if last_seen is None:
        collector = {"healthy": False, "last_seen_utc": None, "state": "stopped", "note_de": "Kein Sammlerlauf. Ein gestoppter Prozess oder ein ausgeschalteter Rechner ist nicht gesund und hat kein last-seen."}
    else:
        collector = {"healthy": False, "last_seen_utc": last_seen, "state": "last_seen_only", "note_de": "Nur der letzte gespeicherte Zeitstempel. Das ist kein laufender Sammler."}
    return {
        "book": book,
        "book_reason_de": None if book else "Kein beobachtetes Orderbuch. Historische Tiefe wird nicht rekonstruiert.",
        "candles": candles,
        "candle_reason_de": None if candles else "Keine erfassten Kerzen. Es wird keine Kursreihe erfunden.",
        "collector": collector,
        "fixture_present": fixture,
        "plotted": bool(candles or book),
    }


def _fixture_market(root):
    path = root / "engineering" / "synthetic_market.json"
    empty = {"book": None, "candles": [], "label": "synthetic_engineering_fixture", "plotted_as_market": False}
    if not path.is_file() or path.is_symlink():
        return empty
    try:
        raw = _read_json(path)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return empty
    if raw.get("evidence_class") != "synthetic_engineering_fixture":
        return empty
    candles = raw.get("candles") if isinstance(raw.get("candles"), list) else []
    book = raw.get("book") if isinstance(raw.get("book"), dict) else None
    return {"book": book, "candles": candles, "label": "synthetic_engineering_fixture", "plotted_as_market": False}


def _handoff(root):
    path = root / "handoff" / "config.json"
    if not path.is_file() or path.is_symlink():
        return {"present": False, "budget": None, "fx_status": None, "usable_quote": None, "strategy_status": None, "native_decision": "pending"}
    try:
        config = _read_json(path)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {"present": False, "budget": None, "fx_status": "unreadable", "usable_quote": None, "strategy_status": None, "native_decision": "pending"}
    capital = config.get("capital") if isinstance(config, dict) else {}
    fx = config.get("fx") if isinstance(config, dict) else {}
    strategy = config.get("strategy") if isinstance(config, dict) else {}
    usable = None
    if isinstance(fx, dict) and fx.get("status") == "cited" and capital.get("currency") == "USDC":
        usable = capital.get("starting_cash")
    return {
        "present": True,
        "budget": {"amount": capital.get("starting_cash"), "currency": capital.get("currency")},
        "fx_status": fx.get("status") if isinstance(fx, dict) else None,
        "usable_quote": usable,
        "strategy_status": strategy.get("status") if isinstance(strategy, dict) else None,
        "native_decision": "pending",
    }


def scan(root):
    root = Path(root)
    records = _jsonl(root / "engineering-tests.jsonl") if root.exists() else []
    runs = []
    runs_dir = root / "runs"
    if runs_dir.is_dir() and not runs_dir.is_symlink():
        for child in sorted(path for path in runs_dir.iterdir() if path.is_dir() and not path.is_symlink()):
            runs.append(_run(child))
    synthetic = [item for item in runs if item.get("classification") == "synthetic_engineering_fixture"]
    paper = [item for item in runs if item.get("classification") == "captured_public_replay"]
    conflicts = [item for item in runs if item.get("classification") == "conflict"]
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "local_readonly_dashboard",
        "bind": HOST,
        "read_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "disclaimer_de": "Bestandene technische Tests prüfen nur die Software. Sie validieren keine Strategie und sind kein Marktbeweis.",
        "judge_note_de": "Die Kenntnisnahme des Desk Lead ist nur Rollenverständnis. Die judge_id authentifiziert ihn nicht, und dieser Prozess kontaktiert keinen Bot.",
        "engineering": {
            "label_de": "Phase 1 — Entwicklung und technische Prüfung",
            "records": records,
            "counts": {
                "recorded": len(records),
                "passed": sum(item.get("status") == "passed" for item in records),
                "failed": sum(item.get("status") == "failed" for item in records),
                "errors": sum(item.get("status") == "error" for item in records),
            },
            "fixture_market": _fixture_market(root),
            "synthetic_runs": synthetic,
            "conflicts": [{
                "id": item.get("id"),
                "readable": item.get("readable"),
                "errors": item.get("errors"),
                "config_evidence_class": item.get("config_evidence_class"),
                "report_evidence_class": item.get("report_evidence_class"),
                "note_de": "Widersprüchliche Dateien. Kein Kontostand aus diesem Ordner.",
            } for item in conflicts],
        },
        "handoff": _handoff(root),
        "market": _market(root),
        "paper": {
            "label_de": "Phase 2 — Lokaler Paper-Durchlauf",
            "started": bool(paper),
            "message_de": None if paper else PAPER_EMPTY,
            "runs": paper,
        },
        "template": _template(root),
    }


def _template(root):
    path = root / "paper_config_template.json"
    if not path.is_file() or path.is_symlink():
        return {"present": False, "ready": False, "run_started": False}
    try:
        raw = _read_json(path)
        report = validate_paper_template(raw)
    except (OSError, json.JSONDecodeError, UnicodeError, PaperError) as exc:
        return {"present": True, "ready": False, "run_started": False, "error": str(exc)}
    return {"present": True, **report, "observation_market": raw["observation"]["market"], "campaign": raw["campaign"], "fx_eur_usd": raw["fx"]["eur_usd"]["status"], "fx_usdc_usd": raw["fx"]["usdc_usd"]["status"], "strategy_status": raw["strategy"]["status"], "exit_status": raw["exit"]["status"]}


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            self._static("index.html", "text/html; charset=utf-8")
        elif path == "/style.css":
            self._static("style.css", "text/css; charset=utf-8")
        elif path == "/app.js":
            self._static("app.js", "text/javascript; charset=utf-8")
        elif path == "/api/status":
            body = json.dumps(scan(self.server.root), ensure_ascii=False, sort_keys=True).encode()
            self._bytes(200, "application/json; charset=utf-8", body)
        else:
            self._bytes(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
        self._bytes(405, "text/plain; charset=utf-8", b"read only")

    def do_PUT(self):
        self._bytes(405, "text/plain; charset=utf-8", b"read only")

    def do_DELETE(self):
        self._bytes(405, "text/plain; charset=utf-8", b"read only")

    def _static(self, name, content_type):
        path = STATIC / name
        if not path.is_file() or path.is_symlink():
            self._bytes(404, "text/plain; charset=utf-8", b"missing")
            return
        self._bytes(200, content_type, path.read_bytes())

    def _bytes(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def serve(root, port):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("port must be an integer from 0 to 65535")
    server = ThreadingHTTPServer((HOST, port), DashboardHandler)
    server.root = Path(root)
    if server.server_address[0] != HOST:
        server.server_close()
        raise RuntimeError("dashboard refused a non-local bind")
    return server


def schema():
    return {
        "schema_version": SCHEMA_VERSION,
        "command": "paper_dashboard",
        "mode": "local_readonly_dashboard",
        "network_access": "none",
        "bind": HOST,
        "methods": ["GET"],
        "empty_paper_message_de": PAPER_EMPTY,
        "note": "Reads local artifacts only. Does not start a paper run or contact an exchange.",
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = ReadOnlyParser(prog="paper_dashboard", description="Serve the local paper dashboard on 127.0.0.1.", machine_readable="--json" in argv)
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.schema:
        print(json.dumps(schema(), indent=2, sort_keys=True))
        return 0
    if args.root is None:
        parser.error("--root is required")
    try:
        server = serve(args.root, args.port)
    except OSError as exc:
        print(f"dashboard unavailable: {exc}", file=sys.stderr)
        return 1
    host, port = server.server_address
    print(json.dumps({"schema_version": SCHEMA_VERSION, "mode": "local_readonly_dashboard", "url": f"http://{host}:{port}/", "root": str(Path(args.root))}, sort_keys=True))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
