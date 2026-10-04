from __future__ import annotations
import hashlib
import json
import shutil
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from . import config as cfg

GENESIS = "0" * 64
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    seq            INTEGER PRIMARY KEY,
    ts             TEXT NOT NULL,
    actor          TEXT NOT NULL,
    action         TEXT NOT NULL,
    subject        TEXT NOT NULL,
    payload        TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    prev_hash      TEXT NOT NULL,
    entry_hash     TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_ledger_subject ON ledger(subject);
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
"""

def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()

def _entry_hash(seq, ts, actor, action, subject, payload_sha, prev_hash) -> str:
    return sha256(f"{seq}|{ts}|{actor}|{action}|{subject}|{payload_sha}|{prev_hash}")

def connect(path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(path or cfg.LEDGER_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con

def append(actor: str, action: str, subject: str, payload: dict) -> dict:
    with _lock:
        con = connect()
        try:
            last = con.execute("SELECT seq, entry_hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
            seq = (last["seq"] + 1) if last else 1
            prev = last["entry_hash"] if last else GENESIS
            ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            body = canonical(payload)
            p_sha = sha256(body)
            e_hash = _entry_hash(seq, ts, actor, action, subject, p_sha, prev)
            con.execute(
                "INSERT INTO ledger VALUES (?,?,?,?,?,?,?,?,?)",
                (seq, ts, actor, action, subject, body, p_sha, prev, e_hash),
            )
            con.commit()
        finally:
            con.close()
    return {"seq": seq, "ts": ts, "actor": actor, "action": action, "subject": subject,
            "payload_sha256": p_sha, "prev_hash": prev, "entry_hash": e_hash}

def _row(r) -> dict:
    d = dict(r)
    d["payload"] = json.loads(d["payload"])
    return d

def entries(limit=200, offset=0, subject=None, action=None) -> list[dict]:
    con = connect()
    try:
        q, args = "SELECT * FROM ledger", []
        cond = []
        if subject:
            cond.append("subject LIKE ?")
            args.append(f"%{subject}%")
        if action:
            cond.append("action = ?")
            args.append(action)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY seq DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        return [_row(r) for r in con.execute(q, args)]
    finally:
        con.close()

def stats() -> dict:
    con = connect()
    try:
        n = con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        head = con.execute("SELECT seq, entry_hash, ts FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
        by_action = {r[0]: r[1] for r in con.execute("SELECT action, COUNT(*) FROM ledger GROUP BY action")}
        return {"entries": n, "head_seq": head["seq"] if head else 0,
                "head_hash": head["entry_hash"] if head else GENESIS,
                "head_ts": head["ts"] if head else None, "by_action": by_action}
    finally:
        con.close()

def verify(path: Path | None = None) -> dict:
    con = connect(path)
    try:
        prev = GENESIS
        n = 0
        for r in con.execute("SELECT * FROM ledger ORDER BY seq"):
            n += 1
            p_sha = sha256(r["payload"])
            if p_sha != r["payload_sha256"]:
                return {"valid": False, "checked": n, "broken_at": r["seq"],
                        "reason": "payload hash mismatch - payload was modified after it was written"}
            if r["prev_hash"] != prev:
                return {"valid": False, "checked": n, "broken_at": r["seq"],
                        "reason": "chain link mismatch - an earlier entry was altered, inserted or removed"}
            expect = _entry_hash(r["seq"], r["ts"], r["actor"], r["action"], r["subject"], p_sha, prev)
            if expect != r["entry_hash"]:
                return {"valid": False, "checked": n, "broken_at": r["seq"],
                        "reason": "entry hash mismatch - entry metadata was modified"}
            prev = r["entry_hash"]
        return {"valid": True, "checked": n, "head_hash": prev}
    finally:
        con.close()

def tamper_demo(seq: int | None = None) -> dict:
    tmpdir = Path(tempfile.mkdtemp(prefix="samvid-tamper-"))
    copy = tmpdir / "ledger-copy.db"
    shutil.copy(cfg.LEDGER_PATH, copy)
    con = sqlite3.connect(copy)
    try:
        total = con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        if total < 2:
            return {"error": "ledger too short for a demo"}
        target = seq or max(1, total // 2)
        row = con.execute("SELECT action, payload FROM ledger WHERE seq=?", (target,)).fetchone()
        payload = json.loads(row[1])
        payload["_tampered"] = "confidence raised / decision flipped after the fact"
        if "decision" in payload:
            payload["decision"] = "rejected" if payload["decision"] == "confirmed" else "confirmed"
        con.executescript("DROP TRIGGER ledger_no_update;")
        con.execute("UPDATE ledger SET payload=? WHERE seq=?", (canonical(payload), target))
        con.commit()
    finally:
        con.close()
    before = verify()
    after = verify(copy)
    shutil.rmtree(tmpdir, ignore_errors=True)
    return {"tampered_seq": target, "tampered_action": row[0], "real_ledger": before, "tampered_copy": after}

def export(subject: str | None = None) -> dict:
    con = connect()
    try:
        if subject:
            rows = con.execute("SELECT * FROM ledger WHERE subject LIKE ? ORDER BY seq", (f"%{subject}%",)).fetchall()
        else:
            rows = con.execute("SELECT * FROM ledger ORDER BY seq").fetchall()
    finally:
        con.close()
    return {"exported_at": datetime.now(timezone.utc).isoformat(), "verification": verify(),
            "ledger_head": stats()["head_hash"], "entries": [_row(r) for r in rows],
            "how_to_verify": "For each entry in seq order: payload_sha256 == sha256(canonical(payload)); "
                             "entry_hash == sha256('seq|ts|actor|action|subject|payload_sha256|prev_hash'); "
                             "prev_hash == previous entry_hash (genesis = 64 zeros)."}
