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
  const H = 520;
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
      const rx = 246;
      const ry = 166;
      const x = CX + Math.cos(angle) * rx;
      const y = CY + Math.sin(angle) * ry;
      const zh = ZH[p.specialist_id] ?? { name: p.role, glyph: p.role.slice(0, 1) };
      const cos = Math.cos(angle);
      const sin = Math.sin(angle);
      const side: Node["side"] =
        cos > 0.22 ? "right" : cos < -0.22 ? "left" : sin > 0 ? "bottom" : "top";
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
        side,
      };
    });
  }

  $: running = queue?.counts.running ?? 0;
  $: queued = queue?.counts.queued ?? 0;
  $: waiting = queue?.counts.waiting_approval ?? 0;
  $: products = prometheus?.stages.creation.products_count ?? 0;
  $: nodesTotal = prometheus?.stages.discovery.knowledge_nodes_total ?? 0;
  $: coreState = !connected ? "未连接" : stale ? "数据过期" : "运行中";

  function ticks(count: number, deltas: number[]) {
    const [r1, r2, ry1, ry2] = deltas;
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

  $: ringTicks = ticks(60, [212, 220, 137, 145]);

  const PLATE_W = 86;

  function platePos(node: Node) {
    if (node.side === "right") {
      return { tx: node.x + 44, ty: node.y + 36, anchor: "start" as const, boxX: node.x + 21, boxY: node.y + 22 };
    }
    if (node.side === "left") {
      return { tx: node.x - 44, ty: node.y + 36, anchor: "end" as const, boxX: node.x - 21 - PLATE_W, boxY: node.y + 22 };
    }
    if (node.side === "bottom") {
      return { tx: node.x, ty: node.y + 42, anchor: "middle" as const, boxX: node.x - PLATE_W / 2, boxY: node.y + 28 };
    }
    return { tx: node.x, ty: node.y - 32, anchor: "middle" as const, boxX: node.x - PLATE_W / 2, boxY: node.y - 46 };
  }

  function nodeKey(event: KeyboardEvent, id: string): void {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect?.(id);
    }
  }
</script>

<div class="co">
  <svg viewBox="0 38 720 442" role="img" aria-label="调度阵：Muse 核心与专家角色节点">
    <defs>
      <radialGradient id="coCoreGlow" cx="50%" cy="46%" r="52%">
        <stop offset="0%" stop-color="#f6e4b4" stop-opacity="0.42" />
        <stop offset="34%" stop-color="#c8a24a" stop-opacity="0.16" />
        <stop offset="72%" stop-color="#7a5f22" stop-opacity="0.05" />
        <stop offset="100%" stop-color="#000" stop-opacity="0" />
      </radialGradient>
      <radialGradient id="coPlateFill" cx="50%" cy="0%" r="120%">
        <stop offset="0%" stop-color="#1a1710" />
        <stop offset="100%" stop-color="#0a0b10" />
      </radialGradient>
    </defs>

    <ellipse cx={CX} cy={CY} rx="266" ry="184" fill="url(#coCoreGlow)" />

    <g class="co-rails">
      <ellipse cx={CX} cy={CY} rx="246" ry="166" class="co-rail-outer" />
      <ellipse cx={CX} cy={CY} rx="196" ry="132" class="co-rail-mid" />
      <ellipse cx={CX} cy={CY} rx="140" ry="94" class="co-rail-in" />
    </g>

    <g class="co-ticks">
      {#each ringTicks as t, i (i)}
        <line x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2} class:co-major={t.major} />
      {/each}
    </g>

    <g class="co-links">
      {#each nodes as nd (nd.id)}
        <path
          d={`M ${CX} ${CY} Q ${(CX + nd.x) / 2 + (nd.y - CY) * 0.14} ${(CY + nd.y) / 2 - (nd.x - CX) * 0.14} ${nd.x} ${nd.y}`}
          class:co-live-link={nd.live}
          class:co-selected={selected === nd.id}
        />
      {/each}
      {#each liveAgents.slice(0, 10) as agent, i (agent.agent_id)}
        {@const a = (i / Math.max(1, Math.min(10, liveAgents.length))) * Math.PI * 2 - Math.PI / 2}
        <circle cx={CX + Math.cos(a) * 158} cy={CY + Math.sin(a) * 106} r="2.6" class="co-live-dot" />
      {/each}
    </g>

    <g class="co-core" class:co-offline={!connected || stale}>
      <circle cx={CX} cy={CY} r="66" class="co-core-halo" />
      <circle cx={CX} cy={CY} r="52" class="co-core-ring" />
      <circle cx={CX} cy={CY} r="43" class="co-core-disc" />
      <text x={CX} y={CY - 3} class="co-core-mark">Muse</text>
      <text x={CX} y={CY + 16} class="co-core-state">{coreState}</text>
    </g>

    {#each nodes as nd (nd.id)}
      {@const plate = platePos(nd)}
      <g
        class="co-node"
        class:co-live={nd.live}
        class:co-active={selected === nd.id}
        role="button"
        tabindex="0"
        aria-label={`${nd.zh} · ${nd.role} · 历史 ${nd.runs} 次`}
        onclick={() => onSelect?.(nd.id)}
        onkeydown={(e) => nodeKey(e, nd.id)}
      >
        <circle cx={nd.x} cy={nd.y} r="15" class="co-node-halo" />
        <circle cx={nd.x} cy={nd.y} r="10.5" class="co-node-disc" />
        <text x={nd.x} y={nd.y + 3.4} class="co-node-glyph">{nd.glyph}</text>
        <rect class="co-plate" x={plate.boxX} y={plate.boxY} width={PLATE_W} height="28" rx="3" />
        <text x={plate.tx} y={plate.boxY + 12} class="co-plate-name" text-anchor={plate.anchor}>{nd.zh}</text>
        <text x={plate.tx} y={plate.boxY + 23} class="co-plate-meta" text-anchor={plate.anchor}>{nd.live ? "实连" : "待接入"} · {nd.runs}</text>
      </g>
    {/each}
  </svg>

  <div class="co-caption">
    <span class:co-ok={connected && !stale}>{connected && !stale ? "核心在线" : "核心未连接"}</span>
    <span>排队 {queued}</span>
    <span class:co-hot={waiting > 0}>待批 {waiting}</span>
    <span class:co-running={running > 0}>执行 {running}</span>
    <span>成片 {products}</span>
    <span>知识 {nodesTotal}</span>
  </div>
  {#if nodes.length === 0}
    <p class="co-empty">角色目录尚未读取：本地核心返回专家档案后，调度阵会按真实角色亮起。</p>
  {/if}
</div>

<style>
  .co {
    display: grid;
    gap: 6px;
    min-width: 0;
  }

  .co svg {
    display: block;
    /* Constrain both axes so the drawn ring, not an empty letterbox, fills the
       card. Short desktop windows (653 CSS px at 125% DPI) used to leave the
       orbit stranded in dead space. */
    width: auto;
    height: auto;
    max-width: 100%;
    aspect-ratio: 720 / 442;
    max-height: min(44vh, 420px);
    margin: 0 auto;
    overflow: visible;
  }

  .co-rail-outer {
    fill: none;
    stroke: rgba(200, 162, 74, 0.26);
    stroke-width: 1;
  }

  .co-rail-mid {
    fill: none;
    stroke: rgba(120, 140, 190, 0.16);
    stroke-width: 1;
    stroke-dasharray: 2 6;
  }

  .co-rail-in {
    fill: none;
    stroke: rgba(200, 162, 74, 0.13);
    stroke-width: 1;
  }

  .co-ticks line {
    stroke: rgba(200, 162, 74, 0.18);
    stroke-width: 0.8;
  }

  .co-ticks line.co-major {
    stroke: rgba(240, 215, 155, 0.42);
    stroke-width: 1;
  }

  .co-links path {
    fill: none;
    stroke: rgba(200, 162, 74, 0.2);
    stroke-width: 1;
  }

  .co-links path.co-live-link {
    stroke: rgba(79, 183, 154, 0.42);
  }

  .co-links path.co-selected {
    stroke: rgba(240, 215, 155, 0.85);
    stroke-width: 1.4;
  }

  .co-live-dot {
    fill: #4fb79a;
    opacity: 0.85;
  }

  .co-core-halo {
    fill: none;
    stroke: rgba(240, 215, 155, 0.2);
    stroke-width: 1;
  }

  .co-core-ring {
    fill: none;
    stroke: rgba(200, 162, 74, 0.55);
    stroke-width: 1.1;
  }

  .co-core-disc {
    fill: #0d0c0a;
    stroke: rgba(240, 215, 155, 0.3);
    stroke-width: 0.8;
  }

  .co-core-mark {
    fill: #f0d79b;
    font: 600 19px "Noto Serif SC", Georgia, serif;
    letter-spacing: 0.05em;
    text-anchor: middle;
  }

  .co-core-state {
    fill: #8d8a80;
    font: 8.5px "Cascadia Mono", Consolas, monospace;
    letter-spacing: 0.12em;
    text-anchor: middle;
  }

  .co-core.co-offline .co-core-ring {
    stroke: rgba(196, 84, 74, 0.42);
  }

  .co-core.co-offline .co-core-mark {
    fill: #d9b6ae;
  }

  .co-node {
    cursor: pointer;
  }

  .co-node-halo {
    fill: rgba(200, 162, 74, 0.07);
    stroke: rgba(200, 162, 74, 0.2);
    stroke-width: 0.8;
  }

  .co-node.co-live .co-node-halo {
    fill: rgba(79, 183, 154, 0.1);
    stroke: rgba(79, 183, 154, 0.3);
  }

  .co-node.co-active .co-node-halo {
    fill: rgba(240, 215, 155, 0.18);
    stroke: rgba(240, 215, 155, 0.62);
  }

  .co-node-disc {
    fill: #11131a;
    stroke: rgba(200, 162, 74, 0.5);
    stroke-width: 1;
  }

  .co-node-glyph {
    fill: #e4d7b2;
    font: 600 10px "Noto Serif SC", "Microsoft YaHei", serif;
    text-anchor: middle;
  }

  .co-plate {
    fill: url(#coPlateFill);
    stroke: rgba(200, 162, 74, 0.3);
    stroke-width: 0.8;
  }

  .co-node.co-active .co-plate {
    stroke: rgba(240, 215, 155, 0.8);
  }

  .co-plate-name {
    fill: #e9e4d8;
    font: 500 11px "Noto Sans SC", "Microsoft YaHei", sans-serif;
    letter-spacing: 0.12em;
  }

  .co-plate-meta {
    fill: #7b786e;
    font: 8px "Cascadia Mono", Consolas, monospace;
    letter-spacing: 0.06em;
  }

  .co-caption {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 3px 16px;
    font: 10px "Cascadia Mono", Consolas, monospace;
    letter-spacing: 0.04em;
    color: #6e6b63;
  }

  .co-caption span.co-ok {
    color: #4fb79a;
  }

  .co-caption span.co-hot {
    color: #d8a44a;
  }

  .co-caption span.co-running {
    color: #5ec8d8;
  }

  .co-empty {
    margin: 0;
    padding: 9px 12px;
    border: 1px dashed #232833;
    border-radius: 8px;
    color: #6e6b63;
    font-size: 11px;
    text-align: center;
    line-height: 1.7;
  }

  @media (prefers-reduced-motion: no-preference) {
    .co-core-halo {
      animation: co-breathe 6.5s ease-in-out infinite;
    }

    .co-live-dot {
      animation: co-pulse 3.4s ease-in-out infinite;
    }

    @keyframes co-breathe {
      50% {
        stroke: rgba(240, 215, 155, 0.42);
        stroke-width: 1.6;
      }
    }

    @keyframes co-pulse {
      50% {
        opacity: 0.3;
      }
    }
  }
</style>
