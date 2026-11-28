<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import type { ApiClient, CommanderAdapterRow, CommanderApproval, CommanderStatusResponse } from "./api";

  export let client: ApiClient | null = null;
  export let connected = false;

  let status: CommanderStatusResponse["status"] | null = null;
  let approvals: CommanderApproval[] = [];
  let adapters: CommanderAdapterRow[] = [];
  let operator = "user:owner";
  let killReason = "";
  let killArmed = false;
  let busy = false;
  let message = "";
  let error = "";
  let timer: ReturnType<typeof setInterval> | null = null;

  const riskLabel = (risk?: string | null) => ({ high: "高风险", medium: "中风险", low: "低风险" }[risk ?? ""] ?? "未分级");
  const stateLabel = (state: string) => ({
    LIVE_CONNECTED: "已实连", CONTRACT_ONLY: "仅契约", SKIPPED_ENV: "本机缺环境", UNCONNECTED: "未接入",
  }[state] ?? state);

  async function refresh(): Promise<void> {
    if (!client || !connected) return;
    try {
      const [s, a, d] = await Promise.all([client.commanderStatus(), client.commanderApprovals(), client.commanderAdapters()]);
      status = s.status; approvals = a.approvals; adapters = d.adapters; error = "";
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  async function approve(item: CommanderApproval): Promise<void> {
    if (!client || busy) return;
    if (!window.confirm(`确认批准「${item.title}」？\n风险：${riskLabel(item.risk)}\n原因：${(item.risk_reasons ?? []).join("、") || "无"}`)) return;
    busy = true; message = ""; error = "";
    try { await client.commanderApprove(item.task_id, operator.trim()); message = `已批准：${item.title}`; }
    catch (e) { error = e instanceof Error ? e.message : String(e); }
    finally { busy = false; await refresh(); }
  }

  async function reject(item: CommanderApproval): Promise<void> {
    if (!client || busy) return;
    busy = true; message = ""; error = "";
    try { await client.commanderCancel(item.task_id, operator.trim(), "rejected in control panel"); message = `已拒绝并取消：${item.title}`; }
    catch (e) { error = e instanceof Error ? e.message : String(e); }
    finally { busy = false; await refresh(); }
  }

  async function kill(): Promise<void> {
    if (!client || busy) return;
    if (!killArmed) { killArmed = true; message = "再次点击「确认紧急停止」才会执行。"; return; }
    busy = true; error = "";
    try {
      const out = await client.commanderKill(operator.trim(), killReason.trim() || "控制台紧急停止");
      const cancelled = Array.isArray((out.result as { cancelled?: unknown[] }).cancelled) ? (out.result as { cancelled: unknown[] }).cancelled.length : 0;
      message = `紧急停止已生效：所有派发路径阻断，已取消 ${cancelled} 张未启动票据。复位只能在本机命令行执行 reset-kill。`;
    } catch (e) { error = e instanceof Error ? e.message : String(e); }
    finally { busy = false; killArmed = false; await refresh(); }
  }

  onMount(() => { refresh(); timer = setInterval(refresh, 5000); });
  onDestroy(() => { if (timer) clearInterval(timer); });
  $: if (connected && client) refresh();
</script>

<section class="commander-panel" id="commander" aria-label="Muse Commander 统帅控制">
  <div class="card-h">
    <div>
      <h2>统帅<em>控制台</em></h2>
      <p>唯一调度中心：高风险步骤在此人工审批；紧急停止会同时阻断 Commander、Ruflo 多智能体派发与自治运行时。</p>
    </div>
    <span class="chip" class:is-quiet={status?.safety.mode === "running"}>{status ? (status.safety.mode === "running" ? "运行中" : status.safety.mode === "killed" ? "已紧急停止" : "已暂停") : "未知"}</span>
  </div>

  {#if !connected}
    <p class="memory-empty">本地控制 API 尚未连接。</p>
  {:else}
    <label class="commander-operator">操作人身份 <input bind:value={operator} maxlength="70" aria-label="操作人身份（user: 或 human: 开头）" /></label>

    <div class="memory-grid">
      {#each Object.entries(status?.tasks ?? {}) as [k, v]}
        <div><small>{k}</small><b>{v}</b></div>
      {/each}
      <div><small>完成凭证链</small><b>{status ? (status.receipts.ok ? `完整 · ${status.receipts.count ?? 0}` : "已被篡改") : "—"}</b></div>
    </div>

    <h3>待人工审批（{approvals.length}）</h3>
    {#if approvals.length === 0}
      <p class="memory-empty">当前没有等待审批的票据。</p>
    {:else}
      <ul class="commander-approvals">
        {#each approvals as item (item.task_id)}
          <li>
            <div><b>{item.title}</b> <span class="chip">{riskLabel(item.risk)}</span></div>
            <small>{item.goal ?? ""} · {(item.risk_reasons ?? []).join("、")}</small>
            <div class="commander-actions">
              <button type="button" disabled={busy} on:click={() => approve(item)}>批准</button>
              <button type="button" disabled={busy} on:click={() => reject(item)}>拒绝</button>
            </div>
          </li>
        {/each}
      </ul>
    {/if}

    <h3>适配器四态</h3>
    <div class="memory-grid">
      {#each adapters as row (row.adapter)}
        <div title={row.detail}><small>{row.framework}</small><b>{stateLabel(row.state)}</b></div>
      {/each}
    </div>

    <div class="commander-kill">
      <input bind:value={killReason} maxlength="200" placeholder="紧急停止原因（可选）" aria-label="紧急停止原因" />
      <button type="button" class="danger" disabled={busy || status?.safety.mode === "killed"} on:click={kill}>
        {killArmed ? "确认紧急停止" : "紧急停止 Kill Switch"}
      </button>
    </div>
  {/if}
  {#if message}<p class="commander-msg" role="status">{message}</p>{/if}
  {#if error}<p class="commander-err" role="alert">{error}</p>{/if}
</section>

<style>
  .commander-operator { display: flex; gap: .5rem; align-items: center; margin: .5rem 0; }
  .commander-approvals { list-style: none; padding: 0; display: grid; gap: .5rem; }
  .commander-approvals li { border: 1px solid rgba(255,255,255,.12); border-radius: 8px; padding: .6rem; }
  .commander-actions { display: flex; gap: .5rem; margin-top: .4rem; }
  .commander-kill { display: flex; gap: .5rem; margin-top: 1rem; }
  .commander-kill input { flex: 1; }
  .danger { background: #b3261e; color: #fff; font-weight: 700; }
  .commander-err { color: #ff8a80; }
  .commander-msg { color: #b9f6ca; }
</style>
