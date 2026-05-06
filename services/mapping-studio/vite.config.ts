import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/migration-harness/explore/",
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom"],
          flow: ["@xyflow/react"],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/migration-harness/api": "http://localhost:8088",
      "/migration-harness/result": "http://localhost:8088",
    },
  },
});
