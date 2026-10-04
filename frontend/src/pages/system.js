import { mountShell, refreshStatus } from "../js/shell.js";
import { API } from "../js/api.js";
import { esc, $, toast, num, pct, fdate, icon, loading } from "../js/ui.js";

const page = mountShell({ title: "Data & security" });

page.innerHTML = `
<div class="grid g2">
  <div class="card"><div class="card-h"><h2>Network</h2><button class="btn sm" id="st">Test it</button></div><div class="card-b" id="off">${loading()}</div></div>
  <div class="card"><div class="card-h"><h2>Load new images</h2><button class="btn sm primary" id="ing">Load waiting images</button></div><div class="card-b" id="inc">${loading()}</div></div>
</div>
<div class="card" style="margin-top:18px"><div class="card-h"><h2>Validation</h2><span class="faint small">Reference set with known changes</span></div><div class="card-b" id="evs">${loading()}</div></div>
<div class="card" style="margin-top:18px"><div class="card-h"><h2>What Samvid runs on</h2></div><div class="scroll"><table class="t"><thead><tr><th>Part</th><th>What it is</th><th>Licence</th><th>Status</th><th>Used for</th></tr></thead><tbody id="man"></tbody></table></div></div>`;

const shield = (ok) => `<svg viewBox="0 0 48 48" style="width:54px;height:54px;flex-shrink:0"><path d="M24 4 8 10v12c0 9.6 6.8 17.6 16 20 9.2-2.4 16-10.4 16-20V10L24 4z" fill="${ok ? "rgba(93,203,138,.14)" : "rgba(239,111,106,.14)"}" stroke="${ok ? "var(--ok)" : "var(--bad)"}" stroke-width="2"/>
  <path d="${ok ? "m17 24 5 5 9-10" : "M19 19l10 10M29 19 19 29"}" fill="none" stroke="${ok ? "var(--ok)" : "var(--bad)"}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

async function offline() {
  const o = await API.offline();
  const fb = o.allowed_hosts.length > 0;
  $("#off").innerHTML = `<div class="row" style="align-items:flex-start;gap:16px">${shield(o.guard_installed)}<div style="flex:1">
      <h3>${o.guard_installed ? (fb ? "Locked down, with Google sign-in" : "Fully offline") : "Not protected"}</h3>
      <p class="muted small" style="margin-top:4px">${fb ? "Local connections only, plus Google sign-in verification." : "Local connections only."}</p></div></div>
    <dl class="kv" style="margin-top:16px"><dt>Blocked attempts</dt><dd>${o.blocked_attempts}${o.blocked.length ? ` — last: ${esc(o.blocked[o.blocked.length - 1].host)}` : ""}</dd>
      ${fb ? `<dt>Allowed calls</dt><dd>${o.allowed_external.length} to ${esc(o.allowed_hosts.join(", "))}</dd>` : ""}
      <dt>Maps & fonts</dt><dd>Bundled with the app, no online map tiles</dd></dl>
`;
  $("#man").innerHTML = o.manifest.map((m) => `<tr><td><b>${esc(m.component)}</b></td><td>${esc(m.name)} <span class="faint">${esc(m.version || "")}</span></td><td class="small">${esc(m.license)}</td>
    <td>${m.staged === true ? '<span class="tag ok">Installed</span>' : m.staged === false ? '<span class="tag">Not installed</span>' : '<span class="tag info">Optional</span>'}</td><td class="small muted">${esc(m.role)}</td></tr>`).join("");
}

async function incoming() {
  const keep = $("#ires")?.innerHTML || "";     
  const r = await API.incoming();
  $("#inc").innerHTML = `<p class="muted small">Folder: <span class="mono">backend/data/incoming/&lt;area&gt;/</span></p>
    <div class="row" style="margin-top:14px;gap:22px"><div><div class="num" style="font-size:26px" id="vec">${num(r.index.vectors)}</div><div class="faint small">places searchable</div></div>
      <div><div class="num" style="font-size:26px">${r.files.length}</div><div class="faint small">image${r.files.length === 1 ? "" : "s"} waiting</div></div></div>
    ${r.files.length ? `<ul class="small" style="margin:12px 0 0;padding-left:18px">${r.files.map((f) => `<li>${esc(f.aoi)}: ${esc(f.name)}</li>`).join("")}</ul>` : '<p class="faint small" style="margin-top:12px">Nothing waiting.</p>'}<div id="ires">${keep}</div>`;
  $("#ing").disabled = !r.files.length;
}

async function evaluation() {
  const e = await API.evaluation();
  if (!e.per_event) { $("#evs").innerHTML = '<p class="muted">Run <span class="mono">python -m scripts.evaluate</span> to fill this in.</p>'; return; }
  const s = e.summary;
  $("#evs").innerHTML = `<div class="row" style="gap:28px;margin-bottom:16px">
      <div><div class="num" style="font-size:26px">${s.events_detected}/${s.events}</div><div class="faint small">known changes found</div></div>
      <div><div class="num" style="font-size:26px">${s.queue_true_positives}/${s.queue_candidates}</div><div class="faint small">alerts that were real</div></div>
      <div><div class="num" style="font-size:26px">${s.earliest_abs_error_vs_true_onset_months_mean} mo</div><div class="faint small">average start-date error</div></div>
      <div><div class="num" style="font-size:26px">${s.binary_search_comparisons} vs ${s.linear_scan_comparisons}</div><div class="faint small">images checked vs checking all</div></div></div>
    <div class="scroll" style="max-height:380px"><table class="t"><thead><tr><th>Known change</th><th>Found?</th><th>Really started</th><th>Samvid says</th><th>Off by</th></tr></thead><tbody>
    ${e.per_event.map((x) => `<tr><td>${esc(x.note)}<div class="faint tiny">${esc(x.aoi)}</div></td><td>${x.detected ? '<span class="tag ok">Found</span>' : '<span class="tag bad">Missed</span>'}</td>
      <td>${fdate(Array.isArray(x.true_start) ? `${x.true_start[0]}-${String(x.true_start[1]).padStart(2, "0")}` : x.true_start)}</td><td>${fdate(x.earliest_found)}</td>
      <td>${x.earliest_error_months == null ? "—" : x.earliest_error_months === 0 ? "same month" : `${Math.abs(x.earliest_error_months)} mo ${x.earliest_error_months < 0 ? "early" : "late"}`}</td></tr>`).join("")}</tbody></table></div>`;
}

$("#st").onclick = async () => {
  try { const r = await API.selftest(); toast(r.outbound_blocked ? "Test passed: the internet request was blocked" : "Warning: an internet request got through", r.outbound_blocked ? "ok" : "err"); offline(); refreshStatus(); }
  catch (e) { toast(e.message, "err"); }
};
$("#ing").onclick = async () => {
  $("#ing").disabled = true; $("#ires").innerHTML = loading("Loading, checking quality and looking for changes…");
  try {
    const r = await API.ingestIncoming();
    $("#ires").innerHTML = r.results.map((x) => `<div class="notice good" style="margin-top:12px">${icon("check")}<div><b>${esc(x.scene_id || x.file)}</b> loaded${x.quality ? `, ${pct(x.quality.usable_fraction)} clear` : ""}.
      ${x.vectors_added ? `${x.vectors_added} places added to search (no rebuild needed).` : ""} ${esc(r.aois_updated.join(", "))} re-checked; total alerts ${r.candidates_before} → ${r.candidates_after}.</div></div>`).join("");
    await incoming();
    const v = $("#vec"); if (v) v.textContent = num(r.index_vectors);
    toast("New images loaded", "ok"); refreshStatus();
  } catch (e) { toast(e.message, "err"); $("#ires").innerHTML = ""; }
};
offline(); incoming(); evaluation();