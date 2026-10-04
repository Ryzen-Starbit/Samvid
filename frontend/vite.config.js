import { defineConfig } from "vite";

const pages = ["index", "dashboard", "search", "change", "review", "discovery", "ledger", "system"];

export default defineConfig({
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
  preview: { port: 4173, proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { rollupOptions: { input: Object.fromEntries(pages.map((p) => [p, `${p}.html`])) } },
});
