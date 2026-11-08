# Lokales Papier — Umsetzungs- und Prüfbericht

Stand: 2026-11-08. Fork `Asunaa-ctrl/hypergrok-trading-desk`, Basis `v1.7.1`, Commit `82bc645899b9581a693463b59553aa1329e021c4` (`git rev-parse HEAD` und `git rev-parse v1.7.1^{}` stimmen überein). Kein Upstream-PR, kein Merge, keine Veröffentlichung, kein Deployment.

Es wurde kein neuer Bot angelegt. Die vorhandene Desk-Lead-Rolle bleibt unverändert. Der bestehende Desk Lead hat das lokale Mandat als Rollenverständnis zur Kenntnis genommen. Das ist keine authentifizierte Verbindung und kein Laufzeitzugriff. Die Kennung `a9a5e3d6-724d-44ab-bb6f-ce99f55ac11b` bleibt eine Zeichenkette. Ein geänderter Urteilstext oder eine geänderte judge_id ohne passenden Provenance-Hash wird nicht ausgeführt.

## Befehle und Ergebnisse

Nach der Prüfung auf gefälschte Urteile, Zustandsrennen, Ledger, Lookahead, Manifest-Tampering und die Anzeige:

```text
python3 scripts/test_paper_sim.py
# Ran 21 tests — OK

python3 scripts/test_paper_dashboard.py
# Ran 4 tests — OK

bash scripts/check.sh
# ok: 17 skills, 7 agents, 68 markdown files
# Passed 18 read-only CLI journeys
```

Neue Abhängigkeiten: keine. Die 21 Simulator-Tests schließen jetzt ein: geändertes Urteil ohne Provenance-Hash führt nicht aus; geänderte judge_id wird abgelehnt; ein späteres frisches Buch füllt nicht zum veralteten Preis; eine Finanzierung nach der gebundenen Version blockiert die wartende Order; Manifest, Ledger und Journal, die nach dem Schreiben geändert werden, scheitern bei `--verify-run`; Replay öffnet keinen Socket.

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
- Keine Live-Datensammlung. Die öffentliche `/info`-Allowlist hat keinen mitgelieferten Socket.
- Keine menschliche Review-Note. Das Prozessjournal ist mechanisch und bewertet PnL nicht.
- Öffentliches L2 beweist keine Queue. Kerzen enthalten keinen Intrabar-Pfad und kein historisches Oracle.

## Grenzen

Hashes zeigen lokale Wiederholbarkeit, keine unabhängige Herkunft. Wer Entscheidung, Journal, Bericht und Manifest gemeinsam neu schreibt, erzeugt eine neue lokale Geschichte; das wird dadurch nicht authentisch. Ein bestandener Engineering-Test ist kein Strategieergebnis und kein Marktbeleg. `APPROVE` erlaubt nur eine hypothetische Aktion im Prozess. Testnet bleibt der bisherige, freigabepflichtige Weg. Es gibt keine Laufzeitverbindung zum Desk Lead.
