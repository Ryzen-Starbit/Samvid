import { mountShell } from "../js/shell.js";
import { API, img } from "../js/api.js";
import { esc, $, toast, pct, fdate, icon, lcBar, lcLegend, loading } from "../js/ui.js";
import { sceneMap } from "../js/map.js";

const page = mountShell({ title: "Search" });
const EXAMPLES = [
  "newly built structures near a river",
  "large vehicle concentrations on open ground",
  "expanding water at the reservoir sector",
  "new roads in the highland sector since 2025-01",
  "cleared vegetation in 2025",
];
const CONCEPT = { built: "buildings", new_built: "newly built", near_water: "near water", water: "water", vehicles: "vehicles",
  open_ground: "open ground", vegetation: "vegetation", road: "roads", clearance: "cleared vegetation", water_change: "water change", large: "large", recent: "recent change" };

page.innerHTML = `
<form class="searchbar" id="qf">${icon("search", "lead")}<input id="q" placeholder="e.g. newly built structures near a river" autocomplete="off" aria-label="Search the archive">
  <label class="btn" title="Find areas that look like an image you upload">${icon("image")} By image<input type="file" id="chip" accept=".tif,.tiff" hidden></label>
  <button class="btn primary" type="submit">Search</button></form>
<div class="row" style="margin-top:12px"><span class="faint small">Try:</span>${EXAMPLES.map((e) => `<button class="chip" data-q="${esc(e)}">${esc(e)}</button>`).join("")}</div>
<details class="more" style="margin-top:14px"><summary>Narrow by area, dates or sensor</summary><div class="filters">
  <label class="f">Area<select id="f-aoi"><option value="">All areas</option></select></label>
  <label class="f">From<select id="f-from"></select></label>
  <label class="f">To<select id="f-to"></select></label>
  <label class="f">Sensor<select id="f-sensor"><option value="">Optical (normal photo)</option><option value="S1">Radar (sees through cloud)</option></select></label>
  <button class="btn sm ghost" type="button" id="clear">Clear filters</button></div>
  <p class="small muted" id="f-note" style="margin-top:8px"></p></details>
<div class="main-side" style="margin-top:20px">
  <div id="res"><div class="card"><div class="empty">${icon("search", "")}<p style="margin-top:6px">No search yet.</p></div></div></div>
  <aside class="stack sticky" id="side">
    <div class="card"><div class="card-h"><h3>Colour key</h3></div><div class="card-b">${lcLegend({ water: .2, veg: .2, built: .2, bare: .2, road: .1, disturbed: .1, vehicle: .1 })}</div></div>
  </aside>
</div>`;

let aois = [];
const sceneDates = {};          
async function initFilters() {
  aois = await API.aois();
  $("#f-aoi").innerHTML += aois.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("");
  await Promise.all(aois.map(async (a) => {
    sceneDates[a.id] = (await API.scenes(a.id)).scenes.filter((x) => x.status === "ok").map((x) => ({ date: x.acq_date, sensor: x.sensor }));
  }));
  drawDates();
}
function drawDates() {
  const area = $("#f-aoi").value, sensor = $("#f-sensor").value || "S2";
  const list = [...new Set((area ? [area] : Object.keys(sceneDates)).flatMap((a) => (sceneDates[a] || []).filter((x) => x.sensor === sensor).map((x) => x.date)))].sort();
  const keepFrom = $("#f-from").value, keepTo = $("#f-to").value;
  $("#f-from").innerHTML = `<option value="">Earliest</option>${list.map((d) => `<option value="${d}">${fdate(d)}</option>`).join("")}`;
  $("#f-to").innerHTML = `<option value="">Latest</option>${list.map((d) => `<option value="${d}">${fdate(d)}</option>`).join("")}`;
  if (list.includes(keepFrom)) $("#f-from").value = keepFrom;
  if (list.includes(keepTo)) $("#f-to").value = keepTo;
  $("#f-note").textContent = list.length
    ? `${list.length} ${sensor === "S1" ? "radar" : "optical"} image dates available${area ? ` for ${aoiName(area)}` : " across all areas"}, ${fdate(list[0])} to ${fdate(list[list.length - 1])}. Only these dates can be chosen.`
    : "No images for this selection.";
}
initFilters();
$("#f-aoi").addEventListener("change", drawDates);
$("#f-sensor").addEventListener("change", drawDates);
$("#f-from").addEventListener("change", () => { if ($("#f-to").value && $("#f-from").value > $("#f-to").value) $("#f-to").value = $("#f-from").value; });
$("#f-to").addEventListener("change", () => { if ($("#f-from").value && $("#f-to").value < $("#f-from").value) $("#f-from").value = $("#f-to").value; });
const aoiName = (id) => aois.find((a) => a.id === id)?.name || id;

function filters() {
  const f = {};
  if ($("#f-aoi").value) f.aoi = [$("#f-aoi").value];
  if ($("#f-from").value) f.date_from = $("#f-from").value;
  if ($("#f-to").value) f.date_to = $("#f-to").value;
  if ($("#f-sensor").value) f.sensor = $("#f-sensor").value;
  return f;
}
$("#clear").onclick = () => { ["#f-aoi", "#f-from", "#f-to", "#f-sensor"].forEach((s) => ($(s).value = "")); drawDates(); };

function understood(r) {
  const p = r.plan; if (!p) return "";
  const cs = Object.keys(p.concepts || {});
  const f = filters();
  return `<div class="card"><div class="card-h"><h3>Interpreted as</h3></div><div class="card-b stack-s">
    <div class="row">${cs.map((c) => `<span class="tag info">${esc(CONCEPT[c] || c)}</span>`).join("") || '<span class="muted small">No specific features — ranking by look alone.</span>'}</div>
    <div class="small muted">${(f.aoi || p.aoi || []).length ? `In ${esc((f.aoi || p.aoi).map(aoiName).join(", "))}` : "In all areas"}${(f.date_from || p.date_from) ? `, from ${fdate(f.date_from || p.date_from)}` : ""}${(f.date_to || p.date_to) ? ` to ${fdate(f.date_to || p.date_to)}` : ""}.</div>
    <details class="more"><summary>How it searched</summary><div><ol class="steps">${r.trace.map((s) => `<li><b>${esc(s.step)}</b>${esc(s.detail || "")}</li>`).join("")}</ol></div></details></div></div>`;
}

function render(r, title) {
  const weak = r.weak;
  $("#res").innerHTML = `
    <div class="row" style="margin-bottom:12px"><h2>${esc(title)}</h2><span class="sp"></span><span class="muted small">${r.results.length} places, best match first</span></div>
    ${weak ? `<div class="notice warn" style="margin-bottom:14px">${icon("info")}<div><b>No strong matches.</b> Showing the closest places.
      ${filters().date_from || filters().date_to ? "Try a wider date range — the thing you're looking for may not be visible in this window." : "Try different words or a wider area."}</div></div>` : ""}
    <div class="tiles">${r.results.map((o, i) => `
      <button class="tile" data-i="${i}"><div class="ph"><img src="${img(o.thumb)}" alt="" loading="lazy"><span class="rank">${i + 1}</span></div>
        <div class="b"><div class="t"><span>${esc(aoiName(o.aoi))}</span><span class="faint small">${fdate(o.date)}</span></div>
          <div style="margin:8px 0 6px">${lcBar(o.fractions)}</div>
          <div class="row small" style="gap:8px"><span class="faint">Match</span><div class="meter" style="flex:1"><i style="width:${Math.round(o.score * 100)}%"></i></div></div>
          <div class="why">${esc(o.why[0] || "")}</div></div></button>`).join("") || '<div class="empty">Nothing found. Clear the filters and try again.</div>'}</div>`;
  $("#res").querySelectorAll(".tile").forEach((el) => (el.onclick = () => {
    $("#res").querySelectorAll(".tile").forEach((x) => x.classList.toggle("on", x === el));
    detail(r.results[+el.dataset.i], r);
  }));
  $("#side").innerHTML = understood(r);
}

let map;
function detail(o, r) {
  const a = aois.find((x) => x.id === o.aoi);
  $("#side").innerHTML = `
    <div class="card"><div class="card-h"><h3>${esc(aoiName(o.aoi))}</h3><span class="faint small">${fdate(o.date)}</span></div><div class="card-b stack-s">
      <div id="dmap" class="map" style="height:260px"></div>
      <div>${lcBar(o.fractions, true)}<div style="margin-top:6px">${lcLegend(o.fractions)}</div></div>
      <ul class="small" style="margin:4px 0 0;padding-left:18px">${o.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>
      <div class="row" style="margin-top:6px"><button class="btn sm primary" id="sim">Find similar places</button>
        <a class="btn sm" href="change.html?aoi=${o.aoi}&tile=${encodeURIComponent(o.tile_id)}">See how it changed</a>
        <a class="btn sm" href="discovery.html?obs=${o.obs_id}">Flag this site</a></div></div></div>
    ${understood(r)}`;
  if (map) map.map.remove();
  if (a && o.bounds) { map = sceneMap($("#dmap"), a, o.scene_id); map.box(o.bounds, { color: "#f5b13d", weight: 3 }); }
  $("#sim").onclick = async () => {
    $("#res").innerHTML = loading("Looking for places that look like this…");
    try { const r2 = await API.searchImage(o.obs_id, filters()); render({ ...r2, plan: null }, `Places that look like ${aoiName(o.aoi)}, ${fdate(o.date)}`); }
    catch (e) { toast(e.message, "err"); }
  };
}

async function run(q) {
  $("#q").value = q;
  $("#res").innerHTML = `<div class="card">${loading("Scanning the archive…")}</div>`;
  try { render(await API.searchText(q, filters()), `“${q}”`); }
  catch (e) { $("#res").innerHTML = `<div class="notice bad">Search failed: ${esc(e.message)}</div>`; }
}
$("#qf").onsubmit = (e) => { e.preventDefault(); if ($("#q").value.trim()) run($("#q").value.trim()); };
page.querySelectorAll(".chip").forEach((c) => (c.onclick = () => run(c.dataset.q)));
$("#chip").onchange = async (e) => {
  const f = e.target.files[0]; if (!f) return;
  $("#res").innerHTML = `<div class="card">${loading("Reading your image…")}</div>`;
  try { const r = await API.searchChip(f); if (r.error) throw new Error(r.error); render({ ...r, plan: null }, `Places that look like ${f.name}`); }
  catch (x) { $("#res").innerHTML = `<div class="notice bad">${esc(x.message)}</div>`; }
};
const qp = new URLSearchParams(location.search).get("q");
if (qp) run(qp);