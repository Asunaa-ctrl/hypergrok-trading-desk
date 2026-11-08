#!/usr/bin/env python3
"""Bounded public /info planning, opt-in capture, and offline conversion.

`--observe-plan` and import do not open a socket. Official collection runs only
after `--execute-observation --confirm-public-info`, and only as POST
`/info` on `https://api.hyperliquid.xyz` or `https://api.hyperliquid-testnet.xyz`.
Redirects, proxies, account fields, and the exchange path are refused.
This module does not sign, read an account, or open exposure.

Based on HyperGrok Trading Desk by Andrew Wilkinson and Galleon Labs, MIT license.
"""
from __future__ import annotations

import json
import ssl
import time
from pathlib import Path
import sys
import urllib.request
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
MAX_TIMEOUT_S = 8
DEFAULT_TIMEOUT_S = 5
INTERVAL_S = 1
WALL_S = 60
MAX_WINDOW_MS = 24 * 60 * 60 * 1000
MAX_BODY = 1_000_000
INTERVALS_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "8h": 28_800_000,
    "12h": 43_200_000, "1d": 86_400_000,
}
OFFICIAL = {
    "api.hyperliquid.xyz": ("https://api.hyperliquid.xyz", "mainnet"),
    "api.hyperliquid-testnet.xyz": ("https://api.hyperliquid-testnet.xyz", "testnet"),
}
PURPOSES = {"bounded_snapshot"}
INCLUDES = {"meta", "metaAndAssetCtxs", "l2Book", "candleSnapshot", "fundingHistory"}
HASH_SCOPE = "local_sha256_of_saved_bytes"
TEST_LABEL = "TEST-ONLY. Not an external capture and not a paper result. Lokaler Hash, keine externe Beglaubigung."
UNSUPPORTED_PURPOSES = {
    "reconstruct_book": "historical order-book reconstruction is not supported",
    "cadence_stream": "continuous or unbounded capture cadence is not supported",
    "infer_funding_oracle": "a candle or mark cannot stand in for the funding oracle",
}


def _origin_parts(origin):
    try:
        parts = urlsplit(origin if "://" in origin else "https://" + origin)
    except ValueError as exc:
        raise NetworkDenied("origin is not a URL") from exc
    if parts.username or parts.password or parts.query or parts.fragment:
        raise NetworkDenied("credentials, query, and fragments are refused")
    return parts


def classify_origin(origin):
    """Return mainnet, testnet, or loopback. Every other origin is refused."""
    parts = _origin_parts(origin)
    host = (parts.hostname or "").lower()
    if host in OFFICIAL and parts.scheme == "https" and parts.port in (None, 443) and parts.path in ("", "/"):
        return OFFICIAL[host][1]
    if parts.scheme == "http" and host == "127.0.0.1" and parts.port and parts.path in ("", "/"):
        return "loopback"
    raise NetworkDenied("origin is not an official https info host or the loopback test host")


def canonical_origin(origin):
    kind = classify_origin(origin)
    if kind == "loopback":
        parts = _origin_parts(origin)
        return f"http://127.0.0.1:{parts.port}", kind
    host = _origin_parts(origin).hostname.lower()
    base, label = OFFICIAL[host]
    return base, label


def info_url(origin):
    base, _kind = canonical_origin(origin)
    if not base.startswith("https://"):
        raise NetworkDenied("public info capture requires https")
    url = base + "/info"
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.path != "/info" or parts.query or parts.fragment or parts.username:
        raise NetworkDenied("public info URL is outside the allowlist")
    if parts.port not in (None, 443) or (parts.hostname or "").lower() not in OFFICIAL:
        raise NetworkDenied("public info host or port is outside the allowlist")
    return url


def _window(start, end):
    if type(start) is not int or type(end) is not int or end <= start or end - start > MAX_WINDOW_MS:
        raise PaperError("config_incomplete", "public info window must be an explicit range of at most 24h")


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
    timeout = parameters.get("timeout_s", DEFAULT_TIMEOUT_S)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < float(timeout) <= MAX_TIMEOUT_S:
        raise PaperError("config_incomplete", f"timeout_s must be greater than 0 and at most {MAX_TIMEOUT_S}")
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
            if window["interval"] not in INTERVALS_MS:
                raise PaperError("config_incomplete", "candle interval is outside the bounded set")
            _window(window["startTime"], window["endTime"])
            payload = {"type": "candleSnapshot", "req": {"coin": coin, **window}}
        else:
            window = parameters.get("funding_window")
            if not isinstance(window, dict) or set(window) != {"startTime", "endTime"}:
                raise PaperError("config_incomplete", "funding capture requires an explicit window and still cannot invent an oracle")
            _window(window["startTime"], window["endTime"])
            payload = {"type": "fundingHistory", "coin": coin, **window}
        PublicInfoClient("https://api.hyperliquid.xyz").validate(payload)
        requests.append(payload)
    if len(requests) > max_requests:
        raise PaperError("config_incomplete", "planned requests exceed max_requests")
    if not requests:
        raise PaperError("config_incomplete", "capture planned no requests")
    estimate = len(requests) * float(timeout) + max(0, len(requests) - 1) * INTERVAL_S
    if estimate > WALL_S:
        raise PaperError("config_incomplete", "planned public reads exceed the wall-clock bound")
    return requests


def _header_items(headers):
    if headers is None:
        return []
    if hasattr(headers, "items"):
        return list(headers.items())
    return list(headers)


def _no_redirect(status, headers):
    if status != 200:
        raise NetworkDenied(f"refusing HTTP status {status}; redirects are not followed")
    for key, _value in _header_items(headers):
        if str(key).lower() == "location":
            raise NetworkDenied("refusing a redirect location")
        if str(key).lower() == "set-cookie":
            raise NetworkDenied("refusing a cookie")


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise NetworkDenied("refusing a redirect")


def build_official_opener():
    """HTTPS opener for the two official info origins. It does not connect by itself."""
    context = ssl.create_default_context()
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise NetworkDenied("TLS certificate verification is required")
    https = urllib.request.HTTPSHandler(context=context, check_hostname=True)
    proxy = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(proxy, _RefuseRedirect, https)
    allowed = {base + "/info" for base, _kind in OFFICIAL.values()}

    def open_request(request, timeout):
        if getattr(request, "full_url", None) not in allowed:
            raise NetworkDenied("opener refused a url outside the official info allowlist")
        return opener.open(request, timeout=timeout)

    open_request.opener = opener
    open_request.context = context
    return open_request


def exchange(origin, payload, timeout, opener):
    """POST one allowlisted body to origin/info. opener(request, timeout) must not follow redirects."""
    url = info_url(origin)
    base, _kind = canonical_origin(origin)
    PublicInfoClient(base).validate(payload)
    data = canonical(payload)
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "hypergrok-paper-capture/1"},
        method="POST",
    )
    for key in request.headers:
        if key.lower() in {"authorization", "cookie", "x-api-key"}:
            raise NetworkDenied("public info request carries a credential header")
    if opener is None:
        raise PaperError("collection_not_confirmed", "no transport was supplied")
    response = opener(request, timeout=timeout)
    with response:
        status = getattr(response, "status", None)
        if status is None and hasattr(response, "getcode"):
            status = response.getcode()
        final = response.geturl() if hasattr(response, "geturl") else url
        headers = getattr(response, "headers", [])
        raw = response.read(MAX_BODY + 1)
    if final != url:
        raise NetworkDenied("refusing a final url that is not the official info path")
    _no_redirect(status, headers)
    if len(raw) > MAX_BODY:
        raise NetworkDenied("public info body exceeds the size bound")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PaperError("unsupported", "public info body must be JSON") from exc
    if not isinstance(body, (dict, list)):
        raise PaperError("unsupported", "public info body must be a JSON object or array")
    canonical(body)
    return body


def _record(origin, payload, body, sequence, retrieved_at_utc, local_receipt_ms):
    return {
        "body": body,
        "externally_authenticated": False,
        "local_receipt_ms": local_receipt_ms,
        "request": payload,
        "retrieved_at_utc": retrieved_at_utc,
        "sequence": sequence,
        "sha256": digest(canonical(body)),
        "status": 200,
        "url": info_url(origin) if classify_origin(origin) != "loopback" else origin.rstrip("/") + "/info",
    }


def capture_loopback(origin, parameters, opener, retrieved_at_utc):
    """Record one bounded loopback exchange. opener(url, payload) -> (status, headers, body)."""
    base, kind = canonical_origin(origin)
    if kind != "loopback":
        raise NetworkDenied("loopback capture cannot use an official or external host")
    parameters = dict(parameters)
    parameters["transport"] = "loopback"
    requests = plan_requests(parameters)
    records = []
    for sequence, payload in enumerate(requests, start=1):
        url = base + "/info"
        status, headers, body = opener(url, payload)
        _no_redirect(status, headers)
        if not isinstance(body, (dict, list)):
            raise PaperError("unsupported", "public info body must be JSON")
        records.append(_record(base, payload, body, sequence, retrieved_at_utc, parameters.get("local_receipt_ms", 0)))
    return _bundle(kind, base, parameters, records, evidence_class="loopback_fixture", network_execution="loopback_only", collection_performed=False)


def capture_official(origin, parameters, opener, retrieved_at_utc, timeout=DEFAULT_TIMEOUT_S, sleeper=None, clock=None, wall_ms=None):
    """Bounded official POST /info. The caller supplies the opener; the CLI builds it only after confirm."""
    base, kind = canonical_origin(origin)
    if kind not in {"mainnet", "testnet"}:
        raise NetworkDenied("official capture accepts only the two official https origins")
    if parameters.get("coin") != "BTC":
        raise PaperError("config_incomplete", "official observation is BTC only")
    parameters = dict(parameters)
    parameters["transport"] = "official_https"
    parameters["timeout_s"] = timeout
    requests = plan_requests(parameters)
    sleeper = sleeper or time.sleep
    clock = clock or time.monotonic
    wall_ms = wall_ms or (lambda: int(time.time() * 1000))
    started = clock()
    records = []
    for sequence, payload in enumerate(requests, start=1):
        now = clock()
        if now - started + timeout > WALL_S:
            raise PaperError("config_incomplete", "capture exceeded the wall-clock bound before the next read")
        if sequence > 1:
            sleeper(INTERVAL_S)
        body = exchange(base, payload, timeout, opener)
        records.append(_record(base, payload, body, sequence, retrieved_at_utc, wall_ms()))
    return _bundle(kind, base, parameters, records, evidence_class="observed_public_info", network_execution="public_info_post", collection_performed=True)


def bundle_from_fixture(fixture):
    """Build a bundle from a saved loopback fixture. This path performs no HTTP."""
    if not isinstance(fixture, dict) or fixture.get("evidence_class") != "loopback_fixture":
        raise PaperError("config_incomplete", "fixture evidence must be labelled loopback_fixture")
    if fixture.get("network_execution") not in {None, "not_performed"}:
        raise PaperError("config_incomplete", "a fixture cannot claim an external collection")
    origin = fixture.get("origin", "http://127.0.0.1:9")
    base, kind = canonical_origin(origin)
    if kind != "loopback":
        raise NetworkDenied("a loopback fixture cannot name an official host")
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
        records.append(_record(base, payload, row.get("body"), sequence, row["retrieved_at_utc"], row.get("local_receipt_ms", 0)))
    return _bundle("loopback", base, parameters, records, evidence_class="loopback_fixture", network_execution="not_performed", collection_performed=False)


def _bundle(kind, origin, parameters, records, *, evidence_class, network_execution, collection_performed):
    limitations = [
        "Candles are not converted into a book, a trade, or an oracle.",
        "Funding history without an oracle observation cannot become exact funding cash.",
        "No continuous cadence and no historical book reconstruction.",
        "The SHA-256 is a local hash of the saved bytes. It is not an external notary and it does not authenticate the venue.",
    ]
    if evidence_class == "loopback_fixture":
        limitations.insert(0, "Loopback or saved fixture only. This file is not a Hyperliquid capture.")
    bundle = {
        "authenticated": False,
        "claims_market_evidence": False,
        "collection_performed": collection_performed,
        "evidence_class": evidence_class,
        "externally_authenticated": False,
        "hash_scope": HASH_SCOPE,
        "limitations": limitations,
        "mode": "public_info_capture",
        "network_execution": network_execution,
        "opens_exposure": False,
        "origin": origin,
        "origin_class": kind,
        "parameters": parameters,
        "quality": _quality(records),
        "records": records,
        "schema_version": SCHEMA_VERSION,
    }
    bundle["sha256"] = digest(canonical({key: bundle[key] for key in bundle if key != "sha256"}))
    return bundle


def mark_test_harness(bundle):
    """Label a schema copy as a test fixture. This does not perform a read."""
    if not isinstance(bundle, dict) or bundle.get("evidence_class") != "observed_public_info":
        raise PaperError("config_incomplete", "only an observed public info schema can be marked as a test harness")
    marked = {key: bundle[key] for key in bundle if key != "sha256"}
    marked["collection_performed"] = False
    marked["network_execution"] = "not_performed"
    marked["test_harness"] = True
    marked["test_label"] = TEST_LABEL
    marked["claims_market_evidence"] = False
    marked["externally_authenticated"] = False
    marked["sha256"] = digest(canonical({key: marked[key] for key in marked if key != "sha256"}))
    return marked


def validate_observed_bundle(bundle):
    if not isinstance(bundle, dict) or bundle.get("schema_version") != SCHEMA_VERSION:
        raise PaperError("config_incomplete", "observed bundle schema is not supported")
    if bundle.get("evidence_class") != "observed_public_info":
        raise PaperError("config_incomplete", "not an observed public info bundle")
    if bundle.get("externally_authenticated") is not False or bundle.get("authenticated") not in (None, False):
        raise PaperError("unsupported", "a local hash is not external authentication")
    if bundle.get("claims_market_evidence") is True:
        raise PaperError("unsupported", "a local info capture cannot claim certified market evidence")
    if bundle.get("hash_scope") != HASH_SCOPE:
        raise PaperError("hash_mismatch", "hash scope must stay local")
    harness = bundle.get("test_harness") is True
    if harness:
        if bundle.get("test_label") != TEST_LABEL or bundle.get("collection_performed") is not False or bundle.get("network_execution") != "not_performed":
            raise PaperError("config_incomplete", "a test harness must keep its test label and must not claim a collection")
    else:
        if bundle.get("test_label") not in (None, "") or bundle.get("test_harness") not in (None, False):
            raise PaperError("config_incomplete", "an observed bundle cannot carry a partial test label")
        if bundle.get("collection_performed") is not True or bundle.get("network_execution") != "public_info_post":
            raise PaperError("config_incomplete", "an observed bundle must record that the public info post ran")
    origin = bundle.get("origin")
    base, kind = canonical_origin(origin)
    if kind not in {"mainnet", "testnet"}:
        raise NetworkDenied("observed bundle origin is not an official https info host")
    records = bundle.get("records")
    if not isinstance(records, list) or not records:
        raise PaperError("config_incomplete", "observed bundle has no records")
    for record in records:
        if not isinstance(record, dict):
            raise PaperError("config_incomplete", "observed record is not an object")
        if record.get("externally_authenticated") is not False:
            raise PaperError("unsupported", "a record hash is not external authentication")
        if record.get("url") != base + "/info":
            raise NetworkDenied("observed record url is outside the official info path")
        if record.get("sha256") != digest(canonical(record.get("body"))):
            raise PaperError("hash_mismatch", "observed record hash does not match its body")
        PublicInfoClient(base).validate(record.get("request"))
    body = {key: bundle[key] for key in bundle if key != "sha256"}
    if bundle.get("sha256") != digest(canonical(body)):
        raise PaperError("hash_mismatch", "observed bundle hash does not match the saved bytes")
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
    kind = bundle.get("evidence_class")
    if kind == "observed_public_info":
        validate_observed_bundle(bundle)
        label = bundle.get("test_label") or "offline conversion of observed public info; local hash, not externally authenticated"
    elif kind == "loopback_fixture":
        label = "synthetic loopback fixture, not market evidence"
    else:
        raise PaperError("config_incomplete", "only a loopback fixture or observed public info bundle can be converted here")
    events = []
    sequence = 1
    for record in bundle.get("records") or []:
        request = record["request"]
        body = record["body"]
        stamp = record.get("local_receipt_ms", 0)
        info = request["type"]
        if info == "l2Book" and isinstance(body, dict) and isinstance(body.get("levels"), list) and len(body["levels"]) == 2:
            bids, asks = body["levels"][0], body["levels"][1]
            events.append({"sequence": sequence, "type": "book", "exchange_time_ms": int(body.get("time") or stamp), "local_receipt_ms": stamp, "coin": body.get("coin", bundle["parameters"]["coin"]), "depth": "l2", "bids": [{"px": level["px"], "sz": level["sz"]} for level in bids], "asks": [{"px": level["px"], "sz": level["sz"]} for level in asks]})
            sequence += 1
        elif info == "metaAndAssetCtxs" and isinstance(body, list) and len(body) == 2 and body[1]:
            row = body[1][0]
            events.append({"sequence": sequence, "type": "asset_ctx", "exchange_time_ms": stamp, "local_receipt_ms": stamp, "coin": bundle["parameters"]["coin"], "mark_px": row.get("markPx"), "oracle_px": row.get("oraclePx")})
            sequence += 1
        elif info == "candleSnapshot" and isinstance(body, list):
            for row in body:
                if not isinstance(row, dict) or any(row.get(key) in (None, "") for key in ("t", "T", "o", "h", "l", "c")):
                    continue
                events.append({"sequence": sequence, "type": "candle", "exchange_time_ms": row["t"], "local_receipt_ms": stamp, "coin": row.get("s", bundle["parameters"]["coin"]), "t": row["t"], "T": row["T"], "o": row["o"], "h": row["h"], "l": row["l"], "c": row["c"], "v": row.get("v", "0"), "n": row.get("n", 0), "closed": True})
                sequence += 1
        elif info == "fundingHistory" and isinstance(body, list):
            for row in body:
                events.append({"sequence": sequence, "type": "funding", "exchange_time_ms": row["time"], "local_receipt_ms": stamp, "coin": row["coin"], "funding_rate": row.get("fundingRate"), "oracle_px": None, "premium": row.get("premium")})
                sequence += 1
    if any(event["type"] == "trade" for event in events):
        raise PaperError("unsupported", "conversion must not invent trades")
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_class": kind,
        "externally_authenticated": False,
        "hash_scope": HASH_SCOPE,
        "label": label,
        "events": events,
    }


def observation_plan(coin="BTC", max_requests=4):
    if coin != "BTC":
        raise PaperError("config_incomplete", "the authorized observation market is BTC")
    if type(max_requests) is not int or not 1 <= max_requests <= MAX_REQUESTS:
        raise PaperError("config_incomplete", "observation allows at most 8 public info requests")
    return {
        "coin": coin,
        "include": ["meta", "metaAndAssetCtxs", "l2Book"],
        "max_requests": max_requests,
        "max_snapshots": max_requests,
        "opens_exposure": False,
        "purpose": "bounded_snapshot",
        "timeout_s": DEFAULT_TIMEOUT_S,
    }


def assert_no_execution_tape(bundle):
    if bundle["quality"]["execution_tape"] != "unavailable":
        raise PaperError("unsupported", "this build does not promote a capture into an execution tape")
    if bundle["evidence_class"] not in {"loopback_fixture", "observed_public_info"}:
        raise PaperError("config_incomplete", "capture evidence class was relabelled")
    return True


def schema():
    return {
        "schema_version": SCHEMA_VERSION,
        "command": "paper_capture",
        "mode": "public_info_capture",
        "default": "dry plan; no auto-start",
        "network_access": "none for --schema, --observe-plan, --from-fixture, and --to-tape; POST /info only after --execute-observation --confirm-public-info",
        "official_origins": ["https://api.hyperliquid.xyz", "https://api.hyperliquid-testnet.xyz"],
        "path": "/info",
        "tls": "certificate verification required; redirects and proxies refused",
        "bounds": {"max_requests": MAX_REQUESTS, "max_timeout_s": MAX_TIMEOUT_S, "min_interval_s": INTERVAL_S, "wall_s": WALL_S, "max_window_ms": MAX_WINDOW_MS},
        "exchange_writes": "none",
        "account_reads": "none",
        "training": "not_run",
        "opens_exposure": False,
        "info_types": sorted(INFO_TYPES),
        "unsupported": list(UNSUPPORTED_PURPOSES.values()),
    }


def _parameters_from_args(args):
    include = [item.strip() for item in args.include.split(",") if item.strip()]
    parameters = observation_plan(args.coin, args.max_requests)
    parameters["include"] = include
    if "candleSnapshot" in include:
        parameters["window"] = {"interval": args.interval, "startTime": args.start_ms, "endTime": args.end_ms}
    if "fundingHistory" in include:
        parameters["funding_window"] = {"startTime": args.start_ms, "endTime": args.end_ms}
    parameters["timeout_s"] = args.timeout
    return parameters


def _emit(code, message):
    print(json.dumps({"schema_version": SCHEMA_VERSION, "error": {"code": code, "message": message}}, sort_keys=True))


def execute_observation(args, opener=None, sleeper=None, clock=None, retrieved_at_utc=None):
    """Opt-in official read. opener=None builds the real TLS transport; tests pass a mock."""
    if not args.confirm_public_info:
        _emit("collection_not_confirmed", "official public info capture stays off until --confirm-public-info is present")
        return 2
    if not args.base_url or args.output is None:
        _emit("invalid_arguments", "--base-url and --output are required")
        return 2
    try:
        parameters = _parameters_from_args(args)
        plan_requests(parameters)
        info_url(args.base_url)
    except (PaperError, NetworkDenied) as exc:
        _emit(exc.code, str(exc))
        return 2
    directory = Path(args.output)
    if directory.exists():
        _emit("invalid_arguments", "output must be a new directory")
        return 2
    transport = opener or build_official_opener()
    try:
        bundle = capture_official(
            args.base_url,
            parameters,
            transport,
            retrieved_at_utc or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            timeout=args.timeout,
            sleeper=sleeper,
            clock=clock,
        )
        assert_no_execution_tape(bundle)
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "manifest.json").write_bytes(pretty(bundle))
    except (PaperError, NetworkDenied, OSError) as exc:
        code = getattr(exc, "code", "output_unavailable")
        _emit(code, str(exc))
        return 2
    print(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "mode": "public_info_capture",
        "evidence_class": bundle["evidence_class"],
        "collection_performed": True,
        "externally_authenticated": False,
        "opens_exposure": False,
        "sha256": bundle["sha256"],
    }, sort_keys=True))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = ReadOnlyParser(prog="paper_capture", description="Plan a dry public-info observation, or capture only after an explicit confirm flag.", machine_readable="--json" in argv)
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--observe-plan", action="store_true")
    parser.add_argument("--execute-observation", action="store_true")
    parser.add_argument("--confirm-public-info", action="store_true")
    parser.add_argument("--coin", default="BTC")
    parser.add_argument("--max-requests", type=int, default=4)
    parser.add_argument("--include", default="meta,metaAndAssetCtxs,l2Book")
    parser.add_argument("--interval", default=None)
    parser.add_argument("--start-ms", type=int, default=None)
    parser.add_argument("--end-ms", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--base-url")
    parser.add_argument("--from-fixture", type=Path)
    parser.add_argument("--to-tape", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.confirm_public_info and not args.execute_observation:
        parser.error("--confirm-public-info does not start a read unless --execute-observation is also set")
    if args.schema:
        print(json.dumps(schema(), indent=2, sort_keys=True))
        return 0
    if args.observe_plan:
        try:
            plan = observation_plan(args.coin, args.max_requests)
            requests = plan_requests(plan)
        except PaperError as exc:
            _emit(exc.code, str(exc))
            return 2
        print(json.dumps({"schema_version": SCHEMA_VERSION, "live_collection": "not_performed", "opens_exposure": False, "requests": requests, "plan": plan}, indent=2, sort_keys=True))
        return 0
    if args.execute_observation:
        if args.from_fixture is not None or args.to_tape is not None:
            parser.error("--execute-observation does not accept a fixture or a tape conversion")
        return execute_observation(args)
    if args.to_tape is not None:
        if args.output is None:
            parser.error("--output is required with --to-tape")
        bundle = json.loads(Path(args.to_tape).read_text(encoding="utf-8"))
        tape = fixture_to_tape(bundle)
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            parser.error("--output must be a new file")
        destination.write_bytes(pretty(tape))
        print(json.dumps({"schema_version": SCHEMA_VERSION, "mode": "offline_tape", "evidence_class": tape["evidence_class"], "externally_authenticated": False, "events": len(tape["events"])}, sort_keys=True))
        return 0
    if args.from_fixture is None or args.output is None:
        parser.error("choose --observe-plan, --from-fixture with --output, or --execute-observation --confirm-public-info")
    fixture = json.loads(Path(args.from_fixture).read_text(encoding="utf-8"))
    bundle = bundle_from_fixture(fixture)
    assert_no_execution_tape(bundle)
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "manifest.json").write_bytes(pretty(bundle))
    print(json.dumps({"schema_version": SCHEMA_VERSION, "mode": "public_info_capture", "evidence_class": bundle["evidence_class"], "live_collection": "not_performed", "sha256": bundle["sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
