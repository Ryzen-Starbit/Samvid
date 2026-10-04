const KEY = "samvid_session";

export const session = {
  get() { try { return JSON.parse(localStorage.getItem(KEY)); } catch { return null; } },
  set(s) { try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* private mode */ } },
  clear() { try { localStorage.removeItem(KEY); } catch { /* ignore */ } },
  token() { return this.get()?.token || ""; },
};

async function req(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.token()}`, ...(opts.headers || {}) },
  });
  if (res.status === 401 && !path.includes("/auth/")) {
    session.clear();
    location.href = "index.html";
    throw new Error("session expired");
  }
  if (!res.ok) {
    let msg = `${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch { /* ignore */ }
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res.text();
}
const post = (p, body) => req(p, { method: "POST", body: JSON.stringify(body || {}) });

export const img = (path) => `${path}${path.includes("?") ? "&" : "?"}t=${encodeURIComponent(session.token())}`;

export const API = {
  login: (username, password) => post("/api/auth/login", { username, password }),
  authConfig: () => fetch("/api/auth/config").then((r) => r.json()),
  loginGoogle: (id_token) => post("/api/auth/firebase", { id_token }),
  logout: () => post("/api/auth/logout"),
  overview: () => req("/api/overview"),
  aois: () => req("/api/aois"),
  scenes: (aoi) => req(`/api/aois/${aoi}/scenes`),
  searchText: (query, filters) => post("/api/search/text", { query, filters }),
  searchImage: (obs_id, filters) => post("/api/search/image", { obs_id, filters }),
  searchChip: async (file) => {
    const fd = new FormData(); fd.append("file", file);
    const r = await fetch("/api/search/chip", { method: "POST", body: fd, headers: { Authorization: `Bearer ${session.token()}` } });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    return r.json();
  },
  tileSeries: (tileId) => req(`/api/tiles/${encodeURIComponent(tileId)}/series`),
  similar: (obsId) => req(`/api/similar/${obsId}`),
  analyzeChange: (body) => post("/api/change/analyze", body),
  candidates: (status = "pending", aoi = "", type = "") =>
    req(`/api/candidates?status=${status}${aoi ? `&aoi=${aoi}` : ""}${type ? `&change_type=${type}` : ""}`),
  candidate: (id) => req(`/api/candidates/${id}`),
  decide: (id, decision, note) => post(`/api/candidates/${id}/decision`, { decision, note }),
  reportUrl: (id) => img(`/api/candidates/${id}/report`),
  bundleUrl: (id) => img(`/api/candidates/${id}/bundle`),
  clusters: () => req("/api/clusters"),
  cluster: (id) => req(`/api/clusters/${id}`),
  ledger: (limit = 100, offset = 0, subject = "", action = "") =>
    req(`/api/ledger?limit=${limit}&offset=${offset}${subject ? `&subject=${encodeURIComponent(subject)}` : ""}${action ? `&action=${action}` : ""}`),
  verify: () => req("/api/ledger/verify"),
  tamperDemo: (seq) => post(`/api/ledger/tamper-demo${seq ? `?seq=${seq}` : ""}`),
  ledgerExportUrl: () => img("/api/ledger/export"),
  offline: () => req("/api/system/offline"),
  selftest: () => post("/api/system/selftest"),
  incoming: () => req("/api/system/incoming"),
  ingestIncoming: () => post("/api/system/ingest-incoming"),
  evaluation: () => req("/api/evaluation"),
};