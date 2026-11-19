<script lang="ts">
  import type {
    BaguaTrigramItem,
    CelestialCatalogResponse,
    GpuResourceStatus,
    PrometheusCoreResponse,
    StarMansionItem,
  } from "./api";
  import {
    buildBaguaRingItems,
    buildPrometheusStageItems,
    buildStarMansionRingItems,
    describeArcPath,
  } from "../../cyber-astrolabe.js";

  export let connected = false;
  export let stale = false;
  export let connectionLabel = "本地核心 · 未连接";
  export let activeCount = 0;
  export let totalAgents = 0;
  export let celestial: CelestialCatalogResponse | null = null;
  export let prometheus: PrometheusCoreResponse | null = null;
  export let gpuResourceStatus: GpuResourceStatus | null = null;
  export let onInspectBagua: ((bits: string) => void) | undefined = undefined;

  let selectedKind: "core" | "bagua" | "mansion" | "stage" = "core";
  let selectedBagua: BaguaTrigramItem | null = null;
  let selectedMansion: StarMansionItem | null = null;
  let selectedStageKey = "ignition";

  $: baguaItems = buildBaguaRingItems(celestial?.bagua_trigrams ?? [], 260, 260, 136);
  $: mansionItems = buildStarMansionRingItems(celestial?.star_mansions ?? [], 260, 260, 206);
  $: stageItems = buildPrometheusStageItems(prometheus?.stages ?? null, 260, 260, 79);

  $: freeRamGiB =
    typeof prometheus?.resource_governor?.available_memory_bytes === "number"
      ? (prometheus.resource_governor.available_memory_bytes / 1024 ** 3).toFixed(1)
      : null;
  $: totalRamGiB =
    typeof prometheus?.resource_governor?.total_memory_bytes === "number"
      ? (prometheus.resource_governor.total_memory_bytes / 1024 ** 3).toFixed(1)
      : null;

  function selectBagua(item: BaguaTrigramItem): void {
    selectedKind = "bagua";
    selectedBagua = item;
  }

  function selectMansion(item: StarMansionItem): void {
    selectedKind = "mansion";
    selectedMansion = item;
  }

  function selectStage(key: string): void {
    selectedKind = "stage";
    selectedStageKey = key;
  }

  function onNodeKey(event: KeyboardEvent, handler: () => void): void {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      handler();
    }
  }

  function stageDetail(key: string): { title: string; summary: string; metrics: string } {
    const s = prometheus?.stages;
    if (!s) {
      return {
        title: "普罗米修斯火种核心",
        summary: "让知识之火不断传递，让未知成为探索的起点。等待本机状态 API 连接后展示五阶真实账本。",
        metrics: "OFFLINE",
      };
    }
    switch (key) {
      case "ignition":
        return {
          title: "01 · 点火 (Ignition)",
          summary: "从灵感工坊、创造者项目与本地提醒捕获创意火花，保留来源与性质边界。",
          metrics: `灵感 ${s.ignition.inspirations_count} · 创意项目 ${s.ignition.initiatives_count} · 提醒 ${s.ignition.reminders_count}`,
        };
      case "discovery":
        return {
          title: "02 · 求知 (Discovery)",
          summary: "跨领域检索公开资料、保存证据快照与网址知识库，寻找支持证据与反例。",
          metrics: `星图节点 ${s.discovery.knowledge_nodes_total} · 证据候选 ${s.discovery.claim_evidence_count} · 站点 ${s.discovery.research_sites_count}`,
        };
      case "synthesis":
        return {
          title: "03 · 融合 (Synthesis)",
          summary: "将跨界线索织成关系边并沉淀为 Muse 哲学原则与可证伪实验假设。",
          metrics: `星图连边 ${s.synthesis.knowledge_edges_total} · 哲学经验 ${s.synthesis.philosophy_experiences_count} · 原则 ${s.synthesis.philosophy_principles_count}`,
        };
      case "creation":
        return {
          title: "04 · 创造 (Creation)",
          summary: "由唯一 Muse Commander 编排专家与本地视频/工程流水线，产出真实可检作品。",
          metrics: `产物 ${s.creation.products_count} · 报告 ${s.creation.artifacts_count} · 编排 ${s.creation.orchestration_runs_count} · 视频尝试 ${s.creation.video_attempts_total}`,
        };
      default:
        return {
          title: "05 · 传承 (Transmission)",
          summary: "双轨隔离记忆、进度账本、Watchdog 自检与可回滚改进提案持续护航下一轮进化。",
          metrics: `User 记忆 ${s.transmission.user_track_memories_count} · Agent 经验 ${s.transmission.agent_track_memories_count} · 进度事件 ${s.transmission.progress_events_count}`,
        };
    }
  }

  $: activeStageInfo = stageDetail(selectedStageKey);
</script>

<section class="astrolabe-hero" aria-label="Muse 赛博玄枢浑天仪">
  <div class="astrolabe-svg-wrap">
    <svg class="astrolabe-svg" viewBox="0 0 520 520" role="img" aria-label="三重星环：二十八星宿、八卦阵盘与普罗米修斯五阶核心">
      <defs>
        <radialGradient id="prometheusCoreGrad" cx="40%" cy="35%" r="65%">
          <stop offset="0%" stop-color="#ffb64a" stop-opacity="0.48" />
          <stop offset="55%" stop-color="#112b4f" stop-opacity="0.96" />
          <stop offset="100%" stop-color="#040814" stop-opacity="1" />
        </radialGradient>
        <linearGradient id="cyberBarGrad" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stop-color="#00f0ff" />
          <stop offset="100%" stop-color="#3bf5c4" />
        </linearGradient>
      </defs>

      <!-- Quadrant background arcs for 28 Mansions -->
      <path d={describeArcPath(260, 260, 242, 0, 88)} class="astro-ring-track" stroke="#00f0ff" stroke-opacity="0.35" />
      <path d={describeArcPath(260, 260, 242, 90, 178)} class="astro-ring-track" stroke="#7aa6ff" stroke-opacity="0.35" />
      <path d={describeArcPath(260, 260, 242, 180, 268)} class="astro-ring-track" stroke="#e3ecff" stroke-opacity="0.35" />
      <path d={describeArcPath(260, 260, 242, 270, 358)} class="astro-ring-track" stroke="#ffb64a" stroke-opacity="0.35" />

      <!-- Rotating cyber orbital tracks -->
      <g class="ring-spin-slow">
        <circle cx="260" cy="260" r="206" class="astro-ring-track dashed" />
        <circle cx="260" cy="260" r="172" class="astro-ring-track violet" />
      </g>
      <g class="ring-spin-reverse">
        <circle cx="260" cy="260" r="136" class="astro-ring-track gold" />
        <circle cx="260" cy="260" r="102" class="astro-ring-track dashed" />
      </g>
      <circle cx="260" cy="260" r="79" class="astro-ring-track" />

      <!-- Crosshair meridians -->
      <line x1="260" y1="22" x2="260" y2="498" stroke="#183252" stroke-width="0.8" />
      <line x1="22" y1="260" x2="498" y2="260" stroke="#183252" stroke-width="0.8" />

      <!-- Outer Ring: 28 Star Mansions -->
      {#each mansionItems as mansion (mansion.name)}
        {@const isSel = selectedKind === "mansion" && selectedMansion?.name === mansion.name}
        <g
          class="astro-node"
          role="button"
          tabindex="0"
          aria-label={`星宿 ${mansion.full_name}：${mansion.theme}`}
          onclick={() => selectMansion(mansion)}
          onkeydown={(e) => onNodeKey(e, () => selectMansion(mansion))}
        >
          <line x1="260" y1="260" x2={mansion.x} y2={mansion.y} stroke="#193452" stroke-width="0.45" opacity="0.55" />
          <circle
            cx={mansion.x}
            cy={mansion.y}
            r={isSel ? 5.8 : 3.8}
            class={`astro-mansion-dot q-${mansion.quadrant} ${isSel ? "selected" : ""}`}
          />
          <text
            x={mansion.lx}
            y={mansion.ly}
            class={`astro-mansion-label ${isSel ? "selected" : ""}`}
          >
            {mansion.name}
          </text>
        </g>
      {/each}

      <!-- Middle Ring: Bagua 8 Trigrams -->
      {#each baguaItems as trigram (trigram.bits_bottom_to_top)}
        {@const isSel = selectedKind === "bagua" && selectedBagua?.bits_bottom_to_top === trigram.bits_bottom_to_top}
        <g
          class="astro-node"
          role="button"
          tabindex="0"
          aria-label={`八卦 ${trigram.name} (${trigram.nature})`}
          onclick={() => selectBagua(trigram)}
          onkeydown={(e) => onNodeKey(e, () => selectBagua(trigram))}
        >
          <rect
            x={trigram.x - 19}
            y={trigram.y - 20}
            width="38"
            height="40"
            rx="4"
            class={`astro-bagua-bg ${isSel ? "selected" : ""}`}
          />
          {#each trigram.linesTopDown as isYang, lineIdx}
            {@const ly = trigram.y - 12 + lineIdx * 5}
            {#if isYang}
              <line x1={trigram.x - 10} y1={ly} x2={trigram.x + 10} y2={ly} class="astro-bagua-yao" />
            {:else}
              <line x1={trigram.x - 10} y1={ly} x2={trigram.x - 2} y2={ly} class="astro-bagua-yao" />
              <line x1={trigram.x + 2} y1={ly} x2={trigram.x + 10} y2={ly} class="astro-bagua-yao" />
            {/if}
          {/each}
          <text x={trigram.x} y={trigram.y + 14} class="astro-bagua-name">{trigram.name}</text>
        </g>
      {/each}

      <!-- Inner Ring: Prometheus 5 Stages -->
      {#each stageItems as stage (stage.key)}
        <g
          class="astro-node"
          role="button"
          tabindex="0"
          aria-label={`普罗米修斯阶段 ${stage.short}`}
          onclick={() => selectStage(stage.key)}
          onkeydown={(e) => onNodeKey(e, () => selectStage(stage.key))}
        >
          <circle cx={stage.x} cy={stage.y} r="17" class="astro-stage-pill" />
          <text x={stage.x} y={stage.y - 2} class="astro-stage-text">{stage.short}</text>
          <text x={stage.x} y={stage.y + 9} class="astro-core-sub">{stage.metric}</text>
        </g>
      {/each}

      <!-- Center: Prometheus Core -->
      <g
        class="astro-node"
        role="button"
        tabindex="0"
        aria-label="普罗米修斯火种核心"
        onclick={() => { selectedKind = "core"; }}
        onkeydown={(e) => onNodeKey(e, () => { selectedKind = "core"; })}
      >
        <circle cx="260" cy="260" r="52" class="astro-ring-track gold core-pulse-ring" />
        <circle cx="260" cy="260" r="44" class="astro-core-circle" />
        <text x="260" y="256" class="astro-core-title">火种</text>
        <text x="260" y="274" class="astro-core-sub">
          {connected && !stale ? "CORE LIVE" : stale ? "STALE" : "OFFLINE"}
        </text>
      </g>
    </svg>
  </div>

  <div class="astrolabe-right">
    <div class="astrolabe-headline">
      <div class="cyber-kicker">MUSE CELESTIAL ENGINE / 星环 · 八卦 · 星宿 · 普罗米修斯核心</div>
      <h2>万象超融合，<em>让灵感长成作品。</em></h2>
      <p>
        以唯一 Muse Commander 为枢轴，将文化符号（星环、八卦、二十八星宿）、研究星图（Research Wiki）、
        多智能体协作与普罗米修斯五阶创造闭环统合于本地可审计秩序之中。
      </p>
      <div class="cyber-badge-row">
        <a href="#celestial-lab" class="primary-link">进入八卦起卦工坊</a>
        <a href="#knowledge-starmap" class="dock-item">跃迁知识星图</a>
        <a href="#agents" class="dock-item">查看 Agent 星系</a>
        <a href="#prometheus-core" class="dock-item">能力与愿景矩阵</a>
      </div>
    </div>

    <div class="astrolabe-inspector" aria-live="polite">
      {#if selectedKind === "bagua" && selectedBagua}
        <div class="inspector-head">
          <strong>{selectedBagua.symbol} {selectedBagua.name}卦 · {selectedBagua.nature}</strong>
          <span class="cyber-pill gold">玄学启发透镜 · 非事实证据</span>
        </div>
        <div class="inspector-meta">
          <span>方位：{selectedBagua.direction}</span>
          <span>五行：{selectedBagua.element}</span>
          <span>二进制爻序（自下而上）：{selectedBagua.bits_bottom_to_top}</span>
        </div>
        <p class="inspector-body">
          <b>系统隐喻：</b>{selectedBagua.system_lens}<br />
          <b>探索提问：</b>{selectedBagua.inquiry_prompt}
        </p>
        {#if onInspectBagua}
          <div>
            <button type="button" class="vs-btn primary" onclick={() => onInspectBagua?.(selectedBagua!.bits_bottom_to_top)}>
              以此卦为下卦推演六十四卦 ↗
            </button>
          </div>
        {/if}
      {:else if selectedKind === "mansion" && selectedMansion}
        <div class="inspector-head">
          <strong>☆ {selectedMansion.full_name}（第 {selectedMansion.index} 宿）</strong>
          <span class="cyber-pill cyan">{celestial?.star_mansion_quadrants?.[selectedMansion.quadrant]?.name ?? selectedMansion.quadrant}</span>
        </div>
        <div class="inspector-meta">
          <span>七政属性：{selectedMansion.element}</span>
          <span>星宿主题：{selectedMansion.theme}</span>
          <span>epistemic: symbolic_inspiration_only</span>
        </div>
        <p class="inspector-body"><b>星官追问：</b>{selectedMansion.inquiry}</p>
      {:else if selectedKind === "stage"}
        <div class="inspector-head">
          <strong>{activeStageInfo.title}</strong>
          <span class="cyber-pill mint">{activeStageInfo.metrics}</span>
        </div>
        <p class="inspector-body">{activeStageInfo.summary}</p>
      {:else}
        <div class="inspector-head">
          <strong>🜂 普罗米修斯火种核心 · Prometheus Core</strong>
          <span class="cyber-pill gold">点击星环任意星宿 / 八卦 / 阶段节点检视</span>
        </div>
        <p class="inspector-body">
          {prometheus?.mission ?? "让知识之火不断传递，让未知成为探索的起点。"}
          —— 外环为四象二十八星宿，中环为先天八卦系统透镜，内环为普罗米修斯五阶实测账本。
        </p>
      {/if}
    </div>

    <div class="astrolabe-telemetry">
      <div class="telemetry-box">
        <small>核心状态 · API</small>
        <strong class:good={connected && !stale} class:warn={!connected && stale}>{connectionLabel}</strong>
        <span>Loopback Bearer 鉴权</span>
      </div>
      <div class="telemetry-box">
        <small>干支时空参考（文化历算）</small>
        <strong>
          {celestial ? `${celestial.calendar_reference.day_ganzhi}日 · ${celestial.calendar_reference.hour_ganzhi}时` : "待读取"}
        </strong>
        <span>{celestial ? `${celestial.calendar_reference.year_ganzhi_civil}年 · 非事实预测` : "—"}</span>
      </div>
      <div class="telemetry-box">
        <small>资源感知并发护栏</small>
        <strong>
          {prometheus ? `安全并发 ${prometheus.resource_governor.recommended_workers} / 上限 ${prometheus.resource_governor.maximum_workers}` : "探测中"}
        </strong>
        <span>
          {prometheus
            ? `${prometheus.resource_governor.logical_cpu_count ?? "?"} 逻辑核 · ${freeRamGiB ?? "?"}/${totalRamGiB ?? "?"} GiB 可用`
            : "保守策略"}
        </span>
      </div>
      <div class="telemetry-box">
        <small>GPU 显存与独占租约</small>
        <strong class:good={gpuResourceStatus?.status === "idle"} class:warn={gpuResourceStatus?.status !== "idle"}>
          {gpuResourceStatus?.status === "reserved"
            ? `占用中 · ${gpuResourceStatus.lease?.owner_kind ?? "task"}`
            : gpuResourceStatus?.status === "idle"
              ? "空闲 · 显存未测 UNKNOWN"
              : "UNKNOWN（禁止 CPU 推测）"}
        </strong>
        <span>宪章约束：未知显存绝不超卖</span>
      </div>
      <div class="telemetry-box">
        <small>Research Wiki 星图</small>
        <strong>
          {prometheus ? `${prometheus.stages.discovery.knowledge_nodes_total} 星体 · ${prometheus.stages.synthesis.knowledge_edges_total} 连边` : "0 星体"}
        </strong>
        <span>候选证据：{prometheus?.stages.discovery.claim_evidence_count ?? 0} 条</span>
      </div>
      <div class="telemetry-box">
        <small>实连 Agent / 双轨记忆</small>
        <strong>{activeCount} / {totalAgents} 实连</strong>
        <span>
          {prometheus
            ? `User ${prometheus.stages.transmission.user_track_memories_count} · Agent ${prometheus.stages.transmission.agent_track_memories_count}（仅计数）`
            : "双轨严格隔离"}
        </span>
      </div>
    </div>
  </div>
</section>
