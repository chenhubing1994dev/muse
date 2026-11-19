<script lang="ts">
  import type { MemPalaceStatusResponse } from "./api";
  export let status: MemPalaceStatusResponse | null = null;
  export let connected = false;

  const stateLabel = (value: string): string => ({
    not_configured: "尚未配置",
    disabled: "已关闭",
    invalid_config: "配置异常",
    configured_store_missing: "本地 Palace 路径不可用",
    local_store_present: "本地 Palace 目录存在（服务未探测）",
  }[value] ?? value);
</script>

<section class="memory-panel" id="mempalace" aria-label="MemPalace 记忆状态">
  <div class="memory-head">
    <div>
      <div class="memory-kicker">MEMORY PALACE / LOCAL SEMANTIC RECALL</div>
      <h2>记忆殿堂 · MemPalace</h2>
      <p>辅助语义回忆层 · Muse MemoryStore 与 Research Wiki 仍是权威数据源</p>
    </div>
    <span class="memory-local">LOCAL ONLY</span>
  </div>
  {#if !connected}
    <p class="memory-empty">本地控制 API 尚未连接，无法读取状态。</p>
  {:else if !status}
    <p class="memory-empty">状态暂不可用；面板不会启动 MemPalace 服务或后台同步。</p>
  {:else}
    <div class="memory-state-row">
      <span class="memory-state-dot" class:good={status.connection_state === "local_store_present"} class:warn={status.connection_state === "configured_store_missing" || status.connection_state === "invalid_config"}></span>
      <strong>{stateLabel(status.connection_state)}</strong>
      <small>{status.enabled ? "配置启用" : "配置关闭"}</small>
    </div>
    <div class="memory-grid">
      <div><small>PALACE 目录</small><b>{status.palace_directory_present ? "存在" : "未发现"}</b></div>
      <div><small>向量索引文件</small><b>{status.vector_index_present ? "存在" : "未发现"}</b></div>
      <div><small>索引新鲜度</small><b>尚未测量</b></div>
      <div><small>MemoryStore</small><b>{status.memory_store_state === "ready" ? "可读取" : status.memory_store_state === "not_initialized" ? "尚未初始化" : "不可用"}</b></div>
    </div>
    <div class="memory-outbox">
      <div class="memory-outbox-title"><b>Agent Track 投影账本</b><small>{status.projection_outbox.initialized ? (status.projection_outbox.status === "attention_required" ? "需要关注" : status.projection_outbox.status === "pending" ? "有待处理项目" : "健康 · 只读统计") : "尚未初始化"}</small></div>
      <div class="memory-counts">
        <div><small>待处理</small><b>{status.projection_outbox.pending}</b></div>
        <div><small>失败</small><b>{status.projection_outbox.failed}</b></div>
        <div><small>已投影</small><b>{status.projection_outbox.delivered}</b></div>
      </div>
    </div>
    <p class="memory-foot">模型：{status.embedding_model} · 推理：{status.embedding_device.toUpperCase()} · 最早待处理：{status.projection_outbox.oldest_pending_at ?? "暂无"} · 最近成功投影：{status.projection_outbox.latest_delivered_at ?? "暂无"}</p>
    <p class="memory-guard">只读状态探测 · 不启动远端进程 · 不读取记忆正文 · 不自动写入 User Track</p>
  {/if}
</section>

<style>
  .memory-panel{margin:18px 0 22px;padding:clamp(14px,2vw,22px);border:1px solid #31536a;background:radial-gradient(ellipse at 88% 0%,rgba(33,105,121,.18),transparent 42%),linear-gradient(145deg,rgba(12,27,43,.96),rgba(7,16,29,.96));box-shadow:inset 0 0 28px rgba(61,150,166,.06),0 12px 34px rgba(0,0,0,.18);color:#dcebed}
  .memory-head{display:flex;justify-content:space-between;align-items:flex-start;gap:14px}.memory-kicker{font:9px Consolas,monospace;letter-spacing:.16em;color:#83c8c5}.memory-head h2{font-size:clamp(16px,2vw,21px);margin:5px 0;color:#e6f5f0}.memory-head p,.memory-foot,.memory-guard{font-size:10px;color:#91aab7;line-height:1.6;margin:5px 0 0}.memory-local{border:1px solid #477b78;padding:5px 8px;color:#9ad8c7;font:9px Consolas,monospace;white-space:nowrap}.memory-state-row{display:flex;align-items:center;gap:8px;margin:16px 0 10px}.memory-state-row strong{font-size:12px}.memory-state-row small,.memory-outbox-title small{margin-left:auto;color:#91aab7;font-size:9px}.memory-state-dot{width:8px;height:8px;border-radius:50%;background:#718398}.memory-state-dot.good{background:#69d5ad;box-shadow:0 0 10px #69d5ad}.memory-state-dot.warn{background:#e6b76d;box-shadow:0 0 9px #e6b76d}.memory-grid,.memory-counts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.memory-grid>div,.memory-counts>div{min-width:0;padding:10px;border:1px solid #263f55;background:rgba(5,15,27,.65)}.memory-grid small,.memory-grid b,.memory-counts small,.memory-counts b{display:block}.memory-grid small,.memory-counts small{font-size:9px;color:#91aab7}.memory-grid b{margin-top:6px;font-size:11px;color:#d5e8e8}.memory-outbox{margin-top:12px;padding-top:11px;border-top:1px solid #263f55}.memory-outbox-title{display:flex;align-items:center;gap:10px;margin-bottom:8px}.memory-outbox-title b{font-size:10px}.memory-counts{grid-template-columns:repeat(3,minmax(0,1fr))}.memory-counts b{margin-top:5px;font:600 19px Consolas,monospace;color:#a9ded3}.memory-guard{color:#80a89f}.memory-empty{padding:12px;border:1px dashed #38536a;color:#9db6c1;font-size:11px}
  @media(max-width:650px){.memory-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.memory-head{flex-direction:column}.memory-local{align-self:flex-start}}
</style>
