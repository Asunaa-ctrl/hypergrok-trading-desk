# Local paper

Local paper is a hypothetical ledger in this process. It is not testnet, not mainnet, and not an exchange ticket.

Testnet practice still uses the normal lifecycle: a Risk PASS, your approval by ticket id, and one send by the Execution Trader. A recorded `APPROVE` here does not approve that ticket and does not change `risk-limits.md`. The Desk Lead profile is unchanged. No new Bot is created. Native acknowledgement of the local judge role is pending.

The existing Desk Lead has acknowledged this local-paper mandate as role understanding. That acknowledgement is not an authenticated connection and this process does not call the Bot. The role file is unchanged and no new Bot is created. The id `a9a5e3d6-724d-44ab-bb6f-ce99f55ac11b` is only a declared string. Matching it does not authenticate a session. Replay never asks a model or a chat for a fresh verdict. An edited verdict or judge id that no longer matches its provenance hash is refused.

## Paper template

`scripts/fixtures/local_paper/paper_config_template.json` records the explicit unrun paper setup: 100.00 EUR virtual budget, USDC as the venue quote, no stored EUR/USD rate, USDC/USD unavailable and not assumed equal to USD, BTC observation that opens no exposure, taker-only, strategy and exit required but unselected, and a first bound of 3 days or 5 simulated trades. The 14-day and 20-trade example is not the bound. `python3 scripts/paper_sim.py --validate-template <file>` checks that file and does not start a run. A later start remains authorized only after the missing gates exist. This checkout does not deploy to another computer.

## What you must supply

The config has no strategy, capital, fee, risk, latency, or protection default. Omit any of them and the run stops. A synthetic fixture cannot claim market evidence. Protection is either explicitly off, `recorded_exits_only`, or `stop_mark` for one side. With protection off, an entry that opens exposure is refused. Nothing here invents a stop price.

`decision_source` must be `recorded_envelopes`. Each entry or exit envelope is `APPROVE` or `REJECT` and is bound to hashes, a ledger version, an expiry in tape time, and a Risk verdict. Risk `REJECT` blocks the action even if the recorded verdict is `APPROVE`. The envelope is not rewritten. Journal failure blocks the hypothetical mutation.

## Command

```bash
python3 scripts/paper_sim.py --schema
python3 scripts/paper_sim.py --validate-config config.json
python3 scripts/paper_sim.py --replay \
  --config config.json --tape tape.json --decisions decisions.json \
  --output ../paper-data/runs/lauf-1
python3 scripts/paper_sim.py --verify-run ../paper-data/runs/lauf-1
python3 scripts/paper_dashboard.py --root ../paper-data --port 8765
```

The dashboard listens on `127.0.0.1` only. Open `http://127.0.0.1:8765/`. It reads run manifests and `engineering-tests.jsonl`. It has no order button and does not start a run. With no captured paper run it shows `Noch kein Paper-Durchlauf gestartet` and no invented balance.

`--schema` and `--verify-run` do not use the network. The public `/info` allowlist exists for an injected transport and is not called by these commands. Live collection is not enabled.

## Supported and not supported

Supported: cash-secured linear USDC perps without leverage; limit orders with `gtc`, `ioc`, and `alo`; taker walks of visible depth; a conservative maker queue or an explicit unsupported maker; hourly funding only when the payment event has both the hourly rate and the oracle; recorded entry and exit decisions; a preauthorized stop that runs before discretionary intents when that policy is present in the config.

Not supported: leverage, cross or isolated margin, liquidation, trigger orders, spot, a live judge, account reads, signing, and any send on mainnet or testnet. Public L2 does not prove queue position. Candles do not contain the intrabar path, the historical spread, or the historical oracle. Missing funding is unavailable, not zero. A passing engineering test is not a strategy result.

Hashes show that a local directory still matches its replay. They do not make the directory tamper-proof and they do not prove who produced a decision.
