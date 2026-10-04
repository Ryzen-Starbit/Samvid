from __future__ import annotations
import os
from . import auth, config as cfg, db
try:
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token as g_id_token
    HAS_GOOGLE_AUTH = True
except ImportError:  
    HAS_GOOGLE_AUTH = False
_session = None

class FirebaseAuthError(Exception):
    pass

def verify(token: str) -> dict:
    global _session
    if not cfg.FIREBASE_ENABLED:
        raise FirebaseAuthError("Google sign-in is not enabled on this server (FIREBASE_PROJECT_ID not set)")
    if not HAS_GOOGLE_AUTH:
        raise FirebaseAuthError("google-auth is not installed: pip install google-auth requests")
    if _session is None:
        import requests
        _session = requests.Session()
        _session.trust_env = os.environ.get("SAMVID_USE_PROXY", "0") == "1"   # no surprise proxies
    try:
        claims = g_id_token.verify_firebase_token(token, g_requests.Request(session=_session),
                                                  audience=cfg.FIREBASE_PROJECT_ID, clock_skew_in_seconds=10)
    except Exception as e:  # noqa: BLE001 - any verification problem is a 401
        raise FirebaseAuthError(f"token rejected: {e}") from e
    if not claims:
        raise FirebaseAuthError("token rejected")
    return claims

def allowed(email: str) -> bool:
    email = email.lower()
    if not cfg.ALLOWED_EMAILS and not cfg.ALLOWED_DOMAINS:
        return True
    return email in cfg.ALLOWED_EMAILS or email.split("@")[-1] in cfg.ALLOWED_DOMAINS

def login_with_token(token: str) -> dict:
    claims = verify(token)
    email = (claims.get("email") or "").lower()
    if not email or not claims.get("email_verified", False):
        raise FirebaseAuthError("Google account has no verified email")
    if not allowed(email):
        raise PermissionError(f"{email} is not on this deployment's allow-list")
    name = claims.get("name") or email.split("@")[0]
    role = "supervisor" if email in cfg.SUPERVISOR_EMAILS else "analyst"
    existing = db.one("SELECT role FROM users WHERE username=?", (email,))
    if existing:
        db.run("UPDATE users SET display=?, role=? WHERE username=?",
               (name, role if email in cfg.SUPERVISOR_EMAILS else existing["role"], email))
    else:
        db.run("INSERT INTO users VALUES (?,?,?,?,?)", (email, name, role, "00", "!"))
    sess = auth.issue_session(email)
    sess["user"]["picture"] = claims.get("picture")
    sess["user"]["email"] = email
    sess["claims"] = {"uid": claims.get("user_id") or claims.get("sub"),
                      "provider": (claims.get("firebase") or {}).get("sign_in_provider")}
    return sess
