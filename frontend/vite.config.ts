import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev mode proxies API calls to the local backend; in production nginx
// handles the same /api proxy.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
