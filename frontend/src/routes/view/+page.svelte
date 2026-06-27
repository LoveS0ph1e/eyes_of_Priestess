<script lang="ts">
  // 画像 / 情景只读页（第一期）—— 对接后端只读路由（readonly.py）。
  // 只读接口鉴权可选：未登录也能看。开发期走 MockEverOSGateway（返空），各块显占位；
  // 接入真 EverOS（后端换 HTTPEverOSGateway）后即出真数据，本页不需重改。
  import { onMount } from "svelte";
  import { view, ApiError, type ProfileView, type EpisodeView } from "$lib/api";
  import Modal from "$lib/Modal.svelte";

  // 默认不预填 user_id，按需输入
  let uid = $state("");

  let health = $state<"idle" | "ok" | "fail">("idle");

  let profile = $state<ProfileView | null>(null);
  let profileLoaded = $state(false);
  let profileBusy = $state(false);

  let episodes = $state<EpisodeView[]>([]);
  let epTotal = $state(0);
  let epPage = $state(1);
  const EP_PAGE_SIZE = 10;
  let epLoaded = $state(false);
  let epBusy = $state(false);

  let query = $state("");
  let searchResult = $state<{ episodes?: unknown[]; profiles?: unknown[] } | null>(null);
  let searchBusy = $state(false);

  // 失败弹窗（共享 Modal notice 型）
  let failOpen = $state(false);
  let failMsg = $state("失败了失败了失败了");

  onMount(async () => {
    try {
      const h = await view.health();
      health = h.healthy ? "ok" : "fail";
    } catch {
      health = "fail";
    }
  });

  function fail(e: unknown) {
    failMsg = errMsg(e);
    failOpen = true;
  }

  function errMsg(e: unknown): string {
    if (e instanceof ApiError) {
      if (e.status === 401) return "未登录或会话过期";
      if (e.status === 400) return "身份非法：" + (e.detail ?? "");
      if (e.status === 502) return "EverOS 不可达：" + (e.detail ?? "");
      return `失败 ${e.status}：${e.detail ?? ""}`;
    }
    return "失败了失败了失败了";
  }

  async function loadProfile() {
    if (!uid.trim()) return;
    profileBusy = true;
    try {
      profile = await view.profile(uid.trim());
      profileLoaded = true;
    } catch (e) {
      fail(e);
    } finally {
      profileBusy = false;
    }
  }

  async function loadEpisodes(page = 1) {
    if (!uid.trim()) return;
    epBusy = true;
    try {
      const r = await view.episodes(uid.trim(), page, EP_PAGE_SIZE);
      episodes = r.items;
      epTotal = r.total;
      epPage = r.page;
      epLoaded = true;
    } catch (e) {
      fail(e);
    } finally {
      epBusy = false;
    }
  }

  async function doSearch() {
    if (!uid.trim() || !query.trim()) return;
    searchBusy = true;
    try {
      searchResult = await view.search(uid.trim(), query.trim());
    } catch (e) {
      fail(e);
    } finally {
      searchBusy = false;
    }
  }

  const epPages = $derived(Math.max(1, Math.ceil(epTotal / EP_PAGE_SIZE)));
</script>

<h2>画像 / 情景只读</h2>

<div class="health">
  EverOS 健康：
  {#if health === "ok"}<span class="ok">✅ 在线</span>
  {:else if health === "fail"}<span class="err">❌ 不可达</span>
  {:else}…{/if}
</div>

<div class="uid-row">
  <label>
    user_id（QQ 号）
    <input bind:value={uid} placeholder="如 10001" />
  </label>
</div>

<!-- ① 画像 -->
<section class="block">
  <div class="block-head">
    <h3>画像</h3>
    <button onclick={loadProfile} disabled={profileBusy || !uid.trim()}>
      {profileBusy ? "查询中…" : "查画像"}
    </button>
  </div>
  {#if !profileLoaded}
    <p class="hint">点「查画像」加载。</p>
  {:else if !profile}
    <p class="hint">暂无画像（开发期 Mock 返空；接入真 EverOS 后出真数据）。</p>
  {:else}
    <dl class="profile">
      <dt>Summary</dt>
      <dd>{profile.summary || "（空）"}</dd>
      <dt>显式信息</dt>
      <dd>
        {#if profile.explicit.length}
          <ul class="items">
            {#each profile.explicit as x}
              <li><b>{x.category ?? "—"}</b>：{x.description ?? ""}</li>
            {/each}
          </ul>
        {:else}（空）{/if}
      </dd>
      <dt>隐式特质</dt>
      <dd>
        {#if profile.implicit.length}
          <ul class="items">
            {#each profile.implicit as x}
              <li><b>{x.trait ?? "—"}</b>：{x.description ?? ""}</li>
            {/each}
          </ul>
        {:else}（空）{/if}
      </dd>
    </dl>
  {/if}
</section>

<!-- ② episode 列表 -->
<section class="block">
  <div class="block-head">
    <h3>情景记忆（episode）</h3>
    <button onclick={() => loadEpisodes(1)} disabled={epBusy || !uid.trim()}>
      {epBusy ? "查询中…" : "查 episode"}
    </button>
  </div>
  {#if !epLoaded}
    <p class="hint">点「查 episode」加载。</p>
  {:else if episodes.length === 0}
    <p class="hint">暂无 episode（开发期 Mock 返空；接入真 EverOS 后出真数据）。</p>
  {:else}
    <table>
      <thead><tr><th>时间</th><th>标题</th><th>摘要</th></tr></thead>
      <tbody>
        {#each episodes as ep (ep.entry_id)}
          <tr>
            <td class="mono">{ep.timestamp}</td>
            <td>{ep.subject || "—"}</td>
            <td>{ep.summary}</td>
          </tr>
        {/each}
      </tbody>
    </table>
    <div class="pager">
      <button onclick={() => loadEpisodes(epPage - 1)} disabled={epBusy || epPage <= 1}>上一页</button>
      <span>{epPage} / {epPages}（共 {epTotal}）</span>
      <button onclick={() => loadEpisodes(epPage + 1)} disabled={epBusy || epPage >= epPages}>下一页</button>
    </div>
  {/if}
</section>

<!-- ③ 检索（/epk 可视化） -->
<section class="block">
  <div class="block-head">
    <h3>检索预览（/epk search）</h3>
  </div>
  <div class="search-row">
    <input bind:value={query} placeholder="检索词…" onkeydown={(e) => e.key === "Enter" && doSearch()} />
    <button onclick={doSearch} disabled={searchBusy || !uid.trim() || !query.trim()}>
      {searchBusy ? "检索中…" : "检索"}
    </button>
  </div>
  {#if searchResult}
    {@const eps = searchResult.episodes ?? []}
    {@const profs = searchResult.profiles ?? []}
    {#if eps.length === 0 && profs.length === 0}
      <p class="hint">无命中（开发期 Mock 返空；接入真 EverOS 后出真数据）。</p>
    {:else}
      <p class="hint">命中 episode {eps.length} 条、profile {profs.length} 条。原始返回：</p>
      <pre>{JSON.stringify(searchResult, null, 2)}</pre>
    {/if}
  {/if}
</section>

<Modal bind:open={failOpen} variant="notice" tone="err" message={failMsg} />

<style>
  .health {
    margin: 0.5rem 0 1rem;
  }
  .uid-row {
    margin-bottom: 1rem;
  }
  label {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    max-width: 320px;
  }
  input {
    padding: 0.4rem;
    font-size: 0.95rem;
  }
  .block {
    border: 1px solid #ddd;
    border-radius: 6px;
    padding: 0.8rem 1rem;
    margin-bottom: 1rem;
  }
  .block-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.5rem;
  }
  .block-head h3 {
    margin: 0;
    font-size: 1.05rem;
  }
  .profile dt {
    font-weight: 600;
    margin-top: 0.5rem;
  }
  .profile dd {
    margin: 0.2rem 0 0;
  }
  table {
    border-collapse: collapse;
    width: 100%;
  }
  th,
  td {
    border: 1px solid #ddd;
    padding: 0.3rem 0.6rem;
    text-align: left;
    font-size: 0.9rem;
  }
  .mono {
    font-family: monospace;
  }
  .pager {
    display: flex;
    align-items: center;
    gap: 0.8rem;
    margin-top: 0.6rem;
    font-size: 0.9rem;
  }
  .search-row {
    display: flex;
    gap: 0.6rem;
  }
  .search-row input {
    flex: 1;
  }
  pre {
    background: #f6f6f6;
    padding: 0.6rem;
    overflow: auto;
    font-size: 0.8rem;
    max-height: 300px;
  }
  .hint {
    color: #888;
    font-size: 0.88rem;
  }
  .ok {
    color: #2a7;
  }
  .err {
    color: #c44;
  }
</style>