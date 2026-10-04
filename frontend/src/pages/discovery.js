import { mountShell } from "../js/shell.js";
import { API, img } from "../js/api.js";
import { esc, $, toast, pct, fdate, what, sure, barChart, loading, lcBar, TYPE_COLOR } from "../js/ui.js";

const page = mountShell({ title: "Hotspots" });
const qs = new URLSearchParams(location.search);

page.innerHTML = `<div id="flag"></div>
<h2 style="margin:4px 0 12px">Growing faster</h2><div class="grid g3" id="hot">${loading()}</div>
<div class="side-main" style="margin-top:26px">
  <div class="card"><div class="card-h"><h3>All groups of similar places</h3><span class="faint small" id="n"></span></div><div id="list" class="alist"></div></div>
  <div id="detail"></div>
</div>`;

const cap = (s) => s.replace(/^./, (x) => x.toUpperCase());
const label = (c) => `${cap(c.label)}${c.summary.dominant_change ? `, mostly ${what(c.summary.dominant_change).toLowerCase()}` : ""}`;
const growth = (c) => (c.summary.rate_ratio_late_vs_early ? `${c.summary.rate_ratio_late_vs_early}× faster than a year ago` : "new activity this year");
let clusters = [];

async function load() {
  clusters = await API.clusters();
  const hot = clusters.filter((c) => c.hotspot);
  $("#hot").innerHTML = hot.map((c) => `<button class="card" data-id="${c.id}" style="text-align:left;cursor:pointer;padding:0;background:var(--s1)">
      <div class="card-b"><div class="row"><span class="tag change">Hotspot</span><span class="faint small">${c.size} places</span></div>
        <h3 style="margin:10px 0 2px">${esc(label(c))}</h3><p class="muted small">${esc(growth(c))}, in ${esc(c.summary.aois.join(", "))}</p>
        <div data-chart="${c.id}" style="margin-top:10px"></div></div></button>`).join("") || '<div class="card"><div class="empty">No group is speeding up right now.</div></div>';
  hot.forEach((c) => barChart(page.querySelector(`[data-chart="${c.id}"]`), c.trend.map((t) => ({ label: t.period.slice(2).replace("-", " "), value: t.changed_km2, title: t.period.replace("-", " ") })), { height: 110 }));
  $("#hot").querySelectorAll("[data-id]").forEach((b) => (b.onclick = () => openCluster(+b.dataset.id, true)));
  $("#n").textContent = `${clusters.length} groups`;
  $("#list").innerHTML = clusters.map((c) => `<button class="aitem" data-id="${c.id}" style="grid-template-columns:1fr">
      <div><div class="row"><span class="t">${esc(cap(c.label))}</span><span class="sp"></span>${c.hotspot ? '<span class="tag change">Hotspot</span>' : ""}</div>
      <div class="s">${c.size} places${c.summary.dominant_change ? `, mostly ${esc(what(c.summary.dominant_change).toLowerCase())}` : ", little change"}</div>
      <div style="margin-top:6px">${lcBar(Object.fromEntries(Object.entries(c.profile).filter(([k]) => k.startsWith("f_")).map(([k, v]) => [k.slice(2), v])))}</div></div></button>`).join("");
  $("#list").querySelectorAll(".aitem").forEach((b) => (b.onclick = () => openCluster(+b.dataset.id)));
}

async function openCluster(id, scroll) {
  $("#list").querySelectorAll(".aitem").forEach((el) => el.classList.toggle("on", +el.dataset.id === id));
  $("#detail").innerHTML = `<div class="card">${loading()}</div>`;
  const c = await API.cluster(id);
  $("#detail").innerHTML = `<div class="stack">
    <div class="card"><div class="card-h"><div><h2>${esc(label(c))}</h2><p class="muted small" style="margin-top:4px">${c.size} places in ${esc(c.summary.aois.join(", "))}${c.hotspot ? `, ${esc(growth(c))}` : ""}</p></div>${c.hotspot ? '<span class="tag change">Hotspot</span>' : ""}</div>
      <div class="card-b"><div class="small muted" style="margin-bottom:6px">Changed area per quarter, km²</div><div id="trend"></div></div></div>
    <div class="card"><div class="card-h"><h3>Places in this group</h3></div>
      <div class="card-b"><div class="tiles" style="grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:10px">${c.tiles.map((t) => `
        <button class="tile" data-obs="${t.obs_id}" title="${esc(t.tile_id)}, ${fdate(t.date)}"><div class="ph"><img src="${img(t.thumb)}" alt="" loading="lazy"></div></button>`).join("")}</div></div></div>
    <div class="card"><div class="card-h"><h3>Alerts in this group</h3></div><div class="alist">${c.candidates.map((x) => `
      <a class="aitem" href="review.html?id=${x.id}"><img src="${img(`/api/candidates/${x.id}/img/change.png`)}" alt="" loading="lazy">
        <div><div class="t">${esc(what(x.change_type))}</div><div class="s">${esc(x.aoi)}, by ${fdate(x.earliest_date)}</div><div class="s">${sure(x.confidence)}</div></div></a>`).join("") || '<div class="empty">No alerts in this group.</div>'}</div></div>
  </div>`;
  barChart($("#trend"), c.trend.map((t) => ({ label: t.period.slice(2).replace("-", " "), value: t.changed_km2, title: t.period.replace("-", " ") })));
  $("#detail").querySelectorAll("[data-obs]").forEach((b) => (b.onclick = () => flag(b.dataset.obs)));
  if (scroll) $("#detail").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function flag(obs) {
  history.replaceState(null, "", `?obs=${obs}`);
  $("#flag").innerHTML = `<div class="card" style="margin-bottom:22px">${loading("Finding places that look like this one…")}</div>`;
  window.scrollTo({ top: 0, behavior: "smooth" });
  try {
    const r = await API.similar(obs);
    $("#flag").innerHTML = `<div class="card" style="margin-bottom:22px"><div class="card-h"><div><h2>Places that look like the one you flagged</h2>
</div>
        <button class="btn sm" id="closeflag">Close</button></div>
      <div class="card-b"><div class="tiles" style="grid-template-columns:repeat(auto-fill,minmax(120px,1fr))">
        <div class="tile on"><div class="ph"><img src="${img(`/api/tiles/${obs}/thumb.png`)}" alt=""></div><div class="b small"><b>Flagged</b><div class="faint">${esc(r.source.tile_id)}</div></div></div>
        ${r.similar.map((s) => `<button class="tile" data-obs="${s.obs_id}"><div class="ph"><img src="${img(s.thumb)}" alt="" loading="lazy"></div>
          <div class="b small"><div class="row" style="gap:6px"><span class="faint">${esc(s.aoi)}</span></div><div class="meter" style="margin-top:6px"><i style="width:${Math.round(s.similarity * 100)}%"></i></div><div class="faint tiny" style="margin-top:3px">${pct(s.similarity)} alike</div></div></button>`).join("")}</div></div></div>`;
    $("#closeflag").onclick = () => { $("#flag").innerHTML = ""; history.replaceState(null, "", "discovery.html"); };
    $("#flag").querySelectorAll("button[data-obs]").forEach((b) => (b.onclick = () => flag(b.dataset.obs)));
    if (r.cluster) openCluster(r.cluster.id);
  } catch (e) { toast(e.message, "err"); $("#flag").innerHTML = ""; }
}

load().then(() => {
  if (qs.get("obs")) flag(qs.get("obs"));
  else { const h = clusters.find((c) => c.hotspot) || clusters[0]; if (h) openCluster(h.id); }
});