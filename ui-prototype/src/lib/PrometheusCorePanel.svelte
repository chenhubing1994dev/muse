<script lang="ts">
  import type { PrometheusCoreResponse } from "./api";

  export let prometheus: PrometheusCoreResponse | null = null;

  let matrixFilter: "all" | "implemented" | "partial" | "backend_only" | "vision" = "all";

  $: filteredMatrix = (prometheus?.capability_matrix ?? []).filter(
    (item) => matrixFilter === "all" || item.status === matrixFilter,
  );

  function statusPillClass(status: string): string {
    switch (status) {
      case "implemented":
        return "mint";
      case "partial":
        return "gold";
      case "backend_only":
        return "cyan";
      default:
        return "violet";
    }
  }
</script>

<section class="cyber-panel" id="prometheus-core" aria-label="普罗米修斯火种核心与能力愿景矩阵">
  <div class="cyber-head">
    <div>
      <div class="cyber-kicker">04 / PROJECT PROMETHEUS · 普罗米修斯核心</div>
      <h2>普罗米修斯火种五阶与「现实能力 vs 长期愿景」全图</h2>
      <p>{prometheus?.mission ?? "让知识之火不断传递，让未知成为探索的起点。"} —— 一切数字取自本机真实 SQLite 账本，能力与愿景严格分层标注。</p>
    </div>
    <div class="cyber-badge-row">
      <span class="cyber-pill gold">哲学原则 {prometheus?.stages?.synthesis?.philosophy_principles_count ?? 12} 条</span>
      <span class="cyber-pill cyan">哲学经验 {prometheus?.stages?.synthesis?.philosophy_experiences_count ?? 0} 条</span>
      <span class="cyber-pill mint">User Track 记忆原文绝不泄露</span>
    </div>
  </div>

  {#if prometheus}
    <!-- 5 Stages Ledger -->
    <div class="prometheus-stages">
      <article class="p-stage-card">
        <small>01 · IGNITION / 点火</small>
        <strong>{prometheus.stages.ignition.inspirations_count + prometheus.stages.ignition.initiatives_count}</strong>
        <ul>
          <li>灵感池：{prometheus.stages.ignition.inspirations_count} 条</li>
          <li>创作 Initiative：{prometheus.stages.ignition.initiatives_count} 项</li>
          <li>本机提醒：{prometheus.stages.ignition.reminders_count} 条</li>
        </ul>
      </article>
      <article class="p-stage-card">
        <small>02 · DISCOVERY / 求知</small>
        <strong>{prometheus.stages.discovery.knowledge_nodes_total}</strong>
        <ul>
          <li>主张 {prometheus.stages.discovery.nodes_by_type?.claim ?? 0} · 想法 {prometheus.stages.discovery.nodes_by_type?.idea ?? 0}</li>
          <li>来源 {prometheus.stages.discovery.nodes_by_type?.source ?? 0} · 实验 {prometheus.stages.discovery.nodes_by_type?.experiment ?? 0}</li>
          <li>证据快照 {prometheus.stages.discovery.research_evidence_count} · 站点 {prometheus.stages.discovery.research_sites_count}</li>
        </ul>
      </article>
      <article class="p-stage-card">
        <small>03 · SYNTHESIS / 融合</small>
        <strong>{prometheus.stages.synthesis.knowledge_edges_total}</strong>
        <ul>
          <li>知识图谱关系边：{prometheus.stages.synthesis.knowledge_edges_total} 条</li>
          <li>哲学经验关联：{prometheus.stages.synthesis.philosophy_links_count} 次</li>
          <li>待审原则提案：{prometheus.stages.synthesis.philosophy_proposals_count} 项</li>
        </ul>
      </article>
      <article class="p-stage-card">
        <small>04 · CREATION / 创造</small>
        <strong>{prometheus.stages.creation.products_count}</strong>
        <ul>
          <li>交付作品：{prometheus.stages.creation.products_count} · 报告：{prometheus.stages.creation.artifacts_count}</li>
          <li>多 Agent 编排：{prometheus.stages.creation.orchestration_runs_count} 次（{prometheus.stages.creation.agent_runs_count} 步）</li>
          <li>视频尝试：{prometheus.stages.creation.video_attempts_total}（成功 {prometheus.stages.creation.video_attempts_succeeded}）</li>
        </ul>
      </article>
      <article class="p-stage-card">
        <small>05 · TRANSMISSION / 传承</small>
        <strong>{prometheus.stages.transmission.progress_events_count}</strong>
        <ul>
          <li>User 记忆 {prometheus.stages.transmission.user_track_memories_count} · Agent 经验 {prometheus.stages.transmission.agent_track_memories_count}</li>
          <li>Watchdog 自检：{prometheus.stages.transmission.watchdog_checks_count} 次</li>
          <li>能力注册 {prometheus.stages.transmission.capabilities_count} · 改进提案 {prometheus.stages.transmission.improvement_proposals_count}</li>
        </ul>
      </article>
    </div>

    <!-- Capability vs Vision Interactive Matrix -->
    <div class="worker-heading">
      <b>当前已实现功能 vs 将要完成的愿景（按宪章诚实分级）</b>
      <div class="mode-switch">
        <button type="button" class="mode-btn" class:active={matrixFilter === "all"} onclick={() => (matrixFilter = "all")}>全部 ({prometheus.capability_matrix.length})</button>
        <button type="button" class="mode-btn" class:active={matrixFilter === "implemented"} onclick={() => (matrixFilter = "implemented")}>已实现</button>
        <button type="button" class="mode-btn" class:active={matrixFilter === "partial"} onclick={() => (matrixFilter = "partial")}>部分实现·本机依赖</button>
        <button type="button" class="mode-btn" class:active={matrixFilter === "backend_only"} onclick={() => (matrixFilter = "backend_only")}>仅后端/CLI</button>
        <button type="button" class="mode-btn" class:active={matrixFilter === "vision"} onclick={() => (matrixFilter = "vision")}>长期愿景</button>
      </div>
    </div>

    <div class="matrix-grid">
      {#each filteredMatrix as item (item.id)}
        <article class="matrix-card" class:vision={item.status === "vision"}>
          <div class="matrix-card-head">
            <h4>{item.title}</h4>
            <span class={`cyber-pill ${statusPillClass(item.status)}`}>{item.status_label}</span>
          </div>
          <p>{item.summary}</p>
          <div class="matrix-meta">
            <span><b>所属支柱：</b>{item.pillar} · <b>入口：</b>{item.entry}</span>
            <span><b>现实依据：</b>{item.evidence}</span>
            <span><b>安全/能力边界：</b>{item.boundary}</span>
          </div>
        </article>
      {/each}
    </div>

    <!-- 12 Muse Evolution Philosophy Principles -->
    {#if prometheus.principles?.length}
      <div class="worker-heading">
        <b>Muse 演化哲学原则共振矩阵（来自 philosophy/muse.sqlite3）</b>
        <span>共 {prometheus.principles.length} 项活跃核心准则</span>
      </div>
      <div class="principles-grid">
        {#each prometheus.principles as pri (pri.principle_key)}
          <article class="principle-card">
            <div class="inspector-head">
              <h4>{pri.title}</h4>
              <span class="cyber-pill cyan">经验 {pri.experience_count}</span>
            </div>
            <p>{pri.description}</p>
            <small>实践守则：{pri.practice}</small>
          </article>
        {/each}
      </div>
    {/if}
  {:else}
    <div class="empty-state compact">等待连接到本地 Status API 后展示普罗米修斯五阶核心数据与能力矩阵。</div>
  {/if}
</section>
