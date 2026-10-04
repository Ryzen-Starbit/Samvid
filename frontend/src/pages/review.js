import { mountShell, refreshStatus } from "../js/shell.js";
import { API, img } from "../js/api.js";
import { esc, $, toast, pct, fdate, what, sure, story, statusTag, gauge, reasons, lcBar, lcLegend, compareSlider, whenStrip, loading, icon } from "../js/ui.js";

const page = mountShell({ title: "Alerts" });
const qs = new URLSearchParams(location.search);

page.innerHTML = `
<div class="side-main">
  <div class="card sticky"><div class="card-h" style="flex-wrap:wrap;gap:8px">
      <div class="seg" id="st"><button class="on" data-s="pending">To review</button><button data-s="confirmed">Confirmed</button><button data-s="rejected">Dismissed</button></div>
      <select id="aoi" aria-label="Area"><option value="">All areas</option></select></div>
    <div class="alist" id="list" style="max-height:calc(100vh - 210px);overflow:auto"></div></div>
  <div id="detail">${loading()}</div>
</div>`;

let items = [], current = null, status = "pending", aois = [], scenesCache = {};
const aoiName = (id) => aois.find((a) => a.id === id)?.name || id;

async function loadList(selectId) {
  items = await API.candidates(status, $("#aoi").value);
  $("#list").innerHTML = items.map((c) => `
    <button class="aitem ${c.id === current ? "on" : ""}" data-id="${c.id}"><img src="${img(`/api/candidates/${c.id}/img/change.png`)}" alt="" loading="lazy">
      <div><div class="t">${esc(what(c.change_type))}</div><div class="s">${esc(aoiName(c.aoi))}, by ${fdate(c.earliest_date)}</div><div class="s">${sure(c.confidence)}</div></div></button>`).join("")
    || `<div class="empty">${status === "pending" ? "All caught up — nothing waiting for review." : "Nothing here yet."}</div>`;
  $("#list").querySelectorAll(".aitem").forEach((el) => (el.onclick = () => open(el.dataset.id)));
  const pick = selectId || items[0]?.id;
  if (pick) open(pick); else $("#detail").innerHTML = `<div class="card"><div class="empty">Select an alert on the left.</div></div>`;
}

async function open(id) {
  current = id;
  $("#list").querySelectorAll(".aitem").forEach((el) => el.classList.toggle("on", el.dataset.id === id));
  $("#detail").innerHTML = `<div class="card">${loading("Loading evidence…")}</div>`;
  const c = await API.candidate(id);
  if (!scenesCache[c.aoi]) scenesCache[c.aoi] = (await API.scenes(c.aoi)).scenes;
  const m = c.metrics, u = (k) => img(`/api/candidates/${id}/img/${k}.png`);
  const sar = m.sar;
  $("#detail").innerHTML = `
  <div class="stack">
    <div class="card"><div class="card-b">
      <div class="row" style="align-items:flex-start"><div style="flex:1;min-width:260px">
          <div class="row" style="margin-bottom:8px">${statusTag(c.status)}<span class="faint small">${esc(aoiName(c.aoi))}, ${c.centroid[0].toFixed(4)}°N ${c.centroid[1].toFixed(4)}°E</span></div>
          <p class="story">${story(c)}</p></div>
        <div class="row"><a class="btn sm" target="_blank" rel="noopener" href="${API.reportUrl(id)}">View report</a><a class="btn sm primary" href="${API.reportUrl(id, true)}" download>Download PDF</a><a class="btn sm" href="${API.bundleUrl(id)}">Data (JSON)</a></div></div>
      <div class="film" style="margin-top:18px">
        <figure><img src="${u("before")}" alt="before"><figcaption><b>Before</b>${fdate(sceneDate(c.before_scene))}</figcaption></figure>
        <figure><img src="${u("earliest")}" alt="first sign"><figcaption><b>First sign</b>${fdate(c.earliest_date)}</figcaption></figure>
        <figure><img src="${u("after")}" alt="latest"><figcaption><b>Latest</b>${fdate(sceneDate(c.after_scene))}</figcaption></figure>
        <figure><img src="${u("change")}" alt="changed area"><figcaption><b>Changed area</b>highlighted in red</figcaption></figure>
      </div></div></div>

    <div class="grid g2">
      <div class="card"><div class="card-h"><h3>Slide to compare</h3></div><div class="card-b">${compareSlider(u("before"), u("after"), "before", "latest")}</div></div>
      <div class="stack">
        <div class="card"><div class="card-h"><h3>How sure is this?</h3></div><div class="card-b">${gauge(c.confidence, reasons(m))}</div></div>
        <div class="card"><div class="card-h"><h3>Ground at the time of detection</h3></div><div class="card-b stack-s">
          <div class="small muted">Before</div>${lcBar(m.class_before, true)}${lcLegend(m.class_before)}
          <div class="small muted" style="margin-top:8px">When detected</div>${lcBar(m.class_after, true)}${lcLegend(m.class_after)}</div></div>
      </div>
    </div>

    <div class="card"><div class="card-h"><h3>When did it start?</h3><span class="muted small">Seen in ${m.persistence} check${m.persistence > 1 ? "s" : ""}</span></div><div class="card-b"><div id="when"></div></div></div>

    <div class="card" id="decide"><div class="card-h"><h3>Your decision</h3>${c.decided_by ? `<span class="faint small">${c.status === "confirmed" ? "Confirmed" : c.status === "rejected" ? "Dismissed" : "Re-opened"} by ${esc(c.decided_by)}, ${fdate(c.decided_at?.slice(0, 10))}</span>` : ""}</div>
      <div class="card-b"><div class="row"><input class="input" id="note" placeholder="Add a note for the record (optional)" style="flex:1;min-width:220px" value="${esc(c.note || "")}">
        <button class="btn good" id="ok">${icon("check")} Confirm</button><button class="btn danger" id="no">${icon("x")} Dismiss</button>
        ${c.status !== "pending" ? '<button class="btn ghost sm" id="re">Re-open</button>' : ""}</div>
<div id="rr"></div></div></div>

    <details class="more card" style="padding:16px 18px"><summary>Technical details</summary><div class="grid g2">
      <div><h3 style="margin-bottom:10px">Processing steps</h3><ol class="steps">${c.processing.map((p) => `<li><b>${esc(p.step)}</b>${esc(p.detail)}<br><span class="faint">${esc(p.model)}</span></li>`).join("")}</ol></div>
      <div><h3 style="margin-bottom:10px">Audit entries</h3>${c.ledger.map((e) => `<div class="small" style="padding:6px 0;border-bottom:1px solid var(--line)"><b>#${e.seq}</b> ${esc(e.action)} <span class="faint">by ${esc(e.actor)}</span><div class="mono faint tiny" style="word-break:break-all">${e.entry_hash}</div></div>`).join("")}
        <dl class="kv small" style="margin-top:12px"><dt>Signal strength</dt><dd>${m.z_median} × normal variation</dd><dt>Radar</dt><dd>${sar ? `${sar.median_delta_db} dB change` : "no pair"}</dd><dt>Area id</dt><dd class="mono">${c.id}</dd></dl></div></div></details>
  </div>`;
  whenStrip($("#when"), c.search, scenesCache[c.aoi]);
  const decide = async (d) => {
    try {
      const r = await API.decide(id, d, $("#note").value);
      toast(d === "confirmed" ? "Confirmed and saved to the audit trail" : d === "rejected" ? "Dismissed and saved to the audit trail" : "Re-opened", d === "rejected" ? "" : "ok");
      if (r.rerank.top_moves.length) $("#rr").innerHTML = `<p class="faint small" style="margin-top:10px">${r.rerank.changed} alerts re-ranked.</p>`;
      refreshStatus();
      const row = $(`#list .aitem[data-id="${id}"]`);
      if (row && status !== d) { row.classList.add("leaving"); await new Promise((res) => setTimeout(res, 420)); }
      const next = items.find((x) => x.id !== id);
      current = null; await loadList(status === "pending" ? next?.id : id);
    } catch (e) { toast(e.message, "err"); }
  };
  $("#ok").onclick = () => decide("confirmed");
  $("#no").onclick = () => decide("rejected");
  if ($("#re")) $("#re").onclick = () => decide("pending");
}
const sceneDate = (sid) => `${sid.slice(-8, -4)}-${sid.slice(-4, -2)}-${sid.slice(-2)}`;

$("#st").querySelectorAll("button").forEach((b) => (b.onclick = () => {
  status = b.dataset.s; $("#st").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b)); current = null; loadList();
}));
API.aois().then((a) => {
  aois = a;
  $("#aoi").innerHTML += a.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("");
  if (qs.get("aoi")) $("#aoi").value = qs.get("aoi");
  if (qs.get("id")) { status = "pending"; }
  $("#aoi").onchange = () => { current = null; loadList(); };
  loadList(qs.get("id"));
});