"use strict";

const filter = document.querySelector("#record-filter");
const refreshButton = document.querySelector("#refresh");
let latest = null;

filter.addEventListener("change", () => { if (latest) render(latest); });
refreshButton.addEventListener("click", () => { load(); });

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function badge(className, text) {
  return el("span", "badge " + className, text);
}

function fundingLabel(funding) {
  if (!funding || funding.status === "unavailable" || funding.claimed == null) return "nicht verfügbar";
  if (funding.status === "not_due") return "nicht fällig";
  return String(funding.claimed);
}

function definition(pairs) {
  const list = document.createElement("dl");
  for (const [term, value] of pairs) {
    list.append(el("dt", null, term), el("dd", null, value == null || value === "" ? "nicht verfügbar" : String(value)));
  }
  return list;
}

function decisionList(decisions) {
  const box = el("div");
  if (!decisions.length) {
    box.append(el("p", null, "Keine aufgezeichnete Entscheidung."));
    return box;
  }
  for (const item of decisions) {
    box.append(definition([
      ["Art", item.kind || "unbekannt"],
      ["Aufgezeichnet", item.recorded_verdict || "keine"],
      ["Ausgeführt", item.action_taken ? "ja, simuliert" : "nein"],
      ["Grund", item.reason_de || item.reason_code || "nicht verfügbar"],
      ["Erklärung", item.explanation || ""],
    ]));
  }
  return box;
}

function equityTable(points) {
  if (!points.length) return el("p", null, "Keine Equity-Punkte im Bericht.");
  const wrap = el("div", "wrap");
  const table = document.createElement("table");
  const head = document.createElement("tr");
  head.append(el("th", null, "Ereigniszeit (ms)"), el("th", null, "Simulierte Equity"));
  table.append(head);
  for (const point of points) {
    const row = document.createElement("tr");
    row.append(el("td", null, String(point.time_ms)), el("td", null, String(point.equity)));
    table.append(row);
  }
  wrap.append(table);
  return wrap;
}

function renderRun(run) {
  const card = el("article");
  const title = el("h3", null, run.id || "Lauf");
  card.append(title);
  const flags = el("p");
  flags.append(badge("badge-sim", "Simuliert"));
  if (run.evidence_class === "synthetic_engineering_fixture") {
    flags.append(badge("badge-synthetic", "Synthetisches Fixture — keine Marktdaten"));
  }
  if (!run.claims_market_evidence) flags.append(badge("badge-synthetic", "Kein Marktnachweis"));
  flags.append(badge(run.accepted_experiment ? "badge-approve" : "badge-reject", run.accepted_experiment ? "Ledger stimmt; kein Strategieergebnis" : "Ledger oder Finanzierung unvollständig"));
  card.append(flags);
  if (!run.readable) {
    card.append(el("p", null, "Dieser Ordner konnte nicht gelesen werden."));
    return card;
  }
  const ledger = run.ledger || {};
  card.append(definition([
    ["Startguthaben", ledger.starting_cash],
    ["Virtuelles Cash", ledger.cash],
    ["Virtuelle Equity", ledger.equity],
    ["Position", ledger.position],
    ["Gebühren", run.fees],
    ["Finanzierung", fundingLabel(run.funding)],
    ["Slippage", run.slippage && run.slippage.amount != null ? run.slippage.amount : "nicht verfügbar"],
    ["Drawdown", run.drawdown],
    ["Netz", run.source_network],
    ["Konfiguration", run.identifiers && run.identifiers.config_sha256],
    ["Code", run.identifiers && run.identifiers.code_sha256],
    ["Daten", run.identifiers && run.identifiers.data_sha256],
    ["Journalkopf", run.identifiers && run.identifiers.journal_head],
    ["Datei beobachtet", run.file_observed_at_utc],
  ]));
  if (run.errors && run.errors.length) {
    const list = document.createElement("ul");
    for (const error of run.errors) list.append(el("li", null, error.detail_de || error.code));
    card.append(el("h4", null, "Lücken und Fehler"), list);
  } else {
    card.append(el("p", null, "Keine aufgezeichneten Datenfehler in diesem Bericht."));
  }
  card.append(el("h4", null, "Lokale Entscheidungen"), decisionList(run.decisions || []));
  card.append(el("h4", null, "Equity aus dem Journal"), equityTable(run.equity_points || []));
  return card;
}

function render(status) {
  document.querySelector("#disclaimer").textContent = status.disclaimer_de;
  document.querySelector("#judge-note").textContent = status.judge_note_de;
  document.querySelector("#read-at").textContent = "Anzeige gelesen um " + status.read_at_utc + " UTC. Das ist die Lesezeit, kein Marktzeitpunkt.";
  const engineering = document.querySelector("#engineering-body");
  engineering.replaceChildren();
  const counts = status.engineering.counts;
  const list = document.createElement("ul");
  list.className = "counts";
  for (const [label, value] of [["Aufgezeichnet", counts.recorded], ["Bestanden", counts.passed], ["Fehlgeschlagen", counts.failed], ["Fehler", counts.errors]]) {
    list.append(el("li", null, label + ": " + value));
  }
  engineering.append(list);
  const wanted = filter.value;
  const records = status.engineering.records.filter((record) => wanted === "all" || record.status === wanted);
  engineering.append(el("p", null, "Angezeigt " + records.length + " von " + counts.recorded + " aufgezeichneten Protokollen."));
  if (!records.length) {
    engineering.append(el("p", null, "Keine technischen Testprotokolle für diesen Filter."));
  } else {
    const table = document.createElement("table");
    const head = document.createElement("tr");
    for (const label of ["Protokoll", "Status", "Beleg"]) head.append(el("th", null, label));
    table.append(head);
    for (const record of records) {
      const row = document.createElement("tr");
      row.append(el("td", null, record.name || "ohne Name"), el("td", null, record.status || "unbekannt"), el("td", null, record.evidence_class === "synthetic_engineering_fixture" ? "Synthetisches Fixture" : (record.evidence_class || "unbekannt")));
      table.append(row);
    }
    const wrap = el("div", "wrap");
    wrap.append(table);
    engineering.append(wrap);
  }
  const conflicts = status.engineering.conflicts || [];
  if (conflicts.length) {
    engineering.append(el("h3", null, "Widersprüchliche Ordner"));
    for (const conflict of conflicts) {
      const card = el("article");
      card.append(el("h3", null, conflict.id || "Ordner"));
      card.append(el("p", null, conflict.note_de || "Widersprüchliche Dateien. Kein Kontostand aus diesem Ordner."));
      const list = document.createElement("ul");
      for (const error of conflict.errors || []) list.append(el("li", null, error.detail_de || error.code));
      card.append(list);
      engineering.append(card);
    }
  }
  if (!status.engineering.synthetic_runs.length) {
    engineering.append(el("p", null, "Kein synthetischer Simulatorlauf in diesem Ordner."));
  } else {
    engineering.append(el("h3", null, "Synthetische Simulatorläufe"));
    engineering.append(el("p", null, "Erfundenes Fixture. Kein Paper-Durchlauf und kein erfasstes Marktzeugnis."));
    for (const run of status.engineering.synthetic_runs) engineering.append(renderRun(run));
  }
  const paper = document.querySelector("#paper-body");
  paper.replaceChildren();
  if (!status.paper.started) {
    paper.append(el("p", null, status.paper.message_de));
    paper.firstChild.id = "paper-empty";
    return;
  }
  paper.append(el("p", null, "Auch ein als Capture bezeichneter Lauf ist eine Simulation. Fehlender Marktnachweis bleibt fehlender Marktnachweis."));
  for (const run of status.paper.runs) paper.append(renderRun(run));
}

async function load() {
  const response = await fetch("/api/status", { cache: "no-store" });
  if (!response.ok) {
    document.querySelector("#read-at").textContent = "Die Statusdatei konnte nicht gelesen werden.";
    return;
  }
  latest = await response.json();
  render(latest);
}

load();
if (new URLSearchParams(location.search).get("once") !== "1") setInterval(load, 5000);
