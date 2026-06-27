<script lang="ts">
  // 根布局：左侧导航 + 右侧内容槽。第一期四组入口对应后端路由分组。
  import { onMount } from "svelte";
  import { auth } from "$lib/auth.svelte";

  let { children } = $props();

  onMount(() => auth.init());

  const nav = [
    { href: "/", label: "概览" },
    { href: "/covenant", label: "铭契编辑（第一期）" },
    { href: "/view", label: "画像/情景只读（第一期）" },
    { href: "/audit", label: "审计日志" },
  ];
</script>

<div class="shell">
  <nav>
    <h1 class="title">
      <span class="title-cn">世界线变动率探测仪</span>
      <span class="title-en">Divergence Meter</span>
    </h1>
    <ul>
      {#each nav as item}
        <li><a href={item.href}>{item.label}</a></li>
      {/each}
    </ul>
    <div class="authbox">
      {#if auth.booting}
        <p>探登录态…</p>
      {:else if auth.loggedIn}
        <p>已登录 · admin</p>
        <button onclick={() => auth.logout()}>登出</button>
      {:else}
        <p>未登录</p>
        <a href="/login">登录</a>
      {/if}
    </div>
    <p class="warn">写操作需登录 · 服务仅监听 127.0.0.1</p>
  </nav>
  <main>
    {@render children()}
  </main>
</div>

<style>
  .shell {
    display: grid;
    grid-template-columns: 220px 1fr;
    min-height: 100vh;
  }
  nav {
    background: #111;
    color: #eee;
    padding: 1rem;
  }
  /* 标题：中文一行、英文换行在下一行——用 display 控制，便于以后调间距/字号不碰结构 */
  .title-en {
    display: block;
    font-size: 0.75em;
    opacity: 0.8;
    margin-top: 0.2em;
  }
  nav ul {
    list-style: none;
    padding: 0;
  }
  nav a {
    color: #9be;
  }
  .warn {
    margin-top: 2rem;
    font-size: 0.8rem;
    color: #c93;
  }
  .authbox {
    margin-top: auto;
    padding-top: 1rem;
    font-size: 0.85rem;
  }
  .authbox button {
    margin-top: 0.3rem;
    padding: 0.2rem 0.6rem;
  }
</style>