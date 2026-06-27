import { sveltekit } from "@sveltejs/kit/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    host: "127.0.0.1", // 绝不 0.0.0.0：写记忆入口不公网
    port: 5173,
    // 开发期经 Vite 代理把后端接口转给 127.0.0.1:8761，避免 CORS/跨端口直连。
    proxy: {
      "/api": "http://127.0.0.1:8761",
      "/health": "http://127.0.0.1:8761",
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
  },
});