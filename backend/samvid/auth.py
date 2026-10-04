from __future__ import annotations
import hashlib
import hmac
import os
import secrets
import time
from . import db

_DEMO_PW = os.environ.get("SAMVID_DEMO_PASSWORD", "samvid@123")
DEMO_USERS = [("analyst", "Analyst One", "analyst", _DEMO_PW),
              ("supervisor", "Supervisor", "supervisor", _DEMO_PW)]

def _hash(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()

def create_user(username, display, role, password):
    salt = os.urandom(16).hex()
    db.run("INSERT OR REPLACE INTO users VALUES (?,?,?,?,?)", (username, display, role, salt, _hash(password, salt)))

def seed():
    for u in DEMO_USERS:
        create_user(*u)  

def login(username: str, password: str):
    u = db.one("SELECT * FROM users WHERE username=?", (username,))
    if not u or not hmac.compare_digest(u["pw_hash"], _hash(password, u["salt"])):
        return None
    return issue_session(username)

def issue_session(username: str) -> dict:
    u = db.one("SELECT * FROM users WHERE username=?", (username,))
    token = secrets.token_urlsafe(32)
    db.run("INSERT INTO sessions VALUES (?,?,?)", (token, username, time.time() + 12 * 3600))
    return {"token": token, "user": {"username": u["username"], "name": u["display"], "role": u["role"]}}

def user_for(token: str | None):
    if not token:
        return None
    s = db.one("SELECT * FROM sessions WHERE token=?", (token,))
    if not s or s["expires"] < time.time():
        return None
    u = db.one("SELECT username, display, role FROM users WHERE username=?", (s["username"],))
    return {"username": u["username"], "name": u["display"], "role": u["role"]} if u else None

def logout(token: str):
    db.run("DELETE FROM sessions WHERE token=?", (token,))