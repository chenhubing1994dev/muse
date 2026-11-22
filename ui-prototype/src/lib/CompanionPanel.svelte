<script lang="ts">
  import type {
    ApiClient,
    CompanionAuditEvent,
    CompanionDevice,
    CompanionPairing,
    CompanionPairingChallenge,
  } from "./api";

  export let client: ApiClient | null = null;
  export let connected = false;

  let pairings: CompanionPairing[] = [];
  let devices: CompanionDevice[] = [];
  let audit: CompanionAuditEvent[] = [];
  let challenge: CompanionPairingChallenge | null = null;
  let loading = false;
  let busy = "";
  let message = "";
  let error = "";
  let loadedClient: ApiClient | null = null;

  $: if (!connected) loadedClient = null;
  $: if (connected && client && loadedClient !== client) {
    loadedClient = client;
    void refresh();
  }

  async function refresh(): Promise<void> {
    if (!client || !connected || loading) return;
    loading = true;
    error = "";
    try {
      const [pairingResult, deviceResult, auditResult] = await Promise.all([
        client.companionPairings(50),
        client.companionDevices(100),
        client.companionAudit(30),
      ]);
      pairings = pairingResult.pairings;
      devices = deviceResult.devices;
      audit = [...auditResult.events].reverse();
    } catch (cause) {
      error = cause instanceof Error ? cause.message : "无法读取 Companion 设备状态";
    } finally {
      loading = false;
    }
  }

  async function startPairing(): Promise<void> {
    if (!client || busy) return;
    busy = "start";
    message = "";
    error = "";
    try {
      const result = await client.startCompanionPairing(180);
      challenge = result.challenge;
      message = "一次性挑战已生成。将挑战 ID 与挑战码交给可信设备；设备通过 HTTPS endpoint 申请后，仍需 Host 人工批准。";
    } catch (cause) {
      error = cause instanceof Error ? cause.message : "无法生成配对挑战";
    } finally {
      busy = "";
    }
  }

  async function approvePairing(requestId: string): Promise<void> {
    if (!client || busy) return;
    busy = requestId;
    message = "";
    error = "";
    try {
      await client.approveCompanionPairing(requestId);
      message = "设备配对请求已由 Host 批准。";
      await refresh();
    } catch (cause) {
      error = cause instanceof Error ? cause.message : "批准配对请求失败";
    } finally {
      busy = "";
    }
  }

  async function denyPairing(requestId: string): Promise<void> {
    if (!client || busy) return;
    busy = requestId;
    message = "";
    error = "";
    try {
      await client.denyCompanionPairing(requestId);
      message = "配对请求已拒绝。";
      await refresh();
    } catch (cause) {
      error = cause instanceof Error ? cause.message : "拒绝配对请求失败";
    } finally {
      busy = "";
    }
  }

  async function revokeDevice(deviceId: string): Promise<void> {
    if (!client || busy) return;
    busy = deviceId;
    message = "";
    error = "";
    try {
      await client.revokeCompanionDevice(deviceId);
      message = "设备凭据已撤销，后续请求将被拒绝。";
      await refresh();
    } catch (cause) {
      error = cause instanceof Error ? cause.message : "撤销设备失败";
    } finally {
      busy = "";
    }
  }

  async function copyChallenge(): Promise<void> {
    if (!challenge) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify({
        challenge_id: challenge.challenge_id,
        code: challenge.code,
      }));
      message = "挑战 ID 与一次性挑战码已复制。请通过可信渠道交给设备，并在有效期内完成 HTTPS 申请。";
    } catch {
      error = "剪贴板不可用，请手动复制挑战码。";
    }
  }

  function shortFingerprint(value: string): string {
    return value.length > 24 ? `${value.slice(0, 12)}…${value.slice(-8)}` : value;
  }

  function formatEpoch(value: number | null | undefined): string {
    if (typeof value !== "number" || !Number.isFinite(value)) return "时间未知";
    return new Date(value * 1000).toLocaleString();
  }
</script>

<section class="companion-panel">
  <div class="panel-head">
    <div>
      <small>COMPANION SECURITY / HOST TRUST CENTER</small>
      <h2>跨端设备与安全配对</h2>
    </div>
    <span class:live={connected} class="connection-state">{connected ? "HOST 本机接口已连接" : "HOST 未连接"}</span>
  </div>

  <p class="panel-intro">
    这里管理 Windows/macOS Host 的设备信任记录。管理 API 只接受本机 loopback + 桌面 Bearer；独立 Companion TLS listener 通过 muse-companion-host 显式启动，手机只使用设备专属凭据。Host 必须人工批准；当前展示指纹仍由客户端声明，尚未完成公钥持有证明。
  </p>

  <div class="toolbar">
    <button type="button" class="primary" onclick={startPairing} disabled={!connected || !!busy || loading}>
      {busy === "start" ? "生成中…" : "生成一次性 Host 挑战"}
    </button>
    <button type="button" onclick={refresh} disabled={!connected || loading || !!busy}>
      {loading ? "刷新中…" : "刷新设备状态"}
    </button>
    <span class="read-only-note"><i></i> 默认只读权限 · 不开放任务执行</span>
  </div>

  {#if challenge}
    <div class="challenge-card">
      <div>
        <small>ONE-TIME HOST CHALLENGE · 180 秒</small>
        <small>CHALLENGE ID</small>
        <strong class="challenge-code">{challenge.challenge_id}</strong>
        <small>ONE-TIME CODE</small>
        <strong class="challenge-code">{challenge.code}</strong>
        <p>到期时间：{formatEpoch(challenge.expires_at)}。申请提交后仍须 Host 人工批准。</p>
      </div>
      <button type="button" onclick={copyChallenge}>复制配对信息</button>
    </div>
  {/if}

  {#if message}<p class="notice success" role="status">{message}</p>{/if}
  {#if error}<p class="notice error" role="alert">{error}</p>{/if}

  <div class="companion-grid">
    <section class="subpanel">
      <header><div><small>PAIRING REQUESTS</small><h3>待审批设备</h3></div><b>{pairings.length}</b></header>
      {#if pairings.length}
        <div class="rows">
          {#each pairings as pairing (pairing.request_id)}
            <article class="device-row">
              <div>
                <strong>{pairing.device_name}</strong>
                <small>客户端声明指纹 {shortFingerprint(pairing.device_fingerprint)}</small>
                <small>{pairing.status === "approved" ? "已批准 · 等待设备领取" : "等待 Host 人工审批"}</small>
                <small>申请于 {formatEpoch(pairing.created_at)} · 到期 {formatEpoch(pairing.expires_at)}</small>
              </div>
              <div class="actions">
                <button type="button" class="approve" onclick={() => approvePairing(pairing.request_id)} disabled={!!busy || loading || pairing.status !== "pending"}>批准</button>
                <button type="button" class="danger" onclick={() => denyPairing(pairing.request_id)} disabled={!!busy || loading || pairing.status !== "pending"}>拒绝</button>
              </div>
            </article>
          {/each}
        </div>
      {:else}
        <div class="empty-state">暂无待审批设备。设备可通过独立 HTTPS endpoint 提交申请，Host 必须人工批准。</div>
      {/if}
    </section>

    <section class="subpanel">
      <header><div><small>TRUSTED DEVICES</small><h3>已登记设备</h3></div><b>{devices.length}</b></header>
      {#if devices.length}
        <div class="rows">
          {#each devices as device (device.device_id)}
            <article class="device-row">
              <div>
                <strong>{device.device_name}</strong>
                <small>客户端声明指纹 {shortFingerprint(device.device_fingerprint)}</small>
                <small>权限：{device.permissions.join(" · ")}</small>
                <small>登记于 {formatEpoch(device.created_at)}</small>
              </div>
              <div class="actions">
                <span class:revoked={device.status === "revoked"} class="device-status">{device.status === "active" ? "有效" : "已撤销"}</span>
                {#if device.status === "active"}
                  <button type="button" class="danger" onclick={() => revokeDevice(device.device_id)} disabled={!!busy || loading}>撤销</button>
                {/if}
              </div>
            </article>
          {/each}
        </div>
      {:else}
        <div class="empty-state">尚无已配对设备。</div>
      {/if}
    </section>
  </div>

  <section class="subpanel audit-panel">
    <header><div><small>SECURITY AUDIT</small><h3>安全审计轨迹</h3></div><b>{audit.length}</b></header>
    {#if audit.length}
      <div class="rows">
        {#each audit as event (event.event_id)}
          <div class="audit-row">
            <span><strong>{event.event_type}</strong><small>{event.actor}{event.device_id ? ` · ${event.device_id.slice(0, 8)}` : ""}</small></span>
            <time>{formatEpoch(event.created_at)}</time>
          </div>
        {/each}
      </div>
    {:else}
      <div class="empty-state">暂无安全事件记录。</div>
    {/if}
  </section>
</section>

<style>
  .companion-panel { display: grid; gap: 16px; }
  .panel-head, .subpanel > header, .toolbar, .device-row, .audit-row, .challenge-card {
    display: flex; align-items: center; justify-content: space-between; gap: 14px;
  }
  .panel-head small, .subpanel header small, .challenge-card small {
    display: block; color: var(--text-muted, #91a0b4); font-size: 10px; letter-spacing: .14em;
  }
  .panel-head h2, .subpanel h3 { margin: 4px 0 0; }
  .connection-state, .device-status {
    border: 1px solid var(--line, rgba(160,180,210,.2)); border-radius: 999px;
    padding: 5px 10px; color: var(--text-muted, #91a0b4); font-size: 11px;
  }
  .connection-state.live { color: var(--jade, #79e3c1); border-color: rgba(121,227,193,.35); }
  .panel-intro { max-width: 900px; color: var(--text-muted, #91a0b4); line-height: 1.7; }
  .toolbar { justify-content: flex-start; flex-wrap: wrap; }
  button {
    border: 1px solid var(--line, rgba(160,180,210,.24)); border-radius: 8px;
    padding: 8px 12px; background: rgba(12,22,38,.78); color: inherit; cursor: pointer;
  }
  button:disabled { opacity: .45; cursor: not-allowed; }
  button.primary { border-color: rgba(104,205,186,.5); color: var(--jade, #79e3c1); }
  button.approve { color: var(--jade, #79e3c1); }
  button.danger { color: #ff9a9a; }
  .read-only-note { display: inline-flex; align-items: center; gap: 7px; color: var(--text-muted, #91a0b4); font-size: 11px; }
  .read-only-note i { width: 6px; height: 6px; border-radius: 50%; background: var(--jade, #79e3c1); }
  .challenge-card, .subpanel {
    border: 1px solid var(--line, rgba(160,180,210,.18)); border-radius: 12px;
    background: rgba(8,18,34,.56); padding: 16px;
  }
  .challenge-card { align-items: flex-start; }
  .challenge-card p { margin: 7px 0 0; color: var(--text-muted, #91a0b4); font-size: 12px; }
  .challenge-code { display: block; margin-top: 8px; font: 600 20px/1.3 ui-monospace, monospace; letter-spacing: .08em; color: var(--jade, #79e3c1); overflow-wrap: anywhere; }
  .companion-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
  .subpanel { min-width: 0; }
  .subpanel > header { margin-bottom: 12px; }
  .subpanel > header b { color: var(--jade, #79e3c1); font-variant-numeric: tabular-nums; }
  .rows { display: grid; gap: 8px; }
  .device-row, .audit-row { padding: 10px 0; border-top: 1px solid var(--line, rgba(160,180,210,.12)); }
  .device-row > div:first-child, .audit-row > span { min-width: 0; display: grid; gap: 4px; }
  .device-row strong, .audit-row strong { font-size: 12px; overflow-wrap: anywhere; }
  .device-row small, .audit-row small, .audit-row time { color: var(--text-muted, #91a0b4); font-size: 10px; overflow-wrap: anywhere; }
  .actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
  .device-status.revoked { color: #ff9a9a; }
  .audit-row time { white-space: nowrap; }
  .audit-panel { margin-top: 0; }
  .empty-state { padding: 18px 10px; text-align: center; color: var(--text-muted, #91a0b4); font-size: 12px; }
  .notice { margin: 0; padding: 9px 12px; border-radius: 8px; font-size: 12px; }
  .notice.success { color: var(--jade, #79e3c1); background: rgba(52,150,122,.1); }
  .notice.error { color: #ff9a9a; background: rgba(160,55,65,.12); }
  @media (max-width: 900px) {
    .companion-grid { grid-template-columns: 1fr; }
    .challenge-card, .device-row { align-items: flex-start; flex-direction: column; }
  }
</style>
