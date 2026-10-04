import { mountShell } from "../js/shell.js";
import { API } from "../js/api.js";
import { esc, $, toast, num, fdate, icon, loading, reduced } from "../js/ui.js";

const page = mountShell({ title: "Audit trail",
  actions: `<a class="btn" href="${API.ledgerExportUrl()}">${icon("upload")} Export</a>` });

const ACTION = {
  "auth.login": "Signed in", "auth.failed_login": "Failed sign-in", "ingest.scene": "Image loaded", "ingest.rejected": "Image rejected",
  "model.seasonal_baseline": "Season model built", "index.build": "Search index built", "index.incremental_add": "Added to search index",
  "model.change_candidate": "Alert created", "model.monitoring_run": "Area checked for changes", "model.discovery": "Groups & hotspots updated",
  "model.retrieval": "Search", "model.retrieval_image": "Image search", "model.change_analysis": "Dates compared",
  "analyst.decision": "Decision", "analyst.flag_site": "Site flagged", "report.export": "Report exported", "ledger.export": "Audit trail exported",
  "system.setup": "System set up", "system.offline_selftest": "Network test",
};
const nice = (a) => ACTION[a] || a;
const seal = (ok) => `<svg viewBox="0 0 48 48"><circle cx="24" cy="24" r="21" fill="${ok ? "rgba(93,203,138,.15)" : "rgba(239,111,106,.15)"}" stroke="${ok ? "var(--ok)" : "var(--bad)"}" stroke-width="2"/>
  <path d="${ok ? "m15 24 6 6 12-13" : "M17 17l14 14M31 17 17 31"}" fill="none" stroke="${ok ? "var(--ok)" : "var(--bad)"}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

page.innerHTML = `
<div class="grid g2 top">
  <div class="card"><div class="card-b" id="verdict">${loading("Re-checking every record…")}</div></div>
  <div class="card"><div class="card-b"><div class="row"><div style="flex:1"><h3>Tamper test</h3>
      <p class="muted small" style="margin-top:4px">Edits one record in a copy and re-checks it. The real trail isn't touched.</p></div>
      <button class="btn" id="tamper">Run the test</button></div><div id="tres"></div></div></div>
</div>
<div class="card" style="margin-top:18px"><div class="card-h"><h3>Latest records, linked</h3></div><div class="card-b"><div class="chain" id="chain"></div></div></div>
<div class="card" style="margin-top:18px"><div class="card-h" style="flex-wrap:wrap"><h3>All records</h3>
  <div class="row"><input class="input" id="subj" placeholder="Filter by item, e.g. CHG-… or AOI-PLN" style="width:240px"><select id="act"><option value="">Everything</option></select><button class="btn sm" id="go">Filter</button></div></div>
  <div class="scroll"><table class="t"><thead><tr><th>#</th><th>When</th><th>Who</th><th>What happened</th><th>Item</th><th></th></tr></thead><tbody id="rows"></tbody></table></div></div>`;

let latest = [];
async function verdict() {
  const v = await API.verify();
  $("#verdict").innerHTML = `<div class="seal">${seal(v.valid)}<div style="flex:1"><div class="big">${v.valid ? "Nothing has been altered" : `Altered at record #${v.broken_at}`}</div>
    <div class="muted small">${v.valid ? `All ${num(v.checked)} records re-checked just now.` : esc(v.reason)}</div></div><button class="btn sm" id="rev">Check again</button></div>`;
  $("#rev").onclick = verdict;
}
function drawChain(broken) {
  const blocks = [...latest].reverse();
  $("#chain").innerHTML = blocks.map((e, i) => {
    const st = broken == null ? "" : e.seq === broken ? "broken" : e.seq > broken ? "after" : "";
    const ln = i === 0 ? "" : `<div class="link ${broken != null && e.seq > broken ? "broken" : ""}"></div>`;
    return `${ln}<div class="blk ${st}" title="${esc(e.entry_hash)}"><b>#${e.seq}</b>${esc(nice(e.action))}<div class="h">${e.entry_hash.slice(0, 14)}…</div></div>`;
  }).join("");
  $("#chain").scrollLeft = $("#chain").scrollWidth;
}
async function load() {
  const r = await API.ledger(300, 0, $("#subj").value.trim(), $("#act").value);
  if ($("#act").options.length === 1) $("#act").innerHTML += Object.keys(r.stats.by_action).sort().map((a) => `<option value="${a}">${esc(nice(a))}</option>`).join("");
  if (!latest.length) { latest = r.entries.slice(0, 8); drawChain(null); }
  $("#rows").innerHTML = r.entries.map((e) => `<tr>
    <td class="faint">${e.seq}</td><td style="white-space:nowrap">${fdate(e.ts.slice(0, 10))}<div class="faint tiny">${e.ts.slice(11, 19)} UTC</div></td>
    <td>${esc(e.actor.replace("system:", "Samvid · "))}</td><td>${esc(nice(e.action))}</td><td class="small">${esc(e.subject)}</td>
    <td><details class="more"><summary>Details</summary><div><div class="mono tiny faint" style="word-break:break-all">fingerprint ${e.entry_hash}<br>previous ${e.prev_hash}</div><pre class="code">${esc(JSON.stringify(e.payload, null, 1))}</pre></div></details></td></tr>`).join("");
}
$("#tamper").onclick = async () => {
  $("#tres").innerHTML = loading("Editing a copy and re-checking…");
  try {
    const pickSeq = latest.length > 3 ? latest[Math.floor(latest.length / 2)].seq : null;
    const r = await API.tamperDemo(pickSeq);
    if (!reduced()) await new Promise((res) => setTimeout(res, 500));
    drawChain(r.tampered_copy.broken_at);
    $("#tres").innerHTML = `<div class="grid g2" style="margin-top:14px">
      <div class="notice bad">${icon("x")}<div><b>Copy: altered at #${r.tampered_copy.broken_at}</b><br>Every later link breaks.</div></div>
      <div class="notice good">${icon("check")}<div><b>Real trail: intact.</b><br>All ${num(r.real_ledger.checked)} records still match.</div></div></div>
      <p style="margin-top:10px"><button class="btn ghost sm" id="heal">Show the real trail</button></p>`;
    $("#heal").onclick = () => drawChain(null);
  } catch (e) { toast(e.message, "err"); $("#tres").innerHTML = ""; }
};
$("#go").onclick = load;
const sp = new URLSearchParams(location.search).get("subject"); if (sp) $("#subj").value = sp;
verdict(); load();