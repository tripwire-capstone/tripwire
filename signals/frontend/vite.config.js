import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In Docker, compose sets API_URL=http://api:8100. Run directly, the API is on localhost.
const api = process.env.API_URL ?? 'http://127.0.0.1:8100';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': api,
    },
  },
  preview: {
    proxy: {
      '/api': api,
    },
  },
});
