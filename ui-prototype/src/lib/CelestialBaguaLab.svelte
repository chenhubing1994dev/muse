<script lang="ts">
  import type {
    ApiClient,
    CelestialCatalogResponse,
    StarMansionItem,
    YijingInquiryResponse,
  } from "./api";

  export let client: ApiClient | null = null;
  export let connected = false;
  export let celestial: CelestialCatalogResponse | null = null;
  export let presetLowerBits = "";
  export let onSaveHypothesisInspiration: ((payload: { title: string; content: string; tags: string[]; domains: string[] }) => Promise<void>) | undefined = undefined;

  let mode: "seeded_cast" | "trigram_pair" | "manual_lines" = "seeded_cast";
  let seed = "muse-prometheus-ember";
  let goal = "";
  let lowerBits = "111";
  let upperBits = "101";
  let manualLines = [7, 8, 9, 7, 6, 8];

  let busy = false;
  let errorMsg = "";
  let saveMsg = "";
  let inquiry: YijingInquiryResponse | null = null;

  let activeQuadrant: "azure_dragon" | "black_tortoise" | "white_tiger" | "vermilion_bird" = "azure_dragon";
  let selectedMansion: StarMansionItem | null = null;
  let lastAppliedPreset = "";

  $: if (presetLowerBits && presetLowerBits !== lastAppliedPreset) {
    lastAppliedPreset = presetLowerBits;
    mode = "trigram_pair";
    lowerBits = presetLowerBits;
    if (connected && client) {
      void runInquiry();
    }
  }

  $: quadrantMansions = (celestial?.star_mansions ?? []).filter((m) => m.quadrant === activeQuadrant);
  $: if (!selectedMansion && quadrantMansions.length) {
    selectedMansion = quadrantMansions[0];
  }

  async function runInquiry(): Promise<void> {
    if (!client || !connected || busy) return;
    busy = true;
    errorMsg = "";
    saveMsg = "";
    try {
      const payload =
        mode === "seeded_cast"
          ? { mode, seed: seed.trim() || "muse-ember", goal: goal.trim() || undefined }
          : mode === "trigram_pair"
            ? { mode, lower_bits: lowerBits, upper_bits: upperBits, goal: goal.trim() || undefined }
            : { mode, line_values: manualLines.map((v) => Number(v)), goal: goal.trim() || undefined };
      inquiry = await client.castYijing(payload);
    } catch (error) {
      errorMsg = error instanceof Error ? error.message : "玄枢推演请求失败";
    } finally {
      busy = false;
    }
  }

  async function saveAsLocalHypothesis(): Promise<void> {
    if (!inquiry || !onSaveHypothesisInspiration) return;
    const p = inquiry.reading.primary;
    const r = inquiry.reading.relating;
    const title = `【玄学假设】第${p.king_wen_number}卦 ${p.king_wen_name} → ${r.king_wen_name}`.slice(0, 80);
    const content = [
      `[性质声明] epistemic_status=${inquiry.epistemic_status}; is_evidence=false; is_prediction=false`,
      goal.trim() ? `探索目标：${goal.trim()}` : "探索目标：系统结构与假设启发",
      `主卦：${p.unicode_symbol} ${p.king_wen_name}（下${p.lower_trigram.name}上${p.upper_trigram.name}），动爻：${inquiry.reading.moving_lines.length ? inquiry.reading.moving_lines.join(",") : "无"}，之卦：${r.unicode_symbol} ${r.king_wen_name}`,
      inquiry.cross_system?.synthesis_candidates?.[0]
        ? `待证伪假设：${inquiry.cross_system.synthesis_candidates[0].hypothesis}\n检验问题：${inquiry.cross_system.synthesis_candidates[0].test_question}\n证伪边界：${inquiry.cross_system.synthesis_candidates[0].falsification_rule}`
        : "待进一步构造可证伪问题并通过实验或公开资料验证。",
    ].join("\n").slice(0, 2000);
    await onSaveHypothesisInspiration({
      title,
      content,
      tags: ["玄学推演", "待验证假设", p.king_wen_name],
      domains: ["跨领域假设", "象数结构启发"],
    });
    saveMsg = "已作为待验证假设存入本地灵感池（默认不联网，绝不写回 User Track）。";
  }

  function updateManualLine(idx: number, raw: string): void {
    const val = Number(raw);
    if (![6, 7, 8, 9].includes(val)) return;
    manualLines = manualLines.map((item, i) => (i === idx ? val : item));
  }

  $: displayLinesTopDown = inquiry ? [...inquiry.reading.lines].reverse() : [];
</script>

<section class="cyber-panel" id="celestial-lab" aria-label="八卦与二十八星宿假设实验室">
  <div class="cyber-head">
    <div>
      <div class="cyber-kicker">01 / CYBER BAGUA &amp; 28 MANSIONS HYPOTHESIS LAB</div>
      <h2>赛博玄枢 · 八卦推演与二十八星宿矩阵</h2>
      <p>将易经象数、四象二十八星宿、奇门/紫微/大六壬对照为可证伪的提问框架与灵感生成器。</p>
    </div>
    <div class="cyber-badge-row">
      <span class="cyber-pill gold">epistemic: symbolic_hypothesis_only</span>
      <span class="cyber-pill cyan">is_evidence: false · is_prediction: false</span>
    </div>
  </div>

  <div class="epistemic-banner" role="note">
    <span>
      <strong>宪章硬边界：</strong>
      八卦起卦（含种子三钱模拟）与星宿透镜仅作文化符号与假设激发工具，绝非物理掷币、吉凶预测或工程事实依据；不会自动写入个人记忆或批准任务。
    </span>
    <span>方法来源：ProfesseurHaipeng/iching-research-skill · muse-yijing-three-coin-v1</span>
  </div>

  <div class="celestial-grid">
    <!-- Left Column: Interactive Yi Jing & Cross-System Inquiry -->
    <div class="cyber-card">
      <div class="mode-switch" role="tablist" aria-label="起卦推演模式">
        <button type="button" class="mode-btn" class:active={mode === "seeded_cast"} onclick={() => (mode = "seeded_cast")}>
          种子三钱模拟
        </button>
        <button type="button" class="mode-btn" class:active={mode === "trigram_pair"} onclick={() => (mode = "trigram_pair")}>
          上下八卦合卦
        </button>
        <button type="button" class="mode-btn" class:active={mode === "manual_lines"} onclick={() => (mode = "manual_lines")}>
          手动六爻输入
        </button>
      </div>

      <div class="cyber-form-grid">
        <label>
          探索问题 / 创意目标（填写入可同时生成「易经 · 奇门 · 紫微 · 大六壬」四体系研究提问）
          <input
            type="text"
            class="cyber-input"
            bind:value={goal}
            maxlength="1000"
            placeholder="例如：如何为 Muse 打造低耦合、资源安全的多 Agent 自演化闭环？"
          />
        </label>

        {#if mode === "seeded_cast"}
          <label>
            可见随机种子（SHA-256 确定性复现，非物理掷币）
            <input type="text" class="cyber-input" bind:value={seed} maxlength="256" placeholder="输入任意种子词，如 muse-ember-01" />
          </label>
        {:else if mode === "trigram_pair"}
          <div>
            <small>上卦（外境 / 高维系统交互）</small>
            <div class="bagua-picker-grid">
              {#each celestial?.bagua_trigrams ?? [] as tri (tri.bits_bottom_to_top)}
                <button
                  type="button"
                  class="trigram-btn"
                  class:selected={upperBits === tri.bits_bottom_to_top}
                  onclick={() => (upperBits = tri.bits_bottom_to_top)}
                >
                  <span>{tri.symbol}</span>
                  <b>{tri.name}</b>
                </button>
              {/each}
            </div>
          </div>
          <div>
            <small>下卦（内基 / 核心执行状态）</small>
            <div class="bagua-picker-grid">
              {#each celestial?.bagua_trigrams ?? [] as tri (tri.bits_bottom_to_top)}
                <button
                  type="button"
                  class="trigram-btn"
                  class:selected={lowerBits === tri.bits_bottom_to_top}
                  onclick={() => (lowerBits = tri.bits_bottom_to_top)}
                >
                  <span>{tri.symbol}</span>
                  <b>{tri.name}</b>
                </button>
              {/each}
            </div>
          </div>
        {:else}
          <div class="bagua-picker-grid">
            {#each manualLines as lineVal, idx}
              <label>
                第 {idx + 1} 爻
                <select
                  class="cyber-select"
                  value={String(lineVal)}
                  onchange={(e) => updateManualLine(idx, (e.currentTarget as HTMLSelectElement).value)}
                >
                  <option value="9">9 · 老阳（动）</option>
                  <option value="7">7 · 少阳（静）</option>
                  <option value="8">8 · 少阴（静）</option>
                  <option value="6">6 · 老阴（动）</option>
                </select>
              </label>
            {/each}
          </div>
        {/if}

        <div class="cyber-badge-row">
          <button type="button" class="vs-btn primary" onclick={runInquiry} disabled={!connected || busy}>
            {busy ? "正在推演象数结构…" : "运行确定性八卦推演"}
          </button>
          {#if inquiry && onSaveHypothesisInspiration}
            <button type="button" class="vs-btn" onclick={saveAsLocalHypothesis} disabled={!connected}>
              存入本地灵感候选（待研究）
            </button>
          {/if}
        </div>

        {#if errorMsg}<p class="feedback-message" role="alert">{errorMsg}</p>{/if}
        {#if saveMsg}<p class="feedback-message" role="status">{saveMsg}</p>{/if}
      </div>

      {#if inquiry}
        <div class="hexagram-showcase">
          <div class="hexagram-glyph-box">
            <strong>{inquiry.reading.primary.unicode_symbol}</strong>
            <span>第 {inquiry.reading.primary.king_wen_number} 卦 · {inquiry.reading.primary.king_wen_name}</span>
            <small>下{inquiry.reading.primary.lower_trigram.name} · 上{inquiry.reading.primary.upper_trigram.name}</small>
          </div>

          <div>
            <svg class="hexagram-lines-svg" viewBox="0 0 120 94" aria-label="六爻阴阳动变图（由初爻至上爻）">
              {#each displayLinesTopDown as line, idx (line.position)}
                {@const y = 10 + idx * 14}
                {@const color = line.changing ? "#ffb64a" : "#00f0ff"}
                {#if line.yin_yang === "yang"}
                  <line x1="12" y1={y} x2="88" y2={y} stroke={color} stroke-width="4" stroke-linecap="round" />
                {:else}
                  <line x1="12" y1={y} x2="44" y2={y} stroke={color} stroke-width="4" stroke-linecap="round" />
                  <line x1="56" y1={y} x2="88" y2={y} stroke={color} stroke-width="4" stroke-linecap="round" />
                {/if}
                <text x="98" y={y + 3} font-size="9" fill={color}>
                  {line.position}{line.changing ? "动" : ""}
                </text>
              {/each}
            </svg>
            <small>
              方法：{inquiry.reading.calculation_method} · 动爻：
              {inquiry.reading.moving_lines.length ? inquiry.reading.moving_lines.map((n) => `第${n}爻`).join("、") : "静卦无动爻"}
            </small>
          </div>
        </div>

        <div class="hex-relations-grid">
          <div class="hex-rel-card">
            <small>之卦（变卦）</small>
            <strong>{inquiry.reading.relating.unicode_symbol}</strong>
            <span>{inquiry.reading.relating.king_wen_name}</span>
          </div>
          <div class="hex-rel-card">
            <small>互卦（内部结构）</small>
            <strong>{inquiry.reading.structural_relations.nuclear.unicode_symbol}</strong>
            <span>{inquiry.reading.structural_relations.nuclear.king_wen_name}</span>
          </div>
          <div class="hex-rel-card">
            <small>错卦（对立镜像）</small>
            <strong>{inquiry.reading.structural_relations.opposite.unicode_symbol}</strong>
            <span>{inquiry.reading.structural_relations.opposite.king_wen_name}</span>
          </div>
          <div class="hex-rel-card">
            <small>综卦（视角反转）</small>
            <strong>{inquiry.reading.structural_relations.inverse.unicode_symbol}</strong>
            <span>{inquiry.reading.structural_relations.inverse.king_wen_name}</span>
          </div>
        </div>
      {:else}
        <div class="empty-state compact">点击「运行确定性八卦推演」，查看六十四卦本/之/互/错/综结构与四体系研究提问。</div>
      {/if}
    </div>

    <!-- Right Column: 28 Mansions Matrix + Cross-System Dossier + Engine Status -->
    <div class="cyber-card">
      <div class="cyber-badge-row">
        <strong>二十八星宿 · 四象矩阵</strong>
        <div class="mode-switch">
          {#each Object.entries(celestial?.star_mansion_quadrants ?? {}) as [qKey, qInfo]}
            <button
              type="button"
              class="mode-btn"
              class:active={activeQuadrant === qKey}
              onclick={() => {
                activeQuadrant = qKey as typeof activeQuadrant;
                selectedMansion = (celestial?.star_mansions ?? []).find((m) => m.quadrant === qKey) ?? null;
              }}
            >
              {qInfo.name}
            </button>
          {/each}
        </div>
      </div>

      <div class="mansion-quadrant-grid">
        {#each quadrantMansions as m (m.name)}
          <button
            type="button"
            class="mansion-chip"
            class:active={selectedMansion?.name === m.name}
            onclick={() => (selectedMansion = m)}
          >
            <b>{m.name}</b>
            <small>{m.element}</small>
          </button>
        {/each}
      </div>

      {#if selectedMansion}
        <div class="astrolabe-inspector">
          <div class="inspector-head">
            <strong>☆ {selectedMansion.full_name} · {selectedMansion.theme}</strong>
            <span class="cyber-pill violet">第 {selectedMansion.index} / 28 宿</span>
          </div>
          <p class="inspector-body"><b>设计启发追问：</b>{selectedMansion.inquiry}</p>
        </div>
      {/if}

      {#if inquiry?.cross_system}
        <div class="cross-table" aria-label="四体系跨域研究对照">
          <b>四体系对照矩阵（易经 · 奇门 · 紫微 · 大六壬）— 提问框架（非共识结论）</b>
          {#each inquiry.cross_system.comparison_axes as axis (axis.axis_id)}
            <div class="cross-row">
              <b>{axis.label}</b>
              <div class="cross-cols">
                {#each Object.entries(axis.perspectives) as [fid, desc]}
                  <span><b>{fid}：</b>{desc}</span>
                {/each}
              </div>
            </div>
          {/each}
          {#each inquiry.cross_system.synthesis_candidates as cand (cand.id)}
            <div class="cross-row">
              <b>可证伪研究假设 · {cand.id}</b>
              <small>{cand.hypothesis}</small>
              <small><b>验证问句：</b>{cand.test_question}</small>
              <small><b>证伪门槛：</b>{cand.falsification_rule}</small>
            </div>
          {/each}
        </div>
      {/if}

      <div>
        <small>可选本地术数排盘引擎真实探测（未安装时诚实标记 unavailable，不影响 Muse 核心）</small>
        <div class="engine-probe-grid">
          {#each Object.entries(celestial?.optional_engines ?? {}) as [key, info]}
            <div class="engine-probe-item">
              <b>{info.label}</b>
              <span class={`cyber-pill ${info.status === "ready" || info.status === "configured" ? "mint" : "gold"}`}>
                {info.status}
              </span>
              <small>{info.reason}</small>
            </div>
          {/each}
        </div>
      </div>
    </div>
  </div>
</section>
