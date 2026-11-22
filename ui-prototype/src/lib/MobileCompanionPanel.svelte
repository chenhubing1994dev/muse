<script lang="ts">
  import { onMount } from "svelte";
  import { createCompanionRemoteClient, type CompanionRemoteClient } from "./api";

  export let client: CompanionRemoteClient | null = null;
  export let onClientChange: (client: CompanionRemoteClient | null) => void = () => {};
  export let onConnectionChange: (connected: boolean) => void = () => {};

  let remoteClient: CompanionRemoteClient | null = client;
  let hostUrl = client?.baseUrl ?? "";
  let challengeId = "";
  let challengeCode = "";
  let deviceName = "Muse Companion";
  let requestId = client?.pendingRequestId ?? "";
  let busy = "";
  let message = "";
  let errorMessage = "";
  let paired = client?.isPaired ?? false;
  let statusPayload: any = null;
  let remoteEvents: Array<{ id: string; category: string; event: string; created_at: number; error_code?: string | null }> = [];
  let lastEventId = "";

  $: if (client !== remoteClient) {
    remoteClient = client;
    hostUrl = client?.baseUrl ?? hostUrl;
    requestId = client?.pendingRequestId ?? "";
    paired = client?.isPaired ?? false;
  }

  onMount(() => {
    if (remoteClient?.hasPendingPairing) requestId = remoteClient.pendingRequestId ?? "";
    if (remoteClient?.isPaired) {
      paired = true;
      onConnectionChange(true);
      void refreshStatus();
    }
  });

  async function startPairing(): Promise<void> {
    if (busy) return;
    busy = "pair";
    errorMessage = "";
    message = "";
    try {
      const endpoint = hostUrl.trim();
      if (!remoteClient) {
        remoteClient = createCompanionRemoteClient({ baseUrl: endpoint });
        onClientChange(remoteClient);
      } else if (remoteClient.baseUrl !== new URL(endpoint).origin) {
        throw new Error("当前已有另一 Host 会话；请先断开，再更换 Host 地址");
      }
      const result = await remoteClient.requestPairing({
        challengeId: challengeId.trim(),
        code: challengeCode.trim(),
        deviceName: deviceName.trim(),
      });
      requestId = result.requestId;
      message = "配对申请已送达。请在 Muse Host 的「设备安全」页面人工批准，再回来领取设备凭据。";
    } catch (cause) {
      errorMessage = cause instanceof Error ? cause.message : "无法提交配对申请";
    } finally {
      busy = "";
    }
  }

  async function claimDevice(): Promise<void> {
    if (!remoteClient || !remoteClient.hasPendingPairing || busy) return;
    busy = "claim";
    errorMessage = "";
    message = "";
    try {
      await remoteClient.claim();
      paired = true;
      onConnectionChange(true);
      message = "Host 已批准并签发只读设备凭据。凭据仅保存在当前 App 内存中。";
      await refreshStatus();
    } catch (cause) {
      errorMessage = cause instanceof Error
        ? cause.message + "；若 Host 尚未批准，请批准后重试。"
        : "尚未领取设备凭据";
    } finally {
      busy = "";
    }
  }

  async function refreshStatus(): Promise<void> {
    if (!remoteClient?.isPaired || busy === "refresh") return;
    busy = "refresh";
    errorMessage = "";
    try {
      const [status, eventBatch] = await Promise.all([
        remoteClient.getStatus(),
        remoteClient.getEvents(lastEventId),
      ]);
      statusPayload = status;
      if (eventBatch.reset) {
        remoteEvents = [];
        lastEventId = "";
        const fresh = await remoteClient.getEvents();
        remoteEvents = fresh.events ?? [];
      } else if (Array.isArray(eventBatch.events) && eventBatch.events.length) {
        remoteEvents = [...remoteEvents, ...eventBatch.events].slice(-50);
      }
      if (remoteEvents.length) lastEventId = remoteEvents[remoteEvents.length - 1].id;
      paired = true;
      onConnectionChange(true);
    } catch (cause) {
      errorMessage = cause instanceof Error ? cause.message : "读取 Host 状态失败";
    } finally {
      busy = "";
    }
  }

  function disconnect(): void {
    remoteClient?.forget();
    remoteClient = null;
    onClientChange(null);
    onConnectionChange(false);
    paired = false;
    requestId = "";
    statusPayload = null;
    remoteEvents = [];
    lastEventId = "";
    message = "设备会话已断开；内存凭据已清除。";
    errorMessage = "";
  }

  function formatTime(epoch: number): string {
    return typeof epoch === "number" && Number.isFinite(epoch)
      ? new Date(epoch * 1000).toLocaleTimeString()
      : "时间未知";
  }
</script>

<section class="mobile-companion">
  <header class="mobile-head">
    <div>
      <small>REMOTE COMPANION / DEVICE SESSION</small>
      <h2>连接 Muse Host</h2>
    </div>
    <span class:live={paired} class="state-pill">{paired ? "只读设备已连接" : requestId ? "等待 Host 审批" : "尚未配对"}</span>
  </header>

  <p class="intro">
    使用公网可达的 HTTPS 域名或可信隧道域名；不支持 localhost 或局域网地址。WebView 会执行系统 CA 与主机名验证；不接受 HTTP、自签名未受信任证书或重定向。
    Host 端仍须人工批准，手机只获得状态与事件读取权限。
  </p>

  {#if !paired}
    <div class="connect-form">
      <label>
        <span>Host HTTPS 地址</span>
        <input bind:value={hostUrl} type="url" autocomplete="url" placeholder="https://muse.example.com:8766" disabled={!!requestId} />
      </label>
      <label>
        <span>一次性挑战 ID</span>
        <input bind:value={challengeId} spellcheck="false" autocomplete="off" placeholder="从 Host「设备安全」页面复制 challenge_id" disabled={!!requestId} />
      </label>
      <label>
        <span>一次性挑战码</span>
        <input bind:value={challengeCode} spellcheck="false" autocomplete="off" placeholder="Host 显示的随机挑战码" disabled={!!requestId} />
      </label>
      <label>
        <span>设备名称</span>
        <input bind:value={deviceName} maxlength="64" autocomplete="off" placeholder="Muse Companion" disabled={!!requestId} />
      </label>
      <div class="actions">
        {#if !requestId}
          <button type="button" class="primary" onclick={startPairing} disabled={!!busy}>
            {busy === "pair" ? "正在申请…" : "提交配对申请"}
          </button>
        {:else}
          <button type="button" class="primary" onclick={claimDevice} disabled={!!busy || !remoteClient?.hasPendingPairing}>
            {busy === "claim" ? "领取中…" : "Host 已批准 · 领取设备凭据"}
          </button>
          <button type="button" onclick={disconnect} disabled={!!busy}>取消申请</button>
        {/if}
      </div>
      {#if requestId}
        <div class="request-card">
          <small>PAIRING REQUEST ID</small>
          <b>{requestId}</b>
          <p>申请已提交。批准动作必须在 Host 本机完成；本手机不能自行批准自己。</p>
        </div>
      {/if}
    </div>
  {:else}
    <div class="status-grid">
      <div class="metric"><small>Host</small><b>{statusPayload?.host?.status ?? "已配对"}</b></div>
      <div class="metric"><small>Commander · 排队</small><b>{statusPayload?.commander?.queue_counts?.queued ?? "—"}</b></div>
      <div class="metric"><small>执行中</small><b>{statusPayload?.commander?.queue_counts?.running ?? "—"}</b></div>
      <div class="metric"><small>在线 Worker</small><b>{statusPayload?.workers?.active ?? "—"}</b></div>
    </div>
    <div class="actions">
      <button type="button" class="primary" onclick={refreshStatus} disabled={!!busy}>
        {busy === "refresh" ? "刷新中…" : "刷新 Host 状态与事件"}
      </button>
      <button type="button" class="danger" onclick={disconnect} disabled={!!busy}>断开并清除凭据</button>
    </div>
    <section class="events">
      <header><b>Commander 安全事件</b><small>{remoteEvents.length} 条 · 最近批次</small></header>
      {#if remoteEvents.length}
        {#each remoteEvents as event (event.id)}
          <article>
            <span><b>{event.category} · {event.event}</b><small>{event.error_code ?? "无错误码"}</small></span>
            <time>{formatTime(event.created_at)}</time>
          </article>
        {/each}
      {:else}
        <p class="empty">当前没有可显示的新事件。</p>
      {/if}
    </section>
  {/if}

  {#if message}<p class="notice success" role="status">{message}</p>{/if}
  {#if errorMessage}<p class="notice error" role="alert">{errorMessage}</p>{/if}

  <aside class="memory-boundary">
    <b>设备凭据保存边界</b>
    <p>当前版本只在 App 运行期间的内存中保留 claim secret 与设备 token，不写入 localStorage、IndexedDB 或普通配置文件。退出或重载后需要重新配对；Android Keystore / Apple Keychain 持久化尚未接入。</p>
  </aside>
</section>

<style>
  .mobile-companion { display: grid; gap: 16px; }
  .mobile-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .mobile-head small { color: var(--text-muted, #91a0b4); font-size: 10px; letter-spacing: .14em; }
  .mobile-head h2 { margin: 4px 0 0; }
  .state-pill { border: 1px solid var(--line, rgba(160,180,210,.2)); border-radius: 999px; padding: 6px 10px; color: var(--text-muted, #91a0b4); font-size: 11px; }
  .state-pill.live { color: var(--jade, #79e3c1); border-color: rgba(121,227,193,.35); }
  .intro, .memory-boundary p { color: var(--text-muted, #91a0b4); line-height: 1.65; font-size: 12px; }
  .connect-form { display: grid; gap: 12px; }
  label { display: grid; gap: 6px; color: var(--text-muted, #91a0b4); font-size: 11px; }
  input { width: 100%; min-width: 0; border: 1px solid var(--line, rgba(160,180,210,.2)); border-radius: 8px; padding: 11px 12px; background: rgba(6,14,28,.8); color: inherit; font: inherit; }
  input:disabled { opacity: .55; }
  .actions { display: flex; flex-wrap: wrap; gap: 8px; }
  button { border: 1px solid var(--line, rgba(160,180,210,.24)); border-radius: 8px; padding: 10px 13px; background: rgba(12,22,38,.78); color: inherit; cursor: pointer; }
  button:disabled { opacity: .45; cursor: not-allowed; }
  button.primary { border-color: rgba(104,205,186,.5); color: var(--jade, #79e3c1); }
  button.danger { color: #ff9a9a; }
  .request-card, .metric, .events, .memory-boundary { border: 1px solid var(--line, rgba(160,180,210,.18)); border-radius: 10px; background: rgba(8,18,34,.56); padding: 13px; }
  .request-card small, .metric small, .events header small { display: block; color: var(--text-muted, #91a0b4); font-size: 10px; }
  .request-card b { display: block; margin-top: 5px; font: 600 13px ui-monospace, monospace; overflow-wrap: anywhere; }
  .request-card p { color: var(--text-muted, #91a0b4); font-size: 11px; }
  .status-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }
  .metric b { display: block; margin-top: 5px; font-size: 18px; }
  .events header, .events article { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
  .events article { padding: 10px 0; border-top: 1px solid var(--line, rgba(160,180,210,.12)); }
  .events article span { display: grid; gap: 4px; min-width: 0; }
  .events article b { font-size: 11px; overflow-wrap: anywhere; }
  .events article small, .events time { color: var(--text-muted, #91a0b4); font-size: 10px; }
  .events time { white-space: nowrap; }
  .empty { color: var(--text-muted, #91a0b4); font-size: 12px; text-align: center; padding: 12px; }
  .memory-boundary { border-color: rgba(121,227,193,.2); }
  .memory-boundary b { font-size: 12px; }
  .memory-boundary p { margin-bottom: 0; }
  .notice { margin: 0; padding: 10px 12px; border-radius: 8px; font-size: 12px; line-height: 1.5; }
  .notice.success { color: var(--jade, #79e3c1); background: rgba(52,150,122,.1); }
  .notice.error { color: #ff9a9a; background: rgba(160,55,65,.12); }
</style>
