from __future__ import annotations
import threading
import numpy as np
from . import config as cfg

try:
    import faiss  
    HAS_FAISS = True
except ImportError: 
    HAS_FAISS = False
_lock = threading.Lock()
INDEX_FILE = cfg.INDEX_DIR / "tiles.faiss"
NP_FILE = cfg.INDEX_DIR / "tiles.npz"

class VectorIndex:
    def __init__(self, dim: int):
        self.dim = dim
        self.backend = "faiss.IndexIDMap2(IndexFlatIP)" if HAS_FAISS else "numpy-flat"
        self._ids = np.zeros(0, np.int64)
        self._vecs = np.zeros((0, dim), np.float32)
        self.index = faiss.IndexIDMap2(faiss.IndexFlatIP(dim)) if HAS_FAISS else None
        self.add_events = []

    def save(self):
        with _lock:
            if HAS_FAISS:
                faiss.write_index(self.index, str(INDEX_FILE))
            np.savez(NP_FILE, ids=self._ids, vecs=self._vecs)

    @classmethod
    def load(cls, dim: int):
        idx = cls(dim)
        if NP_FILE.exists():
            z = np.load(NP_FILE)
            if z["vecs"].shape[1:] == (dim,):
                idx._ids, idx._vecs = z["ids"], z["vecs"]
                if HAS_FAISS:
                    if INDEX_FILE.exists():
                        idx.index = faiss.read_index(str(INDEX_FILE))
                    else:
                        idx.index.add_with_ids(idx._vecs, idx._ids)
        return idx

    @property
    def ntotal(self) -> int:
        return int(self._ids.size)

    def add(self, ids: np.ndarray, vecs: np.ndarray):
        ids = np.asarray(ids, np.int64)
        vecs = np.ascontiguousarray(vecs, np.float32)
        with _lock:
            if HAS_FAISS:
                self.index.add_with_ids(vecs, ids)
            self._ids = np.concatenate([self._ids, ids])
            self._vecs = np.concatenate([self._vecs, vecs])
        self.add_events.append(int(ids.size))

    def vector(self, id_: int):
        hit = np.where(self._ids == id_)[0]
        return self._vecs[hit[0]] if hit.size else None

    def search(self, q: np.ndarray, k: int = 50, allowed: np.ndarray | None = None):
        q = np.ascontiguousarray(q.reshape(1, -1), np.float32)
        if self.ntotal == 0:
            return np.zeros(0, np.int64), np.zeros(0, np.float32)
        k = min(k, self.ntotal)
        if HAS_FAISS:
            params = None
            if allowed is not None:
                if len(allowed) == 0:
                    return np.zeros(0, np.int64), np.zeros(0, np.float32)
                sel = faiss.IDSelectorBatch(np.asarray(allowed, np.int64))
                params = faiss.SearchParameters(sel=sel)
                k = min(k, len(allowed))
            D, I = self.index.search(q, k, params=params)
            keep = I[0] >= 0
            return I[0][keep], D[0][keep]
        mask = np.ones(self.ntotal, bool) if allowed is None else np.isin(self._ids, allowed)
        sims = self._vecs[mask] @ q[0]
        order = np.argsort(-sims)[:k]
        return self._ids[mask][order], sims[order]
