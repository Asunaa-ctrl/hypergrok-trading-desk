# LOCAL-PAPER training plan — 100 EUR virtual capital

Status: DELEGATED-CONFIG-SELECTED / NOT-READY. Not active; no campaign. Prepared 2026-11-08 16:22 CET (Europe/Berlin) by Desk Lead a9a5e3d6-724d-44ab-bb6f-ce99f55ac11b.
Authority: user to Claude (eb68c2b6-b674-4dae-b44a-94d0517d36c4, pos170, 2026-11-08 16:18 CET): "Also alles sollst du entscheiden was ideal ist aber das Startkapital ist 100€". Claude delegated drafting to Desk Lead. Claude then selected the config items marked "selected by Claude under user delegation (Claude transcript pos170)". These are delegated selections, NOT direct user answers to this plan. Edited 2026-11-08 16:24 CET to record that. "Ideal" is not claimed. No return, edge or profitability is claimed or implied.
Scope: LOCAL simulation only, per paper-judge-mandate.md and paper-training-preparation.md. No accounts, keys, signatures, wallets, testnet or /exchange calls; desk-strategy-lab §5 testnet path is excluded (paper-judge-mandate.md:15). /workspace/trading-desk/risk-limits.md stays absent and untouched; live desk limits unchanged (desk.md:13). No campaign, routine, bot or post is started by this plan.

## 1. Capital and currency

- Budget currency: EUR. Virtual starting capital: 100.00 EUR (user-given; the only selected number).
- Ledger currency: USDC (Hyperliquid perp collateral; prices quoted in USD). No 1:1 EUR=USD or USD=USDC mapping.
- Run-start record (mandatory, in the run manifest): the ECB EUR/USD reference and the Kraken USDCUSD last trade, both re-read at run start under the FX valuation policy below. Old evidence (including the 2026-11-08 sample) is never reused as run FX. Limits are converted once at start and fixed in USDC for the run. Any EUR translation at the end uses a newly cited end reference, else "EUR translation unavailable".
- If any FX policy check fails or is unknown, BLOCK: the run cannot start. No rate is selected or recorded by this plan.
- FX source identification (research 2026-11-08 16:58 CET / 15:58Z; evidence data/paper-fx-sources-20261108T155823Z/manifest.json (sha256 22d58dc58d466cdc9de8cca8a2b8c298f39ac64003e09f7ec79becf9379a2f8b)). Public reads only; no account or key.
  - EUR/USD: ECB euro reference rate. Sources: https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml and the SDMX series EXR/D.USD.EUR.SP00.A (https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A). Units: USD per 1 EUR. The rate is fixed by concertation at about 14:10 CET, labelled 2.15 pm CET, and published around 16:00 CET each working day except TARGET closing days. It is for information only and is NOT an executable conversion. The sample is Friday 2026-11-06 = 1.1165, retrieved on a Sunday, so it is a weekend carry-forward. A weekend or holiday carry-forward is labelled explicitly and governed by the selected FX valuation policy below (≤4 calendar days, else BLOCK). It is never silently treated as fresh. SOURCE IDENTIFIED.
  - USDC/USD: Kraken public spot pair USDCUSD. AssetPairs gives base USDC, quote ZUSD, wsname USDC/USD, tick 0.0001, status online. ZUSD is US dollar fiat per a Kraken support article; the asset class "currency" alone would not prove fiat. Units: USD per 1 USDC. Semantics: the ticker carries bid, ask and last trade but NO event timestamp. Depth levels carry per-level unix-second timestamps. Trades carry event times. /public/Time gives server time. Correction (supersedes earlier loose wording 'recent trade or depth timestamp'): trade freshness is not book freshness, and per-level depth timestamps are not snapshot freshness. This policy uses only the LAST TRADE, and only as a reference valuation, not as book execution. The ticker ask/bid is not claimed executable or current. It is a single-venue market quote, not an index, and not a conversion this desk can execute. Sample: bid 0.9997 / ask 0.9998 (1.0 bps), last 0.9998; latest trade 15:58:01.517Z against server time 15:58:24Z. SOURCE IDENTIFIED. A second venue for cross-check is not identified (optional).
  - These samples are dated evidence only, NOT approved run FX; the evidence directory and manifest are retained unchanged as provenance.
  - Sample arithmetic (illustration only, not run FX): 100 × 1.1165 ÷ 0.9998 (sample last trade) = 111.6723 USDC.
- FX valuation policy (selected by Claude under user delegation (Claude transcript pos170), not direct user approval; recorded 2026-11-08 ~17:00 CET):
  1. The 100 EUR starting capital is a REFERENCE VALUATION ONLY. No simulated EUR→USDC conversion is executed.
  2. EUR/USD: r_EURUSD is the latest published ECB reference (USD per EUR). Its reference date must be at most 4 CALENDAR days old at run start. The reference date and any carry-forward (weekend or holiday) are shown explicitly in the manifest and dashboard. Older or unclear dates → BLOCK.
  3. USDC/USD: p_lastTrade_USDCUSD is the actual latest Kraken USDCUSD TRADE price with its provider event timestamp. Trade age is measured against the queried Kraken provider time (/public/Time) and must be ≤60 s. A future-dated, unknown or missing timestamp → reject/BLOCK.
  4. Retrieval checks, kept separate from trade age: (a) retrieval round duration (local request→response, all FX calls in the round) ≤5 s; (b) clock deviation between local clock and provider time ≤5 s. Unknown or excess → BLOCK. Duration, clock offset and event age are three different quantities and are never conflated.
  5. Reference calculation: C_USDC = 100 × r_EURUSD ÷ p_lastTrade_USDCUSD. It is computed once at run start from that run's fresh reads.
  6. Real EUR/USDC exchange costs (FX spread, deposit/withdrawal and on-ramp fees) are UNMODELLED. This label stays permanently visible in every report and dashboard. No fee or haircut is invented.
  7. Results describe paper trading from a reference-valued budget only. They are NOT a complete EUR deposit-to-withdrawal return.

## 2. Hyperliquid constraints at this size

Rules: minimum order value 10 USD for perps; size rounded to szDecimals (round down on the desk); price ≤5 significant figures and ≤(6 − szDecimals) decimals (hyperliquid-api-reference/SKILL.md:113-115). Never round up to reach the minimum; if the minimum implies more risk than the budget allows, REJECT (desk-risk-limits/SKILL.md:86-87, 155).

Live public metadata, fetched by me: POST https://api.hyperliquid.xyz/info type=metaAndAssetCtxs, mainnet, request 2026-11-08T15:20:19.279Z / received 15:20:20.118Z UTC (16:20 CET). Raw: data/paper-plan-public-meta-20261108T152019Z/metaAndAssetCtxs.json, sha256 3a692923f10ba23fb989269910499c716d6b26f434e47ff8fb3debda495536ac. These are a snapshot for illustration, not run inputs. Re-read at run time.

| perp | szDecimals | maxLev | mark (USD) | 1 lot ≈ USD | smallest size ≥ $10 | its notional |
| --- | --- | --- | --- | --- | --- | --- |
| BTC | 5 | 40 | 76006.0 | 0.76 | 0.00014 | 10.64 |
| ETH | 4 | 25 | 2238.7 | 0.22 | 0.0045 | 10.07 |
| SOL | 2 | 20 | 89.57 | 0.90 | 0.12 | 10.75 |
| HYPE | 2 | 10 | 77.257 | 0.77 | 0.13 | 10.04 |

Arithmetic (r = USD per EUR at run start; USDC≈USD only if (b) above supports it). Minimum order as a fraction of capital = 10 / (100 × r). Illustrative band only, not a chosen rate: r = 1.00 gives 10.0%, r = 1.20 gives 8.3%. In practice the minimum is about 10.0–10.8 USD after lot rounding, so roughly 8–11% of capital.
Consequences: (i) sizing is coarse: one lot is 2–9% of the minimum order; (ii) the risk-sized position must already be ≥ the minimum after rounding down, otherwise REJECT; (iii) the smallest order at risk budget B USD only fits if the stressed stop distance is ≤ B / ~10.7 of entry (see §3); (iv) price moves between decision and simulated fill can push a just-above-minimum order below 10 USD, so REJECT at fill. Margin tiers (marginTableId 52–56 in the snapshot) were not expanded; with the 1x cap below they do not bind at this size, but they must be read from meta before any run (desk-risk-limits/SKILL.md:119).

## 3. PAPER limits — selected by Claude under user delegation (Claude transcript pos170), as written

These are paper-only. They are not a risk-limits.md and not a change to live limits. Each one is stricter than desk-risk-limits §0 (SKILL.md:21-24). Risk skill suggested starting range: 0.25–0.5%/trade, ≤3x (desk-risk-limits/SKILL.md:56).

| item | selected paper value | §0 ceiling |
| --- | --- | --- |
| max risk per trade (stressed, to stop) | 0.5% of virtual equity (≈0.50 EUR at start) | 2% |
| max total open risk incl. reservations | 1.0% | 6% |
| max positions / markets | 1 / 1 perp (named in the user's strategy rule) | — |
| max gross notional | 1.0× virtual equity (no leverage) until a margin/liquidation model is implemented and reviewed | 20x |
| daily loss stop | −2% of start-of-day virtual equity → no new risk until user resets via Claude | −10% |
| run kill (total drawdown) | −5% of starting USDC equity → run stops, review | — |
| stop | deterministic, user-selected stop on every entry (see §5) | mandatory |
| order types | taker-only crossing simulation; maker/resting fills disabled | — |

Feasibility check at this size (sizing per desk-risk-limits/SKILL.md:75-87). Risk budget B = 0.005 × 100 × r USD, so 0.50 USD at r = 1.00 and 0.60 USD at r = 1.20. A minimum order of about 10.7 USD therefore needs a stressed stop distance of ≤ B/10.7, about 4.7–5.6% of entry. A wider stop at 0.5% risk is infeasible: REJECT, never resize up. The 1x notional cap means a stressed stop tighter than about 0.5% is capped by notional, so the risk taken is below budget, which is allowed. Fees are small next to B (2 × 0.045% × 10.7 USD ≈ 0.0096 USD round trip). Tick and lot rounding, stop slippage and spread take a material share of B on tight stops. With ~0.50 USD risk units, each result is a few cents, so samples are small and noisy; the plan makes no claim of statistical power. If the user's strategy needs wider stops, the honest options are a smaller universe of trades (REJECTs), or the user explicitly choosing a higher per-trade risk ≤2%. The desk does not loosen this.

## 4. Data, costs and fill realism — selected by Claude under user delegation (Claude transcript pos170), taker-only fills (gates; any failure → REJECT / no simulated action)

- Freshness: decision snapshot ≤60 s old (as the desk's BTC brief used, briefs/2026-11-08-BTC.md:2). Book used for a simulated fill ≤5 s old relative to the eligible event, else no fill. No exchange timestamp means "retrieval time only", which is labelled.
- Integrity: research bundles only via scripts/research_dataset.py with offline --verify; only `complete` coverage supports a claim (desk-strategy-lab/SKILL.md:66-70; RESEARCH_DATASETS.md:43, 56-63). No forward-fill, no zero funding for gaps, no cropping. Candle retention is limited to the latest 5000 per interval (hyperliquid-market-data/SKILL.md:114).
- Fees: published base tier-0 perps taker 0.045% / maker 0.015% (hyperliquid-api-reference/SKILL.md:137; Hyperliquid fees page as fetched 2026-11-08 15:03 CET, paper-training-preparation.md:32, 71). Use taker on every leg. No discounts or rebates. Recheck the page at run start and freeze its version and time.
- Funding: hourly, paid on oracle price; cash flow = −q × oracle × hourly_rate (positive funding: longs pay) (hyperliquid-api-reference/SKILL.md:134; hyperliquid-market-data/SKILL.md:59, 146; paper-training-preparation.md:36). Missing oracle or rate means funding is unavailable, not zero. Snapshot magnitude: 0.0000125/h on 10 USD ≈ 0.000125 USD/h. This is observed, not a forecast.
- Slippage/depth: walk observed L2 on the correct side at the eligible event (after a configured latency). Never fill at mid. Size beyond visible depth leaves a partial or rejected remainder; it is not extrapolated. Twenty levels are a page, not the book (hyperliquid-market-data/SKILL.md:84, 102). Stop slippage is at least the current spread and is widened on thin depth (desk-risk-limits/SKILL.md:77-78). Sensitivity: 2× fees and 2× slippage must be reported.
- No "touched means filled"; partial fills are charged per fill, and the remainder is handled per the frozen rule. Candle-only intrabar stop/target ambiguity uses the conservative outcome or is flagged (paper-training-preparation.md:33-35). Today's book is not historical depth.

## 5. Protection and exit

No stop/exit/protection policy is selected (paper-judge-mandate.md:53). Proposed requirement: the user selects a deterministic stop/exit/expiry policy (exact triggers, evidence, priority, audit timing). It is frozen and versioned before any run. Until then, any run that can open simulated exposure is BLOCKED, and the judge REJECTs every entry. Observation-only work (§7 Phase A) opens no exposure and is unaffected. Once selected, policy-driven protective actions follow the mandate without a new discretionary veto (paper-judge-mandate.md:51).

## 6. Abort / kill conditions (run stops; state frozen and journaled; restart needs Claude-relayed user go-ahead)

1. Data stale beyond §4 thresholds for >5 min, or any gap, out-of-order, duplicate-conflict or reconnect not reconciled → pause fills; if still unresolved, abort.
2. Journal / decision-log append fails, or a hash chain mismatch → REJECT, then abort (paper-judge-mandate.md:45).
3. Daily loss stop (−2%) → no new risk. Run kill (−5% total) → abort.
4. Unknown or irreconcilable simulated state, duplicate/expired envelope use, or ledger conservation failure → abort.
5. Any code path attempting /exchange, account endpoints, keys or signing → abort and incident note.
6. Missing FX record (§1), changed fee schedule without re-freeze, or a limits/config hash mismatch → abort.
7. Horizon reached (§9 first run) → normal stop.

## 7. Horizon and usage discipline

- Phase 0, prerequisites (no data runs): the simulator on the user's fork is reviewed and its tests pass (Claude-owned; not verified here, paper-training-preparation.md:76-80), and the user inputs in §8 are given.
- Phase A, observation-only baseline (no strategy, no trades, no exposure). Market: BTC, selected by Claude under user delegation (Claude transcript pos170). (a) One historical bundle: BTC 1h candles + funding over one fixed closed 30-day UTC window (720 slots), then verify offline. (b) At most 10 manual opening_bell snapshots over ≤3 days, to measure spread, depth reach, freshness and funding consistency. Output: a data-quality report (coverage, gaps, latency, spread/depth distribution). No signals, no PnL.
- Phase B: the ONLY authorized horizon is the §9 bounded first run (max 3 days / 5 simulated trades, then stop for review). Longer phases (for example 14 calendar days or 20 simulated trades, desk-strategy-lab/SKILL.md:113) are NOT AUTHORIZED. They are possible future steps only, after the review and a new decision. Report per paper-training-preparation.md:46. Record "no demonstrated edge" if the evidence is insufficient (<30 trades = not enough evidence, desk-strategy-lab/SKILL.md:103).
- Usage: no routines, no WebSocket recorder, no high-frequency polling by default. Prospective continuous capture needs a separate approval. Batch /info reads and respect the weights (hyperliquid-api-reference/SKILL.md:120). Handoffs are path + hash + task + open points, with short acks (desk.md:117-119).

## 8. Strategy and required user inputs

Strategy: UNSELECTED. The desk must not originate one: "Never propose a strategy the user did not bring." (/workspace/hypergrok/skills/desk-strategy-lab/SKILL.md:121; also :13). Allowed now: methodology and Phase A observation only.

Delegated selections (selected by Claude under user delegation (Claude transcript pos170)): §3 limits, §4 model with taker-only fills, BTC for Phase A observation, USDC/USD requires a real cited source, and §9 first-run horizon.

Remaining DOMAIN user inputs (the only two). They are NOT covered by the pos170 delegation, because the desk/Claude must not originate them:
1. Strategy rule: the user's own trading idea as complete rules in strategies/<name>/RULES.md. Governing rule: /workspace/hypergrok/skills/desk-strategy-lab/SKILL.md:121 (also :13, :27-44).
2. Exit/protection policy: a deterministic stop/exit/expiry policy. Governing rule: /workspace/trading-desk/paper-judge-mandate.md:53 ("Do not invent one"). It may be part of the RULES.md in input 1.

Open TECHNICAL gates (not user inputs):
- Simulator review, integration and tests (paper-judge-mandate.md:3, 59; paper-training-preparation.md:63).
- FX: sources IDENTIFIED (ECB; Kraken USDCUSD), and the valuation policy is SELECTED (§1, Claude under delegation). NOT READY: reader enforcement of the ≤4-calendar-day, ≤60 s trade age, ≤5 s duration and ≤5 s clock checks, its tests, and a re-read at run start.
- Dated EUR/USD FX recorded at run start (§1).
- Fresh public data capture working within §4 (§9 checklist).
- Judge workflow working end to end (paper-judge-mandate.md:41-47).
Run authority: CONDITIONAL (§9). No further user yes is needed once every gate passes.

## 9. Bounded start authority (CONDITIONAL)

Authority: user to Claude (eb68c2b6, pos176, 2026-11-08 ~16:21 CET): "Ja also das Training kannst du sobald alles geht und bereit ist starten ohne meine Einverständnis ich will auch das ich dam im Trainingsdashoard die grafen sehe und alles wieviel plus usw alles zusammen".
Meaning: the user pre-authorizes starting LOCAL paper training (100 EUR virtual, real public data) without a further yes, but ONLY when every gate below is actually satisfied and evidenced in the run manifest. This is not exchange, testnet, account or key authority, and it carries no return guarantee. §3 limits are selected by Claude under user delegation (Claude transcript pos170); they are not direct user confirmation. An engineering PASS alone is NOT a training-ready signal.

Start checklist (all must be PASS with evidence path and hash; any FAIL or unknown means no start):
- [ ] Config: schema-validated run config frozen and hashed; no defaults filled silently.
- [ ] User strategy rule: user's own complete RULES.md frozen and hashed (desk-strategy-lab/SKILL.md:121).
- [ ] Risk/paper limits: §3 as selected by Claude under user delegation (Claude transcript pos170), each ≤ §0 ceilings; independent Risk PAPER check available.
- [ ] Exit/protection policy: user-selected deterministic policy frozen and tested (§5).
- [ ] Data capture fresh: public-only adapter live and within §4 freshness; integrity checks pass; no unresolved gaps.
- [ ] Dated FX per the §1 policy: ECB reference ≤4 calendar days with date and carry-forward shown; Kraken last trade ≤60 s against provider time; round duration ≤5 s; clock deviation ≤5 s; re-read and recorded at run start; enforced by a tested reader. NOT READY.
- [ ] Evidence/journal: append-only decision log and Reviewer journal writable; hash chain verified; append-before-action tested.
- [ ] Judge workflow: binary APPROVE/REJECT envelope flow per paper-judge-mandate.md working end to end, including REJECT paths.
- [ ] Simulator: implementation reviewed, integrated, and its tests passing (paper-judge-mandate.md:59; paper-training-preparation.md:63). Network guard refuses /exchange, account endpoints and signing on both networks.
- [ ] §6 kill conditions wired and tested.

Bounded first run (the ONLY authorized horizon): Phase A observation first (§7: one 30-day BTC 1h+funding bundle, ≤10 snapshots, no trades). Then, if all gates pass, at most 3 calendar days or 5 simulated trades, whichever comes first, single market, §3 limits. Then STOP for review by Reviewer, Desk Lead and the user via Claude before any further run. A §6 kill ends it earlier. Restarts or extensions need a fresh gate check and a review result.

Dashboard: graphs (equity, net PnL after fees/funding/slippage, drawdown, trades) are Claude's separate code task. They are shown from real recorded run data only; no simulated, backfilled or illustrative curves are presented as results. Equity is shown in USDC, with the 100 EUR reference valuation and the permanent label "real EUR/USDC exchange costs unmodelled; not a deposit-to-withdrawal return".
Note (historical): the user's PC was offline at ~16:21 CET. It reconnected at 16:23 CET per Claude; this is agent-reported, not independently verified. Nothing is deployed, controlled or started by this document.
