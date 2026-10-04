import { initializeApp } from "firebase/app";
import { getAuth, GoogleAuthProvider, signInWithPopup, signOut } from "firebase/auth";

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};

export const firebaseConfigured = Boolean(firebaseConfig.apiKey && firebaseConfig.projectId);

let auth = null;
function getFirebaseAuth() {
  if (!firebaseConfigured) throw new Error("Firebase is not configured (frontend/.env)");
  if (!auth) auth = getAuth(initializeApp(firebaseConfig));
  return auth;
}

export async function googleIdToken() {
  const provider = new GoogleAuthProvider();
  provider.setCustomParameters({ prompt: "select_account" });
  const result = await signInWithPopup(getFirebaseAuth(), provider);
  return result.user.getIdToken(/* forceRefresh */ true);
}

export async function firebaseSignOut() {
  if (firebaseConfigured) await signOut(getFirebaseAuth());
}
