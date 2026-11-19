<script lang="ts">
  import { onMount } from "svelte";
  import type {
    ApiClient,
    ClaimDossier,
    KnowledgeGraphResponse,
    KnowledgeNodeDetailResponse,
  } from "./api";
  import {
    KNOWLEDGE_TYPE_META,
    layoutKnowledgeConstellation,
    type PositionedKnowledgeNode,
  } from "../../cyber-astrolabe.js";

  export let client: ApiClient | null = null;
  export let connected = false;
  export let graph: KnowledgeGraphResponse | null = null;
  export let onQueryGraph: ((opts: { nodeType: string | null; q: string }) => Promise<void>) | undefined = undefined;

  const LOGICAL_W = 920;
  const LOGICAL_H = 520;

  let canvasEl: HTMLCanvasElement | null = null;
  let nodeTypeFilter = "";
  let searchQuery = "";
  let filterBusy = false;

  let zoom = 1;
  let panX = 0;
  let panY = 0;
  let dragging = false;
  let dragMoved = false;
  let lastPtrX = 0;
  let lastPtrY = 0;

  let hoveredId: string | null = null;
  let selectedId: string | null = null;
  let nodeDetail: KnowledgeNodeDetailResponse | null = null;
  let detailBusy = false;
  let detailError = "";
  let claimDossier: ClaimDossier | null = null;
  let dossierBusy = false;
  let dossierError = "";

  $: layout = layoutKnowledgeConstellation(
    graph?.nodes ?? [],
    graph?.edges ?? [],
    LOGICAL_W,
    LOGICAL_H,
  );

  $: activeHighlightId = selectedId ?? hoveredId;

  $: if (canvasEl && layout) {
    drawStarMap();
  }

  onMount(() => {
    drawStarMap();
  });

  function drawStarMap(): void {
    if (!canvasEl) return;
    const ctx = canvasEl.getContext("2d");
    if (!ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const targetW = Math.round(LOGICAL_W * dpr);
    const targetH = Math.round(LOGICAL_H * dpr);
    if (canvasEl.width !== targetW || canvasEl.height !== targetH) {
      canvasEl.width = targetW;
      canvasEl.height = targetH;
    }

    ctx.save();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, LOGICAL_W, LOGICAL_H);

    // Apply pan & zoom around center
    ctx.translate(LOGICAL_W / 2 + panX, LOGICAL_H / 2 + panY);
    ctx.scale(zoom, zoom);
    ctx.translate(-LOGICAL_W / 2, -LOGICAL_H / 2);

    const cx = LOGICAL_W / 2;
    const cy = LOGICAL_H / 2;

    // Celestial astrolabe coordinate rings on star map
    ctx.strokeStyle = "rgba(40, 88, 138, 0.35)";
    ctx.lineWidth = 1 / zoom;
    for (const r of [90, 175, 240]) {
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.moveTo(cx - 255, cy);
    ctx.lineTo(cx + 255, cy);
    ctx.moveTo(cx, cy - 245);
    ctx.lineTo(cx, cy + 245);
    ctx.stroke();

    const highlightNeighbors = new Set<string>();
    if (activeHighlightId) {
      for (const edge of layout.edges) {
        if (edge.source_id === activeHighlightId) highlightNeighbors.add(edge.target_id);
        if (edge.target_id === activeHighlightId) highlightNeighbors.add(edge.source_id);
      }
    }

    // Draw edges
    for (const edge of layout.edges) {
      const isActiveEdge =
        activeHighlightId !== null &&
        (edge.source_id === activeHighlightId || edge.target_id === activeHighlightId);
      ctx.beginPath();
      ctx.moveTo(edge.x1, edge.y1);
      ctx.lineTo(edge.x2, edge.y2);
      ctx.strokeStyle = isActiveEdge
        ? "rgba(255, 182, 74, 0.85)"
        : activeHighlightId
          ? "rgba(45, 92, 136, 0.14)"
          : "rgba(56, 130, 178, 0.28)";
      ctx.lineWidth = (isActiveEdge ? 1.8 : 0.8) / zoom;
      ctx.stroke();
    }

    // Draw star nodes
    for (const node of layout.nodes) {
      const isSelected = node.id === selectedId;
      const isHovered = node.id === hoveredId;
      const isNeighbor = highlightNeighbors.has(node.id);
      const dimmed = activeHighlightId !== null && !isSelected && !isHovered && !isNeighbor;

      if (!dimmed && (node.degree >= 4 || isSelected || isHovered)) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, node.r + 5 / zoom, 0, Math.PI * 2);
        ctx.fillStyle = isSelected ? "rgba(255, 182, 74, 0.24)" : "rgba(0, 240, 255, 0.14)";
        ctx.fill();
      }

      ctx.beginPath();
      ctx.arc(node.x, node.y, node.r, 0, Math.PI * 2);
      ctx.fillStyle = node.color;
      ctx.globalAlpha = dimmed ? 0.28 : 0.95;
      ctx.fill();
      ctx.globalAlpha = 1;

      if (isSelected || isHovered) {
        ctx.lineWidth = 2 / zoom;
        ctx.strokeStyle = "#ffffff";
        ctx.stroke();
      }

      if (isSelected || isHovered || (zoom >= 1.15 && node.degree >= 5) || (!activeHighlightId && node.degree >= 8)) {
        const label = node.title.length > 22 ? node.title.slice(0, 22) + "…" : node.title;
        ctx.font = `${Math.max(9, Math.round(11 / Math.sqrt(zoom)))}px "Microsoft YaHei", sans-serif`;
        ctx.fillStyle = isSelected ? "#ffe3a0" : "#dbeeff";
        ctx.fillText(label, node.x + node.r + 5, node.y + 3);
      }
    }

    ctx.restore();
  }

  function toGraphCoords(clientX: number, clientY: number): { x: number; y: number } | null {
    if (!canvasEl) return null;
    const rect = canvasEl.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    const lx = ((clientX - rect.left) / rect.width) * LOGICAL_W;
    const ly = ((clientY - rect.top) / rect.height) * LOGICAL_H;
    const gx = (lx - LOGICAL_W / 2 - panX) / zoom + LOGICAL_W / 2;
    const gy = (ly - LOGICAL_H / 2 - panY) / zoom + LOGICAL_H / 2;
    return { x: gx, y: gy };
  }

  function findNodeAt(clientX: number, clientY: number): PositionedKnowledgeNode | null {
    const pt = toGraphCoords(clientX, clientY);
    if (!pt) return null;
    let best: PositionedKnowledgeNode | null = null;
    let bestDist = Infinity;
    for (const node of layout.nodes) {
      const dist = Math.hypot(node.x - pt.x, node.y - pt.y);
      const hitRadius = node.r + 7 / zoom;
      if (dist <= hitRadius && dist < bestDist) {
        best = node;
        bestDist = dist;
      }
    }
    return best;
  }

  function onPointerDown(event: PointerEvent): void {
    dragging = true;
    dragMoved = false;
    lastPtrX = event.clientX;
    lastPtrY = event.clientY;
  }

  function onPointerMove(event: PointerEvent): void {
    if (dragging) {
      const dx = event.clientX - lastPtrX;
      const dy = event.clientY - lastPtrY;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) dragMoved = true;
      panX += dx;
      panY += dy;
      lastPtrX = event.clientX;
      lastPtrY = event.clientY;
      drawStarMap();
      return;
    }
    const hit = findNodeAt(event.clientX, event.clientY);
    const nextHover = hit ? hit.id : null;
    if (nextHover !== hoveredId) {
      hoveredId = nextHover;
      drawStarMap();
    }
  }

  function onPointerUp(event: PointerEvent): void {
    const wasClick = dragging && !dragMoved;
    dragging = false;
    if (wasClick) {
      const hit = findNodeAt(event.clientX, event.clientY);
      if (hit) {
        void inspectNode(hit.id);
      }
    }
  }

  function onWheel(event: WheelEvent): void {
    event.preventDefault();
    const factor = event.deltaY < 0 ? 1.14 : 0.88;
    zoom = Math.max(0.55, Math.min(3.5, Number((zoom * factor).toFixed(3))));
    drawStarMap();
  }

  function adjustZoom(factor: number): void {
    zoom = Math.max(0.55, Math.min(3.5, Number((zoom * factor).toFixed(3))));
    drawStarMap();
  }

  function resetViewport(): void {
    zoom = 1;
    panX = 0;
    panY = 0;
    drawStarMap();
  }

  async function inspectNode(nodeId: string): Promise<void> {
    selectedId = nodeId;
    drawStarMap();
    nodeDetail = null;
    claimDossier = null;
    dossierError = "";
    if (!client || !connected) return;
    detailBusy = true;
    detailError = "";
    try {
      const detail = await client.knowledgeNode(nodeId);
      if (selectedId !== nodeId) return;
      nodeDetail = detail;
      if (detail.node.node_type === "claim") {
        dossierBusy = true;
        try {
          const result = await client.knowledgeClaimDossier(nodeId, 24);
          if (selectedId === nodeId) claimDossier = result.dossier;
        } catch (error) {
          if (selectedId === nodeId) dossierError = error instanceof Error ? error.message : "无法读取主张证据档案";
        } finally {
          if (selectedId === nodeId) dossierBusy = false;
        }
      }
    } catch (error) {
      if (selectedId === nodeId) detailError = error instanceof Error ? error.message : "无法读取知识星体详情";
    } finally {
      if (selectedId === nodeId) detailBusy = false;
    }
  }

  async function applyFilters(): Promise<void> {
    if (!onQueryGraph || filterBusy) return;
    filterBusy = true;
    try {
      await onQueryGraph({
        nodeType: nodeTypeFilter || null,
        q: searchQuery.trim(),
      });
      resetViewport();
    } finally {
      filterBusy = false;
    }
  }
</script>

<section class="cyber-panel" id="knowledge-starmap" aria-label="Research Wiki 赛博知识星图">
  <div class="cyber-head">
    <div>
      <div class="cyber-kicker">02 / RESEARCH WIKI CONSTELLATION &amp; GOVERNANCE</div>
      <h2>知识星图 · Research Wiki 宇宙拓扑</h2>
      <p>实时可视化持久化知识库节点、因果/启发关系边及待审证据候选；支持拖拽、缩放、关键词筛选与邻域星体跃迁。</p>
    </div>
    <div class="cyber-badge-row">
      <span class="cyber-pill cyan">全库节点 {graph?.totals?.active_nodes ?? 0}</span>
      <span class="cyber-pill gold">全库关系边 {graph?.totals?.active_edges ?? 0}</span>
      <span class="cyber-pill violet">当前展示 {graph?.nodes?.length ?? 0} 星体 · {graph?.edges?.length ?? 0} 边</span>
    </div>
  </div>

  <div class="starmap-toolbar">
    <div class="starmap-filters">
      <button
        type="button"
        class="mode-btn"
        class:active={nodeTypeFilter === ""}
        onclick={() => { nodeTypeFilter = ""; void applyFilters(); }}
      >
        全部类型
      </button>
      {#each Object.entries(KNOWLEDGE_TYPE_META) as [typeKey, meta]}
        <button
          type="button"
          class="mode-btn"
          class:active={nodeTypeFilter === typeKey}
          onclick={() => { nodeTypeFilter = typeKey; void applyFilters(); }}
        >
          {meta.label} ({graph?.totals?.by_type?.[typeKey] ?? 0})
        </button>
      {/each}
    </div>

    <div class="cyber-badge-row">
      <input
        type="search"
        class="cyber-input"
        bind:value={searchQuery}
        maxlength="120"
        placeholder="按知识标题或摘要关键词检索…"
        aria-label="搜索知识星图"
        onkeydown={(e) => { if (e.key === "Enter") void applyFilters(); }}
      />
      <button type="button" class="vs-btn primary" onclick={applyFilters} disabled={!connected || filterBusy}>
        {filterBusy ? "筛选中…" : "检索星群"}
      </button>
    </div>
  </div>

  <div class="starmap-layout">
    <div class="starmap-canvas-frame">
      {#if !graph || !graph.configured}
        <div class="empty-state">Research Wiki 数据库尚未配置或连接未就绪。</div>
      {:else if graph.nodes.length === 0}
        <div class="empty-state">当前筛选条件下未找到匹配的知识星体。试试清空关键词或切换节点类型。</div>
      {:else}
        <canvas
          bind:this={canvasEl}
          class="starmap-canvas"
          aria-label="交互式知识星图画布：可拖拽平移、滚轮缩放、点击星体查看详情"
          onpointerdown={onPointerDown}
          onpointermove={onPointerMove}
          onpointerup={onPointerUp}
          onpointerleave={() => { dragging = false; hoveredId = null; drawStarMap(); }}
          onwheel={onWheel}
        ></canvas>
      {/if}

      <div class="starmap-controls-overlay">
        <button type="button" class="mode-btn" onclick={() => adjustZoom(1.25)}>放大 +</button>
        <button type="button" class="mode-btn" onclick={() => adjustZoom(0.8)}>缩小 -</button>
        <button type="button" class="mode-btn" onclick={resetViewport}>重置视口</button>
      </div>

      <div class="starmap-legend" aria-hidden="true">
        {#each Object.entries(KNOWLEDGE_TYPE_META) as [typeKey, meta]}
          <span class="legend-item">
            <i class={`legend-dot ${typeKey}`}></i>
            {meta.label}
          </span>
        {/each}
      </div>
    </div>

    <aside class="starmap-inspector" aria-label="知识星体档案检视器">
      {#if detailBusy}
        <div class="empty-state compact">正在从本机 Research Wiki 读取星体档案…</div>
      {:else if detailError}
        <div class="empty-state error-state compact">{detailError}</div>
      {:else if nodeDetail}
        <div class="inspector-head">
          <strong>{nodeDetail.node.title}</strong>
          <span class="cyber-pill cyan">{nodeDetail.node.node_type} · {nodeDetail.node.status}</span>
        </div>
        <div class="inspector-meta">
          <span>ID: {nodeDetail.node.id.slice(0, 12)}…</span>
          {#if nodeDetail.node.metadata?.epistemic_status}
            <span>性质: {String(nodeDetail.node.metadata.epistemic_status)}</span>
          {/if}
        </div>
        <p class="inspector-body">{nodeDetail.node.summary || "（该星体暂无详细摘要）"}</p>

        {#if nodeDetail.node.node_type === "claim"}
          <section class="claim-dossier" aria-label="Claim 证据档案">
            <div class="inspector-head"><strong>主张证据档案</strong><span class="cyber-pill violet">{claimDossier?.epistemic_status ?? "待读取"}</span></div>
            {#if dossierBusy}
              <div class="empty-state compact">正在读取支持与反向证据…</div>
            {:else if dossierError}
              <div class="empty-state error-state compact">{dossierError}</div>
            {:else if claimDossier}
              <p class="inspector-body">原始主张：{claimDossier.original_claim || claimDossier.title}</p>
              <div class="telemetry-box">
                <small>证据覆盖 · {claimDossier.evidence_coverage.coverage_state}</small>
                <strong>已审核 {claimDossier.evidence_coverage.reviewed_evidence_count} / {claimDossier.evidence.length} 条</strong>
                <span>审核状态：{claimDossier.evidence_coverage.review_coverage_state} · 冲突：{claimDossier.evidence_coverage.evidence_conflict_state}</span>
              </div>
              <div class="evidence-columns">
                <div><small>支持方向（检索候选）</small>
                  {#each claimDossier.evidence.filter((item) => item.direction === "support_search_candidate") as item (item.id)}
                    <article class="telemetry-box"><b>{item.source_title}</b><span>{item.evidence_type} · {item.quality} · {item.independence}</span><small>{item.reviews?.length ? item.reviews[item.reviews.length - 1].verdict + " · " + item.reviews[item.reviews.length - 1].reviewer_id : "待人工审核"}</small>
                      {#if item.source_ref && /^https?:\/\//i.test(item.source_ref)}<a href={item.source_ref} target="_blank" rel="noopener noreferrer">查看来源</a>{/if}
                    </article>
                  {:else}<div class="empty-state compact">暂无支持方向候选</div>{/each}
                </div>
                <div><small>反向方向（反证检索候选）</small>
                  {#each claimDossier.evidence.filter((item) => item.direction === "counter_search_candidate") as item (item.id)}
                    <article class="telemetry-box"><b>{item.source_title}</b><span>{item.evidence_type} · {item.quality} · {item.independence}</span><small>{item.reviews?.length ? item.reviews[item.reviews.length - 1].verdict + " · " + item.reviews[item.reviews.length - 1].reviewer_id : "待人工审核"}</small>
                      {#if item.source_ref && /^https?:\/\//i.test(item.source_ref)}<a href={item.source_ref} target="_blank" rel="noopener noreferrer">查看来源</a>{/if}
                    </article>
                  {:else}<div class="empty-state compact">暂无反向检索候选</div>{/each}
                </div>
              </div>
              <div class="telemetry-box"><small>结论边界</small><span>检索方向不等于证据判决；自动评分关闭。置信度：{claimDossier.confidence.level} · {claimDossier.confidence.basis}</span></div>
            {/if}
          </section>
        {/if}

        {#if nodeDetail.evidence_summary}
          <div class="telemetry-box">
            <small>主张证据候选统计（仅统计 · 不自动晋升事实）</small>
            <strong>共 {nodeDetail.evidence_summary.total} 条候选 · 已人工审查 {nodeDetail.evidence_summary.reviewed_count} 条</strong>
            <span>支持检索 {nodeDetail.evidence_summary.by_direction?.support_search_candidate ?? 0} · 反证检索 {nodeDetail.evidence_summary.by_direction?.counter_search_candidate ?? 0}</span>
          </div>
        {/if}

        {#if nodeDetail.node.provenance?.length}
          <div>
            <small>来源 Provenance</small>
            {#each nodeDetail.node.provenance.slice(0, 4) as prov}
              <div class="telemetry-box"><span>{prov}</span></div>
            {/each}
          </div>
        {/if}

        <div>
          <small>直接关联邻居（点击跃迁 · {nodeDetail.neighbors?.length ?? 0} 个）</small>
          {#if nodeDetail.neighbors?.length}
            <div class="neighbor-list">
              {#each nodeDetail.neighbors as nb (nb.id + nb.edge_type + nb.direction)}
                <button type="button" class="neighbor-btn" onclick={() => inspectNode(nb.id)}>
                  <b>{nb.direction === "outgoing" ? "→" : "←"} [{nb.edge_type}] {nb.title}</b>
                  <small>{nb.node_type} · {nb.status}</small>
                </button>
              {/each}
            </div>
          {:else}
            <div class="empty-state compact">暂无直接关系边（孤立或待关联节点）。</div>
          {/if}
        </div>
      {:else}
        <div class="empty-state compact">
          在左侧知识星图中点击任意星体，即可检视其完整摘要、来源链路、证据候选数量及邻居跃迁。
        </div>
      {/if}
    </aside>
  </div>

  {#if graph?.governance}
    <div class="governance-strip" aria-label="Research Wiki 数据治理诚实度仪表盘">
      <div class="telemetry-box">
        <small>待治理孤立节点 (idea/exp/claim)</small>
        <strong class="warn">{graph.governance.lint_scope_isolated_nodes}</strong>
        <span>需分类并补充关系边</span>
      </div>
      <div class="telemetry-box">
        <small>尚无证据记录的 Claim</small>
        <strong class="warn">{graph.governance.claims_without_evidence}</strong>
        <span>保持 proposed 未验证</span>
      </div>
      <div class="telemetry-box">
        <small>未人工审查的证据候选</small>
        <strong class="warn">{graph.governance.evidence_items_unreviewed}</strong>
        <span>候选证据 ≠ 已验证事实</span>
      </div>
      <div class="telemetry-box">
        <small>已晋升高级状态 Claim</small>
        <strong>{graph.governance.advanced_claims}</strong>
        <span>须具备独立审查签核</span>
      </div>
      <div class="telemetry-box">
        <small>待审来源谱系候选</small>
        <strong>{graph.governance.pending_source_identity_candidates}</strong>
        <span>来源关系 {graph.governance.source_relationships} 条</span>
      </div>
    </div>
  {/if}
</section>
