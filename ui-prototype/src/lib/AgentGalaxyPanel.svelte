<script lang="ts">
  import type {
    AgentRecord,
    AgentOrgChartNode,
    AgentOrgChartResponse,
    AgentStatus,
    ApiClient,
    GalaxyResponse,
    RoutePreviewResponse,
  } from "./api";
  import { layoutAgentGalaxy } from "../../cyber-astrolabe.js";

  export let client: ApiClient | null = null;
  export let connected = false;
  export let agents: AgentRecord[] = [];
  export let registryConfigured = false;
  export let orgChart: AgentOrgChartResponse | null = null;
  export let galaxy: GalaxyResponse | null = null;

  const statusLabels: Record<AgentStatus, string> = {
    online: "在线",
    busy: "执行中",
    offline: "离线",
    disabled: "已停用",
  };

  let routeGoal = "";
  let previewBusy = false;
  let previewError = "";
  let routePreview: RoutePreviewResponse | null = null;
  let selectedSpecialistId = "";

  $: galaxyLayout = layoutAgentGalaxy(galaxy, 620, 440);
  $: matchedIds = new Set((routePreview?.selected ?? []).map((item) => item.specialist_id));
  $: selectedSpecialist = (galaxy?.specialist_profiles ?? []).find((s) => s.specialist_id === selectedSpecialistId) ?? null;

  interface OrgTreeRow { node: AgentOrgChartNode; depth: number; unresolvedParent: boolean; }

  function buildOrgRows(chart: AgentOrgChartResponse | null): OrgTreeRow[] {
    if (!chart) return [];
    const byId = new Map(chart.nodes.map((node) => [node.agent_id, node]));
    const children = new Map<string, AgentOrgChartNode[]>();
    const roots: AgentOrgChartNode[] = [];
    for (const node of chart.nodes) {
      const parentId = node.reports_to;
      if (parentId && byId.has(parentId)) {
        const siblings = children.get(parentId) ?? [];
        siblings.push(node);
        children.set(parentId, siblings);
      } else {
        roots.push(node);
      }
    }
    const sortNodes = (items: AgentOrgChartNode[]) => items.sort((a, b) =>
      (a.display_name || a.agent_id).localeCompare(b.display_name || b.agent_id));
    sortNodes(roots);
    for (const items of children.values()) sortNodes(items);
    const rows: OrgTreeRow[] = [];
    const visited = new Set<string>();
    const visit = (node: AgentOrgChartNode, depth: number, unresolvedParent = false) => {
      if (visited.has(node.agent_id)) return;
      visited.add(node.agent_id);
      rows.push({ node, depth: Math.min(depth, 8), unresolvedParent });
      for (const child of children.get(node.agent_id) ?? []) visit(child, depth + 1);
    };
    for (const root of roots) visit(root, 0, Boolean(root.reports_to && !byId.has(root.reports_to)));
    for (const node of chart.nodes) {
      if (!visited.has(node.agent_id)) visit(node, 0, true);
    }
    return rows;
  }

  function orgStatus(status: string): { label: string; tone: string } {
    switch (status) {
      case "online": return { label: "在线", tone: "online" };
      case "busy": return { label: "执行中", tone: "busy" };
      case "disabled": return { label: "已停用", tone: "disabled" };
      case "blocked": return { label: "受阻", tone: "offline" };
      default: return { label: "离线", tone: "offline" };
    }
  }

  $: orgRows = buildOrgRows(orgChart);

  async function runRoutePreview(): Promise<void> {
    if (!client || !connected || !routeGoal.trim() || previewBusy) return;
    previewBusy = true;
    previewError = "";
    try {
      routePreview = await client.previewRoute(routeGoal.trim());
    } catch (error) {
      previewError = error instanceof Error ? error.message : "路由预演失败";
    } finally {
      previewBusy = false;
    }
  }

  function percent(value: number | null | undefined): string {
    return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";
  }

  function relativeTime(value?: string | null): string {
    if (!value) return "从未收到心跳";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "心跳时间未知";
    const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
    if (seconds < 60) return `${seconds} 秒前`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
    return `${Math.floor(seconds / 3600)} 小时前`;
  }

  function onPlanetKey(event: KeyboardEvent, id: string): void {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectedSpecialistId = id;
    }
  }
</script>

<section class="cyber-panel" id="agents" aria-label="天枢 AI Agent 星系">
  <div class="cyber-head">
    <div>
      <div class="cyber-kicker">03 / COMMANDER &amp; AI AGENT GALAXY</div>
      <h2>AI Agent 星系 · 唯一 Commander 与专家拓扑</h2>
      <p>唯一调度权威 Muse Commander 统御专家角色模型、角色安全策略、实连 Agent 与本机工具能力；绝不把未连模板伪装成在线进程。</p>
    </div>
    <div class="cyber-badge-row">
      <span class="cyber-pill cyan">竞态调度器：{galaxy?.commander?.competing_schedulers ?? 0}</span>
      <span class="cyber-pill gold">历史编排：{galaxy?.orchestration_summary?.total_runs ?? 0} 次</span>
      <span class="cyber-pill mint">实连 Agent：{galaxy?.live_agents?.length ?? agents.length} 个</span>
    </div>
  </div>

  <div class="galaxy-layout">
    <!-- Left: Interactive SVG Galaxy -->
    <div class="galaxy-svg-frame">
      <svg class="galaxy-svg" viewBox="0 0 620 440" role="img" aria-label="AI Agent 星系轨道拓扑图">
        {#each galaxyLayout.orbits as orbit (orbit.label)}
          <circle cx={galaxyLayout.center.x} cy={galaxyLayout.center.y} r={orbit.r} class="galaxy-orbit" />
        {/each}

        <!-- Links from Commander to specialist planets -->
        {#each galaxyLayout.specialists as sp (sp.specialist_id)}
          {@const matched = matchedIds.has(sp.specialist_id)}
          <line
            x1={galaxyLayout.center.x}
            y1={galaxyLayout.center.y}
            x2={sp.x}
            y2={sp.y}
            class={`galaxy-link ${matched ? "matched" : ""}`}
          />
        {/each}

        <!-- Outer ring: Capabilities -->
        {#each galaxyLayout.capabilities as cap (cap.capability_id)}
          <g>
            <circle cx={cap.x} cy={cap.y} r="7" fill="#091d32" stroke="#3bf5c4" stroke-width="1" />
            <text x={cap.x} y={cap.y + 16} class="galaxy-sublabel">{cap.name.slice(0, 12)}</text>
          </g>
        {/each}

        <!-- Middle ring: Live Agents -->
        {#each galaxyLayout.liveAgents as la (la.agent_id)}
          <g>
            <circle cx={la.x} cy={la.y} r="13" class="galaxy-planet live" />
            <text x={la.x} y={la.y + 22} class="galaxy-label">{la.display_name || la.agent_id}</text>
          </g>
        {/each}

        <!-- Inner ring: 8 Specialist Planets -->
        {#each galaxyLayout.specialists as sp (sp.specialist_id)}
          {@const matched = matchedIds.has(sp.specialist_id)}
          <g
            role="button"
            tabindex="0"
            aria-label={`专家角色 ${sp.specialist_id} (${sp.role})`}
            onclick={() => (selectedSpecialistId = sp.specialist_id)}
            onkeydown={(e) => onPlanetKey(e, sp.specialist_id)}
          >
            <circle
              cx={sp.x}
              cy={sp.y}
              r={matched ? 22 : 18}
              class={`galaxy-planet ${matched ? "matched" : ""} ${sp.live_adapter_connected ? "live" : ""}`}
            />
            <text x={sp.x} y={sp.y + 3} class="galaxy-label">{sp.specialist_id.slice(0, 8)}</text>
            <text x={sp.x} y={sp.y + 30} class="galaxy-sublabel">
              {sp.role} · {sp.historical_runs.runs_total}次
            </text>
          </g>
        {/each}

        <!-- Center: Muse Commander -->
        <circle cx={galaxyLayout.center.x} cy={galaxyLayout.center.y} r="36" fill="#0d233d" stroke="#ffb64a" stroke-width="2" />
        <text x={galaxyLayout.center.x} y={galaxyLayout.center.y - 3} class="astro-core-sub">COMMANDER</text>
        <text x={galaxyLayout.center.x} y={galaxyLayout.center.y + 12} class="galaxy-label">唯一调度权威</text>
      </svg>
    </div>

    <!-- Right: Route Preview Sandbox + Specialist Inspector -->
    <div class="cyber-card">
      <div class="route-preview-box">
        <b>统帅路由沙盒预演（只读 · 不实际派工）</b>
        <div class="route-form-row">
          <input
            type="text"
            class="cyber-input"
            bind:value={routeGoal}
            maxlength="500"
            placeholder="输入目标，例如：研究并开发系统架构与安全审查测试"
            onkeydown={(e) => { if (e.key === "Enter") void runRoutePreview(); }}
          />
          <button type="button" class="vs-btn primary" onclick={runRoutePreview} disabled={!connected || previewBusy || !routeGoal.trim()}>
            {previewBusy ? "预演中…" : "路由预演"}
          </button>
        </div>
        {#if previewError}<p class="feedback-message" role="alert">{previewError}</p>{/if}

        {#if routePreview}
          <small>
            命中 {routePreview.considered.length} 个候选角色 · 当前实连可用 {routePreview.live_ready_count} 个 ·
            {routePreview.dispatched ? "已派发" : "只读预演未派工"}
          </small>
          {#if routePreview.selected.length}
            <div class="neighbor-list">
              {#each routePreview.selected as cand (cand.specialist_id)}
                <div class="telemetry-box">
                  <small>评分 {cand.score} · 信任等级 {cand.trust_tier} · {cand.live_adapter_connected ? "实连在线" : "模板元数据 · 待接入执行器"}</small>
                  <strong>{cand.specialist_id} ({cand.role})</strong>
                  <span>命中领域：{cand.matched_domains.join("、") || "—"} · 关键词：{cand.matched_keywords.join("、") || "—"}</span>
                </div>
              {/each}
            </div>
          {:else}
            <div class="empty-state compact">未命中专家关键词，建议尝试包含「研究 / 架构 / 开发 / 审查 / 数据 / 运维 / 规划 / 文档」。</div>
          {/if}
        {/if}
      </div>

      {#if selectedSpecialist}
        <div class="astrolabe-inspector">
          <div class="inspector-head">
            <strong>🪐 {selectedSpecialist.specialist_id} · {selectedSpecialist.role}</strong>
            <span class={`cyber-pill ${selectedSpecialist.live_adapter_connected ? "mint" : "gold"}`}>
              {selectedSpecialist.live_adapter_connected ? "实连在线" : "模板元数据 · 待接入执行器"}
            </span>
          </div>
          <p class="inspector-body">{selectedSpecialist.description}</p>
          <div class="inspector-meta">
            <span>信任等级：{selectedSpecialist.trust_tier}</span>
            <span>历史运行：{selectedSpecialist.historical_runs.runs_total}（成功 {selectedSpecialist.historical_runs.runs_succeeded}）</span>
          </div>
        </div>
      {/if}

      <div>
        <small>内置专家角色目录（8 席 · 按历史 Agent Runs 真实统计）</small>
        <div class="specialist-cards">
          {#each galaxy?.specialist_profiles ?? [] as sp (sp.specialist_id)}
            <div class="specialist-card" class:matched={matchedIds.has(sp.specialist_id)}>
              <div class="inspector-head">
                <b>{sp.specialist_id}</b>
                <span class="cyber-pill">{sp.trust_tier}</span>
              </div>
              <small>{sp.description}</small>
              <small>
                {sp.live_adapter_connected ? "● 实连在线" : "○ 模板元数据"} · 历史 {sp.historical_runs.runs_total} 次
              </small>
            </div>
          {/each}
        </div>
      </div>
    </div>
  </div>

  <div class="worker-heading">
    <b>数字员工组织树 · 职位与汇报关系</b>
    <span>{orgChart ? `${orgChart.total} 名员工 · 展示 ${orgChart.nodes.length}` : "组织快照尚未读取"}</span>
  </div>
  {#if !registryConfigured}
    <div class="empty-state compact">组织树等待 Agent Registry 接入。</div>
  {:else if !orgChart}
    <div class="empty-state compact">后端暂未提供组织树快照；现有 Agent 状态卡仍可查看。</div>
  {:else if orgRows.length === 0}
    <div class="empty-state compact">组织架构尚为空。登记 Agent 并设置职位与直属上级后，组织树会自动呈现。</div>
  {:else}
    {#if orgChart.truncated}
      <p class="org-chart-warning" role="status">组织树快照已截断：当前显示 {orgChart.nodes.length} / {orgChart.total} 名员工；未加载的员工不会被误判为不存在。</p>
    {/if}
    {#if orgChart.unresolved_parent_count > 0}
      <p class="org-chart-warning" role="status">有 {orgChart.unresolved_parent_count} 名员工的直属上级不在当前快照内，可能是截断或遗留孤立关系；请核对 Registry。</p>
    {/if}
    <div class="org-chart-list" aria-label="数字员工汇报关系树">
      {#each orgRows as row (row.node.agent_id)}
        {@const node = row.node}
        {@const state = orgStatus(node.status)}
        <article class="org-tree-row" style={`margin-left: ${row.depth * 18}px`}>
          <div class="org-tree-main">
            <span class="org-tree-branch" aria-hidden="true">{row.depth === 0 ? "◆" : "└"}</span>
            <div class="org-tree-identity">
              <strong>{node.display_name || node.agent_id}</strong>
              <span>{node.title || node.role || "未设置职位"} · {node.role || "未分类角色"}</span>
            </div>
            <span class={`status-pill status-${state.tone}`}>{state.label}</span>
          </div>
          <div class="org-tree-meta">
            <span>ID：{node.agent_id}</span>
            {#if node.reports_to}
              <span>直属上级：{node.reports_to}</span>
            {:else}
              <span>顶层员工</span>
            {/if}
            {#if row.unresolvedParent}
              <span class="org-tree-unresolved">上级未出现在当前快照 / 关系需核对</span>
            {/if}
          </div>
          <div class="capabilities">
            {#if node.capabilities?.length}
              {#each node.capabilities as capability}
                <span class="capability">{capability}</span>
              {/each}
            {:else}
              <span class="capability muted">未声明能力</span>
            {/if}
          </div>
        </article>
      {/each}
    </div>
  {/if}

  <!-- Live Registered Agents Section -->
  <div class="worker-heading">
    <b>实连 Agent 注册表（AgentRegistry）</b>
    <span>{registryConfigured ? `${agents.length} 个已登记实连` : "Registry 未配置"}</span>
  </div>
  {#if !registryConfigured}
    <div class="empty-state compact">Agent Registry 尚未接入。请配置 --agent-db 并启动本地健康探测端点。</div>
  {:else if agents.length === 0}
    <div class="empty-state compact">
      Registry 已连接，当前登记了 0 个实连外部 Agent（已按宪章拒绝拿模板冒充在线）。
      历史已完成 {galaxy?.orchestration_summary?.total_runs ?? 0} 次内部多智能体编排；接入本地 HTTP / Ruflo Agent 后将在此轨道实时亮起。
    </div>
  {:else}
    <div class="agent-grid" aria-live="polite">
      {#each agents as agent (agent.agent_id)}
        {@const status: AgentStatus = statusLabels[agent.status] ? agent.status : "offline"}
        {@const stats = agent.effectiveness ?? {}}
        <article class="agent-card">
          <div class="agent-card-head">
            <span class="agent-glyph">{(agent.display_name || agent.agent_id || "?").slice(0, 1)}</span>
            <span class="status-pill status-{status}">{statusLabels[status]}</span>
          </div>
          <h3>{agent.display_name || agent.agent_id}</h3>
          <p class="agent-role">{agent.role || "未分类角色"} · {agent.source || "来源未知"}</p>
          <div class="capabilities">
            {#if agent.capabilities?.length}
              {#each agent.capabilities as capability}
                <span class="capability">{capability}</span>
              {/each}
            {:else}
              <span class="capability muted">未声明能力</span>
            {/if}
          </div>
          <div class="agent-stats">
            <span>任务成功 <b>{percent(stats.success_rate)}</b></span>
            <span>审查通过 <b>{percent(stats.review_pass_rate)}</b></span>
          </div>
          <div class="agent-foot">
            <span>心跳：{relativeTime(agent.last_seen_at)}</span>
            <span>完成 {Number(stats.completed ?? 0)}</span>
          </div>
        </article>
      {/each}
    </div>
  {/if}
</section>

<style>
  .org-chart-list {
    display: grid;
    gap: 8px;
    padding: 4px 0 14px;
  }

  .org-tree-row {
    min-width: 0;
    padding: 10px 12px;
    border: 1px solid var(--hair);
    border-radius: 8px;
    background: rgba(4, 20, 35, 0.62);
  }

  .org-tree-main {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .org-tree-branch {
    width: 16px;
    color: var(--cyan);
    font: 12px var(--font-data);
  }

  .org-tree-identity {
    display: grid;
    flex: 1;
    min-width: 180px;
    gap: 2px;
  }

  .org-tree-identity strong {
    color: var(--ivory);
    font-size: 13px;
    font-weight: 600;
  }

  .org-tree-identity span,
  .org-tree-meta {
    color: var(--muted);
    font-size: 10px;
  }

  .org-tree-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 14px;
    margin: 7px 0 7px 26px;
  }

  .org-tree-unresolved,
  .org-chart-warning {
    color: var(--amber);
  }

  .org-chart-warning {
    margin: 4px 0 8px;
    font-size: 11px;
  }

  @media (max-width: 680px) {
    .org-tree-row {
      margin-left: 0 !important;
    }
  }
</style>
