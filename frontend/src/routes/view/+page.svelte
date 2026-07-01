<script lang="ts">
  // 画像 / 情景只读页（第一期）—— 对接后端只读路由（readonly.py）。
  // 只读接口鉴权可选：未登录也能看。开发期走 MockEverOSGateway（返空），各块显占位；
  // 真机抓到 EverOS 真实返回、后端换 HTTPEverOSGateway 后即出真数据，本页不需重改。
  import { onMount } from "svelte";
  import {
    view,
    ApiError,
    type ProfileView,
    type EpisodeView,
    type ReindexMode,
    type EpisodeDeleteResult,
  } from "$lib/api";
  import Modal from "$lib/Modal.svelte";

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

  // episode 删除（第二期）：先 plan 预览、admin 确认后再 apply。
  // deleteState 承载「正在为哪条 entry 走确认流程」，null = 未在流程中。
  let deleteState = $state<{
    entryId: string;
    preview: string;
    isEmpty: boolean;
    reindexMode: ReindexMode;
    busy: boolean;
  } | null>(null);
  let deleteResultOpen = $state(false);
  let deleteResult = $state<EpisodeDeleteResult | null>(null);

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
      if (e.status === 404) return "未找到：" + (e.detail ?? "");
      if (e.status === 409) return "冲突：" + (e.detail ?? "");
      if (e.status === 502) return "EverOS 不可达：" + (e.detail ?? "");
      if (e.status === 503) return "未配置：" + (e.detail ?? "");
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

  // 点删除 -> 先 plan 预览（零写入），预览成功才进入确认弹窗；
  // plan 本身失败（如未停机之外的错误）直接走失败提示，不进弹窗。
  async function startDelete(entryId: string) {
    if (!uid.trim()) return;
    try {
      const preview = await view.planDeleteEpisode(uid.trim(), entryId);
      deleteState = {
        entryId,
        preview: preview.render,
        isEmpty: preview.is_empty,
        reindexMode: "incremental",
        busy: false,
      };
    } catch (e) {
      fail(e);
    }
  }

  function cancelDelete() {
    deleteState = null;
  }

  async function confirmDelete() {
    if (!deleteState || !uid.trim()) return;
    deleteState.busy = true;
    try {
      deleteResult = await view.deleteEpisode(uid.trim(), deleteState.entryId, deleteState.reindexMode);
      deleteState = null;
      deleteResultOpen = true;
      await loadEpisodes(epPage); // 刷新当页列表，让已删条目消失
    } catch (e) {
      deleteState = null;
      fail(e);
    }
  }

  async function syncReindex() {
    if (!deleteResult || !uid.trim()) return;
    try {
      await view.reindexEpisodeTxn(uid.trim(), deleteResult.txn);
      deleteResultOpen = false;
      deleteResult = null;
    } catch (e) {
      fail(e);
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
    <p class="hint">暂无画像（开发期 Mock 返空；真机 B5 后出真数据）。</p>
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
    <p class="hint">暂无 episode（开发期 Mock 返空；真机 B5 后出真数据）。</p>
  {:else}
    <table>
      <thead><tr><th>时间</th><th>标题</th><th>摘要</th><th></th></tr></thead>
      <tbody>
        {#each episodes as ep (ep.entry_id)}
          <tr>
            <td class="mono">{ep.timestamp}</td>
            <td>{ep.subject || "—"}</td>
            <td>{ep.summary}</td>
            <td>
              <button class="del" onclick={() => startDelete(ep.entry_id)}>删除</button>
            </td>
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
      <p class="hint">无命中（开发期 Mock 返空；真机 B5 后出真数据）。</p>
    {:else}
      <p class="hint">命中 episode {eps.length} 条、profile {profs.length} 条。原始返回：</p>
      <pre>{JSON.stringify(searchResult, null, 2)}</pre>
    {/if}
  {/if}
</section>

<Modal bind:open={failOpen} variant="notice" tone="err" message={failMsg} />

<!-- episode 删除确认浮层：plan 预览 + 重索引模式选择。视觉对齐 Modal.confirm，
     但内容（diff 文本 + 单选）超出共享组件当前 API，故本页自建，不改 Modal 契约。 -->
{#if deleteState}
  <div class="overlay" role="dialog" aria-modal="true">
    <div class="box">
      <h3>确认删除 episode entry</h3>
      {#if deleteState.isEmpty}
        <p class="hint">该 entry 已不存在（可能已被删除），无需再次操作。</p>
        <div class="actions">
          <button onclick={cancelDelete}>关闭</button>
        </div>
      {:else}
        <pre class="diff">{deleteState.preview}</pre>
        <fieldset>
          <legend>重索引模式</legend>
          <label class="radio">
            <input
              type="radio"
              name="reindex-mode"
              value="incremental"
              checked={deleteState.reindexMode === "incremental"}
              onchange={() => deleteState && (deleteState.reindexMode = "incremental")}
            />
            incremental（默认，改完手动同步这一个文件）
          </label>
          <label class="radio">
            <input
              type="radio"
              name="reindex-mode"
              value="full"
              checked={deleteState.reindexMode === "full"}
              onchange={() => deleteState && (deleteState.reindexMode = "full")}
            />
            full（drop 整个 .index，重启后冷重建，保底更彻底但更慢）
          </label>
        </fieldset>
        <p class="warn">⚠️ 执行前请先 SSH 到服务器 <code>sudo systemctl stop everos</code>，否则会被拒绝（409）。</p>
        <div class="actions">
          <button class="primary" onclick={confirmDelete} disabled={deleteState.busy}>
            {deleteState.busy ? "执行中…" : "确认删除"}
          </button>
          <button onclick={cancelDelete} disabled={deleteState.busy}>取消</button>
        </div>
      {/if}
    </div>
  </div>
{/if}

<!-- 删除结果浮层：incremental 模式给出「重启 everos 后点此同步索引」的后续动作。 -->
{#if deleteResultOpen && deleteResult}
  <div class="overlay" role="dialog" aria-modal="true">
    <div class="box">
      <h3>删除完成</h3>
      <p>txn: <code class="mono">{deleteResult.txn}</code></p>
      {#if deleteResult.reindex_mode === "incremental"}
        <p class="hint">
          incremental 模式：请先 SSH <code>sudo systemctl start everos</code> 重启服务，
          再点下方按钮同步这 {deleteResult.reindex_paths.length} 个改动文件的索引。
        </p>
        <div class="actions">
          <button class="primary" onclick={syncReindex}>重启后 · 同步索引</button>
          <button onclick={() => { deleteResultOpen = false; deleteResult = null; }}>稍后手动处理</button>
        </div>
      {:else}
        <p class="hint">full 模式：.index 已 drop，请 SSH 重启 everos 完成冷重建，无需再点同步。</p>
        <div class="actions">
          <button onclick={() => { deleteResultOpen = false; deleteResult = null; }}>关闭</button>
        </div>
      {/if}
    </div>
  </div>
{/if}

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
  .del {
    color: #c44;
    border-color: #c44;
  }
  /* 删除确认/结果浮层 —— 视觉对齐 $lib/Modal.svelte 的 .overlay/.box/.actions/.primary，
     但内容超出共享组件当前 API（diff 文本 + 单选），本页自建不改共享契约。 */
  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.45);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 50;
  }
  .overlay .box {
    background: #fff;
    color: #222;
    border-radius: 8px;
    padding: 1.2rem 1.4rem;
    min-width: 320px;
    max-width: 520px;
    box-shadow: 0 8px 28px rgba(0, 0, 0, 0.25);
  }
  .overlay .box h3 {
    margin: 0 0 0.6rem;
  }
  .overlay .diff {
    max-height: 240px;
  }
  .overlay fieldset {
    border: 1px solid #ddd;
    border-radius: 4px;
    margin: 0.8rem 0;
    padding: 0.5rem 0.8rem;
  }
  .overlay .radio {
    display: flex;
    flex-direction: row;
    align-items: baseline;
    gap: 0.4rem;
    font-size: 0.9rem;
    margin: 0.3rem 0;
  }
  .overlay .radio input {
    width: auto;
  }
  .overlay .warn {
    color: #c93;
    font-size: 0.85rem;
  }
  .overlay .actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.6rem;
    margin-top: 0.8rem;
  }
  .overlay .actions button.primary {
    background: #36c;
    color: #fff;
    border-color: #36c;
  }
</style>