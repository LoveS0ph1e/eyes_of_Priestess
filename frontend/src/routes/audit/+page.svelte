<script lang="ts">
  // 审计日志页 —— 列出最近写操作（/api/audit/recent），只读、鉴权可选。
  import { onMount } from "svelte";
  import { api } from "$lib/api";
  import { ApiError } from "$lib/api";

  interface Entry {
    at: string;
    actor: string;
    action: string;
    user_id: string;
    detail: Record<string, unknown>;
    backup_path: string | null;
  }

  let entries = $state<Entry[]>([]);
  let loading = $state(true);
  let err = $state<string | null>(null);

  onMount(async () => {
    loading = true;
    try {
      const r = await api.get("/api/audit/recent?limit=50");
      entries = r.items ?? [];
    } catch (e) {
      err = e instanceof ApiError ? `失败 ${e.status}` : String(e);
    } finally {
      loading = false;
    }
  });
</script>

<h2>审计日志</h2>

{#if loading}
  <p>加载中…</p>
{:else if err}
  <p class="err">读取失败：{err}（审计只读接口鉴权可选）</p>
{:else if entries.length === 0}
  <p class="hint">暂无审计记录。</p>
{:else}
  <table>
    <thead>
      <tr><th>时间(UTC)</th><th>操作者</th><th>动作</th><th>用户</th><th>备份</th></tr>
    </thead>
    <tbody>
      {#each entries as e (e.at + e.action + e.user_id)}
        <tr>
          <td class="mono">{e.at}</td>
          <td>{e.actor}</td>
          <td class="mono">{e.action}</td>
          <td class="mono">{e.user_id}</td>
          <td class="mono">{e.backup_path ? "有" : "—"}</td>
        </tr>
      {/each}
    </tbody>
  </table>
{/if}

<style>
  table {
    border-collapse: collapse;
    margin-top: 1rem;
  }
  th,
  td {
    border: 1px solid #ccc;
    padding: 0.3rem 0.6rem;
    text-align: left;
    font-size: 0.9rem;
  }
  .mono {
    font-family: monospace;
  }
  .err {
    color: #c44;
  }
  .hint {
    color: #888;
  }
</style>