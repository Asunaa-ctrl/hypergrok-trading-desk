#!/usr/bin/env python3
"""Local paper-money simulator. Hypothetical fills stay in this process.

Based on HyperGrok Trading Desk by Andrew Wilkinson and Galleon Labs, MIT license.
This module does not sign, read an account, open a wallet, or send an order.
A recorded decision envelope is replayed. No Bot is called and no fresh judge
runs during replay. The supplied judge id is a string, not authenticated authority.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from zoneinfo import ZoneInfo

from cli_contract import ReadOnlyParser

SCHEMA_VERSION = 1
HOUR_MS = 3_600_000
BERLIN = ZoneInfo("Europe/Berlin")
RECOMMENDED_BOT_ID = "a9a5e3d6-724d-44ab-bb6f-ce99f55ac11b"
OFFICIAL_ORIGINS = {
    "https://api.hyperliquid.xyz": "mainnet",
    "https://api.hyperliquid-testnet.xyz": "testnet",
}
INFO_TYPES = {
    "meta": {"type", "dex"},
    "metaAndAssetCtxs": {"type", "dex"},
    "l2Book": {"type", "coin", "nSigFigs", "mantissa"},
    "candleSnapshot": {"type", "req"},
    "fundingHistory": {"type", "coin", "startTime", "endTime"},
}
CANDLE_REQ_KEYS = {"coin", "interval", "startTime", "endTime"}
WS_PUBLIC = {"l2Book", "bbo", "trades", "candle", "activeAssetCtx"}
FORBIDDEN_KEYS = {
    "user", "users", "vaultaddress", "agent", "signature", "nonce",
    "privatekey", "private_key", "secret", "wallet", "authorization",
}
MARKET_TYPES = {"book", "trade", "candle", "asset_ctx", "funding", "gap", "reconnect", "ack"}
REASONS_DE = {
    "missing_decision": "Keine aufgezeichnete Entscheidung für diese Absicht. Es wird kein neues Urteil gebildet.",
    "recorded_reject": "Die aufgezeichnete Entscheidung ist REJECT. Die Simulation führt die Absicht nicht aus.",
    "risk_veto": "Risiko-Veto bindet. Eine aufgezeichnete Freigabe hebt es nicht auf.",
    "expired": "Die Entscheidung ist abgelaufen und wird nicht erneut beurteilt.",
    "already_used": "Die Entscheidung wurde bereits verwendet.",
    "duplicate_intent": "Dieselbe Absicht ist schon im Lauf vorgekommen.",
    "state_changed": "Der simulierte Kontostand entspricht nicht mehr der gebundenen Version.",
    "stale_book": "Das Orderbuch ist älter als die erlaubte Frist.",
    "book_untrusted": "Das Orderbuch ist nach einer Lücke oder einem Reconnect ungültig.",
    "journal_failure": "Das Journal konnte vor der Aktion nicht geschrieben werden. Keine Zustandsänderung.",
    "hard_limit": "Eine harte Grenze der Konfiguration ist verletzt.",
    "lookahead": "Die Entscheidung verweist auf Daten nach ihrem Bewertungszeitpunkt.",
    "holdout_leak": "Die Entscheidung verwendet Holdout-Daten vor dem Holdout-Fenster.",
    "missing_field": "Ein Pflichtfeld der Entscheidung fehlt oder ist widersprüchlich.",
    "hash_mismatch": "Ein Nachweis-Hash stimmt nicht mit Konfiguration, Band, Code oder Belegen überein.",
    "judge_id_mismatch": "Die judge_id der Hülle weicht von der deklarierten Kennung ab. Die Kennung ist keine authentifizierte Autorität.",
    "protection_policy_absent": "Keine Schutz- oder Ausstiegspolitik ist ausgewählt. Neue Exposition wird nicht eröffnet.",
    "protection_integrity": "Die Schutzaktion hat keine gültigen Daten und wird nicht ausgeführt. Die Position bleibt offen.",
    "freshness_limit_mismatch": "Die Entscheidung lockert die konfigurierte Frischegrenze.",
    "intent_mismatch": "Die Absicht weicht von der aufgezeichneten Entscheidung ab.",
    "unavailable_inputs": "Die Freigabe nennt fehlende Pflichtangaben und wird nicht ausgeführt.",
    "unsupported": "Produkt, Margin oder Ordertyp wird nicht unterstützt und nicht stillschweigend ersetzt.",
    "insufficient_cash": "Das virtuelle Cash deckt Notional und Gebühr nicht.",
    "insufficient_depth": "Die sichtbare Tiefe reicht nicht. Es wird keine Liquidität jenseits des Buchs erfunden.",
    "reduce_only": "Reduce-only passt nicht zur simulierten Position.",
    "min_notional": "Der konfigurierte Mindestnotional ist nicht erreicht.",
    "precision": "Preis oder Größe liegt nicht auf dem erlaubten Raster.",
    "capacity": "Positions- oder Notionalgrenze der Konfiguration ist überschritten.",
    "max_loss": "Die konfigurierte Verlustgrenze ist erreicht. Neue Exposition wird abgelehnt.",
    "mark_unavailable": "Ohne Markpreis ist die Verlustgrenze nicht prüfbar.",
    "no_book": "Es gibt kein Orderbuch für eine hypothetische Ausführung.",
    "crossed_book": "Das Orderbuch ist gekreuzt und wird nicht benutzt.",
    "not_yet_eligible": "Die konfigurierte Latenz ist im Band noch nicht erreicht.",
    "post_only_would_take": "Add-liquidity-only würde das Buch kreuzen und wird abgelehnt.",
    "berlin_time_mismatch": "Die Berlin-Zeit weicht von der UTC-Entscheidungszeit ab.",
    "policy_trigger": "Die eingefrorene Schutzpolitik hat ausgelöst. Das ist kein Judge-Urteil.",
    "gates_passed": "Alle geprüften lokalen Tore sind erfüllt. Das ist keine Strategievalidierung.",
    "pending_strategy": "Es ist keine Nutzerstrategie hinterlegt. Es wird keine Strategie erfunden.",
    "pending_native_decision": "Eine native Entscheidung liegt nicht vor. Es wird kein Urteil erzeugt.",
    "fx_unavailable": "Ohne datierten belegten Wechselkurs bleibt das EUR-Budget unkonvertiert. Nutzbares USDC-Quote ist nicht verfügbar.",
    "cancelled": "Die lokale Order wurde vor einer weiteren Ausführung storniert.",
    "expired_order": "Die lokale Order ist nach ihrer eigenen Frist abgelaufen.",
    "awaiting_fresh_book": "Die Order wartet auf ein frisches Buch und wurde nicht gefüllt.",
}
SUPPORTED = [
    "recorded decision envelopes replayed locally; binary APPROVE or REJECT",
    "risk veto, hard limits, freshness, state-hash binding, expiry, single-use, reservations",
    "journal-before-action; a journal failure blocks the hypothetical mutation",
    "entry and exit envelopes; exits are reduce-only",
    "explicit protection mode recorded_exits_only or stop_mark, priority before discretionary decisions",
    "cash-secured linear perp without leverage; taker book walk; conservative maker queue or unsupported maker",
    "hourly funding only with oracle and rate on the payment event",
    "public /info allowlist for an injected transport; paper_sim itself opens no socket",
]
UNIMPLEMENTED = [
    "no runtime connection to Desk Lead or any Bot; native acknowledgement is pending",
    "a judge_id string is not authenticated authority and is not checked against a live session",
    "no live chat judge, no fresh hindsight judgment, no callable judge plugin",
    "no mainnet or testnet order, cancel, transfer, approval, or account read",
    "no leverage, cross margin, isolated margin, or liquidation engine",
    "no automatic protection policy; absence rejects new exposure instead of inventing a stop",
    "no claim that public L2 is queue position or that candles contain the intrabar path",
    "paper_sim opens no socket; paper_capture posts public /info only after --confirm-public-info",
    "human trade-reviewer grading is not run; the process journal is mechanical",
]
LIMITATIONS = [
    "Local paper only. APPROVE is permission for a hypothetical in-process action, never an exchange ticket or testnet order.",
    "Fills are model outputs. Public L2 does not prove queue position. A touched price is not a fill.",
    "Candles do not establish intrabar order, historical spread, historical depth, or the historical oracle.",
    "Funding needs the hourly rate and the oracle on that payment event. Missing evidence is unavailable, not zero. The rate is not divided by eight.",
    "Fee rates are the explicit scenario. No account fee tier, referral, staking discount, or HIP-3 scale is queried or assumed.",
    "Hashes show local consistency, not independent provenance or tamper-proof storage. Rewriting the decisions and every derived file creates a new local story; it does not become authenticated authority.",
    "Passing engineering tests do not validate a strategy and are not market evidence.",
    "The recommended Desk Lead id is a role-fit note. This process does not contact that Bot.",
    "Liquidation and leverage are not modelled. Negative virtual cash is unresolved insolvency, not a liquidation.",
    "A cited research bundle, when required, is checked offline. The bundle is not proof the tape came from that capture.",
    "Recorded decisions are not rewritten. Mechanical gates may refuse to act on an APPROVE.",
]


class PaperError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class JournalFailure(PaperError):
    def __init__(self, key):
        super().__init__("journal_failure", f"journal refused {key}")
        self.key = key


class NetworkDenied(PaperError):
    def __init__(self, message):
        super().__init__("network_denied", message)


def digest(data):
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def pretty(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()


def load_exact(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise PaperError("invalid_arguments", "input must be a regular file, not a symlink")
    text = path.read_text(encoding="utf-8")
    if len(text.encode()) > 5_000_000:
        raise PaperError("invalid_arguments", "input file exceeds 5 MB")
    return json.loads(text, parse_float=_reject_float)


def _reject_float(_value):
    raise PaperError("float_rejected", "JSON numbers with a fraction are refused; use decimal strings")


def dec_str(value):
    if not isinstance(value, Decimal):
        value = Decimal(value)
    if not value.is_finite():
        raise PaperError("nonfinite", "nonfinite decimal")
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def money(value, name, *, positive=False, nonnegative=False):
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, str) and value and len(value) <= 100:
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise PaperError("config_incomplete", f"{name} is not a decimal string") from exc
    else:
        raise PaperError("config_incomplete", f"{name} must be a decimal string")
    if not result.is_finite() or (positive and result <= 0) or (nonnegative and result < 0):
        raise PaperError("config_incomplete", f"{name} has an invalid decimal value")
    return result


def need_keys(value, keys, where):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise PaperError("config_incomplete", f"{where} must contain exactly {', '.join(sorted(keys))}")


def need_text(value, name, pattern_ok=True):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise PaperError("config_incomplete", f"{name} must be a non-empty string")
    if pattern_ok and not all(ch.isalnum() or ch in "._:-" for ch in value):
        raise PaperError("config_incomplete", f"{name} contains unsupported characters")
    return value


def berlin_stamp(ms):
    if type(ms) is not int:
        raise PaperError("missing_field", "decision time must be integer milliseconds")
    moment = datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(BERLIN)
    return moment.isoformat(timespec="milliseconds")


def code_sha256():
    return digest(Path(__file__).resolve().read_bytes())


def role_provenance(declared_id):
    path = Path(__file__).resolve().parents[1] / "agents" / "desk-lead.md"
    present = path.is_file() and not path.is_symlink()
    file_hash = digest(path.read_bytes()) if present else None
    return {
        "role_ref": "desk-lead",
        "role_file": "agents/desk-lead.md",
        "role_file_present": present,
        "role_file_sha256": file_hash,
        "recommended_bot_id": RECOMMENDED_BOT_ID,
        "declared_id": declared_id,
        "declared_id_matches_recommendation": declared_id == RECOMMENDED_BOT_ID,
        "recommendation_status": "role_understanding_acknowledged_not_authenticated",
        "bot_created": False,
        "bot_contacted": False,
        "runtime_integration": False,
        "authenticated": False,
        "note": "The existing Desk Lead has acknowledged the local-paper mandate as role understanding. That acknowledgement is not an authenticated connection, and this process does not call the Bot. Equality of the declared judge_id with the recommended id does not authenticate anything.",
    }


def binding_payload(cash, position, average, mark, reservations, used, open_orders):
    return {
        "average_price": None if average is None else dec_str(average),
        "cash": dec_str(cash),
        "mark": None if mark is None else dec_str(mark),
        "open_orders": open_orders,
        "position": dec_str(position),
        "reservations": list(reservations),
        "used_decisions": list(used),
    }


def binding_hash(cash, position, average, mark, reservations, used, open_orders):
    payload = binding_payload(cash, position, average, mark, reservations, used, open_orders)
    payload["reservations"] = sorted(payload["reservations"])
    payload["used_decisions"] = sorted(payload["used_decisions"])
    payload["open_orders"] = sorted(payload["open_orders"], key=lambda item: item["client_id"])
    return digest(canonical(payload))


def _window(value, name):
    need_keys(value, ["start_ms", "end_ms"], name)
    start, end = value["start_ms"], value["end_ms"]
    if type(start) is not int or type(end) is not int or start < 0 or start >= end:
        raise PaperError("config_incomplete", f"{name} must be an increasing millisecond window")
    return start, end


def validate_config(raw):
    """Parse an explicit config. Missing capital, risk, fees, or policy fields raise."""
    keys = {
        "schema_version", "mode", "run_id", "evidence_class", "claims_market_evidence",
        "source_network", "requires_complete_coverage", "research_bundle", "market",
        "capital", "risk", "fees", "fx", "execution", "evaluation", "strategy", "protection",
        "judge", "decision_source",
    }
    need_keys(raw, keys, "config")
    if raw["schema_version"] != SCHEMA_VERSION or raw["mode"] != "local_paper":
        raise PaperError("unsupported", "config schema must be local_paper version 1")
    if raw["decision_source"] != "recorded_envelopes":
        raise PaperError("unsupported", "only recorded_envelopes is a decision source; a live Bot is not called")
    evidence = raw["evidence_class"]
    if evidence not in ("synthetic_engineering_fixture", "captured_public_replay", "loopback_fixture"):
        raise PaperError("config_incomplete", "evidence_class is not a known local class")
    if type(raw["claims_market_evidence"]) is not bool or type(raw["requires_complete_coverage"]) is not bool:
        raise PaperError("config_incomplete", "evidence flags must be booleans")
    if evidence in ("synthetic_engineering_fixture", "loopback_fixture"):
        if raw["claims_market_evidence"] or raw["requires_complete_coverage"] or raw["research_bundle"] is not None:
            raise PaperError("config_incomplete", "a fixture cannot claim market evidence or cite a bundle")
        expected_network = "loopback" if evidence == "loopback_fixture" else "offline"
        if raw["source_network"] != expected_network:
            raise PaperError("config_incomplete", "fixture network label does not match its evidence class")
    else:
        if raw["source_network"] not in ("mainnet", "testnet"):
            raise PaperError("config_incomplete", "captured replay must name mainnet or testnet")
        if raw["research_bundle"] is not None and not isinstance(raw["research_bundle"], str):
            raise PaperError("config_incomplete", "research_bundle must be a path or null")
        if raw["claims_market_evidence"] and not raw["requires_complete_coverage"]:
            raise PaperError("config_incomplete", "market-evidence claims require a complete-bundle check")
    need_text(raw["run_id"], "run_id")
    market = raw["market"]
    need_keys(market, ["coin", "dex", "product", "margin_model", "quote", "sz_decimals", "min_notional_usd", "allow_dust_reduce_only"], "market")
    if market["product"] != "linear_perp":
        raise PaperError("unsupported", "only a cash-secured linear perp is supported")
    if market["margin_model"] != "cash_secured_no_leverage":
        raise PaperError("unsupported", "leverage, isolated margin and cross margin are not implemented")
    if market["quote"] != "USDC":
        raise PaperError("unsupported", "only USDC quote accounting is supported")
    coin, dex = market["coin"], market["dex"]
    if not isinstance(coin, str) or not coin:
        raise PaperError("config_incomplete", "coin is required")
    if dex is not None and (not isinstance(dex, str) or dex != dex.lower()):
        raise PaperError("config_incomplete", "dex must be a lowercase name or null")
    if ":" in coin and coin.split(":", 1)[0] != dex:
        raise PaperError("config_incomplete", "coin prefix and dex must match")
    sz = market["sz_decimals"]
    if type(sz) is not int or not 0 <= sz <= 8:
        raise PaperError("config_incomplete", "sz_decimals must be an integer from 0 to 8")
    if type(market["allow_dust_reduce_only"]) is not bool:
        raise PaperError("config_incomplete", "allow_dust_reduce_only must be boolean")
    capital = raw["capital"]
    need_keys(capital, ["starting_cash", "currency"], "capital")
    if capital["currency"] not in ("USDC", "EUR"):
        raise PaperError("unsupported", "budget currency must be USDC or EUR; it is not silently converted")
    budget_cash = money(capital["starting_cash"], "starting_cash", positive=True)
    fx_status, usable_quote = _fx(raw["fx"], capital["currency"], budget_cash)
    risk = raw["risk"]
    need_keys(risk, ["max_position_base", "max_notional_usd", "max_loss_usd"], "risk")
    fees = raw["fees"]
    need_keys(fees, ["taker_rate", "maker_rate", "scenario_id", "scenario_note"], "fees")
    need_text(fees["scenario_id"], "fee scenario_id")
    if not isinstance(fees["scenario_note"], str) or len(fees["scenario_note"]) < 8:
        raise PaperError("config_incomplete", "fee scenario_note must describe the explicit scenario")
    execution = raw["execution"]
    need_keys(execution, ["latency_ms", "stale_book_ms", "participation_ratio", "cancel_latency_ms", "maker_model", "incomplete_fill", "funding_requirement"], "execution")
    for name in ("latency_ms", "stale_book_ms", "cancel_latency_ms"):
        if type(execution[name]) is not int or execution[name] < 0:
            raise PaperError("config_incomplete", f"{name} must be a nonnegative integer")
    if execution["maker_model"] not in ("conservative_back_of_visible_queue", "maker_unsupported"):
        raise PaperError("unsupported", "maker model must be conservative or explicitly unsupported")
    if execution["incomplete_fill"] not in ("partial_then_rest", "partial_then_cancel", "reject_if_incomplete"):
        raise PaperError("unsupported", "incomplete-fill policy is not supported")
    if execution["funding_requirement"] != "exact_oracle_only":
        raise PaperError("unsupported", "funding proxies are not implemented; exact oracle evidence is required")
    evaluation = raw["evaluation"]
    need_keys(evaluation, ["train", "validation", "holdout", "embargo_ms", "trial_id", "prior_trial_count", "prior_holdout_access_count"], "evaluation")
    train = _window(evaluation["train"], "train")
    validation = _window(evaluation["validation"], "validation")
    holdout = _window(evaluation["holdout"], "holdout")
    embargo = evaluation["embargo_ms"]
    if type(embargo) is not int or embargo < 0:
        raise PaperError("config_incomplete", "embargo_ms must be a nonnegative integer")
    if train[1] + embargo > validation[0] or validation[1] + embargo > holdout[0]:
        raise PaperError("config_incomplete", "evaluation windows must be chronological and respect the embargo")
    need_text(evaluation["trial_id"], "trial_id")
    for name in ("prior_trial_count", "prior_holdout_access_count"):
        if type(evaluation[name]) is not int or evaluation[name] < 0:
            raise PaperError("config_incomplete", f"{name} must be a nonnegative integer")
    strategy = raw["strategy"]
    need_keys(strategy, ["status", "id", "rules_ref", "rules_hash", "interface"], "strategy")
    if strategy["interface"] != "tape_intents":
        raise PaperError("unsupported", "only tape_intents is supported; no strategy is loaded or invented")
    if strategy["status"] == "pending":
        if any(strategy[key] is not None for key in ("id", "rules_ref", "rules_hash")):
            raise PaperError("config_incomplete", "a pending strategy must not invent an id or a rules hash")
    elif strategy["status"] == "supplied":
        need_text(strategy["id"], "strategy.id")
        if not isinstance(strategy["rules_ref"], str) or not strategy["rules_ref"].strip():
            raise PaperError("config_incomplete", "strategy.rules_ref is required and is not opened or fetched")
        if not isinstance(strategy["rules_hash"], str) or len(strategy["rules_hash"]) != 64:
            raise PaperError("config_incomplete", "strategy.rules_hash must be a caller-supplied sha256 hex string")
        try:
            int(strategy["rules_hash"], 16)
        except ValueError as exc:
            raise PaperError("config_incomplete", "strategy.rules_hash must be hexadecimal") from exc
    else:
        raise PaperError("config_incomplete", "strategy.status must be pending or supplied")
    protection = _protection(raw["protection"])
    judge = raw["judge"]
    need_keys(judge, ["declared_id", "role_ref", "authority"], "judge")
    need_text(judge["declared_id"], "judge.declared_id")
    if judge["role_ref"] != "desk-lead":
        raise PaperError("unsupported", "local paper decisions cite the existing desk-lead role only")
    if judge["authority"] != "unauthenticated_string":
        raise PaperError("unsupported", "this simulator cannot record the judge id as authenticated authority")
    parsed = {
        "raw": raw,
        "run_id": raw["run_id"],
        "evidence_class": evidence,
        "claims_market_evidence": raw["claims_market_evidence"],
        "source_network": raw["source_network"],
        "requires_complete_coverage": raw["requires_complete_coverage"],
        "research_bundle": raw["research_bundle"],
        "coin": coin,
        "dex": dex,
        "sz_decimals": sz,
        "quantum": Decimal(1).scaleb(-sz),
        "min_notional": money(market["min_notional_usd"], "min_notional_usd", positive=True),
        "allow_dust": market["allow_dust_reduce_only"],
        "budget_cash": budget_cash,
        "budget_currency": capital["currency"],
        "fx_status": fx_status,
        "usable_quote": usable_quote,
        "starting_cash": budget_cash if capital["currency"] == "USDC" else (usable_quote if usable_quote is not None else Decimal(0)),
        "quote_enabled": capital["currency"] == "USDC" or usable_quote is not None,
        "max_position": money(risk["max_position_base"], "max_position_base", positive=True),
        "max_notional": money(risk["max_notional_usd"], "max_notional_usd", positive=True),
        "max_loss": money(risk["max_loss_usd"], "max_loss_usd", positive=True),
        "taker_rate": money(fees["taker_rate"], "taker_rate", nonnegative=True),
        "maker_rate": money(fees["maker_rate"], "maker_rate", nonnegative=True),
        "latency_ms": execution["latency_ms"],
        "stale_ms": execution["stale_book_ms"],
        "participation": money(execution["participation_ratio"], "participation_ratio", positive=True),
        "cancel_latency_ms": execution["cancel_latency_ms"],
        "maker_model": execution["maker_model"],
        "incomplete_fill": execution["incomplete_fill"],
        "train": train,
        "validation": validation,
        "holdout": holdout,
        "embargo_ms": embargo,
        "trial_id": evaluation["trial_id"],
        "prior_trials": evaluation["prior_trial_count"],
        "prior_holdout": evaluation["prior_holdout_access_count"],
        "strategy": strategy,
        "protection": protection,
        "judge_id": judge["declared_id"],
    }
    if parsed["participation"] > 1:
        raise PaperError("config_incomplete", "participation_ratio cannot exceed the visible size")
    return parsed


def _fx(value, currency, budget):
    """Map a budget into venue USDC only with an explicit dated citation. Never assume USD equals USDC."""
    if not isinstance(value, dict) or "status" not in value:
        raise PaperError("config_incomplete", "fx.status is required")
    if currency == "USDC":
        need_keys(value, ["status", "reason", "usd_usdc"], "fx")
        if value["status"] != "not_applicable" or value["usd_usdc"] != "not_used":
            raise PaperError("config_incomplete", "a USDC budget is the venue quote and is not an FX conversion")
        if not isinstance(value["reason"], str) or len(value["reason"]) < 8:
            raise PaperError("config_incomplete", "fx.reason must say why no conversion is applied")
        return "not_applicable", budget
    if currency != "EUR":
        raise PaperError("unsupported", "budget currency is not supported")
    if value.get("status") == "reference_valuation":
        need_keys(value, ["round", "run_started_utc", "status"], "fx")
        if budget != Decimal("100"):
            raise PaperError("fx_unavailable", "the reference valuation is defined for the 100 EUR budget")
        from paper_fx import bind_run_fx
        bound = bind_run_fx(value["round"], value["run_started_utc"])
        return "reference_valuation", money(bound["reference_usdc"], "reference_usdc", positive=True)
    need_keys(value, ["status", "reason", "pair", "rate", "as_of", "source_ref", "usd_usdc_status", "usd_usdc_rate", "usd_usdc_as_of", "usd_usdc_source_ref"], "fx")
    if not isinstance(value["reason"], str) or len(value["reason"]) < 8:
        raise PaperError("config_incomplete", "fx.reason is required")
    if value["status"] == "unavailable":
        if value["usd_usdc_status"] != "unavailable":
            raise PaperError("config_incomplete", "unavailable EUR FX cannot claim a USD/USDC rate")
        if any(value[key] is not None for key in ("pair", "rate", "as_of", "source_ref", "usd_usdc_rate", "usd_usdc_as_of", "usd_usdc_source_ref")):
            raise PaperError("config_incomplete", "unavailable FX must not contain an invented rate")
        return "unavailable", None
    if value["status"] != "cited":
        raise PaperError("config_incomplete", "fx.status must be unavailable or cited for EUR")
    pair = value["pair"]
    rate = money(value["rate"], "fx.rate", positive=True)
    if not isinstance(value["as_of"], str) or len(value["as_of"]) < 10:
        raise PaperError("config_incomplete", "fx.as_of must be an explicit date")
    if not isinstance(value["source_ref"], str) or len(value["source_ref"]) < 8:
        raise PaperError("config_incomplete", "fx.source_ref must cite the rate; it is not fetched")
    if pair == "EUR/USDC":
        if value["usd_usdc_status"] != "not_applicable" or any(value[key] is not None for key in ("usd_usdc_rate", "usd_usdc_as_of", "usd_usdc_source_ref")):
            raise PaperError("config_incomplete", "an EUR/USDC citation has no separate USD leg")
        return "cited", budget * rate
    if pair != "EUR/USD":
        raise PaperError("unsupported", "FX pair must be EUR/USD or EUR/USDC")
    if value["usd_usdc_status"] == "unavailable":
        return "usd_known_usdc_unavailable", None
    if value["usd_usdc_status"] != "cited":
        raise PaperError("config_incomplete", "USD/USDC must be cited or explicitly unavailable; 1:1 is not assumed")
    usd_usdc = money(value["usd_usdc_rate"], "usd_usdc_rate", positive=True)
    if not isinstance(value["usd_usdc_as_of"], str) or len(value["usd_usdc_as_of"]) < 10:
        raise PaperError("config_incomplete", "usd_usdc_as_of is required")
    if not isinstance(value["usd_usdc_source_ref"], str) or len(value["usd_usdc_source_ref"]) < 8:
        raise PaperError("config_incomplete", "usd_usdc_source_ref is required")
    if usd_usdc == 1:
        raise PaperError("config_incomplete", "a USD/USDC rate of 1 is not accepted as an implicit parity assumption")
    return "cited", budget * rate * usd_usdc


def validate_paper_template(raw):
    """Validate the explicit 100 EUR paper template. This does not start a run or select a strategy."""
    keys = {
        "auto_start", "campaign", "capital", "execution", "exit", "fx", "ledger_quote",
        "mode", "observation", "paper_limits", "plan_sha256", "readiness", "schema_version",
        "status", "strategy", "training",
    }
    if raw.get("plan_sha256") != "ada0def6834c7ddac2953c325bcce9a86f2e0ff3d5669180648b8e7c47a40b9c":
        raise PaperError("hash_mismatch", "template is not bound to plan ada0def6")
    need_keys(raw, keys, "paper template")
    if raw["schema_version"] != SCHEMA_VERSION or raw["mode"] != "paper_config_template":
        raise PaperError("unsupported", "template mode must be paper_config_template")
    if raw["status"] != "template_not_a_run" or raw["auto_start"] is not False or raw["training"] != "not_run":
        raise PaperError("config_incomplete", "the template must stay unrun, with training not started")
    capital = raw["capital"]
    need_keys(capital, ["starting_cash", "currency"], "template capital")
    if capital["currency"] != "EUR" or capital["starting_cash"] != "100.00":
        raise PaperError("config_incomplete", "the selected virtual budget is 100.00 EUR and is not rewritten")
    if raw["ledger_quote"] != "USDC":
        raise PaperError("config_incomplete", "the venue ledger quote is USDC and is not the EUR budget")
    fx = raw["fx"]
    need_keys(fx, ["conversion_executed", "eur_usd", "exchange_costs", "formula", "max_clock_skew_s", "max_round_s", "policy", "provider_time", "stored_rates", "usdc_usd"], "template fx")
    if fx["policy"] != "reference_valuation_only" or fx["conversion_executed"] is not False or fx["exchange_costs"] != "UNMODELLED" or fx["stored_rates"] is not False:
        raise PaperError("config_incomplete", "FX is a reference valuation with unmodelled conversion costs and no stored rate")
    if fx["formula"] != "100 * ECB_USD_per_EUR / Kraken_USD_per_USDC" or fx["max_round_s"] != 5 or fx["max_clock_skew_s"] != 5:
        raise PaperError("config_incomplete", "the FX formula and retrieval bounds are fixed by the selected policy")
    if fx["provider_time"] != "https://api.kraken.com/0/public/Time":
        raise PaperError("config_incomplete", "provider time is the Kraken public Time endpoint")
    eur, usdc = fx["eur_usd"], fx["usdc_usd"]
    need_keys(eur, ["as_of", "executable", "max_calendar_days", "rate", "reference_date", "source", "status", "units"], "eur_usd")
    need_keys(usdc, ["event_time", "max_trade_age_s", "observation", "parity_assumed", "rate", "source", "status", "units"], "usdc_usd")
    if eur["status"] != "required_reread_at_run_start" or eur["rate"] is not None or eur["as_of"] is not None or eur["reference_date"] is not None or eur["executable"] is not False:
        raise PaperError("config_incomplete", "no EUR/USD rate is stored in the template")
    if eur["source"] != "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml" or eur["units"] != "USD per 1 EUR" or eur["max_calendar_days"] != 4:
        raise PaperError("config_incomplete", "EUR/USD is the ECB daily reference, at most 4 calendar days old")
    if usdc["status"] != "required_reread_at_run_start" or usdc["rate"] is not None or usdc["event_time"] is not None or usdc["parity_assumed"] is not False:
        raise PaperError("config_incomplete", "USDC/USD is unavailable in the template and 1:1 is not a default")
    if usdc["source"] != "https://api.kraken.com/0/public/Trades?pair=USDCUSD&count=1" or usdc["units"] != "USD per 1 USDC" or usdc["observation"] != "last_trade_only" or usdc["max_trade_age_s"] != 60:
        raise PaperError("config_incomplete", "USDC/USD is the latest Kraken USDCUSD trade, not a book or a ticker")
    observation = raw["observation"]
    need_keys(observation, ["market", "phase", "opens_exposure"], "observation")
    if observation["market"] != "BTC" or observation["phase"] != "observation_first" or observation["opens_exposure"] is not False:
        raise PaperError("config_incomplete", "BTC observation comes first and opens no exposure")
    strategy = raw["strategy"]
    need_keys(strategy, ["status", "id", "rules_ref", "rules_hash"], "template strategy")
    if strategy["status"] != "required_unselected" or any(strategy[key] is not None for key in ("id", "rules_ref", "rules_hash")):
        raise PaperError("config_incomplete", "strategy stays required and unselected")
    exit_policy = raw["exit"]
    need_keys(exit_policy, ["status", "policy_id"], "template exit")
    if exit_policy["status"] != "required_unselected" or exit_policy["policy_id"] is not None:
        raise PaperError("config_incomplete", "exit policy stays required and unselected")
    execution = raw["execution"]
    need_keys(execution, ["order_types", "maker_fills"], "template execution")
    if execution["order_types"] != "taker_only" or execution["maker_fills"] != "disabled":
        raise PaperError("unsupported", "this template is taker-only")
    limits = raw["paper_limits"]
    need_keys(limits, ["absolute_usdc", "authority", "daily_loss_stop", "live_desk_limits_unchanged", "max_gross_notional_multiple", "max_markets", "max_open_risk", "max_positions", "max_risk_per_trade", "run_kill_drawdown", "unit"], "paper limits")
    if limits["authority"] != "claude_selected_under_user_delegation_pos170" or limits["live_desk_limits_unchanged"] is not True:
        raise PaperError("config_incomplete", "paper limits are not live desk limits")
    if limits["absolute_usdc"] is not None or limits["unit"] != "fraction_of_virtual_equity":
        raise PaperError("config_incomplete", "limits stay fractions until a cited FX conversion exists")
    expected = {"max_risk_per_trade": "0.005", "max_open_risk": "0.01", "max_gross_notional_multiple": "1", "daily_loss_stop": "0.02", "run_kill_drawdown": "0.05"}
    for key, value in expected.items():
        if limits[key] != value:
            raise PaperError("config_incomplete", f"{key} is not the selected conservative paper fraction")
    if limits["max_positions"] != 1 or limits["max_markets"] != 1:
        raise PaperError("config_incomplete", "one position and one market")
    campaign = raw["campaign"]
    need_keys(campaign, ["ignored_alternatives", "max_calendar_days", "max_sim_trades", "started", "then", "whichever_first"], "campaign")
    if campaign["max_calendar_days"] != 3 or campaign["max_sim_trades"] != 5 or campaign["whichever_first"] is not True or campaign["started"] is not False or campaign["then"] != "stop_for_review":
        raise PaperError("config_incomplete", "the first bound is 3 days or 5 simulated trades, then stop")
    if campaign["ignored_alternatives"] != ["14 calendar days", "20 simulated trades"]:
        raise PaperError("config_incomplete", "the 14-day and 20-trade example is not the bound")
    readiness = raw["readiness"]
    need_keys(readiness, ["later_start_authorized_when_gates_pass", "plan_status", "ready", "unavailable"], "template readiness")
    if readiness["plan_status"] != "planning_evidence_not_readiness" or readiness["ready"] is not False or readiness["later_start_authorized_when_gates_pass"] is not True:
        raise PaperError("config_incomplete", "the plan is not readiness and the template is not ready")
    required_missing = {"strategy", "exit", "eur_usd_rate", "usdc_usd_rate", "native_handoff", "bounded_live_run"}
    if set(readiness["unavailable"]) != required_missing:
        raise PaperError("config_incomplete", "unavailable gates must stay listed and unresolved")
    return {"ready": False, "run_started": False, "training": "not_run", "budget": "100.00 EUR", "usable_usdc_quote": None}


def _protection(value):
    if not isinstance(value, dict) or "selected" not in value or type(value["selected"]) is not bool:
        raise PaperError("config_incomplete", "protection.selected is required")
    if value["selected"] is False:
        need_keys(value, ["selected"], "protection")
        return {"selected": False}
    mode = value.get("mode")
    if mode == "recorded_exits_only":
        need_keys(value, ["selected", "mode", "policy_id", "priority", "audit", "risk_treatment"], "protection")
    elif mode == "stop_mark":
        need_keys(value, ["selected", "mode", "policy_id", "priority", "audit", "risk_treatment", "side", "trigger_mark", "size", "depth", "max_book_age_ms"], "protection")
    else:
        raise PaperError("unsupported", "protection mode must be recorded_exits_only or stop_mark when selected")
    need_text(value["policy_id"], "protection.policy_id")
    if value["priority"] != "before_discretionary":
        raise PaperError("unsupported", "protection must run before discretionary decisions and cannot wait on a judge")
    if value["audit"] != "journal_before_action" or value["risk_treatment"] != "hard_limits_still_bind":
        raise PaperError("unsupported", "protection cannot waive the journal or hard limits")
    parsed = {"selected": True, "mode": mode, "policy_id": value["policy_id"]}
    if mode == "stop_mark":
        if value["side"] not in ("long", "short") or value["size"] != "full_position" or value["depth"] != "visible_book_only":
            raise PaperError("unsupported", "stop_mark closes the full position against visible depth only")
        if type(value["max_book_age_ms"]) is not int or value["max_book_age_ms"] < 0:
            raise PaperError("config_incomplete", "protection max_book_age_ms is required")
        parsed.update({
            "side": value["side"],
            "trigger": money(value["trigger_mark"], "trigger_mark", positive=True),
            "max_book_age_ms": value["max_book_age_ms"],
        })
    return parsed


def assert_bundle(parsed):
    if not parsed["requires_complete_coverage"]:
        return None
    if not parsed["research_bundle"]:
        raise PaperError("incomplete_evidence", "complete coverage was required and no bundle path was given")
    from research_dataset import verify_bundle
    try:
        manifest = verify_bundle(parsed["research_bundle"])
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        raise PaperError("incomplete_evidence", f"research bundle is not a verified complete capture: {exc}") from exc
    if manifest.get("result", {}).get("status") != "complete":
        raise PaperError("incomplete_evidence", "research bundle is not complete; the experiment is refused")
    market = (manifest.get("result") or {}).get("market") or {}
    coin = market.get("coin") or (manifest.get("parameters") or {}).get("coin")
    if coin != parsed["coin"]:
        raise PaperError("incomplete_evidence", "research bundle coin does not match the config")
    return digest(canonical(manifest.get("files", {})))


class PublicInfoClient:
    """Allowlist for public market info. The caller must pass a transport.

    This class never builds a socket of its own. The CLI does not call it.
    """

    def __init__(self, origin):
        self.origin = origin.rstrip("/") if isinstance(origin, str) else ""

    def validate(self, payload):
        if self.origin not in OFFICIAL_ORIGINS:
            raise NetworkDenied("origin is not an official public Hyperliquid info origin")
        if not isinstance(payload, dict):
            raise NetworkDenied("payload must be an object")
        kind = payload.get("type")
        allowed = INFO_TYPES.get(kind)
        if allowed is None or set(payload) - allowed:
            raise NetworkDenied("info type or field is outside the public market allowlist")
        self._walk(payload)
        if kind == "candleSnapshot":
            req = payload["req"]
            if not isinstance(req, dict) or set(req) != CANDLE_REQ_KEYS:
                raise NetworkDenied("candleSnapshot request is outside the allowlist")
            self._walk(req)
        return OFFICIAL_ORIGINS[self.origin]

    def submit(self, path, payload, opener):
        if path != "/info":
            raise NetworkDenied("refusing a path other than the public info path")
        self.validate(payload)
        if opener is None:
            raise PaperError("unsupported", "no transport is shipped; this build does not open a socket")
        return opener(self.origin + "/info", payload)

    def _walk(self, value):
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str) or key.lower() in FORBIDDEN_KEYS or "private" in key.lower() or "secret" in key.lower():
                    raise NetworkDenied("payload contains an account, user, or signing field")
                self._walk(item)
        elif isinstance(value, list):
            for item in value:
                self._walk(item)


def validate_subscription(subscription):
    if not isinstance(subscription, dict) or subscription.get("type") not in WS_PUBLIC:
        raise NetworkDenied("subscription is outside the public market allowlist")
    if set(subscription) - {"type", "coin", "interval", "nSigFigs", "mantissa"}:
        raise NetworkDenied("subscription field is outside the public market allowlist")
    PublicInfoClient(next(iter(OFFICIAL_ORIGINS)))._walk(subscription)
    return subscription["type"]


def normalize_public_trade(payload):
    """Convert one public trade object. Account address fields are refused."""
    if not isinstance(payload, dict):
        raise PaperError("unsupported", "trade payload must be an object")
    if "users" in payload or "user" in payload:
        raise PaperError("network_denied", "trade payload carries account addresses and is refused")
    side = payload.get("side")
    aggressor = {"B": "buy", "A": "sell", "buy": "buy", "sell": "sell"}.get(side)
    if aggressor is None:
        raise PaperError("unsupported", "trade side is not a public aggressor flag")
    return {"px": payload.get("px"), "sz": payload.get("sz"), "aggressor": aggressor, "coin": payload.get("coin")}


class RecordedDecisionSource:
    """Local import surface. Reads envelopes already in memory or on disk.

    There is no method that contacts a Bot, a model, or a network. Replay must
    not construct a fresh verdict.
    """

    runtime_bot_integration = False
    authenticated = False

    def __init__(self, document):
        self.lookups = 0
        self.invalid = []
        self.envelopes = {}
        if not isinstance(document, dict) or set(document) != {"schema_version", "evidence_class", "envelopes"}:
            raise PaperError("config_incomplete", "decisions document shape is invalid")
        if document["schema_version"] != SCHEMA_VERSION:
            raise PaperError("unsupported", "decisions schema version is not supported")
        if not isinstance(document["envelopes"], list):
            raise PaperError("config_incomplete", "envelopes must be a list")
        self.evidence_class = document["evidence_class"]
        for raw in document["envelopes"]:
            try:
                env = validate_envelope(raw)
            except PaperError as exc:
                ident = raw.get("decision_id") if isinstance(raw, dict) else None
                self.invalid.append({"decision_id": ident, "code": exc.code, "message": str(exc)})
                continue
            if env["decision_id"] in self.envelopes:
                raise PaperError("duplicate_intent", f"duplicate decision id {env['decision_id']}")
            self.envelopes[env["decision_id"]] = env

    def recorded(self, decision_id):
        self.lookups += 1
        return self.envelopes.get(decision_id)


PROVENANCE_FIELDS = (
    "as_of_sequence", "decision_id", "decision_time_berlin", "decision_time_ms",
    "evidence_sequences", "expected_state_hash", "expires_at_ms", "explanation",
    "intent", "judge_id", "kind", "max_book_age_ms", "parent_id", "proposal_id",
    "reason_codes", "research", "risk", "run_id", "unavailable", "verdict",
)


def provenance_hash(raw):
    """Digest of the decision the replay must not silently edit. The hash is not an external notary."""
    if not isinstance(raw, dict):
        raise PaperError("hash_mismatch", "decision provenance is not an object")
    body = {key: raw.get(key) for key in PROVENANCE_FIELDS}
    return digest(canonical(body))


def validate_envelope(raw):
    keys = {
        "schema_version", "decision_id", "run_id", "proposal_id", "parent_id", "kind",
        "verdict", "intent", "decision_time_ms", "decision_time_berlin", "as_of_sequence",
        "expires_at_ms", "expected_state_hash", "max_book_age_ms", "judge_id", "hashes",
        "risk", "reason_codes", "explanation", "unavailable", "evidence_sequences", "research",
    }
    need_keys(raw, keys, "decision envelope")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise PaperError("missing_field", "envelope schema version")
    if raw["verdict"] not in ("APPROVE", "REJECT") or raw["kind"] not in ("entry", "exit"):
        raise PaperError("missing_field", "verdict or kind")
    for name in ("decision_id", "run_id", "proposal_id", "judge_id"):
        need_text(raw[name], name)
    if raw["parent_id"] is not None:
        need_text(raw["parent_id"], "parent_id")
    for name in ("decision_time_ms", "as_of_sequence", "expires_at_ms", "max_book_age_ms"):
        if type(raw[name]) is not int or raw[name] < 0:
            raise PaperError("missing_field", name)
    if raw["decision_time_berlin"] != berlin_stamp(raw["decision_time_ms"]):
        raise PaperError("berlin_time_mismatch", "decision_time_berlin does not match Europe/Berlin")
    if not isinstance(raw["expected_state_hash"], str) or len(raw["expected_state_hash"]) != 64:
        raise PaperError("missing_field", "expected_state_hash")
    intent = raw["intent"]
    need_keys(intent, ["client_id", "coin", "side", "time_in_force", "order_type", "px", "sz", "reduce_only", "expire_ms"], "intent")
    if intent["order_type"] != "limit":
        raise PaperError("unsupported", "only a limit intent can be recorded")
    if intent["side"] not in ("buy", "sell") or intent["time_in_force"] not in ("gtc", "ioc", "alo"):
        raise PaperError("unsupported", "side or time in force is not supported")
    if type(intent["reduce_only"]) is not bool:
        raise PaperError("missing_field", "reduce_only")
    if raw["kind"] == "entry" and intent["reduce_only"]:
        raise PaperError("missing_field", "an entry cannot be reduce-only")
    if raw["kind"] == "exit" and not intent["reduce_only"]:
        raise PaperError("missing_field", "an exit must be reduce-only")
    if intent["expire_ms"] is not None and (type(intent["expire_ms"]) is not int or intent["expire_ms"] < 0):
        raise PaperError("missing_field", "expire_ms")
    money(intent["px"], "px", positive=True)
    money(intent["sz"], "sz", positive=True)
    need_text(intent["client_id"], "client_id")
    if not isinstance(intent["coin"], str) or not intent["coin"]:
        raise PaperError("missing_field", "coin")
    hashes = raw["hashes"]
    need_keys(hashes, ["strategy", "config", "code", "data", "ledger", "risk", "research", "provenance"], "hashes")
    for name, value in hashes.items():
        if not isinstance(value, str) or len(value) != 64:
            raise PaperError("missing_field", f"hashes.{name}")
    if hashes["ledger"] != raw["expected_state_hash"]:
        raise PaperError("hash_mismatch", "ledger hash and expected state hash differ")
    risk = raw["risk"]
    need_keys(risk, ["verdict", "reason", "gates", "freshness", "reservation"], "risk")
    if risk["verdict"] not in ("PASS", "REJECT") or risk["freshness"] not in ("pass", "fail"):
        raise PaperError("missing_field", "risk verdict")
    if not isinstance(risk["reason"], str) or not risk["reason"].strip():
        raise PaperError("missing_field", "risk reason")
    if not isinstance(risk["gates"], list) or not risk["gates"] or not all(isinstance(item, str) and item for item in risk["gates"]):
        raise PaperError("missing_field", "risk gates")
    if risk["reservation"] is not None and not isinstance(risk["reservation"], str):
        raise PaperError("missing_field", "risk reservation")
    if digest(canonical(risk)) != hashes["risk"]:
        raise PaperError("hash_mismatch", "risk hash does not match the risk object")
    if not isinstance(raw["reason_codes"], list) or not raw["reason_codes"] or not all(isinstance(item, str) and item for item in raw["reason_codes"]):
        raise PaperError("missing_field", "reason_codes")
    if not isinstance(raw["explanation"], str) or not raw["explanation"].strip():
        raise PaperError("missing_field", "explanation")
    if not isinstance(raw["unavailable"], list) or not all(isinstance(item, str) for item in raw["unavailable"]):
        raise PaperError("missing_field", "unavailable")
    if not isinstance(raw["evidence_sequences"], list) or not all(type(item) is int for item in raw["evidence_sequences"]):
        raise PaperError("missing_field", "evidence_sequences")
    research = raw["research"]
    if not isinstance(research, dict) or research.get("status") not in ("cited", "not_applicable"):
        raise PaperError("missing_field", "research")
    if research["status"] == "not_applicable":
        need_keys(research, ["status", "reason"], "research")
        if not isinstance(research["reason"], str) or not research["reason"].strip():
            raise PaperError("missing_field", "research reason")
    else:
        need_keys(research, ["status", "reason", "as_of_sequence", "source_ref"], "research")
        if type(research["as_of_sequence"]) is not int or research["as_of_sequence"] > raw["as_of_sequence"]:
            raise PaperError("lookahead", "research as-of is after the decision as-of")
        if not isinstance(research["source_ref"], str) or not research["source_ref"].strip():
            raise PaperError("missing_field", "research source_ref")
        if not isinstance(research["reason"], str) or not research["reason"].strip():
            raise PaperError("missing_field", "research reason")
    if digest(canonical(research)) != hashes["research"]:
        raise PaperError("hash_mismatch", "research hash does not match the research object")
    if hashes["provenance"] != provenance_hash(raw):
        raise PaperError("hash_mismatch", "provenance hash does not match verdict, judge id, intent and explanation")
    return raw


def build_envelope(*, config, tape, intent, verdict, risk_verdict, risk_reason, risk_gates, risk_freshness, risk_reservation, expected_state_hash, as_of_sequence, decision_id, proposal_id, parent_id, kind, explanation, reason_codes, research, expires_at_ms, unavailable, evidence_sequences):
    """Assemble a schema-valid envelope from caller-supplied verdict and evidence.

    This does not decide. Omitting the verdict, the risk verdict, or the explanation raises.
    """
    if verdict not in ("APPROVE", "REJECT"):
        raise PaperError("invalid_arguments", "caller must supply APPROVE or REJECT")
    risk = {
        "freshness": risk_freshness,
        "gates": risk_gates,
        "reason": risk_reason,
        "reservation": risk_reservation,
        "verdict": risk_verdict,
    }
    decision_time = intent["decision_time_ms"]
    body = {
        "as_of_sequence": as_of_sequence,
        "decision_id": decision_id,
        "decision_time_berlin": berlin_stamp(decision_time),
        "decision_time_ms": decision_time,
        "evidence_sequences": evidence_sequences,
        "expected_state_hash": expected_state_hash,
        "expires_at_ms": expires_at_ms,
        "explanation": explanation,
        "hashes": {},
        "intent": {
            "client_id": intent["client_id"],
            "coin": intent["coin"],
            "expire_ms": intent["expire_ms"],
            "order_type": intent["order_type"],
            "px": intent["px"],
            "reduce_only": intent["reduce_only"],
            "side": intent["side"],
            "sz": intent["sz"],
            "time_in_force": intent["time_in_force"],
        },
        "judge_id": config["judge"]["declared_id"],
        "kind": kind,
        "max_book_age_ms": config["execution"]["stale_book_ms"],
        "parent_id": parent_id,
        "proposal_id": proposal_id,
        "reason_codes": reason_codes,
        "research": research,
        "risk": risk,
        "run_id": config["run_id"],
        "schema_version": SCHEMA_VERSION,
        "unavailable": unavailable,
        "verdict": verdict,
    }
    body["hashes"] = {
        "code": code_sha256(),
        "config": digest(canonical(config)),
        "data": digest(canonical(tape)),
        "ledger": expected_state_hash,
        "provenance": provenance_hash(body),
        "research": digest(canonical(research)),
        "risk": digest(canonical(risk)),
        "strategy": config["strategy"]["rules_hash"],
    }
    return validate_envelope(body)


def decisions_document(config, envelopes):
    return {"schema_version": SCHEMA_VERSION, "evidence_class": config["evidence_class"], "envelopes": envelopes}


class Journal:
    def __init__(self, fail_before=None):
        self.fail_before = set(fail_before or [])
        self.lines = []
        self.prev = "0" * 64
        self.halted = False

    def write(self, record):
        key = record.get("fail_key")
        if key in self.fail_before:
            self.halted = True
            raise JournalFailure(key)
        body = dict(record)
        body["prev_sha256"] = self.prev
        claimed = digest(canonical(body))
        body["sha256"] = claimed
        line = canonical(body)
        self.prev = claimed
        self.lines.append(line)
        return {"sequence": len(self.lines), "sha256": claimed}

    def text(self):
        return b"".join(line + b"\n" for line in self.lines)


def verify_journal(text):
    prev = "0" * 64
    count = 0
    for line in text.splitlines():
        if not line:
            continue
        body = json.loads(line)
        claimed = body.pop("sha256")
        if digest(canonical(body)) != claimed or body.get("prev_sha256") != prev:
            raise PaperError("journal_failure", "journal hash chain does not match")
        prev = claimed
        count += 1
    return {"lines": count, "head": prev}


class Book:
    def __init__(self):
        self.time_ms = None
        self.bids = []
        self.asks = []
        self.trusted = False
        self.reason = "no_book"
        self.source = None


class Order:
    def __init__(self, intent, kind, envelope):
        self.intent = intent
        self.kind = kind
        self.envelope = envelope
        self.client_id = intent["client_id"]
        self.side = intent["side"]
        self.tif = intent["time_in_force"]
        self.px = money(intent["px"], "px", positive=True)
        self.sz = money(intent["sz"], "sz", positive=True)
        self.remaining = self.sz
        self.filled = Decimal(0)
        self.reduce_only = intent["reduce_only"]
        self.expire_ms = intent["expire_ms"]
        self.decision_time = intent["decision_time_ms"]
        self.decision_id = intent["decision_id"]
        self.status = "submitted"
        self.reason = None
        self.queue_ahead = None
        self.fills = []
        self.mid = None
        self.slippage = None
        self.slippage_known = True
        self.action_taken = False
        self.recorded_verdict = None
        self.paused_logged = False
        self.terminal = False
        self.candle_touch = False
        self.eligible = None


def price_aligned(px, sz_decimals):
    if px == px.to_integral_value():
        return True
    max_dec = max(6 - sz_decimals, 0)
    exponent = min(0, max(px.adjusted() - 4, -max_dec))
    quantum = Decimal(1).scaleb(exponent)
    if px != px.quantize(quantum):
        return False
    return len(px.normalize().as_tuple().digits) <= 5


def parse_tape(document, coin):
    if not isinstance(document, dict) or set(document) != {"schema_version", "evidence_class", "label", "events"}:
        raise PaperError("invalid_arguments", "tape must contain schema, evidence class, label and events")
    if document["schema_version"] != SCHEMA_VERSION:
        raise PaperError("unsupported", "tape schema is not supported")
    if document["evidence_class"] not in ("synthetic_engineering_fixture", "captured_public_replay"):
        raise PaperError("invalid_arguments", "tape evidence class is unknown")
    if not isinstance(document["label"], str) or not document["label"].strip():
        raise PaperError("invalid_arguments", "tape label is required")
    if not isinstance(document["events"], list):
        raise PaperError("invalid_arguments", "events must be a list")
    events = []
    seen = set()
    for raw in document["events"]:
        if not isinstance(raw, dict) or raw.get("type") is None:
            raise PaperError("invalid_arguments", "event type is missing")
        event = _parse_event(raw, coin)
        if event["sequence"] in seen:
            raise PaperError("invalid_arguments", "event sequence values must be unique")
        seen.add(event["sequence"])
        events.append(event)
    events.sort(key=lambda item: item["sequence"])
    prior = None
    for event in events:
        if event["type"] in ("intent", "cancel"):
            if prior is not None and prior > event["decision_time_ms"]:
                raise PaperError("lookahead", "an intent is ordered after a later event time")
            if event["decision_time_ms"] != event["exchange_time_ms"]:
                raise PaperError("lookahead", "decision time must equal the intent event time")
        if prior is None or event["exchange_time_ms"] > prior:
            prior = event["exchange_time_ms"]
    return events


def _parse_event(raw, coin):
    base = {"sequence", "type", "exchange_time_ms", "local_receipt_ms"}
    if not base <= set(raw):
        raise PaperError("invalid_arguments", "event is missing sequence or time")
    if type(raw["sequence"]) is not int or type(raw["exchange_time_ms"]) is not int or type(raw["local_receipt_ms"]) is not int:
        raise PaperError("invalid_arguments", "sequence and times must be integers")
    if raw["sequence"] < 0 or raw["exchange_time_ms"] < 0 or raw["local_receipt_ms"] < 0:
        raise PaperError("invalid_arguments", "sequence and times must be nonnegative")
    kind = raw["type"]
    if kind == "book":
        need_keys(raw, base | {"coin", "bids", "asks", "depth"}, "book event")
        if raw["depth"] not in ("l2", "bbo"):
            raise PaperError("unsupported", "book depth must be l2 or bbo")
        _levels(raw["bids"], "bid")
        _levels(raw["asks"], "ask")
    elif kind == "trade":
        need_keys(raw, base | {"coin", "px", "sz", "aggressor"}, "trade event")
        if raw["aggressor"] not in ("buy", "sell"):
            raise PaperError("unsupported", "trade aggressor must be buy or sell")
        money(raw["px"], "trade px", positive=True)
        money(raw["sz"], "trade sz", positive=True)
    elif kind == "candle":
        need_keys(raw, base | {"coin", "t", "T", "o", "h", "l", "c", "v", "n", "closed"}, "candle event")
        if type(raw["closed"]) is not bool or type(raw["t"]) is not int or type(raw["T"]) is not int or type(raw["n"]) is not int:
            raise PaperError("invalid_arguments", "candle flags and times are invalid")
        for name in ("o", "h", "l", "c"):
            money(raw[name], name, positive=True)
        money(raw["v"], "v", nonnegative=True)
    elif kind == "asset_ctx":
        need_keys(raw, base | {"coin", "mark_px", "oracle_px"}, "asset context")
        if raw["mark_px"] is not None:
            money(raw["mark_px"], "mark_px", positive=True)
        if raw["oracle_px"] is not None:
            money(raw["oracle_px"], "oracle_px", positive=True)
    elif kind == "funding":
        need_keys(raw, base | {"coin", "funding_rate", "oracle_px", "premium"}, "funding event")
        if raw["funding_rate"] is not None:
            money(raw["funding_rate"], "funding_rate")
        if raw["oracle_px"] is not None:
            money(raw["oracle_px"], "oracle_px", positive=True)
        if raw["premium"] is not None:
            money(raw["premium"], "premium")
    elif kind == "gap":
        need_keys(raw, base | {"start_ms", "end_ms"}, "gap event")
        if type(raw["start_ms"]) is not int or type(raw["end_ms"]) is not int or raw["start_ms"] > raw["end_ms"]:
            raise PaperError("invalid_arguments", "gap bounds are invalid")
    elif kind == "reconnect":
        need_keys(raw, base, "reconnect event")
    elif kind == "ack":
        need_keys(raw, base | {"channel"}, "ack event")
        if raw["channel"] not in WS_PUBLIC:
            raise PaperError("network_denied", "ack channel is outside the public market allowlist")
    elif kind == "intent":
        need_keys(raw, base | {"decision_time_ms", "decision_id", "client_id", "coin", "side", "time_in_force", "order_type", "px", "sz", "reduce_only", "expire_ms"}, "intent")
        if raw["order_type"] != "limit":
            raise PaperError("unsupported", "only limit orders are supported")
        if type(raw["decision_time_ms"]) is not int:
            raise PaperError("invalid_arguments", "decision_time_ms must be an integer")
    elif kind == "cancel":
        need_keys(raw, base | {"decision_time_ms", "client_id"}, "cancel")
        if type(raw["decision_time_ms"]) is not int:
            raise PaperError("invalid_arguments", "cancel decision time must be an integer")
    else:
        raise PaperError("unsupported", f"event type {kind} is not supported")
    if raw.get("coin") not in (None, coin) and kind not in ("gap", "reconnect", "ack", "cancel"):
        if raw.get("coin") != coin:
            raise PaperError("unsupported", "event coin does not match the configured market")
    return raw


def _levels(levels, name):
    if not isinstance(levels, list):
        raise PaperError("invalid_arguments", f"{name} levels must be a list")
    seen = set()
    for level in levels:
        if not isinstance(level, dict) or set(level) != {"px", "sz"}:
            raise PaperError("invalid_arguments", f"{name} level must contain px and sz")
        px = money(level["px"], "px", positive=True)
        money(level["sz"], "sz", positive=True)
        if px in seen:
            raise PaperError("invalid_arguments", "duplicate book price")
        seen.add(px)


def _level_pairs(levels, desc):
    pairs = [(money(level["px"], "px", positive=True), money(level["sz"], "sz", positive=True)) for level in levels]
    return sorted(pairs, key=lambda item: item[0], reverse=desc)


def segment_for(parsed, time_ms):
    if parsed["train"][0] <= time_ms < parsed["train"][1]:
        return "train"
    if parsed["validation"][0] <= time_ms < parsed["validation"][1]:
        return "validation"
    if parsed["holdout"][0] <= time_ms < parsed["holdout"][1]:
        return "holdout"
    embargo = parsed["embargo_ms"]
    if embargo and (parsed["train"][1] <= time_ms < parsed["train"][1] + embargo or parsed["validation"][1] <= time_ms < parsed["validation"][1] + embargo):
        return "embargo"
    return "outside"


def in_holdout(parsed, time_ms):
    return parsed["holdout"][0] <= time_ms < parsed["holdout"][1]


class Simulator:
    def __init__(self, parsed, events, source, journal, config_hash, tape_hash):
        self.cfg = parsed
        self.events = events
        self.by_seq = {event["sequence"]: event for event in events}
        self.source = source
        self.journal = journal
        self.config_hash = config_hash
        self.tape_hash = tape_hash
        self.code_hash = code_sha256()
        self.book = Book()
        self.cash = parsed["starting_cash"]
        self.position = Decimal(0)
        self.average = None
        self.mark = None
        self.fees = Decimal(0)
        self.funding_posted = Decimal(0)
        self.buy_notional = Decimal(0)
        self.sell_notional = Decimal(0)
        self.realized = Decimal(0)
        self.reservations = []
        self.used = []
        self.orders = {}
        self.order_list = []
        self.pending = []
        self.cancels = []
        self.seen_intent_ids = set()
        self.records = []
        self.position_samples = [(0, Decimal(0))]
        self.paid_buckets = set()
        self.funding_gaps = []
        self.funding_status = "not_due"
        self.duplicates = 0
        self.out_of_order = 0
        self.gaps = 0
        self._gap_before_next = False
        self.reconnects = 0
        self.open_candles = 0
        self.closed_candles = 0
        self.candle_touches = 0
        self.seen_identity = set()
        self.last_time = None
        self.peak = parsed["starting_cash"]
        self.max_dd = Decimal(0)
        self.drawdown_complete = True
        self.equity_points = []
        self.unresolved_exposure = False
        self.insolvent = False
        self.holdout_leak = False
        self.holdout_touch = False
        self.segments = {name: {"fees": Decimal(0), "funding": Decimal(0), "fill_notional": Decimal(0), "fills": 0} for name in ("train", "validation", "holdout", "embargo", "outside")}
        self.protection_ran = False

    def run(self):
        for event in self.events:
            if self._duplicate_or_stale_order(event):
                continue
            self._apply_market(event)
            if event["type"] == "intent":
                self._register_intent(event)
            elif event["type"] == "cancel":
                self.cancels.append(event)
            if event["type"] == "asset_ctx":
                self._protection(event["exchange_time_ms"])
            self._activate(event["exchange_time_ms"])
            self._apply_cancels(event["exchange_time_ms"])
            self._sample(event["exchange_time_ms"])
            self._note_equity(event["exchange_time_ms"])
        self._funding_gaps()
        self._finalize()
        return self._result()

    def _duplicate_or_stale_order(self, event):
        if event["type"] in ("intent", "cancel"):
            return False
        identity = digest(canonical({key: value for key, value in event.items() if key not in ("sequence", "local_receipt_ms")}))
        if identity in self.seen_identity:
            self.duplicates += 1
            self._log({"kind": "duplicate", "fail_key": None, "sequence": event["sequence"], "type": event["type"]})
            return True
        self.seen_identity.add(identity)
        if self.last_time is not None and event["exchange_time_ms"] < self.last_time:
            self.out_of_order += 1
            self._log({"kind": "out_of_order", "fail_key": None, "sequence": event["sequence"], "exchange_time_ms": event["exchange_time_ms"]})
            return True
        self.last_time = event["exchange_time_ms"]
        return False

    def _apply_market(self, event):
        kind = event["type"]
        if kind == "book":
            bids = _level_pairs(event["bids"], True)
            asks = _level_pairs(event["asks"], False)
            self.book.time_ms = event["exchange_time_ms"]
            self.book.bids = bids
            self.book.asks = asks
            self.book.source = event["depth"]
            if bids and asks and bids[0][0] >= asks[0][0]:
                self.book.trusted = False
                self.book.reason = "crossed_book"
            else:
                self.book.trusted = True
                self.book.reason = None
        elif kind == "trade":
            self._on_trade(money(event["px"], "px", positive=True), money(event["sz"], "sz", positive=True), event["aggressor"])
        elif kind == "asset_ctx":
            if event["mark_px"] is not None:
                self.mark = money(event["mark_px"], "mark_px", positive=True)
        elif kind == "funding":
            self._on_funding(event)
        elif kind == "gap":
            self.gaps += 1
            self._gap_before_next = True
            self.book.trusted = False
            self.book.reason = "gap"
        elif kind == "reconnect":
            self.reconnects += 1
            self.book.trusted = False
            self.book.reason = "reconnect"
        elif kind == "candle":
            if event["closed"]:
                self.closed_candles += 1
                self._candle_touch(event)
            else:
                self.open_candles += 1

    def _candle_touch(self, event):
        low, high = money(event["l"], "l", positive=True), money(event["h"], "h", positive=True)
        for order in self.order_list:
            if order.status in ("resting", "partial", "waiting", "submitted") and low <= order.px <= high:
                order.candle_touch = True
                self.candle_touches += 1

    def _on_funding(self, event):
        bucket = event["exchange_time_ms"] // HOUR_MS * HOUR_MS
        if bucket in self.paid_buckets:
            raise PaperError("unsupported", "two funding events fall in one UTC hour")
        rate, oracle = event["funding_rate"], event["oracle_px"]
        if rate is None or oracle is None:
            self.funding_status = "unavailable"
            self.funding_gaps.append({"time_ms": event["exchange_time_ms"], "reason": "missing_oracle_or_rate"})
            self._log({"kind": "funding_unavailable", "fail_key": None, "time_ms": event["exchange_time_ms"], "posted": None})
            return
        payment = -self.position * money(oracle, "oracle", positive=True) * money(rate, "rate")
        self._log({"kind": "funding", "fail_key": None, "time_ms": event["exchange_time_ms"], "posted": dec_str(payment), "position": dec_str(self.position)})
        self.cash += payment
        self.funding_posted += payment
        self.segments[segment_for(self.cfg, event["exchange_time_ms"])]["funding"] += payment
        self.paid_buckets.add(bucket)
        if self.funding_status != "unavailable":
            self.funding_status = "exact"

    def _funding_gaps(self):
        if not self.events:
            return
        end = self.events[-1]["exchange_time_ms"]
        start = self.events[0]["exchange_time_ms"]
        boundary = (start // HOUR_MS + 1) * HOUR_MS
        while boundary <= end:
            if boundary not in self.paid_buckets and self._position_at(boundary) != 0:
                self.funding_status = "unavailable"
                self.funding_gaps.append({"time_ms": boundary, "reason": "missing_funding_observation"})
            boundary += HOUR_MS

    def _position_at(self, boundary):
        position = Decimal(0)
        for time_ms, sample in self.position_samples:
            if time_ms <= boundary:
                position = sample
        return position

    def _sample(self, time_ms):
        self.position_samples.append((time_ms, self.position))

    def _register_intent(self, event):
        if event["client_id"] in self.seen_intent_ids or event["decision_id"] in {item.decision_id for item in self.order_list}:
            self._terminal_shell(event, "duplicate_intent")
            return
        self.seen_intent_ids.add(event["client_id"])
        invalid = next((item for item in self.source.invalid if item["decision_id"] == event["decision_id"]), None)
        if invalid:
            self._terminal_shell(event, "missing_field", invalid["message"])
            return
        self.pending.append(event)

    def _terminal_shell(self, event, code, message=None):
        self._record(event, None, code, False, message)
        self._log({"kind": "refused", "fail_key": None, "decision_id": event.get("decision_id"), "code": code})

    def _protection(self, time_ms):
        policy = self.cfg["protection"]
        if not policy["selected"] or policy["mode"] != "stop_mark" or self.position == 0 or self.mark is None:
            return
        triggered = (policy["side"] == "long" and self.position > 0 and self.mark <= policy["trigger"]) or (
            policy["side"] == "short" and self.position < 0 and self.mark >= policy["trigger"])
        if not triggered:
            return
        code = self._book_code(time_ms, policy["max_book_age_ms"])
        verdict = "REJECT" if code else "APPROVE"
        try:
            self.journal.write({
                "kind": "policy_evaluation", "fail_key": "policy_evaluation", "time_ms": time_ms,
                "policy_id": policy["policy_id"], "verdict": verdict, "judge_called": False,
                "reason_codes": [code or "policy_trigger"], "source": "frozen_policy",
            })
        except JournalFailure:
            self._record_protection(time_ms, "REJECT", "journal_failure", False)
            return
        if code:
            self.unresolved_exposure = True
            self._record_protection(time_ms, "REJECT", code if code != "stale_book" else "protection_integrity", False)
            return
        if self.journal.halted:
            self._record_protection(time_ms, "REJECT", "journal_failure", False)
            return
        side = "sell" if self.position > 0 else "buy"
        levels = self.book.bids if side == "sell" else self.book.asks
        if not levels:
            self.unresolved_exposure = True
            self._record_protection(time_ms, "REJECT", "protection_integrity", False)
            return
        limit = levels[-1][0]
        try:
            self.journal.write({
                "kind": "pre_action", "fail_key": "protection_pre_action", "time_ms": time_ms,
                "source": "frozen_policy", "side": side, "size": dec_str(abs(self.position)), "judge_called": False,
            })
        except JournalFailure:
            self.unresolved_exposure = True
            self._record_protection(time_ms, "REJECT", "journal_failure", False)
            return
        slices, _remain, plan_code = self._plan(side, limit, abs(self.position), True, "ioc", "reject_if_incomplete")
        if plan_code and not slices:
            self.unresolved_exposure = True
            self._log({"kind": "protection_unfilled", "fail_key": None, "code": plan_code, "time_ms": time_ms})
            self._record_protection(time_ms, "REJECT", "protection_integrity", False)
            return
        for px, qty in slices:
            self._mutate("protection", side, px, qty, "taker", time_ms)
        if self.position != 0:
            self.unresolved_exposure = True
        self.protection_ran = True
        self._record_protection(time_ms, "APPROVE", "policy_trigger", True)

    def _record_protection(self, time_ms, verdict, code, acted):
        self.records.append({
            "action_taken": acted,
            "decision_id": None,
            "explanation": REASONS_DE.get(code, code),
            "judge_authenticated": False,
            "judge_called": False,
            "judge_id": None,
            "kind": "protection",
            "reason_code": code,
            "reason_de": REASONS_DE.get(code, code),
            "recorded_verdict": verdict,
            "runtime_bot_integration": False,
            "source": "frozen_policy",
            "time_ms": time_ms,
        })

    def _activate(self, time_ms):
        ready = [event for event in self.pending if event["decision_time_ms"] + self.cfg["latency_ms"] <= time_ms]
        self.pending = [event for event in self.pending if event not in ready]
        for event in ready:
            self._act(event, time_ms)
        for order in list(self.order_list):
            if order.status == "waiting" and not order.terminal:
                code = self._gate(order, order.envelope, time_ms)
                if code:
                    self._refuse(order, code, time_ms, consume=True)
                else:
                    self._execute(order, time_ms)

    def _act(self, event, time_ms):
        if self.journal.halted:
            self._terminal_shell(event, "journal_failure")
            return
        env = self.source.recorded(event["decision_id"])
        if env is None:
            self._terminal_shell(event, "missing_decision")
            return
        if in_holdout(self.cfg, event["decision_time_ms"]):
            self.holdout_touch = True
        order = Order(event, env["kind"], env)
        order.eligible = event["decision_time_ms"] + self.cfg["latency_ms"]
        order.recorded_verdict = env["verdict"]
        self.orders[order.client_id] = order
        self.order_list.append(order)
        code = self._gate(order, env, time_ms)
        if code:
            self._refuse(order, code, time_ms, consume=True)
            return
        self._execute(order, time_ms)

    def _execute(self, order, time_ms):
        if order.terminal:
            return
        env = order.envelope
        book_code = self._book_code(time_ms, self.cfg["stale_ms"])
        if book_code and order.tif == "gtc" and book_code in ("stale_book", "book_untrusted", "no_book"):
            self._pause(order, book_code, time_ms)
            order.status = "waiting"
            return
        if book_code:
            self._refuse(order, book_code, time_ms, consume=True)
            return
        if not self._aligned(order):
            self._refuse(order, "precision", time_ms, consume=True)
            return
        limit_notional = order.px * order.sz
        dust = self.cfg["allow_dust"] and order.reduce_only and order.sz == abs(self.position)
        if limit_notional < self.cfg["min_notional"] and not dust:
            self._refuse(order, "min_notional", time_ms, consume=True)
            return
        if self._crosses(order) and order.tif == "alo":
            self._refuse(order, "post_only_would_take", time_ms, consume=True)
            return
        policy = self.cfg["incomplete_fill"]
        if order.tif == "ioc":
            policy = "reject_if_incomplete" if policy == "reject_if_incomplete" else "partial_then_cancel"
        slices, remain, plan_code = ([], order.sz, None)
        if self._crosses(order) or order.tif == "ioc":
            slices, remain, plan_code = self._plan(order.side, order.px, order.sz, order.reduce_only, order.tif, policy)
            if plan_code and not slices:
                self._refuse(order, plan_code, time_ms, consume=True)
                return
        if not slices and order.tif == "ioc":
            self._refuse(order, plan_code or "insufficient_depth", time_ms, consume=True)
            return
        try:
            self.journal.write({
                "kind": "pre_action", "fail_key": f"pre_action:{order.decision_id}", "time_ms": time_ms,
                "decision_id": order.decision_id, "recorded_verdict": env["verdict"], "client_id": order.client_id,
            })
        except JournalFailure:
            self._refuse(order, "journal_failure", time_ms, consume=False)
            return
        self.reservations.append(order.decision_id)
        self._consume(order)
        self.reservations = [item for item in self.reservations if item != order.decision_id]
        order.mid = self._mid(time_ms)
        if order.mid is None:
            order.slippage_known = False
        for px, qty in slices:
            self._mutate(order.client_id, order.side, px, qty, "taker", time_ms, order)
        order.filled = order.sz - remain if slices else order.filled
        order.remaining = order.sz - order.filled
        if slices and order.remaining == 0:
            order.status = "filled"
            order.terminal = True
            order.action_taken = True
        elif slices and order.tif != "gtc":
            order.status = "cancelled" if order.remaining else "filled"
            order.reason = "insufficient_depth" if order.remaining else None
            order.terminal = True
            order.action_taken = True
            self._log({"kind": "residual_cancelled", "fail_key": None, "client_id": order.client_id, "remaining": dec_str(order.remaining)})
        elif slices and policy == "partial_then_cancel":
            order.status = "cancelled"
            order.terminal = True
            order.action_taken = True
        elif not slices and order.tif == "gtc":
            self._rest(order)
        elif slices and order.tif == "gtc":
            order.action_taken = True
            if order.remaining == 0:
                order.status = "filled"
                order.terminal = True
            else:
                self._rest(order)
                order.status = "partial"
        else:
            self._rest(order)
        if order.action_taken or order.status in ("resting", "partial", "filled", "cancelled"):
            self._record(order.intent, env, order.reason, True)

    def _gate(self, order, env, time_ms):
        if self.cfg["strategy"].get("status") != "supplied":
            return "pending_strategy"
        if not self.cfg.get("quote_enabled", True):
            return "fx_unavailable"
        if not self.source.envelopes:
            return "pending_native_decision"
        if env["run_id"] != self.cfg["run_id"] or env["judge_id"] != self.cfg["judge_id"]:
            return "judge_id_mismatch" if env["judge_id"] != self.cfg["judge_id"] else "hash_mismatch"
        hashes = env["hashes"]
        if hashes.get("provenance") != provenance_hash(env) or hashes["config"] != self.config_hash or hashes["data"] != self.tape_hash or hashes["code"] != self.code_hash or hashes["strategy"] != self.cfg["strategy"]["rules_hash"]:
            return "hash_mismatch"
        if env["max_book_age_ms"] != self.cfg["stale_ms"]:
            return "freshness_limit_mismatch"
        if any(seq > env["as_of_sequence"] or seq not in self.by_seq or self.by_seq[seq]["exchange_time_ms"] > env["decision_time_ms"] for seq in env["evidence_sequences"]):
            return "lookahead"
        if any(in_holdout(self.cfg, self.by_seq[seq]["exchange_time_ms"]) and env["decision_time_ms"] < self.cfg["holdout"][0] for seq in env["evidence_sequences"]):
            self.holdout_leak = True
            self.holdout_touch = True
            return "holdout_leak"
        intent = env["intent"]
        for field in ("client_id", "coin", "side", "time_in_force", "order_type", "px", "sz", "reduce_only", "expire_ms"):
            if order.intent[field] != intent[field]:
                return "intent_mismatch"
        if self._state_hash() != env["expected_state_hash"]:
            return "state_changed"
        if time_ms > env["expires_at_ms"]:
            return "expired"
        if env["decision_id"] in self.used:
            return "already_used"
        if env["verdict"] == "REJECT":
            return "recorded_reject"
        if env["risk"]["verdict"] != "PASS" or env["risk"]["freshness"] != "pass":
            return "risk_veto"
        if env["unavailable"]:
            return "unavailable_inputs"
        if not order.reduce_only and not self.cfg["protection"]["selected"]:
            return "protection_policy_absent"
        if order.reduce_only:
            if self.position == 0 or (order.side == "buy" and self.position >= 0) or (order.side == "sell" and self.position <= 0) or order.sz > abs(self.position):
                return "reduce_only"
        equity = self._equity()
        if not order.reduce_only and equity is None:
            return "mark_unavailable"
        if not order.reduce_only and equity is not None and self.cfg["starting_cash"] - equity >= self.cfg["max_loss"]:
            return "max_loss"
        return None

    def _aligned(self, order):
        quantum = self.cfg["quantum"]
        return order.sz == order.sz.quantize(quantum) and price_aligned(order.px, self.cfg["sz_decimals"])

    def _book_code(self, time_ms, stale_ms):
        book = self.book
        if book.time_ms is None:
            return "no_book"
        if not book.trusted:
            return "crossed_book" if book.reason == "crossed_book" else "book_untrusted"
        if time_ms - book.time_ms > stale_ms:
            return "stale_book"
        return None

    def _crosses(self, order):
        if order.side == "buy" and self.book.asks and order.px >= self.book.asks[0][0]:
            return True
        if order.side == "sell" and self.book.bids and order.px <= self.book.bids[0][0]:
            return True
        return False

    def _plan(self, side, limit, size, reduce_only, tif, policy):
        levels = self.book.asks if side == "buy" else self.book.bids
        quantum = self.cfg["quantum"]
        remain = size
        cash_left = self.cash
        position = self.position
        slices = []
        stop_code = None
        for px, level_sz in levels:
            if side == "buy" and px > limit:
                break
            if side == "sell" and px < limit:
                break
            avail = (level_sz * self.cfg["participation"]).quantize(quantum, rounding=ROUND_DOWN)
            take = min(remain, avail)
            take = self._fit(side, px, take, cash_left, position, reduce_only)
            if take <= 0:
                stop_code = "insufficient_cash" if avail > 0 else "insufficient_depth"
                if avail > 0 and self._fit(side, px, quantum, cash_left, position, reduce_only) <= 0:
                    stop_code = "insufficient_cash" if cash_left < px * quantum else "capacity"
                break
            slices.append((px, take))
            remain -= take
            fee = px * take * (self.cfg["taker_rate"])
            if side == "buy":
                cash_left -= px * take + fee
                position += take
            else:
                cash_left += px * take - fee
                position -= take
            if remain == 0:
                break
        else:
            if remain > 0 and not stop_code:
                stop_code = "insufficient_depth"
        if remain > 0 and policy == "reject_if_incomplete":
            return [], size, stop_code or "insufficient_depth"
        if remain > 0 and not slices:
            return [], size, stop_code or "insufficient_depth"
        return slices, remain, None

    def _fit(self, side, px, qty, cash_left, position, reduce_only):
        quantum = self.cfg["quantum"]
        if qty <= 0:
            return Decimal(0)
        rate = self.cfg["taker_rate"]
        if reduce_only:
            room = position if side == "sell" else -position
            qty = min(qty, room)
        else:
            increasing = (side == "buy" and position >= 0) or (side == "sell" and position <= 0) or abs(position) < qty
            if increasing:
                next_abs = abs(position + (qty if side == "buy" else -qty))
                if next_abs > self.cfg["max_position"] or next_abs * px > self.cfg["max_notional"]:
                    # Largest lot that respects both caps.
                    cap = self.cfg["max_position"] - abs(position) if (side == "buy") == (position >= 0) or position == 0 else qty
                    if position != 0 and (side == "buy") != (position > 0):
                        cap = qty
                    notional_cap = (self.cfg["max_notional"] / px) - abs(position) if abs(position) * px < self.cfg["max_notional"] else Decimal(0)
                    if position == 0 or (side == "buy" and position > 0) or (side == "sell" and position < 0):
                        qty = min(qty, max(Decimal(0), self.cfg["max_position"] - abs(position)), max(Decimal(0), self.cfg["max_notional"] / px - abs(position)))
        if side == "buy":
            unit = px * (1 + rate)
            if unit > 0:
                qty = min(qty, cash_left / unit)
        else:
            fee = px * qty * rate
            if fee > cash_left and px * rate > 0:
                qty = min(qty, cash_left / (px * rate))
        qty = qty.quantize(quantum, rounding=ROUND_DOWN)
        return qty if qty > 0 else Decimal(0)

    def _rest(self, order):
        order.status = "partial" if order.filled else "resting"
        order.terminal = False
        if self.cfg["maker_model"] == "maker_unsupported":
            order.queue_ahead = None
            order.reason = "maker_unsupported"
            return
        levels = self.book.bids if order.side == "buy" else self.book.asks
        visible = sum((sz for px, sz in levels if px == order.px), Decimal(0))
        order.queue_ahead = visible

    def _on_trade(self, px, sz, aggressor):
        if self.cfg["maker_model"] != "conservative_back_of_visible_queue":
            return
        for order in list(self.order_list):
            if order.status not in ("resting", "partial") or order.queue_ahead is None:
                continue
            if order.side == "buy" and aggressor == "sell":
                self._maker_hit(order, px, sz, through=px < order.px, same=px == order.px)
            elif order.side == "sell" and aggressor == "buy":
                self._maker_hit(order, px, sz, through=px > order.px, same=px == order.px)

    def _maker_hit(self, order, px, sz, through, same):
        if not through and not same:
            return
        qty = order.remaining if through else Decimal(0)
        if same:
            if order.queue_ahead >= sz:
                order.queue_ahead -= sz
                return
            qty = min(order.remaining, sz - order.queue_ahead)
            order.queue_ahead = Decimal(0)
        if qty <= 0 or self.journal.halted:
            return
        try:
            self.journal.write({"kind": "pre_action", "fail_key": f"maker:{order.client_id}:{len(order.fills)}", "time_ms": self.last_time, "client_id": order.client_id, "modelled": True})
        except JournalFailure:
            return
        self._mutate(order.client_id, order.side, order.px, qty, "maker_modelled", self.last_time, order)
        order.filled += qty
        order.remaining -= qty
        order.action_taken = True
        order.status = "filled" if order.remaining == 0 else "partial"
        if order.remaining == 0:
            order.terminal = True

    def _mutate(self, client_id, side, px, qty, liquidity, time_ms, order=None):
        rate = self.cfg["taker_rate"] if liquidity == "taker" else self.cfg["maker_rate"]
        notional = px * qty
        fee = notional * rate
        if side == "buy":
            self.cash -= notional + fee
            self.buy_notional += notional
            signed_increase = True
        else:
            self.cash += notional - fee
            self.sell_notional += notional
            signed_increase = False
        self.fees += fee
        self._apply_position(side, qty, px)
        bucket = self.segments[segment_for(self.cfg, time_ms)]
        bucket["fees"] += fee
        bucket["fill_notional"] += notional
        bucket["fills"] += 1
        if self.cash < 0 or (self._equity() is not None and self._equity() < 0):
            self.insolvent = True
        fill = {"client_id": client_id, "liquidity": liquidity, "px": dec_str(px), "qty": dec_str(qty), "fee": dec_str(fee), "time_ms": time_ms, "observed": False}
        if order is not None:
            if order.mid is None:
                order.slippage_known = False
            else:
                slip = (px - order.mid) * qty if side == "buy" else (order.mid - px) * qty
                order.slippage = (order.slippage or Decimal(0)) + slip
            order.fills.append(fill)
        self._log({"kind": "fill", "fail_key": None, **fill, "side": side})
        return fill

    def _apply_position(self, side, qty, px):
        signed = qty if side == "buy" else -qty
        if self.position == 0:
            self.position = signed
            self.average = px
            return
        if (self.position > 0 and signed > 0) or (self.position < 0 and signed < 0):
            self.average = (self.average * abs(self.position) + px * qty) / (abs(self.position) + qty)
            self.position += signed
            return
        close_qty = min(abs(self.position), qty)
        if self.position > 0:
            self.realized += (px - self.average) * close_qty
        else:
            self.realized += (self.average - px) * close_qty
        self.position += signed
        if self.position == 0:
            self.average = None
        elif abs(signed) > close_qty:
            self.average = px

    def _pause(self, order, code, time_ms):
        if order.paused_logged:
            return
        order.paused_logged = True
        order.reason = code
        self._log({"kind": "paused", "fail_key": None, "client_id": order.client_id, "code": code, "time_ms": time_ms})

    def _refuse(self, order, code, time_ms, consume):
        order.status = "rejected"
        order.reason = code
        order.terminal = True
        order.action_taken = False
        if consume and order.decision_id not in self.used:
            self._consume(order)
        try:
            self._log({"kind": "refused", "fail_key": None, "decision_id": order.decision_id, "code": code, "time_ms": time_ms, "recorded_verdict": order.recorded_verdict})
        except JournalFailure:
            order.reason = "journal_failure"
        self._record(order.intent, order.envelope, order.reason, False)

    def _consume(self, order):
        if order.decision_id not in self.used:
            self.used.append(order.decision_id)

    def _apply_cancels(self, time_ms):
        due = [event for event in self.cancels if event["decision_time_ms"] + self.cfg["cancel_latency_ms"] <= time_ms]
        self.cancels = [event for event in self.cancels if event not in due]
        for event in due:
            order = self.orders.get(event["client_id"])
            if order is None or order.terminal or order.status in ("filled", "rejected", "cancelled", "expired"):
                self._log({"kind": "cancel_missed", "fail_key": None, "client_id": event["client_id"], "time_ms": time_ms})
                continue
            try:
                self.journal.write({"kind": "pre_action", "fail_key": f"cancel:{event['client_id']}", "time_ms": time_ms, "client_id": event["client_id"], "action": "cancel"})
            except JournalFailure:
                continue
            order.status = "cancelled"
            order.reason = "cancelled"
            order.terminal = True
            self._log({"kind": "cancelled", "fail_key": None, "client_id": order.client_id, "remaining": dec_str(order.remaining)})

    def _finalize(self):
        end = self.events[-1]["exchange_time_ms"] if self.events else 0
        for order in self.order_list:
            if order.terminal:
                continue
            if order.expire_ms is not None and end >= order.expire_ms and order.status in ("resting", "partial", "waiting", "submitted"):
                self._log({"kind": "expired", "fail_key": None, "client_id": order.client_id})
                order.status = "expired"
                order.reason = "expired_order"
                order.terminal = True
                continue
            if order.status == "waiting":
                order.status = "rejected"
                order.reason = order.reason or "awaiting_fresh_book"
                order.terminal = True
                self._record(order.intent, order.envelope, order.reason, False)
            elif order.status == "submitted":
                order.status = "rejected"
                order.reason = "not_yet_eligible"
                order.terminal = True
                self._record(order.intent, order.envelope, order.reason, False)
        if self.funding_gaps:
            self.funding_status = "unavailable"

    def _state_hash(self):
        return binding_hash(self.cash, self.position, self.average, self.mark, self.reservations, self.used, self._open_orders())

    def _open_orders(self):
        rows = []
        for order in self.order_list:
            if order.status in ("resting", "partial") and not order.terminal:
                rows.append({"client_id": order.client_id, "px": dec_str(order.px), "remaining": dec_str(order.remaining), "side": order.side, "status": order.status})
        return rows

    def _equity(self):
        if self.position == 0:
            return self.cash
        if self.mark is None:
            return None
        return self.cash + self.position * self.mark

    def _unrealized(self):
        if self.position == 0 or self.mark is None or self.average is None:
            return Decimal(0) if self.position == 0 else None
        return (self.mark - self.average) * self.position

    def _mid(self, time_ms):
        if self._book_code(time_ms, self.cfg["stale_ms"]):
            return None
        if not self.book.bids or not self.book.asks:
            return None
        return (self.book.bids[0][0] + self.book.asks[0][0]) / 2

    def _note_equity(self, time_ms):
        equity = self._equity()
        if equity is None:
            if self.position != 0:
                self.drawdown_complete = False
            return
        if equity > self.peak:
            self.peak = equity
        drawdown = self.peak - equity
        if drawdown > self.max_dd:
            self.max_dd = drawdown
        if len(self.equity_points) < 5000:
            self.equity_points.append({
                "cash": dec_str(self.cash),
                "equity": dec_str(equity),
                "gap_before": self._gap_before_next,
                "realized": dec_str(self.realized),
                "time_ms": time_ms,
                "unrealized": None if self._unrealized() is None else dec_str(self._unrealized()),
            })
            self._gap_before_next = False

    def _log(self, record):
        record.setdefault("mode", "local_paper")
        return self.journal.write(record)

    def _record(self, event, env, code, acted, message=None, acted_flag=None):
        taken = bool(acted if acted_flag is None else acted_flag)
        if code is None and taken:
            code = "gates_passed"
        self.records.append({
            "action_taken": taken,
            "decision_id": None if env is None else env["decision_id"],
            "explanation": None if env is None else env["explanation"],
            "judge_authenticated": False,
            "judge_called": False,
            "judge_id": None if env is None else env["judge_id"],
            "kind": None if env is None else env["kind"],
            "proposal_id": None if env is None else env["proposal_id"],
            "reason_code": code,
            "reason_de": REASONS_DE.get(code, message or code or ""),
            "recorded_verdict": None if env is None else env["verdict"],
            "runtime_bot_integration": False,
            "source": "recorded_envelope",
            "time_ms": event.get("exchange_time_ms"),
        })

    def _chart(self):
        observed = self.cfg["evidence_class"] == "captured_public_replay"
        enabled = self.cfg.get("quote_enabled", True)
        funding_ok = self.funding_status != "unavailable"
        eligible = observed and enabled and funding_ok and bool(self.equity_points)
        return {
            "eligible": eligible,
            "externally_authenticated": False,
            "gap_policy": "lines_are_not_connected_across_gaps",
            "gaps": self.gaps,
            "hash_scope": "local_sha256_not_external_notary",
            "points": self.equity_points if eligible else [],
            "reason_de": "Aufgezeichnete Papierpunkte. Lokaler Hash, keine externe Beglaubigung." if eligible else "Keine echte Papierbeobachtung. Eine Kurve wird nicht gezeichnet.",
            "slippage_included_in_equity": False,
        }

    def _breakdown(self, gross, net, slippage):
        enabled = self.cfg.get("quote_enabled", True)
        return {
            "eur_pnl": None,
            "fees": dec_str(self.fees) if enabled else None,
            "funding": None if self.funding_status == "unavailable" or not enabled else dec_str(self.funding_posted),
            "fx_revaluation_eur": None,
            "gross_quote": None if gross is None else dec_str(gross),
            "market_pnl_quote": None if net is None else dec_str(net),
            "net_quote": None if net is None else dec_str(net),
            "slippage_in_net": False,
            "slippage_quote": None if slippage is None or not enabled else dec_str(slippage),
        }

    def _reconcile(self):
        expected_cash = self.cfg["starting_cash"] - self.buy_notional + self.sell_notional - self.fees + self.funding_posted
        expected_position = sum((money(fill["qty"], "qty") if fill.get("side") == "buy" else -money(fill["qty"], "qty") for _o in [] ), Decimal(0))
        # Position identity from fills stored on orders plus protection fills logged.
        buy_qty = Decimal(0)
        sell_qty = Decimal(0)
        for line in self.journal.lines:
            body = json.loads(line)
            if body.get("kind") == "fill":
                qty = money(body["qty"], "qty")
                if body["side"] == "buy":
                    buy_qty += qty
                else:
                    sell_qty += qty
        expected_position = buy_qty - sell_qty
        cash_ok = self.cash == expected_cash
        position_ok = self.position == expected_position
        equity = self._equity()
        unrealized = self._unrealized()
        equity_ok = True
        if equity is not None and unrealized is not None and self.funding_status != "broken":
            equity_ok = equity == self.cfg["starting_cash"] + self.realized + unrealized - self.fees + self.funding_posted
        return {
            "ok": bool(cash_ok and position_ok and equity_ok),
            "cash_ok": cash_ok,
            "position_ok": position_ok,
            "equity_ok": equity_ok,
            "identity": "cash = starting - buy notional + sell notional - fees + posted funding; equity = cash + position * mark",
        }

    def _result(self):
        reconciliation = self._reconcile()
        equity = self._equity()
        unrealized = self._unrealized()
        funding_claim = None if self.funding_status == "unavailable" else dec_str(self.funding_posted)
        net = None
        gross = None
        if self.cfg.get("quote_enabled", True) and equity is not None and self.funding_status != "unavailable":
            net = equity - self.cfg["starting_cash"]
            gross = net - self.funding_posted + self.fees
        slippage_known = all(order.slippage_known for order in self.order_list if order.fills)
        slippage = sum((order.slippage or Decimal(0) for order in self.order_list), Decimal(0)) if slippage_known and any(order.fills for order in self.order_list) else None
        if any(order.fills and not order.slippage_known for order in self.order_list):
            slippage = None
        accepted = reconciliation["ok"] and self.funding_status != "unavailable" and not self.journal.halted and not self.insolvent
        counts = {"count": len(self.order_list), "filled": 0, "rejected": 0, "cancelled": 0, "expired": 0, "resting": 0, "partial": 0}
        for order in self.order_list:
            if order.status in counts:
                counts[order.status] += 1
        report = {
            "accepted_experiment": accepted,
            "binding_hash_end": self._state_hash(),
            "claims_demonstrated_edge": False,
            "claims_market_evidence": self.cfg["claims_market_evidence"],
            "code_sha256": self.code_hash,
            "config_sha256": self.config_hash,
            "data_quality": {
                "closed_candles_ignored_for_fills": self.closed_candles,
                "duplicates": self.duplicates,
                "gaps": self.gaps,
                "open_candles_ignored": self.open_candles,
                "out_of_order": self.out_of_order,
                "reconnects": self.reconnects,
                "resting_orders_touched_by_candle_range": self.candle_touches,
                "candle_execution": "unsupported",
                "intrabar_path": "not_observed",
            },
            "data_sha256": self.tape_hash,
            "decisions": self.records,
            "decision_source": "recorded_envelopes",
            "drawdown": dec_str(self.max_dd) if self.drawdown_complete else None,
            "drawdown_complete": self.drawdown_complete,
            "equity_points": self.equity_points,
            "evaluation": {
                "holdout_access_count": self.cfg["prior_holdout"] + (1 if self.holdout_touch or self.holdout_leak else 0),
                "holdout_leak": self.holdout_leak,
                "holdout_touched": self.holdout_touch or self.holdout_leak,
                "prior_trial_count": self.cfg["prior_trials"],
                "trial_id": self.cfg["trial_id"],
                "trial_index": self.cfg["prior_trials"] + 1,
            },
            "evidence_class": self.cfg["evidence_class"],
            "fees": dec_str(self.fees),
            "fills_are_observed_exchange_fills": False,
            "funding": {"claimed": funding_claim, "missing": self.funding_gaps, "posted": dec_str(self.funding_posted), "status": self.funding_status},
            "gross_pnl": None if gross is None else dec_str(gross),
            "judge_provenance": role_provenance(self.cfg["judge_id"]),
            "budget": {"amount": dec_str(self.cfg["budget_cash"]), "currency": self.cfg["budget_currency"]},
            "chart": self._chart(),
            "fx": {
                "conversion_executed": False,
                "eur_translation": None,
                "exchange_costs": "UNMODELLED" if self.cfg["fx_status"] == "reference_valuation" else None,
                "permanent_label": "real EUR/USDC exchange costs unmodelled; not a deposit-to-withdrawal return" if self.cfg["fx_status"] == "reference_valuation" else None,
                "revaluation_eur": None,
                "status": self.cfg["fx_status"],
                "usable_quote": None if self.cfg["usable_quote"] is None else dec_str(self.cfg["usable_quote"]),
                "usable_quote_currency": "USDC",
                "usd_usdc_assumed_one": False,
            },
            "ledger": {
                "average_price": None if self.average is None else dec_str(self.average),
                "budget_cash": dec_str(self.cfg["budget_cash"]),
                "budget_currency": self.cfg["budget_currency"],
                "cash": None if not self.cfg.get("quote_enabled", True) else dec_str(self.cash),
                "currency": "USDC" if self.cfg.get("quote_enabled", True) else None,
                "equity": None if equity is None or not self.cfg.get("quote_enabled", True) else dec_str(equity),
                "mark": None if self.mark is None else dec_str(self.mark),
                "position": dec_str(self.position),
                "realized": dec_str(self.realized) if self.cfg.get("quote_enabled", True) else None,
                "starting_cash": dec_str(self.cfg["starting_cash"]) if self.cfg.get("quote_enabled", True) else None,
                "unrealized": None if unrealized is None or not self.cfg.get("quote_enabled", True) else dec_str(unrealized),
            },
            "native_decision": "pending" if not self.source.envelopes else "imported_file_unauthenticated",
            "pnl_breakdown": self._breakdown(gross, net, slippage),
            "readiness": {
                "authorized_later": True,
                "started": False,
                "training": "not_run",
                "unavailable": [item for item in ("strategy" if self.cfg["strategy"].get("status") != "supplied" else "", "fx" if self.cfg["budget_currency"] == "EUR" and self.cfg["fx_status"] != "cited" else "", "native_risk_and_judge_handoff" if not self.source.envelopes else "", "bounded_live_run") if item],
            },
            "limitations": LIMITATIONS,
            "mode": "local_paper",
            "net_pnl": None if net is None else dec_str(net),
            "orders": counts,
            "protection_selected": self.cfg["protection"]["selected"],
            "protection_mode": None if not self.cfg["protection"]["selected"] else self.cfg["protection"]["mode"],
            "reconciliation": reconciliation,
            "runtime_bot_integration": False,
            "schema_version": SCHEMA_VERSION,
            "segments": {name: {"fees": dec_str(item["fees"]), "fill_notional": dec_str(item["fill_notional"]), "fills": item["fills"], "funding": dec_str(item["funding"])} for name, item in self.segments.items()},
            "slippage": {"amount": None if slippage is None else dec_str(slippage), "reference": "decision_time_mid", "status": "exact" if slippage is not None else "unavailable"},
            "source_network": self.cfg["source_network"],
            "supported": SUPPORTED,
            "turnover": dec_str(self.buy_notional + self.sell_notional),
            "unimplemented": UNIMPLEMENTED,
            "unresolved_virtual_exposure": self.unresolved_exposure,
            "unresolved_virtual_insolvency": self.insolvent,
            "reviewer": {"human_review": "not_run", "pnl_is_not_a_process_grade": True, "process_grade": None, "process_journal": "mechanical"},
        }
        orders = []
        for order in self.order_list:
            orders.append({
                "action_taken": order.action_taken,
                "client_id": order.client_id,
                "decision_id": order.decision_id,
                "fills": order.fills,
                "kind": order.kind,
                "reason": order.reason,
                "reason_de": REASONS_DE.get(order.reason, "") if order.reason else "",
                "recorded_verdict": order.recorded_verdict,
                "remaining": dec_str(order.remaining),
                "status": order.status,
            })
        ledger = dict(report["ledger"])
        ledger.update({"fees": report["fees"], "funding_posted": report["funding"]["posted"], "reconciliation": reconciliation})
        return report, orders, ledger


def replay(config, tape, decisions, journal=None):
    if callable(decisions):
        raise PaperError("unsupported", "a live or fresh judge is not called during replay")
    parsed = validate_config(config)
    if tape.get("evidence_class") != parsed["evidence_class"]:
        raise PaperError("config_incomplete", "tape evidence class must match the config")
    assert_bundle(parsed)
    events = parse_tape(tape, parsed["coin"])
    source = decisions if isinstance(decisions, RecordedDecisionSource) else RecordedDecisionSource(decisions)
    if source.evidence_class != parsed["evidence_class"]:
        raise PaperError("config_incomplete", "decision evidence class must match the config")
    journal = journal or Journal()
    simulator = Simulator(parsed, events, source, journal, digest(canonical(config)), digest(canonical(tape)))
    report, orders, ledger = simulator.run()
    files = {
        "config.json": pretty(config),
        "tape.json": pretty(tape),
        "decisions.json": pretty(decisions if not isinstance(decisions, RecordedDecisionSource) else {"schema_version": SCHEMA_VERSION, "evidence_class": source.evidence_class, "envelopes": list(source.envelopes.values())}),
        "journal.jsonl": journal.text(),
        "orders.json": pretty(orders),
        "ledger.json": pretty(ledger),
        "report.json": pretty(report),
    }
    manifest = {
        "experiment_accepted": report["accepted_experiment"],
        "files": {name: {"bytes": len(data), "sha256": digest(data)} for name, data in files.items()},
        "journal_head": journal.prev,
        "limitations": LIMITATIONS,
        "mode": "local_paper",
        "schema_version": SCHEMA_VERSION,
    }
    files["manifest.json"] = pretty(manifest)
    report["manifest_sha256"] = digest(files["manifest.json"])
    files["report.json"] = pretty(report)
    manifest["files"]["report.json"] = {"bytes": len(files["report.json"]), "sha256": digest(files["report.json"])}
    files["manifest.json"] = pretty(manifest)
    # The manifest hash of report.json cannot include manifest_sha256 without a cycle.
    # report.json above includes manifest_sha256 of the previous manifest. Recompute once
    # without putting the manifest hash inside the hashed report.
    report.pop("manifest_sha256", None)
    files["report.json"] = pretty(report)
    manifest["files"]["report.json"] = {"bytes": len(files["report.json"]), "sha256": digest(files["report.json"])}
    files["manifest.json"] = pretty(manifest)
    return {"accepted": report["accepted_experiment"], "files": files, "journal": journal, "report": report, "source": source}


def write_run(directory, result):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    for name, data in result["files"].items():
        if name == "manifest.json":
            continue
        with (directory / name).open("xb") as handle:
            handle.write(data)
    with (directory / "manifest.json").open("xb") as handle:
        handle.write(result["files"]["manifest.json"])
    return directory


def verify_run(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise PaperError("verification_failed", "run directory is not a real directory")
    for name in ("config.json", "tape.json", "decisions.json", "journal.jsonl", "orders.json", "ledger.json", "report.json", "manifest.json"):
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise PaperError("verification_failed", f"missing run file {name}")
    verify_journal((directory / "journal.jsonl").read_bytes())
    config = load_exact(directory / "config.json")
    tape = load_exact(directory / "tape.json")
    decisions = load_exact(directory / "decisions.json")
    result = replay(config, tape, decisions)
    for name, data in result["files"].items():
        if (directory / name).read_bytes() != data:
            raise PaperError("verification_failed", f"run file differs from replay: {name}")
    return json.loads(result["files"]["manifest.json"])


def export_handoff(template_path, directory):
    """Write a local evidence bundle. It contains no verdict and starts no run."""
    raw = load_exact(template_path)
    validate_paper_template(raw)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    template_bytes = Path(template_path).read_bytes()
    pending = {
        "authenticated": False,
        "channel": "local_file",
        "coordinator": "sanctioned_messaging",
        "exit": "required_unselected",
        "fx": "policy_selected_rates_not_stored",
        "judge_verdict": "pending",
        "native_decision": "pending",
        "reviewer": "not_run",
        "risk_verdict": "pending",
        "runtime_integration": False,
        "strategy": "required_unselected",
    }
    journal = Journal()
    journal.write({"fail_key": None, "human_review": "not_run", "kind": "reviewer_pending", "mode": "local_paper"})
    files = {"pending.json": pretty(pending), "reviewer_journal.jsonl": journal.text(), "template.json": template_bytes}
    manifest = {
        "files": {name: {"bytes": len(data), "sha256": digest(data)} for name, data in files.items()},
        "plan_sha256": raw["plan_sha256"],
        "ready": False,
        "run_started": False,
        "training": "not_run",
    }
    files["manifest.json"] = pretty(manifest)
    for name, data in files.items():
        (directory / name).write_bytes(data)
    return manifest


def import_decisions(path):
    """Read a coordinator-supplied file. No chat client and no generated verdict."""
    document = load_exact(path)
    supply = document.get("supply")
    if not isinstance(supply, dict):
        raise PaperError("config_incomplete", "imported decisions need a supply record")
    need_keys(supply, ["authenticated", "channel", "claimed_producer_role", "coordinator"], "supply")
    if supply["authenticated"] is not False or supply.get("runtime_integration") is True:
        raise PaperError("unsupported", "a file cannot authenticate a Bot")
    if supply["channel"] != "local_file" or supply["coordinator"] != "sanctioned_messaging":
        raise PaperError("unsupported", "decisions are imported from a local file, not a chat or Bot API")
    if supply["claimed_producer_role"] not in {"desk-lead", "risk-manager", "trade-reviewer"}:
        raise PaperError("config_incomplete", "claimed role is not one of the existing roles")
    source = RecordedDecisionSource({key: document[key] for key in ("schema_version", "evidence_class", "envelopes")})
    return {
        "authenticated": False,
        "envelopes": len(source.envelopes),
        "invalid": len(source.invalid),
        "native_decision": "pending" if not source.envelopes else "imported_file_unauthenticated",
        "runtime_integration": False,
    }


def schema():
    return {
        "schema_version": SCHEMA_VERSION,
        "command": "paper_sim",
        "mode": "local_paper",
        "network_access": "none",
        "account_reads": "none",
        "exchange_writes": "none",
        "decision_source": "recorded_envelopes only; replay never calls a judge",
        "judge_authority": "unauthenticated supplied string; not a Bot session",
        "recommended_role": role_provenance(RECOMMENDED_BOT_ID),
        "supported": SUPPORTED,
        "unimplemented": UNIMPLEMENTED,
        "exit_codes": {"0": "replay written and the experiment gates that were required passed", "1": "written run refused as evidence or reconciliation", "2": "invalid config, tape, or decisions"},
    }


def _emit(code, message, json_mode):
    if json_mode:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "mode": "local_paper", "error": {"code": code, "message": message}}, sort_keys=True))
    else:
        print(f"{code}: {message}", file=sys.stderr)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in argv
    parser = ReadOnlyParser(prog="paper_sim", description="Replay a local paper run from explicit config and recorded decisions.", machine_readable=json_mode)
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--validate-config", type=Path)
    parser.add_argument("--validate-template", type=Path)
    parser.add_argument("--export-handoff", type=Path)
    parser.add_argument("--import-decisions", type=Path)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--verify-run", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--tape", type=Path)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.schema:
            print(json.dumps(schema(), indent=2, sort_keys=True))
            return 0
        if args.export_handoff:
            if args.output is None:
                parser.error("--output is required for a handoff bundle")
            manifest = export_handoff(args.export_handoff, args.output)
            print(json.dumps({"schema_version": SCHEMA_VERSION, "mode": "local_file_handoff", "ready": manifest["ready"], "run_started": False, "training": "not_run"}, sort_keys=True))
            return 0
        if args.import_decisions:
            summary = import_decisions(args.import_decisions)
            print(json.dumps({"schema_version": SCHEMA_VERSION, "mode": "local_file_handoff", **summary}, sort_keys=True))
            return 0
        if args.validate_template:
            report = validate_paper_template(load_exact(args.validate_template))
            print(json.dumps({"schema_version": SCHEMA_VERSION, "mode": "paper_config_template", **report}, indent=2, sort_keys=True))
            return 0
        if args.validate_config:
            parsed = validate_config(load_exact(args.validate_config))
            print(json.dumps({"schema_version": SCHEMA_VERSION, "mode": "local_paper", "config_sha256": digest(canonical(load_exact(args.validate_config))), "run_id": parsed["run_id"], "protection_selected": parsed["protection"]["selected"]}, indent=2, sort_keys=True))
            return 0
        if args.verify_run:
            manifest = verify_run(args.verify_run)
            print(json.dumps({"schema_version": SCHEMA_VERSION, "mode": "local_paper", "verification": "verified", "experiment_accepted": manifest["experiment_accepted"]}, indent=2, sort_keys=True))
            return 0 if manifest["experiment_accepted"] else 1
        if not args.replay:
            parser.error("choose --schema, --validate-config, --replay, or --verify-run")
        for name in ("config", "tape", "decisions", "output"):
            if getattr(args, name) is None:
                parser.error(f"--{name} is required for replay")
        config = load_exact(args.config)
        tape = load_exact(args.tape)
        decisions = load_exact(args.decisions)
        result = replay(config, tape, decisions)
        write_run(args.output, result)
    except PaperError as exc:
        _emit(exc.code, str(exc), json_mode)
        return 2 if exc.code in {"config_incomplete", "unsupported", "lookahead", "float_rejected", "invalid_arguments", "missing_field", "network_denied", "hash_mismatch", "berlin_time_mismatch", "duplicate_intent"} else 1
    payload = {"schema_version": SCHEMA_VERSION, "mode": "local_paper", "output": str(args.output), "accepted_experiment": result["accepted"], "claims_demonstrated_edge": False}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
