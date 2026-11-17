import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Port 5174 so it doesn't clash with the prototype frontend on 5173.
// /api goes to the Tripwire backend (docker compose in signals/backend).
// The build uses relative asset paths ("./assets/..."); index.html's <base href> decides where they
// resolve, so the same build works at "/" or under a prefix like "/tripwire/" (src/basePath.js).
export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === "build" ? "./" : "/",
  server: {
    port: 5174,
    proxy: { "/api": "http://localhost:8080" },
  },
}));
