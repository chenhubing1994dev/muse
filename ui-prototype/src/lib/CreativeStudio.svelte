<script lang="ts">
  import { onMount } from "svelte";
  import type {
    ApiClient, MediaAssetResult, NarrationSegment, StudioJob,
    StudioStylesResponse,
  } from "./api";

  export let client: ApiClient | null = null;
  export let connected = false;

  let styles: StudioStylesResponse | null = null;
  let loadError = "";
  let selectedStyle: "hk-retro" | "pixel" | "cosmic" | "mix" = "hk-retro";
  let selectedNarration: "yujie_canto" | "cute_canto" | "news_canto" | "none" = "yujie_canto";
  let topic = "";
  let researchText = "";
  let inspiration = "";
  let aspectRatio: "9:16" | "16:9" | "1:1" = "9:16";

  let mediaBusy = false;
  let scriptBusy = false;
  let renderBusy = false;
  let mediaAssets: MediaAssetResult[] = [];
  let mediaStatus = "";
  let scriptSegments: NarrationSegment[] = [];
  let scriptStatus = "";
  let museNote = "";
  let renderMessage = "";
  let jobs: StudioJob[] = [];
  let pollHandle: ReturnType<typeof setInterval> | null = null;
  let stylesLoaded = false;

  $: preset = (styles?.styles ?? []).find((item) => item.id === selectedStyle) ?? null;
  // App establishes the API client asynchronously; load as soon as it becomes ready.
  $: if (client && connected && !stylesLoaded) {
    stylesLoaded = true;
    void loadStyles();
  }

  async function loadStyles(): Promise<void> {
    if (!client || !connected) {
      stylesLoaded = false;
      return;
    }
    try {
      styles = await client.studioStyles();
      if (!styles.styles.some((item) => item.id === selectedStyle) && styles.styles.length) {
        selectedStyle = styles.styles[0].id;
      }
      if (!styles.narration_styles.some((item) => item.id === selectedNarration) && styles.narration_styles.length) {
        selectedNarration = styles.narration_styles[0].id;
      }
      await loadJobs();
      loadError = "";
    } catch (error) {
      stylesLoaded = false;
      loadError = error instanceof Error ? error.message : "风格目录读取失败";
    }
  }

  async function loadJobs(): Promise<void> {
    if (!client) return;
    try {
      jobs = (await client.studioJobs(20)).jobs;
    } catch {
      // keep previous snapshot on transient error
    }
  }

  function requireInput(): boolean {
    if (!client || !connected) {
      renderMessage = "请先连接本地核心。";
      return false;
    }
    if (!topic.trim()) {
      renderMessage = "先写一个创作主题。";
      return false;
    }
    renderMessage = "";
    return true;
  }

  function payload() {
    return {
      topic: topic.trim().slice(0, 200),
      research_text: researchText.trim().slice(0, 8000),
      inspiration: inspiration.trim().slice(0, 2000),
      style: selectedStyle,
      narration_style: selectedNarration,
      query: topic.trim().slice(0, 300),
      aspect_ratio: aspectRatio,
    };
  }

  async function searchMedia(): Promise<void> {
    if (!requireInput() || mediaBusy) return;
    mediaBusy = true;
    mediaStatus = "正在本地检索素材索引…";
    try {
      const result = await client!.studioSearchMedia(payload());
      mediaAssets = result.assets ?? [];
      mediaStatus = result.status === "found"
        ? `找到 ${mediaAssets.length} 项候选素材（仅展示，未自动下载）。`
        : (result.reason ?? "素材检索未就绪；可先用本地素材创作。");
    } catch (error) {
      mediaStatus = error instanceof Error ? error.message : "素材检索失败";
    } finally {
      mediaBusy = false;
    }
  }

  async function generateScript(): Promise<void> {
    if (!requireInput() || scriptBusy) return;
    scriptBusy = true;
    scriptStatus = "正在生成粤语解说…";
    try {
      const [script, philo] = await Promise.all([
        client!.studioGenerateScript(payload()),
        client!.studioPhilosophyInject(payload()).catch(() => null),
      ]);
      scriptSegments = script.segments ?? [];
      scriptStatus = script.status === "generated"
        ? `生成 ${scriptSegments.length} 段解说。`
        : (script.reason ?? "本地解说生成器未就绪。");
      museNote = philo?.note ?? philo?.muse_note ?? "";
    } catch (error) {
      scriptStatus = error instanceof Error ? error.message : "解说生成失败";
    } finally {
      scriptBusy = false;
    }
  }

  async function submitRender(): Promise<void> {
    if (!requireInput() || renderBusy) return;
    renderBusy = true;
    renderMessage = "正在提交本地渲染任务…";
    try {
      const result = await client!.studioRender(payload());
      renderMessage = `任务 ${result.job.id.slice(0, 8)} 已${result.job.status === "queued" ? "排队" : result.job.status}。`;
      jobs = [result.job, ...jobs.filter((item) => item.id !== result.job.id)];
      startPolling();
    } catch (error) {
      renderMessage = error instanceof Error ? error.message : "渲染提交失败";
    } finally {
      renderBusy = false;
    }
  }

  function startPolling(): void {
    if (pollHandle) return;
    pollHandle = setInterval(async () => {
      const pending = jobs.filter((job) => job.status === "queued" || job.status === "running");
      if (!pending.length) {
        if (pollHandle) clearInterval(pollHandle);
        pollHandle = null;
        return;
      }
      for (const job of pending) {
        try {
          const fresh = (await client?.studioJob(job.id))?.job;
          if (fresh) Object.assign(job, fresh);
        } catch {
          // keep last known state
        }
      }
      jobs = jobs;
    }, 2500);
  }

  function jobClass(status: string): string {
    if (status === "succeeded") return "ok";
    if (status === "failed") return "bad";
    if (status === "running") return "live";
    return "";
  }

  onMount(() => {
    return () => {
      if (pollHandle) clearInterval(pollHandle);
    };
  });
</script>

<section class="studio" id="creative-studio" aria-label="Muse 视频创作工作室">
  <div class="studio-head">
    <div>
      <div class="studio-kicker">STYLE · VOICE · FILM / 视频创作工作室</div>
      <h2>把灵感编译成一部有风格的短片</h2>
      <p>选一种视觉语言，交给 Muse 写粤语解说、找素材、配风格动画。渲染在本机完成，不调用付费服务。</p>
    </div>
    <button class="studio-reload" type="button" onclick={loadStyles} disabled={!connected}>刷新风格目录</button>
  </div>

  {#if loadError}<p class="studio-error" role="alert">{loadError}</p>{/if}

  <div class="style-grid" role="radiogroup" aria-label="视觉风格">
    {#each styles?.styles ?? [] as item (item.id)}
      <button
        type="button"
        class="style-card"
        class:selected={selectedStyle === item.id}
        role="radio"
        aria-checked={selectedStyle === item.id}
        onclick={() => (selectedStyle = item.id)}
      >
        <span class="style-thumb thumb-{item.id}" aria-hidden="true">
          <span class="thumb-sky"></span>
          <span class="thumb-glyph">{item.id === "hk-retro" ? "霓" : item.id === "pixel" ? "▦" : item.id === "cosmic" ? "✧" : "◈"}</span>
        </span>
        <span class="style-meta">
          <strong>{item.label}</strong>
          <small>{item.sub_label}</small>
        </span>
        <span class="style-palette palette-{item.id}" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
      </button>
    {:else}
      <div class="style-empty">连接本地核心后加载风格目录。</div>
    {/each}
  </div>

  {#if preset}
    <p class="style-desc">{preset.description}</p>
  {/if}

  <div class="studio-body">
    <div class="studio-form">
      <label for="studio-topic">创作主题</label>
      <input id="studio-topic" type="text" bind:value={topic} maxlength="200" placeholder="例如：三条法则看懂港风霓虹的美学" />

      <label for="studio-research">研究资料（可选）</label>
      <textarea id="studio-research" rows="3" maxlength="8000" bind:value={researchText}
        placeholder="粘贴已核实的资料要点，Muse 会据此组织解说，不联网、不虚构来源。"></textarea>

      <label for="studio-inspiration">灵感底色（可选）</label>
      <input id="studio-inspiration" type="text" bind:value={inspiration} maxlength="2000" placeholder="想融入的情绪、象征或哲学问题" />

      <div class="form-row">
        <label for="studio-narration">解说风格</label>
        <select id="studio-narration" bind:value={selectedNarration}>
          {#each styles?.narration_styles ?? [] as option (option.id)}
            <option value={option.id}>{option.label}</option>
          {:else}
            <option value="yujie_canto">粤语御姐</option>
          {/each}
        </select>
      </div>
      <div class="form-row">
        <label for="studio-ratio">画幅</label>
        <select id="studio-ratio" bind:value={aspectRatio}>
          <option value="9:16">竖屏 9:16</option>
          <option value="16:9">横屏 16:9</option>
          <option value="1:1">方形 1:1</option>
        </select>
      </div>

      <div class="studio-actions">
        <button type="button" onclick={searchMedia} disabled={mediaBusy || !connected}>{mediaBusy ? "检索中…" : "搜索素材"}</button>
        <button type="button" onclick={generateScript} disabled={scriptBusy || !connected}>{scriptBusy ? "生成中…" : "生成解说"}</button>
        <button class="primary" type="button" onclick={submitRender} disabled={renderBusy || !connected}>{renderBusy ? "提交中…" : "一键成片"}</button>
      </div>
      {#if renderMessage}<p class="studio-message" role="status">{renderMessage}</p>{/if}
      {#if museNote}<p class="muse-note">Muse 手记：{museNote}</p>{/if}
    </div>

    <div class="studio-preview">
      <div class="preview-frame frame-{selectedStyle}">
        <div class="preview-scanline" aria-hidden="true"></div>
        <div class="preview-content">
          <span class="preview-kicker">{preset?.sub_label ?? "MUSE"}</span>
          <strong>{topic || "你的片名会出现在这里"}</strong>
          <span class="preview-sub">{selectedNarration === "none" ? "纯 BGM" : (styles?.narration_styles.find((item) => item.id === selectedNarration)?.label ?? "粤语御姐")}</span>
        </div>
      </div>
      <p class="preview-caption">实时风格预览 · 画幅 {aspectRatio}</p>
    </div>
  </div>

  {#if mediaAssets.length || mediaStatus}
    <div class="studio-panel">
      <h3>素材候选</h3>
      {#if mediaStatus}<p class="panel-status">{mediaStatus}</p>{/if}
      {#if mediaAssets.length}
        <ul class="asset-list">
          {#each mediaAssets.slice(0, 12) as asset, i (asset.url + i)}
            <li><span class="asset-title">{asset.title || "未命名素材"}</span><span class="asset-license">{asset.license}</span></li>
          {/each}
        </ul>
      {/if}
    </div>
  {/if}

  {#if scriptSegments.length || scriptStatus}
    <div class="studio-panel">
      <h3>粤语解说分段</h3>
      {#if scriptStatus}<p class="panel-status">{scriptStatus}</p>{/if}
      {#if scriptSegments.length}
        <ol class="script-list">
          {#each scriptSegments.slice(0, 12) as seg (seg.index)}
            <li><span class="seg-time">{seg.estimated_duration_seconds.toFixed(1)}s</span><span>{seg.text}</span></li>
          {/each}
        </ol>
      {/if}
    </div>
  {/if}

  {#if jobs.length}
    <div class="studio-panel">
      <h3>渲染任务</h3>
      <ul class="job-list">
        {#each jobs.slice(0, 10) as job (job.id)}
          <li class="job-{jobClass(job.status)}">
            <span class="job-title">{job.title}</span>
            <span class="job-state">{job.status} · {job.progress}%</span>
            {#if job.error}<span class="job-error">{job.error}</span>{/if}
          </li>
        {/each}
      </ul>
    </div>
  {/if}
</section>
