<script lang="ts">
  // 登录页 —— 输入 WEBUI_AUTH_SECRET 换 cookie 会话。
  import { auth } from "$lib/auth.svelte";
  import { goto } from "$app/navigation";

  let secret = $state("");
  let err = $state<string | null>(null);
  let busy = $state(false);

  async function submit(e: Event) {
    e.preventDefault();
    busy = true;
    err = null;
    const r = await auth.login(secret.trim());
    busy = false;
    if (r.ok) {
      secret = "";
      goto("/covenant");
    } else {
      err = r.detail ?? "登录失败";
    }
  }
</script>

<h2>登录</h2>
<p class="hint">输入 WEBUI_AUTH_SECRET 登录。服务仅监听 127.0.0.1，cookie 经同源自动下发。</p>

{#if auth.authConfigured === false}
  <p class="warn">⚠️ 后端未配密钥（WEBUI_AUTH_SECRET 为空，写接口将拒绝裸奔）。先注入密钥再登录。</p>
{/if}

<form onsubmit={submit}>
  <label>
    密钥
    <input type="password" bind:value={secret} placeholder="WEBUI_AUTH_SECRET" autocomplete="off" />
  </label>
  <button type="submit" disabled={busy || !secret.trim()}>{busy ? "登录中…" : "登录"}</button>
  {#if err}<p class="err">{err}</p>{/if}
</form>

<style>
  .hint {
    color: #666;
    font-size: 0.9rem;
  }
  .warn {
    color: #c93;
  }
  form {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    max-width: 360px;
    margin-top: 1rem;
  }
  label {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
  input {
    padding: 0.45rem;
    font-size: 1rem;
  }
  button {
    padding: 0.5rem 1rem;
    align-self: flex-start;
  }
  .err {
    color: #c44;
  }
</style>