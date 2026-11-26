<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { filterCommands, isCommandPaletteShortcut } from "../command-palette.js";
  import { USER_GUIDE_STEPS, getGuideProgress, getGuideStep } from "../user-guide.js";
  import { filterQueueTasks } from "../queue-filter.js";
  import { resolveProductAttempt } from "./lib/video-trace.js";
  import { scoreIdea, toInspirationBrief } from "../value-studio.js";
  import "./design/muse.css";
  import "./design/bridge.css";
  import CreativeStudio from "./lib/CreativeStudio.svelte";
  import Icon from "./lib/Icon.svelte";
  import WindowChrome from "./lib/WindowChrome.svelte";
  import CommandOrbit from "./lib/CommandOrbit.svelte";
  import CelestialBaguaLab from "./lib/CelestialBaguaLab.svelte";
  import KnowledgeStarMap from "./lib/KnowledgeStarMap.svelte";
  import AgentGalaxyPanel from "./lib/AgentGalaxyPanel.svelte";
  import PrometheusCorePanel from "./lib/PrometheusCorePanel.svelte";
  import InitiativePerformancePanel from "./lib/InitiativePerformancePanel.svelte";
  import VideoReleaseReview from "./lib/VideoReleaseReview.svelte";
  import VideoVisualReview from "./lib/VideoVisualReview.svelte";
  import MemPalacePanel from "./lib/MemPalacePanel.svelte";
  import CompanionPanel from "./lib/CompanionPanel.svelte";
  import MobileCompanionPanel from "./lib/MobileCompanionPanel.svelte";
  import {
    createApiClient,
    resolveDesktopConnection,
    type AgentRecord,
    type AgentOrgChartResponse,
    type ApiClient,
    type CompanionRemoteClient,
    type CelestialCatalogResponse,
    type FeedbackRecord,
    type GalaxyResponse,
    type ImprovementProposal,
    type ImprovementDetail,
    type KnowledgeGraphResponse,
    type MuseEvent,
    type ProductRecord,
    type PrometheusCoreResponse,
    type SubtitleReviewDocument,
    type InitiativeTimeline,
    type InitiativeTimelineEvent,
    type VideoAttemptSummary,
    type VideoRecipeTrace,
    type VideoEngineStatus,
    type GpuResourceStatus,
    type QueueSnapshot,
    type WorkerRecord,
    type WorkflowRunRecord,
    type SelfImprovementStatus,
    type MemPalaceStatusResponse,
    type SystemDashboardResponse,
    type FusionRecord,
    type ContextualInquiryReviewSummary,
    type ContextualInquiryReviewResult,
  } from "./lib/api";
  const POLL_INTERVAL_MS = 5000;

  interface Snapshot {
    agents: AgentRecord[];
    orgChart: AgentOrgChartResponse | null;
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
  let mobileCompanion = false;
  let mobileCompanionConnected = false;
  let mobileCompanionClient: CompanionRemoteClient | null = null;
  let stale = false;
  let message = "";
  let registryConfigured = false;
  let agents: AgentRecord[] = [];
  let agentOrgChart: AgentOrgChartResponse | null = null;
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
  let mempalaceStatus: MemPalaceStatusResponse | null = null;
  let systemDashboard: SystemDashboardResponse | null = null;
  let contextualReviewTickets: ContextualInquiryReviewSummary[] = [];
  let selectedContextualReviewId: string | null = null;
  let contextualReviewResult: ContextualInquiryReviewResult | null = null;
  let contextualReviewBusy = false;
  let contextualReviewMessage = "";
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
  let evolvingInspirationId = "";
  let inspirationMessage = "";
  let inspirations: import("./lib/api").InspirationRecord[] = [];
  let artifacts: import("./lib/api").ArtifactRecord[] = [];
  let products: ProductRecord[] = [];
  let selectedSubtitleReviewProductId: string | null = null;
  let subtitleReviewDocument: SubtitleReviewDocument | null = null;
  let subtitleReviewReviewedIndexes: number[] = [];
  let subtitleReviewCorrections: Record<number, string> = {};
  let subtitleReviewReviewer = "";
  let subtitleReviewReviewerId = "";
  let subtitleReviewReviewerRole = "subtitle_editor";
  let subtitleReviewRationale = "";
  let subtitleReviewEvidenceRefs = "";  let subtitleReviewConfirmation = "";
  let subtitleReviewBusy = false;
  let subtitleReviewSubmitting = false;
  let subtitleReviewMessage = "";
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
  let selectedRecipeTraceAttemptId: string | null = null;
  let recipeTrace: VideoRecipeTrace | null = null;
  let recipeTraceBusy = false;
  let recipeTraceMessage = "";
  let contactSheetUrl = "";
  let contactSheetMessage = "";
  let selectedVisualReviewProductId: string | null = null;
  let visualReviewSheetUrl = "";
  let visualReviewMessage = "";
  let visualReviewBusy = false;
  let visualReviewSubmitting = false;
  let visualReviewRecords: import("./lib/api").VisualReviewRecord[] = [];
  let visualRevisionBrief: import("./lib/api").VisualRevisionBrief | null = null;
  let visualQualityReport: import("./lib/api").VideoVisualQualityReport | null = null;
  onDestroy(() => {
    if (visualReviewSheetUrl) URL.revokeObjectURL(visualReviewSheetUrl);
    contextualReviewResult = null;
  });
  let videoEngineStatus: VideoEngineStatus | null = null;
  let gpuResourceStatus: GpuResourceStatus | null = null;
  let pairingCode = "";
  let videoMode: "auto" | "local_assets" | "prompt_to_video" = "auto";
  let resolvedVideoMode: "local_assets" | "prompt_to_video" = "local_assets";
  let videoSourceKey = "muse-prometheus-teaser-v1";
  let videoTitle = "";
  let videoPrompt = "";
  let videoNegativePrompt = "";
  let videoNarrationScript = "";
  let videoDuration = "2";
  let videoMotionProfile: "none" | "brand_intro_outro" | "brand_scene_transitions" = "brand_intro_outro";
  let videoAspectRatio: "16:9" | "9:16" = "16:9";
  let videoAudioName = "";
  let videoAudioMode: "music" | "narration" = "music";
  let videoApprovalRequests: Array<{
    id: string; title: string; status: string; can_approve: boolean;
    attempt_id?: string | null; execution_ticket_id?: string | null; execution_status?: string | null;
    approved_by?: string | null;
  }> = [];
  let videoBusy = false;
  let videoMessage = "";
  $: resolvedVideoMode = videoMode === "auto"
    ? (videoEngineStatus?.comfyui.status === "ready"
      && videoEngineStatus?.commander_planner?.status === "ready"
      && gpuResourceStatus?.status === "idle" ? "prompt_to_video" : "local_assets")
    : videoMode;
  let downloadMessage = "";
  let lanUrls: string[] = [];

  // ── 价值创作台：灵感 → 评分 → 研究 → 视频 → 审片反馈闭环 ──
  let studioIdea = "";
  let studioAudience = "";
  let studioGoal = "";
  let studioResult: ReturnType<typeof scoreIdea> | null = null;
  let studioScoredSource = "";
  let studioBusy = false;
  let studioMessage = "";
  let studioStage = 0; // 0=灵感 1=评分 2=研究 3=视频 4=回流

  // ── 自主运行时（长驻守护进程） ──
  let daemonStatus: import("./lib/api").DaemonStatus | null = null;
  let daemonBusy = "";
  let daemonMessage = "";
  // ── 一键创作流水线 ──
  let pipelineRecipes: import("./lib/api").PipelineRecipe[] = [];
  let pipelineJobs: import("./lib/api").PipelineJob[] = [];
  let pipelineBusy = false;
  let pipelineMessage = "";
  let selectedRecipe = "high_value_short_v2";
  let pipelinePollHandle: ReturnType<typeof setInterval> | null = null;

  // ── 赛博玄枢 · 星环 / 八卦星宿 / 知识星图 / Agent星系 / 普罗米修斯核心 ──
  let celestialCatalog: CelestialCatalogResponse | null = null;
  let prometheusCore: PrometheusCoreResponse | null = null;
  let knowledgeGraph: KnowledgeGraphResponse | null = null;
  let galaxySnapshot: GalaxyResponse | null = null;
  let graphNodeTypeFilter: string | null = null;
  let graphSearchQuery = "";
  let presetLowerBits = "";
  let reducedFx = false;

  type ViewKey = "overview" | "fusion" | "system" | "companion" | "tasks" | "agents" | "memory" | "knowledge" | "studio" | "evolve" | "runtime" | "reviews";
  const VIEWS: Array<{ key: ViewKey; label: string; icon: string; hint: string }> = [
    { key: "overview", label: "总览", icon: "core", hint: "调度阵、健康度与实时指标" },
    { key: "fusion", label: "超融合", icon: "knowledge", hint: "一键导入源码，提取能力并连接 Muse 工作流" },
    { key: "system", label: "万象中枢", icon: "core", hint: "系统文件、工作区、技能与服务连接" },
    { key: "companion", label: "设备安全", icon: "shield", hint: "Host 配对挑战、设备凭据与安全审计" },
    { key: "tasks", label: "任务中心", icon: "tasks", hint: "队列、Commander 工作流与执行节点" },
    { key: "agents", label: "智能体", icon: "agents", hint: "唯一 Commander、专家角色与实连 Agent" },
    { key: "memory", label: "记忆库", icon: "memory", hint: "双轨记忆、MemPalace 与灵感池" },
    { key: "knowledge", label: "知识图谱", icon: "knowledge", hint: "Research Wiki 星图与卦象透镜" },
    { key: "studio", label: "创作台", icon: "studio", hint: "选题评分、影像工坊与成果库" },
    { key: "evolve", label: "进化", icon: "evolve", hint: "反馈回流、能力雷达与改进提案" },
    { key: "runtime", label: "运行时", icon: "flame", hint: "常驻引擎、创作流水线与事件流" },
    { key: "reviews", label: "个案审查", icon: "lock", hint: "加密审查结果、人工复核与独立审批" },
  ];
  $: visibleViews = mobileCompanion ? VIEWS.filter((item) => item.key === "companion") : VIEWS;
  const ANCHOR_VIEW: Record<string, ViewKey> = {

    "#overview": "overview",
    "#celestial-lab": "knowledge",
    "#knowledge-starmap": "knowledge",
    "#agents": "agents",
    "#prometheus-core": "overview",
    "#value-studio": "studio",
    "#creative-studio": "studio",
    "#daemon-runtime": "runtime",
    "#pipeline": "runtime",
    "#inspirations": "memory",
    "#mempalace": "memory",
    "#products": "studio",
    "#queue": "tasks",
    "#feedback": "evolve",
    "#events": "overview",
  };
  let view: ViewKey = "overview";
  let fusionSourceUrl = "";
  let fusionFile: File | null = null;
  let fusionBusy = false;
  let fusionMessage = "";
  let fusionResult: FusionRecord | null = null;
  let fusionJobs: FusionRecord[] = [];
  let selectedRole = "";
  let clockLabel = "";
  let dateLabel = "";

  function tickClock(): void {
    const now = new Date();
    clockLabel = now.toLocaleTimeString("zh-CN", { hour12: false });
    dateLabel = now.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit", weekday: "short" });
  }

  function setView(next: ViewKey): void {
    if (next !== "reviews") closeContextualInquiryReview();
    view = next;
    document.querySelector(".stage")?.scrollTo({ top: 0, behavior: "smooth" });
    if (next === "fusion") void loadFusionJobs();
  }

  async function loadFusionJobs(): Promise<void> {
    if (!client) return;
    try { fusionJobs = (await client.fusionJobs(20)).jobs; } catch { /* retain the last local snapshot */ }
  }

  function selectFusionFile(event: Event): void {
    const input = event.currentTarget as HTMLInputElement;
    const selected = input.files?.[0] ?? null;
    if (selected && (selected.size < 1 || selected.size > 8 * 1024 * 1024 || !selected.name.toLowerCase().endsWith(".zip"))) {
      fusionFile = null;
      input.value = "";
      fusionMessage = "只接受不超过 8 MiB 的 ZIP 源码压缩包。";
      return;
    }
    fusionFile = selected;
    fusionMessage = selected ? `已选择 ${selected.name} · ${(selected.size / 1024 / 1024).toFixed(2)} MiB` : "";
  }

  async function submitFusion(): Promise<void> {
    if (!client || fusionBusy) return;
    const sourceUrl = fusionSourceUrl.trim();
    if (Boolean(sourceUrl) === Boolean(fusionFile)) {
      fusionMessage = "请只提供一个 GitHub/GitLab 地址，或选择一个 ZIP 压缩包。";
      return;
    }
    fusionBusy = true;
    fusionMessage = "正在安全检查源码并提取可复用能力…";
    try {
      let response: { api_version: 1; fusion: FusionRecord };
      if (fusionFile) {
        const bytes = new Uint8Array(await fusionFile.arrayBuffer());
        let binary = "";
        for (let offset = 0; offset < bytes.length; offset += 0x8000) {
          binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
        }
        response = await client.submitFusion({
          archive_base64: btoa(binary), file_name: fusionFile.name,
        });
      } else {
        response = await client.submitFusion({ source_url: sourceUrl });
      }
      fusionResult = response.fusion;
      fusionMessage = fusionResult.duplicate
        ? "检测到相同源码，已载入既有融合记录。"
        : `分析完成并已自动接入候选能力图：检查 ${fusionResult.analysis.file_count} 个文件，识别 ${fusionResult.analysis.capabilities.length} 项候选能力。`;
      await loadFusionJobs();
    } catch (error) {
      fusionMessage = error instanceof Error ? error.message : "融合分析失败，请检查来源后重试。";
    } finally {
      fusionBusy = false;
    }
  }

  async function approveFusion(): Promise<void> {
    if (!client || !fusionResult || fusionBusy || fusionResult.status !== "integrated_unverified") return;
    fusionBusy = true;
    fusionMessage = "正在将已审阅的能力关系写入 Muse 能力图…";
    try {
      const response = await client.approveFusion(fusionResult.fusion_id);
      fusionResult = response.fusion;
      fusionMessage = "已记录人工审阅；外部代码仍保持未验证，尚未获准执行。";
      await loadFusionJobs();
    } catch (error) {
      fusionMessage = error instanceof Error ? error.message : "能力入库失败。";
    } finally {
      fusionBusy = false;
    }
  }

  async function rollbackFusion(): Promise<void> {
    if (!client || !fusionResult || fusionBusy || !["integrated_unverified", "approved_unverified"].includes(fusionResult.status)) return;
    fusionBusy = true;
    fusionMessage = "正在停用这次融合写入的候选能力…";
    try {
      const response = await client.rollbackFusion(fusionResult.fusion_id);
      fusionResult = response.fusion;
      fusionMessage = "已回滚：相关能力已标记为 disabled，原始压缩包和审计记录保留。";
      await loadFusionJobs();
    } catch (error) {
      fusionMessage = error instanceof Error ? error.message : "融合回滚失败。";
    } finally {
      fusionBusy = false;
    }
  }

  function focusAnchor(selector: string): void {
    const next = ANCHOR_VIEW[selector] ?? "overview";
    if (next !== "reviews") closeContextualInquiryReview();
    view = next;
    window.setTimeout(() => {
      document.querySelector(selector)?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 60);
  }

  function handleRoleSelect(id: string): void {
    selectedRole = selectedRole === id ? "" : id;
  }

  async function queryKnowledgeGraph(opts: { nodeType: string | null; q: string }): Promise<void> {
    graphNodeTypeFilter = opts.nodeType;
    graphSearchQuery = opts.q;
    if (!client) return;
    try {
      knowledgeGraph = await client.knowledgeGraph({
        limit: 160,
        nodeType: graphNodeTypeFilter,
        q: graphSearchQuery,
      });
    } catch {
      // keep previous graph snapshot on transient error
    }
  }

  function handleInspectBagua(bits: string): void {
    presetLowerBits = bits;
    jumpToAnchor("#celestial-lab");
  }

  async function saveHypothesisInspiration(payload: { title: string; content: string; tags: string[]; domains: string[] }): Promise<void> {
    if (!client) return;
    await client.submitInspiration({
      title: payload.title,
      content: payload.content,
      tags: payload.tags,
      research_domains: payload.domains,
      allow_external_research: false,
      source: "muse_local_dashboard",
    });
    await refresh();
  }

  function jumpToAnchor(selector: string): void {
    focusAnchor(selector);
  }
  $: studioDirty = studioResult === null || studioScoredSource !== studioCompose();
  function studioCompose(): string {
    return [studioIdea.trim(), studioAudience.trim() ? `受众：${studioAudience.trim()}` : "", studioGoal.trim() ? `目标：${studioGoal.trim()}` : ""].filter(Boolean).join("\n");
  }
  function evaluateStudioIdea(): void {
    studioMessage = "";
    const text = studioCompose();
    if (!text.trim()) {
      studioResult = null;
      studioMessage = "先写下你想创作的题目，Muse 才会做启发式评估。";
      return;
    }
    studioResult = scoreIdea(text);
    studioScoredSource = text;
    studioStage = Math.max(studioStage, 1);
    studioMessage = studioResult.empty ? "题目为空。" : `评估完成：${studioResult.tier} 级 / ${studioResult.total} 分。评分只是排序启发，不预测收益。`;
  }
  function useStudioAsVideo(): void {
    if (!studioResult || !studioCompose().trim()) return;
    videoMode = "prompt_to_video";
    videoTitle = studioIdea.trim().slice(0, 80) || studioAudience.trim().slice(0, 80) || "Muse 创作短片";
    videoPrompt = studioCompose().slice(0, 2000);
    studioStage = Math.max(studioStage, 3);
    studioMessage = "已带入视频创作表单；确认本地 ComfyUI 就绪后即可生成镜头。";
    document.getElementById("products")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  async function sendStudioToInbox(): Promise<void> {
    if (!studioResult || !client || studioBusy) return;
    const text = studioCompose();
    if (!text.trim()) return;
    studioBusy = true;
    studioMessage = "";
    try {
      const brief = toInspirationBrief(studioResult, text);
      const tags = [...studioResult.potentialTags, ...studioResult.scores.filter((item) => item.matched.length).map((item) => item.label)].slice(0, 12);
      await client.submitInspiration({
        content: brief.slice(0, 2000),
        allow_external_research: false,
        tags,
        research_domains: studioResult.suggestedPlatforms.slice(0, 6),
        source: "muse_local_dashboard",
      });
      studioStage = Math.max(studioStage, 2);
      studioMessage = "已写入灵感工坊候选池（默认仅本地，不联网）。研究后可再用「生成视频」提交。";
      await refresh();
    } catch (error) {
      studioMessage = error instanceof Error ? error.message : "写入灵感候选池失败";
    } finally {
      studioBusy = false;
    }
  }

  async function controlDaemon(action: "start" | "pause" | "resume" | "stop" | "once"): Promise<void> {
    if (!client || daemonBusy) return;
    daemonBusy = action;
    daemonMessage = "";
    try {
      const result = await client.daemonControl(action);
      const labels: Record<string, string> = {
        start: "已请求启动常驻运行时",
        pause: "已暂停（保留心跳与租约）",
        resume: "已恢复运行",
        stop: "已请求停止，等待当前工作完成",
        once: "单轮维护已执行完成",
      };
      daemonMessage = `${labels[action]}。当前状态：${result.result?.state ?? "未知"}。`;
      if (action === "once" && result.result?.task) {
        daemonMessage += " 本轮处理了一个任务。";
      }
      await refresh();
    } catch (error) {
      daemonMessage = error instanceof Error ? error.message : "运行时控制失败";
    } finally {
      daemonBusy = "";
    }
  }

  async function submitPipeline(): Promise<void> {
    if (!client || pipelineBusy || !selectedRecipe) return;
    pipelineBusy = true;
    pipelineMessage = "";
    try {
      const result = await client.submitPipeline({ recipe_id: selectedRecipe });
      pipelineMessage = `已提交「${result.job.recipe_label}」；${result.job.exists_before_run ? "注意：目标输出目录已存在，脚本可能拒绝覆盖。" : "任务已排队，正在本地渲染。"}`;
      await refresh();
      // Poll this job until it reaches a terminal state.
      const jobId = result.job.job_id as string;
      if (pipelinePollHandle) clearInterval(pipelinePollHandle);
      pipelinePollHandle = window.setInterval(async () => {
        try {
          const response = await client?.pipelineJob(jobId);
          const job = response?.job;
          if (!job) return;
          pipelineJobs = [job, ...pipelineJobs.filter((item) => item.job_id !== jobId)];
          if (job.status === "succeeded" || job.status === "failed") {
            if (pipelinePollHandle) clearInterval(pipelinePollHandle);
            pipelinePollHandle = null;
            pipelineMessage = job.status === "succeeded"
              ? "流水线已完成，交付包已生成；请在产品库查看并人工审片。"
              : `流水线失败：${job.error ?? "未知错误"}`;
            await refresh();
          }
        } catch {
          // transient; keep polling
        }
      }, 3000);
    } catch (error) {
      pipelineMessage = error instanceof Error ? error.message : "流水线提交失败";
    } finally {
      pipelineBusy = false;
    }
  }

  function daemonAgeLabel(age: number | null | undefined): string {
    if (age === null || age === undefined) return "—";
    if (age < 60) return `${age}s`;
    return `${Math.floor(age / 60)}m`;
  }

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
    const targetSelector = guideStep.target;
    closeGuide();
    jumpToAnchor(targetSelector);
  }
  const quickCommands = [
    { id: "overview", label: "赛博星环浑天仪", description: "查看三重星环、实时状态与算力护栏", keywords: ["总览", "星环", "状态", "主页"], target: "#overview" },
    { id: "celestial", label: "八卦与星宿工坊", description: "易经六十四卦推演、二十八星宿与四体系提问", keywords: ["八卦", "易经", "星宿", "玄学", "假设"], target: "#celestial-lab" },
    { id: "starmap", label: "知识星图", description: "交互式漫游 Research Wiki 星体与治理审计", keywords: ["知识", "星图", "wiki", "图谱", "节点"], target: "#knowledge-starmap" },
    { id: "agents", label: "AI Agent 星系", description: "唯一 Commander、专家行星与路由沙盒预演", keywords: ["agent", "智能体", "星系", "commander", "路由"], target: "#agents" },
    { id: "prometheus", label: "普罗米修斯核心", description: "五阶火种账本、演化原则与现实/愿景矩阵", keywords: ["普罗米修斯", "愿景", "能力", "核心", "哲学"], target: "#prometheus-core" },
    { id: "creative-studio", label: "视频创作工作室", description: "选港风/像素/宇宙风格，生成粤语解说并一键成片", keywords: ["视频", "工作室", "港风", "像素", "宇宙", "粤语", "解说", "风格"], target: "#creative-studio" },
    { id: "studio", label: "价值创作台", description: "评估灵感价值并串起研究、视频与变现假设", keywords: ["价值", "选题", "变现", "创作台", "评分"], target: "#value-studio" },
    { id: "daemon", label: "自主运行时", description: "启动/暂停/恢复 24 小时常驻运行时", keywords: ["运行时", "常驻", "守护", "24小时", "daemon"], target: "#daemon-runtime" },
    { id: "pipeline", label: "创作流水线", description: "一键运行研究型高价值短片配方", keywords: ["流水线", "一键", "短片", "pipeline", "配方"], target: "#pipeline" },
    { id: "inspiration", label: "灵感工坊", description: "提交想法，启动本地记录或研究", keywords: ["灵感", "想法", "研究"], target: "#inspirations" },
    { id: "video", label: "视频创作", description: "Wan 2.1 文生视频或 FFmpeg 素材剪辑", keywords: ["视频", "wan", "mp4", "剪辑"], target: "#products" },
    { id: "products", label: "成果产品库", description: "预览、下载 MP4 与工程 ZIP", keywords: ["成果", "产品", "下载", "成片"], target: "#products" },
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
  $: pendingAttention = (queueSnapshot?.counts.waiting_approval ?? 0);
  $: selectedRoleDetail = (galaxySnapshot?.specialist_profiles ?? []).find(
    (item) => item.specialist_id === selectedRole,
  ) ?? null;
  $: ramLabel = prometheusCore?.resource_governor
    ? (() => {
        const g = prometheusCore!.resource_governor;
        if (typeof g.available_memory_bytes !== "number" || typeof g.total_memory_bytes !== "number") return "未探测";
        return `${(g.available_memory_bytes / 1024 ** 3).toFixed(1)} / ${(g.total_memory_bytes / 1024 ** 3).toFixed(0)} GiB`;
      })()
    : "未读取";
  $: gpuLabel = gpuResourceStatus?.status === "reserved"
    ? `占用 · ${gpuResourceStatus.lease?.owner_kind ?? "task"}`
    : gpuResourceStatus?.status === "idle"
      ? "空闲（显存未测）"
      : "未知 · 不超卖";
  $: stageMaxima = prometheusCore
    ? {
        ignition: Math.max(1, prometheusCore.stages.ignition.inspirations_count + prometheusCore.stages.ignition.initiatives_count),
        discovery: Math.max(1, prometheusCore.stages.discovery.knowledge_nodes_total),
        synthesis: Math.max(1, prometheusCore.stages.synthesis.knowledge_edges_total),
        creation: Math.max(1, prometheusCore.stages.creation.products_count),
        transmission: Math.max(1, prometheusCore.stages.transmission.progress_events_count),
      }
    : { ignition: 1, discovery: 1, synthesis: 1, creation: 1, transmission: 1 };
  $: stageLadder = prometheusCore
    ? [
        { key: "ignition", label: "点火", value: prometheusCore.stages.ignition.inspirations_count, tone: "is-jade" },
        { key: "discovery", label: "求知", value: prometheusCore.stages.discovery.knowledge_nodes_total, tone: "is-jade" },
        { key: "synthesis", label: "融合", value: prometheusCore.stages.synthesis.knowledge_edges_total, tone: "is-cyan" },
        { key: "creation", label: "创造", value: prometheusCore.stages.creation.products_count, tone: "" },
        { key: "transmission", label: "传承", value: prometheusCore.stages.transmission.progress_events_count, tone: "" },
      ].map((row) => ({
        ...row,
        pct: Math.min(100, Math.round((row.value / (stageMaxima as Record<string, number>)[row.key]) * 100)),
      }))
    : [];
  $: queueLadder = (() => {
    const c = queueSnapshot?.counts;
    const peak = Math.max(1, c ? Math.max(c.queued, c.running, c.waiting_approval, c.failed, c.succeeded) : 1);
    return [
      { label: "排队", value: c?.queued ?? 0, tone: "" },
      { label: "执行", value: c?.running ?? 0, tone: "is-cyan" },
      { label: "待批", value: c?.waiting_approval ?? 0, tone: "" },
      { label: "失败", value: c?.failed ?? 0, tone: "is-dim" },
    ].map((row) => ({ ...row, pct: Math.min(100, Math.round((row.value / peak) * 100)) }));
  })();

  $: filteredQueueTasks = filterQueueTasks(queueSnapshot?.active ?? [], queueQuery, queueStatusFilter);
  $: successRate = aggregateRate(agents, "succeeded", "completed");
  $: reviewRate = aggregateRate(agents, "review_approved", "reviewed");
  $: connectionStatusKey = (mobileCompanion ? mobileCompanionConnected : connected && !stale) ? "online" : stale ? "stale" : "offline";
  $: connectionLabel = mobileCompanion ? (mobileCompanionConnected ? "Muse Host · 已连接" : "Muse Companion · 未配对") : connected ? "本地核心 · 在线" : stale ? "连接中断 · 数据已过期" : "本地核心 · 未连接";
  $: connectionBadge = mobileCompanion ? (mobileCompanionConnected ? "SECURE" : "UNPAIRED") : connected ? "LIVE" : stale ? "STALE" : "OFFLINE";
  $: updatedLabel = updatedAt ? `更新于 ${new Date(updatedAt).toLocaleTimeString()}` : "尚无实时数据";
  $: videoEngineSummary = videoEngineStatus
    ? `FFmpeg ${videoEngineStatus.ffmpeg_available ? "就绪" : "缺失"} · ${videoEngineStatus.hardware_encoders.includes("h264_amf") ? "AMD AMF 可用" : "硬件编码器未检测到"} · Commander 模型 ${videoEngineStatus.commander_planner?.status === "ready" ? "就绪" : "未就绪"} · ComfyUI ${videoEngineStatus.comfyui.status === "ready" ? "已连接" : "未连接"} · GPU ${gpuResourceStatus?.status ?? "unknown"} · Velorn ${videoEngineStatus.velorn.status === "ready" ? "已连接" : "未连接"}`
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
    agentOrgChart = snapshot.orgChart;
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

  function handleMobileClientChange(next: CompanionRemoteClient | null): void {
    mobileCompanionClient = next;
  }

  function handleMobileConnectionChange(isConnected: boolean): void {
    mobileCompanionConnected = isConnected;
    message = isConnected ? "Muse Companion 已建立安全只读连接。" : "Muse Companion 尚未配对。";
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
          const [health, agentResponse, overview, feedbackResponse, improvementResponse, selfImprovementResponse, inspirationResponse, artifactResponse, productResponse, engineResponse, videoAttemptResponse, videoApprovalResponse, gpuResourceResponse, daemonResponse, recipesResponse, jobsResponse, celestialResponse, prometheusResponse, graphResponse, galaxyResponse, mempalaceResponse, contextualReviewResponse, systemDashboardResponse] = await Promise.all([
            client.health(),
            client.agents(),
            client.overview(),
            client.feedback(20),
            client.improvements(),
            client.selfImprovement(),
            client.inspirations(50),
            client.artifacts(50),
            client.products(50).catch(() => null),
            client.videoEngines(),
            client.videoAttempts().catch(() => null),
            client.videoApprovalRequests(30).catch(() => null),
            client.gpuResources().catch(() => null),
            client.daemonStatus().catch(() => null),
            client.pipelineRecipes().catch(() => null),
            client.pipelineJobs(30).catch(() => null),
            client.celestialCatalog().catch(() => null),
            client.prometheusCore().catch(() => null),
            client.knowledgeGraph({ limit: 160, nodeType: graphNodeTypeFilter, q: graphSearchQuery }).catch(() => null),
            client.galaxy().catch(() => null),
            client.mempalaceStatus().catch(() => null),
            client.contextualInquiryReviews(100).catch(() => null),
            client.systemDashboard().catch(() => null),
          ]);
          lastSnapshot = {
            agents: Array.isArray(agentResponse.agents) ? agentResponse.agents : [],
            orgChart: agentResponse.org_chart ?? null,
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
          if (mempalaceResponse) mempalaceStatus = mempalaceResponse;
          if (systemDashboardResponse) systemDashboard = systemDashboardResponse;
          if (contextualReviewResponse) {
            contextualReviewTickets = Array.isArray(contextualReviewResponse.reviews) ? contextualReviewResponse.reviews : [];
            if (selectedContextualReviewId && !contextualReviewTickets.some((item) => item.task_id === selectedContextualReviewId)) {
              closeContextualInquiryReview();
            }
          }
          inspirations = Array.isArray(inspirationResponse.inspirations) ? inspirationResponse.inspirations : [];
          artifacts = Array.isArray(artifactResponse.artifacts) ? artifactResponse.artifacts : [];
          products = Array.isArray(productResponse?.products) ? productResponse.products : [];
          videoAttemptSummary = videoAttemptResponse;
          videoApprovalRequests = Array.isArray(videoApprovalResponse?.approval_requests) ? videoApprovalResponse.approval_requests : [];
          videoEngineStatus = engineResponse;
          gpuResourceStatus = gpuResourceResponse;
          daemonStatus = daemonResponse;
          if (celestialResponse) celestialCatalog = celestialResponse;
          if (prometheusResponse) prometheusCore = prometheusResponse;
          if (graphResponse) knowledgeGraph = graphResponse;
          if (galaxyResponse) galaxySnapshot = galaxyResponse;
          if (recipesResponse?.recipes) {
            pipelineRecipes = recipesResponse.recipes;
            if (!pipelineRecipes.some((item) => item.id === selectedRecipe) && pipelineRecipes.length) {
              selectedRecipe = pipelineRecipes[0].id;
            }
          }
          if (jobsResponse?.jobs) pipelineJobs = jobsResponse.jobs;
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
  function closeContextualInquiryReview(): void {
    selectedContextualReviewId = null;
    contextualReviewResult = null;
    contextualReviewMessage = "";
    contextualReviewBusy = false;
  }

  async function openContextualInquiryReview(taskId: string): Promise<void> {
    if (!client || !/^[0-9a-f]{32}$/.test(taskId) || contextualReviewBusy) return;
    if (selectedContextualReviewId === taskId) {
      closeContextualInquiryReview();
      return;
    }
    selectedContextualReviewId = taskId;
    contextualReviewResult = null;
    contextualReviewMessage = "";
    contextualReviewBusy = true;
    try {
      const response = await client.contextualInquiryReviewResult(taskId);
      if (selectedContextualReviewId === taskId) contextualReviewResult = response.review;
    } catch (error) {
      if (selectedContextualReviewId === taskId) {
        contextualReviewMessage = error instanceof Error ? error.message : "无法读取加密审查结果";
      }
    } finally {
      if (selectedContextualReviewId === taskId) contextualReviewBusy = false;
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

  async function evolveSingleInspiration(inspirationId: string, enqueueVideo: boolean): Promise<void> {
    if (!client || evolvingInspirationId) return;
    evolvingInspirationId = inspirationId;
    inspirationMessage = "";
    try {
      const response = await client.evolveInspiration({
        inspiration_id: inspirationId,
        enqueue_video: enqueueVideo,
        aspect_ratios: ["9:16", "16:9"],
      });
      const evo = response.evolution;
      const proposalCount = evo.improvement_proposal_ids?.length ?? 0;
      const queuedCount = evo.queued_video_jobs?.length ?? 0;
      inspirationMessage = `已完成「${evo.inspiration_title}」闭环演化：生成 ${proposalCount} 项自我改进提案（待人工审核，不自动改码），分镜蓝图 ${evo.blueprint_path}${queuedCount ? `，并已向 Commander 入队 ${queuedCount} 条 9:16 + 16:9 成片流水线任务` : ""}。`;
      await refresh();
    } catch (error) {
      inspirationMessage = error instanceof Error ? error.message : "灵感深研进化失败";
    } finally {
      evolvingInspirationId = "";
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

  async function toggleVideoRecipeTrace(attemptId: string): Promise<void> {
    if (!client || recipeTraceBusy) return;
    if (selectedRecipeTraceAttemptId === attemptId) {
      selectedRecipeTraceAttemptId = null;
      recipeTrace = null;
      recipeTraceMessage = "";
      contactSheetMessage = "";
      if (contactSheetUrl) URL.revokeObjectURL(contactSheetUrl);
      contactSheetUrl = "";
      return;
    }
    selectedRecipeTraceAttemptId = attemptId;
    recipeTrace = null;
    recipeTraceMessage = "";
    contactSheetMessage = "";
    if (contactSheetUrl) URL.revokeObjectURL(contactSheetUrl);
    contactSheetUrl = "";
    recipeTraceBusy = true;
    try {
      const response = await client.videoRecipeTrace(attemptId);
      recipeTrace = response.trace;
      const contact = response.trace.release_review.checks?.visual_anomaly_review?.contact_sheet;
      const initiativeId = response.trace.product.product_id;
      const versionId = response.trace.product.version_id;
      if (contact?.status === "available" && initiativeId && versionId) {
        try {
          const blob = await client.videoContactSheet(initiativeId, versionId);
          const objectUrl = URL.createObjectURL(blob);
          if (selectedRecipeTraceAttemptId === attemptId) contactSheetUrl = objectUrl;
          else URL.revokeObjectURL(objectUrl);
        } catch (error) {
          contactSheetMessage = error instanceof Error ? error.message : "联系表读取失败";
        }
      } else {
        contactSheetMessage = contact?.status === "not_provided" ? "此版本尚未生成联系表。" : "没有可核验的联系表证据。";
      }
    } catch (error) {
      recipeTraceMessage = error instanceof Error ? error.message : "配方追溯读取失败";
    } finally {
      recipeTraceBusy = false;
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
      audio_loudness_low: "音频响度偏低",
    };
    return labels[code] ?? code;
  }
  function cameraMoveLabel(move?: string): string {
    const labels: Record<string, string> = {
      push_in: "缓慢推进", drift_left: "向左漂移", pull_out: "缓慢拉远",
      rise: "向上升起", drift_right: "向右漂移",
    };
    return move ? labels[move] ?? "未指定运镜" : "静态 / 未指定";
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

  async function toggleProductVisualReview(product: ProductRecord): Promise<void> {
    if (!client || product.kind !== "video_creator" || visualReviewBusy || visualReviewSubmitting) return;
    if (selectedVisualReviewProductId === product.id) {
      selectedVisualReviewProductId = null;
      if (visualReviewSheetUrl) URL.revokeObjectURL(visualReviewSheetUrl);
      visualReviewSheetUrl = "";
      visualReviewMessage = "";
      visualReviewRecords = [];
      visualRevisionBrief = null;
      visualQualityReport = null;
      return;
    }
    selectedVisualReviewProductId = product.id;
    if (visualReviewSheetUrl) URL.revokeObjectURL(visualReviewSheetUrl);
    visualReviewSheetUrl = "";
    visualReviewMessage = "";
    visualReviewRecords = [];
    visualRevisionBrief = null;
    visualQualityReport = null;
    visualReviewBusy = true;
    try {
      const blob = await client.videoContactSheet(product.initiative_id, product.version_id);
      if (selectedVisualReviewProductId !== product.id) return;
      visualReviewSheetUrl = URL.createObjectURL(blob);
      const [history, revision] = await Promise.all([
        client.visualReviews(product.initiative_id, product.version_id),
        client.visualRevisionBrief(product.initiative_id, product.version_id),
      ]);
      if (selectedVisualReviewProductId !== product.id) return;
      visualReviewRecords = history.reviews;
      visualRevisionBrief = revision.brief;
      visualQualityReport = history.visual_quality ?? null;
    } catch (error) {
      visualReviewMessage = error instanceof Error
        ? error.message
        : "没有可核验的联系表；请先为该版本生成并打包联系表。";
    } finally {
      visualReviewBusy = false;
    }
  }

  async function submitProductVisualReview(
    product: ProductRecord,
    payload: import("./lib/api").VisualReviewPayload,
  ): Promise<void> {
    if (!client || product.kind !== "video_creator" || visualReviewSubmitting || !visualReviewSheetUrl) return;
    visualReviewSubmitting = true;
    visualReviewMessage = "";
    try {
      const saved = await client.submitVisualReview(product.initiative_id, product.version_id, payload);
      if (selectedVisualReviewProductId !== product.id) return;
      try {
        const [history, revision] = await Promise.all([
          client.visualReviews(product.initiative_id, product.version_id),
          client.visualRevisionBrief(product.initiative_id, product.version_id),
        ]);
        if (selectedVisualReviewProductId !== product.id) return;
        visualReviewRecords = history.reviews;
        visualRevisionBrief = revision.brief;
        visualQualityReport = history.visual_quality ?? null;
        visualReviewMessage = "审核记录已追加并绑定当前视频及联系表哈希；身份为本机自报，发布权限没有改变。";
      } catch {
        visualReviewRecords = [...visualReviewRecords, saved.record];
        visualReviewMessage = "审核记录已写入；历史列表暂时无法刷新。记录已绑定当前素材哈希，发布权限没有改变。";
      }
    } catch (error) {
      visualReviewMessage = error instanceof Error ? error.message : "视觉审核记录提交失败";
    } finally {
      visualReviewSubmitting = false;
    }
  }
  function subtitleCueTime(seconds: number): string {
    const safe = Math.max(0, seconds);
    const minutes = Math.floor(safe / 60);
    const remainder = (safe - minutes * 60).toFixed(2).padStart(5, "0");
    return minutes + ":" + remainder;
  }

  function markSubtitleCueReviewed(index: number, checked: boolean): void {
    subtitleReviewReviewedIndexes = checked
      ? [...new Set([...subtitleReviewReviewedIndexes, index])].sort((a, b) => a - b)
      : subtitleReviewReviewedIndexes.filter((value) => value !== index);
  }

  async function toggleSubtitleReview(product: ProductRecord): Promise<void> {
    if (!client || subtitleReviewBusy || subtitleReviewSubmitting) return;
    if (selectedSubtitleReviewProductId === product.id) {
      selectedSubtitleReviewProductId = null;
      subtitleReviewDocument = null;
      subtitleReviewMessage = "";
      return;
    }
    selectedSubtitleReviewProductId = product.id;
    subtitleReviewDocument = null;
    subtitleReviewReviewedIndexes = [];
    subtitleReviewCorrections = {};
    subtitleReviewConfirmation = "";
    subtitleReviewReviewer = "";
    subtitleReviewReviewerId = "";
    subtitleReviewReviewerRole = "subtitle_editor";
    subtitleReviewRationale = "";
    subtitleReviewEvidenceRefs = "";    subtitleReviewMessage = "";
    subtitleReviewBusy = true;
    try {
      const response = await client.subtitleReview(product.initiative_id, product.version_id);
      subtitleReviewDocument = response.review;
      subtitleReviewCorrections = Object.fromEntries(
        response.review.segments.map((segment) => [segment.index, segment.asr_text]),
      );
    } catch (error) {
      subtitleReviewMessage = error instanceof Error ? error.message : "字幕审核资料读取失败";
    } finally {
      subtitleReviewBusy = false;
    }
  }

  async function approveSubtitleReview(product: ProductRecord): Promise<void> {
    if (!client || !subtitleReviewDocument || subtitleReviewSubmitting) return;
    if (subtitleReviewReviewedIndexes.length !== subtitleReviewDocument.segments.length) {
      subtitleReviewMessage = "请先逐条听审并勾选全部字幕。";
      return;
    }
    if (subtitleReviewConfirmation !== "APPROVE-HUMAN-REVIEW") {
      subtitleReviewMessage = "请明确输入批准口令，避免误触发布审核。";
      return;
    }
    if (!subtitleReviewReviewer.trim() || !subtitleReviewReviewerId.trim() || !subtitleReviewReviewerRole.trim()
        || !subtitleReviewRationale.trim() || !subtitleReviewEvidenceRefs.trim()) {
      subtitleReviewMessage = "请填写审核者身份、角色、审批理由和证据引用。";
      return;
    }
    const corrections = subtitleReviewDocument.segments.flatMap((segment) => {
      const value = (subtitleReviewCorrections[segment.index] ?? segment.asr_text).trim();
      return value && value !== segment.asr_text
        ? [{ segment_index: segment.index, corrected_text: value }]
        : [];
    });
    subtitleReviewSubmitting = true;
    subtitleReviewMessage = "正在验证人工审核记录并生成独立字幕版本…";
    try {
      const result = await client.approveSubtitleReview(product.initiative_id, product.version_id, {
        reviewed_segment_indexes: subtitleReviewReviewedIndexes,
        corrections,
        reviewer: subtitleReviewReviewer.trim(),
        reviewer_id: subtitleReviewReviewerId.trim(),
        reviewer_role: subtitleReviewReviewerRole.trim(),
        rationale: subtitleReviewRationale.trim(),
        evidence_refs: subtitleReviewEvidenceRefs.split(/\r?\n/).map((value) => value.trim()).filter(Boolean),
        confirmation: subtitleReviewConfirmation,
      });
      subtitleReviewMessage = result.product.validated
        ? "已生成新版本 " + result.product.version_id + "；原版本保持不变，作品仍处于 " + result.product.status + "，不会自动发布。"
        : "字幕修订已提交，但新版本尚未通过完整验证。";
      selectedSubtitleReviewProductId = null;
      subtitleReviewDocument = null;
      await refresh();
    } catch (error) {
      subtitleReviewMessage = error instanceof Error ? error.message : "字幕审核封装失败";
    } finally {
      subtitleReviewSubmitting = false;
    }
  }

  async function waitForVideoJob(jobId: string, aiMode: boolean): Promise<void> {
    if (!client) throw new Error("Muse API 尚未连接");
    for (let attempt = 0; attempt < 1800; attempt += 1) {
      await new Promise<void>((resolve) => window.setTimeout(resolve, 2000));
      const response = await client.videoJob(jobId);
      if (response.job.worker_available === false && (response.job.status === "queued" || response.job.status === "running")) {
        videoMessage = response.job.worker_mode === "commander_background"
          ? "Commander 执行票已持久化，但后台 Worker 当前不可用；任务记录保留，未伪报生成中。"
          : "任务账本中没有可恢复的执行 Worker；记录已保留，请检查运行时状态。";
        await refresh();
        return;
      }
      if (response.job.status === "succeeded") {
        videoMessage = aiMode
          ? "Wan 镜头、粤语配音、Remotion 动效包装及 MP4 技术 QA 已完成；成品已保存到产品库，仍需人工审片后发布。"
          : "本地 CantoTTS 粤语配音、Whisper ASR 字幕校验、Remotion 动效与 FFmpeg MP4 QA 已完成；成品已保存到产品库。";
        videoTitle = "";
        if (aiMode) {
          videoPrompt = "";
          videoNarrationScript = "";
        }
        await refresh();
        return;
      }
      if (response.job.status === "failed" || response.job.status === "cancelled") {
        throw new Error(response.job.error || (response.job.status === "cancelled" ? "视频任务已取消" : "视频制作失败"));
      }
      videoMessage = response.job.status === "queued"
        ? "执行 Ticket 已排队，等待 Commander 视频 Worker 与 GPU 租约…"
        : response.job.status.includes("review")
          ? "视频生成已进入独立审查阶段；等待签署与复核，不会自动标记为可发布。"
          : aiMode
            ? "Wan 2.1 正在本机生成镜头，随后执行粤语 TTS、Remotion 动效包装与 QA…"
            : "FFmpeg 正在剪辑，随后自动进行 Remotion 动效合成与 QA…";
    }
    throw new Error("视频任务超过 60 分钟仍未完成；请检查 Commander、GPU 租约和本地运行日志。");
  }

  async function createVideoProduct(): Promise<void> {
    const aiMode = resolvedVideoMode === "prompt_to_video";
    if (!client || videoBusy || !videoTitle.trim()) return;
    if (aiMode ? !videoPrompt.trim() : !videoSourceKey.trim()) return;
    videoBusy = true;
    videoMessage = aiMode
      ? "正在生成严格 Commander 规划并创建一次性审批票；批准前不会启动 Wan 或占用 GPU…"
      : "正在提交本地素材剪辑 + Remotion 动效合成任务…";
    try {
      if (aiMode) {
        const created = await client.createVideoApprovalRequest({
          request_id: crypto.randomUUID().replace(/-/g, ""),
          title: videoTitle.trim(),
          prompt: videoPrompt.trim(),
          negative_prompt: videoNegativePrompt.trim(),
          aspect_ratio: videoAspectRatio,
          duration_seconds: Number(videoDuration),
          steps: 20,
          narration_script: videoNarrationScript.trim(),
        });
        videoMessage = `Commander 一次性审批票已创建：${created.approval.task_id.slice(0, 8)}。当前状态：${created.approval.status}。批准前不会启动 Wan / GPU。`;
        await refresh();
        return;
      }
      const created = await client.createVideoProduct({
        mode: "local_assets" as const,
        source: videoSourceKey.trim(),
        title: videoTitle.trim(),
        audio_mode: videoAudioMode,
        narration_script: videoNarrationScript.trim(),
        motion_profile: videoMotionProfile,
        ...(videoAudioName.trim() ? { audio: videoAudioName.trim() } : {}),
      });
      await waitForVideoJob(created.job.id, false);
    } catch (error) {
      videoMessage = error instanceof Error ? error.message : "视频制作失败";
    } finally {
      videoBusy = false;
    }
  }

  async function approveVideoApprovalRequest(taskId: string): Promise<void> {
    if (!client || videoBusy || !/^[0-9a-f]{32}$/.test(taskId)) return;
    const confirmed = window.confirm(
      "你正在批准这一个指定视频尝试。批准后 Commander 可在 GPU 租约与 Admission 通过时启动 Wan 镜头生成。继续？",
    );
    if (!confirmed) return;
    videoBusy = true;
    videoMessage = "正在持久化这一次人工审批，并创建唯一 video_pipeline 执行 Ticket…";
    try {
      const approved = await client.approveVideoApprovalRequest(taskId);
      videoMessage = `人工审批已持久化；执行 Ticket ${approved.execution_ticket_id.slice(0, 8)} 已排队。GPU 尚未启动，需等待 Commander Admission 与租约。`;
      await refresh();
      if (approved.attempt_id) await waitForVideoJob(approved.attempt_id, true);
    } catch (error) {
      videoMessage = error instanceof Error ? error.message : "视频审批或派发失败";
      await refresh();
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
    let disposed = false;
    const localConfig = (window as Window & { __MUSE_LOCAL_CONNECTION__?: { pairingCode?: string | null; lanUrls?: string[] } }).__MUSE_LOCAL_CONNECTION__;
    pairingCode = localConfig?.pairingCode ?? "";
    lanUrls = Array.isArray(localConfig?.lanUrls) ? localConfig.lanUrls : [];

    eventStreamController = new AbortController();
    window.addEventListener("keydown", handleGlobalKeydown);
    tickClock();
    const clockHandle = window.setInterval(tickClock, 1000);
    void (async () => {
      const invokeMobile = (window as Window & {
        __TAURI__?: { core?: { invoke?: (command: string) => Promise<unknown> } };
      }).__TAURI__?.core?.invoke;
      mobileCompanion = invokeMobile
        ? await invokeMobile("is_mobile_companion").then((value) => value === true).catch(() => false)
        : false;
      if (disposed) return;
      if (mobileCompanion) {
        setView("companion");
        setConnection(false, false, "Muse Companion 尚未配对。请使用 HTTPS Host 地址发起设备配对。");
        return;
      }
      void refresh();
      if (eventStreamController) void runEventStream(eventStreamController.signal);
      pollHandle = window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
    })();
    return () => {
      disposed = true;
      window.removeEventListener("keydown", handleGlobalKeydown);
      window.clearInterval(clockHandle);
      stopPolling();
      if (pipelinePollHandle) clearInterval(pipelinePollHandle);
      eventStreamController?.abort();
      eventStreamController = null;
    };
  });
</script>
<svelte:head>
  <title>MUSE · 璇玑终端 · 本地优先 Super Agent 指挥中枢</title>
  <meta name="description" content="Muse 璇玑终端：唯一 Commander 调度阵、任务队列、知识图谱、记忆库与创作工坊" />
</svelte:head>

<div class="app">
<header class="titlebar">
  <div class="tb-brand">
    <svg class="tb-mark" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true">
      <circle cx="12" cy="12" r="9.2" opacity="0.34" />
      <circle cx="12" cy="12" r="5.6" opacity="0.6" />
      <path d="M12 2.8v18.4M2.8 12h18.4" opacity="0.22" />
      <path d="M12 6.6c2.4 0 4.3 2.4 4.3 5.4S14.4 17.4 12 17.4 7.7 15 7.7 12s1.9-5.4 4.3-5.4z" />
    </svg>
    <div class="tb-word">
      <b>MUSE</b>
      <span>COMMAND CENTER</span>
    </div>
  </div>

  <div class="tb-mid">
    <span class={`chip ${connected && !stale ? "is-live" : stale ? "is-stale" : "is-down"}`}>
      <i class="dot" aria-hidden="true"></i>
      {mobileCompanion ? (connected ? "Muse Host 已连接" : "Companion 未配对") : connected && !stale ? "本地核心在线" : stale ? "数据已过期" : "本地核心未连接"}
    </span>
    <span class="chip is-quiet">{updatedLabel}</span>
    {#if gpuResourceStatus?.status === "reserved"}
      <span class="chip is-stale">GPU 占用 · {gpuResourceStatus.lease?.owner_kind ?? "任务"}</span>
    {:else if gpuResourceStatus?.status === "idle"}
      <span class="chip is-quiet">GPU 空闲</span>
    {/if}
    <span class="chip is-quiet">并发 {prometheusCore ? `${prometheusCore.resource_governor.recommended_workers}/${prometheusCore.resource_governor.maximum_workers}` : "—"}</span>
  </div>

  <div class="tb-right">
    <div class="tb-clock" aria-live="off">
      <b>{clockLabel || "--:--:--"}</b>
      <span>{dateLabel || "本地时间"}</span>
    </div>
    <div class="tb-tools">
      <button class="btn is-sm is-ghost" type="button" onclick={openCommandPalette} aria-keyshortcuts="Control+K Meta+K" title="快速跳转 Ctrl K">
        <Icon name="search" size={13} /><span>跳转</span>
      </button>
      <button class="btn is-sm is-ghost" type="button" onclick={openGuide} title="使用指南">指南</button>
      <button class="btn is-sm" type="button" onclick={() => (reducedFx = !reducedFx)} title="切换动效强度">
        {reducedFx ? "静态" : "动效"}
      </button>
      <button class="btn is-sm" type="button" onclick={refresh} disabled={refreshBusy}>
        <Icon name="refresh" size={13} />{refreshBusy ? "刷新中" : "刷新"}
      </button>
    </div>
    <WindowChrome title="MUSE · 璇玑终端" />
  </div>
</header>

{#if guideOpen}
  <div class="guide-backdrop" role="presentation" onclick={(event) => { if (event.target === event.currentTarget) closeGuide(); }}>
    <dialog open class="guide-dialog" aria-labelledby="guide-title" aria-describedby="guide-purpose">
      <header class="guide-header">
        <div><small>MUSE FIELD MANUAL / 操作手册</small><h2 id="guide-title">一步一步认识 Muse 赛博玄枢</h2><p>每一页都告诉你这个模块做什么、怎么操作，以及当前真实能力边界。</p></div>
        <button type="button" class="command-close" onclick={closeGuide} aria-label="关闭使用指南">关闭</button>
      </header>
      <svg class="svg-bar-guide" viewBox="0 0 100 5" preserveAspectRatio="none" aria-label="指南进度">
        <rect class="svg-bar-guide-fill" x="0" y="0" width={guideProgress.percent} height="5" />
      </svg>
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
      <input bind:this={commandInput} bind:value={commandQuery} class="command-search" type="search" placeholder="搜索星环、八卦、星图、Agent、普罗米修斯、视频、任务…" aria-label="搜索功能" />
      <div class="command-results" role="listbox" aria-label="可跳转功能">
        {#each filteredCommands as command (command.id)}
          <a
            class="command-result"
            href={command.target}
            role="option"
            aria-selected="false"
            onclick={(e) => { e.preventDefault(); closeCommandPalette(); jumpToAnchor(command.target); }}
          >
            <span class="command-result-glyph">◇</span>
            <span><b>{command.label}</b><small>{command.description}</small></span>
            <kbd>↵</kbd>
          </a>
        {:else}
          <p class="command-empty">没有找到匹配功能。试试「星环」「八卦」「星图」「视频」或「任务」。</p>
        {/each}
      </div>
      <footer><span><kbd>Ctrl K</kbd> 或 <kbd>/</kbd> 打开</span><span><kbd>Esc</kbd> 关闭</span></footer>
    </div>
  </div>{/if}

<div class="body">
<nav class="rail" aria-label="主导航">
  {#each visibleViews as item (item.key)}
    <button
      class="rail-item"
      type="button"
      aria-current={view === item.key ? "page" : undefined}
      title={item.hint}
      onclick={() => setView(item.key)}
    >
      <Icon name={item.icon} size={19} />
      <span>{item.label}</span>
      {#if item.key === "tasks" && pendingAttention > 0}<i aria-hidden="true"></i>{/if}
      {#if item.key === "evolve" && selfImprovementStatus && selfImprovementStatus.pending_human_review > 0}<i aria-hidden="true"></i>{/if}
      {#if item.key === "reviews" && contextualReviewTickets.length > 0}<i aria-hidden="true"></i>{/if}
    </button>
  {/each}
  <div class="rail-sep" aria-hidden="true"></div>
  <!-- B398 Omega direct entries (static pages, external JS/CSS, CSP-safe) -->
  <a class="rail-item" href="./review-desk.html" title="待签名审查台"><Icon name="shield" size={19} /><span>审查台</span></a>
  <a class="rail-item" href="./unit-economics.html" title="单位经济模拟器 · CSV/Excel/PDF 报价识别"><Icon name="tasks" size={19} /><span>经济</span></a>
  <a class="rail-item" href="./consciousness-map.html" title="十维意识星图"><Icon name="core" size={19} /><span>星图</span></a>
  <a class="rail-item" href="./xuanshu-lab.html" title="玄枢灵感工坊（仅作灵感）"><Icon name="knowledge" size={19} /><span>玄枢</span></a>
  <a class="rail-item" href="./evolution-lab.html" title="自我进化实验室 · 熔断与回滚"><Icon name="flame" size={19} /><span>进化</span></a>
  <a class="rail-item" href="./research-bridge.html" title="深度研究 · 创作反馈回传"><Icon name="search" size={19} /><span>研究</span></a>
  <a class="rail-item" href="./video-factory.html" title="一键电影级视频工坊"><Icon name="studio" size={19} /><span>出片</span></a>
  <div class="rail-foot">
    <b>璇</b>
    <small>v0.3<br />LOCAL</small>
  </div>
</nav>

<main class="stage" id="overview">
  <div class="stage-inner">
    {#if message}
      <div class={`notice ${connected && !stale ? "is-ok" : "is-bad"}`} role="status">
        <Icon name={connected && !stale ? "shield" : "leave"} size={14} />
        <span>{message}</span>
      </div>
    {/if}

    {#if view === "overview"}
      <header class="view-head">
        <div>
          <h1>调度<em>阵</em></h1>
          <p>唯一 Commander 统御八个专家角色；环形节点由本地专家档案真实驱动，连线只在适配器实连时点亮。所有数字来自本机 API，断链会如实标记为过期。</p>
        </div>
        <div class="view-head-meta">
          <span class={`chip ${connected && !stale ? "is-live" : stale ? "is-stale" : "is-down"}`}>
            <i class="dot" aria-hidden="true"></i>{connectionLabel}
          </span>
          <span class="chip is-quiet">{agents.length} 实连 Agent</span>
        </div>
      </header>

      <section class="card">
        <div class="card-h">
          <div>
            <h2>Muse <em>调度中枢</em></h2>
            <p>点选任一角色节点，右侧遥测会切换为该专家的信任等级、历史运行与实连状态。</p>
          </div>
          <div class="card-tools">
            <button class="btn is-sm" type="button" onclick={() => setView("agents")}>角色目录</button>
            <button class="btn is-sm" type="button" onclick={() => setView("tasks")}>任务队列</button>
          </div>
        </div>
        <div class="overview-grid">
          <CommandOrbit
            galaxy={galaxySnapshot}
            queue={queueSnapshot}
            prometheus={prometheusCore}
            {connected}
            {stale}
            selected={selectedRole}
            onSelect={handleRoleSelect}
          />
          <aside class="overview-side">
            <div class="side-block">
              <div class="sect"><b>五阶火种账本</b><span>PROMETHEUS</span></div>
              <div class="ladder">
                {#each stageLadder as stage (stage.key)}
                  <div class="ladder-row">
                    <b>{stage.label}</b>
                    <span class="meter {stage.tone}">
                      <svg viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true">
                        <rect x="0" y="0" width={stage.pct} height="6" />
                      </svg>
                    </span>
                    <i>{stage.value}</i>
                  </div>
                {/each}
              </div>
            </div>
            <div class="side-block">
              <div class="sect"><b>角色详情</b><span>{selectedRole ? selectedRole.toUpperCase() : "未选中"}</span></div>
              {#if selectedRoleDetail}
                <div class="kv">
                  <div class="kv-row"><span>角色</span><b>{selectedRoleDetail.role}</b></div>
                  <div class="kv-row"><span>信任等级</span><b>{selectedRoleDetail.trust_tier}</b></div>
                  <div class="kv-row"><span>适配器</span><b class={selectedRoleDetail.live_adapter_connected ? "is-live" : "is-dim"}>{selectedRoleDetail.live_adapter_connected ? "实连在线" : "模板元数据"}</b></div>
                  <div class="kv-row"><span>历史运行</span><b>{selectedRoleDetail.historical_runs.runs_total}</b></div>
                  <div class="kv-row"><span>成功 / 失败</span><b>{selectedRoleDetail.historical_runs.runs_succeeded} / {selectedRoleDetail.historical_runs.runs_failed}</b></div>
                </div>
                <p class="side-copy">{selectedRoleDetail.description}</p>
              {:else}
                <p class="side-copy">尚未选择角色。点击环形节点查看该专家的真实元数据；模板角色不会被描述为在线进程。</p>
              {/if}
            </div>
            <div class="side-block">
              <div class="sect"><b>核心口径</b><span>GUARDRAILS</span></div>
              <div class="kv">
                <div class="kv-row"><span>调度权威</span><b>{galaxySnapshot?.commander?.sole_authority ? "唯一 Commander" : "未读取"}</b></div>
                <div class="kv-row"><span>竞态调度器</span><b class={galaxySnapshot?.commander?.competing_schedulers ? "is-warn" : "is-live"}>{galaxySnapshot?.commander?.competing_schedulers ?? "—"}</b></div>
                <div class="kv-row"><span>显存策略</span><b class="is-dim">{gpuResourceStatus?.status === "reserved" ? "已占租约" : "未知不超卖"}</b></div>
                <div class="kv-row"><span>自动改码</span><b class={selfImprovementStatus?.automatic_code_changes ? "is-warn" : "is-live"}>{selfImprovementStatus?.automatic_code_changes ? "已启用" : "关闭"}</b></div>
              </div>
            </div>
          </aside>
        </div>
      </section>

      <section class="stats" aria-label="实时指标">
        <div class="stat">
          <small>已登记智能体</small>
          <b>{agents.length}</b>
          <em>Agent Registry 实连记录</em>
        </div>
        <div class="stat">
          <small>在线 / 执行中</small>
          <b class="is-jade">{activeCount}</b>
          <em>按最近心跳推导</em>
        </div>
        <div class="stat">
          <small>任务成功率</small>
          <b>{percent(successRate)}</b>
          <em>仅统计真实完成样本</em>
        </div>
        <div class="stat">
          <small>审查通过率</small>
          <b>{percent(reviewRate)}</b>
          <em>仅统计 Reviewer 样本</em>
        </div>
        <div class="stat">
          <small>排队 / 待批</small>
          <b class="is-gold">{queueSnapshot?.counts.queued ?? 0}<span class="stat-sep">/</span>{queueSnapshot?.counts.waiting_approval ?? 0}</b>
          <em>Commander 队列实时</em>
        </div>
        <div class="stat">
          <small>已交付产品</small>
          <b>{products.length}</b>
          <em>需人工验收后发布</em>
        </div>
      </section>

      <section class="card" id="mempalace">
        <MemPalacePanel status={mempalaceStatus} {connected} />
      </section>

      <div class="split">
        <section class="card" id="events">
          <div class="card-h">
            <div>
              <h2>星轨<em>事件流</em></h2>
              <p>SSE 实时推送；断线后自动重连并显示最后一次成功刷新时间。</p>
            </div>
            <span class="chip is-quiet">{events.length} 条</span>
          </div>
          {#if events.length === 0}
            <div class="empty"><b>暂无事件</b><span>后端产生进度事件后会实时出现在这里。</span></div>
          {:else}
            <ol class="rows">
              {#each events.slice(0, 12) as event, index (event.id ?? index)}
                <li class="row event-row">
                  <span>
                    <b>{eventTitle(event)}</b>
                    <small>{eventSource(event) || "本地运行时"}</small>
                  </span>
                  <span class="row-meta">{eventTime(event)}</span>
                </li>
              {/each}
            </ol>
          {/if}
        </section>

        <section class="card" id="prometheus-core">
          <div class="card-h">
            <div>
              <h2>数据与<em>权限</em></h2>
              <p>界面只通过回环受限 API 读取状态与提交受控指令，从不直连 SQLite，也不会自动批准提案。</p>
            </div>
            <span class="chip is-quiet">本机受限</span>
          </div>
          <div class="kv">
            <div class="kv-row"><span>连接</span><b>127.0.0.1 · Bearer 会话</b></div>
            <div class="kv-row"><span>事件通道</span><b>SSE / Text-Event-Stream</b></div>
            <div class="kv-row"><span>写操作</span><b class="is-dim">灵感、反馈、审片、运行控制</b></div>
            <div class="kv-row"><span>禁止</span><b class="is-dim">自动改码 / 自动发布</b></div>
          </div>
          {#if pairingCode && lanUrls.length}
            <div class="notice is-spaced">
              <Icon name="lock" size={14} />
              <span>局域网配对已开启：{lanUrls.join(" · ")} · 配对码 {pairingCode}（12 小时有效）</span>
            </div>
          {/if}
          <p class="side-copy">玄学推演（八卦、星宿、干支）仅作为结构隐喻与假设生成工具，明确标注 symbolic_hypothesis_only，不会写入 User Track 记忆，也不作为事实证据。</p>
        </section>
      </div>
    {/if}

    {#if view === "studio"}
      <header class="view-head">
        <div>
          <h1>创作<em>台</em></h1>
          <p>从灵感评分到影像成片，再回到审片反馈。评分是排序启发，成片必须人工验收；模型推理与合成全部在本机完成。</p>
        </div>
        <div class="view-head-meta">
          <span class="chip is-quiet">本地渲染</span>
          <span class="chip is-quiet">{products.length} 件成果</span>
        </div>
      </header>

      <CreativeStudio {client} {connected} />

      <!-- ───────────────── 价值创作台 ───────────────── -->
      <section class="value-studio" id="value-studio" aria-label="价值创作台">
        <div class="vs-head">
          <div class="vs-title">
            <span class="vs-glyph" aria-hidden="true">✦</span>
            <div>
              <div class="vs-kicker">IDEA → RESEARCH → FILM → FEEDBACK</div>
              <h2>价值创作台</h2>
              <p>把一闪而过的念头，变成可验证、可制作、可复盘的创作路径。</p>
            </div>
          </div>
          <div class="vs-engine" title="纯本地启发式评分，不联网、不预测收益">
            <span class="vs-engine-dot"></span>
            LOCAL HEURISTIC · V1
          </div>
        </div>

      <ol class="vs-flow" aria-label="创作闭环阶段">
        <li class:done={studioStage >= 1}><b>01</b><span>注入灵感</span></li>
        <li class:done={studioStage >= 2}><b>02</b><span>价值评估</span></li>
        <li class:done={studioStage >= 3}><b>03</b><span>研究入池</span></li>
        <li class:done={studioStage >= 4}><b>04</b><span>生成影片</span></li>
        <li class:done={studioStage >= 5}><b>05</b><span>审片回流</span></li>
      </ol>

      <div class="vs-grid">
        <div class="vs-input-card">
          <label for="studio-idea">创作题目 / 灵感</label>
          <textarea id="studio-idea" bind:value={studioIdea} rows="4" maxlength="2000"
            placeholder="例如：用 3 分钟数据动画拆解「新手做短视频最常见的 5 个误区」，附实测对比…"></textarea>
          <div class="vs-two-col">
            <label for="studio-audience">目标受众
              <input id="studio-audience" type="text" bind:value={studioAudience} maxlength="120" placeholder="例如：想做自媒体的新手" />
            </label>
            <label for="studio-goal">看完后能做什么
              <input id="studio-goal" type="text" bind:value={studioGoal} maxlength="120" placeholder="例如：避开 5 个坑并做出第一条片子" />
            </label>
          </div>
          <div class="vs-actions">
            <button type="button" class="vs-btn primary" onclick={evaluateStudioIdea}>
              {studioDirty && studioResult ? "重新评估" : "评估选题价值"}
            </button>
            <button type="button" class="vs-btn" onclick={sendStudioToInbox}
              disabled={!studioResult || studioDirty || studioBusy || !connected}>{studioBusy ? "写入中…" : "存入灵感池"}</button>
            <button type="button" class="vs-btn" onclick={useStudioAsVideo} disabled={!studioResult}>生成视频</button>
          </div>
          {#if studioMessage}<p class="vs-message" role="status">{studioMessage}</p>{/if}
          <p class="vs-disclaimer">评分维度来自项目宪章：需求 25% · 证据 20% · 原创 20% · 可视化 15% · 长尾 10% · 成本 10%。这是排序启发，不是流量或收益预测。</p>
        </div>

        <div class="vs-result-card">
          {#if studioResult && !studioDirty}
            <div class="vs-score-top">
              <div class="vs-tier tier-{studioResult.tier.toLowerCase()}">
                <strong>{studioResult.tier}</strong>
                <small>选题级别</small>
              </div>
              <div class="vs-score-num">
                <strong>{studioResult.total}</strong><span>/ 100</span>
                <small>启发式价值分 · {studioResult.label}</small>
              </div>
            </div>
            <div class="vs-bars" aria-label="维度评分">
              {#each studioResult.scores as dim}
                <div class="vs-bar-row" title={dim.hint}>
                  <span class="vs-bar-label">{dim.label}<small>{Math.round(dim.weight * 100)}%</small></span>
                  <span class="vs-bar-track">
                    <svg class="svg-bar" viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true">
                      <rect class="svg-bar-fill" x="0" y="0" width={Math.max(0, Math.min(100, (dim.score / 5) * 100))} height="6" />
                    </svg>
                  </span>
                  <b>{dim.score}</b>
                </div>
              {/each}
            </div>
            {#if studioResult.riskFlags.length}
              <div class="vs-risks">
                待核实：{#each studioResult.riskFlags as flag}<span>{flag.label}</span>{/each}
              </div>
            {/if}
            <div class="vs-reco">
              <div><small>建议制作路径</small><b>{studioResult.suggestedPath}</b></div>
              <div><small>候选标签</small><b>{studioResult.potentialTags.join(" · ")}</b></div>
              <div><small>适配平台假设</small><b>{studioResult.suggestedPlatforms.join(" · ")}</b></div>
              <p>{studioResult.monetizationNote}</p>
            </div>
          {:else}
            <div class="vs-empty">
              <span>◇</span>
              <b>等待你的第一个题目</b>
              <p>写下选题，Muse 会从六个维度给出结构化评估与制作建议。所有评分只在本机计算。</p>
            </div>
          {/if}
        </div>
      </div>
    </section>
    {/if}

    {#if view === "fusion"}
      <header class="view-head">
        <div>
          <h1>万象<em>超融合</em></h1>
          <p>提交一个源码地址或 ZIP。Muse 会拆解项目、识别能力，并寻找与现有技能和工作流的连接点。</p>
        </div>
        <div class="view-head-meta"><span class="chip is-live">本地分析 · 不执行外部代码</span></div>
      </header>

      <div class="fusion-grid">
        <section class="card fusion-source-card">
          <div class="card-h">
            <div><h2>一键<em>接入源代码</em></h2><p>支持 GitHub 仓库地址、GitHub/GitLab ZIP 和本地 ZIP。</p></div>
            <span class="chip is-quiet">单次提交</span>
          </div>
          <label class="fusion-field">
            <span>源码网址</span>
            <input class="fusion-input" type="url" bind:value={fusionSourceUrl} placeholder="https://github.com/owner/repository" disabled={fusionBusy || !!fusionFile} />
          </label>
          <div class="fusion-orbit"><span></span>或</div>
          <label class="fusion-field">
            <span>上传源码压缩包</span>
            <input class="fusion-input fusion-file" type="file" accept=".zip,application/zip,application/x-zip-compressed" onchange={selectFusionFile} disabled={fusionBusy || !!fusionSourceUrl.trim()} />
            <small>ZIP · 最大 8 MiB；压缩展开后最多 32 MiB、300 个文件</small>
          </label>
          {#if fusionFile}
            <div class="fusion-selected"><b>{fusionFile.name}</b><span>{(fusionFile.size / 1024 / 1024).toFixed(2)} MiB</span><button type="button" class="fusion-clear" onclick={() => { fusionFile = null; fusionMessage = ""; }}>移除</button></div>
          {/if}
          <button type="button" class="feedback-submit fusion-submit" onclick={submitFusion} disabled={!connected || fusionBusy || (!fusionSourceUrl.trim() && !fusionFile)}>
            {fusionBusy ? "正在融合分析…" : "启动一键超融合"}
          </button>
          {#if fusionMessage}<p class="fusion-message" role="status">{fusionMessage}</p>{/if}
          <p class="fusion-note">安全边界：只做静态读取，不安装依赖、不运行脚本、不启动未知服务。来源许可证及信任状态未确认前，不会激活外部代码。</p>
        </section>

        <section class="card fusion-result-card">
          <div class="card-h"><div><h2>能力<em>融合图谱</em></h2><p>显示新项目能为 Muse 带来什么，以及可以连接哪些现有能力。</p></div></div>
          {#if fusionResult}
            <div class="fusion-result-head">
              <span class="chip {fusionResult.status === "approved_unverified" ? "is-stale" : fusionResult.status === "rolled_back" ? "is-quiet" : "is-live"}">{fusionResult.status === "integrated_unverified" ? "已自动接入候选图" : fusionResult.status === "approved_unverified" ? "已审阅 · 未激活" : fusionResult.status === "rolled_back" ? "已回滚" : "无候选能力"}</span>
              <b>{fusionResult.source.label}</b>
              <small>SHA-256 · {fusionResult.source.sha256.slice(0, 20)}…</small>
            </div>
            <div class="fusion-metrics">
              <div><b>{fusionResult.analysis.file_count}</b><small>检查文件</small></div>
              <div><b>{fusionResult.analysis.capabilities.length}</b><small>候选能力</small></div>
              <div><b>{fusionResult.analysis.integration_edges.length}</b><small>连接建议</small></div>
            </div>
            <div class="fusion-capabilities">
              {#each fusionResult.analysis.capabilities as capability (capability.id)}
                <article class="fusion-capability">
                  <div><b>{capability.name}</b><span>{capability.domain}</span></div>
                  <p>{capability.description}</p>
                  <small>匹配信号：{capability.signals.join(" · ")}</small>
                </article>
              {:else}
                <div class="empty"><b>未识别到明确能力</b><span>项目已安全读取，但当前规则没有足够信号形成能力候选。</span></div>
              {/each}
            </div>
            {#if fusionResult.analysis.integration_edges.length}
              <div class="worker-heading"><b>跨技能连接建议</b><span>{fusionResult.analysis.integration_edges.length} 条</span></div>
              <div class="feedback-list">
                {#each fusionResult.analysis.integration_edges as edge, index (index)}
                  <div class="artifact-row"><span><b>{edge.from.replace("external:", "新能力 · ")}</b><small>连接到 {edge.to.replace("muse:", "Muse · ")}</small></span><span class="chip is-quiet">{edge.relationship === "overlap" ? "能力重叠" : "领域关联"}</span></div>
                {/each}
              </div>
            {:else}
              <p class="side-copy">暂未找到足够强的现有能力匹配；候选仍可进入人工审阅。</p>
            {/if}
            <div class="fusion-safety">
              <b>安全与验证状态</b>
              <span>源码已检查 · 未执行代码 · 未安装依赖 · 许可证：{fusionResult.analysis.license_status}</span>
            </div>
            {#if fusionResult.status === "integrated_unverified"}
              <p class="fusion-note">候选能力及跨技能关系已自动写入 Muse 能力图；仍为 unverified，不能被 Commander 调度或执行。</p>
              <button type="button" class="vs-btn" onclick={approveFusion} disabled={fusionBusy}>标记为已审阅</button>
              <button type="button" class="vs-btn" onclick={rollbackFusion} disabled={fusionBusy}>回滚本次融合</button>
            {:else if fusionResult.status === "approved_unverified"}
              <p class="fusion-note">已记录人工审阅，但代码和依赖仍未执行；通过独立测试及信任审查前，不能进入可调度状态。</p>
              <button type="button" class="vs-btn" onclick={rollbackFusion} disabled={fusionBusy}>回滚本次融合</button>
            {:else if fusionResult.status === "rolled_back"}
              <p class="fusion-note">本次融合已回滚；相关候选能力均已停用，原始源码压缩包和审计记录仍保留。</p>
            {:else}
              <p class="fusion-note">没有识别到可安全注册的候选能力；源码和分析报告仍保留供复核。</p>
            {/if}
          {:else}
            <div class="empty fusion-empty"><b>等待第一个融合项目</b><span>提交一个源码仓库或 ZIP，Muse 会生成能力清单和跨技能连接建议。</span></div>
          {/if}
        </section>
      </div>

      <section class="card fusion-history">
        <div class="card-h"><div><h2>融合<em>记录</em></h2><p>已分析的来源及其入库状态。</p></div><button type="button" class="vs-btn" onclick={loadFusionJobs}>刷新</button></div>
        {#if fusionJobs.length}
          <div class="feedback-list">
            {#each fusionJobs as job (job.fusion_id)}
              <button type="button" class="fusion-history-row" onclick={() => { fusionResult = job; fusionMessage = ""; }}>
                <span><b>{job.source.label}</b><small>{job.analysis.file_count} 个文件 · {job.analysis.capabilities.length} 项能力 · {job.fusion_id.slice(0, 8)}</small></span>
                <span class="chip is-quiet">{job.status === "integrated_unverified" ? "已自动入图" : job.status === "approved_unverified" ? "已审阅" : job.status === "rolled_back" ? "已回滚" : job.status === "analyzed_no_capabilities" ? "无候选" : "候选记录"}</span>
              </button>
            {/each}
          </div>
        {:else}
          <p class="side-copy">尚无融合记录。</p>
        {/if}
      </section>
    {/if}

    {#if view === "system"}
      <header class="view-head">
        <div>
          <h1>万象<em>中枢</em></h1>
          <p>把 Muse 本机运行状态、能力注册表、文件资产与外部 Runner 观察汇总到一个只读控制台。每项都标明数据来源和新鲜度。</p>
        </div>
        <div class="view-head-meta">
          <span class={"chip " + (systemDashboard?.runner.status === "fresh" ? "is-live" : systemDashboard?.runner.status === "stale" ? "is-stale" : "is-quiet")}>
            <i class="dot" aria-hidden="true"></i>
            {systemDashboard?.runner.status === "fresh" ? "Runner 快照新鲜" : systemDashboard?.runner.status === "stale" ? "Runner 快照过期" : "Runner 未同步"}
          </span>
          <span class="chip is-quiet">只读 · 本地状态 5 秒刷新</span>
        </div>
      </header>

      <section class="stats" aria-label="系统总览">
        <div class="stat"><small>已登记项目</small><b>{systemDashboard?.runner.project_count ?? "—"}</b><em>WebCodex 外部快照</em></div>
        <div class="stat"><small>Runner 活跃任务</small><b>{systemDashboard?.runner.active_job_count ?? "—"}</b><em>运行 {systemDashboard?.runner.running_job_count ?? "—"} · 排队 {systemDashboard?.runner.queued_job_count ?? "—"}</em></div>
        <div class="stat"><small>Runner Skills</small><b>{systemDashboard?.runner.skill_count ?? "—"}</b><em>{systemDashboard?.runner.invalid_skill_count ?? "—"} 项定义异常</em></div>
        <div class="stat"><small>本地 Agent</small><b>{agents.length}</b><em>Agent Registry</em></div>
        <div class="stat"><small>执行中任务</small><b class="is-jade">{queueSnapshot?.counts.running ?? "—"}</b><em>本地 Commander 队列</em></div>
        <div class="stat"><small>本地能力</small><b>{systemDashboard?.local_capabilities.count ?? "—"}</b><em>Muse Capability Registry</em></div>
        <div class="stat"><small>工作区文件</small><b>{systemDashboard?.files.count ?? "—"}</b><em>安全白名单目录</em></div>
      </section>

      <div class="cols-2">
        <section class="card">
          <div class="card-h">
            <div><h2>本地<em>文件清单</em></h2><p>只显示文档、脚本、源码与前端文件名和元数据，不读取文件内容。</p></div>
            <span class="chip is-quiet">{systemDashboard?.files.count ?? 0} 项</span>
          </div>
          {#if systemDashboard?.files.items.length}
            <div class="system-file-list">
              {#each systemDashboard.files.items.slice(0, 160) as file (file.path)}
                <div class="artifact-row">
                  <span><b>{file.path}</b><small>{(file.size_bytes / 1024).toFixed(1)} KB · {relativeTime(file.modified_at)}</small></span>
                </div>
              {/each}
            </div>
            {#if systemDashboard.files.truncated}<p class="side-copy">清单已按安全规则截断，完整计数 {systemDashboard.files.count} 项。</p>{/if}
          {:else}
            <div class="empty"><b>文件清单暂不可用</b><span>本地 API 尚未返回工作区文件信息。</span></div>
          {/if}
        </section>

        <section class="card">
          <div class="card-h"><div><h2>本机<em>运行态势</em></h2><p>来自 Muse 本地状态 API，不把已安装误判为正在运行。</p></div></div>
          <div class="kv">
            <div class="kv-row"><span>MemPalace</span><b>{mempalaceStatus?.enabled ? "已启用" : mempalaceStatus?.connection_state === "disabled" ? "已关闭" : mempalaceStatus?.connection_state ?? "未读取"}</b></div>
            <div class="kv-row"><span>记忆存储</span><b>{mempalaceStatus?.memory_store_state === "ready" ? "可用" : mempalaceStatus?.memory_store_state ?? "未读取"}</b></div>
            <div class="kv-row"><span>向量索引</span><b>{mempalaceStatus ? (mempalaceStatus.vector_index_present ? "已建立" : "未建立") : "未读取"}</b></div>
            <div class="kv-row"><span>记忆投影队列</span><b>{mempalaceStatus?.projection_outbox.status === "not_initialized" ? "未初始化" : mempalaceStatus?.projection_outbox.status ?? "未读取"} · 待处理 {mempalaceStatus?.projection_outbox.pending ?? "—"}</b></div>
            <div class="kv-row"><span>Agent / Worker</span><b>{agents.length} / {workers.length}</b></div>
            <div class="kv-row"><span>任务队列</span><b>排队 {queueSnapshot?.counts.queued ?? "—"} · 执行 {queueSnapshot?.counts.running ?? "—"}</b></div>
            <div class="kv-row"><span>自主运行时</span><b class={daemonStatus?.running ? "is-live" : "is-dim"}>{daemonStatus?.running ? "运行中" : daemonStatus?.state ?? "未读取"}</b></div>
            <div class="kv-row"><span>GPU 资源</span><b>{gpuResourceStatus?.status ?? "未读取"}</b></div>
            <div class="kv-row"><span>自我进化</span><b>{selfImprovementStatus?.status ?? "未读取"}</b></div>
          </div>
          <div class="worker-heading"><b>Windows 计划任务</b><span>{systemDashboard?.scheduled_tasks.status ?? "未读取"}</span></div>
          {#if systemDashboard?.scheduled_tasks.items.length}
            <div class="feedback-list">
              {#each systemDashboard.scheduled_tasks.items as task (task.name)}
                <div class="artifact-row">
                  <span><b>{task.name}</b><small>{task.state === "running" ? "正在运行" : task.state === "ready" ? "已注册 · 当前空闲" : task.state === "disabled" ? "已禁用" : task.state === "not_registered" ? "未注册" : "状态未知"}</small></span>
                  <span class="chip is-quiet">{task.state === "running" ? "运行中" : task.state === "ready" ? "就绪" : task.state === "disabled" ? "已禁用" : task.state === "not_registered" ? "未注册" : "未知"}</span>
                </div>
              {/each}
            </div>
          {:else}
            <p class="side-copy">{systemDashboard?.scheduled_tasks.note ?? "尚未读取计划任务状态。"}</p>
          {/if}
        </section>
      </div>

      <div class="cols-2">
        <section class="card">
          <div class="card-h"><div><h2>Runner <em>项目空间</em></h2><p>外部 WebCodex 控制面只读快照，不等同于 Muse 本地 Space。</p></div><span class="chip is-quiet">{systemDashboard?.runner.status === "fresh" ? "快照新鲜" : systemDashboard?.runner.status === "stale" ? "快照过期" : "未同步"}</span></div>
          {#if systemDashboard?.runner.projects.length}
            <div class="feedback-list">
              {#each systemDashboard.runner.projects as project (project.name)}
                <div class="artifact-row">
                  <span><b>{project.name}</b><small>{project.connected ? "Runner 已连接" : "Runner 未连接"}</small></span>
                  <span class={project.connected ? "is-live" : "is-dim"}>{project.connected ? "在线" : "离线"}</span>
                </div>
              {/each}
            </div>
            <p class="side-copy">快照时间：{systemDashboard.runner.captured_at ? relativeTime(systemDashboard.runner.captured_at) : "未知"}。超过 15 分钟会标记为过期。</p>
            <div class="worker-heading"><b>Runner 运行队列</b><span class={systemDashboard.runner.connected ? "is-live" : "is-dim"}>{systemDashboard.runner.connected ? "在线" : "未知"}</span></div>
            <div class="kv">
              <div class="kv-row"><span>并发上限</span><b>{systemDashboard.runner.concurrency_limit ?? "—"}</b></div>
              <div class="kv-row"><span>运行中 / 排队</span><b>{systemDashboard.runner.running_job_count ?? "—"} / {systemDashboard.runner.queued_job_count ?? "—"}</b></div>
            </div>
            {#if systemDashboard.runner.active_jobs.length}
              <div class="feedback-list">
                {#each systemDashboard.runner.active_jobs as job, index (`${job.kind}-${index}`)}
                  <div class="artifact-row"><span><b>{job.kind}</b><small>{job.project || "Runner"} · {job.elapsed_secs === null ? "运行时长未知" : `快照时已运行 ${Math.floor(job.elapsed_secs / 60)} 分 ${job.elapsed_secs % 60} 秒`}</small></span><span class="is-live">{job.status}</span></div>
                {/each}
              </div>
            {/if}
          {:else}
            <div class="empty"><b>尚无 Runner 快照</b><span>外部项目清单尚未同步到本地仪表盘。</span></div>
          {/if}
          <div class="worker-heading"><b>Muse Space</b><span>{systemDashboard?.spaces.status ?? "未读取"}</span></div>
          <p class="side-copy">{systemDashboard?.spaces.note ?? "Space 注册表状态未知。"}</p>
        </section>

        <section class="card">
          <div class="card-h"><div><h2>Runner <em>技能目录</em></h2><p>只展示技能名称，不在仪表盘执行技能内容。</p></div><span class="chip is-quiet">{systemDashboard?.runner.skill_count ?? "—"} 项</span></div>
          {#if systemDashboard?.runner.skills.length}
            <div class="system-skill-list">
              {#each systemDashboard.runner.skills as skill (skill.name)}
                <span class="chip is-quiet">{skill.name}</span>
              {/each}
            </div>
            {#if systemDashboard.runner.skill_sources.length}
              <div class="kv">
                {#each systemDashboard.runner.skill_sources as source (source.name)}
                  <div class="kv-row"><span>{source.name === "project" ? "Muse 项目内" : source.name === "configured_runner_roots" ? "Runner 配置目录" : source.name === "managed_runner_store" ? "Runner 托管库" : source.name}</span><b>{source.count} 项 · 异常 {source.invalid_count}</b></div>
                {/each}
              </div>
            {/if}
            {#if (systemDashboard.runner.invalid_skill_count ?? 0) > 0}
              <p class="side-copy is-warn">{systemDashboard.runner.invalid_skill_count} 项技能定义存在异常；当前目录摘要没有提供异常项名称，故不猜测是哪几项。</p>
            {/if}
          {:else}
            <div class="empty"><b>技能目录未同步</b><span>Runner 技能不在 Muse 本地 API 的直接读取范围内。</span></div>
          {/if}
        </section>
      </div>

      <div class="cols-2">
        <section class="card">
          <div class="card-h"><div><h2>Muse <em>本地能力注册表</em></h2><p>这是本机能力元数据，与 Runner Skills 分开统计。</p></div><span class="chip is-quiet">{systemDashboard?.local_capabilities.count ?? 0} 项</span></div>
          {#if systemDashboard?.local_capabilities.items.length}
            <div class="feedback-list">
              {#each systemDashboard.local_capabilities.items.slice(0, 24) as capability (capability.capability_id)}
                <div class="artifact-row">
                  <span><b>{capability.name ?? capability.capability_id}</b><small>{capability.domain ?? "能力"} · {capability.capability_id}</small></span>
                  <span class="chip is-quiet">{capability.status ?? "未知"}</span>
                </div>
              {/each}
            </div>
          {:else}
            <div class="empty"><b>能力注册表未配置或为空</b><span>不会用 Skill 数量替代本地能力数量。</span></div>
          {/if}
        </section>

        <section class="card">
          <div class="card-h"><div><h2>外部<em>服务连接</em></h2><p>连接状态来自外部快照或本地连接检测。</p></div></div>
          <div class="feedback-list">
            {#each systemDashboard?.integrations ?? [] as integration (integration.id)}
              <div class="artifact-row">
                <span><b>{integration.label}</b><small>{integration.detail}</small></span>
                <span class={integration.status === "available" ? "is-live" : integration.status === "not_connected" ? "is-dim" : "is-warn"}>{integration.status === "snapshot_only" ? "快照同步" : integration.status === "available" ? "已连接" : integration.status === "empty" ? "暂无登记" : integration.status === "not_connected" ? "未连接" : "未桥接"}</span>
              </div>
            {/each}
          </div>
          <p class="side-copy">MCP、插件与邮件没有接入时会明确显示未连接；不会把功能代码或配置示例算作在线服务。</p>
        </section>
      </div>
    {/if}

    {#if view === "companion"}
      <header class="view-head">
        <div>
          <h1>跨端<em>设备安全</em></h1>
          <p>Host 本机设备信任中心。配对审批与凭据撤销留在 Muse Host；移动端不会获得桌面 Bearer，也不会绕过唯一 Commander。</p>
        </div>
        <div class="view-head-meta"><span class="chip is-quiet">{mobileCompanion ? "HTTPS · DEVICE HMAC · READ-ONLY" : "LOOPBACK · BEARER · READ-ONLY DEVICE SCOPE"}</span></div>
      </header>
      {#if mobileCompanion}
        <section class="card"><MobileCompanionPanel client={mobileCompanionClient} onClientChange={handleMobileClientChange} onConnectionChange={handleMobileConnectionChange} /></section>
      {:else}
        <section class="card"><CompanionPanel {client} connected={connected && !stale} /></section>
      {/if}
    {/if}

    {#if view === "runtime"}
      <header class="view-head">
        <div>
          <h1>运行<em>时</em></h1>
          <p>常驻监督循环与一键配方流水线。运行时使用确定性 allowlist 例行任务，不自动批准代码、不自动发布；生命周期操作全部写入本地审计日志。</p>
        </div>
        <div class="view-head-meta">
          <span class={`chip ${daemonStatus?.running ? "is-live" : daemonStatus?.state === "paused" ? "is-stale" : "is-quiet"}`}>
            <i class="dot" aria-hidden="true"></i>{daemonStatus?.running ? "运行时运行中" : daemonStatus?.state === "paused" ? "已暂停" : "未运行"}
          </span>
          <span class="chip is-quiet">配方 {pipelineRecipes.length}</span>
        </div>
      </header>

      <section class="ops-grid" id="daemon-runtime" aria-label="自主运行时">
      <section class="panel daemon-panel">
        <div class="panel-head">
          <div><small>AUTONOMOUS RUNTIME / 常驻引擎</small><h2>自主运行时</h2></div>
          <span class="tag" class:tag-live={daemonStatus?.running}>{daemonStatus?.running ? "运行中" : daemonStatus?.state === "paused" ? "已暂停" : "未运行"}</span>
        </div>
        <p class="panel-intro">本地常驻监督循环：定时检查健康、知识完整性、能力雷达、队列，并可在我空闲时做有来源的自主研究与视频派发。受本机 SQLite 单例租约约束，可随时暂停或停止。</p>
        <div class="daemon-status">
          <div><small>当前状态</small><strong>{daemonStatus?.state ?? "未知"}</strong></div>
          <div><small>心跳</small><strong>{daemonAgeLabel(daemonStatus?.heartbeat_age_seconds)} 前</strong></div>
          <div><small>托管进程</small><strong>{daemonStatus?.managed_pid ?? "—"}</strong></div>
          <div><small>最近错误</small><strong>{daemonStatus?.last_error ?? "无"}</strong></div>
        </div>
        <div class="daemon-actions">
          <button type="button" class="vs-btn primary" onclick={() => controlDaemon("start")} disabled={!connected || !!daemonBusy || daemonStatus?.running}>{daemonBusy === "start" ? "启动中…" : "启动 24h 运行时"}</button>
          <button type="button" class="vs-btn" onclick={() => controlDaemon("pause")} disabled={!connected || !!daemonBusy || !daemonStatus?.running}>{daemonBusy === "pause" ? "…" : "暂停"}</button>
          <button type="button" class="vs-btn" onclick={() => controlDaemon("resume")} disabled={!connected || !!daemonBusy || daemonStatus?.state !== "paused"}>{daemonBusy === "resume" ? "…" : "恢复"}</button>
          <button type="button" class="vs-btn" onclick={() => controlDaemon("stop")} disabled={!connected || !!daemonBusy || daemonStatus?.state === "stopped"}>{daemonBusy === "stop" ? "…" : "停止"}</button>
          <button type="button" class="vs-btn" onclick={() => controlDaemon("once")} disabled={!connected || !!daemonBusy || daemonStatus?.running}>{daemonBusy === "once" ? "执行中…" : "只跑一轮"}</button>
        </div>
        {#if daemonMessage}<p class="feedback-message" role="status">{daemonMessage}</p>{/if}
        <p class="vs-disclaimer">默认不开启 GPU 派发；模型推理需本地已配置。运行时使用确定性 allowlist 例行任务，不自动批准代码、不自动发布。所有生命周期操作写入 data/autonomy/control-log.jsonl 可审计。</p>
      </section>

      <section class="panel pipeline-panel" id="pipeline">
        <div class="panel-head">
          <div><small>ONE-CLICK PIPELINE / 创作流水线</small><h2>一键创作流水线</h2></div>
          <span class="tag">可复现配方</span>
        </div>
        <p class="panel-intro">选择一个经过验证的本地配方，Muse 会在后台运行并生成可下载交付包；进度实时刷新，产物仍需人工审片。</p>
        <div class="pipeline-form">
          <label for="pipeline-recipe">创作配方</label>
          <select id="pipeline-recipe" bind:value={selectedRecipe}>
            {#each pipelineRecipes as recipe (recipe.id)}
              <option value={recipe.id} disabled={!recipe.available}>{recipe.label}{recipe.available ? "" : "（暂不可用）"}</option>
            {:else}
              <option value="">加载中或不可用</option>
            {/each}
          </select>
          {#if pipelineRecipes.find((item) => item.id === selectedRecipe)}
            <small>{pipelineRecipes.find((item) => item.id === selectedRecipe)?.description ?? pipelineRecipes.find((item) => item.id === selectedRecipe)?.reason}</small>
          {/if}
          <div class="inspiration-actions">
            <small>单任务最长 30 分钟 · 输出位于 data/prometheus</small>
            <button type="button" class="vs-btn primary" onclick={submitPipeline} disabled={!connected || pipelineBusy || !selectedRecipe || pipelineRecipes.find((item) => item.id === selectedRecipe)?.available === false}>{pipelineBusy ? "提交中…" : "运行配方"}</button>          </div>
        </div>
        {#if pipelineMessage}<p class="feedback-message" role="status">{pipelineMessage}</p>{/if}
        <div class="worker-heading"><b>流水线任务</b><span>{pipelineJobs.length} 条</span></div>
        {#if pipelineJobs.length}
          <div class="feedback-list">
            {#each pipelineJobs.slice(0, 8) as job (job.job_id)}
              <div class="pipeline-job">
                <span class="job-status job-{job.status}">{job.status === "succeeded" ? "已交付" : job.status === "failed" ? "失败" : job.status === "running" ? "运行中" : "排队"}</span>
                <span><b>{job.recipe_label}</b><small>{job.progress} · {relativeTime(job.created_at)}</small>
                  {#if job.error}<small class="job-error">{job.error}</small>{/if}
                  {#if job.status === "succeeded" && job.result?.bundle}<small>交付包：{String((job.result.bundle as { path?: string })?.path ?? "已生成")}</small>{/if}
                </span>
              </div>
            {/each}
          </div>
        {:else}
          <div class="empty-state compact">尚无流水线任务。选择一个配方开始。</div>
        {/if}
      </section>
      </section>
    {/if}

    {#if view === "memory"}
      <header class="view-head">
        <div>
          <h1>记忆<em>库</em></h1>
          <p>User Track 与 Agent Track 严格隔离：界面只显示计数与健康状态，从不读取记忆正文。灵感默认仅存本地，只有显式勾选才会用于公开资料检索。</p>
        </div>
        <div class="view-head-meta">
          <span class="chip is-quiet">{inspirations.length} 条灵感</span>
          <span class="chip is-quiet">{artifacts.length} 份报告</span>
        </div>
      </header>

      <section class="card" aria-label="记忆殿堂">
        <MemPalacePanel status={mempalaceStatus} {connected} />
      </section>

    <section class="card inspiration-panel" id="inspirations">
      <div class="card-h">
        <div><h2>灵感<em>工坊</em></h2><p>写下想法、问题或故事种子；默认只保存在本机，不联网。</p></div>
        <span class="chip is-quiet">本地保存 · 可选联网</span>
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
                  <small>{relativeTime(item.created_at)} · {item.allow_external_research ? "已允许联网研究" : "仅本地保存"} · {item.status === "not_researched" ? "待研究" : item.status}{#if item.evolution_stage && item.evolution_stage !== "not_evolved"} · 闭环：{item.evolution_stage}{/if}{#if item.improvement_proposal_ids?.length} · 自我改进提案：{item.improvement_proposal_ids.length} 项{/if}</small>
                  {#if item.blueprint_path}
                    <small>分镜蓝图：{item.blueprint_path}</small>
                  {/if}
                  <div class="inspiration-row-actions">
                    <button type="button" class="vs-btn" onclick={() => evolveSingleInspiration(item.id, false)} disabled={!connected || Boolean(evolvingInspirationId)}>
                      {evolvingInspirationId === item.id ? "演化中…" : "自主深研 + 自我改进提案"}
                    </button>
                    <button type="button" class="vs-btn primary" onclick={() => evolveSingleInspiration(item.id, true)} disabled={!connected || Boolean(evolvingInspirationId)}>
                      {evolvingInspirationId === item.id ? "排队中…" : "进化并生成双画幅视频 (YouTube/抖音/视频号)"}
                    </button>
                  </div>
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
    </section>
    {/if}

    {#if view === "studio"}
      <section class="card product-library" id="products">
          <div class="card-h">
            <div><h2>影像<em>工坊</em> · 成果库</h2><p>本地素材剪辑 + Wan 2.1 文生视频，成片需人工审片后才能发布。</p></div>
            <span class="chip is-quiet">{products.length} 件</span>
          </div>
          <p class="artifact-note">{videoEngineSummary}</p>
          <p class="panel-intro">智能自动模式仅在本地 Commander 模型、ComfyUI / Wan 与 GPU 资源都就绪时选择 AI 镜头路径；否则切换到不依赖 llama.cpp / Wan 的本地素材动效路径（CantoTTS 粤语配音 + Whisper ASR + Remotion + FFmpeg）。本地路径需要素材文件夹和旁白稿；AI 镜头当前为 480p 预览级，所有成片仍需人工审片。</p>
          <div class="inspiration-form">
            <label for="video-mode">制作方式</label>
            <select id="video-mode" bind:value={videoMode}>
              <option value="auto">智能自动（AI / GPU 全就绪走 Commander；否则走本地动效）</option>
              <option value="prompt_to_video">AI 镜头生成（Wan 2.1 · Commander 审批）</option>
              <option value="local_assets">纯本地素材动效 + 粤语配音（Remotion / FFmpeg）</option>
            </select>
            <label for="video-title">影片名称</label>
            <input id="video-title" bind:value={videoTitle} maxlength="160" placeholder="给这部影片起个名字" />
            <small>自动模式当前选择：{resolvedVideoMode === "prompt_to_video" ? "AI 镜头路径（Commander 模型 + ComfyUI + GPU 空闲）" : "本地素材动效路径（CantoTTS + Whisper ASR + Remotion + FFmpeg）"}</small>
            {#if resolvedVideoMode === "prompt_to_video"}
              <label for="video-prompt">影片灵感 / 画面描述</label>
              <textarea id="video-prompt" bind:value={videoPrompt} rows="4" maxlength="2000" placeholder="例如：一只红狐狸穿过晨雾中的竹林，镜头缓慢推进，电影级自然光…"></textarea>
              <label for="video-negative-prompt">不希望出现的内容（可选）</label>
              <textarea id="video-negative-prompt" bind:value={videoNegativePrompt} rows="2" maxlength="1000" placeholder="例如：模糊、闪烁、畸形、字幕、水印"></textarea>
              <label for="video-narration-script">粤语旁白稿（可选，留空由本地生成）</label>
              <textarea id="video-narration-script" bind:value={videoNarrationScript} rows="3" maxlength="12000" placeholder="可粘贴旁白稿；系统会按粤语配音并进行 ASR 字幕校验。"></textarea>
              <label for="video-aspect-ratio">视频画幅</label>
              <select id="video-aspect-ratio" bind:value={videoAspectRatio}>
                <option value="16:9">横屏 16:9</option>
                <option value="9:16">竖屏 9:16</option>
              </select>
              <label for="video-duration">预览时长（秒）</label>
              <select id="video-duration" bind:value={videoDuration}>
                <option value="1">约 1 秒</option>
                <option value="2">约 2 秒</option>
                <option value="3">约 3 秒</option>
                <option value="4">约 4 秒</option>
                <option value="5">约 5 秒</option>
              </select>
              <small>Wan 1.3B 当前为 480p / 12 FPS 预览镜头；严格 Commander 审批后才会申请 GPU，完成后还要经过 Remotion、真实粤语 TTS / ASR 与技术 QA。</small>
            {:else}
              <label for="video-source-key">素材文件夹名</label>
              <input id="video-source-key" bind:value={videoSourceKey} maxlength="64" placeholder="例如 demo" />
              <label for="video-narration-script-local">粤语旁白稿（必填）</label>
              <textarea id="video-narration-script-local" bind:value={videoNarrationScript} rows="4" maxlength="12000" placeholder="写下这条视频的粤语旁白内容；Muse 会用本地 CantoTTS 合成，并用粤语 ASR 校验字幕。"></textarea>
              <label for="video-audio-name">背景音乐文件名（可选）</label>
              <input id="video-audio-name" bind:value={videoAudioName} maxlength="180" placeholder="例如 music.mp3" />
              <label for="video-audio-mode">音轨用途</label>
              <select id="video-audio-mode" bind:value={videoAudioMode}>
                <option value="music">背景音乐（循环铺底）</option>
                <option value="narration">旁白 / 人声（按原时长）</option>
              </select>
              <small>支持 PNG/JPG/WebP 与 MP4/MOV/MKV/WebM；最多 100 个镜头素材。素材放在 data/video_sources/素材文件夹名。留空旁白稿不会启动本地配音路径；同时添加音频文件时请将用途设为背景音乐。</small>
            {/if}
            <label for="video-motion-profile">Remotion 动效包装</label>
            <select id="video-motion-profile" bind:value={videoMotionProfile}>
              <option value="brand_intro_outro">动态片头 + 正片 + 动态片尾</option>
              {#if resolvedVideoMode !== "prompt_to_video"}
                <option value="brand_scene_transitions">逐镜头交叉淡化 / 擦除 + 动态片头片尾（推荐）</option>
              {/if}
              <option value="none">不添加动效</option>
            </select>
            <small>逐镜头模式会先按统一画幅/FPS规范化每段素材，再由 Remotion TransitionSeries 交替渲染淡化与擦除转场；原音轨按新时间轴保留。AI 单镜头模式使用片头片尾包装。</small>
            <div class="inspiration-actions">
              <small>{resolvedVideoMode === "prompt_to_video" ? "AI 路径先生成严格 Commander 审批票；批准前不会启动 Wan / GPU。手动选择 AI 时，服务未就绪会明确报错或保持执行票排队。" : "本地路径使用 CantoTTS 粤语配音、Whisper 粤语 ASR 字幕校验、Remotion 动效与 FFmpeg；不调用 ComfyUI / Wan。需要本地素材文件夹与旁白稿。"}</small>
              <button type="button" class="feedback-submit" onclick={createVideoProduct} disabled={!connected || videoBusy || !videoTitle.trim() || (resolvedVideoMode === "prompt_to_video" ? !videoPrompt.trim() : (!videoSourceKey.trim() || !videoNarrationScript.trim()))}>{videoBusy ? "正在处理…" : resolvedVideoMode === "prompt_to_video" ? "提交 Commander 审批" : "制作本地粤语配音 MP4"}</button>
            </div>          </div>
          {#if videoMessage}<p class="feedback-message" role="status">{videoMessage}</p>{/if}
          {#if videoApprovalRequests.length}
            <section class="video-attempt-summary" aria-label="Commander 视频审批">
              <b>Commander 视频审批 · 一次性授权</b>
              <small>只有你明确批准指定 Ticket 后，系统才会创建 video_pipeline 执行任务；GPU 仍需通过 Admission 与租约检查。</small>
              <div class="video-attempt-list">
                {#each videoApprovalRequests.slice(0, 8) as approval (approval.id)}
                  <div class="video-attempt-row">
                    <span>{approval.title || "AI 视频创作"} · {approval.status === "waiting_approval" ? "待人工批准" : approval.status === "queued" ? "已批准" : approval.status}</span>
                    <small>审批票 {approval.id.slice(0, 8)}{#if approval.approved_by} · 批准者 {approval.approved_by}{/if}{#if approval.attempt_id} · Attempt {approval.attempt_id.slice(0, 8)}{/if}</small>
                    {#if approval.can_approve}
                      <button type="button" class="feedback-submit" onclick={() => approveVideoApprovalRequest(approval.id)} disabled={!connected || videoBusy}>批准这一次 AI 镜头生成</button>
                    {:else if approval.execution_ticket_id}
                      <small>执行 Ticket {approval.execution_ticket_id.slice(0, 8)} · {approval.execution_status || "等待 Commander 调度"} · GPU 尚未启动</small>
                    {:else}
                      <small>审批状态已保存；尚未创建执行 Ticket。</small>
                    {/if}
                  </div>
                {/each}
              </div>
            </section>
          {/if}
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
                      <button type="button" class="radar-scan" onclick={() => toggleVideoRecipeTrace(attempt.attempt_id)} disabled={recipeTraceBusy} aria-expanded={selectedRecipeTraceAttemptId === attempt.attempt_id}>{selectedRecipeTraceAttemptId === attempt.attempt_id ? "收起配方追溯" : "查看配方追溯"}</button>
                      {#if selectedRecipeTraceAttemptId === attempt.attempt_id}
                        <section class="video-recipe-trace" aria-label="视频配方来源追溯">
                          {#if recipeTraceBusy}
                            <small>正在读取候选、审批、Attempt 与产品指纹…</small>
                          {:else if recipeTraceMessage}
                            <small role="status">{recipeTraceMessage}</small>
                          {:else if recipeTrace}
                            <div class="video-recipe-trace-grid">
                              <span><b>候选配方</b><small>{recipeTrace.candidate.candidate_id ?? "未关联"}</small></span>
                              <span><b>配方 SHA-256</b><small>{recipeTrace.candidate.recipe_sha256?.slice(0, 20) ?? "未记录"}</small></span>
                              <span><b>人工审批</b><small>{recipeTrace.approval.human_decision_verified ? "已核验" : "未核验"}{#if recipeTrace.approval.decision_actor} · {recipeTrace.approval.decision_actor}{/if}</small></span>
                              <span><b>审批任务绑定</b><small>{recipeTrace.approval.attempt_matches_ticket === true ? "当前 Attempt 一致" : recipeTrace.approval.attempt_matches_ticket === false ? "Attempt 不匹配" : "旧记录未提供"}</small></span>
                              <span><b>产品 Manifest</b><small>{recipeTrace.product.status}{#if recipeTrace.product.manifest_sha256} · {recipeTrace.product.manifest_sha256.slice(0, 20)}{/if}</small></span>
                              <span><b>整链一致性</b><small>{recipeTrace.chain_consistent ? "通过" : "待核验 / 不一致"}</small></span>
                              <span><b>发布权限</b><small>未授权 · {recipeTrace.release_review.status}</small></span>
                            </div>
                            <VideoReleaseReview review={recipeTrace.release_review} contactSheetUrl={contactSheetUrl} contactSheetMessage={contactSheetMessage} />
                            <small>只读追溯：不启动生成、不修改账本、不触发发布。发布仍须独立人工审核。</small>
                          {/if}
                        </section>
                      {/if}
                    </div>
                  {/each}
                </div>
              {/if}
            </section>
          {/if}
          {#if products.length}
            <div class="feedback-list">
              {#each products as product (product.id)}
                {@const recipeTraceLink = resolveProductAttempt(product, videoAttemptSummary?.attempts ?? [])}
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
                    {#if product.scene_timeline?.length}
                      <div class="quality-scene-matches" aria-label="分镜运镜与时间轴">
                        {#each product.scene_timeline as scene (scene.scene_id)}
                          <div class="quality-scene-match">
                            <span>
                              <b>{scene.scene_id} · {cameraMoveLabel(scene.camera_move)}</b>
                              <small>{scene.source_asset}{#if typeof scene.timeline_start_seconds === "number" && typeof scene.timeline_end_seconds === "number"} · {subtitleCueTime(scene.timeline_start_seconds)}–{subtitleCueTime(scene.timeline_end_seconds)} · {Number(scene.duration_seconds ?? (scene.timeline_end_seconds - scene.timeline_start_seconds)).toFixed(2)}s{/if}{#if typeof scene.transition_in_seconds === "number" && scene.transition_in_seconds > 0} · 入场转场 {scene.transition_in_seconds.toFixed(2)}s{/if}{#if typeof scene.transition_out_seconds === "number" && scene.transition_out_seconds > 0} · 出场转场 {scene.transition_out_seconds.toFixed(2)}s{/if}</small>
                            </span>
                            {#if typeof scene.timeline_start_seconds === "number"}
                              <button type="button" class="radar-scan" onclick={() => previewProduct(product, scene.timeline_start_seconds)}>预览此镜头</button>
                            {/if}
                          </div>
                        {/each}
                      </div>
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
                            <span><b>{qualityWarningLabel(warning.code)}</b><small>{#if typeof warning.start_seconds === "number" && typeof warning.duration_seconds === "number"}{warning.start_seconds.toFixed(2)}s 起 · 持续 {warning.duration_seconds.toFixed(2)}s{:else}{warning.message || "整段指标需要复核"}{#if typeof warning.value === "number"} · {warning.value.toFixed(1)} {warning.unit ?? ""}{/if}{/if}</small></span>
                            <button type="button" class="radar-scan" onclick={() => previewProduct(product, warning.start_seconds)}>定位复核</button>
                          </div>
                          {#if warning.storyboard_matches?.length}
                            <div class="quality-scene-matches" aria-label="关联分镜与源素材">
                              {#each warning.storyboard_matches as match (match.scene_id)}
                                <div class="quality-scene-match">
                                  <span>
                                    <b>{match.scene_id}</b>
                                    <small>{match.source_file} · 镜头 {subtitleCueTime(match.scene_start_seconds)}–{subtitleCueTime(match.scene_end_seconds)} · 告警重叠 {match.overlap_seconds.toFixed(2)}s</small>
                                  </span>
                                  <button type="button" class="radar-scan" onclick={() => previewProduct(product, match.overlap_start_seconds)}>定位此镜头</button>
                                </div>
                              {/each}
                            </div>
                          {:else if warning.mapping_status === "outside_storyboard"}
                            <small class="quality-mapping-note">此告警时间不在已记录的分镜区间内，保留原片时间定位。</small>
                          {/if}
                        {/each}
                        <small>这些是黑帧、静帧或静音的启发式提示；有意的暗场、定格与留白不自动判为缺陷，也不会单独阻断技术 QA。</small>
                      </div>
                    {/if}
                  </span>                <div class="product-actions">
                  <button type="button" class="radar-scan" onclick={() => toggleInitiativeTimeline(product)} disabled={timelineBusy} aria-expanded={selectedTimelineId === timelineInitiativeId(product)}>{selectedTimelineId === timelineInitiativeId(product) ? "收起时间线" : "创作时间线"}</button>
                  {#if product.kind === "video_creator" && recipeTraceLink.status === "matched" && recipeTraceLink.attemptId}
                    <button type="button" class="radar-scan" onclick={() => toggleVideoRecipeTrace(recipeTraceLink.attemptId!)} disabled={recipeTraceBusy} aria-expanded={selectedRecipeTraceAttemptId === recipeTraceLink.attemptId}>{selectedRecipeTraceAttemptId === recipeTraceLink.attemptId ? "收起配方追溯" : "配方追溯"}</button>
                  {:else if product.kind === "video_creator"}
                    <small class="quality-gate-line">{recipeTraceLink.status === "ambiguous" ? "存在多条匹配任务，已停止关联；需核对 Attempt 账本" : videoAttemptSummary?.attempts_truncated ? "最近 200 条账本中未找到匹配；更早记录尚未核验" : videoAttemptSummary ? "未找到对应的唯一视频任务；不猜测关联" : "视频任务账本暂不可用，配方追溯入口未启用"}</small>
                  {/if}
                  <button type="button" class="radar-scan" onclick={() => previewProduct(product)}>预览</button>
                  {#if product.kind === "video_creator"}
                    <button type="button" class="radar-scan" onclick={() => toggleProductVisualReview(product)} disabled={visualReviewBusy || visualReviewSubmitting} aria-expanded={selectedVisualReviewProductId === product.id}>
                      {selectedVisualReviewProductId === product.id ? "收起视觉审片" : "人工视觉审片"}
                    </button>
                  {/if}
                  {#if product.kind === "video_creator" && product.subtitle_review?.status === "pending_human_review"}
                    <button type="button" class="radar-scan" onclick={() => toggleSubtitleReview(product)} aria-expanded={selectedSubtitleReviewProductId === product.id}>逐条字幕审校</button>
                  {:else if product.kind === "video_creator" && product.subtitle_review?.status === "unavailable"}
                    <small class="quality-gate-line">字幕审校资料未能与此作品版本匹配，暂不可审校</small>
                  {:else if product.kind === "video_creator" && product.subtitle_review?.status === "ambiguous"}
                    <small class="quality-gate-line">字幕审校来源不唯一，需先核对来源记录</small>
                  {/if}
                  {#if product.kind === "video_creator" && product.video_path}
                    <button type="button" class="radar-scan" onclick={() => downloadVideo(product)}>下载 MP4</button>
                  {/if}
                  <button type="button" class="radar-scan" onclick={() => downloadProduct(product)}>下载工程 ZIP</button>
                </div>
              </div>
              {#if product.kind === "video_creator" && selectedVisualReviewProductId === product.id}
                <VideoVisualReview imageUrl={visualReviewSheetUrl} message={visualReviewMessage || (visualReviewBusy ? "正在读取联系表…" : "")} busy={visualReviewSubmitting} reviews={visualReviewRecords} revisionBrief={visualRevisionBrief} visualQuality={visualQualityReport} on:submit={(event) => submitProductVisualReview(product, event.detail)} />
                <div class="product-actions">
                  <button type="button" class="radar-scan" onclick={() => previewProduct(product)}>打开完整原片复核</button>
                  <button type="button" class="radar-scan" onclick={() => { if (selectedTimelineId !== timelineInitiativeId(product)) toggleInitiativeTimeline(product); }}>打开审片反馈</button>
                </div>
              {/if}
              {#if client}
                <InitiativePerformancePanel client={client} initiativeId={timelineInitiativeId(product)} />
              {/if}
              {#if product.kind === "video_creator" && recipeTraceLink.status === "matched" && recipeTraceLink.attemptId && selectedRecipeTraceAttemptId === recipeTraceLink.attemptId}
                <section class="video-recipe-trace" aria-label="产品配方来源追溯">
                  {#if recipeTraceBusy}
                    <small>正在读取候选、审批、Attempt 与产品指纹…</small>
                  {:else if recipeTraceMessage}
                    <small role="status">{recipeTraceMessage}</small>
                  {:else if recipeTrace}
                    <div class="video-recipe-trace-grid">
                      <span><b>候选配方</b><small>{recipeTrace.candidate.candidate_id ?? "未关联"}</small></span>
                      <span><b>配方 SHA-256</b><small>{recipeTrace.candidate.recipe_sha256?.slice(0, 20) ?? "未记录"}</small></span>
                      <span><b>人工审批</b><small>{recipeTrace.approval.human_decision_verified ? "已核验" : "未核验"}{#if recipeTrace.approval.decision_actor} · {recipeTrace.approval.decision_actor}{/if}</small></span>
                      <span><b>审批任务绑定</b><small>{recipeTrace.approval.attempt_matches_ticket === true ? "当前 Attempt 一致" : recipeTrace.approval.attempt_matches_ticket === false ? "Attempt 不匹配" : "旧记录未提供"}</small></span>
                      <span><b>产品 Manifest</b><small>{recipeTrace.product.status}{#if recipeTrace.product.manifest_sha256} · {recipeTrace.product.manifest_sha256.slice(0, 20)}{/if}</small></span>
                      <span><b>整链一致性</b><small>{recipeTrace.chain_consistent ? "通过" : "待核验 / 不一致"}</small></span>
                      <span><b>发布权限</b><small>未授权 · {recipeTrace.release_review.status}</small></span>
                    </div>
                    <VideoReleaseReview review={recipeTrace.release_review} contactSheetUrl={contactSheetUrl} contactSheetMessage={contactSheetMessage} />
                    <small>只读追溯：不启动生成、不修改账本、不触发发布。can_publish=false，发布仍须独立人工审核。</small>
                  {/if}
                </section>
              {/if}
              {#if selectedSubtitleReviewProductId === product.id}
                <section class="product-review-form subtitle-review-workbench" aria-label="粤语字幕逐条人工审校">
                  <b>粤语旁白 · 逐条字幕审校</b>                  <div class="product-actions">
                    <button type="button" class="radar-scan" onclick={() => previewProduct(product)}>打开原片听审</button>
                    <span>{subtitleReviewReviewedIndexes.length} / {subtitleReviewDocument?.segments.length ?? 0} 条已确认</span>
                  </div>
                  {#if subtitleReviewBusy}
                    <small>正在读取与本产品音频哈希绑定的字幕审核资料…</small>
                  {:else if subtitleReviewDocument}
                    {#each subtitleReviewDocument.segments as segment (segment.index)}
                      <div class="quality-warning-row subtitle-cue-row">
                        <label class="subtitle-cue-confirm">
                          <input
                            type="checkbox"
                            checked={subtitleReviewReviewedIndexes.includes(segment.index)}
                            onchange={(event) => markSubtitleCueReviewed(segment.index, (event.currentTarget as HTMLInputElement).checked)}
                          />
                          <span>已听审 #{segment.index} · {subtitleCueTime(segment.start_seconds)}–{subtitleCueTime(segment.end_seconds)}</span>
                        </label>
                        <small>ASR 原文：{segment.asr_text}</small>
                        <label>最终字幕（修改会另存为人工修订）
                          <textarea rows="2" maxlength="500" bind:value={subtitleReviewCorrections[segment.index]}></textarea>
                        </label>
                      </div>
                    {/each}
                    <label>审核人
                      <input maxlength="120" placeholder="填写实际审核人" bind:value={subtitleReviewReviewer} />
                    </label>
                    <label>审核者 ID（本地自报身份，未经过账号认证）
                      <input maxlength="120" placeholder="例如 local-editor-1" bind:value={subtitleReviewReviewerId} />
                    </label>
                    <label>审核角色
                      <input maxlength="80" placeholder="例如 subtitle_editor" bind:value={subtitleReviewReviewerRole} />
                    </label>
                    <label>审批理由
                      <textarea rows="2" maxlength="2000" placeholder="说明已如何核对全部字幕、旁白和修订" bind:value={subtitleReviewRationale}></textarea>
                    </label>
                    <label>证据引用（每行一条，填写本次实际核对过的材料）
                      <textarea rows="2" maxlength="2000" placeholder="例如 subtitle-review.json#segments" bind:value={subtitleReviewEvidenceRefs}></textarea>
                    </label>                    <label>明确批准口令
                      <input maxlength="40" placeholder="输入 APPROVE-HUMAN-REVIEW" bind:value={subtitleReviewConfirmation} />
                    </label>
                    <button type="button" class="feedback-submit" onclick={() => approveSubtitleReview(product)}
                      disabled={subtitleReviewSubmitting || subtitleReviewReviewedIndexes.length !== subtitleReviewDocument.segments.length || subtitleReviewConfirmation !== "APPROVE-HUMAN-REVIEW" || !subtitleReviewReviewer.trim() || !subtitleReviewReviewerId.trim() || !subtitleReviewReviewerRole.trim() || !subtitleReviewRationale.trim() || !subtitleReviewEvidenceRefs.trim()}>
                      {subtitleReviewSubmitting ? "正在生成新版本…" : "确认人工审校并生成新版本"}
                    </button>
                  {:else if subtitleReviewMessage}
                    <small role="status">{subtitleReviewMessage}</small>                  {/if}

                </section>
              {/if}
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
    {/if}

    {#if view === "overview"}
      <PrometheusCorePanel prometheus={prometheusCore} />
    {/if}

    {#if view === "knowledge"}
      <header class="view-head">
        <div>
          <h1>知识<em>图谱</em></h1>
          <p>Research Wiki 中的主张、来源、实验与失败构成可追溯星图；卦象与星宿作为结构透镜，只生成可证伪假设，不冒充事实证据。</p>
        </div>
        <div class="view-head-meta">
          <span class="chip is-quiet">{knowledgeGraph?.nodes.length ?? 0} 星体</span>
          <span class="chip is-quiet">{knowledgeGraph?.edges.length ?? 0} 连边</span>
        </div>
      </header>

      <KnowledgeStarMap
        {client}
        {connected}
        graph={knowledgeGraph}
        onQueryGraph={queryKnowledgeGraph}
      />

      <CelestialBaguaLab
        {client}
        {connected}
        celestial={celestialCatalog}
        {presetLowerBits}
        onSaveHypothesisInspiration={saveHypothesisInspiration}
      />
    {/if}

    {#if view === "agents"}
      <header class="view-head">
        <div>
          <h1>智能<em>体</em></h1>
          <p>唯一调度权威 Muse Commander 统御专家角色、角色安全策略与本机能力；模板元数据与实连进程在界面上严格区分。</p>
        </div>
        <div class="view-head-meta">
          <span class="chip is-quiet">{galaxySnapshot?.specialist_profiles.length ?? 0} 专家席</span>
          <span class={`chip ${registryConfigured ? "is-live" : "is-quiet"}`}>{registryConfigured ? "Registry 已连接" : "Registry 未配置"}</span>
        </div>
      </header>

    <AgentGalaxyPanel
      {client}
      {connected}
      {agents}
      {registryConfigured}
      orgChart={agentOrgChart}
      galaxy={galaxySnapshot}
    />
    {/if}

    {#if view === "tasks"}
      <header class="view-head">
        <div>
          <h1>任务<em>中心</em></h1>
          <p>只读监控 Commander 队列、工作流阶段与 Worker 心跳。撤销、重试与审批全部由后端策略裁决，界面不会绕过授权。</p>
        </div>
        <div class="view-head-meta">
          <span class="chip is-quiet">{queueSnapshot?.active.length ?? 0} 活跃任务</span>
          <span class="chip is-quiet">{workflowRuns.length} 工作流</span>
        </div>
      </header>

    <div class="cols-2">
      <section class="card" id="queue">
        <div class="card-h">
          <div><h2>任务<em>队列</em></h2><p>只读监控；筛选仅作用于当前已载入的任务。</p></div>
          <span class="chip is-quiet">只读</span>
        </div>
        {#if !queueSnapshot}
          <div class="empty"><b>队列数据不可用</b><span>本地 API 未返回任务快照。</span></div>
        {:else}
          <div class="stats is-spaced">
            <div class="stat"><small>排队中</small><b>{queueSnapshot.counts.queued}</b></div>
            <div class="stat"><small>执行中</small><b class="is-jade">{queueSnapshot.counts.running}</b></div>
            <div class="stat"><small>待审批</small><b class="is-gold">{queueSnapshot.counts.waiting_approval}</b></div>
            <div class="stat"><small>失败</small><b class="is-dim">{queueSnapshot.counts.failed}</b></div>
          </div>
          <div class="queue-tools" aria-label="任务筛选">
            <input id="queue-search" class="input" type="search" bind:value={queueQuery} placeholder="按任务名称或编号搜索…" aria-label="查找任务" />
            <select id="queue-status" class="select" bind:value={queueStatusFilter} aria-label="按状态筛选">
              <option value="all">全部状态</option>
              <option value="queued">排队中</option>
              <option value="running">执行中</option>
              <option value="waiting_approval">待审批</option>
              <option value="failed">失败</option>
            </select>
          </div>
          <p class="queue-note">显示 {filteredQueueTasks.length} / {queueSnapshot.active.length} 条已载入任务</p>
          {#if filteredQueueTasks.length}
            <div class="rows" aria-live="polite">
              {#each filteredQueueTasks as task (task.id)}
                <div class="task">
                  <span class={`state state-${task.status}`}>{task.status}</span>
                  <div class="task-title">
                    <b>{task.title}</b>
                    <small>{task.depends_on?.length ? `依赖 ${task.depends_on.length} 项 · ` : ""}尝试 {task.attempts} 次 · 更新于 {relativeTime(task.updated_at)}</small>
                  </div>
                  <span class="row-meta">{task.id.slice(0, 8)}</span>
                </div>
              {/each}
            </div>
          {:else if queueSnapshot.active.length}
            <div class="empty"><b>没有符合条件的任务</b><span>调整关键词或状态筛选。</span><button class="btn is-sm" type="button" onclick={() => { queueQuery = ""; queueStatusFilter = "all"; }}>清除筛选</button></div>
          {:else}
            <div class="empty"><b>队列已清空</b><span>当前没有排队、执行或等待审批的任务。</span></div>
          {/if}
          {#if queueSnapshot.active_truncated}
            <small class="queue-note">后端返回的活跃任务列表已截断；搜索与筛选仅作用于当前已载入的 {queueSnapshot.active.length} 条任务。</small>
          {/if}
        {/if}
        <div class="sect is-spaced"><b>Commander 工作流</b><span>{workflowRuns.length} 个活跃流程</span></div>
        {#if workflowRuns.length}
          <div class="rows">
            {#each workflowRuns as workflow (workflow.ticket_id)}
              <article class="wf">
                <div class="wf-head">
                  <b>{workflow.title}</b>
                  <span class={`state state-${workflow.status}`}>{workflow.status}</span>
                </div>
                <div class="wf-stage">
                  <strong>{workflow.stage}</strong>
                  <small>{workflow.profile_name ?? "team"} · v{workflow.profile_version} · 尝试 {workflow.attempt}</small>
                </div>
                {#if workflow.step_id}<small class="wf-note">当前 DAG 步骤：{workflow.step_id}</small>{/if}
                {#if workflow.next_action}<small class="wf-note">下一步：{workflow.next_action}</small>{/if}
                <small class="wf-id">Ticket {workflow.ticket_id.slice(0, 8)} · Flow {workflow.workflow_id.slice(0, 8)}</small>
              </article>
            {/each}
          </div>
        {:else}
          <div class="empty"><b>暂无工作流</b><span>Commander 绑定工作流后会出现在这里。</span></div>
        {/if}
      </section>

      <section class="card">
        <div class="card-h">
          <div><h2>执行<em>节点</em></h2><p>Worker 心跳与存活状态。</p></div>
          <span class="chip is-quiet">{workers.length} 记录</span>
        </div>
        {#if workers.length}
          <div class="rows">
            {#each workers.slice(0, 12) as worker (worker.worker_id)}
              <div class="worker-row">
                <span class:worker-stale={worker.stale} class="worker-indicator"></span>
                <b>{worker.worker_id}</b>
                <small>{worker.stale ? "心跳过期" : worker.state === "active" ? "运行中" : "已停止"} · {relativeTime(worker.heartbeat_at)}</small>
              </div>
            {/each}
          </div>
        {:else}
          <div class="empty"><b>尚无 Worker</b><span>Worker 启动并写入心跳后会显示在这里。</span></div>
        {/if}
      </section>
    </div>
    {/if}

    {#if view === "evolve"}
      <header class="view-head">
        <div>
          <h1>进化<em>回路</em></h1>
          <p>反馈只记录 allowlist 标签与严重度，不采集自由文本；能力雷达生成的提案必须人工批准，界面不会自动改码或自动发布。</p>
        </div>
        <div class="view-head-meta">
          <span class="chip is-quiet">{proposals.length} 提案</span>
          <span class={`chip ${selfImprovementStatus?.automatic_code_changes ? "is-stale" : "is-live"}`}>{selfImprovementStatus?.automatic_code_changes ? "自动改码已启用" : "自动改码关闭"}</span>
        </div>
      </header>

      <div class="cols-2">
      <section class="card" id="feedback">
        <div class="card-h">
          <div><h2>反馈 · <em>自我改进</em></h2><p>只记录预设分类标签和严重程度，不收集聊天原文或自由文本。</p></div>
          <span class="chip is-quiet">人工输入</span>
        </div>
        {#if selfImprovementStatus}
          <div class="stats is-spaced" aria-label="自我进化状态">
            <div class="stat"><small>待研究</small><b>{selfImprovementStatus.pending_research}</b></div>
            <div class="stat"><small>待人工审查</small><b class="is-gold">{selfImprovementStatus.pending_human_review}</b></div>
            <div class="stat"><small>已批待执行</small><b>{selfImprovementStatus.approved_waiting_execution}</b></div>
            <div class="stat"><small>拒绝 / 回滚</small><b class="is-dim">{selfImprovementStatus.proposal_counts.rejected + selfImprovementStatus.proposal_counts.rolled_back}</b></div>
          </div>
          <div class="kv">
            <div class="kv-row"><span>Evolution Cycle</span><b>{selfImprovementStatus.cycle_state === "progress" ? "运行中 / 可恢复" : selfImprovementStatus.cycle_state === "started" ? "已启动 / 可恢复" : selfImprovementStatus.cycle_state === "completed" ? "最近周期已完成" : selfImprovementStatus.cycle_state === "failed" ? "最近周期失败" : selfImprovementStatus.cycle_state === "cancelled" ? "最近周期已取消" : "尚未运行"}</b></div>
            <div class="kv-row"><span>下一步</span><b>{selfImprovementStatus.next_action}</b></div>
            <div class="kv-row"><span>主题检查点</span><b class={selfImprovementStatus.topic_checkpoint_saved ? "is-live" : "is-dim"}>{selfImprovementStatus.topic_checkpoint_saved ? "已保存" : "待保存"}</b></div>
            <div class="kv-row"><span>安全门</span><b class={selfImprovementStatus.approval_required ? "is-live" : "is-warn"}>{selfImprovementStatus.approval_required ? "需要人工批准" : "未启用批准"}</b></div>
          </div>
        {/if}
        <div class="feedback-form">
          <label>反馈类别
            <select bind:value={feedbackCategory} class="select">
              <option value="reliability">可靠性</option>
              <option value="accuracy">准确性</option>
              <option value="usability">易用性</option>
              <option value="capability">能力缺口</option>
              <option value="performance">性能</option>
            </select>
          </label>
          <label>问题标签
            <select bind:value={feedbackTag} class="select">
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
            <select bind:value={feedbackSeverity} class="select">
              <option value="1">1 · 轻微</option>
              <option value="2">2</option>
              <option value="3">3 · 一般</option>
              <option value="4">4</option>
              <option value="5">5 · 严重</option>
            </select>
          </label>
          <button type="button" class="btn is-primary" onclick={submitFeedback} disabled={feedbackBusy || !connected}>
            {feedbackBusy ? "正在提交…" : "记录反馈"}
          </button>
        </div>
        {#if feedbackMessage}
          <p class="feedback-message" role="status">{feedbackMessage}</p>
        {/if}
        <div class="sect is-spaced"><b>最近反馈</b><span>{feedbackRows.length} 条</span></div>
        {#if feedbackRows.length}
            <div class="rows">
              {#each feedbackRows.slice(0, 6) as item, index}
                <div class="row" data-feedback-index={index}>
                  <span><b>{item.tag}</b><small>{item.category} · {relativeTime(item.created_at)}</small></span>
                  <span class="row-meta">严重度 {item.severity}/5</span>
                </div>
              {/each}
            </div>
        {:else}
          <div class="empty"><b>还没有用户反馈</b><span>提交一条反馈即可启动学习回路。</span></div>
        {/if}
      </section>

      <section class="card">
        <div class="card-h">
          <div><h2>改进<em>提案</em></h2><p>能力雷达扫描现有反馈与运行信号，生成待人工审批的改进候选。</p></div>
          <button type="button" class="btn is-sm" onclick={runRadarScan} disabled={radarBusy || !connected}>{radarBusy ? "扫描中…" : "扫描改进机会"}</button>
        </div>
        {#if radarMessage}<p class="feedback-message" role="status">{radarMessage}</p>{/if}
        {#if proposals.length}
          <div class="rows">
            {#each proposals.slice(0, 6) as proposal (proposal.id)}
              <button type="button" class="row proposal-row" onclick={() => openProposal(proposal.id)} disabled={proposalBusy}>
                <span><b>{proposal.title}</b><small>{proposal.status} · 更新于 {relativeTime(proposal.updated_at)} · 查看详情</small></span>
                <span class="row-meta">P{proposal.priority}</span>
              </button>
            {/each}
          </div>
          {#if selectedProposal}
            <article class="proposal-detail">
              <div class="card-h"><div><h2>提案详情 · {selectedProposal.status}</h2></div><button type="button" class="btn is-sm" onclick={() => selectedProposal = null}>收起</button></div>
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
          <div class="empty"><b>尚无改进提案</b><span>反馈达到触发条件后可运行能力雷达生成。</span></div>
        {/if}
      </section>
      </div>
    {/if}

    {#if view === "runtime"}
      <section class="card" id="events">
        <div class="card-h">
          <div><h2>星轨<em>事件</em></h2><p>SSE 实时推送；断线自动重连。</p></div>
          <span class="chip is-quiet">{events.length} 条</span>
        </div>
        {#if events.length === 0}
          <div class="empty"><b>暂无事件</b><span>后端产生事件后会显示在这里。</span></div>
        {:else}
          <div class="rows">
            {#each events.slice(0, 24) as event, index}
              <div class="row event-row" data-event-index={index}>
                <span><b>{eventTitle(event)}</b><small>{eventSource(event) || "本地运行时"}</small></span>
                <span class="row-meta">{eventTime(event)}</span>
              </div>
            {/each}
          </div>
        {/if}
      </section>
    {/if}

    {#if view === "reviews"}
      <header class="view-head">
        <div>
          <h1>加密个案<em>人工复核</em></h1>
          <p>只读取已完成 review-only 审查的密文结果。打开时才在内存中解密显示；此处不会批准任务、写入长期知识库或执行实验。</p>
        </div>
        <div class="view-head-meta">
          <span class="chip is-quiet">{contextualReviewTickets.length} 项待复核</span>
        </div>
      </header>
      <section class="card">
        <div class="card-h">
          <div><h2>待人工复核<em>队列</em></h2><p>列表仅显示任务编号和时间，不显示用户目标或个案原文。</p></div>
          <button class="btn is-sm" type="button" onclick={refresh} disabled={!connected || refreshBusy}>刷新列表</button>
        </div>
        {#if contextualReviewTickets.length === 0}
          <div class="empty"><b>暂无可复核的加密结果</b><span>只有经过独立 review-only 授权、专用 Worker 审查并完成密文提交的任务才会出现在这里。</span></div>
        {:else}
          <div class="rows">
            {#each contextualReviewTickets as ticket (ticket.task_id)}
              <div class="row">
                <span>
                  <b>个案 · {ticket.task_id.slice(0, 8)}</b>
                  <small>创建 {ticket.created_at || "时间未知"} · 更新 {ticket.updated_at || "时间未知"}</small>
                </span>
                <button
                  class="btn is-sm"
                  type="button"
                  onclick={() => openContextualInquiryReview(ticket.task_id)}
                  disabled={contextualReviewBusy}
                >{selectedContextualReviewId === ticket.task_id ? "收起结果" : "读取加密结果"}</button>
              </div>
            {/each}
          </div>
        {/if}
      </section>
      {#if selectedContextualReviewId}
        <section class="card">
          <div class="card-h">
            <div><h2>审查结果<em>· {selectedContextualReviewId.slice(0, 8)}</em></h2><p>结果只在当前页面内存中展示；关闭或切换页面会清除展示状态。</p></div>
            <button class="btn is-sm" type="button" onclick={closeContextualInquiryReview}>清除显示</button>
          </div>
          {#if contextualReviewBusy}
            <div class="empty"><b>正在读取 DPAPI 加密结果…</b><span>读取过程不会修改任务状态。</span></div>
          {:else if contextualReviewMessage}
            <div class="notice is-bad" role="status">{contextualReviewMessage}</div>
          {:else if contextualReviewResult}
            <article class="proposal-detail">
              <h3>审查摘要</h3>
              <p>{contextualReviewResult.summary}</p>
              <small>结果版本 {contextualReviewResult.schema_version} · 个案编号 {contextualReviewResult.inquiry_id}</small>
            </article>
            <div class="rows">
              {#each contextualReviewResult.findings as finding (finding.finding_id)}
                <article class="proposal-detail">
                  <div class="card-h">
                    <h3>{finding.statement}</h3>
                    <span class="chip is-quiet">
                      {finding.assessment === "support_candidate" ? "支持线索候选" : finding.assessment === "counter_candidate" ? "反向线索候选" : "尚未解决"}
                    </span>
                  </div>
                  <p>{finding.rationale}</p>
                  <small>观察引用 {finding.observation_refs.length} 项 · 假设引用 {finding.hypothesis_refs.length} 项</small>
                </article>
              {/each}
            </div>
            <article class="proposal-detail">
              <h3>仍待查明</h3>
              {#if contextualReviewResult.unknowns.length}
                <ul>{#each contextualReviewResult.unknowns as item}<li>{item}</li>{/each}</ul>
              {:else}
                <p>本轮没有新增未知项；仍需人工核对来源与引用。</p>
              {/if}
              <small>候选判断不等于事实裁决。此页面没有批准、证据定案或知识入库操作。</small>
            </article>
          {/if}
        </section>
      {/if}
    {/if}

    <footer class="stage-foot">
      <span>MUSE · LOCAL-FIRST SUPER AGENT COMMAND CENTER</span>
      <span>所有状态来自本地 API；玄学推演仅作启发假设，绝不冒充事实证据。</span>
    </footer>
  </div>
</main>

<aside class="telemetry" aria-label="系统遥测">
  <div class="tele-head"><b>系统遥测</b><span>LIVE · 5s</span></div>

  <div class="tele-card">
    <div class="kv">
      <div class="kv-row"><span>核心</span><b class={connected && !stale ? "is-live" : "is-warn"}>{connectionBadge}</b></div>
      <div class="kv-row"><span>更新时间</span><b class="is-dim">{updatedLabel}</b></div>
      <div class="kv-row"><span>网关</span><b class="is-dim">{mobileCompanion ? "等待安全配对" : "127.0.0.1 · Bearer"}</b></div>
    </div>
  </div>

  <div class="tele-card">
    <div class="sect"><b>队列</b><span>QUEUE</span></div>
    <div class="ladder">
      {#each queueLadder as row (row.label)}
        <div class="ladder-row">
          <b>{row.label}</b>
          <span class="meter {row.tone}">
            <svg viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true"><rect x="0" y="0" width={row.pct} height="6" /></svg>
          </span>
          <i>{row.value}</i>
        </div>
      {/each}
    </div>
  </div>

  <div class="tele-card">
    <div class="sect"><b>资源护栏</b><span>GOVERNOR</span></div>
    <div class="kv">
      <div class="kv-row"><span>逻辑核</span><b>{prometheusCore?.resource_governor.logical_cpu_count ?? "—"}</b></div>
      <div class="kv-row"><span>安全并发</span><b class="is-gold">{prometheusCore?.resource_governor.recommended_workers ?? "—"} / {prometheusCore?.resource_governor.maximum_workers ?? "—"}</b></div>
      <div class="kv-row"><span>可用内存</span><b>{ramLabel}</b></div>
      <div class="kv-row"><span>GPU 显存</span><b class={gpuResourceStatus?.status === "reserved" ? "is-warn" : "is-dim"}>{gpuLabel}</b></div>
    </div>
  </div>

  <div class="tele-card">
    <div class="sect"><b>知识</b><span>WIKI</span></div>
    <div class="kv">
      <div class="kv-row"><span>星体</span><b>{prometheusCore?.stages.discovery.knowledge_nodes_total ?? 0}</b></div>
      <div class="kv-row"><span>连边</span><b>{prometheusCore?.stages.synthesis.knowledge_edges_total ?? 0}</b></div>
      <div class="kv-row"><span>候选证据</span><b>{prometheusCore?.stages.discovery.claim_evidence_count ?? 0}</b></div>
      <div class="kv-row"><span>已收入库</span><b class="is-dim">{prometheusCore?.stages.transmission.user_track_memories_count ?? 0} / {prometheusCore?.stages.transmission.agent_track_memories_count ?? 0}</b></div>
    </div>
  </div>

  <div class="tele-card">
    <div class="sect"><b>最新动态</b><span>STREAM</span></div>
    {#if events.length}
      <div class="rows">
        {#each events.slice(0, 5) as event, index (event.id ?? index)}
          <div class="tele-event">
            <i aria-hidden="true"></i>
            <span><b>{eventTitle(event)}</b><small>{eventTime(event)}</small></span>
          </div>
        {/each}
      </div>
    {:else}
      <p class="side-copy">暂无事件。</p>
    {/if}
  </div>
</aside>
</div>

<nav class="dock" aria-label="快捷指令坞">
  <button type="button" class="dock-item is-key" onclick={openCommandPalette}>
    <Icon name="search" size={18} /><span>指令</span>
  </button>
  <button type="button" class="dock-item" onclick={() => setView("tasks")}>
    <Icon name="tasks" size={18} /><span>任务</span>
  </button>
  <button type="button" class="dock-item" onclick={() => setView("agents")}>
    <Icon name="agents" size={18} /><span>智能体</span>
  </button>
  <div class="dock-key">
    <button type="button" onclick={() => setView("overview")} aria-label="回到调度阵">
      <Icon name="core" size={21} />
    </button>
  </div>
  <button type="button" class="dock-item" onclick={() => setView("knowledge")}>
    <Icon name="knowledge" size={18} /><span>知识</span>
  </button>
  <button type="button" class="dock-item" onclick={() => setView("studio")}>
    <Icon name="studio" size={18} /><span>创作</span>
  </button>
  <button type="button" class="dock-item" onclick={() => setView("runtime")}>
    <Icon name="flame" size={18} /><span>运行时</span>
  </button>
</nav>
</div>

<style>
  /* shell-scoped layout details that belong to this view only */

  .overview-grid {
    display: grid;
    grid-template-columns: minmax(0, 1.5fr) minmax(268px, 1fr);
    gap: 20px;
    align-items: start;
  }

  .overview-side {
    display: grid;
    gap: 14px;
    align-content: start;
  }

  .side-block {
    padding: 13px 14px;
    border: 1px solid var(--hair-soft);
    border-radius: var(--r-md);
    background: #0a0d14;
  }

  .side-block .sect {
    margin: 0 0 9px;
  }

  .side-copy {
    margin-top: 8px;
    font-size: 10.5px;
    line-height: 1.7;
    color: var(--muted);
  }

  .ladder {
    display: grid;
    gap: 2px;
  }

  .ladder-row {
    display: grid;
    grid-template-columns: 46px minmax(0, 1fr) 52px;
    align-items: center;
    gap: 9px;
    padding: 4px 0;
    font-size: 10.5px;
    color: var(--ivory-dim);
  }

  .ladder-row b {
    font-weight: 400;
    color: var(--muted);
  }

  .ladder-row i {
    font: 500 10.5px var(--font-data);
    font-style: normal;
    color: var(--ivory);
    text-align: right;
    font-variant-numeric: tabular-nums;
  }

  .stat-sep {
    margin: 0 3px;
    color: #4b5158;
    font-size: 15px;
  }

  .event-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 10px;
    align-items: baseline;
  }

  .tele-card {
    padding: 12px 13px;
    border: 1px solid var(--hair-soft);
    border-radius: var(--r-md);
    background: #0a0d14;
  }

  .tele-card .sect {
    margin: 0 0 8px;
    letter-spacing: 0.04em;
  }

  .tele-card .sect::after {
    display: none;
  }

  .tele-event {
    display: flex;
    gap: 8px;
    align-items: flex-start;
    padding: 5px 0;
    border-bottom: 1px dashed rgba(33, 39, 52, 0.8);
  }

  .tele-event:last-child {
    border-bottom: 0;
  }

  .tele-event i {
    width: 5px;
    height: 5px;
    margin-top: 5px;
    flex: none;
    border-radius: 50%;
    background: var(--gold);
    box-shadow: 0 0 7px rgba(200, 162, 74, 0.6);
  }

  .tele-event b {
    display: -webkit-box;
    display: block;
    font-size: 10.5px;
    font-weight: 400;
    line-height: 1.5;
    color: var(--ivory-2);
    overflow: hidden;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
  }

  .tele-event small {
    display: block;
    margin-top: 2px;
    font: 9px var(--font-data);
    color: #5f6672;
  }

  .queue-tools {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 130px;
    gap: 8px;
  }

  .queue-note {
    margin: 8px 0 6px;
  }

  .wf {
    padding: 10px 0;
    border-bottom: 1px solid var(--hair-soft);
  }

  .wf:last-child {
    border-bottom: 0;
  }

  .wf-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
  }

  .wf-head b {
    font-size: 12px;
    font-weight: 500;
    color: var(--ivory);
  }

  .wf-stage {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin-top: 4px;
  }

  .wf-stage strong {
    font: 500 10.5px var(--font-data);
    color: var(--gold);
  }

  .wf-stage small,
  .wf-note,
  .wf-id {
    font-size: 10px;
    color: var(--muted);
  }

  .wf-note {
    display: block;
    margin-top: 3px;
  }

  .wf-id {
    display: block;
    margin-top: 4px;
    font-family: var(--font-data);
    color: #5a6068;
  }

  .worker-row {
    display: grid;
    grid-template-columns: 8px minmax(0, 1fr);
    gap: 3px 9px;
    align-items: center;
    padding: 9px 0;
    border-bottom: 1px solid var(--hair-soft);
  }

  .worker-row:last-child {
    border-bottom: 0;
  }

  .worker-row b {
    font-size: 11.5px;
    font-weight: 500;
    color: var(--ivory);
    overflow-wrap: anywhere;
  }

  .worker-row small {
    grid-column: 2;
    font-size: 10px;
    color: var(--muted);
  }

  .worker-indicator {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--jade);
    box-shadow: 0 0 7px rgba(79, 183, 154, 0.5);
  }

  .worker-indicator.worker-stale {
    background: var(--amber);
    box-shadow: none;
  }

  .feedback-form {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px;
    align-items: end;
    margin: 14px 0;
  }

  .feedback-form label {
    display: grid;
    gap: 6px;
    font-size: 10.5px;
    color: var(--muted);
  }

  .feedback-message {
    font-size: 11px;
    color: var(--jade);
    margin: 8px 0;
  }

  .proposal-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 10px;
    align-items: baseline;
    width: 100%;
    text-align: left;
    padding: 10px 0;
    border: 0;
    border-bottom: 1px solid var(--hair-soft);
    background: none;
    cursor: pointer;
  }

  .proposal-row:hover b {
    color: var(--gold-hi);
  }

  .proposal-detail {
    margin-top: 12px;
    padding: 14px;
    border: 1px solid var(--hair);
    border-radius: var(--r-md);
    background: #0a0d14;
  }

  .proposal-detail h3 {
    font: 600 14px var(--font-display);
    color: var(--ivory);
    margin: 0 0 8px;
  }

  .proposal-detail p,
  .proposal-detail li {
    font-size: 11px;
    line-height: 1.7;
    color: var(--ivory-2);
  }

  .proposal-detail ul {
    padding-left: 16px;
    list-style: disc;
    margin: 6px 0;
  }

  .stage-foot {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: 8px 18px;
    margin-top: 6px;
    padding-top: 14px;
    border-top: 1px solid var(--hair-soft);
    font: 9.5px var(--font-data);
    letter-spacing: 0.06em;
    color: #4f545c;
  }

  .stage-foot span:last-child {
    font-family: var(--font-ui);
    letter-spacing: 0;
  }

  @media (max-width: 1240px) {
    .overview-grid {
      grid-template-columns: minmax(0, 1fr);
    }
  }

  @media (max-width: 780px) {
    .feedback-form {
      grid-template-columns: minmax(0, 1fr);
    }

    .queue-tools {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  .system-file-list {
    max-height: 430px;
    overflow: auto;
    padding-right: 4px;
  }

  .system-skill-list {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    max-height: 260px;
    overflow: auto;
    padding: 4px 0;
  }

  .system-file-list .artifact-row b {
    overflow-wrap: anywhere;
    font-family: var(--font-data);
    font-size: 10px;
  }

  .system-file-list .artifact-row small {
    font-size: 9px;
  }

  .fusion-grid {
    display: grid;
    grid-template-columns: minmax(260px, .82fr) minmax(0, 1.18fr);
    gap: 14px;
    align-items: start;
  }

  .fusion-source-card,
  .fusion-result-card,
  .fusion-history {
    min-width: 0;
  }

  .fusion-field {
    display: grid;
    gap: 7px;
    margin: 16px 0;
    color: var(--ivory-2);
    font-size: 12px;
  }

  .fusion-field > span {
    color: var(--gold-hi);
    font-weight: 650;
  }

  .fusion-input {
    width: 100%;
    min-width: 0;
    box-sizing: border-box;
    border: 1px solid var(--hair);
    border-radius: var(--r-sm);
    background: var(--ink-2);
    color: var(--ivory);
    padding: 11px 12px;
    font: 12px var(--font-ui);
  }

  .fusion-input:focus-visible,
  .fusion-clear:focus-visible,
  .fusion-history-row:focus-visible {
    outline: 2px solid var(--cyan);
    outline-offset: 2px;
  }

  .fusion-file {
    padding: 10px;
  }

  .fusion-field small,
  .fusion-note,
  .fusion-result-head small,
  .fusion-capability small {
    color: var(--muted);
    font-size: 10px;
    line-height: 1.6;
  }

  .fusion-orbit {
    display: flex;
    align-items: center;
    gap: 10px;
    color: var(--muted);
    font-size: 10px;
  }

  .fusion-orbit::before,
  .fusion-orbit::after {
    content: "";
    height: 1px;
    flex: 1;
    background: var(--hair);
  }

  .fusion-orbit span {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--jade);
    box-shadow: 0 0 12px var(--jade);
  }

  .fusion-selected,
  .fusion-result-head,
  .fusion-safety {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 9px;
    padding: 10px;
    margin: 12px 0;
    border: 1px solid var(--hair);
    border-radius: var(--r-sm);
    background: var(--ink-2);
  }

  .fusion-selected b,
  .fusion-result-head b {
    min-width: 0;
    overflow-wrap: anywhere;
    color: var(--ivory);
    font-size: 11px;
  }

  .fusion-selected span {
    color: var(--muted);
    font: 10px var(--font-data);
  }

  .fusion-clear {
    margin-left: auto;
    border: 0;
    background: transparent;
    color: var(--cyan);
    cursor: pointer;
    font-size: 11px;
  }

  .fusion-submit {
    width: 100%;
    margin-top: 12px;
    min-height: 42px;
  }

  .fusion-submit:disabled {
    opacity: .45;
    cursor: not-allowed;
  }

  .fusion-message {
    color: var(--jade);
    font-size: 11px;
    line-height: 1.6;
    overflow-wrap: anywhere;
  }

  .fusion-note {
    display: block;
    margin: 12px 0 0;
  }

  .fusion-result-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .fusion-result-head small {
    font-family: var(--font-data);
    overflow-wrap: anywhere;
  }

  .fusion-metrics {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
    margin: 14px 0;
  }

  .fusion-metrics > div {
    padding: 12px;
    border: 1px solid var(--hair);
    border-radius: var(--r-sm);
    background: var(--ink-2);
  }

  .fusion-metrics b,
  .fusion-metrics small {
    display: block;
  }

  .fusion-metrics b {
    color: var(--gold-hi);
    font: 600 22px var(--font-data);
  }

  .fusion-metrics small {
    margin-top: 4px;
    color: var(--muted);
    font-size: 10px;
  }

  .fusion-capabilities {
    display: grid;
    gap: 8px;
    max-height: 300px;
    overflow: auto;
  }

  .fusion-capability {
    padding: 12px;
    border: 1px solid var(--hair);
    border-left: 2px solid var(--jade);
    border-radius: var(--r-sm);
    background: var(--panel-2);
  }

  .fusion-capability > div {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: 8px;
  }

  .fusion-capability b {
    color: var(--ivory);
    font-size: 12px;
  }

  .fusion-capability > div span {
    color: var(--cyan);
    font: 10px var(--font-data);
  }

  .fusion-capability p {
    margin: 6px 0;
    color: var(--ivory-2);
    font-size: 11px;
    line-height: 1.55;
  }

  .fusion-capability small {
    display: block;
    overflow-wrap: anywhere;
  }

  .fusion-safety {
    align-items: flex-start;
    flex-direction: column;
    border-color: var(--gold-dim);
  }

  .fusion-safety b {
    color: var(--gold-hi);
    font-size: 11px;
  }

  .fusion-safety span {
    color: var(--ivory-2);
    font-size: 10px;
    line-height: 1.6;
  }

  .fusion-empty {
    min-height: 220px;
  }

  .fusion-history {
    margin-top: 14px;
  }

  .fusion-history-row {
    display: flex;
    width: 100%;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 12px 4px;
    border: 0;
    border-bottom: 1px solid var(--hair-soft);
    background: transparent;
    color: inherit;
    text-align: left;
    cursor: pointer;
  }

  .fusion-history-row > span:first-child {
    display: grid;
    min-width: 0;
    gap: 4px;
  }

  .fusion-history-row b {
    color: var(--ivory);
    font-size: 11px;
    overflow-wrap: anywhere;
  }

  .fusion-history-row small {
    color: var(--muted);
    font-size: 10px;
  }

  @media (max-width: 900px) {
    .fusion-grid {
      grid-template-columns: minmax(0, 1fr);
    }
  }

  @media (max-width: 520px) {
    .fusion-metrics b {
      font-size: 18px;
    }

    .fusion-history-row {
      align-items: flex-start;
      flex-direction: column;
    }
  }

</style>

