import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In the production image the built assets are served by the FastAPI
// process itself; `npm run dev` proxies API calls to localhost:8000.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
});
