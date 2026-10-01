/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The build lands inside the Python package so `cerebellum ui` needs no Node at runtime.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // One bundle (~180 kB gzipped) served from localhost; splitting would not make it load faster.
  build: { outDir: "../src/cerebellum/server/static", emptyOutDir: true, chunkSizeWarningLimit: 800 },
  server: { proxy: { "/api": "http://127.0.0.1:7400" } },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
