import "../css/style.css";
import { API, session } from "../js/api.js";

if (session.get()) location.href = "dashboard.html";
const err = document.getElementById("err");
const showErr = (m) => { err.textContent = m; err.classList.remove("hidden"); };

document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  err.classList.add("hidden");
  try {
    session.set({ ...(await API.login(fd.get("username"), fd.get("password"))), method: "local" });
    location.href = "dashboard.html";
  } catch (x) {
    showErr(x.message === "invalid credentials" ? "Invalid username or password" : `Sign-in failed: ${x.message}`);
  }
});

(async () => {
  let cfg = { google: false };
  try { cfg = await API.authConfig(); } catch { /* backend down: local form will show the error */ }
  let fb;
  try { fb = await import("../js/firebase.js"); } catch (e) { console.error("Firebase failed to load", e); return; }
  if (!(cfg.google && fb.firebaseConfigured)) return;
  document.getElementById("google-wrap").classList.remove("hidden");
  const btn = document.getElementById("google");
  btn.onclick = async () => {
    err.classList.add("hidden");
    btn.disabled = true;
    try {
      const idToken = await fb.googleIdToken();
      session.set({ ...(await API.loginGoogle(idToken)), method: "google" });
      location.href = "dashboard.html";
    } catch (x) {
      const code = x.code || "";
      if (code === "auth/popup-closed-by-user" || code === "auth/cancelled-popup-request") showErr("Google sign-in was cancelled");
      else if (code === "auth/unauthorized-domain") showErr("This domain is not authorised in Firebase → Authentication → Settings → Authorized domains");
      else if (code === "auth/operation-not-allowed") showErr("Enable the Google provider in Firebase → Authentication → Sign-in method");
      else showErr(`Google sign-in failed: ${x.message}`);
      try { await fb.firebaseSignOut(); } catch { /* ignore */ }
    } finally { btn.disabled = false; }
  };
})();