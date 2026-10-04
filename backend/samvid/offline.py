from __future__ import annotations
import hashlib
import importlib.metadata as md
import ipaddress
import socket
import threading
from datetime import datetime, timezone
from . import config as cfg

_lock = threading.Lock()
BLOCKED: list[dict] = []
ALLOWED_COUNT = {"n": 0}
_installed = {"v": False}
_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex
_orig_create = socket.create_connection
_orig_getaddrinfo = socket.getaddrinfo

ALLOWED_HOSTS: set[str] = set(cfg.FIREBASE_CERT_HOSTS) if cfg.FIREBASE_ENABLED else set()
_allowed_ips: set[str] = set()
ALLOWED_EXTERNAL: list[dict] = []

def _is_local(host) -> bool:
    if host in ("localhost", "", None):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_unspecified
    except ValueError:
        return False

def _check(addr, kind):
    if isinstance(addr, tuple) and addr:
        host = addr[0]
    else:
        return  
    if _is_local(host):
        ALLOWED_COUNT["n"] += 1
        return
    if str(host) in _allowed_ips:
        return
    with _lock:
        BLOCKED.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "host": str(host),
                        "port": addr[1] if len(addr) > 1 else None, "via": kind})
    raise ConnectionRefusedError(f"SAMVID offline guard: outbound connection to {host} blocked")

def install_guard():
    if _installed["v"]:
        return

    def connect(self, addr):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _check(addr, "socket.connect")
        return _orig_connect(self, addr)

    def connect_ex(self, addr):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _check(addr, "socket.connect_ex")
        return _orig_connect_ex(self, addr)

    def getaddrinfo(host, *a, **k):
        if isinstance(host, bytes):
            host = host.decode()
        if host in ALLOWED_HOSTS:
            res = _orig_getaddrinfo(host, *a, **k)
            with _lock:
                _allowed_ips.update(r[4][0] for r in res)
                ALLOWED_EXTERNAL.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                         "host": host, "purpose": "Firebase ID-token signing keys"})
                del ALLOWED_EXTERNAL[:-20]
            return res
        if host not in (None, "", "localhost") and not _is_ip_local(host):
            with _lock:
                BLOCKED.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "host": str(host), "port": a[0] if a else None, "via": "dns"})
            raise socket.gaierror(f"SAMVID offline guard: DNS lookup for {host} blocked")
        return _orig_getaddrinfo(host, *a, **k)
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo
    _installed["v"] = True

def _is_ip_local(host) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False

def self_test() -> dict:
    import urllib.request
    try:
        urllib.request.urlopen("https://example.com", timeout=2)
        return {"outbound_blocked": False}
    except Exception as e:  # noqa: BLE001
        return {"outbound_blocked": True, "error": f"{type(e).__name__}: {e}"[:160]}

def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()

def _ver(pkg):
    try:
        return md.version(pkg)
    except md.PackageNotFoundError:
        return None

def _rel(p) -> str:
    try:
        return str(p.relative_to(cfg.BACKEND_DIR))
    except (ValueError, AttributeError):
        return str(p)

def manifest() -> list[dict]:
    items = [
        {"component": "Tile embedder (built-in)", "kind": "model", "name": "spectral-texture descriptor",
         "version": "1.0", "license": "Team NullNVoid (MIT)", "location": "code: samvid/embed.py", "staged": True,
         "role": "active embedding backend for FAISS"},
        {"component": "Remote-sensing CLIP", "kind": "model weights", "name": "RemoteCLIP ViT-B/32",
         "version": "2023", "license": "Apache-2.0", "location": _rel(cfg.REMOTECLIP_WEIGHTS),
         "staged": cfg.REMOTECLIP_WEIGHTS.exists(), "role": "optional 512-d text+image embedder (drop weights in models/)"},
        {"component": "Agent LLM", "kind": "model weights", "name": cfg.OLLAMA_MODEL, "version": "2.5",
         "license": "Apache-2.0 (Qwen2.5-7B-Instruct)", "location": f"Ollama on {cfg.OLLAMA_URL} (loopback only)",
         "staged": None, "role": "query planning; rule-based planner is the offline fallback"},
        {"component": "Sign-in", "kind": "service", "name": "Firebase Authentication (Google)" if cfg.FIREBASE_ENABLED else "Local accounts (PBKDF2)",
         "version": "", "license": "Google ToS" if cfg.FIREBASE_ENABLED else "—",
         "location": f"project {cfg.FIREBASE_PROJECT_ID}" if cfg.FIREBASE_ENABLED else "data/samvid.db",
         "staged": True, "role": "identity; Firebase is optional and disabled in air-gapped mode"},
        {"component": "Change clustering", "kind": "library", "name": "scikit-learn HDBSCAN", "version": _ver("scikit-learn"),
         "license": "BSD-3-Clause", "location": "python site-packages", "staged": True, "role": "change objects + site discovery"},
        {"component": "Vector index", "kind": "library", "name": "faiss-cpu", "version": _ver("faiss-cpu"),
         "license": "MIT", "location": _rel(cfg.INDEX_DIR), "staged": True, "role": "on-prem ANN index, incremental adds"},
        {"component": "Raster I/O", "kind": "library", "name": "rasterio / GDAL", "version": _ver("rasterio"),
         "license": "BSD-3-Clause / MIT", "location": "python site-packages", "staged": True, "role": "GeoTIFF / COG ingestion"},
        {"component": "PDF reports", "kind": "library", "name": "ReportLab", "version": _ver("reportlab"),
         "license": "BSD-3-Clause", "location": "python site-packages", "staged": True, "role": "evidence report PDFs"},
        {"component": "API server", "kind": "library", "name": "FastAPI + Uvicorn", "version": _ver("fastapi"),
         "license": "MIT / BSD-3-Clause", "location": "python site-packages", "staged": True, "role": "local REST API"},
        {"component": "Archive data", "kind": "data", "name": "Synthetic S2/S1 archive (demo)", "version": "1",
         "license": "CC0 (generated)", "location": _rel(cfg.ARCHIVE_DIR), "staged": cfg.ARCHIVE_DIR.exists(),
         "role": "replace with the evaluation archive (Copernicus data: CC-BY-SA IGO 3.0 attribution)"},
    ]
    for it in items:
        if it["kind"] == "model weights" and it["staged"] and str(it["location"]).endswith(".pt"):
            it["sha256"] = _sha(cfg.BACKEND_DIR / it["location"])
    return items

def status() -> dict:
    fb = cfg.FIREBASE_ENABLED
    return {"guard_installed": _installed["v"], "blocked_attempts": len(BLOCKED), "blocked": BLOCKED[-20:],
            "mode": "online sign-in (Firebase)" if fb else "air-gapped",
            "allowed_hosts": sorted(ALLOWED_HOSTS), "allowed_external": ALLOWED_EXTERNAL[-10:],
            "loopback_connections": ALLOWED_COUNT["n"], "manifest": manifest(),
            "policy": ("Only loopback connections are permitted, plus " + ", ".join(sorted(ALLOWED_HOSTS)) +
                       " for verifying Google sign-in tokens. All other outbound traffic is refused."
                       if ALLOWED_HOSTS else
                       "Only loopback (127.0.0.1 / ::1) connections are permitted. DNS lookups for external hosts are refused."),
            "frontend": "all JS/CSS/fonts bundled locally by Vite - no CDN, no map tiles from the internet"}