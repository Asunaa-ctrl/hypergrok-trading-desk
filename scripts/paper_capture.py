#!/usr/bin/env python3
"""Allowlisted public /info capture planning and offline conversion.

This build does not collect from Hyperliquid. Official origins are refused
before a socket exists. Tests may use 127.0.0.1. No redirects, credentials,
account payloads, or exchange paths.

Based on HyperGrok Trading Desk by Andrew Wilkinson and Galleon Labs, MIT license.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

from cli_contract import ReadOnlyParser
from paper_sim import (
    INFO_TYPES,
    NetworkDenied,
    PaperError,
    PublicInfoClient,
    canonical,
    digest,
    pretty,
)

SCHEMA_VERSION = 1
MAX_REQUESTS = 8
PURPOSES = {"bounded_snapshot"}
INCLUDES = {"meta", "metaAndAssetCtxs", "l2Book", "candleSnapshot", "fundingHistory"}
UNSUPPORTED_PURPOSES = {
    "reconstruct_book": "historical order-book reconstruction is not supported",
    "cadence_stream": "continuous or unbounded capture cadence is not supported",
    "infer_funding_oracle": "a candle or mark cannot stand in for the funding oracle",
}


def _origin_host(origin):
    try:
        parts = urlsplit(origin)
    except ValueError as exc:
        raise NetworkDenied("origin is not a URL") from exc
    if parts.username or parts.password or parts.query or parts.fragment:
        raise NetworkDenied("credentials, query, and fragments are refused")
    return parts


def classify_origin(origin):
    """Return loopback or refuse. Official hosts are allowlisted but not collected."""
    parts = _origin_host(origin if "://" in origin else "https://" + origin)
    host = (parts.hostname or "").lower()
    if host in {"api.hyperliquid.xyz", "api.hyperliquid-testnet.xyz"}:
        raise PaperError("collection_not_enabled", "external public collection is not enabled in this build")
    if parts.scheme == "http" and host == "127.0.0.1" and parts.port:
        return "loopback"
    raise NetworkDenied("origin is not the loopback test host")


def plan_requests(parameters):
    if not isinstance(parameters, dict):
        raise PaperError("config_incomplete", "capture parameters must be an object")
    purpose = parameters.get("purpose")
    if purpose in UNSUPPORTED_PURPOSES:
        raise PaperError("unsupported", UNSUPPORTED_PURPOSES[purpose])
    if purpose not in PURPOSES:
        raise PaperError("unsupported", "capture purpose is not supported")
    include = parameters.get("include")
    if not isinstance(include, list) or not include or any(item not in INCLUDES for item in include):
        raise PaperError("config_incomplete", "include must list allowlisted public info types")
    if len(include) != len(set(include)):
        raise PaperError("config_incomplete", "include contains a duplicate type")
    max_requests = parameters.get("max_requests")
    if type(max_requests) is not int or not 1 <= max_requests <= MAX_REQUESTS:
        raise PaperError("config_incomplete", f"max_requests must be an integer from 1 to {MAX_REQUESTS}")
    coin = parameters.get("coin")
    if not isinstance(coin, str) or not coin:
        raise PaperError("config_incomplete", "coin is required")
    requests = []
    for kind in ("meta", "metaAndAssetCtxs", "l2Book", "candleSnapshot", "fundingHistory"):
        if kind not in include:
            continue
        if kind == "meta":
            payload = {"type": "meta"}
            if parameters.get("dex"):
                payload["dex"] = parameters["dex"]
        elif kind == "metaAndAssetCtxs":
            payload = {"type": "metaAndAssetCtxs"}
            if parameters.get("dex"):
                payload["dex"] = parameters["dex"]
        elif kind == "l2Book":
            payload = {"type": "l2Book", "coin": coin}
        elif kind == "candleSnapshot":
            window = parameters.get("window")
            if not isinstance(window, dict) or set(window) != {"interval", "startTime", "endTime"}:
                raise PaperError("config_incomplete", "candle capture requires an explicit window")
            payload = {"type": "candleSnapshot", "req": {"coin": coin, **window}}
        else:
            window = parameters.get("funding_window")
            if not isinstance(window, dict) or set(window) != {"startTime", "endTime"}:
                raise PaperError("config_incomplete", "funding capture requires an explicit window and still cannot invent an oracle")
            payload = {"type": "fundingHistory", "coin": coin, **window}
        PublicInfoClient("https://api.hyperliquid.xyz").validate(payload)
        requests.append(payload)
    if len(requests) > max_requests:
        raise PaperError("config_incomplete", "planned requests exceed max_requests")
    if not requests:
        raise PaperError("config_incomplete", "capture planned no requests")
    return requests


def _no_redirect(status, headers):
    if status != 200:
        raise NetworkDenied(f"refusing HTTP status {status}; redirects are not followed")
    for key, _value in headers:
        if key.lower() == "location":
            raise NetworkDenied("refusing a redirect location")
        if key.lower() == "set-cookie":
            raise NetworkDenied("refusing a cookie")


def capture_loopback(origin, parameters, opener, retrieved_at_utc):
    """Record one bounded loopback exchange. opener(url, payload) -> (status, headers, body)."""
    kind = classify_origin(origin)
    parameters = dict(parameters)
    parameters["transport"] = "loopback"
    requests = plan_requests(parameters)
    records = []
    for sequence, payload in enumerate(requests, start=1):
        url = origin.rstrip("/") + "/info"
        if not url.startswith("http://127.0.0.1:"):
            raise NetworkDenied("loopback capture cannot leave 127.0.0.1")
        status, headers, body = opener(url, payload)
        _no_redirect(status, headers)
        if not isinstance(body, (dict, list)):
            raise PaperError("unsupported", "public info body must be JSON")
        raw = canonical(body)
        records.append({
            "body": body,
            "local_receipt_ms": parameters.get("local_receipt_ms", 0),
            "request": payload,
            "retrieved_at_utc": retrieved_at_utc,
            "sequence": sequence,
            "sha256": digest(raw),
            "status": status,
            "url": url,
        })
    return _bundle(kind, origin, parameters, records)


def bundle_from_fixture(fixture):
    """Build a bundle from a saved fixture. This path performs no HTTP."""
    if not isinstance(fixture, dict) or fixture.get("evidence_class") != "loopback_fixture":
        raise PaperError("config_incomplete", "fixture evidence must be labelled loopback_fixture")
    if fixture.get("network_execution") not in {None, "not_performed"}:
        raise PaperError("collection_not_enabled", "a fixture cannot claim an external collection")
    origin = fixture.get("origin", "http://127.0.0.1:9")
    classify_origin(origin)
    parameters = fixture["parameters"]
    planned = plan_requests(parameters)
    rows = fixture.get("responses")
    if not isinstance(rows, list) or len(rows) != len(planned):
        raise PaperError("config_incomplete", "fixture responses must match the planned request list")
    records = []
    for sequence, (payload, row) in enumerate(zip(planned, rows), start=1):
        if row.get("request") != payload:
            raise PaperError("hash_mismatch", "fixture request does not match the planned allowlist")
        if row.get("status") != 200:
            raise NetworkDenied("fixture contains a non-200 response")
        body = row.get("body")
        records.append({
            "body": body,
            "local_receipt_ms": row.get("local_receipt_ms", 0),
            "request": payload,
            "retrieved_at_utc": row["retrieved_at_utc"],
            "sequence": sequence,
            "sha256": digest(canonical(body)),
            "status": 200,
            "url": origin.rstrip("/") + "/info",
        })
    return _bundle("loopback", origin, parameters, records)


def _bundle(kind, origin, parameters, records):
    quality = _quality(records)
    bundle = {
        "evidence_class": "loopback_fixture",
        "limitations": [
            "Loopback or saved fixture only. This file is not a Hyperliquid capture.",
            "Candles are not converted into a book, a trade, or an oracle.",
            "Funding history without an oracle observation cannot become exact funding cash.",
            "No continuous cadence and no historical book reconstruction.",
        ],
        "mode": "public_info_capture",
        "network_execution": "loopback_only" if kind == "loopback" and records else "not_performed",
        "origin_class": kind,
        "parameters": parameters,
        "quality": quality,
        "records": records,
        "schema_version": SCHEMA_VERSION,
    }
    if "retrieved_at_utc" in records[0] and records[0]["url"].startswith("http://127.0.0.1:"):
        bundle["network_execution"] = "loopback_only" if parameters.get("transport") == "loopback" else "not_performed"
    bundle["sha256"] = digest(canonical({key: bundle[key] for key in bundle if key != "sha256"}))
    return bundle


def _quality(records):
    found = {record["request"]["type"] for record in records}
    ctx = next((record["body"] for record in records if record["request"]["type"] == "metaAndAssetCtxs"), None)
    mark = oracle = "missing"
    if isinstance(ctx, list) and len(ctx) == 2 and isinstance(ctx[1], list) and ctx[1] and isinstance(ctx[1][0], dict):
        row = ctx[1][0]
        mark = "present" if row.get("markPx") else "missing"
        oracle = "present" if row.get("oraclePx") else "missing"
    funding = "missing"
    if "fundingHistory" in found:
        funding = "history_without_oracle"
    return {
        "depth": "present" if "l2Book" in found else "missing",
        "execution_tape": "unavailable",
        "funding": funding,
        "mark": mark,
        "oracle": oracle,
        "quote": "USDC",
    }


def fixture_to_tape(bundle):
    """Offline conversion. Candles never become a book, a trade, or an oracle."""
    if bundle.get("evidence_class") != "loopback_fixture":
        raise PaperError("config_incomplete", "only a loopback fixture can be converted here")
    events = []
    sequence = 1
    for record in bundle.get("records") or []:
        kind = record["request"]["type"]
        body = record["body"]
        stamp = record.get("local_receipt_ms", 0)
        if kind == "l2Book" and isinstance(body, dict) and isinstance(body.get("levels"), list):
            bids, asks = body["levels"][0], body["levels"][1]
            events.append({"sequence": sequence, "type": "book", "exchange_time_ms": int(body.get("time") or stamp), "local_receipt_ms": stamp, "coin": body.get("coin", "BTC"), "depth": "l2", "bids": [{"px": level["px"], "sz": level["sz"]} for level in bids], "asks": [{"px": level["px"], "sz": level["sz"]} for level in asks]})
            sequence += 1
        elif kind == "metaAndAssetCtxs" and isinstance(body, list) and len(body) == 2 and body[1]:
            row = body[1][0]
            events.append({"sequence": sequence, "type": "asset_ctx", "exchange_time_ms": stamp, "local_receipt_ms": stamp, "coin": bundle["parameters"]["coin"], "mark_px": row.get("markPx"), "oracle_px": row.get("oraclePx")})
            sequence += 1
        elif kind == "candleSnapshot" and isinstance(body, list):
            for row in body:
                events.append({"sequence": sequence, "type": "candle", "exchange_time_ms": row["t"], "local_receipt_ms": stamp, "coin": row.get("s", bundle["parameters"]["coin"]), "t": row["t"], "T": row["T"], "o": row["o"], "h": row["h"], "l": row["l"], "c": row["c"], "v": row.get("v", "0"), "n": row.get("n", 0), "closed": True})
                sequence += 1
        elif kind == "fundingHistory" and isinstance(body, list):
            for row in body:
                events.append({"sequence": sequence, "type": "funding", "exchange_time_ms": row["time"], "local_receipt_ms": stamp, "coin": row["coin"], "funding_rate": row.get("fundingRate"), "oracle_px": None, "premium": row.get("premium")})
                sequence += 1
    if any(event["type"] == "trade" for event in events):
        raise PaperError("unsupported", "conversion must not invent trades")
    return {"schema_version": SCHEMA_VERSION, "evidence_class": "loopback_fixture", "label": "synthetic loopback fixture, not market evidence", "events": events}


def observation_plan(coin="BTC", max_requests=4):
    if coin != "BTC":
        raise PaperError("config_incomplete", "the authorized observation market is BTC")
    if type(max_requests) is not int or not 1 <= max_requests <= 10:
        raise PaperError("config_incomplete", "observation allows at most 10 public info requests")
    return {"coin": coin, "include": ["meta", "metaAndAssetCtxs", "l2Book"], "max_requests": max_requests, "max_snapshots": 10, "purpose": "bounded_snapshot", "opens_exposure": False}


def assert_no_execution_tape(bundle):
    if bundle["quality"]["execution_tape"] != "unavailable":
        raise PaperError("unsupported", "this build does not promote a capture into an execution tape")
    if bundle["evidence_class"] != "loopback_fixture":
        raise PaperError("config_incomplete", "capture evidence class was relabelled")
    return True


def schema():
    return {
        "schema_version": SCHEMA_VERSION,
        "command": "paper_capture",
        "mode": "public_info_capture",
        "network_access": "loopback fixture only; official origins are refused",
        "exchange_writes": "none",
        "account_reads": "none",
        "training": "not_run",
        "live_collection": "disabled",
        "info_types": sorted(INFO_TYPES),
        "unsupported": list(UNSUPPORTED_PURPOSES.values()),
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = ReadOnlyParser(prog="paper_capture", description="Plan or convert a local public-info fixture. No external collection.", machine_readable="--json" in argv)
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--observe-plan", action="store_true")
    parser.add_argument("--execute-observation", action="store_true")
    parser.add_argument("--coin", default="BTC")
    parser.add_argument("--max-requests", type=int, default=4)
    parser.add_argument("--base-url")
    parser.add_argument("--from-fixture", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.schema:
        print(json.dumps(schema(), indent=2, sort_keys=True))
        return 0
    if args.observe_plan:
        plan = observation_plan(args.coin, args.max_requests)
        print(json.dumps({"schema_version": SCHEMA_VERSION, "live_collection": "not_performed", "requests": plan_requests(plan), "plan": plan}, indent=2, sort_keys=True))
        return 0
    if args.execute_observation:
        if not args.base_url:
            parser.error("--base-url is required and this command was not run")
        try:
            classify_origin(args.base_url)
        except PaperError as exc:
            print(json.dumps({"schema_version": SCHEMA_VERSION, "error": {"code": exc.code, "message": str(exc)}}, sort_keys=True))
            return 2
        parser.error("loopback observation needs --from-fixture in this build; no socket client is invoked here")
    if args.from_fixture is None or args.output is None:
        parser.error("--from-fixture and --output are required; external collection is not enabled")
    fixture = json.loads(Path(args.from_fixture).read_text(encoding="utf-8"))
    bundle = bundle_from_fixture(fixture)
    assert_no_execution_tape(bundle)
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=False)
    payload = pretty(bundle)
    (directory / "manifest.json").write_bytes(payload)
    print(json.dumps({"schema_version": SCHEMA_VERSION, "mode": "public_info_capture", "evidence_class": bundle["evidence_class"], "live_collection": "disabled", "sha256": bundle["sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
