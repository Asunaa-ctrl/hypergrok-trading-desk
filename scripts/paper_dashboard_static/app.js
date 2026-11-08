"use strict";

const filter = document.querySelector("#record-filter");
document.querySelector("#refresh").addEventListener("click", load);
filter.addEventListener("change", () => { if (window.latestStatus) render(window.latestStatus); });

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function money(value, currency) {
  if (value == null || value === "") return "nicht verfügbar";
  return String(value) + (currency ? " " + currency : "");
}

function definition(pairs) {
  const list = document.createElement("dl");
  for (const [term, value] of pairs) {
    list.append(el("dt", null, term), el("dd", null, value == null || value === "" ? "nicht verfügbar" : String(value)));
  }
  return list;
}

function unavailable(text) {
  return el("p", "empty", text);
}

function lineChart(points, currency) {
  const usable = (points || []).filter((point) => point && point.equity != null && point.gap_before !== true);
  if (!usable.length) return unavailable("Keine aufgezeichnete Papierkurve. Nicht verfügbar.");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "chart");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Equity in " + (currency || "Quote") + ", nur aufgezeichnete Punkte, Lücken nicht verbunden");
  const values = usable.map((point) => Number(point.equity));
  if (values.some((value) => !Number.isFinite(value))) return unavailable("Equity-Punkte sind nicht numerisch. Keine Kurve.");
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  let segment = [];
  const segments = [];
  (points || []).forEach((point, index) => {
    if (!point || point.equity == null || point.gap_before === true) {
      if (segment.length) segments.push(segment);
      segment = point && point.equity != null && point.gap_before === true ? [index] : [];
      return;
    }
    segment.push(index);
  });
  if (segment.length) segments.push(segment);
  const flat = (points || []).filter((point) => point && point.equity != null);
  segments.forEach((indexes) => {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    const coords = indexes.map((index) => {
      const point = points[index];
      const x = (flat.indexOf(point) / Math.max(flat.length - 1, 1)) * 100;
      const y = 100 - ((Number(point.equity) - min) / span) * 100;
      return x + "," + y;
    }).join(" ");
    line.setAttribute("points", coords);
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "#e0a15a");
    line.setAttribute("stroke-width", "1.5");
    line.setAttribute("vector-effect", "non-scaling-stroke");
    svg.append(line);
  });
  svg.setAttribute("viewBox", "0 0 100 100");
  svg.setAttribute("preserveAspectRatio", "none");
  const wrap = el("div");
  wrap.append(svg, el("p", "empty", "Achse: Equity " + (currency || "Quote") + ". Slippage ist nicht in der Equity enthalten."));
  return wrap;
}

function candleChart(candles) {
  const rows = (candles || []).filter((row) => row && row.gap !== true);
  if (!rows.length) return unavailable("Keine erfassten Kerzen. Es wird keine Kursreihe erfunden.");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "chart");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Kerzen aus erfassten OHLC, Lücken bleiben offen, Stand " + (rows[0].as_of || "ohne Zeitstempel"));
  const highs = rows.map((row) => Number(row.h));
  const lows = rows.map((row) => Number(row.l));
  if (highs.concat(lows).some((value) => !Number.isFinite(value))) return unavailable("Kerzen sind nicht numerisch. Keine Kursreihe.");
  const min = Math.min(...lows);
  const max = Math.max(...highs);
  const span = max - min || 1;
  (candles || []).forEach((row, index) => {
    if (!row || row.gap === true) return;
    const x = ((index + 0.5) / candles.length) * 100;
    const y = (price) => 100 - ((Number(price) - min) / span) * 100;
    const wick = document.createElementNS("http://www.w3.org/2000/svg", "line");
    wick.setAttribute("x1", x); wick.setAttribute("x2", x);
    wick.setAttribute("y1", y(row.h)); wick.setAttribute("y2", y(row.l));
    wick.setAttribute("stroke", "#a8a396");
    wick.setAttribute("vector-effect", "non-scaling-stroke");
    const body = document.createElementNS("http://www.w3.org/2000/svg", "line");
    body.setAttribute("x1", x); body.setAttribute("x2", x);
    body.setAttribute("y1", y(row.o)); body.setAttribute("y2", y(row.c));
    body.setAttribute("stroke", Number(row.c) >= Number(row.o) ? "#8fbf9f" : "#e08b8b");
    body.setAttribute("stroke-width", "3");
    body.setAttribute("vector-effect", "non-scaling-stroke");
    svg.append(wick, body);
  });
  svg.setAttribute("viewBox", "0 0 100 100");
  svg.setAttribute("preserveAspectRatio", "none");
  const wrap = el("div");
  const hash = rows[0].sha256 ? " Hash " + rows[0].sha256 + " ist lokal und nicht extern beglaubigt." : "";
  wrap.append(svg, el("p", "empty", "Quelle: " + (rows[0].source || "Beobachtung") + ". Stand: " + (rows[0].as_of || "nicht verfügbar") + "." + hash));
  return wrap;
}

function bookLadder(book) {
  if (!book) return unavailable("Kein beobachtetes Orderbuch. Historische Tiefe wird nicht rekonstruiert.");
  const table = document.createElement("table");
  const head = document.createElement("tr");
  ["Seite", "Preis", "Größe"].forEach((label) => head.append(el("th", null, label)));
  table.append(head);
  (book.asks || []).slice(0, 8).forEach((level) => table.append(row("Ask", level)));
  (book.bids || []).slice(0, 8).forEach((level) => table.append(row("Bid", level)));
  const wrap = el("div");
  if (book.stale) wrap.append(el("p", "stale", book.stale_reason_de || "Veraltet."));
  const hash = book.sha256 ? " Hash " + book.sha256 + " ist lokal und nicht extern beglaubigt." : "";
  wrap.append(table, el("p", "empty", "Quelle: " + (book.source || "Beobachtung") + ". Stand: " + (book.as_of || book.time || "nicht verfügbar") + "." + hash));
  return wrap;
}

function row(side, level) {
  const line = document.createElement("tr");
  line.append(el("td", null, side), el("td", null, String(level.px)), el("td", null, String(level.sz)));
  return line;
}

function render(status) {
  document.querySelector("#disclaimer").textContent = status.disclaimer_de;
  document.querySelector("#judge-note").textContent = status.judge_note_de;
  document.querySelector("#read-at").textContent = "Zuletzt gelesen " + status.read_at_utc + " UTC. Kein Dauerbetrieb.";
  const collector = status.market.collector;
  document.querySelector("#collector").replaceChildren(el("h2", null, "Sammler"), definition([
    ["Zustand", collector.state],
    ["Gesund", "nein"],
    ["Zuletzt gesehen", collector.last_seen_utc],
    ["Hinweis", collector.note_de],
  ]));
  const template = status.template || {};
  const handoff = status.handoff || {};
  const budgetAmount = template.present ? "100.00" : (handoff.budget && handoff.budget.amount);
  const budgetCurrency = template.present ? "EUR" : (handoff.budget && handoff.budget.currency);
  document.querySelector("#budget").replaceChildren(el("h2", null, "Budget"), definition([
    ["Startbudget", template.present ? "100.00 EUR" : money(budgetAmount, budgetCurrency)],
    ["Nutzbares USDC-Quote", template.present ? null : handoff.usable_quote],
    ["EUR/USD", template.present ? "Quelle benannt, Kurs nicht belegt" : (handoff.fx_status || "nicht verfügbar")],
    ["USDC/USD", template.present ? "nicht verfügbar, keine Parität" : "nicht verfügbar"],
    ["Strategie", template.present ? "erforderlich, nicht ausgewählt" : (handoff.strategy_status || "nicht ausgewählt")],
    ["Exit", template.present ? "erforderlich, nicht ausgewählt" : "nicht ausgewählt"],
    ["Kampagne", template.present ? "nicht gestartet, maximal 3 Tage oder 5 Sim-Trades" : "keine"],
  ]));
  document.querySelector("#quality").replaceChildren(el("h2", null, "Qualität"), definition([
    ["Vorlage bereit", template.present ? "nein" : "keine Vorlage"],
    ["Training", "nicht gelaufen"],
    ["Native Entscheidung", "ausstehend"],
    ["Beobachtung", template.observation_market || "keine"],
  ]));
  const market = status.market || {};
  const candles = document.querySelector("#candles");
  candles.replaceChildren(el("h2", null, "Kurs"));
  if (market.test_harness) candles.append(el("p", "test-banner", market.test_label));
  if (market.provenance_note_de) candles.append(el("p", "empty", market.provenance_note_de));
  candles.append(candleChart(market.candles));
  if (market.gap_count) candles.append(el("p", "empty", "Lücken: " + market.gap_count + ". Die Reihe wird darüber nicht verbunden."));
  if (market.missing_candles) candles.append(el("p", "empty", "Unvollständige Kerzen ausgelassen: " + market.missing_candles + "."));
  const book = document.querySelector("#book");
  book.replaceChildren(el("h2", null, "Orderbuch"));
  if (market.test_harness) book.append(el("p", "test-banner", market.test_label));
  book.append(bookLadder(market.book));
  const paper = document.querySelector("#pnl");
  paper.replaceChildren(el("h2", null, "Virtuelles Ergebnis"));
  if (!status.paper.started) {
    paper.append(el("p", null, status.paper.message_de));
    paper.lastChild.id = "paper-empty";
  } else {
    status.paper.runs.forEach((run) => {
      const ledger = run.ledger || {};
      const pnl = run.pnl_breakdown || {};
      paper.append(definition([
        ["Budget", money(run.budget && run.budget.amount, run.budget && run.budget.currency)],
        ["Quote-Cash", money(ledger.cash, ledger.currency)],
        ["Equity", money(ledger.equity, ledger.currency)],
        ["Realisiert", ledger.realized],
        ["Unrealisiert", ledger.unrealized],
        ["Netto Quote", pnl.net_quote],
        ["Brutto Quote", pnl.gross_quote],
        ["Gebühren", pnl.fees],
        ["Finanzierung", pnl.funding],
        ["Slippage, nicht in Equity", pnl.slippage_quote],
        ["FX-Neubewertung EUR", pnl.fx_revaluation_eur],
        ["Drawdown", run.drawdown],
      ]));
      if (run.chart && run.chart.eligible) {
        paper.append(el("p", "empty", "Equity, Netto und Drawdown aus dem Lauf. Lokaler Hash, keine externe Beglaubigung."));
        paper.append(lineChart(run.chart.points, ledger.currency));
        if (run.chart.gaps) paper.append(el("p", "empty", "Lücken in der Equity: " + run.chart.gaps + ". Die Linie wird darüber nicht verbunden."));
      } else paper.append(unavailable(run.chart && run.chart.reason_de || "Keine echte Papierkurve."));
    });
  }
  const orders = document.querySelector("#orders");
  orders.replaceChildren(el("h2", null, "Orders und Entscheidungen"));
  const visible = status.paper.started ? status.paper.runs : [];
  if (!visible.length) orders.append(unavailable("Keine aufgezeichnete Order."));
  visible.forEach((run) => {
    (run.orders_detail || []).forEach((order) => {
      orders.append(definition([
        ["Status", order.status],
        ["Seite", order.side || ""],
        ["Entscheidung", order.recorded_verdict],
        ["Grund", order.reason_de || order.reason],
      ]));
    });
    (run.decisions || []).forEach((item) => {
      orders.append(definition([
        ["Art", item.kind],
        ["Aufgezeichnet", item.recorded_verdict],
        ["Ausgeführt", item.action_taken ? "ja, simuliert" : "nein"],
        ["Grund", item.reason_de],
      ]));
    });
  });
  const position = document.querySelector("#position");
  position.replaceChildren(el("h2", null, "Position"));
  if (!visible.length) position.append(unavailable("Keine Position."));
  visible.forEach((run) => position.append(definition([
    ["Basis", run.ledger && run.ledger.position],
    ["Durchschnitt", run.ledger && run.ledger.average_price],
    ["Mark", run.ledger && run.ledger.mark],
  ])));
  document.querySelector("#journal").replaceChildren(el("h2", null, "Journal"), unavailable(visible.length ? "Entscheidungen stehen bei den Orders. Ein menschliches Review ist nicht gelaufen." : "Kein Journal."));
  const engineering = document.querySelector("#engineering-body");
  engineering.replaceChildren();
  const counts = status.engineering.counts;
  engineering.append(el("p", null, "Aufgezeichnet: " + counts.recorded + ". Bestanden: " + counts.passed + ". Fehlgeschlagen: " + counts.failed + ". Fehler: " + counts.errors + "."));
  const wanted = filter.value;
  const records = status.engineering.records.filter((record) => wanted === "all" || record.status === wanted);
  engineering.append(el("p", null, "Angezeigt " + records.length + " von " + counts.recorded + " aufgezeichneten Protokollen."));
  const fixtureMarket = status.engineering.fixture_market || {candles: [], book: null};
  if (fixtureMarket.candles.length || fixtureMarket.book) {
    engineering.append(el("p", null, "SYNTHETISCH. Diese Kerzen und das Buch sind kein Markterfassung und werden nicht als Papierkurs gezeichnet."));
    if (fixtureMarket.candles.length) engineering.append(candleChart(fixtureMarket.candles));
    if (fixtureMarket.book) engineering.append(bookLadder(fixtureMarket.book));
  }
  if (status.engineering.synthetic_runs.length) {
    engineering.append(el("p", null, "SYNTHETISCH. Fixture-Equity ist keine Papierkurve."));
    status.engineering.synthetic_runs.forEach((run) => {
      engineering.append(definition([
        ["Lauf", run.id],
        ["Quote-Cash", run.ledger && run.ledger.cash],
        ["Position", run.ledger && run.ledger.position],
        ["Netto", run.pnl_breakdown && run.pnl_breakdown.net_quote],
        ["Drawdown", run.drawdown],
      ]));
      if (run.fixture_series && run.fixture_series.length) engineering.append(lineChart(run.fixture_series, "USDC Fixture"));
    });
  }
}

async function load() {
  const response = await fetch("/api/status", {cache: "no-store"});
  if (!response.ok) {
    document.querySelector("#read-at").textContent = "Statusdatei nicht lesbar.";
    return;
  }
  window.latestStatus = await response.json();
  render(window.latestStatus);
}

load();
if (new URLSearchParams(location.search).get("once") !== "1") setInterval(load, 5000);
