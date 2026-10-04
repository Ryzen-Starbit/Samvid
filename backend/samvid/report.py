from __future__ import annotations
import base64
import html
import json
from datetime import datetime, timezone
from . import db, evidence, ledger, review

CSS = """
body{font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;color:#101826;margin:32px;max-width:960px}
h1{font-size:22px;margin:0 0 4px} h2{font-size:15px;margin:26px 0 8px;border-bottom:1px solid #d8dee4;padding-bottom:4px}
.meta{color:#4b5866;font-size:12px} table{border-collapse:collapse;width:100%;font-size:12.5px}
td,th{border:1px solid #d8dee4;padding:5px 7px;text-align:left;vertical-align:top} th{background:#f4f6f8;width:28%}
.ev{display:grid;grid-template-columns:repeat(4,1fr);gap:8px} .ev figure{margin:0} .ev img{width:100%;image-rendering:pixelated;border:1px solid #d8dee4}
figcaption{font-size:11px;color:#4b5866} code{font:11px ui-monospace,Menlo,monospace;word-break:break-all}
.ok{color:#2a6b45;font-weight:600} .bad{color:#8c2a20;font-weight:600} .badge{display:inline-block;padding:1px 8px;border-radius:10px;background:#e2f2f0;color:#135e59;font-size:12px}
@media print{body{margin:12mm}}
"""

def _img(cid, kind):
    return "data:image/png;base64," + base64.b64encode(evidence.candidate_image(cid, kind)).decode()

def candidate_html(cid: str, actor: str) -> str:
    c = review.public(db.one("SELECT * FROM candidates WHERE id=?", (cid,)))
    entries = sorted(ledger.entries(limit=500, subject=cid), key=lambda e: e["seq"])
    ver = ledger.verify()
    st = ledger.stats()
    e = ledger.append(actor, "report.export", cid, {"ledger_head_at_export": st["head_hash"],
                                                    "entries_included": [x["seq"] for x in entries]})
    m = c["metrics"]
    s = c["search"]
    sea = m.get("seasonal") or {}
    esc = html.escape
    rows = [
        ("Change", f"{esc(c['label'])} — {esc(c['direction'])}"),
        ("AOI / tiles", f"{esc(c['aoi'])} · {', '.join(c['tile_ids'][:6])}"),
        ("Centroid (lat, lon)", f"{c['centroid'][0]:.5f}, {c['centroid'][1]:.5f}"),
        ("Area", f"{c['area_px']} px ≈ {c['area_m2'] / 1e4:.2f} ha"),
        ("Earliest supporting observation", f"<b>{esc(c['earliest_date'])}</b> ({esc(c['earliest_scene'])}); last observation without change: {esc(s['last_without_change_date'])}"),
        ("Binary search", f"{s['comparisons_binary']} comparisons vs {s['comparisons_linear']} for a linear scan; {len(s['skipped_masked'])} masked scene(s) skipped"),
        ("Confidence", f"{c['confidence']:.2f} (rank score {c['rank_score']:.2f})"),
        ("Seasonal check", f"NDVI before {sea.get('ndvi_before', {}).get('observed')} (expected {sea.get('ndvi_before', {}).get('expected')}), "
                           f"after {sea.get('ndvi_after', {}).get('observed')} (expected {sea.get('ndvi_after', {}).get('expected')}), σ {sea.get('ndvi_sigma')}"),
        ("Quality", f"clear fraction {m.get('quality')}, registration error {m.get('registration_error_px')} px, HDBSCAN membership {m.get('hdbscan_prob')}"),
        ("SAR corroboration", esc(json.dumps(m.get("sar"))) if m.get("sar") else "no SAR pair within 40 days"),
        ("Analyst decision", f"<span class='badge'>{esc(c['status'])}</span> {esc(c['decided_by'] or '')} {esc(c['decided_at'] or '')} {esc(c['note'] or '')}"),
    ]
    proc = "".join(f"<tr><td>{esc(p['step'])}</td><td>{esc(p['detail'])}</td><td><code>{esc(p['model'])}</code></td></tr>" for p in c["processing"])
    led = "".join(f"<tr><td>#{x['seq']}</td><td>{esc(x['ts'])}</td><td>{esc(x['actor'])}</td><td>{esc(x['action'])}</td>"
                  f"<td><code>{x['entry_hash']}</code><br><span class='meta'>prev <code>{x['prev_hash'][:24]}…</code></span></td></tr>" for x in entries)
    vtxt = f"<span class='ok'>VALID</span> — {ver['checked']} entries re-hashed" if ver["valid"] else \
        f"<span class='bad'>BROKEN at #{ver['broken_at']}</span> — {esc(ver['reason'])}"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>SAMVID evidence report {cid}</title><style>{CSS}</style></head><body>
<h1>SAMVID evidence report — {cid}</h1>
<div class="meta">Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by {esc(actor)} · offline on-prem system · export ledger entry #{e['seq']}</div>
<h2>Evidence</h2><div class="ev">
<figure><img src="{_img(cid, 'before')}"><figcaption>Before · {esc(c['before_scene'])}</figcaption></figure>
<figure><img src="{_img(cid, 'earliest')}"><figcaption>Earliest support · {esc(c['earliest_scene'])}</figcaption></figure>
<figure><img src="{_img(cid, 'after')}"><figcaption>After · {esc(c['after_scene'])}</figcaption></figure>
<figure><img src="{_img(cid, 'change')}"><figcaption>Change mask</figcaption></figure></div>
<h2>Finding</h2><table>{''.join(f'<tr><th>{k}</th><td>{v}</td></tr>' for k, v in rows)}</table>
<h2>Processing history</h2><table><tr><th>Step</th><th>Detail</th><th>Model / version</th></tr>{proc}</table>
<h2>Chain of custody</h2>
<p>Ledger verification at export: {vtxt}. Ledger head <code>{st['head_hash']}</code> (entry #{st['head_seq']}).</p>
<table><tr><th>Seq</th><th>Time (UTC)</th><th>Actor</th><th>Action</th><th>Entry hash</th></tr>{led}</table>
<p class="meta">Verify: entry_hash = SHA-256(seq|ts|actor|action|subject|SHA-256(payload)|prev_hash); each prev_hash equals the previous entry's hash.
The full ledger can be exported from the Audit Ledger page and checked independently.</p>
</body></html>"""

def bundle(cid: str) -> dict:
    c = review.public(db.one("SELECT * FROM candidates WHERE id=?", (cid,)))
    return {"candidate": c, "ledger": ledger.export(subject=cid)}
