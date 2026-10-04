from __future__ import annotations
import json
import sqlite3
import threading
from . import config as cfg
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS aois (
    id TEXT PRIMARY KEY, name TEXT, lat REAL, lon REAL, bounds TEXT
);
CREATE TABLE IF NOT EXISTS scenes (
    id TEXT PRIMARY KEY, aoi TEXT, sensor TEXT, acq_date TEXT, month_idx INTEGER,
    path TEXT, sha256 TEXT UNIQUE, crs TEXT, bands TEXT, width INTEGER, height INTEGER,
    bounds TEXT, cloud_frac REAL, shadow_frac REAL, snow_frac REAL, haze INTEGER,
    usable_frac REAL, reg_dx REAL, reg_dy REAL, reg_error REAL, radiometric TEXT,
    status TEXT, status_reason TEXT, quicklook TEXT, ingested_at TEXT
);
CREATE TABLE IF NOT EXISTS tiles (
    id TEXT PRIMARY KEY, aoi TEXT, row INTEGER, col INTEGER, bounds TEXT,
    cluster_id INTEGER DEFAULT -1
);
CREATE TABLE IF NOT EXISTS tile_obs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, tile_id TEXT, scene_id TEXT, aoi TEXT,
    sensor TEXT, acq_date TEXT, month_idx INTEGER, usable_frac REAL,
    f_water REAL, f_veg REAL, f_built REAL, f_bare REAL, f_road REAL, f_vehicle REAL,
    f_disturbed REAL, vehicle_count INTEGER, ndvi REAL, ndvi_expected REAL, ndvi_std REAL,
    ndwi REAL, ndbi REAL, river_dist REAL, new_built REAL, d_water REAL, d_veg REAL,
    linearity REAL, embedded INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_obs_tile ON tile_obs(tile_id);
CREATE INDEX IF NOT EXISTS ix_obs_scene ON tile_obs(scene_id);
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY, aoi TEXT, tile_ids TEXT, bbox TEXT, centroid TEXT,
    change_type TEXT, direction TEXT, first_scene TEXT, last_scene TEXT,
    before_scene TEXT, after_scene TEXT, earliest_scene TEXT, earliest_date TEXT,
    bracket_scene TEXT, search TEXT, confidence REAL, rank_score REAL,
    area_px INTEGER, area_m2 REAL, metrics TEXT, processing TEXT, status TEXT DEFAULT 'pending',
    decided_by TEXT, decided_at TEXT, note TEXT, cluster_id INTEGER DEFAULT -1, created_at TEXT,
    mask TEXT
);
CREATE TABLE IF NOT EXISTS tile_change (
    tile_id TEXT, aoi TEXT, pair_idx INTEGER, period TEXT, changed_px INTEGER,
    changed_px_naive INTEGER, change_type TEXT
);
CREATE TABLE IF NOT EXISTS clusters (
    id INTEGER PRIMARY KEY, label TEXT, size INTEGER, members TEXT, profile TEXT,
    trend TEXT, acceleration REAL, hotspot INTEGER, summary TEXT
);
CREATE TABLE IF NOT EXISTS reranker (key TEXT PRIMARY KEY, weight REAL);
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY, display TEXT, role TEXT, salt TEXT, pw_hash TEXT
);
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, username TEXT, expires REAL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""

def conn() -> sqlite3.Connection:
    c = getattr(_local, "con", None)
    if c is None:
        c = sqlite3.connect(cfg.DB_PATH, check_same_thread=False, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        _local.con = c
    return c

def q(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]

def one(sql, args=()):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None

def run(sql, args=()):
    c = conn()
    cur = c.execute(sql, args)
    c.commit()
    return cur

def many(sql, rows):
    c = conn()
    c.executemany(sql, rows)
    c.commit()

def kv_get(key, default=None):
    r = one("SELECT value FROM kv WHERE key=?", (key,))
    return json.loads(r["value"]) if r else default

def kv_set(key, value):
    run("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value)))

def reset():
    c = conn()
    for t in ("aois", "scenes", "tiles", "tile_obs", "candidates", "tile_change", "clusters", "reranker", "kv"):
        c.execute(f"DELETE FROM {t}")
    c.execute("DELETE FROM sqlite_sequence WHERE name='tile_obs'") if one(
        "SELECT name FROM sqlite_master WHERE name='sqlite_sequence'") else None
    c.commit()
