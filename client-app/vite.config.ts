import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  // Relative asset paths: the same build is served from /app/ until the cutover and from the
  // root after it (WEB-PANEL-PLAN.md D2). Routes live in the hash, so CloudFront never routes.
  base: "./",
  plugins: [vue()],
  build: { target: "es2022" },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.spec.ts"],
  },
});
