// 后端 API 客户端 —— 极薄 fetch 封装。
//
// 所有 /api 请求同源带 cookie（HttpOnly 会话由浏览器自动管理）。
// 后端鉴权：写接口需登录（401），未配密钥 503，只读接口鉴权可选。
// 调用方按 status 处理——这里不吞错误、只解 JSON。

export class ApiError extends Error {
  status: number;
  detail?: string;
  constructor(status: number, detail?: string) {
    super(`API ${status}: ${detail ?? ""}`);
    this.status = status;
    this.detail = detail;
  }
}

async function req(
  url: string,
  init?: RequestInit & { json?: unknown },
): Promise<any> {
  const headers = new Headers(init?.headers);
  let body = init?.body;
  if (init?.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  const r = await fetch(url, {
    ...init,
    headers,
    body,
    credentials: "same-origin",
  });
  let data: any = null;
  const text = await r.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!r.ok) {
    throw new ApiError(r.status, data?.detail);
  }
  return data;
}

export const api = {
  get: (u: string) => req(u),
  post: (u: string, json?: unknown) => req(u, { method: "POST", json }),
  put: (u: string, json: unknown) => req(u, { method: "PUT", json }),
  del: (u: string, json?: unknown) => req(u, { method: "DELETE", json }),
};

// ── covenant 资源 ──────────────────────────────────────────────────

export interface Covenant {
  user_id: string;
  text: string;
}

export const covenant = {
  list: () => api.get("/api/covenant") as Promise<Covenant[]>,
  upsert: (user_id: string, text: string) =>
    api.put(`/api/covenant/${encodeURIComponent(user_id)}`, { user_id, text }),
  delete: (user_id: string) => api.del(`/api/covenant/${encodeURIComponent(user_id)}`),
};

// ── view 只读资源（对接 readonly.py；开发期 Mock 返空，真机 B5 后出真数据）──

// 字段对齐后端 everos_gateway 的 ProfileDTO / EpisodeDTO（B5 真机坐实）
// explicit/implicit 是结构化对象数组（非字符串）：
//   explicit 项 = {category, description, evidence}
//   implicit 项 = {trait, description, evidence, basis}
export interface ProfileView {
  user_id: string;
  summary: string;
  explicit: Array<Record<string, unknown>>;
  implicit: Array<Record<string, unknown>>;
  raw: Record<string, unknown>;
}

export interface EpisodeView {
  entry_id: string;
  summary: string;
  subject: string;
  timestamp: string;
  raw: Record<string, unknown>;
}

export interface EpisodePage {
  items: EpisodeView[];
  total: number;
  page: number;
  page_size: number;
}

// entry 删除（第二期，对接 readonly.py 的 plan/delete/reindex 三端点）
export type ReindexMode = "incremental" | "full";

export interface EpisodeDeletePreview {
  plan_id: string;
  is_empty: boolean;
  render: string; // Palimpsest Plan.render() 的纯文本 diff，直接展示给 admin 确认
}

export interface EpisodeDeleteResult {
  txn: string;
  status: string;
  reindex_mode: ReindexMode;
  reindex_paths: string[];
}

const APP_ID = "astrbot";
const PROJECT_ID = "default";

// 后端只读端点统一返回 {data, meta} 信封（见 readonly.py）；这里解包成干净的领域类型，
// 让页面组件不感知传输层信封。
export const view = {
  health: () => api.get("/api/view/health") as Promise<{ healthy: boolean }>,
  async profile(uid: string): Promise<ProfileView | null> {
    const r = await api.get(
      `/api/view/profile/${encodeURIComponent(uid)}?app_id=${APP_ID}&project_id=${PROJECT_ID}`,
    );
    return (r.data ?? null) as ProfileView | null;
  },
  async episodes(uid: string, page = 1, pageSize = 20): Promise<EpisodePage> {
    const r = await api.get(
      `/api/view/episodes/${encodeURIComponent(uid)}?app_id=${APP_ID}&project_id=${PROJECT_ID}&page=${page}&page_size=${pageSize}`,
    );
    const meta = r.meta ?? {};
    return {
      items: (r.data ?? []) as EpisodeView[],
      total: meta.total ?? 0,
      page: meta.page ?? page,
      page_size: meta.page_size ?? pageSize,
    };
  },
  // search：query/top_k 走 query string，身份走 body（与后端 readonly.search 签名一致）
  async search(
    uid: string,
    query: string,
    topK = 5,
  ): Promise<{ episodes?: unknown[]; profiles?: unknown[]; [k: string]: unknown }> {
    const r = await api.post(
      `/api/view/search?query=${encodeURIComponent(query)}&top_k=${topK}`,
      { user_id: uid, app_id: APP_ID, project_id: PROJECT_ID },
    );
    return (r.data ?? {}) as { episodes?: unknown[]; profiles?: unknown[]; [k: string]: unknown };
  },
  // entry_id 传 EverOS API 的复合形式（{owner_id}_{entry_id}，即 EpisodeView.entry_id
  // 原样传入）——后端 strip_owner_prefix 负责剥离，前端不用自己拼/剥。
  async planDeleteEpisode(uid: string, entryId: string): Promise<EpisodeDeletePreview> {
    const r = await api.get(
      `/api/view/episodes/${encodeURIComponent(uid)}/${encodeURIComponent(entryId)}/plan`,
    );
    return r.data as EpisodeDeletePreview;
  },
  async deleteEpisode(
    uid: string,
    entryId: string,
    reindexMode: ReindexMode = "incremental",
  ): Promise<EpisodeDeleteResult> {
    const r = await api.del(
      `/api/view/episodes/${encodeURIComponent(uid)}/${encodeURIComponent(entryId)}`,
      { reindex_mode: reindexMode },
    );
    return r.data as EpisodeDeleteResult;
  },
  async reindexEpisodeTxn(uid: string, txn: string): Promise<unknown> {
    const r = await api.post(`/api/view/episodes/${encodeURIComponent(uid)}/reindex/${encodeURIComponent(txn)}`);
    return r.data;
  },
};