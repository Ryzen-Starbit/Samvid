from __future__ import annotations
import io
import math
from datetime import date, datetime, timezone
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from . import db, evidence, ledger, review

W, H = A4
M = 36                      
CW = W - 2 * M              

INK = HexColor("#0f1723")
NAVY = HexColor("#152031")
NAVY2 = HexColor("#1d2b40")
TEXT = HexColor("#1b2533")
MUTED = HexColor("#5a6a7e")
FAINT = HexColor("#93a1b3")
LINE = HexColor("#dfe5ec")
PANEL = HexColor("#f4f7fa")
SKY = HexColor("#6cc4f0")
SKY_D = HexColor("#2a8fc4")
AMBER = HexColor("#f5b13d")
AMBER_D = HexColor("#c98512")
OK = HexColor("#2f9e62")
BAD = HexColor("#d4544e")

LC = [("water", "Water", "#4a90e2"), ("vegetation", "Vegetation", "#4fb06d"), ("built", "Built-up", "#e0644a"),
      ("bare", "Open ground", "#d2b478"), ("road", "Roads", "#8e99ab"), ("disturbed", "Earthworks", "#b07a4a"),
      ("vehicle", "Vehicles", "#f2d14b")]
TYPE_COLOR = {"construction": "#e0644a", "site_clearing": "#e8913a", "clearance": "#4fb06d", "water_expansion": "#4a90e2",
              "water_recession": "#7fb2ee", "road_development": "#8e99ab", "vehicle_concentration": "#e6c21f",
              "vehicle_dispersal": "#c9b04a", "demolition": "#b05a4a", "vegetation_regrowth": "#6cc48a", "unclassified": "#70839c"}
DIRECTION = {"appearance": "Appeared", "disappearance": "Disappeared", "expansion": "Grew", "contraction": "Shrank",
             "appearance \u2192 disappearance (transient)": "Came and went"}
STATUS = {"pending": ("Waiting for review", AMBER_D), "confirmed": ("Confirmed by analyst", OK), "rejected": ("Dismissed by analyst", BAD)}
ACTIONS = {"ingest.scene": "Image loaded", "model.monitoring_run": "Area analysed", "model.change_candidate": "Alert created",
           "analyst.decision": "Analyst decision", "report.export": "Report exported", "model.change_analysis": "Dates compared",
           "analyst.flag_site": "Site flagged"}

BODY = ParagraphStyle("b", fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=TEXT)
SMALL = ParagraphStyle("s", parent=BODY, fontSize=8, leading=11, textColor=MUTED)
LEAD = ParagraphStyle("l", parent=BODY, fontSize=11.5, leading=16)

def _d(s) -> date | None:
    if not s:
        return None
    s = str(s)
    if s.startswith(("S2_", "S1_")):
        s = s.rsplit("_", 1)[-1]
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None

def _fd(s, short=False) -> str:
    d = _d(s)
    return "-" if not d else d.strftime("%b %Y" if short else "%d %b %Y").lstrip("0")

def _area(m2: float) -> str:
    ha = m2 / 1e4
    return f"{ha:.1f} hectares" if ha >= 1 else f"{int(round(m2)):,} square metres"

def _sure(c: float) -> str:
    return "Very likely real" if c >= 0.85 else "Likely real" if c >= 0.65 else "Worth a look"

def _conf_color(c: float):
    return OK if c >= 0.85 else AMBER_D if c >= 0.65 else BAD

def _para(cv, text, style, x, y_top, w):
    p = Paragraph(text, style)
    _, h = p.wrap(w, 1000)
    p.drawOn(cv, x, y_top - h)
    return y_top - h

def _section(cv, y, title, sub=None):
    cv.setFillColor(SKY_D)
    cv.rect(M, y - 11, 3, 13, stroke=0, fill=1)
    cv.setFillColor(INK)
    cv.setFont("Helvetica-Bold", 12)
    cv.drawString(M + 10, y - 9, title)
    if sub:
        cv.setFont("Helvetica", 8.5)
        cv.setFillColor(MUTED)
        cv.drawRightString(M + CW, y - 9, sub)
    return y - 24

def _card(cv, x, y_top, w, h, fill=PANEL):
    cv.setFillColor(fill)
    cv.setStrokeColor(LINE)
    cv.setLineWidth(0.6)
    cv.roundRect(x, y_top - h, w, h, 6, stroke=1, fill=1)

def _reasons(m: dict) -> list[tuple[str, float, str]]:
    sig = min(1, max(0, (m.get("z_median", 3) - 3) / 6))
    q = m.get("quality") or 0
    reg = m.get("registration_error_px") or 0
    out = [("Strength of the change", 0.35 + 0.65 * sig,
            "far outside normal seasonal variation" if m.get("z_median", 0) >= 6 else "clearly outside normal variation"),
           ("Image clarity", q, "no cloud or shadow over the area" if q > 0.95 else f"{round(q * 100)}% of the area was clear"),
           ("Image alignment", max(0, 1 - reg / 1.5), f"off by {reg} pixels at most")]
    if m.get("hdbscan_prob") is not None:
        out.append(("Shape consistency", m["hdbscan_prob"], "changed pixels form one solid patch"))
    if m.get("sar"):
        out.append(("Radar check", 0.95 if m["sar"]["corroborated"] else 0.4,
                    "radar sees the same change" if m["sar"]["corroborated"] else "radar is inconclusive"))
    return out

def _header(cv, cid, generated, compact=False):
    h = 92 if not compact else 46
    cv.setFillColor(INK)
    cv.rect(0, H - h, W, h, stroke=0, fill=1)
    # faint orbit lines
    cv.saveState()
    clip = cv.beginPath()
    clip.rect(0, H - h, W, h)
    cv.clipPath(clip, stroke=0, fill=0)
    cv.setStrokeColor(NAVY2)
    cv.setLineWidth(1)
    for r in (70, 110, 150):
        cv.circle(W - 60, H - h / 2, r, stroke=1, fill=0)
    cv.restoreState()
    # logo mark
    lx, ly = M + 11, H - (h / 2 if compact else 34)
    cv.setFillColor(NAVY2)
    cv.setStrokeColor(SKY)
    cv.setLineWidth(1.3)
    cv.circle(lx, ly, 7, stroke=1, fill=1)
    cv.saveState()
    cv.translate(lx, ly)
    cv.rotate(24)
    cv.setStrokeColor(AMBER)
    cv.setDash(1.6, 2)
    cv.ellipse(-13, -4.8, 13, 4.8, stroke=1, fill=0)
    cv.restoreState()
    cv.setFillColor(AMBER)
    cv.circle(lx + 11.5, ly + 5.2, 1.9, stroke=0, fill=1)
    cv.setFillColor(white)
    cv.setFont("Helvetica-Bold", 15 if not compact else 12)
    cv.drawString(M + 28, ly - 5 if not compact else ly - 4, "SAMVID")
    if compact:
        cv.setFont("Helvetica", 8.5)
        cv.setFillColor(FAINT)
        cv.drawRightString(W - M, ly - 3, f"Change evidence report  |  {cid}")
        return H - h - 22
    cv.setFont("Helvetica", 9)
    cv.setFillColor(FAINT)
    cv.drawString(M + 28 + cv.stringWidth("SAMVID", "Helvetica-Bold", 15) + 10, ly - 4.5, "Satellite imagery change analysis")
    cv.setFillColor(white)
    cv.setFont("Helvetica-Bold", 20)
    cv.drawString(M, H - 74, "Change evidence report")
    cv.setFont("Helvetica", 9)
    cv.setFillColor(SKY)
    cv.drawRightString(W - M, H - 30, cid)
    cv.setFillColor(FAINT)
    cv.drawRightString(W - M, H - 44, f"Generated {generated}")
    cv.drawRightString(W - M, H - 74, "Processed fully offline")
    return H - h - 24

def _footer(cv, page, total, head_hash):
    cv.setStrokeColor(LINE)
    cv.setLineWidth(0.6)
    cv.line(M, 30, W - M, 30)
    cv.setFont("Helvetica", 7.5)
    cv.setFillColor(FAINT)
    cv.drawString(M, 19, f"Audit ledger head {head_hash[:20]}...  |  verify on the Audit trail page")
    cv.drawRightString(W - M, 19, f"Page {page} of {total}")

def _gauge(cv, cx, cy, r, val):
    cv.saveState()
    cv.setLineCap(1)
    cv.setLineWidth(10)
    cv.setStrokeColor(LINE)
    p = cv.beginPath()
    p.arc(cx - r, cy - r, cx + r, cy + r, 0, 180)
    cv.drawPath(p, stroke=1, fill=0)
    cv.setStrokeColor(_conf_color(val))
    p = cv.beginPath()
    p.arc(cx - r, cy - r, cx + r, cy + r, 180, -180 * val)
    cv.drawPath(p, stroke=1, fill=0)
    cv.restoreState()
    cv.setFillColor(INK)
    cv.setFont("Helvetica-Bold", 22)
    cv.drawCentredString(cx, cy + 2, f"{round(val * 100)}%")
    cv.setFont("Helvetica", 8)
    cv.setFillColor(MUTED)
    cv.drawCentredString(cx, cy - 12, "confidence")

def _image(cv, png: bytes, x, y_top, size, label, sub, accent=None):
    cv.setFillColor(INK)
    cv.roundRect(x - 2, y_top - size - 2, size + 4, size + 4, 4, stroke=0, fill=1)
    cv.drawImage(ImageReader(io.BytesIO(png)), x, y_top - size, size, size)
    if accent is not None:
        cv.setFillColor(accent)
        cv.rect(x, y_top - size - 2, size, 2.5, stroke=0, fill=1)
    cv.setFillColor(INK)
    cv.setFont("Helvetica-Bold", 9)
    cv.drawString(x, y_top - size - 15, label)
    cv.setFont("Helvetica", 8)
    cv.setFillColor(MUTED)
    cv.drawString(x, y_top - size - 26, sub)

def _lc_bar(cv, x, y, w, h, fr: dict, title):
    cv.setFont("Helvetica-Bold", 8.5)
    cv.setFillColor(MUTED)
    cv.drawString(x, y + h + 5, title.upper())
    tot = sum(fr.get(k, 0) for k, _, _ in LC) or 1
    cx = x
    cv.saveState()
    p = cv.beginPath()
    p.roundRect(x, y, w, h, 4)
    cv.clipPath(p, stroke=0, fill=0)
    cv.setFillColor(LINE)
    cv.rect(x, y, w, h, stroke=0, fill=1)
    for k, _, col in LC:
        v = fr.get(k, 0) / tot
        if v <= 0:
            continue
        cv.setFillColor(HexColor(col))
        cv.rect(cx, y, w * v, h, stroke=0, fill=1)
        if w * v > 34:
            cv.setFillColor(white if k not in ("bare", "vehicle") else INK)
            cv.setFont("Helvetica-Bold", 8)
            cv.drawCentredString(cx + w * v / 2, y + h / 2 - 3, f"{round(v * 100)}%")
        cx += w * v
    cv.restoreState()

def _meter(cv, x, y, w, label, val, note):
    cv.setFont("Helvetica-Bold", 8.8)
    cv.setFillColor(TEXT)
    cv.drawString(x, y - 9, label)
    cv.drawRightString(x + w, y - 9, f"{round(val * 100)}")
    cv.setFillColor(LINE)
    cv.roundRect(x, y - 20, w, 6, 3, stroke=0, fill=1)
    cv.setFillColor(OK if val >= 0.8 else AMBER if val >= 0.5 else BAD)
    cv.roundRect(x, y - 20, max(6, w * val), 6, 3, stroke=0, fill=1)
    cv.setFont("Helvetica", 7.6)
    cv.setFillColor(MUTED)
    cv.drawString(x, y - 31, note)

def _time_axis(d0: date, d1: date, x0, x1):
    span = max((d1 - d0).days, 1)
    return lambda d: x0 + (x1 - x0) * ((d - d0).days / span)

def _month_ticks(cv, X, d0, d1, y, every=3):
    cv.setFont("Helvetica", 7)
    cv.setFillColor(MUTED)
    cv.setStrokeColor(LINE)
    y_, m_ = d0.year, d0.month
    while True:
        d = date(y_, m_, 1)
        if d > d1:
            break
        if d >= d0 and (m_ - 1) % every == 0:
            cv.line(X(d), y, X(d), y - 3)
            cv.drawCentredString(X(d), y - 11, d.strftime("%b %y"))
        m_ += 1
        if m_ > 12:
            y_, m_ = y_ + 1, 1

def _seasonal_chart(cv, x, y_top, w, h, series, c):
    obs = [o for o in series["observations"] if o.get("ndvi") is not None and o.get("ndvi_expected") is not None
           and o.get("status") == "ok" and (o.get("usable_frac") or 0) >= 0.7]
    if len(obs) < 3:
        return _para(cv, "Not enough clear images of this spot to draw the seasonal chart.", SMALL, x, y_top, w)
    pl, pr, pt, pb = 34, 8, 10, 22
    gx0, gx1, gy0, gy1 = x + pl, x + w - pr, y_top - h + pb, y_top - pt
    ds = [_d(o["acq_date"]) for o in obs]
    X = _time_axis(ds[0], ds[-1], gx0, gx1)
    lo = min(min(o["ndvi"] for o in obs), min(o["ndvi_expected"] - 2 * o["ndvi_std"] for o in obs))
    hi = max(max(o["ndvi"] for o in obs), max(o["ndvi_expected"] + 2 * o["ndvi_std"] for o in obs))
    lo, hi = math.floor(lo * 10) / 10, math.ceil(hi * 10) / 10
    Y = lambda v: gy0 + (gy1 - gy0) * (v - lo) / max(hi - lo, 1e-6)  # noqa: E731
    # grid
    cv.setLineWidth(0.5)
    cv.setFont("Helvetica", 7)
    v = lo
    while v <= hi + 1e-6:
        cv.setStrokeColor(LINE)
        cv.line(gx0, Y(v), gx1, Y(v))
        cv.setFillColor(MUTED)
        cv.drawRightString(gx0 - 4, Y(v) - 2.5, f"{v:.1f}")
        v += 0.2 if hi - lo > 0.6 else 0.1
    # baseline-era shading (training year)
    train_end = ds[min(11, len(ds) - 1)]
    cv.setFillColor(HexColor("#eaf4fb"))
    cv.rect(gx0, gy0, X(train_end) - gx0, gy1 - gy0, stroke=0, fill=1)
    cv.setFillColor(SKY_D)
    cv.setFont("Helvetica", 7)
    cv.drawString(gx0 + 4, gy1 - 9, "learning what normal looks like")
    # expected band
    p = cv.beginPath()
    for i, o in enumerate(obs):
        (p.moveTo if i == 0 else p.lineTo)(X(ds[i]), Y(o["ndvi_expected"] + 2 * o["ndvi_std"]))
    for i in range(len(obs) - 1, -1, -1):
        o = obs[i]
        p.lineTo(X(ds[i]), Y(o["ndvi_expected"] - 2 * o["ndvi_std"]))
    p.close()
    cv.setFillColor(HexColor("#cfe7f5"))
    cv.drawPath(p, stroke=0, fill=1)
    # expected line
    cv.setStrokeColor(SKY_D)
    cv.setLineWidth(1)
    cv.setDash(3, 2)
    p = cv.beginPath()
    for i, o in enumerate(obs):
        (p.moveTo if i == 0 else p.lineTo)(X(ds[i]), Y(o["ndvi_expected"]))
    cv.drawPath(p, stroke=1, fill=0)
    cv.setDash()
    # change marker
    ed = _d(c["earliest_date"])
    if ed and ds[0] <= ed <= ds[-1]:
        cv.setStrokeColor(AMBER_D)
        cv.setLineWidth(1)
        cv.line(X(ed), gy0, X(ed), gy1)
        cv.setFillColor(AMBER_D)
        cv.setFont("Helvetica-Bold", 7.5)
        cv.drawString(X(ed) + 3, gy1 - 9, "first sign")
    # observed line + points
    cv.setStrokeColor(INK)
    cv.setLineWidth(1.4)
    p = cv.beginPath()
    for i, o in enumerate(obs):
        (p.moveTo if i == 0 else p.lineTo)(X(ds[i]), Y(o["ndvi"]))
    cv.drawPath(p, stroke=1, fill=0)
    for i, o in enumerate(obs):
        out = abs(o["ndvi"] - o["ndvi_expected"]) > 2 * o["ndvi_std"]
        cv.setFillColor(AMBER if out else INK)
        cv.setStrokeColor(white)
        cv.setLineWidth(0.6)
        cv.circle(X(ds[i]), Y(o["ndvi"]), 2.6 if out else 1.9, stroke=1, fill=1)
    _month_ticks(cv, X, ds[0], ds[-1], gy0)
    cv.saveState()
    cv.translate(x + 7, (gy0 + gy1) / 2)
    cv.rotate(90)
    cv.setFont("Helvetica", 7)
    cv.setFillColor(MUTED)
    cv.drawCentredString(0, 0, "greenness (NDVI)")
    cv.restoreState()
    # legend
    ly = y_top - h - 6
    lx = x + pl
    cv.setFillColor(HexColor("#cfe7f5"))
    cv.rect(lx, ly - 6, 14, 7, stroke=0, fill=1)
    cv.setFillColor(MUTED)
    cv.setFont("Helvetica", 7.5)
    cv.drawString(lx + 18, ly - 5, "normal range for that time of year")
    lx += 150
    cv.setStrokeColor(INK)
    cv.setLineWidth(1.4)
    cv.line(lx, ly - 2.5, lx + 14, ly - 2.5)
    cv.drawString(lx + 18, ly - 5, "what was observed")
    lx += 100
    cv.setFillColor(AMBER)
    cv.circle(lx + 4, ly - 2.5, 2.6, stroke=0, fill=1)
    cv.setFillColor(MUTED)
    cv.drawString(lx + 12, ly - 5, "outside the normal range")
    return ly - 14

def _search_timeline(cv, x, y_top, w, c):
    s = c["search"]
    win = s.get("window") or [s.get("last_without_change_date"), c["after_scene"]]
    d0, d1 = _d(win[0]), _d(win[1]) or _d(c["after_scene"])
    if not d0 or not d1:
        return y_top
    X = _time_axis(d0, d1, x + 12, x + w - 12)
    ay = y_top - 46
    lw, ef = _d(s.get("last_without_change_date")), _d(s.get("earliest_date"))
    # bracket highlight
    if lw and ef:
        cv.setFillColor(HexColor("#fdf1dc"))
        cv.roundRect(X(lw), ay - 9, max(X(ef) - X(lw), 3), 18, 4, stroke=0, fill=1)
    cv.setStrokeColor(FAINT)
    cv.setLineWidth(1.2)
    cv.line(x + 12, ay, x + w - 12, ay)
    _month_ticks(cv, X, d0, d1, ay - 12, every=2)
    for sid in s.get("skipped_masked", []):
        d = _d(sid)
        if d:
            cv.setStrokeColor(FAINT)
            cv.setLineWidth(1.2)
            cv.line(X(d) - 3, ay - 3, X(d) + 3, ay + 3)
            cv.line(X(d) - 3, ay + 3, X(d) + 3, ay - 3)
    for pr in s.get("probes", []):
        d = _d(pr["date"])
        if not d:
            continue
        col = AMBER if pr["supports_change"] else SKY_D
        cv.setFillColor(col)
        cv.setStrokeColor(white)
        cv.setLineWidth(1)
        cv.circle(X(d), ay, 5.5, stroke=1, fill=1)
        cv.setFillColor(white)
        cv.setFont("Helvetica-Bold", 6.5)
        cv.drawCentredString(X(d), ay - 2.3, str(pr["step"] + 1))
        cv.setFillColor(MUTED)
        cv.setFont("Helvetica", 6.8)
        cv.drawCentredString(X(d), ay + 9, f"{round(pr['support'] * 100)}%")
    if ef:
        cv.setFillColor(AMBER_D)
        cv.setFont("Helvetica-Bold", 8)
        lab = f"First sign {_fd(ef)}"
        lw_ = cv.stringWidth(lab, "Helvetica-Bold", 8)
        cv.drawString(min(max(X(ef) - lw_ / 2, x + 6), x + w - 6 - lw_), ay + 26, lab)
        cv.setStrokeColor(AMBER_D)
        cv.setLineWidth(0.8)
        cv.line(X(ef), ay + 23, X(ef), ay + 16)
    # legend
    ly = ay - 34
    cv.setFont("Helvetica", 7.5)
    for i, (col, lab) in enumerate([(SKY_D, "checked: no change yet"), (AMBER, "checked: change visible"), (None, "skipped: too cloudy")]):
        lx = x + 12 + i * 150
        if col:
            cv.setFillColor(col)
            cv.circle(lx + 4, ly + 2.5, 4, stroke=0, fill=1)
        else:
            cv.setStrokeColor(FAINT)
            cv.line(lx + 1, ly - 0.5, lx + 7, ly + 5.5)
            cv.line(lx + 1, ly + 5.5, lx + 7, ly - 0.5)
        cv.setFillColor(MUTED)
        cv.drawString(lx + 12, ly, lab)
    return ly - 14

def _table(cv, x, y, cols, rows, widths, row_h=None, size=8):
    st = ParagraphStyle("t", parent=BODY, fontSize=size, leading=size + 3)
    hs = ParagraphStyle("th", parent=st, fontName="Helvetica-Bold", textColor=white)
    cv.setFillColor(NAVY)
    cv.rect(x, y - 16, sum(widths), 16, stroke=0, fill=1)
    cx = x
    for h_, wd in zip(cols, widths):
        _para(cv, h_, hs, cx + 5, y - 4, wd - 10)
        cx += wd
    y -= 16
    for i, r in enumerate(rows):
        ps = [Paragraph(str(v), st) for v in r]
        hh = max(p.wrap(wd - 10, 1000)[1] for p, wd in zip(ps, widths)) + 8
        if y - hh < 48:
            return y, rows[i:]
        if i % 2:
            cv.setFillColor(PANEL)
            cv.rect(x, y - hh, sum(widths), hh, stroke=0, fill=1)
        cx = x
        for p, wd in zip(ps, widths):
            p.drawOn(cv, cx + 5, y - 4 - p.height)
            cx += wd
        y -= hh
    cv.setStrokeColor(LINE)
    cv.line(x, y, x + sum(widths), y)
    return y, []

def _best_series(c):
    ed = _d(c["earliest_date"])
    best, score = None, -1.0
    for tid in c["tile_ids"][:6]:
        s = evidence.tile_series(tid)
        zs = [abs(o.get("z", 0)) for o in s["observations"] if o.get("status") == "ok" and _d(o["acq_date"]) and ed
              and _d(o["acq_date"]) >= ed and (o.get("usable_frac") or 0) >= 0.7]
        sc = sum(zs) / len(zs) if zs else 0
        if sc > score:
            best, score = s, sc
    return best

def candidate_pdf(cid: str, actor: str) -> bytes:
    row = db.one("SELECT * FROM candidates WHERE id=?", (cid,))
    if not row:
        raise KeyError(cid)
    c = review.public(row)
    aoi = db.one("SELECT name FROM aois WHERE id=?", (c["aoi"],))
    aoi_name = aoi["name"] if aoi else c["aoi"]
    ver = ledger.verify()
    st = ledger.stats()
    exp = ledger.append(actor, "report.export", cid, {"ledger_head_at_export": st["head_hash"], "format": "pdf"})
    entries = sorted(ledger.entries(limit=500, subject=cid), key=lambda e: e["seq"])
    head = exp.get("entry_hash", st["head_hash"])
    m, s = c["metrics"], c["search"]
    generated = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    tcol = HexColor(TYPE_COLOR.get(c["change_type"], "#70839c"))
    buf = io.BytesIO()
    cv = canvas.Canvas(buf, pagesize=A4)
    cv.setTitle(f"SAMVID change evidence report {cid}")
    cv.setAuthor("SAMVID")
    cv.setSubject(f"{c['label']} - {aoi_name}")
    TOTAL = 3
    y = _header(cv, cid, generated)
    ch = 118
    _card(cv, M, y, CW, ch, fill=white)
    cv.setFillColor(tcol)
    cv.roundRect(M, y - ch, 5, ch, 2, stroke=0, fill=1)
    tx = M + 20
    cv.setFillColor(tcol)
    cv.roundRect(tx, y - 26, cv.stringWidth(c["label"].upper(), "Helvetica-Bold", 7.5) + 14, 15, 7.5, stroke=0, fill=1)
    cv.setFillColor(white)
    cv.setFont("Helvetica-Bold", 7.5)
    cv.drawString(tx + 7, y - 21.5, c["label"].upper())
    stl, scol = STATUS.get(c["status"], (c["status"], MUTED))
    sx = tx + cv.stringWidth(c["label"].upper(), "Helvetica-Bold", 7.5) + 22
    cv.setStrokeColor(scol)
    cv.setFillColor(white)
    cv.setLineWidth(0.8)
    cv.roundRect(sx, y - 26, cv.stringWidth(stl, "Helvetica-Bold", 7.5) + 14, 15, 7.5, stroke=1, fill=1)
    cv.setFillColor(scol)
    cv.drawString(sx + 7, y - 21.5, stl)
    verb = {"construction": "appeared", "site_clearing": "started", "clearance": "happened", "road_development": "appeared",
            "vehicle_concentration": "showed up", "demolition": "happened"}.get(c["change_type"], "began")
    headline = f"{c['label']} {verb} by <b>{_fd(c['earliest_date'])}</b>, covering about {_area(c['area_m2'])}."
    yy = _para(cv, headline, ParagraphStyle("h", parent=LEAD, fontSize=14, leading=18, textColor=INK), tx, y - 36, CW - 190)
    facts = [("Area", aoi_name), ("Location", f"{c['centroid'][0]:.4f}°N, {c['centroid'][1]:.4f}°E"),
             ("What happened", DIRECTION.get(c["direction"], c["direction"].split(" ")[0].title())),
             ("Seen between", f"{_fd(s.get('last_without_change_date'))} and {_fd(c['earliest_date'])}")]
    fy = min(yy - 10, y - 78)
    for i, (k, v) in enumerate(facts):
        fx = tx + (i % 2) * 175
        fyy = fy - (i // 2) * 22
        cv.setFont("Helvetica", 7)
        cv.setFillColor(FAINT)
        cv.drawString(fx, fyy, k.upper())
        cv.setFont("Helvetica-Bold", 9)
        cv.setFillColor(TEXT)
        cv.drawString(fx, fyy - 11, v)
    gx = M + CW - 85
    cv.setStrokeColor(LINE)
    cv.line(gx - 70, y - 14, gx - 70, y - ch + 14)
    _gauge(cv, gx, y - 70, 46, c["confidence"])
    cv.setFont("Helvetica-Bold", 9)
    cv.setFillColor(_conf_color(c["confidence"]))
    cv.drawCentredString(gx, y - ch + 18, _sure(c["confidence"]))
    y -= ch + 26
    y = _section(cv, y, "What the satellite saw", "yellow box marks the changed area")
    size = (CW - 3 * 14) / 4
    shots = [("before", "Before", _fd(c["before_scene"]), SKY_D),
             ("earliest", "First sign", _fd(c["earliest_date"]), AMBER),
             ("after", "Latest", _fd(c["after_scene"]), SKY_D),
             ("change", "Changed area", "highlighted in red", BAD)]
    for i, (k, lab, sub, acc) in enumerate(shots):
        _image(cv, evidence.candidate_image(cid, k), M + i * (size + 14), y, size, lab, sub, acc)
    y -= size + 46
    y = _section(cv, y, "What the ground was and what it became")
    cb, ca = m.get("class_before") or {}, m.get("class_after") or {}
    _lc_bar(cv, M, y - 22, CW, 18, cb, f"Before  |  {_fd(c['before_scene'])}")
    _lc_bar(cv, M, y - 64, CW, 18, ca, f"When detected  |  {_fd(c['after_scene'])}")
    lx, ly = M, y - 84
    cv.setFont("Helvetica", 7.8)
    for k, lab, col in LC:
        if cb.get(k, 0) + ca.get(k, 0) <= 0.005:
            continue
        cv.setFillColor(HexColor(col))
        cv.roundRect(lx, ly, 8, 8, 2, stroke=0, fill=1)
        cv.setFillColor(MUTED)
        cv.drawString(lx + 12, ly + 1, lab)
        lx += cv.stringWidth(lab, "Helvetica", 7.8) + 28
    y = ly - 22
    y = _section(cv, y, "Why SAMVID trusts this alert", "0 = weak, 100 = strong")
    rs = _reasons(m)
    rows_n = math.ceil(len(rs) / 2)
    _card(cv, M, y, CW, rows_n * 44 + 16)
    colw = (CW - 32 - 28) / 2
    for i, (lab, v, note) in enumerate(rs):
        _meter(cv, M + 16 + (i % 2) * (colw + 28), y - 12 - (i // 2) * 44, colw, lab, v, note)
    _footer(cv, 1, TOTAL, head)
    cv.showPage()
    y = _header(cv, cid, generated, compact=True)
    y = _section(cv, y, "Seasonal check", "is this just the time of year?")
    sea = m.get("seasonal") or {}
    nb, na = sea.get("ndvi_before") or {}, sea.get("ndvi_after") or {}
    expl = ("Fields green up and dry out every year, so SAMVID first learns the normal greenness of this exact spot for each month. "
            "The shaded band is that normal range. Points that fall outside it cannot be explained by the season.")
    if na:
        expl += (f" At detection, greenness was <b>{na.get('observed')}</b> where <b>{na.get('expected')}</b> was expected"
                 f" for that time of year.")
    y = _para(cv, expl, BODY, M, y, CW) - 10
    series = _best_series(c)
    y = _seasonal_chart(cv, M, y, CW, 170, series, c) - 18
    y = _section(cv, y, "Finding the first sign", f"{s['comparisons_binary']} images checked instead of {s['comparisons_linear']}")
    y = _para(cv, "Instead of opening every image in order, SAMVID jumps to the middle of the time window, checks for the change, "
                  "and halves the window each time. Cloudy images are skipped. Numbers show the order of the checks; percentages "
                  "show how much of the changed area was visible.", BODY, M, y, CW) - 4
    _card(cv, M, y, CW, 98, fill=white)
    y = _search_timeline(cv, M, y - 6, CW, c) - 22
    y = _section(cv, y, "Second opinion and decision")
    half = (CW - 14) / 2
    _card(cv, M, y, half, 92)
    cv.setFont("Helvetica-Bold", 9.5)
    cv.setFillColor(INK)
    cv.drawString(M + 14, y - 20, "Radar check (Sentinel-1)")
    sar = m.get("sar")
    if sar:
        ok = sar["corroborated"]
        cv.setFillColor(OK if ok else AMBER_D)
        cv.setFont("Helvetica-Bold", 16)
        cv.drawString(M + 14, y - 44, "Agrees" if ok else "Inconclusive")
        _para(cv, f"Radar backscatter changed by <b>{sar['median_delta_db']} dB</b> between {_fd(sar['sar_before'])} and "
                  f"{_fd(sar['sar_after'])}. Radar sees through cloud, so it is an independent check.", SMALL, M + 14, y - 52, half - 28)
    else:
        _para(cv, "No radar image was close enough in time to this change to compare.", SMALL, M + 14, y - 32, half - 28)
    dx = M + half + 14
    _card(cv, dx, y, half, 92)
    cv.setFont("Helvetica-Bold", 9.5)
    cv.setFillColor(INK)
    cv.drawString(dx + 14, y - 20, "Analyst decision")
    cv.setFillColor(scol)
    cv.setFont("Helvetica-Bold", 16)
    cv.drawString(dx + 14, y - 44, stl)
    who = f"By {c['decided_by']} on {_fd(c['decided_at'])}." if c.get("decided_by") else "No one has reviewed this alert yet."
    if c.get("note"):
        who += f" Note: \"{c['note']}\""
    _para(cv, who, SMALL, dx + 14, y - 52, half - 28)
    _footer(cv, 2, TOTAL, head)
    cv.showPage()
    y = _header(cv, cid, generated, compact=True)
    y = _section(cv, y, "Where it is", "full scene at detection")
    S = 150
    cv.setFillColor(INK)
    cv.roundRect(M - 2, y - S - 2, S + 4, S + 4, 4, stroke=0, fill=1)
    cv.drawImage(ImageReader(io.BytesIO(evidence.scene_image(c["after_scene"]))), M, y - S, S, S)
    bx0, by0, bx1, by1 = c["bbox"]
    k = S / 256
    cv.setStrokeColor(AMBER)
    cv.setLineWidth(1.6)
    cv.rect(M + bx0 * k - 3, y - by1 * k - 3, (bx1 - bx0) * k + 6, (by1 - by0) * k + 6, stroke=1, fill=0)
    tx = M + S + 22
    rows_ = [("Area", aoi_name), ("Centre", f"{c['centroid'][0]:.5f} N, {c['centroid'][1]:.5f} E"),
             ("Size on the ground", _area(c["area_m2"])), ("Image used", c["after_scene"]),
             ("Grid tiles", ", ".join(t.split(":", 1)[1] for t in c["tile_ids"][:6]))]
    for i, (k_, v_) in enumerate(rows_):
        yy = y - 8 - i * 28
        cv.setFont("Helvetica", 7)
        cv.setFillColor(FAINT)
        cv.drawString(tx, yy, k_.upper())
        cv.setFont("Helvetica-Bold", 9.5)
        cv.setFillColor(TEXT)
        cv.drawString(tx, yy - 12, v_)
    y -= S + 28
    y = _section(cv, y, "How this alert was produced")
    steps = [[f"<b>{i + 1}. {p['step'][:1].upper() + p['step'][1:]}</b>", p["detail"] if p["step"] != "SAR corroboration" else
              (f"{sar['median_delta_db']} dB change, {'agrees' if sar and sar['corroborated'] else 'inconclusive'}" if sar else p["detail"]),
              f"<font color='#5a6a7e'>{p['model']}</font>"] for i, p in enumerate(c["processing"])]
    y, _ = _table(cv, M, y, ["Step", "What happened", "Method / version"], steps, [130, 210, CW - 340])
    y -= 26

    y = _section(cv, y, "Chain of custody", "tamper-evident audit ledger")
    vok = ver["valid"]
    _card(cv, M, y, CW, 58, fill=HexColor("#ecf8f1") if vok else HexColor("#fcebea"))
    cv.setFillColor(OK if vok else BAD)
    cv.circle(M + 26, y - 29, 12, stroke=0, fill=1)
    cv.setStrokeColor(white)
    cv.setLineWidth(2.2)
    cv.setLineCap(1)
    if vok:
        cv.line(M + 20, y - 29, M + 24.5, y - 33.5)
        cv.line(M + 24.5, y - 33.5, M + 32, y - 24.5)
    else:
        cv.line(M + 21, y - 24, M + 31, y - 34)
        cv.line(M + 21, y - 34, M + 31, y - 24)
    cv.setFillColor(INK)
    cv.setFont("Helvetica-Bold", 11)
    cv.drawString(M + 48, y - 24, "Ledger intact" if vok else f"Ledger broken at entry #{ver['broken_at']}")
    cv.setFont("Helvetica", 8.3)
    cv.setFillColor(MUTED)
    cv.drawString(M + 48, y - 37, f"All {ver['checked']} entries re-checked when this report was made." if vok else str(ver.get("reason", "")))
    cv.setFont("Courier", 7.5)
    cv.drawString(M + 48, y - 49, f"head {head}")
    y -= 74
    led = [[f"#{e['seq']}", _fd(e["ts"]), e["actor"], ACTIONS.get(e["action"], e["action"]),
            f"<font face='Courier' size='6.8'>{e['entry_hash'][:32]}<br/>{e['entry_hash'][32:]}</font>"] for e in entries]
    y, rest = _table(cv, M, y, ["#", "Date", "By", "Action", "Entry fingerprint (SHA-256)"], led, [38, 70, 70, 120, CW - 298])
    if rest:
        cv.setFont("Helvetica", 8)
        cv.setFillColor(MUTED)
        cv.drawString(M, y - 12, f"+ {len(rest)} more entries in the full ledger export.")
        y -= 14
    y -= 16
    _para(cv, "Each entry's fingerprint is computed from its contents plus the previous entry's fingerprint, so changing any past "
              "entry breaks every one after it. Export the full ledger from the Audit trail page to check this independently.",
          SMALL, M, y, CW)
    _footer(cv, 3, TOTAL, head)
    cv.showPage()
    cv.save()
    return buf.getvalue()

def bundle(cid: str) -> dict:
    c = review.public(db.one("SELECT * FROM candidates WHERE id=?", (cid,)))
    return {"candidate": c, "ledger": ledger.export(subject=cid)}