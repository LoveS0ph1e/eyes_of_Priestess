<script lang="ts">
  // 概览页：本服务 + 鉴权配置 + 链接到铭契编辑。
  import { auth } from "$lib/auth.svelte";

  let service = $state<"idle" | "ok" | "fail">("idle");

  // auth.init 已在 layout 探过 /health；此处只复用其 authConfigured + 额外探本服务可达。
  $effect(() => {
    const c = auth.authConfigured;
    if (c === null) service = "fail";
    else service = "ok"; // /health 能回即在线
  });
</script>

<h2>概览</h2>
<p>记忆管理 WebUI 独立服务（非改插件，分期按耦合度递进）。</p>
<dl>
  <dt>本服务</dt>
  <dd>{service === "ok" ? "✅ 在线" : service === "fail" ? "❌ 不可达" : "…"}</dd>
  <dt>鉴权密钥</dt>
  <dd>
    {#if auth.authConfigured === null}
      …
    {:else if auth.authConfigured}
      ✅ 已配置
    {:else}
      ⚠️ 未配置（WEBUI_AUTH_SECRET 为空，写接口将拒绝裸奔）
    {/if}
  </dd>
  <dt>登录态</dt>
  <dd>
    {#if auth.loggedIn === null}
      …
    {:else if auth.loggedIn}
      ✅ 已登录（admin） · <a href="/covenant">去编辑铭契</a>
    {:else}
      未登录 · <a href="/login">登录</a>
    {/if}
  </dd>
</dl>
<p class="hint">
  第一期：铭契编辑已可用（零 EverOS 耦合）；画像/episode 只读与 /epk 可视化随后接入。
  真机验收与出处见 <code>docs/04</code>、<code>docs/01</code>。
</p>

<style>
  .hint {
    color: #666;
    margin-top: 2rem;
  }
</style>