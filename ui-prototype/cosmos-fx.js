// B391 P2 visual effects: starfield particle layer, star-bagua array and health rings.
// Pure DOM/SVG/canvas, no external assets; honours prefers-reduced-motion.
const SVG_NS = "http://www.w3.org/2000/svg";
const TRIGRAMS = [
  ["☰", "乾"], ["☱", "兑"], ["☲", "离"], ["☳", "震"], ["☴", "巽"], ["☵", "坎"], ["☶", "艮"], ["☷", "坤"],
];

export function svg(tag, attrs = {}, text = "") {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  if (text) node.textContent = text;
  return node;
}

/** Animated star-orbit particle layer drawn on a fixed canvas behind the page. */
export function mountStarfield(canvas, { density = 0.00018 } = {}) {
  const ctx = canvas.getContext("2d");
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let stars = [];
  let width = 0; let height = 0; let frame = 0;
  function resize() {
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = Math.floor(width * ratio); canvas.height = Math.floor(height * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const count = Math.min(420, Math.floor(width * height * density));
    stars = Array.from({ length: count }, () => ({
      r: Math.random() * Math.max(width, height) * 0.62,
      a: Math.random() * Math.PI * 2,
      s: (Math.random() * 0.00025 + 0.00005) * (Math.random() < 0.5 ? 1 : -1),
      size: Math.random() * 1.6 + 0.2,
      hue: Math.random() < 0.18 ? 44 : (Math.random() < 0.5 ? 188 : 262),
      tw: Math.random() * Math.PI * 2,
    }));
  }
  function draw() {
    ctx.clearRect(0, 0, width, height);
    const cx = width * 0.62; const cy = height * 0.28;
    for (const star of stars) {
      if (!reduce) { star.a += star.s; star.tw += 0.03; }
      const x = cx + Math.cos(star.a) * star.r;
      const y = cy + Math.sin(star.a) * star.r * 0.55;
      if (x < -4 || y < -4 || x > width + 4 || y > height + 4) continue;
      const alpha = 0.35 + Math.sin(star.tw) * 0.3 + 0.3;
      ctx.fillStyle = `hsla(${star.hue}, 90%, 78%, ${Math.max(0.1, Math.min(1, alpha))})`;
      ctx.beginPath(); ctx.arc(x, y, star.size, 0, Math.PI * 2); ctx.fill();
    }
    if (!reduce && !document.hidden) frame = window.requestAnimationFrame(draw);
  }
  // B391-P2-22: debounced resize, paused while hidden, and a destroy() that releases every listener.
  let resizeTimer = 0;
  const onResize = () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => { window.cancelAnimationFrame(frame); resize(); draw(); }, 150);
  };
  const onVisibility = () => { window.cancelAnimationFrame(frame); if (!document.hidden) draw(); };
  resize(); draw();
  window.addEventListener("resize", onResize);
  document.addEventListener("visibilitychange", onVisibility);
  return {
    destroy() {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(resizeTimer);
      window.removeEventListener("resize", onResize);
      document.removeEventListener("visibilitychange", onVisibility);
    },
  };
}

/** Star-bagua dynamic array with an energy core. Returns the root <svg>. */
export function buildBagua(target, { coreText = "∞", subText = "Muse" } = {}) {
  const root = svg("svg", { viewBox: "0 0 400 400", role: "img", "aria-label": "星象八卦动态法阵" });
  const defs = svg("defs");
  const glow = svg("radialGradient", { id: "coreGlow" });
  glow.append(svg("stop", { offset: "0%", "stop-color": "#fff7dc" }), svg("stop", { offset: "45%", "stop-color": "#F5C563" }),
    svg("stop", { offset: "100%", "stop-color": "#F5C563", "stop-opacity": "0" }));
  defs.append(glow);
  root.append(defs);
  const outer = svg("g", { class: "spin" });
  outer.append(svg("circle", { cx: 200, cy: 200, r: 188, fill: "none", stroke: "rgba(245,197,99,.55)", "stroke-width": 1.2 }));
  outer.append(svg("circle", { cx: 200, cy: 200, r: 176, fill: "none", stroke: "rgba(34,211,238,.35)", "stroke-dasharray": "2 7" }));
  for (let i = 0; i < 28; i += 1) {
    const angle = (i / 28) * Math.PI * 2;
    outer.append(svg("circle", { cx: 200 + Math.cos(angle) * 182, cy: 200 + Math.sin(angle) * 182, r: i % 7 === 0 ? 3 : 1.4, fill: i % 7 === 0 ? "#F5C563" : "#A78BFA" }));
  }
  root.append(outer);
  const ring = svg("g", { class: "spin-rev" });
  TRIGRAMS.forEach(([glyph, name], i) => {
    const angle = (i / 8) * Math.PI * 2 - Math.PI / 2;
    const x = 200 + Math.cos(angle) * 140; const y = 200 + Math.sin(angle) * 140;
    ring.append(svg("text", { x, y: y + 10, "text-anchor": "middle", "font-size": 30, fill: "#F5C563" }, glyph));
    ring.append(svg("text", { x: 200 + Math.cos(angle) * 108, y: 200 + Math.sin(angle) * 108 + 5, "text-anchor": "middle", "font-size": 13, fill: "#d9d2f5" }, name));
  });
  ring.append(svg("polygon", { points: Array.from({ length: 8 }, (_, i) => {
    const angle = (i / 8) * Math.PI * 2 - Math.PI / 2; return `${200 + Math.cos(angle) * 122},${200 + Math.sin(angle) * 122}`;
  }).join(" "), fill: "none", stroke: "rgba(245,197,99,.5)" }));
  root.append(ring);
  const core = svg("g", { class: "pulse" });
  core.append(svg("circle", { cx: 200, cy: 200, r: 74, fill: "url(#coreGlow)" }));
  core.append(svg("text", { x: 200, y: 214, "text-anchor": "middle", "font-size": 54, fill: "#1b0f2e", "font-weight": 800 }, coreText));
  core.append(svg("text", { x: 200, y: 238, "text-anchor": "middle", "font-size": 14, fill: "#3b2410", "font-weight": 700 }, subText));
  root.append(core);
  target.replaceChildren(root);
  return root;
}

/** Health ring; value null renders as "未知". */
export function ring(target, value, caption) {
  const size = 132; const radius = 54; const circumference = 2 * Math.PI * radius;
  const root = svg("svg", { viewBox: `0 0 ${size} ${size}`, class: "ring", role: "img", "aria-label": caption + "：" + (value === null ? "未知" : value + "%") });
  const defs = svg("defs");
  const grad = svg("linearGradient", { id: "ringGrad", x1: 0, y1: 0, x2: 1, y2: 1 });
  grad.append(svg("stop", { offset: "0%", "stop-color": "#22D3EE" }), svg("stop", { offset: "100%", "stop-color": "#34D399" }));
  defs.append(grad); root.append(defs);
  root.append(svg("circle", { class: "track", cx: 66, cy: 66, r: radius, fill: "none", "stroke-width": 12 }));
  const arc = svg("circle", { class: "value", cx: 66, cy: 66, r: radius, fill: "none", "stroke-width": 12, "stroke-linecap": "round",
    "stroke-dasharray": circumference, "stroke-dashoffset": circumference * (1 - (value === null ? 0 : value / 100)), transform: "rotate(-90 66 66)" });
  root.append(arc);
  root.append(svg("text", { x: 66, y: 68, "text-anchor": "middle", "font-size": 26 }, value === null ? "—" : value + "%"));
  root.append(svg("text", { class: "sub", x: 66, y: 90, "text-anchor": "middle", "font-size": 12 }, caption));
  target.replaceChildren(root);
}
