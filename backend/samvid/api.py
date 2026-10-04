from __future__ import annotations
import io
import json
import shutil
import uuid
from datetime import date
from pathlib import Path
import numpy as np
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel
from . import offline

offline.install_guard()   

from . import archive, auth, config as cfg, db, discovery, evidence, firebase_auth, ledger, monitor, report, review, search  # noqa: E402
from .change import LABELS, detect_pair, earliest_observation  # noqa: E402

app = FastAPI(title="SAMVID API", version="1.0",
              description="Semantic Analysis and Multimodal Vision for Change Detection - on-prem prototype")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup():
    auth.seed()

def user(request: Request):
    tok = None
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        tok = h[7:]
    tok = tok or request.query_params.get("t")
    u = auth.user_for(tok)
    if not u:
        raise HTTPException(401, "login required")
    return u

class LoginIn(BaseModel):
    username: str
    password: str

@app.post("/api/auth/login")
def login(body: LoginIn):
    r = auth.login(body.username, body.password)
    if not r:
        ledger.append(body.username, "auth.failed_login", body.username, {})
        raise HTTPException(401, "invalid credentials")
    ledger.append(body.username, "auth.login", body.username, {"method": "local password", "role": r["user"]["role"]})
    return r

class FirebaseLoginIn(BaseModel):
    id_token: str

@app.get("/api/auth/config")
def auth_config():
    """Tells the login page which sign-in methods this deployment offers."""
    return {"local": True, "google": cfg.FIREBASE_ENABLED, "firebase_project": cfg.FIREBASE_PROJECT_ID or None}

@app.post("/api/auth/firebase")
def login_firebase(body: FirebaseLoginIn):
    try:
        r = firebase_auth.login_with_token(body.id_token)
    except PermissionError as e:
        ledger.append("unknown", "auth.failed_login", "google", {"method": "google", "reason": str(e)})
        raise HTTPException(403, str(e))
    except firebase_auth.FirebaseAuthError as e:
        ledger.append("unknown", "auth.failed_login", "google", {"method": "google", "reason": str(e)[:200]})
        raise HTTPException(401, str(e))
    ledger.append(r["user"]["username"], "auth.login", r["user"]["username"],
                  {"method": "google (Firebase)", "role": r["user"]["role"], "uid": r["claims"]["uid"]})
    return r

@app.post("/api/auth/logout")
def logout(request: Request, u=Depends(user)):
    auth.logout(request.headers.get("authorization", "")[7:])
    return {"ok": True}

@app.get("/api/auth/me")
def me(u=Depends(user)):
    return u

@app.get("/api/overview")
def overview(u=Depends(user)):
    counts = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM candidates GROUP BY status")}
    by_type = db.q("SELECT change_type, COUNT(*) n FROM candidates GROUP BY change_type ORDER BY n DESC")
    pair_stats = []
    for a in db.q("SELECT id FROM aois"):
        pair_stats += db.kv_get(f"pair_stats:{a['id']}", [])
    ev = cfg.DATA_DIR / "evaluation.json"
    hot = [dict(c, summary=json.loads(c["summary"]), trend=json.loads(c["trend"])) for c in
           db.q("SELECT id, label, size, acceleration, summary, trend FROM clusters WHERE hotspot=1 ORDER BY acceleration DESC")]
    return {
        "aois": [dict(a, bounds=json.loads(a["bounds"] or "null")) for a in db.q("SELECT * FROM aois")],
        "scenes": db.one("SELECT COUNT(*) n, SUM(sensor='S2') s2, SUM(sensor='S1') s1, SUM(status!='ok') held, MIN(acq_date) first, MAX(acq_date) last FROM scenes"),
        "tiles": db.one("SELECT COUNT(*) n FROM tiles")["n"],
        "tile_obs": db.one("SELECT COUNT(*) n FROM tile_obs")["n"],
        "vectors": archive.get_index().ntotal, "index_backend": archive.get_index().backend,
        "candidates": counts, "candidates_by_type": [dict(r, label=LABELS.get(r["change_type"])) for r in by_type],
        "false_alarms": {"naive_objects": sum(p["naive_objects"] for p in pair_stats),
                         "seasonal_objects": sum(p["seasonal_objects"] for p in pair_stats),
                         "suppressed": sum(p["suppressed_false_alarms"] for p in pair_stats),
                         "naive_px": sum(p["naive_changed_px"] for p in pair_stats),
                         "seasonal_px": sum(p["seasonal_changed_px"] for p in pair_stats)},
        "hotspots": hot,
        "ledger": ledger.stats(),
        "offline": {"guard": offline.status()["guard_installed"], "blocked": len(offline.BLOCKED)},
        "evaluation": json.loads(ev.read_text())["summary"] if ev.exists() else None,
        "top_pending": review.queue("pending", limit=5),
    }

@app.get("/api/aois")
def aois(u=Depends(user)):
    return [dict(a, bounds=json.loads(a["bounds"] or "null")) for a in db.q("SELECT * FROM aois")]

@app.get("/api/aois/{aoi}/scenes")
def aoi_scenes(aoi: str, u=Depends(user)):
    rows = db.q("""SELECT id, sensor, acq_date, cloud_frac, shadow_frac, snow_frac, haze, usable_frac, reg_dx, reg_dy,
                          reg_error, status, status_reason, radiometric, sha256 FROM scenes WHERE aoi=? ORDER BY acq_date""", (aoi,))
    for r in rows:
        r["radiometric"] = json.loads(r["radiometric"] or "{}")
    anchors = db.kv_get(f"anchors:{aoi}", [])
    return {"scenes": rows, "anchors": anchors}

@app.get("/api/scenes/{sid}/image.png")
def scene_png(sid: str, layer: str = "rgb", u=Depends(user)):
    return Response(evidence.scene_image(sid, layer), media_type="image/png")

class SearchIn(BaseModel):
    query: str
    filters: dict = {}

@app.post("/api/search/text")
def search_text(body: SearchIn, u=Depends(user)):
    return search.text_search(body.query, body.filters, actor=u["username"])

class ImageSearchIn(BaseModel):
    obs_id: int
    filters: dict = {}
    k: int = 24

@app.post("/api/search/image")
def search_image(body: ImageSearchIn, u=Depends(user)):
    return search.image_search(obs_id=body.obs_id, filters=body.filters, actor=u["username"], k=body.k)

@app.post("/api/search/chip")
async def search_chip(file: UploadFile = File(...), u=Depends(user)):
    p = cfg.DATA_DIR / "uploads"
    p.mkdir(exist_ok=True)
    dest = p / f"chip-{uuid.uuid4().hex[:8]}-{Path(file.filename or 'chip.tif').name}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        return search.image_search(chip_path=dest, actor=u["username"])
    except Exception as e:  
        raise HTTPException(422, f"could not read chip: {e}")

@app.get("/api/tiles/{obs_id}/thumb.png")
def tile_thumb(obs_id: int, u=Depends(user)):
    return Response(evidence.tile_thumb(obs_id), media_type="image/png")

@app.get("/api/tiles/{tile_id}/series")
def tile_series(tile_id: str, u=Depends(user)):
    return evidence.tile_series(tile_id)

@app.get("/api/similar/{obs_id}")
def similar(obs_id: int, k: int = 12, u=Depends(user)):
    o = db.one("SELECT tile_id FROM tile_obs WHERE id=?", (obs_id,))
    t = db.one("SELECT cluster_id FROM tiles WHERE id=?", (o["tile_id"],)) if o else None
    cl = db.one("SELECT * FROM clusters WHERE id=?", (t["cluster_id"],)) if t and t["cluster_id"] >= 0 else None
    res = discovery.similar_sites(obs_id, k)
    ledger.append(u["username"], "analyst.flag_site", o["tile_id"] if o else str(obs_id),
                  {"obs_id": obs_id, "similar": [r["tile_id"] for r in res], "cluster": cl["id"] if cl else None})
    return {"source": o, "cluster": _cluster_out(cl) if cl else None,
            "similar": [{"obs_id": r["id"], "tile_id": r["tile_id"], "aoi": r["aoi"], "date": r["acq_date"],
                         "similarity": r["similarity"], "thumb": f"/api/tiles/{r['id']}/thumb.png"} for r in res]}

class ChangeIn(BaseModel):
    aoi: str
    date_from: str
    date_to: str
    tile_id: str | None = None

def _closest(st, d, after=True):
    target = date.fromisoformat(d)
    best, bd = None, 10 ** 9
    for k, dd in enumerate(st["dates"]):
        dd = date.fromisoformat(str(dd))
        if st["valid"][k].mean() < 0.5:
            continue
        diff = abs((dd - target).days)
        if diff < bd:
            best, bd = k, diff
    return best

@app.post("/api/change/analyze")
def change_analyze(body: ChangeIn, u=Depends(user)):
    st = archive.load_stack(body.aoi)
    i, j = _closest(st, body.date_from), _closest(st, body.date_to)
    if i is None or j is None or i >= j:
        raise HTTPException(422, "need two usable scenes in order inside the window")
    reg = {s["id"]: s["reg_error"] or 0 for s in db.q("SELECT id, reg_error FROM scenes WHERE aoi=?", (body.aoi,))}
    res = detect_pair(body.aoi, i, j, True, reg)
    naive = detect_pair(body.aoi, i, j, False, reg)
    sel = None
    if body.tile_id:
        _, r, c = body.tile_id.split(":")
        sel = (int(c) * cfg.TILE_PX, int(r) * cfg.TILE_PX, (int(c) + 1) * cfg.TILE_PX, (int(r) + 1) * cfg.TILE_PX)
    objs = []
    for o in res["objects"]:
        if sel:
            x0, y0, x1, y1 = o["bbox"]
            if x1 < sel[0] or x0 > sel[2] or y1 < sel[1] or y0 > sel[3]:
                continue
        search_ = earliest_observation(body.aoi, o["mask"], i, j) if o["type"] != "unclassified" else None
        objs.append({k: v for k, v in o.items() if k != "mask"} | {"label": LABELS[o["type"]], "earliest": search_})
    after = np.array(Image.open(cfg.DERIVED_DIR / body.aoi / f"{st['sids'][j]}.png").convert("RGB")).astype(np.float32)
    run_id = uuid.uuid4().hex[:10]
    outs = {}
    for name, mask, col in (("seasonal", res["changed_mask"], (235, 64, 52)), ("naive", naive["changed_mask"], (250, 204, 21))):
        a = after.copy()
        a[mask] = a[mask] * 0.3 + np.array(col) * 0.7
        p = evidence.CACHE / f"adhoc_{run_id}_{name}.png"
        Image.fromarray(a.astype(np.uint8)).save(p)
        outs[name] = f"/api/change/overlay/{run_id}/{name}.png"
    e = ledger.append(u["username"], "model.change_analysis", f"{body.aoi}:{st['sids'][i]}->{st['sids'][j]}", {
        "aoi": body.aoi, "before": str(st["sids"][i]), "after": str(st["sids"][j]), "tile": body.tile_id,
        "objects": [{"type": o["type"], "bbox": o["bbox"], "confidence": o["confidence"]} for o in objs],
        "naive_objects": len(naive["objects"]), "seasonal_objects": len(res["objects"])})
    return {"before": {"scene": str(st["sids"][i]), "date": str(st["dates"][i])},
            "after": {"scene": str(st["sids"][j]), "date": str(st["dates"][j])},
            "valid_fraction": res["valid_fraction"], "changed_px": res["changed_px"], "naive_changed_px": naive["changed_px"],
            "naive_objects": len(naive["objects"]), "objects": objs, "overlays": outs, "ledger_seq": e["seq"]}

@app.get("/api/change/overlay/{run_id}/{name}.png")
def change_overlay(run_id: str, name: str, u=Depends(user)):
    p = evidence.CACHE / f"adhoc_{run_id}_{name}.png"
    if not p.exists() or not run_id.isalnum():
        raise HTTPException(404)
    return FileResponse(p)

@app.get("/api/candidates")
def candidates(status: str = "pending", aoi: str | None = None, change_type: str | None = None, u=Depends(user)):
    return review.queue(status, aoi, change_type)

@app.get("/api/candidates/{cid}")
def candidate(cid: str, u=Depends(user)):
    c = db.one("SELECT * FROM candidates WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404)
    out = review.public(c)
    out["ledger"] = sorted(ledger.entries(limit=100, subject=cid), key=lambda e: e["seq"])
    return out

@app.get("/api/candidates/{cid}/img/{kind}.png")
def candidate_img(cid: str, kind: str, u=Depends(user)):
    if kind not in ("before", "earliest", "after", "change", "before_cls", "after_cls", "quality_after"):
        raise HTTPException(404)
    return Response(evidence.candidate_image(cid, kind), media_type="image/png")

class DecisionIn(BaseModel):
    decision: str
    note: str = ""

@app.post("/api/candidates/{cid}/decision")
def decide(cid: str, body: DecisionIn, u=Depends(user)):
    try:
        return review.decide(cid, body.decision, u["username"], body.note)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))

@app.get("/api/candidates/{cid}/report.pdf")
def candidate_report(cid: str, download: int = 0, u=Depends(user)):
    try:
        pdf = report.candidate_pdf(cid, u["username"])
    except KeyError:
        raise HTTPException(404, "unknown alert")
    disp = "attachment" if download else "inline"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'{disp}; filename="samvid-report-{cid}.pdf"'})

@app.get("/api/candidates/{cid}/bundle")
def candidate_bundle(cid: str, u=Depends(user)):
    return JSONResponse(report.bundle(cid), headers={"Content-Disposition": f"attachment; filename=samvid-{cid}.json"})

def _cluster_out(c):
    return dict(c, members=json.loads(c["members"]), profile=json.loads(c["profile"]), trend=json.loads(c["trend"]),
                summary=json.loads(c["summary"]), hotspot=bool(c["hotspot"]))

@app.get("/api/clusters")
def clusters(u=Depends(user)):
    return [_cluster_out(c) for c in db.q("SELECT * FROM clusters ORDER BY hotspot DESC, acceleration DESC")]

@app.get("/api/clusters/{cid}")
def cluster(cid: int, u=Depends(user)):
    c = db.one("SELECT * FROM clusters WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404)
    out = _cluster_out(c)
    latest = {r["tile_id"]: r for r in discovery.latest_obs_per_tile()}
    out["tiles"] = [{"tile_id": t, "obs_id": latest[t]["id"], "aoi": latest[t]["aoi"], "date": latest[t]["acq_date"],
                     "bounds": json.loads(db.one("SELECT bounds FROM tiles WHERE id=?", (t,))["bounds"]),
                     "thumb": f"/api/tiles/{latest[t]['id']}/thumb.png"} for t in out["members"] if t in latest]
    out["candidates"] = review.queue("all", None, None, 500)
    out["candidates"] = [c for c in out["candidates"] if c["cluster_id"] == cid]
    return out

@app.get("/api/ledger")
def ledger_list(limit: int = 100, offset: int = 0, subject: str | None = None, action: str | None = None, u=Depends(user)):
    return {"stats": ledger.stats(), "entries": ledger.entries(limit, offset, subject, action)}

@app.get("/api/ledger/verify")
def ledger_verify(u=Depends(user)):
    return ledger.verify()

@app.post("/api/ledger/tamper-demo")
def ledger_tamper(seq: int | None = None, u=Depends(user)):
    return ledger.tamper_demo(seq)

@app.get("/api/ledger/export")
def ledger_export(u=Depends(user)):
    ledger.append(u["username"], "ledger.export", "ledger", {"head": ledger.stats()["head_hash"]})
    return JSONResponse(ledger.export(), headers={"Content-Disposition": "attachment; filename=samvid-ledger.json"})

@app.get("/api/system/offline")
def system_offline(u=Depends(user)):
    return offline.status()

@app.post("/api/system/selftest")
def system_selftest(u=Depends(user)):
    r = offline.self_test()
    ledger.append(u["username"], "system.offline_selftest", "network", r)
    return r | {"status": offline.status()}

@app.get("/api/system/incoming")
def incoming(u=Depends(user)):
    files = sorted(archive.INCOMING_DIR.glob("*/*.tif")) + sorted(archive.INCOMING_DIR.glob("*.tif"))
    return {"files": [{"name": f.name, "aoi": f.parent.name if f.parent != archive.INCOMING_DIR else None,
                       "size": f.stat().st_size} for f in files],
            "index": {"vectors": archive.get_index().ntotal, "backend": archive.get_index().backend}}

def _post_ingest(aois_touched, actor):
    for a in aois_touched:
        monitor.run_aoi(a, actor=actor, verbose=False)
    discovery.run(actor=actor, verbose=False)
    review.rerank()

@app.post("/api/system/ingest-incoming")
def ingest_incoming(u=Depends(user)):
    results, touched = [], set()
    files = sorted(archive.INCOMING_DIR.glob("*/*.tif"))
    for f in files:
        dest = cfg.ARCHIVE_DIR / f.parent.name / f.name
        shutil.move(str(f), dest)
        r = archive.ingest_file(dest, actor=u["username"])
        results.append(r | {"file": f.name})
        if r.get("status") == "ingested":
            touched.add(r["aoi"])
    before = db.one("SELECT COUNT(*) n FROM candidates")["n"]
    _post_ingest(touched, u["username"])
    after = db.one("SELECT COUNT(*) n FROM candidates")["n"]
    return {"results": results, "aois_updated": sorted(touched), "candidates_before": before, "candidates_after": after,
            "index_vectors": archive.get_index().ntotal}

@app.post("/api/system/ingest-upload")
async def ingest_upload(file: UploadFile = File(...), u=Depends(user)):
    tmp = cfg.DATA_DIR / "uploads"
    tmp.mkdir(exist_ok=True)
    p = tmp / Path(file.filename or "upload.tif").name
    with open(p, "wb") as f:
        shutil.copyfileobj(file.file, f)
    r = archive.ingest_file(p, actor=u["username"])
    if r.get("status") == "ingested":
        _post_ingest({r["aoi"]}, u["username"])
    return r

@app.get("/api/evaluation")
def evaluation(u=Depends(user)):
    p = cfg.DATA_DIR / "evaluation.json"
    return json.loads(p.read_text()) if p.exists() else {}

@app.get("/api/health")
def health():
    return {"status": "ok", "offline_guard": offline.status()["guard_installed"]}

DIST = cfg.BACKEND_DIR.parent / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")