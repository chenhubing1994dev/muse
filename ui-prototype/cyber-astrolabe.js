export function polarToCartesian(cx, cy, radius, angleDeg) {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return {
    x: Number((cx + radius * Math.cos(rad)).toFixed(2)),
    y: Number((cy + radius * Math.sin(rad)).toFixed(2)),
  };
}

export function describeArcPath(cx, cy, radius, startDeg, endDeg) {
  const span = Math.abs(endDeg - startDeg);
  if (span <= 0.01) return "";
  const clampedEnd = span >= 360 ? startDeg + 359.99 : endDeg;
  const start = polarToCartesian(cx, cy, radius, clampedEnd);
  const end = polarToCartesian(cx, cy, radius, startDeg);
  const largeArc = Math.abs(clampedEnd - startDeg) <= 180 ? "0" : "1";
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArc} 0 ${end.x} ${end.y}`;
}

export function buildBaguaRingItems(trigrams = [], cx = 260, cy = 260, radius = 136) {
  const list = Array.isArray(trigrams) ? trigrams : [];
  const step = list.length ? 360 / list.length : 45;
  return list.map((item, idx) => {
    const angleDeg = idx * step;
    const pos = polarToCartesian(cx, cy, radius, angleDeg);
    const bits = String(item.bits_bottom_to_top ?? "000").padEnd(3, "0").slice(0, 3);
    // Display top line -> bottom line in visual glyphs
    const linesTopDown = [bits[2] === "1", bits[1] === "1", bits[0] === "1"];
    return {
      ...item,
      index: idx,
      angleDeg,
      x: pos.x,
      y: pos.y,
      linesTopDown,
    };
  });
}

export function buildStarMansionRingItems(mansions = [], cx = 260, cy = 260, radius = 212) {
  const list = Array.isArray(mansions) ? mansions : [];
  const step = list.length ? 360 / list.length : 360 / 28;
  return list.map((item, idx) => {
    const angleDeg = idx * step;
    const pos = polarToCartesian(cx, cy, radius, angleDeg);
    const labelPos = polarToCartesian(cx, cy, radius + 21, angleDeg);
    return {
      ...item,
      order: idx,
      angleDeg,
      x: pos.x,
      y: pos.y,
      lx: labelPos.x,
      ly: labelPos.y,
    };
  });
}

export function buildPrometheusStageItems(stages, cx = 260, cy = 260, radius = 78) {
  const defs = [
    { key: "ignition", short: "点火", metric: stages?.ignition?.inspirations_count ?? 0 },
    { key: "discovery", short: "求知", metric: stages?.discovery?.knowledge_nodes_total ?? 0 },
    { key: "synthesis", short: "融合", metric: stages?.synthesis?.knowledge_edges_total ?? 0 },
    { key: "creation", short: "创造", metric: stages?.creation?.products_count ?? 0 },
    { key: "transmission", short: "传承", metric: stages?.transmission?.progress_events_count ?? 0 },
  ];
  return defs.map((item, idx) => {
    const angleDeg = idx * 72;
    const pos = polarToCartesian(cx, cy, radius, angleDeg);
    return { ...item, angleDeg, x: pos.x, y: pos.y };
  });
}

export const KNOWLEDGE_TYPE_META = {
  claim: { label: "主张 Claim", color: "#e0c07a", angleDeg: 0 },
  idea: { label: "灵感 Idea", color: "#c8a24a", angleDeg: 60 },
  source: { label: "来源 Source", color: "#4fb79a", angleDeg: 120 },
  experiment: { label: "实验 Exp", color: "#8e7bd6", angleDeg: 180 },
  decision: { label: "决策 Decision", color: "#d08fa8", angleDeg: 240 },
  failure: { label: "失败 Failure", color: "#c4544a", angleDeg: 300 },
};

function hashFraction(text) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 10000) / 10000;
}

export function layoutKnowledgeConstellation(nodes = [], edges = [], width = 920, height = 560) {
  const safeW = Math.max(320, Number(width) || 920);
  const safeH = Math.max(260, Number(height) || 560);
  const cx = safeW / 2;
  const cy = safeH / 2;
  const maxR = Math.min(safeW, safeH) * 0.42;
  const pad = 34;

  const sectorCounters = new Map();
  const positioned = (Array.isArray(nodes) ? nodes : []).map((node) => {
    const meta = KNOWLEDGE_TYPE_META[node.node_type] || { label: node.node_type, color: "#7cc8e8", angleDeg: 30 };
    const idxInSector = sectorCounters.get(node.node_type) || 0;
    sectorCounters.set(node.node_type, idxInSector + 1);

    const degree = Math.max(0, Number(node.degree) || 0);
    const radialNorm = Math.max(0.14, 0.94 - Math.min(0.7, Math.sqrt(degree) * 0.12) - (idxInSector % 5) * 0.045);
    const jitterAngle = (hashFraction(String(node.id || idxInSector)) - 0.5) * 44;
    const spiralShift = (idxInSector * 9.5) % 36 - 18;
    const rad = (((meta.angleDeg + jitterAngle + spiralShift) - 90) * Math.PI) / 180;
    const dist = radialNorm * maxR;
    const r = Math.min(13, Math.max(3.6, 3.6 + Math.sqrt(degree) * 1.45));

    return {
      ...node,
      color: meta.color,
      r,
      x: Math.max(pad, Math.min(safeW - pad, cx + Math.cos(rad) * dist)),
      y: Math.max(pad, Math.min(safeH - pad, cy + Math.sin(rad) * dist)),
    };
  });

  const byId = new Map(positioned.map((n) => [n.id, n]));
  const activeEdges = (Array.isArray(edges) ? edges : [])
    .map((e) => ({ ...e, source: byId.get(e.source_id), target: byId.get(e.target_id) }))
    .filter((e) => e.source && e.target);

  // Bounded deterministic force relaxation for visual clarity without browser stalls.
  const iterations = positioned.length > 180 ? 10 : 16;
  for (let step = 0; step < iterations; step += 1) {
    for (let i = 0; i < positioned.length; i += 1) {
      const a = positioned[i];
      for (let j = i + 1; j < positioned.length; j += 1) {
        const b = positioned[j];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        const distSq = dx * dx + dy * dy;
        const minDist = a.r + b.r + 14;
        if (distSq > 0.001 && distSq < minDist * minDist * 2.6) {
          const dist = Math.sqrt(distSq);
          const push = ((minDist - dist) / dist) * 0.28;
          if (push > 0) {
            dx *= push;
            dy *= push;
            a.x -= dx;
            a.y -= dy;
            b.x += dx;
            b.y += dy;
          }
        }
      }
    }
    for (const edge of activeEdges) {
      const dx = edge.target.x - edge.source.x;
      const dy = edge.target.y - edge.source.y;
      const dist = Math.hypot(dx, dy) || 1;
      if (dist > 95) {
        const pull = (dist - 95) * 0.015;
        const ux = (dx / dist) * pull;
        const uy = (dy / dist) * pull;
        edge.source.x += ux;
        edge.source.y += uy;
        edge.target.x -= ux;
        edge.target.y -= uy;
      }
    }
    for (const node of positioned) {
      node.x = Number(Math.max(pad, Math.min(safeW - pad, node.x)).toFixed(2));
      node.y = Number(Math.max(pad, Math.min(safeH - pad, node.y)).toFixed(2));
    }
  }

  return {
    bounds: { width: safeW, height: safeH, cx, cy },
    nodes: positioned,
    edges: activeEdges.map((e) => ({
      id: e.id,
      source_id: e.source_id,
      target_id: e.target_id,
      edge_type: e.edge_type,
      x1: e.source.x,
      y1: e.source.y,
      x2: e.target.x,
      y2: e.target.y,
    })),
  };
}

export function layoutAgentGalaxy(galaxy, width = 640, height = 460) {
  const safeW = Math.max(320, Number(width) || 640);
  const safeH = Math.max(280, Number(height) || 460);
  const cx = safeW / 2;
  const cy = safeH / 2;
  const baseR = Math.min(safeW, safeH) / 2;

  const profiles = Array.isArray(galaxy?.specialist_profiles) ? galaxy.specialist_profiles : [];
  const innerR = baseR * 0.48;
  const specialists = profiles.map((item, idx) => {
    const angleDeg = (idx * 360) / Math.max(1, profiles.length);
    const pos = polarToCartesian(cx, cy, innerR, angleDeg);
    return { ...item, angleDeg, x: pos.x, y: pos.y };
  });

  const live = Array.isArray(galaxy?.live_agents) ? galaxy.live_agents : [];
  const midR = baseR * 0.72;
  const liveAgents = live.map((item, idx) => {
    const angleDeg = (idx * 360) / Math.max(1, live.length) + 18;
    const pos = polarToCartesian(cx, cy, midR, angleDeg);
    return { ...item, angleDeg, x: pos.x, y: pos.y };
  });

  const caps = Array.isArray(galaxy?.capabilities) ? galaxy.capabilities.slice(0, 12) : [];
  const outerR = baseR * 0.86;
  const capabilities = caps.map((item, idx) => {
    const angleDeg = (idx * 360) / Math.max(1, caps.length) + 15;
    const pos = polarToCartesian(cx, cy, outerR, angleDeg);
    return { ...item, angleDeg, x: pos.x, y: pos.y };
  });

  return {
    center: { x: cx, y: cy, title: galaxy?.commander?.title || "Muse Commander" },
    orbits: [
      { label: "专家角色轨", r: Number(innerR.toFixed(1)) },
      { label: "实连 Agent 轨", r: Number(midR.toFixed(1)) },
      { label: "本机能力星环", r: Number(outerR.toFixed(1)) },
    ],
    specialists,
    liveAgents,
    capabilities,
  };
}
