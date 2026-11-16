<script lang="ts">
  import { onMount } from "svelte";
  import { filterCommands, isCommandPaletteShortcut } from "../command-palette.js";
  import { USER_GUIDE_STEPS, getGuideProgress, getGuideStep } from "../user-guide.js";
  import { filterQueueTasks } from "../queue-filter.js";
  import "./workflow.css";  import {
    createApiClient,
    resolveDesktopConnection,
    type AgentRecord,
    type AgentStatus,
    type ApiClient,
    type FeedbackRecord,
    type ImprovementProposal,
    type ImprovementDetail,
    type MuseEvent,
    type ProductRecord,
    type InitiativeTimeline,
    type InitiativeTimelineEvent,
    type VideoAttemptSummary,
    type VideoEngineStatus,
    type GpuResourceStatus,
    type QueueSnapshot,
    type WorkerRecord,
    type WorkflowRunRecord,
    type SelfImprovementStatus,
  } from "./lib/api";
  const POLL_INTERVAL_MS = 5000;
  const statusLabels: Record<AgentStatus, string> = {
    online: "在线",
    busy: "执行中",
    offline: "离线",
    disabled: "已停用",
  };

  interface Snapshot {
    agents: AgentRecord[];
    registryConfigured: boolean;
    events: MuseEvent[];
    queue: QueueSnapshot | null;
    workers: WorkerRecord[];
    workflowRuns: WorkflowRunRecord[];
    updatedAt: number;
  }
  let client: ApiClient | null = null;
  let lastSnapshot: Snapshot | null = null;
  let pollHandle: ReturnType<typeof setInterval> | null = null;
  let eventStreamController: AbortController | null = null;
  let inFlight = false;
  let refreshBusy = false;
  let connected = false;
  let stale = false;
  let message = "";
  let registryConfigured = false;
  let agents: AgentRecord[] = [];
  let events: MuseEvent[] = [];
  let updatedAt: number | null = null;
  let queueSnapshot: QueueSnapshot | null = null;
  let queueQuery = "";
  let queueStatusFilter = "all";
  let workers: WorkerRecord[] = [];
  let workflowRuns: WorkflowRunRecord[] = [];
  let feedbackRows: FeedbackRecord[] = [];
  let proposals: ImprovementProposal[] = [];
  let selfImprovementStatus: SelfImprovementStatus | null = null;
  let selectedProposal: ImprovementDetail | null = null;
  let proposalBusy = false;
  let feedbackCategory = "usability";
  let feedbackTag = "workflow_gap";
  let feedbackSeverity = "3";
  let feedbackBusy = false;
  let feedbackMessage = "";
  let radarBusy = false;
  let radarMessage = "";
  let inspirationContent = "";
  let inspirationTags = "";
  let inspirationDomains = "";
  let allowExternalResearch = false;
  let inspirationBusy = false;
  let inspirationMessage = "";
  let inspirations: import("./lib/api").InspirationRecord[] = [];
  let artifacts: import("./lib/api").ArtifactRecord[] = [];
  let products: ProductRecord[] = [];
  let selectedTimelineId: string | null = null;
  let initiativeTimeline: InitiativeTimeline | null = null;
  let timelineBusy = false;
  let timelineMessage = "";
  let productReviewText = "";
  let productReviewDecision: "accept" | "revise" | "reject" = "revise";
  let productReviewBusy = false;
  let productReviewMessage = "";
  let selectedKnowledgeNode: import("./lib/api").KnowledgeNodeRecord | null = null;
  let knowledgeNodeBusy = false;
  let knowledgeNodeMessage = "";
  let videoAttemptSummary: VideoAttemptSummary | null = null;
  let videoEngineStatus: VideoEngineStatus | null = null;
  let gpuResourceStatus: GpuResourceStatus | null = null;
  let pairingCode = "";
  let videoMode: "local_assets" | "prompt_to_video" = "prompt_to_video";  let videoSourceKey = "demo";
  let videoTitle = "";
  let videoPrompt = "";
  let videoNegativePrompt = "";
  let videoDuration = "2";
  let videoMotionProfile: "none" | "brand_intro_outro" | "brand_scene_transitions" = "brand_intro_outro";
  let videoAspectRatio: "16:9" | "9:16" | "1:1" = "16:9";
  let videoAudioName = "";
  let videoAudioMode: "music" | "narration" = "music";
  let videoBusy = false;
  let videoMessage = "";
  let downloadMessage = "";
  let lanUrls: string[] = [];
  let commandPaletteOpen = false;
  let commandQuery = "";
  let commandInput: HTMLInputElement;
  let guideOpen = false;
  let guideIndex = 0;
  $: guideStep = getGuideStep(guideIndex);
  $: guideProgress = getGuideProgress(guideIndex);

  function openGuide(): void {
    guideOpen = true;
    guideIndex = 0;
  }

  function closeGuide(): void {
    guideOpen = false;
  }

  function moveGuide(delta: number): void {
    guideIndex = Math.max(0, Math.min(USER_GUIDE_STEPS.length - 1, guideIndex + delta));
  }

  function visitGuideTarget(): void {
    const target = document.querySelector(guideStep.target);
    closeGuide();
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  const quickCommands = [    { id: "overview", label: "意识中枢", description: "查看系统总览与连接状态", keywords: ["总览", "状态", "主页"], target: "#overview" },
    { id: "inspiration", label: "灵感工坊", description: "提交想法，启动本地记录或研究", keywords: ["灵感", "想法", "研究"], target: "#inspirations" },
    { id: "video", label: "视频创作", description: "Wan 2.1 文生视频或 FFmpeg 素材剪辑", keywords: ["视频", "wan", "mp4", "剪辑"], target: "#products" },    { id: "products", label: "成果产品库", description: "预览、下载 MP4 与工程 ZIP", keywords: ["成果", "产品", "下载", "成片"], target: "#products" },
    { id: "agents", label: "智能体舰队", description: "查看 Agent 能力、心跳与效果", keywords: ["agent", "智能体", "舰队"], target: "#agents" },
    { id: "queue", label: "任务队列", description: "查看排队、运行、审批与失败任务", keywords: ["任务", "队列", "commander"], target: "#queue" },
    { id: "feedback", label: "进化实验室", description: "记录反馈并扫描改进机会", keywords: ["反馈", "进化", "提案"], target: "#feedback" },
    { id: "events", label: "星轨日志", description: "查看最近系统事件", keywords: ["事件", "日志", "历史"], target: "#events" },
  ];
  $: filteredCommands = filterCommands(quickCommands, commandQuery, 10);

  function openCommandPalette(): void {
    commandPaletteOpen = true;
    commandQuery = "";
    window.setTimeout(() => commandInput?.focus(), 0);
  }

  function closeCommandPalette(): void {
    commandPaletteOpen = false;
    commandQuery = "";
  }

  function handleGlobalKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape" && guideOpen) {
      event.preventDefault();
      closeGuide();
      return;
    }
    if (event.key === "Escape" && commandPaletteOpen) {
      event.preventDefault();
      closeCommandPalette();
      return;
    }
    if (isCommandPaletteShortcut({
      key: event.key,
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
      targetTag: (event.target as HTMLElement | null)?.tagName,
    })) {
      event.preventDefault();
      if (commandPaletteOpen) closeCommandPalette();
      else openCommandPalette();
    }
  }


  $: activeCount = agents.filter((agent) =>
    agent.status === "online" || agent.status === "busy"
  ).length;
  $: filteredQueueTasks = filterQueueTasks(queueSnapshot?.active ?? [], queueQuery, queueStatusFilter);
  $: successRate = aggregateRate(agents, "succeeded", "completed");
  $: reviewRate = aggregateRate(agents, "review_approved", "reviewed");
  $: connectionClass = connected && !stale ? "is-online" : stale ? "is-stale" : "is-offline";
  $: connectionLabel = connected ? "本地核心 · 在线" : stale ? "连接中断 · 数据已过期" : "本地核心 · 未连接";
  $: connectionBadge = connected ? "LIVE" : stale ? "STALE" : "OFFLINE";
  $: updatedLabel = updatedAt ? `更新于 ${new Date(updatedAt).toLocaleTimeString()}` : "尚无实时数据";
  $: agentCountLabel = registryConfigured ? `${agents.length} 个已登记` : "Registry 未配置";
  $: videoEngineSummary = videoEngineStatus
    ? `FFmpeg ${videoEngineStatus.ffmpeg_available ? "就绪" : "缺失"} · ${videoEngineStatus.hardware_encoders.includes("h264_amf") ? "AMD AMF 可用" : "硬件编码器未检测到"} · ComfyUI ${videoEngineStatus.comfyui.status === "ready" ? "已连接" : "未连接"} · Velorn ${videoEngineStatus.velorn.status === "ready" ? "已连接" : "未连接"}`
    : "正在检测本地视频引擎…";

  function percent(value: number | null | undefined): string {
    return typeof value === "number" && Number.isFinite(value)
      ? `${(value * 100).toFixed(1)}%`
      : "—";
  }

  function aggregateRate(
    rows: AgentRecord[],
    numerator: "succeeded" | "review_approved",
    denominator: "completed" | "reviewed",
  ): number | null {
    let top = 0;
    let bottom = 0;
    for (const agent of rows) {
      const stats = agent.effectiveness ?? {};
      top += Number(stats[numerator] ?? 0);
      bottom += Number(stats[denominator] ?? 0);
    }
    return bottom > 0 ? top / bottom : null;
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

  function eventTime(event: MuseEvent): string {
    const value = event.created_at ?? event.timestamp ?? event.time;
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString();
  }

  function eventTitle(event: MuseEvent): string {
    return event.summary || event.message || event.event_type || event.kind || "系统事件";
  }

  function eventSource(event: MuseEvent): string {
    return event.source || event.entity_id || event.task_id || "";
  }

  function applySnapshot(snapshot: Snapshot): void {
    agents = snapshot.agents;
    registryConfigured = snapshot.registryConfigured;
    events = snapshot.events;
    updatedAt = snapshot.updatedAt;
    queueSnapshot = snapshot.queue;
    workers = snapshot.workers;
    workflowRuns = snapshot.workflowRuns;
  }

  function setConnection(isConnected: boolean, isStale = false, detail = ""): void {
    connected = isConnected;
    stale = isStale;
    message = detail;
  }

  async function refresh(): Promise<void> {
    if (inFlight) return;
    inFlight = true;
    refreshBusy = true;
    try {
      let lastError: unknown = null;
      for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
          if (!client) {
            client = createApiClient(await resolveDesktopConnection());
          }
          const [health, agentResponse, overview, feedbackResponse, improvementResponse, selfImprovementResponse, inspirationResponse, artifactResponse, productResponse, engineResponse, videoAttemptResponse, gpuResourceResponse] = await Promise.all([
            client.health(),
            client.agents(),
            client.overview(),
            client.feedback(20),
            client.improvements(),
            client.selfImprovement(),
            client.inspirations(50),
            client.artifacts(50),
            client.products(50),
            client.videoEngines(),
            client.videoAttempts().catch(() => null),
            client.gpuResources().catch(() => null),
          ]);
          lastSnapshot = {
            agents: Array.isArray(agentResponse.agents) ? agentResponse.agents : [],
            registryConfigured: Boolean(agentResponse.registry_configured),
            events: Array.isArray(overview.recent_events) ? overview.recent_events : [],
            updatedAt: Date.now(),
            queue: overview.queue ?? null,
            workers: Array.isArray(overview.workers) ? overview.workers : [],
            workflowRuns: Array.isArray(overview.workflow_runs) ? overview.workflow_runs : [],
          };
          applySnapshot(lastSnapshot);
          feedbackRows = Array.isArray(feedbackResponse.feedback) ? feedbackResponse.feedback : [];
          proposals = Array.isArray(improvementResponse.proposals) ? improvementResponse.proposals : [];
          selfImprovementStatus = selfImprovementResponse;
          inspirations = Array.isArray(inspirationResponse.inspirations) ? inspirationResponse.inspirations : [];
          artifacts = Array.isArray(artifactResponse.artifacts) ? artifactResponse.artifacts : [];
          products = Array.isArray(productResponse.products) ? productResponse.products : [];
          videoAttemptSummary = videoAttemptResponse;
          videoEngineStatus = engineResponse;
          gpuResourceStatus = gpuResourceResponse;
          setConnection(
            true,
            false,
            health.agent_registry_configured
              ? ""
              : "后端在线，但 Agent Registry 数据库尚未配置。",
          );
          return;
        } catch (error) {
          lastError = error;
          // A restarted Python API gets a new ephemeral port and Bearer token.
          // Drop the stale client so the next attempt asks Rust IPC for fresh credentials.
          client = null;
          if (attempt < 2) {
            await new Promise((resolve) => window.setTimeout(resolve, 500 * (attempt + 1)));
          }
        }
      }
      throw lastError instanceof Error ? lastError : new Error("本地 API 连接失败");
    } catch (error) {
      const detail = error instanceof Error ? error.message : "未知连接错误";
      if (lastSnapshot) {
        applySnapshot(lastSnapshot);
        setConnection(false, true, `无法刷新实时数据：${detail}。当前显示的是上次成功读取的数据。`);
      } else {
        agents = [];
        events = [];
        setConnection(false, false, `连接失败：${detail}`);
      }
    } finally {
      inFlight = false;
      refreshBusy = false;
    }
  }
  async function submitInspiration(): Promise<void> {
    if (!client || inspirationBusy || !inspirationContent.trim()) return;
    inspirationBusy = true;
    inspirationMessage = "";
    try {
      await client.submitInspiration({
        content: inspirationContent.trim(),
        allow_external_research: allowExternalResearch,
        tags: inspirationTags.split(/[，,]/).map((item) => item.trim()).filter(Boolean).slice(0, 12),
        research_domains: inspirationDomains.split(/[，,]/).map((item) => item.trim()).filter(Boolean).slice(0, 12),
        source: "muse_local_dashboard",
      });
      inspirationMessage = allowExternalResearch
        ? "灵感已保存，并进入 Muse 自主研究候选池。联网检索只会在你明确勾选后使用这段内容。"
        : "灵感已保存到本地历史。默认不联网检索，也不会把内容发送给外部服务。";
      inspirationContent = "";
      inspirationTags = "";
      inspirationDomains = "";
      await refresh();
    } catch (error) {
      inspirationMessage = error instanceof Error ? error.message : "灵感保存失败";
    } finally {
      inspirationBusy = false;
    }
  }

  function timelineInitiativeId(product: ProductRecord): string {
    return product.creator_initiative_id || product.initiative_id;
  }

  function initiativeEventLabel(event: InitiativeTimelineEvent): string {
    switch (event.event_type) {
      case "created": return "创意已创建";
      case "status_changed": return "开发阶段变化";
      case "feedback_recorded": return "评审反馈：" + String(event.details.decision ?? "已记录");
      case "product_built": return "作品已生成并通过完整性检查";
      case "product_failed": return "视频任务失败 · 已记录待复现问题";
      case "knowledge_linked": return "研究知识已关联 · " + String(event.details.relationship ?? "related");
      default: return event.event_type;
    }
  }

  function initiativeEventDetail(event: InitiativeTimelineEvent): string {
    if (event.event_type === "product_built") {
      const digest = String(event.details.package_sha256 ?? "");
      return String(event.details.relative_path ?? "") + (digest ? " · SHA-256 " + digest.slice(0, 16) : "");
    }
    if (event.event_type === "status_changed") {
      return String(event.from_status ?? "—") + " → " + String(event.to_status ?? "—");
    }
    if (event.event_type === "product_failed") {
      return String(event.details.failure_code ?? "video failure") + " · " + String(event.details.error_type ?? "unknown error");
    }
    if (event.event_type === "knowledge_linked") {
      const nodeId = String(event.details.knowledge_node_id ?? "");
      return String(event.details.node_type ?? "knowledge") + (nodeId ? " · " + nodeId.slice(0, 8) : "");
    }
    return "";
  }

  async function openKnowledgeNode(nodeId: string): Promise<void> {
    if (!client || !/^[0-9a-f]{32}$/.test(nodeId) || knowledgeNodeBusy) return;
    selectedKnowledgeNode = null;
    knowledgeNodeMessage = "";
    knowledgeNodeBusy = true;
    try {
      const response = await client.knowledgeNode(nodeId);
      selectedKnowledgeNode = response.node;
    } catch (error) {
      knowledgeNodeMessage = error instanceof Error ? error.message : "知识节点读取失败";
    } finally {
      knowledgeNodeBusy = false;
    }
  }

  async function toggleInitiativeTimeline(product: ProductRecord): Promise<void> {
    if (!client || timelineBusy) return;
    const initiativeId = timelineInitiativeId(product);
    if (selectedTimelineId === initiativeId) {
      selectedTimelineId = null;
      initiativeTimeline = null;
      selectedKnowledgeNode = null;
      knowledgeNodeMessage = "";
      timelineMessage = "";
      return;
    }
    selectedTimelineId = initiativeId;
    initiativeTimeline = null;
    selectedKnowledgeNode = null;
    knowledgeNodeMessage = "";
    timelineMessage = "";
    timelineBusy = true;
    try {
      initiativeTimeline = await client.initiativeTimeline(initiativeId, 100);
    } catch (error) {
      timelineMessage = error instanceof Error ? error.message : "创作时间线读取失败";
    } finally {
      timelineBusy = false;
    }
  }

  async function downloadArtifact(name: string): Promise<void> {
    if (!client) return;
    downloadMessage = "";
    try {
      const blob = await client.downloadArtifact(name);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = name;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      downloadMessage = error instanceof Error ? error.message : "研究报告下载失败";
    }
  }

  async function downloadProduct(product: ProductRecord): Promise<void> {
    if (!client) return;
    downloadMessage = "";
    try {
      const blob = await client.downloadProduct(product.initiative_id, product.version_id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "muse-product-" + product.initiative_id.slice(0, 8) + ".zip";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      downloadMessage = error instanceof Error ? error.message : "产品工程 ZIP 下载失败";
    }
  }

  async function previewProduct(product: ProductRecord, startSeconds?: number): Promise<void> {
    if (!client) return;
    const previewWindow = window.open("about:blank", "_blank");
    if (!previewWindow) {
      downloadMessage = "浏览器阻止了预览窗口，请允许本地控制台打开新窗口后重试。";
      return;
    }
    previewWindow.opener = null;
    try {
      const blob = await client.previewProduct(product.initiative_id, product.version_id);
      const url = URL.createObjectURL(blob);
      previewWindow.location.replace(url + (Number.isFinite(startSeconds) && Number(startSeconds) >= 0 ? "#t=" + Number(startSeconds).toFixed(3) : ""));
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (error) {
      previewWindow.close();
      downloadMessage = error instanceof Error ? error.message : "产品预览失败";
    }
  }

  function qualityWarningLabel(code: string): string {
    const labels: Record<string, string> = {
      sustained_black: "持续黑帧 / 暗场",
      frozen_frames: "疑似画面冻结",
      sustained_silence: "持续静音",
    };
    return labels[code] ?? code;
  }
  async function downloadVideo(product: ProductRecord): Promise<void> {
    if (!client || !product.video_path) return;
    downloadMessage = "";
    try {
      const blob = await client.downloadVideo(product.initiative_id, product.version_id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "muse-video-" + product.initiative_id.slice(0, 8) + ".mp4";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      downloadMessage = error instanceof Error ? error.message : "MP4 下载失败";
    }
  }

  async function createVideoProduct(): Promise<void> {
    const aiMode = videoMode === "prompt_to_video";
    if (!client || videoBusy || !videoTitle.trim()) return;
    if (aiMode ? !videoPrompt.trim() : !videoSourceKey.trim()) return;
    videoBusy = true;
    videoMessage = aiMode ? "正在向本地 ComfyUI 提交 Wan 2.1 生成任务…" : "正在提交本地素材剪辑 + Remotion 动效合成任务…";
    try {
      const payload = aiMode
        ? {
            mode: "prompt_to_video" as const,
            title: videoTitle.trim(),
            prompt: videoPrompt.trim(),
            negative_prompt: videoNegativePrompt.trim(),
            aspect_ratio: videoAspectRatio,
            duration_seconds: Number(videoDuration),
            steps: 8,
            motion_profile: videoMotionProfile === "brand_scene_transitions" ? "brand_intro_outro" : videoMotionProfile,
          }
        : {
            mode: "local_assets" as const,
            source: videoSourceKey.trim(),
            title: videoTitle.trim(),
            audio_mode: videoAudioMode,
            motion_profile: videoMotionProfile,
            ...(videoAudioName.trim() ? { audio: videoAudioName.trim() } : {}),
          };
      const created = await client.createVideoProduct(payload);
      for (let attempt = 0; attempt < 1800; attempt += 1) {
        await new Promise<void>((resolve) => window.setTimeout(resolve, 2000));
        const response = await client.videoJob(created.job.id);
        if (response.job.worker_available === false && (response.job.status === "queued" || response.job.status === "running")) {
          videoMessage = "已找回任务账本，但本地渲染进程已不可用（可能在 Muse 重启后中断）。任务记录已保留；请检查视频任务历史并重新提交，不会假装仍在生成。";
          await refresh();
          return;
        }
        if (response.job.status === "succeeded") {
          videoMessage = aiMode ? "AI 镜头已生成，Remotion 动效合成与 MP4 技术校验已完成，成品已保存到产品库。" : "Remotion 动效合成与 MP4 技术校验已完成，成品已保存到产品库。";
          videoTitle = "";
          if (aiMode) videoPrompt = "";
          await refresh();
          return;
        }
        if (response.job.status === "failed" || response.job.status === "cancelled") {
          throw new Error(response.job.error || (response.job.status === "cancelled" ? "视频任务已取消" : "视频制作失败"));
        }
        videoMessage = response.job.status === "queued"
          ? "任务已排队，等待本地视频引擎…"
          : aiMode ? "Wan 2.1 正在本机生成镜头，随后自动进行 Remotion 动效合成与 QA…" : "FFmpeg 正在剪辑，随后自动进行 Remotion 动效合成与 QA…";
      }
      throw new Error("视频任务超过 60 分钟仍未完成；可检查本地运行日志和素材文件。");
    } catch (error) {
      videoMessage = error instanceof Error ? error.message : "视频制作失败";
    } finally {
      videoBusy = false;
    }
  }
  async function submitProductReview(product: ProductRecord): Promise<void> {
    if (!client || productReviewBusy) return;
    const initiativeId = product.creator_initiative_id;
    if (!product.creator_feedback_enabled || !initiativeId || !/^[0-9a-f]{32}$/.test(initiativeId)) {
      productReviewMessage = "此作品尚未关联 Creator Initiative，暂不能提交创作审片反馈。";
      return;
    }
    if (!productReviewText.trim()) {
      productReviewMessage = "请先写下具体的审片意见。";
      return;
    }
    productReviewBusy = true;
    productReviewMessage = "";
    try {
      const result = await client.submitInitiativeFeedback(initiativeId, {
        feedback: productReviewText.trim(),
        decision: productReviewDecision,
      });
      productReviewText = "";
      productReviewMessage = result.feedback.decision === "accept"
        ? "验收意见已写入创作账本；下一轮自主研究会同步至知识库。"
        : "审片意见已写入创作账本；下一轮自主研究会将其转成待核验知识与改进候选，仍需独立研究和人工审核。";
      if (selectedTimelineId === timelineInitiativeId(product)) {
        initiativeTimeline = await client.initiativeTimeline(initiativeId, 100);
      }
      await refresh();
    } catch (error) {
      productReviewMessage = error instanceof Error ? error.message : "审片反馈提交失败";
    } finally {
      productReviewBusy = false;
    }
  }

  async function submitFeedback(): Promise<void> {
    if (!client || feedbackBusy) return;
    feedbackBusy = true;
    feedbackMessage = "";
    try {
      await client.submitFeedback({
        category: feedbackCategory,
        tag: feedbackTag,
        severity: Number(feedbackSeverity),
      });
      feedbackMessage = "反馈已记录。达到触发条件后，能力雷达会生成改进提案。";
      await refresh();
    } catch (error) {
      feedbackMessage = error instanceof Error ? error.message : "反馈提交失败";
    } finally {
      feedbackBusy = false;
    }
  }

  async function runRadarScan(): Promise<void> {
    if (!client || radarBusy) return;
    radarBusy = true;
    radarMessage = "";
    try {
      const result = await client.runRadarScan();
      const trendLabels = { improving: "改善", worsening: "恶化", stable: "持平", insufficient_data: "样本不足" } as const;
      const trendText = trendLabels[result.trend.direction] + (result.trend.delta === null ? "" : `（变化 ${result.trend.delta > 0 ? "+" : ""}${result.trend.delta}）`);
      radarMessage = "扫描完成：发现 " + result.signals_seen.length + " 类信号，新建 " + result.created.length + " 项，已有 " + result.existing.length + " 项；跨窗口趋势：" + trendText + `（${result.trend.lookback_hours} 小时窗口）。所有提案仍需人工审批。`;
      await refresh();
    } catch (error) {
      radarMessage = error instanceof Error ? error.message : "能力扫描失败";
    } finally {
      radarBusy = false;
    }
  }

  async function openProposal(id: string): Promise<void> {
    if (!client || proposalBusy) return;
    proposalBusy = true;
    try {
      const result = await client.improvementDetail(id);
      selectedProposal = result.proposal;
    } catch (error) {
      radarMessage = error instanceof Error ? error.message : "无法读取提案详情";
    } finally {
      proposalBusy = false;
    }
  }

  function stopPolling(): void {
    if (pollHandle !== null) {
      clearInterval(pollHandle);
      pollHandle = null;
    }
  }

  async function runEventStream(signal: AbortSignal): Promise<void> {
    let cursor = "";
    while (!signal.aborted) {
      if (!client) {
        try {
          client = createApiClient(await resolveDesktopConnection());
        } catch {
          await new Promise((resolve) => setTimeout(resolve, 1000));
          continue;
        }
      }
      try {
        cursor = await client.streamEvents({
          lastEventId: cursor,
          signal,
          onEvent: (event) => {
            const item: MuseEvent = { ...event, summary: `${event.category}.${event.event}` };
            events = [item, ...events.filter((existing) => existing.id !== item.id)].slice(0, 20);
            if (lastSnapshot) lastSnapshot = { ...lastSnapshot, events };
            updatedAt = Date.now();
          },
        });
      } catch {
        if (signal.aborted) break;
        // Force the next loop to reacquire the current port/token via Rust IPC.
        client = null;
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    }
  }
  onMount(() => {
    const localConfig = (window as Window & { __MUSE_LOCAL_CONNECTION__?: { pairingCode?: string | null; lanUrls?: string[] } }).__MUSE_LOCAL_CONNECTION__;
    pairingCode = localConfig?.pairingCode ?? "";
    lanUrls = Array.isArray(localConfig?.lanUrls) ? localConfig.lanUrls : [];

    eventStreamController = new AbortController();    window.addEventListener("keydown", handleGlobalKeydown);
    void refresh();
    void runEventStream(eventStreamController.signal);
    pollHandle = window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => {
      window.removeEventListener("keydown", handleGlobalKeydown);
      stopPolling();
      eventStreamController?.abort();
      eventStreamController = null;
    };
  });
</script>
<svelte:head>
  <title>MUSE · 灵枢调度中心</title>
  <meta name="description" content="Muse 本地优先的 Super Agent 调度中枢" />
</svelte:head>

<div class="scanlines" aria-hidden="true"></div>
<header class="topbar">
  <div class="brand">
    <span class="sigil" aria-hidden="true">✧</span>
    <div><strong>MUSE <i>宇宙意识中枢</i></strong><small>LOCAL INTELLIGENCE · COMMAND & CREATION</small></div>
  </div>
  <div class="top-actions">
    <span>{updatedLabel}</span>
    <button class="refresh" type="button" onclick={refresh} disabled={refreshBusy}>
      {refreshBusy ? "正在刷新…" : "刷新状态"}
    </button>
    <button class="command-trigger" type="button" onclick={openGuide}>使用指南</button>
    <button class="command-trigger" type="button" onclick={openCommandPalette} aria-keyshortcuts="Control+K Meta+K">快速跳转 <kbd>Ctrl K</kbd></button>  </div>

</header>

{#if guideOpen}
  <div class="guide-backdrop" role="presentation" onclick={(event) => { if (event.target === event.currentTarget) closeGuide(); }}>
    <dialog open class="guide-dialog" aria-labelledby="guide-title" aria-describedby="guide-purpose">
      <header class="guide-header">
        <div><small>MUSE FIELD MANUAL / 操作手册</small><h2 id="guide-title">一步一步认识 Muse</h2><p>每一页都告诉你这个模块做什么、怎么操作，以及当前真实能力边界。</p></div>
        <button type="button" class="command-close" onclick={closeGuide} aria-label="关闭使用指南">关闭</button>
      </header>
      <div class="guide-progress" aria-label="指南进度"><span style={`width:${guideProgress.percent}%`}></span></div>
      <div class="guide-step-count">第 {guideProgress.current} / {guideProgress.total} 步 <span>{guideStep.availability}</span></div>
      <div class="guide-visual" aria-hidden="true">
        <div class="guide-orbit"><span>✧</span></div>
        <div class="guide-flow"><b>输入</b><i></i><b>处理</b><i></i><b>成果</b></div>
      </div>
      <article class="guide-copy">
        <h3>{guideStep.title}</h3>
        <p id="guide-purpose">{guideStep.purpose}</p>
        <div><b>你要怎样使用</b><p>{guideStep.howToUse}</p></div>
        <div><b>你会得到什么</b><p>{guideStep.result}</p></div>
        <aside><b>当前能力边界</b><p>{guideStep.boundary}</p></aside>
      </article>
      <footer class="guide-actions">
        <button type="button" class="radar-scan" onclick={() => moveGuide(-1)} disabled={guideIndex === 0}>上一步</button>
        <button type="button" class="feedback-submit" onclick={visitGuideTarget}>{guideStep.action}</button>
        {#if guideIndex < USER_GUIDE_STEPS.length - 1}
          <button type="button" class="feedback-submit" onclick={() => moveGuide(1)}>下一步</button>
        {:else}
          <button type="button" class="feedback-submit" onclick={closeGuide}>完成指南</button>
        {/if}
      </footer>
    </dialog>
  </div>
{/if}

{#if commandPaletteOpen}
  <div class="command-backdrop" role="presentation" onclick={(event) => { if (event.target === event.currentTarget) closeCommandPalette(); }}>
    <div class="command-dialog" role="dialog" aria-modal="true" aria-labelledby="command-title" tabindex="-1">
      <div class="command-title-row">        <div><small>MUSE COMMAND / 快速操作</small><h2 id="command-title">你现在要去哪里？</h2></div>
        <button type="button" class="command-close" onclick={closeCommandPalette} aria-label="关闭快速跳转">Esc</button>
      </div>
      <input bind:this={commandInput} bind:value={commandQuery} class="command-search" type="search" placeholder="搜索灵感、视频、Agent、任务、成果…" aria-label="搜索功能" />
      <div class="command-results" role="listbox" aria-label="可跳转功能">
        {#each filteredCommands as command (command.id)}
          <a class="command-result" href={command.target} role="option" aria-selected="false" onclick={closeCommandPalette}>
            <span class="command-result-glyph">◇</span>
            <span><b>{command.label}</b><small>{command.description}</small></span>
            <kbd>↵</kbd>
          </a>
        {:else}
          <p class="command-empty">没有找到匹配功能。试试「灵感」「视频」或「任务」。</p>
        {/each}
      </div>
      <footer><span><kbd>Ctrl K</kbd> 或 <kbd>/</kbd> 打开</span><span><kbd>Esc</kbd> 关闭</span></footer>
    </div>
  </div>{/if}

<main class="layout">
  <aside class="rail" aria-label="主导航">
    <div class="rail-mark">天</div>
    <a class="active" href="#overview">◈<span>总览</span></a>
    <a href="#agents">◇<span>智能体</span></a>
    <a href="#inspirations">✧<span>灵感工坊</span></a>
    <a href="#products">◇<span>产品库</span></a>
    <a href="#feedback">✧<span>反馈</span></a>
    <a href="#events">≋<span>事件</span></a>
    <div class="rail-bottom">v0.1<br />LOCAL</div>
  </aside>

  <section class="content" id="overview">
    <div class="eyebrow">THE CELESTIAL ENGINE / 玄枢主控台</div>
    <div class="heading">
      <div>
        <h1>灵枢调度中心</h1>
        <p>让每一个智能体，都在你的秩序之中运行。</p>
      </div>
      <div class="clock">
        <small>运行模式</small>
        <strong>LOCAL FIRST</strong>
        <small>真实数据 · 本地创作与调度</small>
      </div>
    </div>
    <section class="cosmos-command" aria-label="Muse 意识中枢">
      <div class="cosmos-copy">
        <div class="cosmos-kicker"><span class="orbit-dot"></span> MUSE / THE LIVING COSMOS</div>
        <h2>让认知，<em>不断生长。</em></h2>
        <p>一个持续感知、研究、记忆、创造与自我校准的智能文明操作系统。</p>
        <div class="cosmos-actions">
          <a href="#inspirations" class="primary-link">注入一个灵感</a>
          <a href="#agents" class="quiet-link">查看智能体舰队</a>
        </div>
        <div class="cosmos-footnote">意识体是产品愿景；下方只呈现可观测的真实系统状态。</div>
      </div>
      <div class="cosmos-orbit" aria-hidden="true">
        <div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><div class="orbit orbit-three"></div>
        <div class="orbit-core"><span>玄</span><small>MUSE CORE</small></div>
        <i class="satellite satellite-a"></i><i class="satellite satellite-b"></i><i class="satellite satellite-c"></i>
        <span class="orbit-label label-a">感知</span><span class="orbit-label label-b">记忆</span><span class="orbit-label label-c">进化</span>
      </div>
      <div class="cosmos-status">
        <div><small>核心连接</small><b class:good={connected} class:warn={!connected && stale}>{connectionLabel}</b></div>
        <div><small>智能体活动</small><b>{activeCount} / {agents.length}</b></div>
        <div><small>研究与创作记录</small><b>{inspirations.length + products.length} 项</b></div>
        <div><small>GPU 独占资源</small><b class:good={gpuResourceStatus?.status === "idle"} class:warn={gpuResourceStatus?.status !== "idle"}>{gpuResourceStatus?.status === "reserved" ? `占用中 · ${gpuResourceStatus.lease?.owner_kind === "video_generation" ? "视频生成" : "模型推理"}` : gpuResourceStatus?.status === "idle" ? "空闲" : "状态未知"}</b></div>
        <div><small>模型服务协调</small><b>{gpuResourceStatus?.model_supervisor.action === "external_model_active" ? "外部模型服务占用 GPU" : gpuResourceStatus?.model_supervisor.action === "gpu_memory_insufficient" ? "可用显存不足 · 等待释放" : gpuResourceStatus?.model_supervisor.restore_pending ? "等待资源释放后恢复" : gpuResourceStatus?.model_supervisor.status === "online" ? "Supervisor 在线" : "Supervisor 未运行 / 未观测"}</b></div>
        <div><small>ComfyUI 显存释放</small><b>{gpuResourceStatus?.comfyui_memory_release.status === "released" ? `已确认 · ${Math.round((gpuResourceStatus.comfyui_memory_release.vram_free_ratio ?? 0) * 100)}% 空闲` : gpuResourceStatus?.comfyui_memory_release.status === "deferred_queue_busy" ? "队列繁忙 · 延后释放" : gpuResourceStatus?.comfyui_memory_release.status === "unconfirmed" ? "等待显存回收确认" : gpuResourceStatus?.comfyui_memory_release.status === "unavailable" ? "释放服务不可用" : "尚无释放记录"}</b></div>
      </div>
    </section>

    {#if message}
      <div class="connection-message" role="status">{message}</div>
    {/if}

    <section class="panel inspiration-panel" id="inspirations">
      <div class="panel-head">
        <div><small>00 / CREATOR STUDIO</small><h2>灵感工坊 · 创作与成果</h2></div>
        <span class="tag">本地保存 · 可选联网研究</span>
      </div>
      {#if pairingCode && lanUrls.length}
        <div class="lan-share">
          <div><b>局域网共享已开启</b><small>同一 Wi-Fi / 局域网的设备可打开以下地址；首次访问需要输入此配对码。</small></div>
          {#each lanUrls as lanUrl}<a href={lanUrl} target="_blank" rel="noreferrer">{lanUrl}</a>{/each}
          <div class="pair-code"><span>设备配对码：<strong>{pairingCode}</strong></span><button type="button" class="radar-scan" onclick={() => navigator.clipboard.writeText(pairingCode)}>复制配对码</button></div>
          <small>配对授权有效 12 小时；Muse 重启后会生成新配对码。</small>
        </div>
      {/if}

      <p class="panel-intro">把你的灵感、问题、故事种子或产品构想交给 Muse。先保存到本地；只有勾选联网研究，Muse 才会把相关内容用于公开资料检索。</p>
      <div class="inspiration-form">
        <label for="inspiration-content">写下你的灵感</label>
        <textarea id="inspiration-content" bind:value={inspirationContent} maxlength="2000" rows="4" placeholder="例如：我想把易经的变化观与多智能体协作结合，做成一个能自我复盘、不断创造新工具的系统……"></textarea>
        <label for="inspiration-tags">分类标签（逗号分隔）</label>
        <input id="inspiration-tags" type="text" bind:value={inspirationTags} maxlength="500" placeholder="例如：跨领域、产品创意、自动化" />
        <label for="inspiration-domains">研究领域（逗号分隔）</label>
        <input id="inspiration-domains" type="text" bind:value={inspirationDomains} maxlength="500" placeholder="例如：知识图谱、创意 AI、视频生成" />
        <label class="privacy-option"><input type="checkbox" bind:checked={allowExternalResearch} /> 允许 Muse 使用这段灵感进行联网研究（不勾选则仅本地保存）</label>
        <div class="inspiration-actions">
          <small>{inspirationContent.length}/2000 字</small>
          <button type="button" class="feedback-submit" onclick={submitInspiration} disabled={!connected || inspirationBusy || !inspirationContent.trim()}>{inspirationBusy ? "正在保存…" : "保存灵感并交给 Muse"}</button>
        </div>
      </div>
      {#if inspirationMessage}<p class="feedback-message" role="status">{inspirationMessage}</p>{/if}
      {#if downloadMessage}<p class="feedback-message" role="alert">下载 / 预览未完成：{downloadMessage}</p>{/if}
      <div class="feedback-columns">
        <div>
          <div class="worker-heading"><b>灵感历史</b><span>{inspirations.length} 条</span></div>
          {#if inspirations.length}
            <div class="feedback-list">
              {#each inspirations as item (item.id)}
                <article class="inspiration-row">
                  <b>{item.title}</b>
                  <p>{item.content}</p>
                  {#if item.tags?.length}<small>标签：{item.tags.join(" · ")}</small>{/if}
                  {#if item.research_domains?.length}<small>研究领域：{item.research_domains.join(" · ")}</small>{/if}
                  <small>{relativeTime(item.created_at)} · {item.allow_external_research ? "已允许联网研究" : "仅本地保存"} · {item.status === "not_researched" ? "待研究" : item.status}</small>
                </article>
              {/each}
            </div>
          {:else}
            <div class="empty-state compact">还没有灵感记录。你可以从上方提交第一条。</div>
          {/if}
        </div>
        <div>
          <div class="worker-heading"><b>已交付研究报告</b><span>{artifacts.length} 份</span></div>
          {#if artifacts.length}
            <div class="feedback-list">
              {#each artifacts as artifact (artifact.id)}
                <div class="artifact-row">
                  <span><b>{artifact.name}</b><small>研究报告 · {(artifact.size_bytes / 1024).toFixed(1)} KB · {relativeTime(artifact.modified_at)}</small></span>
                  <button type="button" class="radar-scan" onclick={() => downloadArtifact(artifact.name)}>下载</button>
                </div>
              {/each}
            </div>
          {:else}
            <div class="empty-state compact">目前没有可下载的研究报告。Muse 生成报告后会自动出现在这里。</div>
          {/if}
          <small class="artifact-note">报告原件保存在 Muse 项目目录的 data/research/reports；下载后可用 Markdown 阅读器或编辑器打开。</small>
        </div>
      </div>
      <section class="product-library" id="products">
          <div class="worker-heading"><b>一键视频创作</b><span>本地素材剪辑 + Wan 2.1 AI 文生视频 · 交付审片闭环</span></div>
          <p class="artifact-note">{videoEngineSummary}</p>
          <p class="panel-intro">可以直接输入灵感生成 AI 短片，也可以导入自己的图片/视频素材剪辑。AI 模式使用本机 ComfyUI + Wan 2.1 1.3B，不调用云端视频 API；当前为 1–5 秒预览级生成，成片仍需人工验收。</p>
          <div class="inspiration-form">
            <label for="video-mode">制作方式</label>
            <select id="video-mode" bind:value={videoMode}>
              <option value="prompt_to_video">AI 灵感生成短片（Wan 2.1）</option>
              <option value="local_assets">本地素材剪辑合成（FFmpeg）</option>
            </select>
            <label for="video-title">影片名称</label>
            <input id="video-title" bind:value={videoTitle} maxlength="160" placeholder="给这部影片起个名字" />
            {#if videoMode === "prompt_to_video"}
              <label for="video-prompt">影片灵感 / 画面描述</label>
              <textarea id="video-prompt" bind:value={videoPrompt} rows="4" maxlength="2000" placeholder="例如：一只红狐狸穿过晨雾中的竹林，镜头缓慢推进，电影级自然光…"></textarea>
              <label for="video-negative-prompt">不希望出现的内容（可选）</label>
              <textarea id="video-negative-prompt" bind:value={videoNegativePrompt} rows="2" maxlength="1000" placeholder="例如：模糊、闪烁、畸形、字幕、水印"></textarea>
              <label for="video-aspect-ratio">视频画幅</label>
              <select id="video-aspect-ratio" bind:value={videoAspectRatio}>
                <option value="16:9">横屏 16:9</option>
                <option value="9:16">竖屏 9:16</option>
                <option value="1:1">正方形 1:1</option>
              </select>
              <label for="video-duration">预览时长（秒）</label>
              <select id="video-duration" bind:value={videoDuration}>
                <option value="1">约 1 秒</option>
                <option value="2">约 2 秒</option>
                <option value="3">约 3 秒</option>
                <option value="4">约 4 秒</option>
                <option value="5">约 5 秒</option>
              </select>
              <small>当前使用 512 级预览分辨率、12 FPS、8 步采样；较长或高分辨率影片后续再逐级开放。</small>
            {:else}
              <label for="video-source-key">素材文件夹名</label>
              <input id="video-source-key" bind:value={videoSourceKey} maxlength="64" placeholder="例如 demo" />
              <label for="video-audio-name">音乐 / 旁白文件名（可选）</label>
              <input id="video-audio-name" bind:value={videoAudioName} maxlength="180" placeholder="例如 music.mp3" />
              <label for="video-audio-mode">音轨用途</label>
              <select id="video-audio-mode" bind:value={videoAudioMode}>
                <option value="music">背景音乐（循环铺底）</option>
                <option value="narration">旁白 / 人声（按原时长）</option>
              </select>
              <small>支持 PNG/JPG/WebP 与 MP4/MOV/MKV/WebM；最多 100 个镜头素材。素材放在 data/video_sources/素材文件夹名。</small>
            {/if}
            <label for="video-motion-profile">Remotion 动效包装</label>
            <select id="video-motion-profile" bind:value={videoMotionProfile}>
              <option value="brand_intro_outro">动态片头 + 正片 + 动态片尾</option>
              {#if videoMode !== "prompt_to_video"}
                <option value="brand_scene_transitions">逐镜头交叉淡化 / 擦除 + 动态片头片尾（推荐）</option>
              {/if}
              <option value="none">不添加动效</option>
            </select>
            <small>逐镜头模式会先按统一画幅/FPS规范化每段素材，再由 Remotion TransitionSeries 交替渲染淡化与擦除转场；原音轨按新时间轴保留。AI 单镜头模式使用片头片尾包装。</small>
            <div class="inspiration-actions">
              <small>{videoMode === "prompt_to_video" ? "AI 生成需要本机 ComfyUI 和 Wan 模型已启动。" : "先把素材复制到 Muse 项目 data/video_sources 目录。"}</small>
              <button type="button" class="feedback-submit" onclick={createVideoProduct} disabled={!connected || videoBusy || !videoTitle.trim() || (videoMode === "prompt_to_video" ? (!videoPrompt.trim() || videoEngineStatus?.comfyui.status !== "ready") : !videoSourceKey.trim())}>{videoBusy ? "正在制作…" : videoMode === "prompt_to_video" ? "一键 AI 生成 MP4" : "一键制作 MP4"}</button>
            </div>          </div>
          {#if videoMessage}<p class="feedback-message" role="status">{videoMessage}</p>{/if}
          {#if videoAttemptSummary}
            <section class="video-attempt-summary" aria-label="视频创作产能">
              <b>视频创作产能 · 持久化任务账本</b>
              <div class="video-attempt-stats">
                <span><strong>{videoAttemptSummary.metrics.succeeded}</strong> 已验证交付</span>
                <span><strong>{videoAttemptSummary.metrics.failed}</strong> 失败待复盘</span>
                <span><strong>{videoAttemptSummary.metrics.pending}</strong> 排队 / 运行中</span>
                <span><strong>{videoAttemptSummary.metrics.terminal}</strong> 已结束样本</span>
                <span><strong>{videoAttemptSummary.metrics.success_rate === null ? "—" : Math.round(videoAttemptSummary.metrics.success_rate * 100) + "%"}</strong> 终态成功率</span>
              </div>
              <small>成功率 = {videoAttemptSummary.metrics.success_rate_numerator} 次成功 / {videoAttemptSummary.metrics.success_rate_denominator} 次已结束任务；未完成任务不计入分母。</small>
              {#if videoAttemptSummary.attempts.length}
                <div class="video-attempt-list" aria-label="最近视频任务">
                  {#each videoAttemptSummary.attempts.slice(0, 8) as attempt (attempt.attempt_id)}
                    <div class="video-attempt-row">
                      <span>{attempt.status === "succeeded" ? "已交付" : attempt.status === "failed" ? "失败" : attempt.status === "running" ? "运行中" : "排队"} · {attempt.mode === "prompt_to_video" ? "AI 生成" : "素材合成"} · {attempt.attempt_id.slice(0, 8)}</span>
                      <small>{#if attempt.initiative_id}项目 {attempt.initiative_id.slice(0, 8)}{/if}{#if attempt.evolution_cycle_id} · 周期 {attempt.evolution_cycle_id.slice(0, 14)}{/if}{#if attempt.product_id} · 产品 {attempt.product_id.slice(0, 8)}{/if}{#if attempt.failure_code} · {attempt.failure_code}{/if}</small>
                    </div>
                  {/each}
                </div>
              {/if}
            </section>
          {/if}
          {#if products.length}
            <div class="feedback-list">
              {#each products as product (product.id)}
                <div class="artifact-row">
                  <span>
                    <b>{product.kind === "video_creator" ? "🎬 " : "◇ "}{product.name}</b>                  <small>{product.kind === "video_creator" ? "视频成片" : "离线原型"} · {product.version} · {product.status} · {(product.size_bytes / 1024).toFixed(1)} KB · {relativeTime(product.modified_at)}</small>
                  {#if product.kind === "video_creator"}
                    <small class="video-delivery-metrics">MP4 / ZIP 完整性校验通过 · MP4 {(Number(product.video_bytes ?? 0) / 1024 / 1024).toFixed(2)} MB
                      {#if product.video_metrics}
                        · {product.video_metrics.width ?? "?"}×{product.video_metrics.height ?? "?"} · {product.video_metrics.fps ?? "?"} FPS · {Number(product.video_metrics.duration_seconds ?? 0).toFixed(2)} 秒
                      {/if}
                      {#if product.video_metrics?.model} · {product.video_metrics.model}{/if}
                      {#if product.video_mp4_sha256 && product.video_package_sha256}
                        <span class="video-hashes">· MP4 SHA {product.video_mp4_sha256.slice(0, 12)} · ZIP SHA {product.video_package_sha256.slice(0, 12)}</span>
                      {/if}
                    </small>
                  {/if}
                    {#if product.motion?.renderer === "remotion"}
                      <small class="video-delivery-metrics">
                        Remotion {product.motion.remotion_version ?? ""} · {product.motion.transition ?? "motion"} ·
                        {#if product.motion.scene_count}
                          {product.motion.scene_count} 个镜头 / 场景转场 {Number(product.motion.scene_transition_seconds ?? 0).toFixed(2)}s ·
                        {/if}
                        {product.motion.audio_mode === "music" ? "背景音乐" : product.motion.audio_mode === "narration" ? "旁白" : "音轨"} ·
                        片头 {Number(product.motion.intro_seconds ?? 0).toFixed(1)}s / 片尾 {Number(product.motion.outro_seconds ?? 0).toFixed(1)}s
                      </small>
                    {/if}
                    {#if product.quality_gate}
                      <small class="quality-gate-line">
                        技术 QA：{product.quality_gate.status === "passed" ? "通过" : product.quality_gate.status === "failed" ? "未通过" : product.quality_gate.status === "blocked" ? "受阻" : "未知"}
                        {#if product.quality_gate.warning_review_required}
                          · <strong>{product.quality_gate.review_warning_count} 项待人工复核</strong>
                        {:else}
                          · 未发现启发式复核告警
                        {/if}
                      </small>
                    {/if}
                    {#if product.quality_warnings?.length}
                      <div class="quality-warning-list" aria-label="视频质量复核告警">
                        {#each product.quality_warnings as warning}
                          <div class="quality-warning-row">
                            <span><b>{qualityWarningLabel(warning.code)}</b><small>{warning.start_seconds.toFixed(2)}s 起 · 持续 {warning.duration_seconds.toFixed(2)}s</small></span>
                            <button type="button" class="radar-scan" onclick={() => previewProduct(product, warning.start_seconds)}>定位复核</button>
                          </div>
                        {/each}
                        <small>这些是黑帧、静帧或静音的启发式提示；有意的暗场、定格与留白不自动判为缺陷，也不会单独阻断技术 QA。</small>
                      </div>
                    {/if}
                  </span>                <div class="product-actions">
                  <button type="button" class="radar-scan" onclick={() => toggleInitiativeTimeline(product)} disabled={timelineBusy} aria-expanded={selectedTimelineId === timelineInitiativeId(product)}>{selectedTimelineId === timelineInitiativeId(product) ? "收起时间线" : "创作时间线"}</button>
                  <button type="button" class="radar-scan" onclick={() => previewProduct(product)}>预览</button>
                  {#if product.kind === "video_creator" && product.video_path}
                    <button type="button" class="radar-scan" onclick={() => downloadVideo(product)}>下载 MP4</button>
                  {/if}
                  <button type="button" class="radar-scan" onclick={() => downloadProduct(product)}>下载工程 ZIP</button>
                </div>
              </div>
              {#if selectedTimelineId === timelineInitiativeId(product)}
                <div class="initiative-timeline">
                  {#if timelineBusy}
                    <small>正在读取创作记录…</small>
                  {:else if timelineMessage}
                    <small>{timelineMessage}</small>
                  {:else if initiativeTimeline}
                    {#if product.kind === "video_creator" && product.creator_feedback_enabled && product.creator_initiative_id}
                      <section class="product-review-form" aria-label="创作审片反馈">
                        <b>成片审片 · 回流到 Muse 知识与进化系统</b>
                        <label>审片意见
                          <textarea bind:value={productReviewText} rows="3" maxlength="8000" placeholder="例如：第二个镜头切换突兀；主体运动不连贯；旁白与画面不同步…"></textarea>
                        </label>
                        <label>验收决定
                          <select bind:value={productReviewDecision}>
                            <option value="revise">需要修改</option>
                            <option value="reject">拒绝本版</option>
                            <option value="accept">验收通过</option>
                          </select>
                        </label>
                        <div class="product-actions">
                          <button type="button" class="feedback-submit" onclick={() => submitProductReview(product)} disabled={productReviewBusy || !productReviewText.trim()}>
                            {productReviewBusy ? "正在写入创作账本…" : "提交审片意见"}
                          </button>
                        </div>
                        {#if productReviewMessage}<small role="status">{productReviewMessage}</small>{/if}
                        <small>反馈先进入 Creator Initiative；下一轮自主研究会幂等同步到 Research Wiki 和 Capability Radar。用户意见是待核验信号，不会自动批准代码修改或发布成品。</small>
                      </section>
                    {:else if product.kind === "video_creator"}
                      <small>该作品尚未绑定 Creator Initiative，因此暂不显示审片反馈入口。</small>
                    {/if}
                    <b>{initiativeTimeline.initiative.title} · 创作履历</b>
                    {#if !initiativeTimeline.history_complete}
                      <small>该项目在启用时间线之前已有历史；旧记录不会伪造补齐。</small>
                    {/if}
                    {#each initiativeTimeline.events as event (event.id)}
                      <article class="timeline-event">
                        <b>{initiativeEventLabel(event)}</b>
                        <small>{initiativeEventDetail(event)} · {relativeTime(event.created_at)}</small>
                        {#if event.event_type === "knowledge_linked"}
                          <button type="button" class="radar-scan" onclick={() => openKnowledgeNode(String(event.details.knowledge_node_id ?? ""))} disabled={knowledgeNodeBusy}>查看知识节点</button>
                        {/if}
                      </article>
                    {/each}
                    {#if knowledgeNodeBusy}
                      <small>正在读取知识节点…</small>
                    {:else if knowledgeNodeMessage}
                      <small>{knowledgeNodeMessage}</small>
                    {:else if selectedKnowledgeNode}
                      <article class="timeline-event knowledge-node-detail">
                        <b>{selectedKnowledgeNode.title}</b>
                        <small>{selectedKnowledgeNode.node_type} · {selectedKnowledgeNode.status} · {String(selectedKnowledgeNode.metadata.epistemic_status ?? "")}</small>
                        <p>{selectedKnowledgeNode.summary}</p>
                      </article>
                    {/if}
                    {#if !initiativeTimeline.events.length}
                      <small>尚无可追溯事件。</small>
                    {/if}
                  {/if}
                </div>
              {/if}
            {/each}
          </div>
        {:else}
          <div class="empty-state compact">尚无自主创作产品。自主研究生成产品原型或完成视频制作后，会出现在这里。</div>
        {/if}
        <small class="artifact-note">产品原件保存在 data/products；视频素材放在 data/video_sources。视频工程 ZIP 包含 MP4、素材顺序清单和说明。所有产品初始为 testing，须人工验收后才能发布。</small>
      </section>
    </section>

    <section class="metrics" aria-label="实时指标">
      <article>
        <small>已登记智能体</small>
        <strong>{agents.length}</strong>
        <em>来自本地 Agent Registry</em>
      </article>
      <article>
        <small>在线 / 忙碌</small>
        <strong>{activeCount}</strong>
        <em>按最近心跳实时推导</em>
      </article>
      <article>
        <small>任务成功率</small>
        <strong>{percent(successRate)}</strong>
        <em>仅统计真实完成样本</em>
      </article>
      <article>
        <small>审查通过率</small>
        <strong>{percent(reviewRate)}</strong>
        <em>仅统计真实 Reviewer 样本</em>
      </article>
    </section>

    <div class="main-grid">
      <section class="panel agents-panel" id="agents">
        <div class="panel-head">
          <div><small>01 / SWARM TOPOLOGY</small><h2>天枢 · 智能体阵列</h2></div>
          <span class="tag">{agentCountLabel}</span>
        </div>

        {#if !registryConfigured}
          <div class="empty-state">Agent Registry 尚未接入。请先启动 Muse 后端并配置 --agent-db。</div>
        {:else if agents.length === 0}
          <div class="empty-state">Registry 已连接，但还没有登记任何 Agent。连接真实 Agent 后，它们会出现在这里。</div>
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

      <section class="panel queue-panel" id="queue">
        <div class="panel-head">
          <div><small>02 / TASK FLOW & WORKERS</small><h2>任务队列 · 执行节点</h2></div>
          <span class="tag">只读监控</span>
        </div>
        {#if !queueSnapshot}
          <div class="empty-state">任务队列数据暂不可用。</div>
        {:else}
          <div class="queue-counts">
            <div><small>排队中</small><strong>{queueSnapshot.counts.queued}</strong></div>
            <div><small>执行中</small><strong>{queueSnapshot.counts.running}</strong></div>
            <div><small>待审批</small><strong>{queueSnapshot.counts.waiting_approval}</strong></div>
            <div><small>失败</small><strong>{queueSnapshot.counts.failed}</strong></div>
          </div>
          <div class="queue-tools" aria-label="任务筛选">
            <label class="queue-search-label" for="queue-search">查找任务</label>
            <input id="queue-search" class="queue-search" type="search" bind:value={queueQuery} placeholder="按任务名称或编号搜索…" />
            <label class="queue-search-label" for="queue-status">状态</label>
            <select id="queue-status" class="queue-status-filter" bind:value={queueStatusFilter}>
              <option value="all">全部状态</option>
              <option value="queued">排队中</option>
              <option value="running">执行中</option>
              <option value="waiting_approval">待审批</option>
              <option value="failed">失败</option>
            </select>
            <span class="queue-result-count">显示 {filteredQueueTasks.length} / {queueSnapshot.active.length} 条已载入任务</span>
          </div>
          {#if filteredQueueTasks.length}
            <div class="queue-list" aria-live="polite">
              {#each filteredQueueTasks as task (task.id)}
                <div class="queue-task">
                  <span class="task-status task-{task.status}">{task.status}</span>
                  <b>{task.title}</b>
                  <small>{task.depends_on?.length ? `依赖 ${task.depends_on.length} 项 · ` : ""}尝试 {task.attempts} 次 · 更新于 {relativeTime(task.updated_at)}</small>
                </div>
              {/each}
            </div>
          {:else if queueSnapshot.active.length}
            <div class="empty-state compact">没有符合条件的任务。<button type="button" class="radar-scan" onclick={() => { queueQuery = ""; queueStatusFilter = "all"; }}>清除筛选</button></div>
          {:else}
            <div class="empty-state compact">当前没有排队、执行或等待审批的任务。</div>
          {/if}
          {#if queueSnapshot.active_truncated}
            <small class="queue-note">后端返回的活跃任务列表已截断；搜索与筛选仅作用于当前已载入的 {queueSnapshot.active.length} 条任务。</small>
          {/if}
        {/if}
        <div class="workflow-heading"><b>Commander 工作流</b><span>{workflowRuns.length} 个活跃流程</span></div>
        {#if workflowRuns.length}
          <div class="workflow-list">
            {#each workflowRuns as workflow (workflow.ticket_id)}
              <article class="workflow-card">
                <div class="workflow-card-head">
                  <b>{workflow.title}</b>
                  <span class="workflow-state workflow-{workflow.status}">{workflow.status}</span>
                </div>
                <div class="workflow-stage">
                  <strong>{workflow.stage}</strong>
                  <small>{workflow.profile_name ?? "team"} · v{workflow.profile_version} · 尝试 {workflow.attempt}</small>
                </div>
                {#if workflow.step_id}
                  <small>当前 DAG 步骤：{workflow.step_id}</small>
                {/if}
                {#if workflow.next_action}
                  <small>下一步：{workflow.next_action}</small>
                {/if}
                <small class="workflow-id">Ticket {workflow.ticket_id.slice(0, 8)} · Flow {workflow.workflow_id.slice(0, 8)}</small>
              </article>
            {/each}
          </div>
        {:else}
          <div class="empty-state compact">当前没有已绑定的 Commander 工作流。</div>
        {/if}
        <div class="worker-heading"><b>Worker 运行状态</b><span>{workers.length} 个记录</span></div>
        {#if workers.length}
          <div class="worker-list">
            {#each workers.slice(0, 6) as worker (worker.worker_id)}
              <div class="worker-row">
                <span class:worker-stale={worker.stale} class="worker-indicator"></span>
                <b>{worker.worker_id}</b>
                <small>{worker.stale ? "心跳过期" : worker.state === "active" ? "运行中" : "已停止"}</small>
              </div>
            {/each}
          </div>
        {:else}
          <div class="empty-state compact">尚无 Worker 心跳记录。</div>
        {/if}
      </section>

      <section class="panel feedback-panel" id="feedback">
        <div class="panel-head">
          <div><small>03 / LEARNING LOOP</small><h2>反馈 · 自我改进</h2></div>
          <span class="tag">人工输入 · 审批保护</span>
        </div>
        <p class="panel-intro">只记录预设分类标签和严重程度，不收集聊天原文或自由文本。</p>
        {#if selfImprovementStatus}
          <div class="queue-counts" aria-label="自我进化状态">
            <div><small>待研究</small><strong>{selfImprovementStatus.pending_research}</strong></div>
            <div><small>待人工审查</small><strong>{selfImprovementStatus.pending_human_review}</strong></div>
            <div><small>已批准待执行</small><strong>{selfImprovementStatus.approved_waiting_execution}</strong></div>
            <div><small>拒绝 / 回滚</small><strong>{selfImprovementStatus.proposal_counts.rejected + selfImprovementStatus.proposal_counts.rolled_back}</strong></div>
          </div>
          <div class="worker-heading"><b>Evolution Cycle</b><span>{selfImprovementStatus.cycle_state === "progress" ? "运行中 / 可恢复" : selfImprovementStatus.cycle_state === "started" ? "已启动 / 可恢复" : selfImprovementStatus.cycle_state === "completed" ? "最近周期已完成" : selfImprovementStatus.cycle_state === "failed" ? "最近周期失败" : selfImprovementStatus.cycle_state === "cancelled" ? "最近周期已取消" : "尚未运行"}</span></div>
          <small>下一步：{selfImprovementStatus.next_action} · {selfImprovementStatus.topic_checkpoint_saved ? "研究主题检查点已保存" : "等待主题检查点"}</small>
          <small>安全门：{selfImprovementStatus.approval_required ? "需要人工批准" : "未启用人工批准"} · {selfImprovementStatus.automatic_code_changes ? "自动代码修改已启用" : "自动代码修改关闭"}</small>
        {/if}
        <div class="feedback-form">
          <label>反馈类别
            <select bind:value={feedbackCategory}>
              <option value="reliability">可靠性</option>
              <option value="accuracy">准确性</option>
              <option value="usability">易用性</option>
              <option value="capability">能力缺口</option>
              <option value="performance">性能</option>
            </select>
          </label>
          <label>问题标签
            <select bind:value={feedbackTag}>
              <option value="workflow_gap">流程缺口</option>
              <option value="repeated_manual_fix">重复手工修正</option>
              <option value="repeated_retry">重复重试</option>
              <option value="missing_tool">缺少工具</option>
              <option value="incorrect_result">结果不准确</option>
              <option value="slow_response">响应缓慢</option>
              <option value="hard_to_use">操作困难</option>
            </select>
          </label>
          <label>严重程度
            <select bind:value={feedbackSeverity}>
              <option value="1">1 · 轻微</option>
              <option value="2">2</option>
              <option value="3">3 · 一般</option>
              <option value="4">4</option>
              <option value="5">5 · 严重</option>
            </select>
          </label>
          <button type="button" class="feedback-submit" onclick={submitFeedback} disabled={feedbackBusy || !connected}>
            {feedbackBusy ? "正在提交…" : "记录反馈"}
          </button>
        </div>
        {#if feedbackMessage}
          <p class="feedback-message" role="status">{feedbackMessage}</p>
        {/if}
        <div class="feedback-columns">
          <div>
            <div class="worker-heading"><b>最近反馈</b><span>{feedbackRows.length} 条</span></div>
            {#if feedbackRows.length}
              <div class="feedback-list">
                {#each feedbackRows.slice(0, 5) as item, index}
                  <div class="feedback-row" data-feedback-index={index}>
                    <b>{item.tag}</b><span>严重度 {item.severity}/5</span><small>{item.category} · {relativeTime(item.created_at)}</small>
                  </div>
                {/each}
              </div>
            {:else}
              <div class="empty-state compact">还没有用户反馈。</div>
            {/if}
          </div>
          <div>
            <div class="worker-heading"><b>改进提案 · {proposals.length} 项</b><button type="button" class="radar-scan" onclick={runRadarScan} disabled={radarBusy || !connected}>{radarBusy ? "扫描中…" : "扫描改进机会"}</button></div>
            {#if radarMessage}<p class="feedback-message" role="status">{radarMessage}</p>{/if}
            {#if proposals.length}
              <div class="feedback-list">
                {#each proposals.slice(0, 5) as proposal (proposal.id)}
                  <button type="button" class="feedback-row proposal-row" onclick={() => openProposal(proposal.id)} disabled={proposalBusy}>
                    <b>{proposal.title}</b><span>优先级 {proposal.priority}/100</span>
                    <small>{proposal.status} · 更新于 {relativeTime(proposal.updated_at)} · 查看研究详情 ↗</small>
                  </button>
                {/each}
              </div>
              {#if selectedProposal}
                <article class="proposal-detail">
                  <div class="worker-heading"><b>提案详情 · {selectedProposal.status}</b><button type="button" class="radar-scan" onclick={() => selectedProposal = null}>收起</button></div>
                  <h3>{selectedProposal.title}</h3>
                  <p>{selectedProposal.problem}</p>
                  <p><b>研究假设：</b>{selectedProposal.hypothesis}</p>
                  <p><b>预期影响：</b>{selectedProposal.impact} · 置信度 {selectedProposal.confidence === null ? "—" : Math.round(selectedProposal.confidence * 100) + "%"}</p>
                  <b>验收标准</b>
                  <ul>{#each selectedProposal.acceptance_criteria as criterion}<li>{criterion}</li>{/each}</ul>
                  <p><b>回滚方案：</b>{selectedProposal.rollback_plan}</p>
                  <b>研究来源 · {selectedProposal.sources.length}</b>
                  {#if selectedProposal.sources.length}
                    <ul>{#each selectedProposal.sources as source}<li><a href={source.url} target="_blank" rel="noreferrer">{source.title}</a> · {source.license_status} / {source.security_status}</li>{/each}</ul>
                  {:else}<p>尚无已登记研究来源。</p>{/if}
                  <small>审批仍由人工完成；详情页不会自动批准或执行提案。</small>
                </article>
              {/if}
            {:else}
              <div class="empty-state compact">尚无改进提案。反馈达到触发条件后可运行能力雷达生成。</div>
            {/if}
          </div>
        </div>
      </section>

      <section class="panel events-panel" id="events">
        <div class="panel-head">
          <div><small>02 / CHRONICLE STREAM</small><h2>星轨事件</h2></div>
          <span class="tag">真实事件</span>
        </div>
        {#if events.length === 0}
          <div class="empty-state">暂无已记录事件。后端产生事件后会显示在这里。</div>
        {:else}
          <div class="event-list">
            {#each events.slice(0, 20) as event, index}
              <div class="event" data-event-index={index}>
                <time>{eventTime(event)}</time>
                <i class="event-dot"></i>
                <span><b>{eventTitle(event)}</b><small>{eventSource(event)}</small></span>
              </div>
            {/each}
          </div>
        {/if}
      </section>

      <section class="panel integrity-panel">
        <div class="panel-head">
          <div><small>03 / SYSTEM INTEGRITY</small><h2>数据与权限</h2></div>
          <span class="tag">本机受限写入</span>
        </div>
        <p>界面通过受限本地 API 读取状态、提交系统能力反馈和作品审片意见，并可手动运行本地统计扫描；不直接访问 SQLite，也不触发 Agent 执行或自动批准提案。</p>
        <ul>
          <li>本机回环地址</li>
          <li>临时 Bearer 会话凭据</li>
          <li>来源与能力仅作展示，不授予权限</li>
          <li>系统能力反馈仅限 allowlist 标签与 1–5 级严重度；作品审片意见单独写入 Creator Initiative，进入待研究队列</li>
        </ul>
      </section>
    </div>

    <footer>
      ✧ MUSE · PERSONAL SUPER AGENT OS
      <span>所有状态来自本地 API；无数据时明确显示空态或连接错误。</span>
    </footer>
  </section>
</main>
<style>
  .panel-intro{color:var(--muted);font-size:11px;margin:0 0 14px}
  .feedback-form{display:grid;grid-template-columns:repeat(3,minmax(0,1fr)) auto;gap:10px;align-items:end}
  .feedback-form label{display:grid;gap:6px;color:var(--muted);font-size:10px}
  .feedback-form select{min-width:0;width:100%;padding:9px;background:#0b1424;border:1px solid #31415d;color:var(--text);font:inherit}
  .feedback-submit{padding:10px 14px;border:1px solid #5a8a9a;background:#10283a;color:#d8f7ff;cursor:pointer;font:inherit;font-size:10px}
  .feedback-submit:disabled,.radar-scan:disabled{opacity:.45;cursor:not-allowed}
  .radar-scan{border:1px solid #425c75;background:transparent;color:var(--cyan);padding:6px 9px;font:inherit;font-size:9px;cursor:pointer}
  .feedback-message{color:var(--mint);font-size:10px;margin:10px 0}
  .inspiration-form{display:grid;gap:9px;margin:14px 0}
  .inspiration-form>label{font-size:11px;color:var(--muted)}
  .inspiration-form textarea{width:100%;box-sizing:border-box;resize:vertical;min-height:100px;padding:12px;background:#0b1424;border:1px solid #31415d;color:var(--text);font:inherit;line-height:1.6}
  .inspiration-form input[type="text"]{width:100%;box-sizing:border-box;padding:9px 10px;background:#0b1424;border:1px solid #31415d;color:var(--text);font:inherit;font-size:10px}
  .privacy-option{display:flex;align-items:center;gap:8px}
  .privacy-option input{accent-color:#63b6bd}
  .inspiration-actions{display:flex;justify-content:space-between;align-items:center}
  .inspiration-actions small,.artifact-note{color:var(--muted);font-size:9px}
  .inspiration-row,.artifact-row{padding:10px 0;border-bottom:1px solid #243149}
  .inspiration-row b,.artifact-row b{font-size:10px;overflow-wrap:anywhere}
  .inspiration-row p{font-size:10px;line-height:1.6;color:var(--muted);white-space:pre-wrap;overflow-wrap:anywhere;margin:6px 0}
  .inspiration-row small,.artifact-row small{display:block;font-size:9px;color:var(--muted);margin-top:5px}
  .artifact-row{display:flex;justify-content:space-between;align-items:center;gap:10px}
  .artifact-row span{min-width:0}
  .artifact-note{display:block;margin-top:10px;line-height:1.6}
  .lan-share{display:grid;gap:7px;padding:12px;margin:12px 0;background:#0b1b2a;border:1px solid #356073}
  .lan-share b,.lan-share strong{color:#c7f4ee;font-size:12px}
  .lan-share small{display:block;color:var(--muted);font-size:9px;line-height:1.5}
  .lan-share a{color:var(--cyan);font-size:10px;overflow-wrap:anywhere}
  .pair-code{display:flex;justify-content:space-between;align-items:center;gap:10px}


  .feedback-columns{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:16px}
  .feedback-list{display:grid;gap:7px}
  .feedback-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 8px;padding:8px 0;border-bottom:1px solid #243149}
  .feedback-row b{font-size:10px;overflow-wrap:anywhere}
  .feedback-row span,.feedback-row small{font-size:9px;color:var(--muted)}
  .feedback-row small{grid-column:1/-1}
  .queue-counts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:4px 0 14px}
  .queue-counts div{border:1px solid #263650;background:#0b1424;padding:9px;min-width:0}
  .queue-counts small,.queue-counts strong{display:block}
  .queue-counts small{font-size:9px;color:var(--muted)}
  .queue-counts strong{font:600 20px Consolas,monospace;margin-top:5px}
  .queue-tools{display:grid;grid-template-columns:auto minmax(120px,1fr) auto minmax(110px,.55fr) auto;align-items:center;gap:7px;margin:4px 0 10px}
  .queue-search-label{color:var(--muted);font-size:9px}
  .queue-search,.queue-status-filter{min-width:0;width:100%;box-sizing:border-box;padding:8px 9px;border:1px solid #31415d;background:#0b1424;color:var(--text);font:inherit;font-size:10px}
  .queue-search:focus-visible,.queue-status-filter:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}
  .queue-result-count{grid-column:1/-1;color:var(--muted);font-size:9px;text-align:right}
  @media(max-width:700px){.queue-tools{grid-template-columns:auto minmax(0,1fr)}.queue-result-count{grid-column:1/-1;text-align:left}}
  .queue-list,.worker-list{display:grid;gap:7px}
  .queue-task{display:grid;grid-template-columns:auto minmax(0,1fr);gap:5px 9px;align-items:center;padding:8px 0;border-bottom:1px solid #243149}
  .queue-task b,.worker-row b{font-size:10px;overflow-wrap:anywhere}
  .queue-task small{grid-column:2;color:var(--muted);font-size:9px}
  .task-status{font:9px Consolas,monospace;color:var(--cyan);border:1px solid #347486;padding:3px 5px}
  .task-waiting_approval{color:var(--gold);border-color:#806849}
  .task-failed{color:var(--red);border-color:#75464b}
  .queue-note{display:block;color:var(--muted);margin-top:8px;font-size:9px}
  .worker-heading{display:flex;justify-content:space-between;margin:15px 0 8px;padding-top:12px;border-top:1px solid #243149;font-size:10px}
  .worker-heading span,.worker-row small{color:var(--muted);font-size:9px}
  .worker-row{display:flex;align-items:center;gap:8px;padding:5px 0}
  .worker-row b{flex:1}
  .worker-indicator{width:7px;height:7px;border-radius:50%;background:var(--mint);box-shadow:0 0 8px currentColor;flex:none}
  .worker-indicator.worker-stale{background:var(--gold);color:var(--gold)}
  .empty-state.compact{padding:12px;font-size:10px}
  @media(max-width:700px){.queue-counts{grid-template-columns:repeat(2,minmax(0,1fr))}.feedback-form{grid-template-columns:1fr 1fr}.feedback-columns{grid-template-columns:1fr}}

  .command-trigger{display:inline-flex;align-items:center;gap:9px;padding:8px 11px;border:1px solid #38536a;background:#0c1a29;color:#c8e7e8;font:inherit;font-size:10px;cursor:pointer}
  .command-trigger kbd,.command-dialog kbd{padding:3px 6px;border:1px solid #3a5068;background:#101d2d;color:#a9c1cf;font:10px Consolas,monospace}
  .command-backdrop{position:fixed;inset:0;z-index:1000;display:grid;place-items:start center;padding:12vh 18px 24px;background:rgba(2,7,14,.78);backdrop-filter:blur(10px)}
  .command-dialog{width:min(620px,100%);max-height:72vh;overflow:auto;border:1px solid #47677d;background:linear-gradient(155deg,#101e30,#09111d 72%);box-shadow:0 28px 100px rgba(0,0,0,.55);padding:18px}
  .command-title-row{display:flex;justify-content:space-between;align-items:start;gap:12px;margin-bottom:14px}
  .command-title-row small{color:#7cb7bd;font-size:9px;letter-spacing:.12em}
  .command-title-row h2{margin:6px 0 0;color:#e4f2ef;font-size:19px;font-weight:600}
  .command-close{border:1px solid #3a5068;background:#0d1928;color:#b9cbd4;padding:6px 9px;cursor:pointer}
  .command-search{box-sizing:border-box;width:100%;padding:13px 14px;border:1px solid #4b7182;outline:none;background:#07111d;color:#e5f4f2;font:inherit;font-size:13px}
  .command-search:focus{border-color:#8ad2cb;box-shadow:0 0 0 2px rgba(138,210,203,.14)}
  .command-results{display:grid;gap:4px;margin-top:12px}
  .command-result{display:grid;grid-template-columns:28px minmax(0,1fr) auto;align-items:center;gap:10px;padding:10px 9px;border:1px solid transparent;color:#e0ecec;text-decoration:none}
  .command-result:hover,.command-result:focus-visible{outline:none;border-color:#3e6979;background:#102538}
  .command-result-glyph{display:grid;place-items:center;width:27px;height:27px;border:1px solid #385d6c;color:#9bd4cb}
  .command-result b,.command-result small{display:block}
  .command-result b{font-size:12px}
  .command-result small{margin-top:3px;color:#91a6b3;font-size:10px}
  .command-empty{padding:14px;color:#a6b7c0;font-size:11px}
  .command-dialog footer{display:flex;justify-content:space-between;gap:10px;margin-top:12px;padding-top:10px;border-top:1px solid #26394c;color:#91a6b3;font-size:9px}
  @media(max-width:700px){.command-trigger kbd{display:none}.command-backdrop{padding:8vh 10px 12px}.command-dialog{padding:13px}.top-actions{gap:6px}.top-actions>span{display:none}}
  @media(prefers-reduced-motion:reduce){.command-backdrop{backdrop-filter:none}}
  .guide-backdrop{position:fixed;inset:0;z-index:1100;display:grid;place-items:center;padding:18px;background:rgba(2,7,14,.84);backdrop-filter:blur(12px)}
  .guide-dialog{position:relative;margin:0;max-width:none;width:min(760px,100%);max-height:88vh;overflow:auto;border:1px solid #54758a;background:radial-gradient(ellipse at 50% 0%,#17334a 0%,#0c1727 48%,#08111d 100%);box-shadow:0 30px 110px rgba(0,0,0,.62);padding:clamp(16px,3vw,28px);color:#e3f0ee}
  .guide-header{display:flex;justify-content:space-between;gap:18px;align-items:start}.guide-header small{color:#8ed0c8;font-size:9px;letter-spacing:.14em}.guide-header h2{font-size:clamp(20px,3vw,28px);margin:7px 0}.guide-header p{color:#a8bbc4;font-size:11px;line-height:1.6;margin:0;max-width:520px}
  .guide-progress{height:4px;background:#23364b;margin:20px 0 10px}.guide-progress span{display:block;height:100%;background:linear-gradient(90deg,#58b9b3,#d2b16b);transition:width .2s ease}.guide-step-count{display:flex;justify-content:space-between;gap:12px;color:#9cb2be;font-size:10px}.guide-step-count span{color:#d7c18b}
  .guide-visual{display:flex;align-items:center;justify-content:center;gap:24px;min-height:100px;margin:14px 0;padding:12px;border:1px solid #263d53;background:rgba(4,12,22,.55)}.guide-orbit{width:62px;height:62px;display:grid;place-items:center;border:1px solid #6ba7a5;border-radius:50%;box-shadow:0 0 24px rgba(95,191,182,.18);color:#c6f2e8;font-size:28px}.guide-flow{display:flex;align-items:center;gap:9px;color:#c4d6d9;font-size:10px}.guide-flow b{padding:8px 10px;border:1px solid #3b6172;background:#10283a}.guide-flow i{width:20px;height:1px;background:#6ca6a3}
  .guide-copy h3{font-size:18px;margin:12px 0;color:#e8f6f1}.guide-copy>p,.guide-copy div p,.guide-copy aside p{font-size:12px;line-height:1.75;color:#b8c9cf;margin:6px 0 0}.guide-copy>div{padding:12px 0;border-top:1px solid #263a4e}.guide-copy>div>b{font-size:11px;color:#8ed0c8}.guide-copy aside{margin-top:12px;padding:12px;border-left:2px solid #b89556;background:rgba(103,77,35,.13)}.guide-copy aside b{font-size:11px;color:#e0c487}.guide-actions{display:flex;justify-content:space-between;gap:9px;margin-top:20px;padding-top:14px;border-top:1px solid #293d50}.guide-actions button{font-size:11px}
  @media(max-width:600px){.guide-backdrop{padding:8px}.guide-dialog{max-height:94vh;padding:14px}.guide-visual{min-height:76px;gap:12px}.guide-orbit{width:48px;height:48px}.guide-flow{gap:5px}.guide-flow b{padding:7px 6px}.guide-flow i{width:10px}.guide-actions{flex-wrap:wrap}.guide-actions button{flex:1;min-width:90px}}
  @media(prefers-reduced-motion:reduce){.guide-backdrop{backdrop-filter:none}.guide-progress span{transition:none}}
</style>
