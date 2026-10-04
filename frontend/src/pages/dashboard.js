import { mountShell } from "../js/shell.js";
import { API, img } from "../js/api.js";
import { esc, num, pct, fdate, sure, what, story, donut, barChart, countUp, loading, TYPE_COLOR, reduced } from "../js/ui.js";

const page = mountShell({ title: "Overview",
  actions: '<a class="btn primary" href="review.html">Review alerts</a>' });

async function main() {
  page.innerHTML = loading("Loading the latest images…");
  const [o, pending, led] = await Promise.all([API.overview(), API.candidates("pending"), API.ledger(40)]);
  const recent = led.entries.filter((e) => ACT[e.action]).slice(0, 6);
  const latest = {};
  await Promise.all(o.aois.map(async (a) => {
    const s = await API.scenes(a.id);
    const ok = s.scenes.filter((x) => x.sensor === "S2" && x.status === "ok" && x.usable_frac > 0.9);
    latest[a.id] = ok[ok.length - 1];
  }));
  const byAoi = {}, byType = {};
  pending.forEach((c) => { byAoi[c.aoi] = (byAoi[c.aoi] || 0) + 1; byType[c.change_type] = (byType[c.change_type] || 0) + 1; });
  const hot = o.hotspots[0];

  page.innerHTML = `
  <section class="pass" id="pass" aria-label="Latest image of each area">
    <div class="pass-head">Latest images</div>
    <div class="pass-strip">${o.aois.map((a) => `
      <a class="pass-cell" href="review.html?aoi=${a.id}" title="Open alerts for ${esc(a.name)}">
        ${latest[a.id] ? `<img src="${img(`/api/scenes/${latest[a.id].id}/image.png`)}" alt="${esc(a.name)}">` : ""}
        <span class="alerts ${byAoi[a.id] ? "" : "zero"}">${byAoi[a.id] ? `${byAoi[a.id]} alert${byAoi[a.id] > 1 ? "s" : ""}` : "No alerts"}</span>
        <div class="cap"><b>${esc(a.name)}</b><span class="muted small">${latest[a.id] ? `Imaged ${fdate(latest[a.id].acq_date)}` : ""}</span></div>
      </a>`).join("")}</div>
    <div class="scanline"></div>
  </section>

  <div class="kpis" style="margin-top:18px">
    <div class="card kpi"><div class="l">Alerts waiting for review</div><div class="v" data-count="${pending.length}">0</div><div class="d">${o.candidates.confirmed || 0} confirmed, ${o.candidates.rejected || 0} dismissed so far</div></div>
    <div class="card kpi"><div class="l">Hotspots growing faster</div><div class="v" data-count="${o.hotspots.length}">0</div><div class="d">in ${new Set(o.hotspots.flatMap((h) => h.summary.aois)).size} areas</div></div>
    <div class="card kpi"><div class="l">Images in the archive</div><div class="v" data-count="${o.scenes.n}">0</div><div class="d">${o.scenes.s2} optical, ${o.scenes.s1} radar</div></div>
    <div class="card kpi"><div class="l">Newest image</div><div class="v" style="font-size:24px;padding-top:6px">${fdate(o.scenes.last)}</div><div class="d">archive starts ${fdate(o.scenes.first)}</div></div>
  </div>

  <div class="main-side" style="margin-top:18px">
    <div class="card"><div class="card-h"><h2>Alerts waiting for you</h2><a href="review.html" class="small">See all ${pending.length}</a></div>
      <div class="alist">${pending.slice(0, 7).map((c) => `
        <a class="aitem" href="review.html?id=${c.id}"><img src="${img(`/api/candidates/${c.id}/img/change.png`)}" alt="" loading="lazy">
          <div><div class="t">${esc(what(c.change_type))}</div><div class="s">${esc(aoiName(o, c.aoi))}, started by ${fdate(c.earliest_date)}</div>
          <div class="s">${sure(c.confidence)} <span style="margin-left:6px">${pct(c.confidence)} sure</span></div></div></a>`).join("") || '<div class="empty">Nothing to review. New alerts appear here after each ingest.</div>'}</div></div>
    <div class="stack">
      <div class="card"><div class="card-h"><h3>What kind of changes</h3></div><div class="card-b">
        ${donut(Object.entries(byType).sort((a, b) => b[1] - a[1]).map(([t, v]) => ({ label: what(t), value: v, color: TYPE_COLOR[t] || "#70839c" })))}</div></div>
      ${hot ? `<div class="card"><div class="card-h"><h3>Fastest-growing hotspot</h3><a class="small" href="discovery.html">All hotspots</a></div><div class="card-b">
        <div><b>${esc(hot.label.replace(/^./, (x) => x.toUpperCase()))}</b> <span class="muted small">— ${hot.size} similar sites in ${esc(hot.summary.aois.join(", "))}</span></div>
        <p class="muted small" style="margin:4px 0 8px">Mostly ${esc(what(hot.summary.dominant_change || "").toLowerCase())}. Changed area per quarter:</p><div id="hot"></div></div></div>` : ""}
      <div class="card"><div class="card-h"><h3>Recent activity</h3><a class="small" href="ledger.html">Audit trail</a></div>
        <div class="alist">${recent.map((e) => `<div class="aitem" style="grid-template-columns:1fr;cursor:default"><div><div class="t" style="font-weight:500">${esc(ACT[e.action] || e.action)}</div>
          <div class="s">${esc(e.actor.startsWith("system:") ? "Samvid" : e.actor)}, ${fdate(e.ts.slice(0, 10))} ${e.ts.slice(11, 16)}</div></div></div>`).join("")}</div></div>
    </div>
  </div>`;

  page.querySelectorAll("[data-count]").forEach((el) => countUp(el, +el.dataset.count));
  const pass = document.getElementById("pass");
  requestAnimationFrame(() => pass.classList.add("run"));
  setTimeout(() => pass.classList.add("done"), reduced() ? 0 : 1700);
  if (hot) barChart(document.getElementById("hot"), hot.trend.map((t) => ({ label: t.period.slice(2).replace("-", " "), value: t.changed_km2, title: t.period.replace("-", " ") })), { height: 130 });
}
const ACT = { "analyst.decision": "Alert reviewed", "ingest.scene": "New image loaded", "model.monitoring_run": "Area re-checked",
  "model.retrieval": "Archive searched", "model.change_analysis": "Dates compared", "analyst.flag_site": "Site flagged", "report.export": "Evidence report exported",
};
const aoiName = (o, id) => o.aois.find((a) => a.id === id)?.name || id;
main().catch((e) => { page.innerHTML = `<div class="notice bad">Couldn't load the overview: ${esc(e.message)}. Check that the server is running.</div>`; });