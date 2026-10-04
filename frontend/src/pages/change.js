import { mountShell } from "../js/shell.js";
import { API, img } from "../js/api.js";
import { esc, $, toast, pct, num, fdate, fmonth, what, sure, lcBar, lcLegend, compareSlider, whenStrip, greennessChart, loading, icon } from "../js/ui.js";

const page = mountShell({ title: "Compare dates" });
const qs = new URLSearchParams(location.search);

page.innerHTML = `
<div class="card"><div class="card-b stack-s">
  <div class="filters">
    <label class="f">Area<select id="aoi"></select></label>
    <div class="seg" id="slot" role="group" aria-label="Which date are you picking"><button class="on" data-s="a">Pick “before”</button><button data-s="b">Pick “after”</button></div>
    <label class="f">Before<select id="sa"></select></label>
    <label class="f">After<select id="sb"></select></label>
    <span class="sp"></span>
    <span id="tilechip"></span>
    <button class="btn primary" id="run">Compare</button>
  </div>
  <div class="reel" id="reel" aria-label="All images of this area — click to pick dates"></div>
  <div class="legend"><span><i style="background:var(--act)"></i>clear image</span><span><i style="background:#4a7f9a"></i>partly cloudy</span>
    <span><i style="background:repeating-linear-gradient(45deg,var(--line-2) 0 3px,var(--s1) 3px 6px)"></i>too cloudy or snowy</span>
    <span><i style="background:var(--bad)"></i>rejected by quality check</span><span><i style="background:#8b7fd6"></i>radar</span></div>
  <p class="small muted" id="limits"></p>
  <div id="preview"></div>
</div></div>
<div id="out" style="margin-top:18px"></div>
<div id="tilepanel" style="margin-top:18px"></div>`;

const MIN_CLEAR = 0.5;
const usable = (x) => x.sensor === "S2" && x.status === "ok" && x.usable_frac >= MIN_CLEAR;
const why = (x) => x.sensor === "S1" ? "radar image, not used for date comparison" : x.status === "held" ? `rejected by quality check (${x.status_reason})`
  : x.usable_frac < MIN_CLEAR ? `only ${pct(x.usable_frac)} clear — too cloudy to compare` : "";
let scenes = [], pick = { a: null, b: null }, slot = "a", tile = qs.get("tile") || "";
function drawTileChip() {
  $("#tilechip").innerHTML = tile ? `<span class="tag info">Focus: tile ${esc(tile.split(":").slice(1).join(","))} <button class="btn ghost sm" id="untile" aria-label="Remove focus" style="padding:0 2px">${icon("x")}</button></span>` : "";
  if (tile) $("#untile").onclick = () => { tile = ""; drawTileChip(); $("#tilepanel").innerHTML = ""; };
}

function drawReel() {
  let lastY = "";
  $("#reel").innerHTML = scenes.map((x) => {
    const cls = x.sensor === "S1" ? "sar" : x.status === "held" ? "held" : x.usable_frac < 0.4 ? "cloudy" : x.usable_frac < 0.9 ? "part" : "clear";
    const h = x.sensor === "S1" ? 20 : 18 + 34 * Math.max(0.15, x.usable_frac);
    const y = x.acq_date.slice(0, 4); const yl = y !== lastY ? `<span class="yr">${y}</span>` : ""; lastY = y;
    const sel = x.sensor === "S2" && x.acq_date === pick.a ? "sel-a" : x.sensor === "S2" && x.acq_date === pick.b ? "sel-b" : "";
    const ok = usable(x);
    return `<button class="${cls} ${sel} ${ok ? "" : "off"}" style="height:${h}px" data-d="${x.acq_date}" ${x.sensor === "S1" ? "disabled" : ""} aria-disabled="${!ok}"
      title="${fdate(x.acq_date)} — ${ok ? `${pct(x.usable_frac)} clear${x.haze ? ", haze removed" : ""}` : `can't be selected: ${why(x)}`}">${yl}</button>`;
  }).join("");
  $("#reel").style.paddingBottom = "24px";
  $("#reel").querySelectorAll("button[data-d]").forEach((b) => (b.onclick = () => {
    const x = scenes.find((s) => s.acq_date === b.dataset.d && s.sensor === "S2");
    preview(b.dataset.d);
    if (!usable(x)) { toast(`${fdate(x.acq_date)} can't be compared: ${why(x)}. Preview only.`, "err"); return; }
    setPick(slot, b.dataset.d);
    if (slot === "a") setSlot("b");
  }));
}

function drawSelects() {
  const ds = scenes.filter(usable).map((x) => x.acq_date);
  $("#sa").innerHTML = ds.slice(0, -1).map((d) => `<option value="${d}" ${d === pick.a ? "selected" : ""}>${fdate(d)}</option>`).join("");
  $("#sb").innerHTML = ds.filter((d) => d > pick.a).map((d) => `<option value="${d}" ${d === pick.b ? "selected" : ""}>${fdate(d)}</option>`).join("");
  const opt = scenes.filter((x) => x.sensor === "S2"), bad = opt.length - ds.length;
  $("#limits").innerHTML = `${ds.length} of ${opt.length} optical images can be compared (${fdate(ds[0])} to ${fdate(ds[ds.length - 1])}).${bad ? ` ${bad} ${bad === 1 ? "is" : "are"} greyed out: too cloudy or rejected by quality checks.` : ""} Radar images are used only to confirm changes.`;
}
function setPick(which, d) {
  pick[which] = d;
  if (pick.a && pick.b && pick.a >= pick.b) {
    const ds = scenes.filter(usable).map((x) => x.acq_date);
    if (which === "a") pick.b = ds.find((x) => x > pick.a) || pick.b;
    else { [pick.a, pick.b] = [pick.b, pick.a]; if (pick.a === pick.b) pick.a = ds[ds.indexOf(pick.b) - 1]; }
  }
  drawSelects(); drawReel();
}

function preview(d) {
  const x = scenes.find((s) => s.acq_date === d && s.sensor === "S2"); if (!x) return;
  const reg = Math.hypot(x.reg_dx, x.reg_dy);
  $("#preview").innerHTML = `<div class="row" style="align-items:flex-start;gap:18px;margin-top:6px;padding-top:14px;border-top:1px solid var(--line)">
    <div style="position:relative;width:220px;flex-shrink:0"><img id="pv" src="${img(`/api/scenes/${x.id}/image.png`)}" alt="" style="width:100%;border-radius:8px;image-rendering:pixelated">
      <img id="pvq" class="hidden" src="${img(`/api/scenes/${x.id}/image.png?layer=qa`)}" alt="" style="position:absolute;inset:0;width:100%;border-radius:8px;image-rendering:pixelated"></div>
    <div class="stack-s" style="flex:1;min-width:220px"><div class="row"><b>${fdate(x.acq_date)}</b>${usable(x) ? '<span class="tag ok">Can be compared</span>' : `<span class="tag bad">${x.status === "held" ? "Rejected by quality check" : "Too cloudy to compare"}</span>`}</div>
      <div class="seg" id="pvs"><button class="on" data-l="rgb">Photo</button><button data-l="qa">Clouds &amp; shadows</button><button data-l="cls">Ground types</button></div>
      <ul class="small muted" style="margin:0;padding-left:18px">
        <li>${pct(x.usable_frac)} of the image is clear${x.cloud_frac > 0.01 ? ` (cloud ${pct(x.cloud_frac)}${x.shadow_frac > 0.005 ? `, shadow ${pct(x.shadow_frac)}` : ""})` : ""}${x.snow_frac > 0.01 ? `, snow ${pct(x.snow_frac)}` : ""}</li>
        <li>${x.haze ? "Haze detected and removed" : "No haze"}</li>
        <li>${reg > 0.5 ? `Was shifted by ${reg.toFixed(1)} pixels — lined up automatically` : "Already lined up with older images"}${x.status === "held" ? `; ${esc(x.status_reason)}` : ""}</li>
      </ul><p class="faint tiny">White: cloud, purple: shadow, cyan: snow.</p></div></div>`;
  $("#pvs").querySelectorAll("button").forEach((b) => (b.onclick = () => {
    $("#pvs").querySelectorAll("button").forEach((y) => y.classList.toggle("on", y === b));
    $("#pv").src = img(`/api/scenes/${x.id}/image.png${b.dataset.l === "cls" ? "?layer=cls" : ""}`);
    $("#pvq").classList.toggle("hidden", b.dataset.l !== "qa");
  }));
}
function setSlot(s) { slot = s; $("#slot").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.s === s)); }
$("#slot").querySelectorAll("button").forEach((b) => (b.onclick = () => setSlot(b.dataset.s)));

async function loadAoi() {
  const s = await API.scenes($("#aoi").value);
  scenes = s.scenes;
  const opt = scenes.filter((x) => x.sensor === "S2" && x.status === "ok" && x.usable_frac > 0.9);
  const start = opt.find((x) => x.acq_date >= "2024-07-01") || opt[0];
  pick = { a: start?.acq_date, b: opt[opt.length - 1]?.acq_date };
  setSlot("a"); drawSelects(); drawReel();
}

async function run() {
  if (!pick.a || !pick.b) { toast("Pick a before and an after date first", "err"); return; }
  $("#out").innerHTML = `<div class="card">${loading("Comparing the two images…")}</div>`;
  let r;
  try { r = await API.analyzeChange({ aoi: $("#aoi").value, date_from: pick.a, date_to: pick.b, tile_id: tile || null }); }
  catch (e) { $("#out").innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; return; }
  const objs = r.objects.filter((o) => o.type !== "unclassified").sort((a, b) => b.confidence - a.confidence);
  const hidden = r.objects.length - objs.length;
  const cut = 1 - r.changed_px / Math.max(1, r.naive_changed_px);
  $("#out").innerHTML = `
  <div class="main-side">
    <div class="card"><div class="card-h"><h2>${fdate(r.before.date)} → ${fdate(r.after.date)}</h2>
      <div class="seg" id="view"><button class="on" data-v="cmp">Slide to compare</button><button data-v="seasonal">Highlight changes</button><button data-v="naive">Without season correction</button></div></div>
      <div class="card-b"><div id="viewer" style="max-width:640px;margin:0 auto"></div>
        <p class="small muted" style="margin-top:12px" id="vnote"></p></div></div>
    <div class="card"><div class="card-h"><h3>${objs.length ? `${objs.length} change${objs.length > 1 ? "s" : ""} found` : "No real changes"}</h3></div>
      <div class="card-b small muted" style="border-bottom:1px solid var(--line)">Changed area: <b style="color:var(--text)">${num(r.changed_px)}</b> px (raw difference ${num(r.naive_changed_px)} px).${hidden ? ` ${hidden} weak signal${hidden > 1 ? "s" : ""} not shown.` : ""}</div>
      <div id="objs">${objs.map((o, i) => `<div class="ccard" data-i="${i}" tabindex="0">
        <div class="row"><b>${esc(what(o.type))}</b><span class="sp"></span>${sure(o.confidence)}</div>
        <div class="small muted" style="margin:3px 0 8px">${o.earliest ? `Started by ${fdate(o.earliest.earliest_date)}, ` : ""}${(o.area_px * 100 / 1e4).toFixed(1)} ha</div>
        <div class="tiny faint">before</div>${lcBar(o.class_before)}<div class="tiny faint" style="margin-top:4px">after</div>${lcBar(o.class_after)}</div>`).join("") || '<div class="empty">Nothing changed beyond normal seasonal variation.</div>'}</div></div>
  </div>
  <div id="detail" style="margin-top:18px"></div>`;
  const V = {
    cmp: [() => compareSlider(img(`/api/scenes/${r.before.scene}/image.png`), img(`/api/scenes/${r.after.scene}/image.png`), fdate(r.before.date), fdate(r.after.date)), ""],
    seasonal: [() => `<img src="${img(r.overlays.seasonal)}" style="width:100%;border-radius:10px;image-rendering:pixelated">`, "Red: changed ground."],
    naive: [() => `<img src="${img(r.overlays.naive)}" style="width:100%;border-radius:10px;image-rendering:pixelated">`, "Yellow: raw before/after difference, seasonal effects included."],
  };
  const show = (k) => { $("#viewer").innerHTML = V[k][0](); $("#vnote").textContent = V[k][1]; $("#view").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === k)); };
  $("#view").querySelectorAll("button").forEach((b) => (b.onclick = () => show(b.dataset.v)));
  show("cmp");
  $("#objs").querySelectorAll(".ccard").forEach((el) => {
    const open = () => {
      $("#objs").querySelectorAll(".ccard").forEach((x) => x.classList.toggle("on", x === el));
      const o = objs[+el.dataset.i];
      $("#detail").innerHTML = `<div class="card"><div class="card-h"><h3>${esc(what(o.type))} — when did it start?</h3></div><div class="card-b">
        <div id="when"></div>
        <div class="grid g2" style="margin-top:16px">
          <div><div class="small muted" style="margin-bottom:6px">Ground before</div>${lcBar(o.class_before, true)}<div style="margin-top:6px">${lcLegend(o.class_before)}</div></div>
          <div><div class="small muted" style="margin-bottom:6px">Ground after</div>${lcBar(o.class_after, true)}<div style="margin-top:6px">${lcLegend(o.class_after)}</div></div></div>
        <p class="small muted" style="margin-top:14px">${o.sar ? (o.sar.corroborated ? "Radar images show the same change, which cloud can't fake." : "Radar images don't show a clear change here.") : "No radar image close enough to these dates."}
          Greenness went from ${o.seasonal.ndvi_before.observed} to ${o.seasonal.ndvi_after.observed}; the season alone would predict ${o.seasonal.ndvi_after.expected}.</p></div></div>`;
      if (o.earliest) whenStrip($("#when"), o.earliest, scenes); else $("#when").innerHTML = "";
      $("#detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
    };
    el.onclick = open; el.onkeydown = (e) => { if (e.key === "Enter") open(); };
  });
  if (tile) loadTile();
}

async function loadTile() {
  try {
    const s = await API.tileSeries(tile);
    $("#tilepanel").innerHTML = `<div class="card"><div class="card-h"><h3>Greenness of the focused tile over time</h3></div><div class="card-b"><div id="gc"></div>
      </div></div>`;
    greennessChart($("#gc"), s);
  } catch (e) { toast(e.message, "err"); }
}

API.aois().then(async (a) => {
  $("#aoi").innerHTML = a.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("");
  if (qs.get("aoi")) $("#aoi").value = qs.get("aoi");
  drawTileChip();
  await loadAoi();
  $("#aoi").onchange = () => { tile = ""; drawTileChip(); $("#tilepanel").innerHTML = ""; loadAoi(); };
  if (qs.get("aoi")) run();
});
$("#run").onclick = run;
$("#sa").onchange = (e) => setPick("a", e.target.value);
$("#sb").onchange = (e) => setPick("b", e.target.value);