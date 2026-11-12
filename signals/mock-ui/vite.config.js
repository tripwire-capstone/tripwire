import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Port 5174 so it doesn't clash with the prototype frontend on 5173.
// /api goes to the Tripwire backend (docker compose in signals/backend).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: { "/api": "http://localhost:8080" },
  },
});
