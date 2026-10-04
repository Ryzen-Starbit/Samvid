from __future__ import annotations
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

def _load_dotenv(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_dotenv(BACKEND_DIR / ".env")
DATA_DIR = Path(os.environ.get("SAMVID_DATA_DIR", BACKEND_DIR / "data"))
ARCHIVE_DIR = DATA_DIR / "archive"        
DERIVED_DIR = DATA_DIR / "derived"         
INDEX_DIR = DATA_DIR / "index"             
MODELS_DIR = Path(os.environ.get("SAMVID_MODELS_DIR", BACKEND_DIR / "models"))
DB_PATH = DATA_DIR / "samvid.db"          
LEDGER_PATH = DATA_DIR / "ledger.db"        
GROUND_TRUTH_PATH = DATA_DIR / "ground_truth.json"
for _d in (DATA_DIR, ARCHIVE_DIR, DERIVED_DIR, INDEX_DIR, MODELS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

TILE_PX = 32            
SCENE_PX = 256             
GSD_M = 10.0
MIN_USABLE_FRACTION = 0.70  
MAX_REGISTRATION_ERROR_PX = 0.75   
CHANGE_Z_THRESHOLD = 3.0    
MIN_CHANGE_OBJECT_PX = 12    
OLLAMA_URL = os.environ.get("SAMVID_OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("SAMVID_OLLAMA_MODEL", "qwen2.5:7b-instruct")
EMBEDDER = os.environ.get("SAMVID_EMBEDDER", "auto")
REMOTECLIP_WEIGHTS = MODELS_DIR / "RemoteCLIP-ViT-B-32.pt"
SECRET_KEY = os.environ.get("SAMVID_SECRET", "samvid-local-dev-secret-change-me")

FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "").strip()
FIREBASE_ENABLED = bool(FIREBASE_PROJECT_ID)
ALLOWED_EMAILS = {e.strip().lower() for e in os.environ.get("SAMVID_ALLOWED_EMAILS", "").split(",") if e.strip()}
ALLOWED_DOMAINS = {d.strip().lower().lstrip("@") for d in os.environ.get("SAMVID_ALLOWED_DOMAINS", "").split(",") if d.strip()}
SUPERVISOR_EMAILS = {e.strip().lower() for e in os.environ.get("SAMVID_SUPERVISOR_EMAILS", "").split(",") if e.strip()}
FIREBASE_CERT_HOSTS = {"www.googleapis.com"}
