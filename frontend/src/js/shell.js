import "../css/style.css";
import { API, session } from "./api.js";
import { esc, icon } from "./ui.js";

export const LOGO = `<svg viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="7.5" fill="#1b293d" stroke="#6cc4f0" stroke-width="1.6"/>
  <path d="M10 13.5c3 1.2 8 1.2 12 0M9.6 18c3.4 1.4 9.4 1.4 12.8 0" stroke="#6cc4f0" stroke-width="1.1" fill="none" opacity=".7"/>
  <ellipse cx="16" cy="16" rx="14" ry="5.2" transform="rotate(-24 16 16)" fill="none" stroke="#f5b13d" stroke-width="1.3" stroke-dasharray="2 2.4"/>
  <circle cx="28.2" cy="10.6" r="2" fill="#f5b13d"/></svg>`;

const NAV = [
  ["dashboard.html", "Overview", "overview"],
  ["search.html", "Search", "search"],
  ["change.html", "Compare dates", "compare"],
  ["review.html", "Alerts", "alert", "pending"],
  ["discovery.html", "Hotspots", "hotspot"],
  ["ledger.html", "Audit trail", "audit"],
  ["system.html", "Data & security", "shield"],
];

export function mountShell({ title, intro = "", actions = "" }) {
  const s = session.get();
  if (!s) { location.href = "index.html"; throw new Error("no session"); }
  const here = location.pathname.split("/").pop() || "dashboard.html";
  const content = document.getElementById("content");
  const nav = document.createElement("header");
  nav.className = "topnav";
  const initials = esc(s.user.name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase());
  nav.innerHTML = `<div class="topnav-in">
    <a class="brand" href="dashboard.html">${LOGO}<span class="bn">Samvid</span></a>
    <nav class="nav" aria-label="Main">${NAV.map(([h, l, i, c]) => `<a href="${h}" class="${h === here ? "on" : ""}" title="${l}">${icon(i)}<span class="lbl">${l}</span>${c ? `<span class="badge-n hidden" data-count="${c}"></span>` : ""}</a>`).join("")}</nav>
    <a class="net" id="net" href="system.html"><span class="dot"></span><span class="t">Checking…</span></a>
    <div class="me"><button id="me-btn" aria-haspopup="true" aria-expanded="false"><span class="avatar">${s.user.picture ? `<img src="${esc(s.user.picture)}" alt="" referrerpolicy="no-referrer">` : initials}</span></button>
      <div class="menu hidden" id="me-menu"><div class="who"><b>${esc(s.user.name)}</b><div class="muted small">${esc(s.user.email || s.user.username)}</div>
        <div class="faint small">${s.user.role === "supervisor" ? "Supervisor" : "Analyst"} · signed in with ${s.method === "google" ? "Google" : "a local account"}</div></div>
        <button class="btn sm" id="logout" style="width:100%">Sign out</button></div></div>
  </div>`;
  document.body.prepend(nav);
  const main = document.createElement("main");
  main.className = "page"; main.id = "page";
  main.innerHTML = `<div class="page-head"><div><h1>${esc(title)}</h1>${intro ? `<p>${intro}</p>` : ""}</div><div class="row">${actions}</div></div>`;
  nav.after(main);
  if (content) { main.append(...content.childNodes); content.remove(); }
  const mb = nav.querySelector("#me-btn"), mm = nav.querySelector("#me-menu");
  mb.onclick = (e) => { e.stopPropagation(); mm.classList.toggle("hidden"); mb.setAttribute("aria-expanded", String(!mm.classList.contains("hidden"))); };
  document.addEventListener("click", (e) => { if (!mm.contains(e.target)) mm.classList.add("hidden"); });
  nav.querySelector("#logout").onclick = async () => {
    try { await API.logout(); } catch { /* ignore */ }
    if (s.method === "google") { try { (await import("./firebase.js")).firebaseSignOut(); } catch { /* ignore */ } }
    session.clear(); location.href = "index.html";
  };
  refreshStatus();
  const body = document.createElement("div");
  main.append(body);
  return body;
}

export async function refreshStatus() {
  const el = document.getElementById("net");
  if (!el) return null;
  try {
    const o = await API.overview();
    const t = el.querySelector(".t");
    if (!o.offline.guard) { el.className = "net bad"; t.textContent = "Network not protected"; }
    else if (o.offline.blocked) { el.className = "net warn"; t.textContent = `Offline · ${o.offline.blocked} blocked`; }
    else { el.className = "net ok"; t.textContent = "Offline, secure"; }
    el.title = "Only this computer can talk to Samvid. Click for details.";
    const c = document.querySelector('[data-count="pending"]');
    if (c && o.candidates.pending) { c.textContent = o.candidates.pending; c.classList.remove("hidden"); }
    return o;
  } catch { el.className = "net bad"; el.querySelector(".t").textContent = "Server not reachable"; return null; }
}