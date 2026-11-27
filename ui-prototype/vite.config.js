import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

export default defineConfig({
  plugins: [svelte()],
  clearScreen: false,
  server: {
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: "index.html",
        showcase: "showcase.html",
        reviewDesk: "review-desk.html",
        unitEconomics: "unit-economics.html",
        consciousnessMap: "consciousness-map.html",
        xuanshuLab: "xuanshu-lab.html",
        evolutionLab: "evolution-lab.html",
        researchBridge: "research-bridge.html",
        videoFactory: "video-factory.html",
        portal: "portal.html",
        os: "os.html",
      },
    },
  },
});
