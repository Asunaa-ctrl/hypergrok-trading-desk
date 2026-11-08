# Lokales Papier — Umsetzungs- und Prüfbericht

Stand: 2026-11-08. Fork `Asunaa-ctrl/hypergrok-trading-desk`, Basis `v1.7.1`, Commit `82bc645899b9581a693463b59553aa1329e021c4` (`git rev-parse HEAD` und `git rev-parse v1.7.1^{}` stimmen überein). Kein Upstream-PR, kein Merge, keine Veröffentlichung, kein Deployment.

Es wurde kein neuer Bot angelegt. Die vorhandene Desk-Lead-Rolle bleibt unverändert. Der bestehende Desk Lead hat das lokale Mandat als Rollenverständnis zur Kenntnis genommen. Das ist keine authentifizierte Verbindung und kein Laufzeitzugriff. Die Kennung `a9a5e3d6-724d-44ab-bb6f-ce99f55ac11b` bleibt eine Zeichenkette. Ein geänderter Urteilstext oder eine geänderte judge_id ohne passenden Provenance-Hash wird nicht ausgeführt.

## Befehle und Ergebnisse

Nach der Prüfung auf gefälschte Urteile, Zustandsrennen, Ledger, Lookahead, Manifest-Tampering und die Anzeige:

```text
python3 scripts/test_paper_sim.py
# Ran 25 tests — OK

python3 scripts/test_paper_dashboard.py
# Ran 6 tests — OK

bash scripts/check.sh
# ok: 17 skills, 7 agents, 68 markdown files
# Passed 18 read-only CLI journeys
```

Neue Abhängigkeiten: keine. Die Simulator-Tests schließen ein: geändertes Urteil ohne Provenance-Hash führt nicht aus; geänderte judge_id wird abgelehnt; ein späteres frisches Buch füllt nicht zum veralteten Preis; eine Finanzierung nach der gebundenen Version blockiert die wartende Order; Manifest, Ledger und Journal, die nach dem Schreiben geändert werden, scheitern bei `--verify-run`; Replay öffnet keinen Socket. Der offizielle `/info`-Transport wird nur mit Mock-HTTP geprüft. Dieser Lauf hat `api.hyperliquid.xyz` nicht aufgerufen.

## Screenshots

Diese Dateien liegen auf dem Rechner des Agenten, nicht auf dem Rechner des Nutzers:

- `/opt/cursor/artifacts/papier-leer.png`
- `/opt/cursor/artifacts/papier-synthetisch.png`
- `/opt/cursor/artifacts/papier-capture.png`
- `/opt/cursor/artifacts/papier-widerspruch.png`
- `/opt/cursor/artifacts/papier-mobil.png`

Sie zeigen nacheinander den leeren Ordner, ein erfundenes Fixture mit REJECT, einen als Capture bezeichneten Lauf ohne Marktnachweis, einen widersprüchlich umetikettierten Ordner ohne Kontostand, und die schmale Ansicht.

## Wo die Oberfläche läuft

Der Agent hat die Seite nur auf dem Loopback seines eigenen Remote-Rechners gelesen. Diese Adresse ist von hier aus nicht der Rechner des Nutzers und auch kein gemeinsamer Desktop. Sie ist jetzt nicht erreichbar.

Damit die Oberfläche sichtbar wird, muss der Branch auf dem Rechner ausgecheckt werden, auf dem der Browser läuft. Dort, und nur dort, startet `python3 scripts/paper_dashboard.py --root <lokaler Ordner> --port 8765`. Der Browser auf demselben Rechner kann danach dessen Loopback öffnen. Ein anderer Rechner sieht diese Schleifenadresse nicht.

## Abnahme, nicht trainingsbereit

Plantext-Hash `ada0def6834c7ddac2953c325bcce9a86f2e0ff3d5669180648b8e7c47a40b9c`. Ein bestandener Engineering-Test ist nicht Trainingsbereitschaft.

| Thema | Stand |
| --- | --- |
| Vorlage 100 EUR, Taker, BTC, 3 Tage oder 5 Trades, Strategie/Exit ungewählt | implementiert, `validate_paper_template`, nicht gestartet |
| Loopback-Capture, Redirect/Host/Exchange-Sperre, Offline-Umwandlung | implementiert, `paper_capture.capture_loopback`, `fixture_to_tape`, `main --observe-plan` |
| Offizieller Host-Abruf | implementiert, opt-in, Standard aus. `paper_capture.execute_observation` nach `--confirm-public-info`. POST nur `https://api.hyperliquid.xyz/info` oder Testnet `/info`, TLS-Prüfung, keine Redirects, kein Proxy, höchstens 8 Requests, 1 s Abstand, 60 s Wandzeit. Ohne Bestätigung `collection_not_confirmed` und kein Socket. In diesem Lauf nicht ausgeführt |
| Datei-Handoff ohne Bot-API | implementiert, `export_handoff`, `import_decisions` |
| Equity/Kerzen/Buch | implementiert für ein geprüftes `observed_public_info`-Schema und für einen `captured_public_replay`-Lauf: Kerzen, Buch, Equity, Netto, Drawdown, Lücken, fehlende Kerzen, veraltetes Buch. Lokaler SHA-256, `externally_authenticated` falsch. Synthetische Fixtures bleiben in der Engineering-Fläche. Ohne Datei bleibt die Marktfläche leer |
| FX-Referenzleser | implementiert, opt-in, Standard aus. `paper_fx.execute_reference` nach `--confirm-public-fx`. ECB-Tages-XML, Kraken Time und Kraken USDCUSD-Trade, genau diese drei URLs. ≤4 Kalendertage, Trade-Alter ≤60 s gegen Providerzeit, Runde ≤5 s, Uhrabweichung ≤5 s. Formel `100 * ECB_USD_per_EUR / Kraken_USD_per_USDC`. Keine gespeicherten Altkurse, keine ausgeführte Umrechnung, Wechselkosten `UNMODELLED`. In diesem Lauf nicht ausgeführt |
| Native Entscheidung, echter Lauf, Strategie, Exit | fehlen, blockieren den Start |

Spätere Beobachtung, nur durch den Koordinator und nicht in diesem Lauf:

```bash
python3 scripts/paper_capture.py --observe-plan --coin BTC --max-requests 4
python3 scripts/paper_capture.py --execute-observation --confirm-public-info \
  --base-url https://api.hyperliquid.xyz --coin BTC --max-requests 4 --timeout 5 \
  --output ../paper-data/captures/btc-obs-1
python3 scripts/paper_capture.py --to-tape ../paper-data/captures/btc-obs-1/manifest.json \
  --output ../paper-data/tapes/btc-obs-1.json
```

Der erste Befehl öffnet keinen Socket. Der zweite ist der einzige öffentliche Hyperliquid-Lesezugriff. Er wählt keine Strategie und öffnet keine Exposition.

FX-Referenz, ebenfalls nur später und nicht in diesem Lauf:

```bash
python3 scripts/paper_fx.py --policy
python3 scripts/paper_fx.py --import-round scripts/fixtures/local_paper/fx_source_identification_manifest.json
python3 scripts/paper_fx.py --execute-reference --confirm-public-fx \
  --as-of YYYY-MM-DD --output ../paper-data/fx/run-start-1
```

`--policy` öffnet keinen Socket. Der Import der Quellen-Manifest-Datei muss `not_run_fx` ergeben. Nur der dritte Befehl liest ECB und Kraken. Er schreibt keinen Kurs in die Vorlage und startet keinen Lauf.

Beweisbilder, nur Agentenrechner: `/opt/cursor/artifacts/papier-markt-leer.png` zeigt die leere Marktfläche. `/opt/cursor/artifacts/papier-testschema.png` zeigt die TEST-ONLY-Schemazeichnung und ist kein externer Abruf.

## Vorlage, nicht gestartet

`scripts/fixtures/local_paper/paper_config_template.json` ist die ungelaufene Papier-Vorlage: 100,00 EUR, USDC als Börsenquote, kein gespeicherter EUR/USD-Kurs, USDC/USD nicht verfügbar und nicht 1:1, BTC-Beobachtung ohne Exposition, nur Taker, Strategie und Exit erforderlich aber nicht ausgewählt, erste Grenze 3 Tage oder 5 simulierte Trades. Die 14-Tage- und 20-Trade-Angabe gilt nicht. `--validate-template` prüft die Datei und startet nichts. Ein späterer Start bleibt nur zulässig, wenn die offenen Tore wirklich belegt sind. Der Windows-Rechner wurde von hier nicht aktualisiert.

Neue Terminalbilder, nur auf dem Agentenrechner:

- `/opt/cursor/artifacts/papier-terminal-eur.png`
- `/opt/cursor/artifacts/papier-terminal-synthetisch.png`
- `/opt/cursor/artifacts/papier-terminal-mobil.png`

## Unterstützt

- Aufgezeichnete Hüllen mit `APPROVE` oder `REJECT`, im Replay ohne neues Urteil.
- Risiko-Veto, harte Grenzen, Frische, Zustandshash, Ablauf, Einmalnutzung, Reservierung, Journal vor der Mutation.
- Entry und Exit. Exit ist reduce-only. Ohne ausgewählte Schutzpolitik wird neue Exposition abgelehnt.
- `recorded_exits_only` oder `stop_mark` nur wenn die Konfiguration sie ausdrücklich setzt. Schutz läuft vor diskretionären Absichten und wartet nicht auf einen Chat.
- Cash-besicherter linearer Perp ohne Hebel, sichtbare Buchtiefe, konservative Maker-Queue oder ausdrücklich nicht unterstützter Maker.
- Finanzierung nur mit Stundensatz und Oracle am Zahlungsereignis. Fehlendes Oracle oder eine fehlende Stunde ist nicht verfügbar, nicht null.
- Dashboard nur auf `127.0.0.1`, nur lesend.

## Nicht umgesetzt

- Keine Laufzeitverbindung zum Desk Lead oder zu einem anderen Bot.
- Kein Live-Judge, kein Hindsight, kein Konto, keine Signatur, kein Auftrag auf Mainnet oder Testnet.
- Kein Hebel, kein Cross-/Isolated-Margin, keine Liquidation, keine Trigger-Orders, kein Spot.
- Kein automatischer Sammler und kein in diesem Lauf ausgeführter Abruf. Der spätere Befehl ist ausdrücklich `--execute-observation --confirm-public-info` und öffnet keine Exposition.
- Keine menschliche Review-Note. Das Prozessjournal ist mechanisch und bewertet PnL nicht.
- Öffentliches L2 beweist keine Queue. Kerzen enthalten keinen Intrabar-Pfad und kein historisches Oracle.

## Grenzen

Hashes zeigen lokale Wiederholbarkeit, keine unabhängige Herkunft. Wer Entscheidung, Journal, Bericht und Manifest gemeinsam neu schreibt, erzeugt eine neue lokale Geschichte; das wird dadurch nicht authentisch. Ein bestandener Engineering-Test ist kein Strategieergebnis und kein Marktbeleg. `APPROVE` erlaubt nur eine hypothetische Aktion im Prozess. Testnet bleibt der bisherige, freigabepflichtige Weg. Es gibt keine Laufzeitverbindung zum Desk Lead.
