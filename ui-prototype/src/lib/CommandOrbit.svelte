<script lang="ts">
  import type { GalaxyResponse, QueueSnapshot, PrometheusCoreResponse } from "./api";

  export let galaxy: GalaxyResponse | null = null;
  export let queue: QueueSnapshot | null = null;
  export let prometheus: PrometheusCoreResponse | null = null;
  export let connected = false;
  export let stale = false;
  export let selected = "";
  export let onSelect: ((id: string) => void) | undefined = undefined;

  /** Role plates are driven by real specialist profiles; nothing is invented. */
  const ZH: Record<string, { name: string; glyph: string }> = {
    planner: { name: "统御", glyph: "御" },
    researcher: { name: "求知", glyph: "研" },
    architect: { name: "架构", glyph: "构" },
    developer: { name: "构建", glyph: "建" },
    reviewer: { name: "审核", glyph: "审" },
    operator: { name: "运维", glyph: "维" },
    writer: { name: "文述", glyph: "述" },
    data_analyst: { name: "数析", glyph: "析" },
  };

  const W = 720;
  const H = 470;
  const CX = W / 2;
  const CY = H / 2 + 6;

  type Node = {
    id: string;
    zh: string;
    glyph: string;
    role: string;
    runs: number;
    live: boolean;
    x: number;
    y: number;
    anchor: "start" | "middle" | "end";
    side: "left" | "right" | "top" | "bottom";
  };

  $: profiles = (galaxy?.specialist_profiles ?? []).slice(0, 8);
  $: liveAgents = galaxy?.live_agents ?? [];
  $: nodes = layout(profiles);

  function layout(list: typeof profiles): Node[] {
    const n = Math.max(1, list.length);
    return list.map((p, i) => {
      // Start at 12 o'clock, go clockwise; elliptical so plates breathe at 16:10.
      const angle = (i / n) * Math.PI * 2 - Math.PI / 2;
      const rx = 250;
      const ry = 170;
      const x = CX + Math.cos(angle) * rx;
      const y = CY + Math.sin(angle) * ry;
      const zh = ZH[p.specialist_id] ?? { name: p.role, glyph: p.role.slice(0, 1) };
      const side = Math.cos(angle) > 0.25 ? "right" : Math.cos(angle) < -0.25 ? "left" : "top";
      return {
        id: p.specialist_id,
        zh: zh.name,
        glyph: zh.glyph,
        role: p.role,
        runs: Number(p.historical_runs?.runs_total ?? 0),
        live: Boolean(p.live_adapter_connected),
        x,
        y,
        anchor: side === "right" ? "start" : side === "left" ? "end" : "middle",
        side: side as Node["side"],
      };
    });
  }

  $: running = queue?.counts.running ?? 0;
  $: queued = queue?.counts.queued ?? 0;
  $: waiting = queue?.counts.waiting_approval ?? 0;
  $: products = prometheus?.stages.creation.products_count ?? 0;
  $: nodesTotal = prometheus?.stages.discovery.knowledge_nodes_total ?? 0;
  $: coreState = !connected ? "未连接" : stale ? "数据过期" : "运行中";

  function ticks(count: number, r1: number, r2: number, ry1: number, ry2: number) {
    return Array.from({ length: count }, (_, i) => {
      const a = (i / count) * Math.PI * 2 - Math.PI / 2;
      return {
        x1: CX + Math.cos(a) * r1,
        y1: CY + Math.sin(a) * ry1,
        x2: CX + Math.cos(a) * r2,
        y2: CY + Math.sin(a) * ry2,
        major: i % 5 === 0,
      };
    });
  }

  $: ringTicks = ticks(60, 214, 222, 138, 146);
  $: plateOf = (node: Node) => {
    const dx = node.side === "right" ? 30 : node.side === "left" ? -30 : 0;
    const dy = node.side === "top" ? -44 : 40;
    return { x: node.x + dx, y: node.y + dy };
  };

  function short(text: string, max: number): string {
    return text.length > max ? text.slice(0, max - 1) + "…" : text;
  }
</script>

<div class="orbit">
  <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="调度阵：Muse 核心与专家角色节点">
    <defs>
      <radialGradient id="coreGlow" cx="50%" cy="46%" r="52%">
        <stop offset="0%" stop-color="#f6e4b4" stop-opacity="0.5" />
        <stop offset="34%" stop-color="#c8a24a" stop-opacity="0.2" />
        <stop offset="72%" stop-color="#7a5f22" stop-opacity="0.06" />
        <stop offset="100%" stop-color="#000" stop-opacity="0" />
      </radialGradient>
      <radialGradient id="plateFill" cx="50%" cy="0%" r="120%">
        <stop offset="0%" stop-color="#1a1710" stop-opacity="0.96" />
        <stop offset="100%" stop-color="#0a0b10" stop-opacity="0.96" />
      </radialGradient>
      <linearGradient id="linkGold" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" stop-color="#c8a24a" stop-opacity="0.06" />
        <stop offset="50%" stop-color="#c8a24a" stop-opacity="0.5" />
        <stop offset="100%" stop-color="#c8a24a" stop-opacity="0.06" />
      </linearGradient>
    </defs>

    <g class="halo">
      <ellipse cx={CX} cy={CY} rx="268" ry="186" fill="url(#coreGlow)" />
    </g>

    <!-- orbit rails -->
    <g class="rails">
      <ellipse cx={CX} cy={CY} rx="250" ry="170" class="rail-outer" />
      <ellipse cx={CX} cy={CY} rx="196" ry="132" class="rail-mid" />
      <ellipse cx={CX} cy={CY} rx="140" ry="94" class="rail-in" />
    </g>

    <g class="ticks">
      {#each ringTicks as t, i (i)}
        <line x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2} class:major={t.major} />
      {/each}
    </g>

    <!-- links from core to each role -->
    <g class="links">
      {#each nodes as node (node.id)}
        <path
          d={`M ${CX} ${CY} Q ${(CX + node.x) / 2 + (node.y - CY) * 0.16} ${(CY + node.y) / 2 - (node.x - CX) * 0.16} ${node.x} ${node.y}`}
          class:live={node.live}
          class:active={selected === node.id}
        />
      {/each}
      {#each liveAgents.slice(0, 10) as agent, i (agent.agent_id)}
        {@const a = (i / Math.max(1, Math.min(10, liveAgents.length))) * Math.PI * 2 - Math.PI / 2}
        <circle
          cx={CX + Math.cos(a) * 158}
          cy={CY + Math.sin(a) * 108}
          r="2.6"
          class="live-dot"
        />
      {/each}
    </g>

    <!-- core -->
    <g class="core" class:offline={!connected || stale}>
      <circle cx={CX} cy={CY} r="66" class="core-halo" />
      <circle cx={CX} cy={CY} r="52" class="core-ring" />
      <circle cx={CX} cy={CY} r="44" class="core-disc" />
      <text x={CX} y={CY - 4} class="core-mark">Muse</text>
      <text x={CX} y={CY + 16} class="core-state">{coreState}</text>
    </g>

    <!-- role nodes + plates -->
    {#each nodes as node (node.id)}
      {@const plate = plateOf(node)}
      <g
        class="node"
        class:live={node.live}
        class:active={selected === node.id}
        role="button"
        tabindex="0"
        aria-label={`${node.zh} · ${node.role} · 历史 ${node.runs} 次`}
        onclick={() => onSelect?.(node.id)}
        onkeydown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect?.(node.id); } }}
      >
        <circle cx={node.x} cy={node.y} r="15" class="node-halo" />
        <circle cx={node.x} cy={node.y} r="10.5" class="node-disc" />
        <text x={node.x} y={node.y + 3.4} class="node-glyph">{node.glyph}</text>
        <g class="plate">
          <rect x={plate.x - (node.anchor === "middle" ? 46 : node.anchor === "end" ? 84 : 0)} y={plate.y - 15} width="86" height="30" rx="3" />
          <text
            x={plate.x + (node.anchor === "middle" ? 0 : node.anchor === "end" ? -41 : 43)}
            y={plate.y - 3}
            class="plate-name"
            text-anchor={node.anchor}
          >{node.zh}</text>
          <text
            x={plate.x + (node.anchor === "middle" ? 0 : node.anchor === "end" ? -41 : 43)}
            y={plate.y + 9}
            class="plate-meta"
            text-anchor={node.anchor}
          >{node.live ? "实连" : "待接入"} · {node.runs}</text>
        </g>
      </g>
    {/each}
  </svg>

  <div class="orbit-caption">
    <span class:ok={connected && !stale}>{connected && !stale ? "核心在线" : "核心未连接"}</span>
    <span>排队 {queued}</span>
    <span class:hot={waiting > 0}>待批 {waiting}</span>
    <span class:live={running > 0}>执行 {running}</span>
    <span>成片 {products}</span>
    <span>知识 {nodesTotal}</span>
  </div>
  {#if nodes.length === 0}
    <p class="orbit-empty">角色目录尚未读取：本地核心返回专家档案后，调度阵会按真实角色亮起。</p>
  {/if}
</div>

<style>
  .orbit {
    position: relative;
    display: grid;
    gap: 8px;
    min-width: 0;
  }

  svg {
    display: block;
    width: 100%;
    height: auto;
    overflow: visible;
  }

  .rail-outer {
    fill: none;
    stroke: rgba(200, 162, 74, 0.24);
    stroke-width: 1;
  }

  .rail-mid {
    fill: none;
    stroke: rgba(120, 140, 190, 0.16);
    stroke-width: 1;
    stroke-dasharray: 2 6;
  }

  .rail-in {
    fill: none;
    stroke: rgba(200, 162, 74, 0.13);
    stroke-width: 1;
  }

  .ticks line {
    stroke: rgba(200, 162, 74, 0.2);
    stroke-width: 0.8;
  }

  .ticks line.major {
    stroke: rgba(240, 215, 155, 0.4);
    stroke-width: 1;
  }

  .links path {
    fill: none;
    stroke: url(#linkGold);
    stroke-width: 1;
  }

  .links path.live {
    stroke: rgba(79, 183, 154, 0.4);
  }

  .links path.active {
    stroke: rgba(240, 215, 155, 0.85);
    stroke-width: 1.4;
  }

  .live-dot {
    fill: #4fb79a;
    opacity: 0.85;
  }

  .core-halo {
    fill: none;
    stroke: rgba(240, 215, 155, 0.2);
    stroke-width: 1;
  }

  .core-ring {
    fill: none;
    stroke: rgba(200, 162, 74, 0.55);
    stroke-width: 1.1;
  }

  .core-disc {
    fill: #0d0c0a;
    stroke: rgba(240, 215, 155, 0.32);
    stroke-width: 0.8;
  }

  .core-mark {
    fill: #f0d79b;
    font: 600 19px "Noto Serif SC", Georgia, serif;
    letter-spacing: 0.06em;
    text-anchor: middle;
  }

  .core-state {
    fill: #8d8a80;
    font: 8.5px "Cascadia Mono", Consolas, monospace;
    letter-spacing: 0.14em;
    text-anchor: middle;
  }

  .core.offline .core-ring {
    stroke: rgba(196, 84, 74, 0.45);
  }

  .core.offline .core-mark {
    fill: #d9b6ae;
  }

  .node {
    cursor: pointer;
  }

  .node-halo {
    fill: rgba(200, 162, 74, 0.08);
    stroke: rgba(200, 162, 74, 0.22);
    stroke-width: 0.8;
  }

  .node.live .node-halo {
    fill: rgba(79, 183, 154, 0.1);
    stroke: rgba(79, 183, 154, 0.3);
  }

  .node.active .node-halo {
    fill: rgba(240, 215, 155, 0.18);
    stroke: rgba(240, 215, 155, 0.6);
  }

  .node-disc {
    fill: #11131a;
    stroke: rgba(200, 162, 74, 0.55);
    stroke-width: 1;
  }

  .node-glyph {
    fill: #e4d7b2;
    font: 600 10px "Noto Serif SC", "Microsoft YaHei", serif;
    text-anchor: middle;
  }

  .plate rect {
    fill: url(#plateFill);
    stroke: rgba(200, 162, 74, 0.34);
    stroke-width: 0.8;
  }

  .node.active .plate rect {
    stroke: rgba(240, 215, 155, 0.8);
  }

  .plate-name {
    fill: #e9e4d8;
    font: 500 11px "Noto Sans SC", "Microsoft YaHei", sans-serif;
    letter-spacing: 0.14em;
  }

  .plate-meta {
    fill: #7b786e;
    font: 8px "Cascadia Mono", Consolas, monospace;
    letter-spacing: 0.08em;
  }

  .orbit-caption {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 4px 16px;
    padding-top: 2px;
    font: 10px "Cascadia Mono", Consolas, monospace;
    letter-spacing: 0.05em;
    color: #6e6b63;
  }

  .orbit-caption span.ok {
    color: #4fb79a;
  }

  .orbit-caption span.hot {
    color: #d8a44a;
  }

  .orbit-caption span.live {
    color: #5ec8d8;
  }

  .orbit-empty {
    margin: 0;
    padding: 10px 12px;
    border: 1px dashed #232833;
    border-radius: 8px;
    color: #6e6b63;
    font-size: 11px;
    text-align: center;
    line-height: 1.7;
  }

  @media (prefers-reduced-motion: no-preference) {
    .core-halo {
      animation: core-breathe 6.5s ease-in-out infinite;
    }

    .ticks {
      animation: slow-spin 90s linear infinite;
      transform-origin: 50% 50%;
    }

    .live-dot {
      animation: dot-pulse 3.4s ease-in-out infinite;
    }

    @keyframes core-breathe {
      50% {
        stroke: rgba(240, 215, 155, 0.4);
        stroke-width: 1.6;
      }
    }

    @keyframes slow-spin {
      to {
        transform: rotate(360deg);
      }
    }

    @keyframes dot-pulse {
      50% {
        opacity: 0.3;
      }
    }
  }
</style>
