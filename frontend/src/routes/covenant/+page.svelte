<script lang="ts">
  // 铭契编辑页（第一期核心）—— 列表 + 编辑/新增 + 删除。
  // 后端语义：空文本 upsert = 删除该键；改完建议在 IM 内发消息确认【永恒铭契】块随之变。
  import { onMount } from "svelte";
  import { covenant, type Covenant } from "$lib/api";
  import { ApiError } from "$lib/api";
  import { auth } from "$lib/auth.svelte";
  import Modal from "$lib/Modal.svelte";

  let rows = $state<Covenant[]>([]);
  let selectedId = $state<string>("");
  let editingUser = $state<string>("");
  let editingText = $state<string>("");
  let loading = $state(true);
  let msg = $state<{ kind: "ok" | "err"; text: string } | null>(null);
  let busy = $state(false);

  // 二次确认弹窗状态：open + 待执行动作 + 标题/提示/按钮文字（均参数化，便于以后调样式/文案）
  let confirmState = $state<{
    open: boolean;
    title: string;
    message: string;
    confirmText: string;
    onConfirm: () => void;
  }>({ open: false, title: "", message: "", confirmText: "发送", onConfirm: () => {} });

  // 成功/失败弹窗（修改/删除确认发送后出）—— 均点任意处关闭
  let successOpen = $state(false);
  let failOpen = $state(false);

  onMount(load);

  async function load() {
    loading = true;
    try {
      rows = await covenant.list();
    } catch (e) {
      flash("err", errMsg(e));
    } finally {
      loading = false;
    }
  }

  function startNew() {
    selectedId = "";
    editingUser = "";
    editingText = "";
    msg = null;
  }

  function select(c: Covenant) {
    selectedId = c.user_id;
    editingUser = c.user_id;
    editingText = c.text;
    msg = null;
  }

  // 保存前置弹窗确认；确认 = 发送，取消 = 不改。
  function save() {
    const uid = editingUser.trim();
    if (!uid) {
      flash("err", "请填 user_id（QQ 号）");
      return;
    }
    confirmState = {
      open: true,
      title: "确认铭契修改",
      message: `即将 ${editingText.trim() ? "保存" : "清空（=删除）"} user_id ${uid} 的铭契。`,
      confirmText: "发送",
      onConfirm: doSave,
    };
  }

  async function doSave() {
    const uid = editingUser.trim();
    busy = true;
    msg = null;
    try {
      await covenant.upsert(uid, editingText);
      successOpen = true;
      await load();
      if (editingText.trim()) selectedId = uid;
    } catch (e) {
      flash("err", errMsg(e)); // 小字留具体原因（401/502/网络）
      failOpen = true; // 醒目弹窗
    } finally {
      busy = false;
    }
  }

  // 删除前置弹窗确认。
  function remove(uid: string) {
    if (!uid) return;
    confirmState = {
      open: true,
      title: "确认删除铭契",
      message: `即将删除 user_id ${uid} 的铭契。`,
      confirmText: "发送",
      onConfirm: () => doRemove(uid),
    };
  }

  async function doRemove(uid: string) {
    busy = true;
    msg = null;
    try {
      await covenant.delete(uid);
      successOpen = true;
      if (selectedId === uid) startNew();
      await load();
    } catch (e) {
      flash("err", errMsg(e)); // 小字留具体原因（401/502/网络）
      failOpen = true; // 醒目弹窗
    } finally {
      busy = false;
    }
  }

  function flash(kind: "ok" | "err", text: string) {
    msg = { kind, text };
  }

  function errMsg(e: unknown): string {
    if (e instanceof ApiError) {
      if (e.status === 401) return "未登录或会话过期，请重新登录";
      if (e.status === 503) return "后端未配密钥（WEBUI_AUTH_SECRET 为空）";
      if (e.status === 400) return "身份非法：" + (e.detail ?? "");
      if (e.status === 502) return "拒绝：" + (e.detail ?? "");
      return `失败 ${e.status}：${e.detail ?? ""}`;
    }
    return String(e);
  }
</script>

<h2>铭契编辑</h2>

{#if auth.loggedIn === false}
  <p class="warn">写操作需登录 —— <a href="/login">去登录</a></p>
{:else if loading}
  <p>加载中…</p>
{:else}
  <div class="grid">
    <section class="list">
      <div class="row">
        <button class="newbtn" onclick={startNew}>＋ 新增</button>
        <span class="count">{rows.length} 条</span>
      </div>
      {#each rows as c (c.user_id)}
        <button
          class="item"
          class:active={c.user_id === selectedId}
          onclick={() => select(c)}
        >
          <span class="uid">{c.user_id}</span>
          <span class="tx">{c.text.slice(0, 40)}{c.text.length > 40 ? "…" : ""}</span>
        </button>
      {/each}
    </section>

    <section class="editor">
      <label>
        <span>user_id（QQ 号）</span>
        <input bind:value={editingUser} placeholder="如 10001" />
      </label>
      <label>
        <span>铭契文本（空 = 删除该键，与 resolve_covenant 语义对齐）</span>
        <textarea bind:value={editingText} rows="8" placeholder="固定核心设定文本…"></textarea>
      </label>
      <div class="actions">
        <button onclick={save} disabled={busy || !editingUser.trim()}>{busy ? "保存中…" : "保存（upsert）"}</button>
        <button class="del" onclick={() => remove(editingUser.trim())} disabled={busy || !editingUser.trim()} style="display:{editingUser ? 'inline' : 'none'}">删除</button>
      </div>
      {#if msg}<p class={msg.kind}>{msg.text}</p>{/if}
      <p class="hint">
        值内引号/换行由后端 json.dumps 转义，直接自然书写。⚠️ 铭契改后需重启 astrbot（<code>docker restart astrbot</code>）才生效 —— 实测插件配置不热重载。
      </p>
    </section>
  </div>
{/if}

<!-- 二次确认弹窗（共享 Modal 组件，confirm 型） -->
<Modal
  bind:open={confirmState.open}
  variant="confirm"
  title={confirmState.title}
  message={confirmState.message}
  confirmText={confirmState.confirmText}
  onconfirm={confirmState.onConfirm}
/>

<!-- 成功弹窗：确认发送后出，点任意处关闭 -->
<Modal bind:open={successOpen} variant="notice" tone="ok" message="世界线已变动" />

<!-- 失败弹窗：写入失败（网络波动等）时出，点任意处关闭；编辑器下方小字另有具体原因 -->
<Modal bind:open={failOpen} variant="notice" tone="err" message="失败了失败了失败了" />

<style>
  .warn {
    color: #c93;
  }
  .grid {
    display: grid;
    grid-template-columns: 280px 1fr;
    gap: 1.5rem;
    margin-top: 1rem;
  }
  .list {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 0.6rem;
  }
  .newbtn {
    width: fit-content;
  }
  .count {
    color: #888;
    font-size: 0.85rem;
  }
  .item {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    align-items: stretch;
    text-align: left;
    padding: 0.5rem;
    border: 1px solid #ccc;
    background: #fff;
    cursor: pointer;
  }
  .item.active {
    border-color: #36c;
    background: #eef;
  }
  .uid {
    font-family: monospace;
    font-weight: 600;
  }
  .tx {
    font-size: 0.85rem;
    color: #555;
  }
  .editor {
    display: flex;
    flex-direction: column;
    gap: 0.7rem;
  }
  label {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
  }
  input,
  textarea {
    padding: 0.4rem;
    font-family: inherit;
    font-size: 0.95rem;
  }
  textarea {
    resize: vertical;
  }
  .actions {
    display: flex;
    gap: 0.6rem;
  }
  .del {
    color: #c44;
  }
  .ok {
    color: #2a7;
  }
  .err {
    color: #c44;
  }
  .hint {
    color: #888;
    font-size: 0.82rem;
  }
</style>