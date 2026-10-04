export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const pct = (v, d = 0) => `${(v * 100).toFixed(d)}%`;
export const num = (v) => Number(v ?? 0).toLocaleString("en-IN");
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const fdate = (d) => { if (!d) return "—"; const [y, m, dd] = String(d).split("-"); return dd ? `${+dd} ${MON[+m - 1]} ${y}` : `${MON[+m - 1]} ${y}`; };
export const fmonth = (d) => { const [y, m] = String(d).split("-"); return `${MON[+m - 1]} ${y}`; };
export const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
export const ICON = {
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  overview: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  compare: '<rect x="3" y="4" width="8" height="16" rx="1.5"/><rect x="13" y="4" width="8" height="16" rx="1.5"/>',
  alert: '<path d="M12 3 2.5 20h19L12 3z"/><path d="M12 10v4M12 17h.01"/>',
  hotspot: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="7" opacity=".6"/><circle cx="12" cy="12" r="10.5" opacity=".3"/>',
  audit: '<rect x="3" y="9" width="7" height="7" rx="1.5"/><rect x="14" y="9" width="7" height="7" rx="1.5"/><path d="M10 12.5h4"/>',
  shield: '<path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6l-8-3z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  check: '<path d="m5 12 5 5 9-10"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
  play: '<path d="M7 5v14l11-7z"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 17-5-5-8 8"/>',
};
export const icon = (k, cls = "") => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICON[k]}</svg>`;

export function toast(msg, kind = "") {
  let r = document.querySelector(".toasts");
  if (!r) { r = document.createElement("div"); r.className = "toasts"; document.body.append(r); }
  const t = document.createElement("div");
  t.className = `toast ${kind}`; t.textContent = msg; r.append(t);
  setTimeout(() => t.remove(), 4500);
}

export const loading = (msg = "Working…") => `<div class="empty"><div class="radar"></div>${esc(msg)}</div>`;

export function countUp(el, to, ms = 900, fmt = num) {
  if (reduced()) { el.textContent = fmt(to); return; }
  const t0 = performance.now();
  const step = (t) => {
    const k = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - k, 3);
    el.textContent = fmt(Math.round(to * e));
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

export const LC = [
  ["water", "Water", "var(--lc-water)"], ["veg", "Vegetation", "var(--lc-veg)"], ["vegetation", "Vegetation", "var(--lc-veg)"],
  ["built", "Built-up", "var(--lc-built)"], ["bare", "Open ground", "var(--lc-bare)"], ["road", "Roads", "var(--lc-road)"],
  ["disturbed", "Earthworks", "var(--lc-disturbed)"], ["vehicle", "Vehicles", "var(--lc-vehicle)"],
];
const LCMAP = Object.fromEntries(LC.map(([k, l, c]) => [k, { l, c }]));

export function lcBar(fr, big = false) {
  const parts = Object.entries(fr || {}).filter(([k, v]) => LCMAP[k] && v > 0.005).sort((a, b) => b[1] - a[1]);
  return `<div class="lc ${big ? "big" : ""}" role="img" aria-label="${esc(parts.map(([k, v]) => `${LCMAP[k].l} ${pct(v)}`).join(", "))}">${
    parts.map(([k, v]) => `<span style="width:${v * 100}%;background:${LCMAP[k].c}" title="${LCMAP[k].l} ${pct(v)}"></span>`).join("")}</div>`;
}
export function lcLegend(fr) {
  const parts = Object.entries(fr || {}).filter(([k, v]) => LCMAP[k] && v > 0.04).sort((a, b) => b[1] - a[1]);
  return `<div class="lc-legend">${parts.map(([k, v]) => `<span><i style="background:${LCMAP[k].c}"></i>${LCMAP[k].l} ${pct(v)}</span>`).join("")}</div>`;
}

const WHAT = {
  construction: "New construction", site_clearing: "Ground cleared for building", clearance: "Vegetation cleared",
  water_expansion: "Water spreading", water_recession: "Water shrinking", road_development: "New road or track",
  vehicle_concentration: "Vehicles gathered", vehicle_dispersal: "Vehicles left", demolition: "Structures removed",
  vegetation_regrowth: "Vegetation regrowing", unclassified: "Unexplained change",
};
export const what = (t) => WHAT[t] || t;
export const sure = (c) => { const n = Math.max(1, Math.round(c * 5)); return `<span class="sure" title="${pct(c)} sure">${[0, 1, 2, 3, 4].map((i) => `<i class="${i < n ? "f" : ""}"></i>`).join("")}</span>`; };
export const sureWord = (c) => (c >= 0.85 ? "Very likely" : c >= 0.7 ? "Likely" : c >= 0.55 ? "Possible" : "Weak");
export const statusTag = (s) => ({ pending: '<span class="tag change">Needs review</span>', confirmed: '<span class="tag ok">Confirmed</span>', rejected: '<span class="tag bad">Dismissed</span>' }[s] || "");

export function story(c) {
  const ha = (c.area_m2 / 1e4);
  const area = ha >= 1 ? `${ha.toFixed(1)} hectares` : `${Math.round(c.area_m2).toLocaleString("en-IN")} m²`;
  const verb = { construction: "appeared", site_clearing: "started", clearance: "happened", water_expansion: "started", water_recession: "started",
    road_development: "appeared", vehicle_concentration: "showed up", demolition: "happened" }[c.change_type] || "began";
  return `${esc(what(c.change_type))} ${verb} by <em>${fdate(c.earliest_date)}</em>, covering about ${area}.`;
}

export function reasons(m) {
  const sig = Math.min(1, Math.max(0, (m.z_median - 3) / 6));
  const out = [
    { l: "Strength of the change", v: 0.35 + 0.65 * sig, d: m.z_median >= 6 ? "far outside normal seasonal variation" : "clearly outside normal variation" },
    { l: "Image clarity", v: m.quality, d: m.quality > 0.95 ? "no cloud or shadow over the area" : `${pct(m.quality)} of the area was clear` },
    { l: "Image alignment", v: Math.max(0, 1 - (m.registration_error_px || 0) / 1.5), d: `off by ${m.registration_error_px ?? 0} pixels at most` },
  ];
  if (m.sar) out.push({ l: "Radar check", v: m.sar.corroborated ? 0.95 : 0.4, d: m.sar.corroborated ? "radar sees the same change" : "radar is inconclusive" });
  return out;
}

export function gauge(c, rs) {
  const R = 54, L = Math.PI * R, id = `g${Math.random().toString(36).slice(2, 7)}`;
  setTimeout(() => { const a = document.getElementById(id); if (a) a.style.strokeDashoffset = L * (1 - c); }, 60);
  return `<div class="gauge"><svg viewBox="0 0 132 78" role="img" aria-label="${pct(c)} confidence">
      <path d="M12 70 A54 54 0 0 1 120 70" fill="none" stroke="var(--s3)" stroke-width="11" stroke-linecap="round"/>
      <path id="${id}" class="arc" d="M12 70 A54 54 0 0 1 120 70" fill="none" stroke="${c >= 0.85 ? "var(--ok)" : c >= 0.65 ? "var(--change)" : "var(--bad)"}" stroke-width="11" stroke-linecap="round" stroke-dasharray="${L}" stroke-dashoffset="${L}"/>
      <text x="66" y="62" text-anchor="middle" font-family="Archivo" font-weight="700" font-size="24" fill="var(--text)">${Math.round(c * 100)}%</text></svg>
    <div class="reasons"><div><b>${sureWord(c)}</b></div>${(rs || []).map((r) => `
      <div class="r ${r.v < 0.5 ? "low" : r.v < 0.8 ? "mid" : ""}"><span title="${esc(r.d)}">${esc(r.l)}<br><span class="faint tiny">${esc(r.d)}</span></span><div class="meter"><i style="width:${Math.round(r.v * 100)}%"></i></div></div>`).join("")}</div></div>`;
}

export function compareSlider(a, b, la, lb) {
  const id = `c${Math.random().toString(36).slice(2, 8)}`;
  setTimeout(() => {
    const el = document.getElementById(id); if (!el) return;
    const top = el.querySelector(".top"), h = el.querySelector(".handle");
    el.querySelector("input").addEventListener("input", (e) => {
      el.classList.remove("intro"); top.style.clipPath = `inset(0 0 0 ${e.target.value}%)`; h.style.left = `${e.target.value}%`;
    });
  });
  return `<div class="compare intro" id="${id}"><img src="${a}" alt="before"><img class="top" src="${b}" alt="after"><div class="handle"></div>
    <span class="lab" style="left:10px">${esc(la)}</span><span class="lab" style="right:10px">${esc(lb)}</span>
    <input type="range" min="0" max="100" value="50" aria-label="Drag to compare before and after"></div>`;
}

export function whenStrip(el, search, scenes) {
  const skipped = new Set(search.skipped_masked || []);
  const order = new Map(search.probes.map((p, i) => [p.scene, { i, ok: p.supports_change }]));
  const list = scenes.filter((s) => s.sensor === "S2" && s.acq_date > search.window[0] && s.acq_date <= search.window[1]);
  el.innerHTML = `<div class="when"><div class="axis"></div><div class="dots">${list.map((s) => `
    <div class="d ${skipped.has(s.id) ? "cloud" : ""}" data-id="${s.id}" title="${fdate(s.acq_date)}${skipped.has(s.id) ? " — too cloudy to use" : ""}"><span class="m">${fmonth(s.acq_date).replace(" 20", " ’")}</span></div>`).join("")}</div></div>
    <div class="row small"><button class="btn sm" data-replay>${icon("play")} Replay the search</button>
      <span class="muted">${search.comparisons_binary} of ${list.length} images checked</span></div>
    <div class="legend" style="margin-top:10px"><span><i style="background:var(--change)"></i>change visible</span><span><i style="background:var(--s3);border:1px solid var(--muted)"></i>no change yet</span>
      <span><i style="background:repeating-linear-gradient(45deg,var(--line-2) 0 2px,transparent 2px 4px)"></i>too cloudy, skipped</span></div>`;
  const dots = new Map([...el.querySelectorAll(".d")].map((d) => [d.dataset.id, d]));
  const play = async () => {
    dots.forEach((d) => { d.classList.remove("yes", "no", "first", "probe"); d.querySelector(".n")?.remove(); d.querySelector(".flag")?.remove(); });
    for (const p of search.probes) {
      const d = dots.get(p.scene); if (!d) continue;
      if (!reduced()) await new Promise((r) => setTimeout(r, 420));
      d.classList.add(p.supports_change ? "yes" : "no", "probe");
      d.insertAdjacentHTML("beforeend", `<span class="n">${order.get(p.scene).i + 1}</span>`);
    }
    const f = dots.get(search.earliest_scene);
    if (f) { f.classList.add("yes", "first"); f.insertAdjacentHTML("beforeend", `<span class="flag">started</span>`); }
  };
  el.querySelector("[data-replay]").onclick = play;
  play();
}

function tip(container) {
  let t = container.querySelector(".tip");
  if (!t) { t = document.createElement("div"); t.className = "tip hidden"; container.append(t); }
  return t;
}
function hover(c, sel, html) {
  const t = tip(c);
  c.querySelectorAll(sel).forEach((n) => {
    n.addEventListener("mouseenter", () => {
      t.innerHTML = html(n); const b = c.getBoundingClientRect(), r = n.getBoundingClientRect();
      t.style.left = `${r.left - b.left + r.width / 2}px`; t.style.top = `${r.top - b.top}px`; t.classList.remove("hidden");
    });
    n.addEventListener("mouseleave", () => t.classList.add("hidden"));
  });
}

export function barChart(el, data, { height = 170, unit = "km²", recent = 2 } = {}) {
  const W = Math.max(280, Math.round(el.clientWidth || 520)), H = height, L = 40, B = 24, T = 8, R = 4;
  const max = Math.max(...data.map((d) => d.value), 1e-9) * 1.12, bw = (W - L - R) / data.length;
  const y = (v) => T + (H - T - B) * (1 - v / max);
  const ticks = [0, max / 2, max * 0.95];
  el.innerHTML = `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="changed area per quarter">
    ${ticks.map((v) => `<line class="gl" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text class="ax" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v === 0 ? "0" : v < 0.1 ? v.toFixed(3) : v.toFixed(2)}</text>`).join("")}
    ${data.map((d, i) => `<rect class="bar ${i < data.length - recent ? "old" : ""}" style="animation-delay:${i * 60}ms" x="${L + bw * i + 4}" y="${y(d.value)}" width="${Math.max(2, bw - 8)}" height="${Math.max(0, H - B - y(d.value))}" rx="3"/>
      <text class="ax" x="${L + bw * i + bw / 2}" y="${H - 6}" text-anchor="middle">${esc(d.label)}</text>
      <rect class="hit" data-i="${i}" x="${L + bw * i}" y="${T}" width="${bw}" height="${H - T - B}" fill="transparent"/>`).join("")}
  </svg></div>`;
  hover(el.querySelector(".chart"), ".hit", (n) => { const d = data[+n.dataset.i]; return `<b>${esc(d.title || d.label)}</b><br>${d.value.toFixed(3)} ${esc(unit)} changed`; });
}

export function greennessChart(el, series) {
  const obs = series.observations.filter((o) => o.ndvi != null && o.ndvi_expected != null);
  if (!obs.length) { el.innerHTML = '<div class="empty">No clear images for this tile.</div>'; return; }
  const W = Math.max(300, Math.round(el.clientWidth || 560)), H = 220, L = 38, B = 26, T = 10, R = 8;
  const t = (d) => new Date(d).getTime(), t0 = t(obs[0].acq_date), t1 = t(obs[obs.length - 1].acq_date);
  const x = (d) => L + (W - L - R) * ((t(d) - t0) / Math.max(1, t1 - t0));
  const vals = obs.flatMap((o) => [o.ndvi, o.ndvi_expected + 2 * o.ndvi_std, o.ndvi_expected - 2 * o.ndvi_std]);
  const lo = Math.min(...vals) - 0.05, hi = Math.max(...vals) + 0.05, y = (v) => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  const band = obs.map((o) => `${x(o.acq_date)},${y(o.ndvi_expected + 2 * o.ndvi_std)}`).join(" ") + " " + [...obs].reverse().map((o) => `${x(o.acq_date)},${y(o.ndvi_expected - 2 * o.ndvi_std)}`).join(" ");
  const years = [...new Set(obs.map((o) => o.acq_date.slice(0, 4)))].slice(1);
  el.innerHTML = `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="greenness over time versus seasonal expectation">
    <polygon points="${band}" fill="rgba(79,176,109,.16)"/>
    <polyline points="${obs.map((o) => `${x(o.acq_date)},${y(o.ndvi_expected)}`).join(" ")}" fill="none" stroke="var(--lc-veg)" stroke-width="2" stroke-dasharray="5 4" opacity=".8"/>
    ${years.map((yr) => `<line class="gl" x1="${x(`${yr}-01-01`)}" x2="${x(`${yr}-01-01`)}" y1="${T}" y2="${H - B}"/><text class="ax" x="${x(`${yr}-01-01`) + 4}" y="${H - 7}">${yr}</text>`).join("")}
    <text class="ax" x="${L - 6}" y="${y(hi - 0.05) + 4}" text-anchor="end">greener</text><text class="ax" x="${L - 6}" y="${y(lo + 0.05)}" text-anchor="end">bare</text>
    ${obs.map((o, i) => { const out = Math.abs(o.z ?? 0) > 3; return `<circle cx="${x(o.acq_date)}" cy="${y(o.ndvi)}" r="${out ? 5.5 : 4}" fill="${out ? "var(--change)" : "var(--text)"}" stroke="var(--s1)" stroke-width="2"/>
      <circle class="hit" data-i="${i}" cx="${x(o.acq_date)}" cy="${y(o.ndvi)}" r="11" fill="transparent"/>`; }).join("")}
  </svg></div>
  <div class="legend" style="margin-top:8px"><span><i style="background:var(--text);border-radius:50%"></i>measured</span><span><i style="background:var(--lc-veg)"></i>what the season predicts</span>
    <span><i style="background:rgba(79,176,109,.3)"></i>normal range</span><span><i style="background:var(--change);border-radius:50%"></i>outside normal range</span></div>`;
  hover(el.querySelector(".chart"), ".hit", (n) => { const o = obs[+n.dataset.i]; return `<b>${fdate(o.acq_date)}</b><br>measured ${o.ndvi.toFixed(2)}, season predicts ${o.ndvi_expected.toFixed(2)}<br>${Math.abs(o.z) > 3 ? "outside the normal range" : "normal for the season"}`; });
}

export function donut(data) {
  const tot = data.reduce((a, d) => a + d.value, 0) || 1, R = 60, C = 2 * Math.PI * R;
  let acc = 0;
  const segs = data.map((d) => { const len = (d.value / tot) * C; const s = `<circle class="seg-a" r="${R}" cx="75" cy="75" fill="none" stroke="${d.color}" stroke-width="20" stroke-dasharray="${Math.max(0, len - 2)} ${C}" stroke-dashoffset="${-acc}" transform="rotate(-90 75 75)"><title>${esc(d.label)}: ${d.value}</title></circle>`; acc += len; return s; });
  return `<div class="donut"><svg viewBox="0 0 150 150" role="img" aria-label="breakdown">${segs.join("")}<text x="75" y="72" text-anchor="middle" font-family="Archivo" font-weight="700" font-size="28" fill="var(--text)">${tot}</text><text x="75" y="92" text-anchor="middle" font-size="11" fill="var(--faint)">alerts</text></svg>
    <ul>${data.map((d) => `<li><i style="width:10px;height:10px;border-radius:3px;background:${d.color}"></i>${esc(d.label)}<b>${d.value}</b></li>`).join("")}</ul></div>`;
}
export const TYPE_COLOR = { construction: "#e0644a", site_clearing: "#e8913a", clearance: "#4fb06d", water_expansion: "#4a90e2", water_recession: "#7fb2ee",
  road_development: "#a3adbf", vehicle_concentration: "#f2d14b", vehicle_dispersal: "#c9b04a", demolition: "#b05a4a", vegetation_regrowth: "#8fd49f", unclassified: "#70839c" };