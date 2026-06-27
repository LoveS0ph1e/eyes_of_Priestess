// SvelteKit 根布局。runes 写法见 +page.svelte 全局占位首页。
export const prerender = true;
export const ssr = false; // 记忆管理是客户端态为主的 CRUD，SPA 模式更简单