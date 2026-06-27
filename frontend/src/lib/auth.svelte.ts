// 鉴权前端态 —— Svelte 5 runes 共享模块（非 store 的另一种写法）。
//
// 后端用 HttpOnly cookie 承载会话（JS 读不到、防 XSS 偷 token），故前端不持有 token：
// 登录调 /api/auth/login 由浏览器存 cookie，之后所有 /api 请求同源自动带 cookie。
// 本模块只跟踪登录态（供 UI 显登录/登出入口），不碰 token 本身。

import { browser } from "$app/environment";

type Ready = boolean | null; // null = 未探明

let loggedIn = $state<Ready>(null);
let authConfigured = $state<Ready>(null);
let booting = $state(true);

export const auth = {
  get loggedIn() {
    return loggedIn;
  },
  get authConfigured() {
    return authConfigured;
  },
  get booting() {
    return booting;
  },

  /** 启动时探一次：本服务自我健康 + 是否已登录。 */
  async init() {
    if (!browser) return;
    booting = true;
    try {
      const h = await fetch("/health").then((r) => r.json());
      authConfigured = !!h.auth_configured;
    } catch {
      authConfigured = null;
    }
    await this.refresh();
    booting = false;
  },

  /** 调 /api/auth/me 探登录态。401=未登录，200=已登录。未配密钥时 503 也算未登录。 */
  async refresh() {
    if (!browser) return;
    try {
      const r = await fetch("/api/auth/me", { credentials: "same-origin" });
      loggedIn = r.ok;
    } catch {
      loggedIn = false;
    }
  },

  /** 登录：成功后 cookie 由浏览器存好，刷新登录态。 */
  async login(secret: string): Promise<{ ok: boolean; detail?: string }> {
    const r = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ secret }),
    });
    if (r.ok) {
      loggedIn = true;
      return { ok: true };
    }
    loggedIn = false;
    return { ok: false, detail: r.status === 401 ? "密钥错误" : r.status === 503 ? "未配密钥" : `失败 ${r.status}` };
  },

  async logout() {
    await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
    loggedIn = false;
  },
};