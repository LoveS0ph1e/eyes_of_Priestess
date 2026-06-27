import adapter from "@sveltejs/adapter-static";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    // 静态产物，由后端 FastAPI 托管或经 SSH 隧道访问。
    // 开发期代理在 vite.config.ts 的 server.proxy（Vite 才认），勿在此配。
    adapter: adapter({ fallback: "index.html" }),
    prerender: {
      // 无 favicon 时不致命（仅 warn），避免 SPA 预渲染因缺链断 build。
      handleHttpError: ({ path, message }) => {
        if (path === "/favicon.png") return;
        throw new Error(message);
      },
    },
  },
};

export default config;