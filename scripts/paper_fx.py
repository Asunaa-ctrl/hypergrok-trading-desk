#!/usr/bin/env python3
"""Opt-in public FX reference reader for the 100 EUR paper budget.

The default commands do not open a socket. A live read requires
`--execute-reference --confirm-public-fx`. It GETs three exact URLs and does
not follow redirects. Sample rates are not stored and are not run inputs.
A saved round must be read again at a real run start.

Based on HyperGrok Trading Desk by Andrew Wilkinson and Galleon Labs, MIT license.
"""
from __future__ import annotations

import json
import re
import ssl
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from pathlib import Path
import sys
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from cli_contract import ReadOnlyParser
from paper_sim import PaperError, canonical, digest, pretty

SCHEMA_VERSION = 1
PLAN_SHA256 = "ada0def6834c7ddac2953c325bcce9a86f2e0ff3d5669180648b8e7c47a40b9c"
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
KRAKEN_TIME_URL = "https://api.kraken.com/0/public/Time"
KRAKEN_TRADES_URL = "https://api.kraken.com/0/public/Trades?pair=USDCUSD&count=1"
ALLOWED_URLS = (KRAKEN_TIME_URL, KRAKEN_TRADES_URL, ECB_URL)
MAX_CALENDAR_DAYS = 4
MAX_TRADE_AGE_S = Decimal("60")
MAX_ROUND_S = Decimal("5")
MAX_CLOCK_SKEW_S = Decimal("5")
MAX_BODY = 200_000
REQUEST_TIMEOUT_S = 2
CAPITAL = Decimal("100")
UNMODELLED = "UNMODELLED"
PERMANENT_LABEL = "real EUR/USDC exchange costs unmodelled; not a deposit-to-withdrawal return"
RATE_TEXT = r"^[0-9]+(\.[0-9]+)?$"


class FxBlocked(PaperError):
    def __init__(self, code, message):
        super().__init__(code, message)


def _utc(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise FxBlocked("time_unknown", "a timestamp must be explicit UTC")
    text = value[:-1]
    fraction = "0"
    if "." in text:
        text, fraction = text.split(".", 1)
        if not fraction.isdigit() or not 1 <= len(fraction) <= 6:
            raise FxBlocked("time_unknown", "a timestamp fraction is not usable")
    try:
        moment = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise FxBlocked("time_unknown", "a timestamp is not UTC") from exc
    return moment + timedelta(microseconds=int(fraction.ljust(6, "0")))


def _epoch(moment):
    moment = moment.astimezone(timezone.utc)
    base = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = moment - base
    return Decimal(delta.days) * Decimal(86400) + Decimal(delta.seconds) + (Decimal(delta.microseconds) / Decimal(1000000))


def _date(value):
    if not isinstance(value, str):
        raise FxBlocked("time_unknown", "a reference date is missing")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise FxBlocked("time_unknown", "a reference date is not a calendar date") from exc


def _decimal_text(value, name):
    if isinstance(value, Decimal):
        value = format(value, "f")
    if not isinstance(value, str) or not re.fullmatch(RATE_TEXT, value):
        raise FxBlocked("units_unknown", f"{name} is not a plain decimal")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise FxBlocked("units_unknown", f"{name} is not numeric") from exc
    if not number.is_finite() or number <= 0:
        raise FxBlocked("units_unknown", f"{name} must be positive")
    return number


def _header_items(headers):
    if headers is None:
        return []
    if hasattr(headers, "items"):
        return list(headers.items())
    return list(headers)


def _no_redirect(status, headers):
    if status != 200:
        raise FxBlocked("network_denied", f"refusing HTTP status {status}; redirects are not followed")
    for key, _value in _header_items(headers):
        lowered = str(key).lower()
        if lowered == "location":
            raise FxBlocked("network_denied", "refusing a redirect location")
        if lowered == "set-cookie":
            raise FxBlocked("network_denied", "refusing a cookie")


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise FxBlocked("network_denied", "refusing a redirect")


def _exact_url(url):
    if url not in ALLOWED_URLS:
        raise FxBlocked("network_denied", "url is outside the FX reference allowlist")
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password or parts.fragment or parts.port not in (None, 443):
        raise FxBlocked("network_denied", "FX reference requires the exact https endpoint")
    return url


def build_opener():
    """HTTPS opener for the three FX endpoints. It does not connect by itself."""
    context = ssl.create_default_context()
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise FxBlocked("network_denied", "TLS certificate verification is required")
    https = urllib.request.HTTPSHandler(context=context, check_hostname=True)
    proxy = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(proxy, _RefuseRedirect, https)

    def open_request(request, timeout):
        if getattr(request, "full_url", None) not in ALLOWED_URLS:
            raise FxBlocked("network_denied", "opener refused a url outside the FX allowlist")
        if request.get_method() != "GET":
            raise FxBlocked("network_denied", "FX reference is GET only")
        return opener.open(request, timeout=timeout)

    open_request.opener = opener
    open_request.context = context
    return open_request


def http_get(url, opener, timeout=REQUEST_TIMEOUT_S):
    _exact_url(url)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/xml" if url == ECB_URL else "application/json", "User-Agent": "hypergrok-paper-fx/1"},
        method="GET",
    )
    for key in request.headers:
        if key.lower() in {"authorization", "cookie", "x-api-key"}:
            raise FxBlocked("network_denied", "FX reference request carries a credential header")
    if opener is None:
        raise FxBlocked("fx_not_confirmed", "no transport was supplied")
    response = opener(request, timeout=timeout)
    with response:
        status = getattr(response, "status", None) or (response.getcode() if hasattr(response, "getcode") else None)
        final = response.geturl() if hasattr(response, "geturl") else url
        headers = getattr(response, "headers", [])
        raw = response.read(MAX_BODY + 1)
    if final != url:
        raise FxBlocked("network_denied", "refusing a final url that is not the allowlisted endpoint")
    _no_redirect(status, headers)
    if len(raw) > MAX_BODY:
        raise FxBlocked("response_too_large", "FX response exceeds the size bound")
    return raw


def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


def parse_ecb_daily(raw):
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, (bytes, bytearray)):
        raise FxBlocked("units_unknown", "ECB body is not XML bytes")
    if len(raw) > MAX_BODY:
        raise FxBlocked("response_too_large", "ECB body exceeds the size bound")
    lowered = raw.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered or b"<!element" in lowered:
        raise FxBlocked("xml_unsafe", "ECB XML with a doctype or entity is refused")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise FxBlocked("xml_unreadable", "ECB XML is not readable") from exc
    dated = [node for node in root.iter() if _local_name(node.tag) == "Cube" and "time" in node.attrib]
    if len(dated) != 1:
        raise FxBlocked("units_unknown", "the daily ECB file must contain one reference date")
    node = dated[0]
    usd = [child for child in list(node) if _local_name(child.tag) == "Cube" and child.attrib.get("currency") == "USD"]
    if len(usd) != 1 or set(usd[0].attrib) != {"currency", "rate"}:
        raise FxBlocked("units_unknown", "the ECB USD per EUR rate is missing or ambiguous")
    return {"reference_date": node.attrib["time"], "usd_per_eur": _decimal_text(usd[0].attrib["rate"], "ECB USD per EUR")}


def _json_safe(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _json_body(raw):
    if isinstance(raw, (bytes, bytearray)):
        text = raw.decode("utf-8")
    elif isinstance(raw, str):
        text = raw
    else:
        raise FxBlocked("units_unknown", "Kraken body is not JSON")
    try:
        body = json.loads(text, parse_float=Decimal)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FxBlocked("units_unknown", "Kraken body is not JSON") from exc
    return body


def parse_kraken_time(raw):
    body = raw if isinstance(raw, dict) else _json_body(raw)
    if body.get("error") not in ([], ()):
        raise FxBlocked("time_unknown", "Kraken time response reports an error")
    result = body.get("result")
    if not isinstance(result, dict) or type(result.get("unixtime")) is not int:
        raise FxBlocked("time_unknown", "Kraken provider time is missing")
    rfc = result.get("rfc1123")
    if not isinstance(rfc, str):
        raise FxBlocked("time_inconsistent", "Kraken provider time has no rfc1123 twin")
    try:
        parsed = parsedate_to_datetime(rfc)
    except (TypeError, ValueError, IndexError) as exc:
        raise FxBlocked("time_inconsistent", "Kraken rfc1123 time is unreadable") from exc
    if parsed.tzinfo is None:
        raise FxBlocked("time_inconsistent", "Kraken rfc1123 time has no timezone")
    if _epoch(parsed) != Decimal(result["unixtime"]):
        raise FxBlocked("time_inconsistent", "Kraken unixtime and rfc1123 disagree")
    return {"provider_epoch_s": Decimal(result["unixtime"]), "rfc1123": rfc}


def parse_kraken_trade(raw):
    body = raw if isinstance(raw, dict) else _json_body(raw)
    if body.get("error") not in ([], ()):
        raise FxBlocked("units_unknown", "Kraken trades response reports an error")
    result = body.get("result")
    if not isinstance(result, dict) or set(result) - {"USDCUSD", "last"}:
        raise FxBlocked("basequote_unknown", "Kraken trades pair is not USDCUSD")
    rows = result.get("USDCUSD")
    if isinstance(rows, dict) or (isinstance(rows, list) and rows and isinstance(rows[0], dict)):
        raise FxBlocked("not_a_trade", "a ticker or book is not the USDCUSD trade reference")
    if not isinstance(rows, list) or not rows:
        raise FxBlocked("time_unknown", "Kraken returned no USDCUSD trade")
    parsed = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 3:
            raise FxBlocked("not_a_trade", "a Kraken trade row is incomplete")
        price = _decimal_text(row[0], "Kraken trade")
        when = row[2]
        if isinstance(when, bool) or isinstance(when, float) or not isinstance(when, (str, Decimal, int)):
            raise FxBlocked("time_unknown", "Kraken trade time is not an exact decimal")
        if isinstance(when, int):
            when = Decimal(when)
        elif isinstance(when, str):
            if not re.fullmatch(r"[0-9]+(\.[0-9]+)?", when):
                raise FxBlocked("time_unknown", "Kraken trade time is not an exact decimal")
            when = Decimal(when)
        if when <= 0:
            raise FxBlocked("time_unknown", "Kraken trade time is not positive")
        parsed.append((when, price))
    newest = max(item[0] for item in parsed)
    prices = {price for when, price in parsed if when == newest}
    if len(prices) != 1:
        raise FxBlocked("time_inconsistent", "the newest Kraken trades disagree on price")
    return {"usd_per_usdc": prices.pop(), "trade_epoch_s": newest, "trade_count_seen": len(parsed)}


def _stamp(moment):
    fraction = f"{moment.microsecond:06d}".rstrip("0")
    base = moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return base + "Z" if not fraction else f"{base}.{fraction}Z"


def assess_round(document):
    """Recompute one saved round. This performs no HTTP and stores no default rate."""
    if not isinstance(document, dict):
        raise FxBlocked("not_run_fx", "FX input is not a reference round")
    if document.get("purpose") and "NOT approved run FX" in str(document.get("purpose")):
        raise FxBlocked("not_run_fx", "source-identification evidence is not a run FX input")
    if document.get("kind") != "fx_reference_round" or document.get("schema_version") != SCHEMA_VERSION:
        raise FxBlocked("not_run_fx", "only an fx_reference_round can be assessed")
    as_of = _date(document.get("as_of_date"))
    calls = document.get("calls")
    if not isinstance(calls, list) or len(calls) != 3 or [item.get("url") for item in calls] != list(ALLOWED_URLS):
        raise FxBlocked("request_count", "the FX round is exactly the three allowlisted reads, in order")
    moments = []
    for call in calls:
        _exact_url(call.get("url"))
        _no_redirect(call.get("status"), call.get("headers") or [])
        moments.append((_utc(call.get("local_request_utc")), _utc(call.get("local_received_utc"))))
        if moments[-1][1] < moments[-1][0]:
            raise FxBlocked("time_inconsistent", "a response arrived before its request")
    if any(moments[index][0] < moments[index - 1][1] for index in range(1, 3)):
        raise FxBlocked("time_inconsistent", "FX calls overlap or run out of order")
    duration = _epoch(moments[-1][1]) - _epoch(moments[0][0])
    if duration < 0 or duration > MAX_ROUND_S:
        raise FxBlocked("round_duration", "the FX round exceeded 5 seconds")
    provider = parse_kraken_time(calls[0].get("body"))
    trade = parse_kraken_trade(calls[1].get("body"))
    ecb = parse_ecb_daily(calls[2].get("body"))
    skew = abs(_epoch(moments[0][1]) - provider["provider_epoch_s"])
    if skew > MAX_CLOCK_SKEW_S:
        raise FxBlocked("clock_skew", "local clock and Kraken provider time differ by more than 5 seconds")
    age = provider["provider_epoch_s"] - trade["trade_epoch_s"]
    if age < 0:
        raise FxBlocked("future_trade", "the Kraken trade is dated after the provider time")
    if age > MAX_TRADE_AGE_S:
        raise FxBlocked("trade_stale", "the Kraken trade is older than 60 seconds against provider time")
    reference_date = _date(ecb["reference_date"])
    gap = (as_of - reference_date).days
    if gap < 0:
        raise FxBlocked("future_reference", "the ECB reference date is after the as-of date")
    if gap > MAX_CALENDAR_DAYS:
        raise FxBlocked("reference_stale", "the ECB reference date is more than 4 calendar days old")
    reference = CAPITAL * ecb["usd_per_eur"] / trade["usd_per_usdc"]
    return {
        "accepted": True,
        "accepted_for_run_start": False,
        "as_of_date": document["as_of_date"],
        "book_freshness": "not_implied",
        "carry_forward": gap > 0,
        "carry_forward_days": gap,
        "carry_forward_note": "The ECB date is the latest published reference. An earlier date is a carry-forward, not a same-day executable rate." if gap else "The ECB reference date is the as-of date. It is still not an executable conversion.",
        "clock_skew_s": format(skew, "f"),
        "conversion_executed": False,
        "ecb_reference_date": ecb["reference_date"],
        "ecb_url": ECB_URL,
        "ecb_usd_per_eur": format(ecb["usd_per_eur"], "f"),
        "eur_translation": None,
        "eur_translation_status": "unavailable_until_a_new_end_reference",
        "exchange_costs": UNMODELLED,
        "executable_fx": False,
        "formula": "100 * ECB_USD_per_EUR / Kraken_USD_per_USDC",
        "kraken_trade_epoch_s": format(trade["trade_epoch_s"], "f"),
        "kraken_url": KRAKEN_TRADES_URL,
        "kraken_usd_per_usdc": format(trade["usd_per_usdc"], "f"),
        "permanent_label": PERMANENT_LABEL,
        "provider_epoch_s": format(provider["provider_epoch_s"], "f"),
        "provider_time_url": KRAKEN_TIME_URL,
        "reference_usdc": format(reference, "f"),
        "reread_required_at_run_start": True,
        "round_duration_s": format(duration, "f"),
        "stored_as_template_default": False,
        "trade_age_s": format(age, "f"),
        "units": {"eur_usd": "USD per 1 EUR", "usdc_usd": "USD per 1 USDC"},
        "usdc_pnl": None,
        "usdc_pnl_note": "USDC paper PnL stays on the run ledger and is not this reference mapping.",
    }


def capture_reference(opener, as_of_date, clock, monotonic=None):
    """Perform the three reads with the supplied transport. Tests pass a mock opener."""
    _date(as_of_date)
    monotonic = monotonic or time.monotonic
    started = monotonic()
    calls = []
    for url in ALLOWED_URLS:
        if Decimal(str(monotonic() - started)) > MAX_ROUND_S:
            raise FxBlocked("round_duration", "the FX deadline elapsed before the next read")
        request_at = clock()
        raw = http_get(url, opener)
        received_at = clock()
        if url == ECB_URL:
            body = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        else:
            body = _json_safe(_json_body(raw))
        calls.append({
            "body": body,
            "headers": [],
            "local_received_utc": _stamp(received_at),
            "local_request_utc": _stamp(request_at),
            "status": 200,
            "url": url,
        })
        if Decimal(str(monotonic() - started)) > MAX_ROUND_S:
            raise FxBlocked("round_duration", "the FX round exceeded 5 seconds")
    document = {"as_of_date": as_of_date, "calls": calls, "kind": "fx_reference_round", "schema_version": SCHEMA_VERSION}
    assessed = assess_round(document)
    document["assessment"] = assessed
    document["sha256"] = digest(canonical({key: document[key] for key in document if key != "sha256"}))
    return document


def bind_run_fx(document, run_started_utc):
    """Accept a round for a run only when that run start is the round itself."""
    assessed = assess_round(document)
    started = _utc(document["calls"][0]["local_request_utc"])
    finished = _utc(document["calls"][-1]["local_received_utc"])
    moment = _utc(run_started_utc)
    if not started <= moment <= finished:
        raise FxBlocked("reread_required", "a saved FX round is not this run start and must be read again")
    bound = dict(assessed)
    bound["accepted_for_run_start"] = True
    bound["run_started_utc"] = run_started_utc
    return bound


def saved_view(path):
    """Read a local reference file for display. It is not a stored run rate."""
    empty = {
        "accepted_for_run_start": False,
        "book_freshness": "not_implied",
        "exchange_costs": UNMODELLED,
        "note_de": "Kein FX-Referenzlauf. Es ist kein alter Kurs hinterlegt.",
        "permanent_label": PERMANENT_LABEL,
        "present": False,
        "reference_usdc": None,
        "reread_required_at_run_start": True,
        "stored_as_template_default": False,
    }
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        return empty
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("kind") == "fx_reference_round":
            assessed = assess_round(raw)
        else:
            raise FxBlocked("not_run_fx", "this file is not an FX reference round")
    except PaperError as exc:
        blocked = dict(empty)
        blocked.update({"blocked": exc.code, "note_de": str(exc), "present": True, "reference_usdc": None})
        return blocked
    assessed["present"] = True
    assessed["accepted_for_run_start"] = False
    assessed["note_de"] = "Geprüfte Referenzrunde. Sie ist kein gespeicherter Laufkurs und muss beim echten Start neu gelesen werden."
    return assessed


def schema():
    return {
        "schema_version": SCHEMA_VERSION,
        "command": "paper_fx",
        "default": "no socket",
        "network_access": "none unless --execute-reference --confirm-public-fx",
        "allowlist": list(ALLOWED_URLS),
        "separate_from": "hyperliquid /info allowlist",
        "bounds": {"calendar_days": MAX_CALENDAR_DAYS, "trade_age_s": "60", "round_s": "5", "clock_skew_s": "5", "requests": 3},
        "formula": "100 * ECB_USD_per_EUR / Kraken_USD_per_USDC",
        "conversion_executed": False,
        "exchange_costs": UNMODELLED,
        "stored_rates": False,
        "plan_sha256": PLAN_SHA256,
    }


def _emit(code, message):
    print(json.dumps({"schema_version": SCHEMA_VERSION, "error": {"code": code, "message": message}}, sort_keys=True))


def execute_reference(as_of, output, opener=None, clock=None, monotonic=None):
    if output is None:
        _emit("invalid_arguments", "--output is required")
        return 2
    directory = Path(output)
    if directory.exists():
        _emit("invalid_arguments", "output must be a new directory")
        return 2
    transport = opener or build_opener()
    try:
        document = capture_reference(transport, as_of, clock or (lambda: datetime.now(timezone.utc)), monotonic=monotonic)
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "reference.json").write_bytes(pretty(document))
    except (PaperError, OSError) as exc:
        _emit(getattr(exc, "code", "output_unavailable"), str(exc))
        return 2
    print(json.dumps({
        "accepted_for_run_start": False,
        "reference_usdc": document["assessment"]["reference_usdc"],
        "reread_required_at_run_start": True,
        "schema_version": SCHEMA_VERSION,
        "stored_as_template_default": False,
    }, sort_keys=True))
    return 0


def import_round(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict) and raw.get("purpose") and "NOT approved run FX" in str(raw.get("purpose")):
        raise FxBlocked("not_run_fx", "the source manifest identifies endpoints and units; its sample rates are not run inputs")
    assessed = assess_round(raw)
    assessed["imported_from_file"] = True
    assessed["accepted_for_run_start"] = False
    return assessed


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = ReadOnlyParser(prog="paper_fx", description="Assess or optionally read the public FX reference. No Hyperliquid call.", machine_readable="--json" in argv)
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--policy", action="store_true")
    parser.add_argument("--import-round", type=Path)
    parser.add_argument("--execute-reference", action="store_true")
    parser.add_argument("--confirm-public-fx", action="store_true")
    parser.add_argument("--as-of")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.confirm_public_fx and not args.execute_reference:
        parser.error("--confirm-public-fx does not start a read by itself")
    if args.schema or args.policy:
        print(json.dumps(schema(), indent=2, sort_keys=True))
        return 0
    if args.import_round is not None:
        try:
            assessed = import_round(args.import_round)
        except PaperError as exc:
            _emit(exc.code, str(exc))
            return 2
        print(json.dumps(assessed, indent=2, sort_keys=True))
        return 0
    if args.execute_reference:
        if not args.confirm_public_fx:
            _emit("fx_not_confirmed", "public FX reference stays off until --confirm-public-fx is present")
            return 2
        if not args.as_of:
            _emit("invalid_arguments", "--as-of YYYY-MM-DD is required and is not taken from a stored rate")
            return 2
        return execute_reference(args.as_of, args.output)
    parser.error("choose --policy, --import-round, or --execute-reference --confirm-public-fx")


if __name__ == "__main__":
    sys.exit(main())
