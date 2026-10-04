from __future__ import annotations
import json
import re
import time
import urllib.request
from datetime import date, timedelta
from . import config as cfg
from . import db

CONCEPTS = {
    "built": r"\b(built|building|buildings|structure|structures|construction|compound|facility|facilities|settlement|houses?|sheds?|hangars?|camp)\b",
    "new_built": r"\b(new(ly)?|recent(ly)?|emerging|fresh|appear(ed|ing)?|came up|under construction|being built)\b",
    "water": r"\b(water|reservoir|lake|pond|flood(ed|ing)?|inundat\w*)\b",
    "near_water": r"\b(near|along|beside|next to|close to|adjacent to|by|on the bank of|banks? of)\s+(a |the )?(river|stream|water|reservoir|lake|canal|bank)\b|\briver ?bank\b|\briverine\b",
    "vehicles": r"\b(vehicles?|trucks?|lorries|convoy|tanks?|parking|parked|vehicle concentration|vehicle concentrations)\b",
    "open_ground": r"\b(open ground|bare|barren|open area|open field|desert|sand|clearing)\b",
    "vegetation": r"\b(vegetation|forest|trees?|crops?|cropland|green|fields?)\b",
    "road": r"\b(road|roads|track|tracks|highway|route|airstrip|runway)\b",
    "clearance": r"\b(cleared|clearance|deforest\w*|felled|vegetation loss)\b",
    "water_change": r"\b(water[- ]extent|rising water|expanding water|water level|filling|shrinking water)\b",
    "large": r"\b(large|big|massive|major|significant)\b",
}
AOI_WORDS = {
    "AOI-RIV": r"\b(aoi-riv|riverine|river sector|north sector)\b",
    "AOI-RES": r"\b(aoi-res|reservoir sector)\b",
    "AOI-PLN": r"\b(aoi-pln|plains|logistics)\b",
    "AOI-HIL": r"\b(aoi-hil|highland|hills?|mountain)\b",
}
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
SYSTEM_PROMPT = """You convert a satellite-imagery analyst request into a JSON search plan.
Allowed concept keys: built, new_built, water, near_water, vehicles, open_ground, vegetation, road, clearance, water_change, large.
Weights in [0,1]. Allowed AOIs: AOI-RIV, AOI-RES, AOI-PLN, AOI-HIL. Sensor: S2 (optical) or S1 (SAR) or null.
Dates ISO YYYY-MM-DD or null. Reply with JSON only:
{"intent":"search","concepts":{...},"aoi":[],"date_from":null,"date_to":null,"sensor":null,"top_k":24,"explain":"..."}"""

def archive_end() -> date:
    r = db.one("SELECT MAX(acq_date) d FROM scenes")
    return date.fromisoformat(r["d"]) if r and r["d"] else date.today()

def _parse_dates(q: str):
    ql = q.lower()
    end = archive_end()
    m = re.search(r"(last|past)\s+(\d+)\s+(month|months|week|weeks|year|years)", ql)
    if m:
        n = int(m.group(2))
        days = n * (30 if "month" in m.group(3) else 7 if "week" in m.group(3) else 365)
        return (end - timedelta(days=days)).isoformat(), None
    iso = re.findall(r"(20\d\d)-(\d\d)(?:-(\d\d))?", ql)
    def to_d(t, start=True):
        y, mth, d = int(t[0]), int(t[1]), int(t[2]) if t[2] else (1 if start else 28)
        return date(y, mth, d).isoformat()
    mon = re.findall(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(20\d\d)\b", ql)
    after = re.search(r"\b(since|after|from)\b", ql)
    before = re.search(r"\b(before|until|till|up to)\b", ql)
    if len(iso) >= 2:
        return to_d(iso[0]), to_d(iso[1], False)
    if len(mon) >= 2:
        return date(int(mon[0][1]), MONTHS[mon[0][0]], 1).isoformat(), date(int(mon[1][1]), MONTHS[mon[1][0]], 28).isoformat()
    single = None
    if iso:
        single = (to_d(iso[0]), to_d(iso[0], False))
    elif mon:
        y, mm = int(mon[0][1]), MONTHS[mon[0][0]]
        single = (date(y, mm, 1).isoformat(), date(y, mm, 28).isoformat())
    else:
        yr = re.search(r"\b(?:in|during)\s+(20\d\d)\b", ql)
        if yr:
            return f"{yr.group(1)}-01-01", f"{yr.group(1)}-12-31"
    if single:
        if after:
            return single[0], None
        if before:
            return None, single[1]
        return single
    return None, None

def rule_plan(query: str) -> dict:
    ql = query.lower()
    concepts = {}
    for k, pat in CONCEPTS.items():
        if re.search(pat, ql):
            concepts[k] = 1.0
    if "near_water" in concepts:
        concepts.pop("water", None)
    if "new_built" in concepts and "built" not in concepts and not ({"road", "vehicles", "water"} & set(concepts)):
        concepts["built"] = 1.0
    if "new_built" in concepts and "built" not in concepts:
        concepts.pop("new_built")
        concepts["recent"] = 1.0
    if "clearance" in concepts:
        concepts.pop("vegetation", None)
    if "large" in concepts and len(concepts) == 1:
        concepts.pop("large")
    aoi = [a for a, pat in AOI_WORDS.items() if re.search(pat, ql)]
    sensor = "S1" if re.search(r"\b(sar|radar|backscatter)\b", ql) else ("S2" if re.search(r"\b(optical|multispectral)\b", ql) else None)
    m = re.search(r"\btop\s+(\d+)", ql)
    top_k = min(100, int(m.group(1))) if m else 24
    d0, d1 = _parse_dates(query)
    intent = "similar" if re.search(r"\b(similar to|like this|looks like)\b", ql) else "search"
    parts = [k.replace("_", " ") for k in concepts]
    explain = ("Looking for tiles with " + ", ".join(parts)) if parts else "No specific concept recognised - ranking by visual similarity to typical change sites"
    return {"intent": intent, "concepts": concepts, "aoi": aoi, "date_from": d0, "date_to": d1, "sensor": sensor,
            "top_k": top_k, "explain": explain}

def _ollama(query: str, timeout=4.0):
    body = json.dumps({"model": cfg.OLLAMA_MODEL, "prompt": query, "system": SYSTEM_PROMPT,
                       "format": "json", "stream": False, "options": {"temperature": 0}}).encode()
    req = urllib.request.Request(cfg.OLLAMA_URL + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    plan = json.loads(out["response"])
    plan.setdefault("concepts", {})
    plan["concepts"] = {k: float(v) for k, v in plan["concepts"].items() if k in CONCEPTS or k == "recent"}
    for k in ("aoi", "date_from", "date_to", "sensor", "top_k", "explain", "intent"):
        plan.setdefault(k, None)
    plan["aoi"] = [a for a in (plan["aoi"] or []) if a in AOI_WORDS]
    plan["top_k"] = int(plan["top_k"] or 24)
    return plan

def plan(query: str) -> dict:
    trace = []
    t0 = time.time()
    p = None
    if cfg.OLLAMA_URL.startswith(("http://127.0.0.1", "http://localhost")):
        try:
            p = _ollama(query)
            trace.append({"step": "Query understanding", "detail": f"local LLM {cfg.OLLAMA_MODEL} via Ollama (loopback)",
                          "ms": int((time.time() - t0) * 1000)})
            planner = f"local-llm:{cfg.OLLAMA_MODEL}"
        except Exception as e:  # noqa: BLE001 - any failure -> deterministic fallback
            trace.append({"step": "Local LLM unavailable", "detail": f"{type(e).__name__}; using rule-based planner",
                          "ms": int((time.time() - t0) * 1000)})
    if p is None:
        t1 = time.time()
        p = rule_plan(query)
        trace.append({"step": "Query understanding", "detail": "rule-based planner (deterministic, offline)",
                      "ms": int((time.time() - t1) * 1000)})
        planner = "rule-planner v1"
    p["planner"] = planner
    p["trace"] = trace
    return p
