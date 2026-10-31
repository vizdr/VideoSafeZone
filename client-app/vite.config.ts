import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  // Relative asset paths: the same build is served from /app/ until the cutover and from the
  // root after it (WEB-PANEL-PLAN.md D2). Routes live in the hash, so CloudFront never routes.
  base: "./",
  plugins: [vue()],
  build: {
    target: "es2022",
    // hls.js alone is ~575 kB and is split off, loaded with the first player (camera-controller.ts);
    // the warning would otherwise flag that lazy chunk on every build.
    chunkSizeWarningLimit: 600,
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.spec.ts"],
    setupFiles: ["tests/setup.ts"],
  },
});
