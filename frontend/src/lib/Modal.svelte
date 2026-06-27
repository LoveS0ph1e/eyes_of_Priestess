<script lang="ts">
  // 通用弹窗组件 —— 两型合一：
  //   variant="confirm"：二次确认（标题+说明+确认/取消），点确认先关弹窗再调 onconfirm。
  //   variant="notice" ：单条大字提示（tone=ok 绿 / err 红），点遮罩任意处关闭。
  // 样式集中在本组件 scoped class，靠 prop 参数化文案/色调 —— 可打磨不写死。
  // Svelte 5 runes：$props + $bindable(open)。
  let {
    open = $bindable(false),
    variant = "notice",
    title = "",
    message = "",
    tone = "ok",
    confirmText = "发送",
    cancelText = "取消",
    onconfirm = () => {},
    oncancel = () => {},
  }: {
    open?: boolean;
    variant?: "confirm" | "notice";
    title?: string;
    message?: string;
    tone?: "ok" | "err";
    confirmText?: string;
    cancelText?: string;
    onconfirm?: () => void;
    oncancel?: () => void;
  } = $props();

  // 确认型：先关弹窗再执行（复刻原 runConfirm 行为，保证「发送」后立刻消失）
  function confirm() {
    open = false;
    onconfirm();
  }
  function cancel() {
    open = false;
    oncancel();
  }
  // 提示型：点遮罩任意处关闭
  function dismiss() {
    open = false;
  }
</script>

{#if open}
  {#if variant === "confirm"}
    <div class="overlay" role="dialog" aria-modal="true" tabindex="-1" aria-label={title}>
      <div class="box">
        <h3>{title}</h3>
        {#if message}<p class="msg">{message}</p>{/if}
        <div class="actions">
          <button class="primary" onclick={confirm}>{confirmText}</button>
          <button class="secondary" onclick={cancel}>{cancelText}</button>
        </div>
      </div>
    </div>
  {:else}
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
    <div
      class="overlay clickable"
      role="dialog"
      aria-modal="true"
      tabindex="-1"
      aria-label={message}
      onclick={dismiss}
    >
      <div class="box notice">
        <p class="notice-text" class:ok={tone === "ok"} class:err={tone === "err"}>{message}</p>
      </div>
    </div>
  {/if}
{/if}

<style>
  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.45);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 50;
  }
  .clickable {
    cursor: pointer;
  }
  .box {
    background: #fff;
    color: #222;
    border-radius: 8px;
    padding: 1.2rem 1.4rem;
    min-width: 280px;
    max-width: 460px;
    box-shadow: 0 8px 28px rgba(0, 0, 0, 0.25);
  }
  .box.notice {
    padding: 2rem 3rem;
    border-radius: 10px;
    pointer-events: none; /* 点内容也穿透到遮罩，算点关闭 */
  }
  h3 {
    margin: 0 0 0.6rem;
  }
  .msg {
    margin: 0 0 1rem;
    color: #555;
    white-space: pre-wrap;
  }
  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.6rem;
  }
  button {
    padding: 0.4rem 1rem;
    cursor: pointer;
    border: 1px solid #ccc;
    background: #fff;
  }
  .primary {
    background: #36c;
    color: #fff;
    border-color: #36c;
  }
  .secondary {
    background: #fff;
    color: #444;
  }
  .notice-text {
    margin: 0;
    font-size: 1.6rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-align: center;
  }
  .notice-text.ok {
    color: #2a7;
  }
  .notice-text.err {
    color: #c44;
  }
</style>