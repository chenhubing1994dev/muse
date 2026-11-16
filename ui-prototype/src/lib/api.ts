import {
  createApiClient as createClient,
  resolveDesktopConnection as resolveConnection,
} from "../../api.js";

export type AgentStatus = "online" | "busy" | "offline" | "disabled";

export interface AgentEffectiveness {
  completed?: number;
  succeeded?: number;
  reviewed?: number;
  review_approved?: number;
  success_rate?: number | null;
  review_pass_rate?: number | null;
}

export interface AgentRecord {
  agent_id: string;
  display_name?: string | null;
  role?: string | null;
  source?: string | null;
  status: AgentStatus;
  capabilities?: string[];
  last_seen_at?: string | null;
  effectiveness?: AgentEffectiveness | null;
}

export interface MuseEvent {
  category?: string;
  event?: string;
  id?: string | number;
  created_at?: string | number | null;
  timestamp?: string | number | null;
  time?: string | number | null;
  summary?: string | null;
  message?: string | null;
  event_type?: string | null;
  kind?: string | null;
  source?: string | null;
  entity_id?: string | null;
  task_id?: string | null;
}

export interface ApiConnection {
  baseUrl: string;
  token: string;
}

export interface HealthResponse {
  api_version: number;
  agent_registry_configured?: boolean;
}

export interface AgentsResponse {
  api_version: number;
  registry_configured?: boolean;
  count?: number;
  agents: AgentRecord[];
}

export interface QueueTask {
  id: string;
  title: string;
  status: string;
  updated_at: string;
  lease_owner?: string | null;
  lease_expires_at?: string | null;
  attempts: number;
  depends_on?: string[];
}

export interface QueueSnapshot {
  counts: {
    queued: number;
    running: number;
    waiting_approval: number;
    succeeded: number;
    failed: number;
    cancelled: number;
  };
  active: QueueTask[];
  active_truncated: boolean;
}

export interface WorkerRecord {
  worker_id: string;
  state: "active" | "stopped";
  heartbeat_at: string;
  stale: boolean;
}

export interface WorkflowRunRecord {
  ticket_id: string;
  title: string;
  workflow_id: string;
  profile_name: string;
  profile_version: number;
  status: string;
  stage: string;
  attempt: number;
  step_id?: string | null;
  next_action?: string | null;
}

export interface OverviewResponse {
  api_version: number;
  recent_events?: MuseEvent[];
  queue?: QueueSnapshot;
  workers?: WorkerRecord[];
  workflow_runs?: WorkflowRunRecord[];
}

export interface FeedbackRecord {
  category: string;
  tag: string;
  severity: number;
  created_at: string;
}
export interface ImprovementProposal {
  id: string;
  title: string;
  status: string;
  priority: number;
  created_at: string;
  updated_at: string;
}
export interface ImprovementDetail extends ImprovementProposal {
  problem: string;
  hypothesis: string;
  confidence: number | null;
  impact: string;
  evidence: Record<string, string | number | boolean | null>;
  acceptance_criteria: string[];
  rollback_plan: string;
  sources: Array<{ id: string; title: string; url: string; license_status: string; security_status: string; created_at: string }>;
  events: Array<{ from_status: string | null; to_status: string; actor: string; created_at: string }>;
  approval_required: true;
}
export interface RadarTrend {
  direction: "improving" | "worsening" | "stable" | "insufficient_data";
  current_total: number;
  previous_total: number | null;
  delta: number | null;
  lookback_hours: number;
}
export interface SelfImprovementStatus {
  status: string;
  proposal_counts: Record<string, number>;
  pending_research: number;
  pending_human_review: number;
  approved_waiting_execution: number;
  next_action: string;
  cycle_state: string;
  cycle_resumable: boolean;
  cycle_updated_at: string | null;
  topic_checkpoint_saved: boolean;
  approval_required: boolean;
  automatic_code_changes: boolean;
}

export interface InspirationRecord {
  id: string; title: string; content: string; created_at: string; updated_at: string;
  status: string; allow_external_research: boolean;
  tags?: string[]; research_domains?: string[]; source?: string;
}
export interface ArtifactRecord {
  id: string; name: string; kind: string; size_bytes: number; modified_at: string;
  download_path: string;
}
export interface ProductQualityGate {
  status: "passed" | "failed" | "blocked" | "unknown";
  review_warning_count: number;
  review_warning_codes: string[];
  warning_review_required: boolean;
}
export interface ProductQualityWarning {
  code: string; message?: string; start_seconds: number; duration_seconds: number;
  review_threshold_seconds?: number;
}
export interface ProductMotionMetadata {
  profile: string;
  renderer: string;
  remotion_version?: string;
  composition_id?: string;
  transition?: string;
  transition_seconds?: number;
  scene_transition_seconds?: number;
  scene_count?: number;
  intro_seconds?: number;
  outro_seconds?: number;
  audio_mode?: "music" | "narration";
}
export interface ProductRecord {
  id: string; initiative_id: string; creator_initiative_id?: string | null; creator_feedback_enabled?: boolean; version_id: string; name: string;
  kind: string; status: string; version: string; size_bytes: number;
  modified_at: string; download_path: string; preview_path: string; video_path?: string;
  video_bytes?: number; video_metrics?: Record<string, string | number>; video_mp4_sha256?: string; video_package_sha256?: string;
  quality_gate?: ProductQualityGate; quality_warnings?: ProductQualityWarning[]; motion?: ProductMotionMetadata;
}export interface InitiativeTimelineEvent {
  id: string;
  event_type: "created" | "status_changed" | "feedback_recorded" | "product_built" | "product_failed" | "knowledge_linked";
  from_status: string | null;
  to_status: string | null;
  actor: string;
  source: string;
  created_at: string;
  details: Record<string, unknown>;
}
export interface InitiativeTimeline {
  initiative: {
    id: string; title: string; kind: string; status: string;
    created_at: string; updated_at: string;
  };
  events: InitiativeTimelineEvent[];
  history_complete: boolean;
}
export interface KnowledgeNodeRecord {
  id: string; node_type: string; title: string; summary: string; status: string;
  provenance: string[]; created_at: string; updated_at: string;
  metadata: Record<string, string | number | boolean | null>;
}
export interface VideoJobRecord {
  id: string; status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  worker_available?: boolean;
  error?: string; product?: { initiative_id: string; version_id: string; title?: string; kind: string };
}
export interface VideoAttemptMetrics {
  total: number; succeeded: number; failed: number; pending: number; terminal: number;
  success_rate: number | null; success_rate_numerator: number; success_rate_denominator: number;
  scope: string;
}
export interface VideoAttemptSummary {
  api_version: number; metrics: VideoAttemptMetrics;
  attempts: Array<{ attempt_id: string; mode: string; status: string; created_at: string; started_at: string | null; finished_at: string | null; initiative_id: string | null; evolution_cycle_id: string | null; product_id: string | null; version_id: string | null; failure_code: string | null; error_type: string | null }>;
}export interface VideoEngineStatus {
  ffmpeg_available: boolean; ffprobe_available: boolean; ffmpeg_version: string | null;
  hardware_encoders: string[];
  comfyui: { status: string; reason?: string };
  velorn: { status: string; reason?: string };
}

export interface GpuResourceStatus {
  api_version: number;
  status: "reserved" | "idle" | "unknown";
  lease: { owner_kind: string; expires_in_seconds: number; heartbeat_age_seconds: number } | null;
  model_supervisor: {
    status: string; action: string; heartbeat_age_seconds: number | null;
    restore_pending: boolean; managed_child: boolean;
  };
  comfyui_memory_release: {
    status: string; updated_age_seconds: number | null;
    vram_free_bytes?: number; vram_total_bytes?: number; vram_free_ratio?: number;
    queue_running?: number; queue_pending?: number;
  };
  policy: string;
}

export interface ApiClient {
  health(): Promise<HealthResponse>;
  agents(): Promise<AgentsResponse>;
  overview(): Promise<OverviewResponse>;
  feedback(limit?: number): Promise<{ api_version: number; count: number; feedback: FeedbackRecord[] }>;
  improvements(): Promise<{ api_version: number; count: number; proposals: ImprovementProposal[] }>;
  selfImprovement(): Promise<{ api_version: number } & SelfImprovementStatus>;
  inspirations(limit?: number): Promise<{ api_version: number; count: number; inspirations: InspirationRecord[] }>;
  artifacts(limit?: number): Promise<{ api_version: number; count: number; artifacts: ArtifactRecord[] }>;
  products(limit?: number): Promise<{ api_version: number; count: number; products: ProductRecord[] }>;
  initiativeTimeline(initiativeId: string, limit?: number): Promise<{ api_version: number } & InitiativeTimeline>;
  knowledgeNode(nodeId: string): Promise<{ api_version: number; node: KnowledgeNodeRecord }>;
  videoAttempts(): Promise<VideoAttemptSummary>;
  videoEngines(): Promise<{ api_version: number } & VideoEngineStatus>;
  gpuResources(): Promise<GpuResourceStatus>;
  downloadProduct(initiativeId: string, versionId: string): Promise<Blob>;
  previewProduct(initiativeId: string, versionId: string): Promise<Blob>;
  downloadVideo(initiativeId: string, versionId: string): Promise<Blob>;
  createVideoProduct(payload: { mode?: "local_assets" | "prompt_to_video"; source?: string; title: string; prompt?: string; negative_prompt?: string; duration_seconds?: number; steps?: number; seed?: number; creator_initiative_id?: string; evolution_cycle_id?: string; audio?: string; audio_mode?: "music" | "narration"; aspect_ratio?: "16:9" | "9:16" | "1:1"; fps?: number; image_seconds?: number; encoder?: "auto" | "amf" | "software"; motion_profile?: "none" | "brand_intro_outro" | "brand_scene_transitions" }): Promise<{ api_version: number; job: { id: string; status: string } }>;  videoJob(id: string): Promise<{ api_version: number; job: VideoJobRecord }>;
  submitInspiration(value: { content: string; allow_external_research: boolean; title?: string; tags?: string[]; research_domains?: string[]; source?: "chatgpt_daily_inspiration" | "muse_local_dashboard" | "user_manual" }): Promise<{ api_version: number; inspiration: InspirationRecord }>; 
  downloadArtifact(name: string): Promise<Blob>;
  improvementDetail(id: string): Promise<{ api_version: number; proposal: ImprovementDetail }>;
  submitFeedback(feedback: { category: string; tag: string; severity: number }): Promise<{ api_version: number; feedback: FeedbackRecord }>;
  submitInitiativeFeedback(initiativeId: string, review: { feedback: string; decision: "accept" | "revise" | "reject"; evolution_cycle_id?: string }): Promise<{ api_version: number; feedback: { id: string; decision: string; created_at: string; evolution_cycle_id: string | null } }>;
  runRadarScan(): Promise<{ api_version: number; signals_seen: Array<{ key: string; count: number; priority: number; confidence: number }>; created: ImprovementProposal[]; existing: ImprovementProposal[]; trend: RadarTrend; approval_required: true }>;
  streamEvents(options: { lastEventId?: string; signal: AbortSignal; onEvent: (event: MuseEvent) => void }): Promise<string>;
  events(limit?: number): Promise<{ api_version: number; events?: MuseEvent[] }>;
}

export const createApiClient = (connection: ApiConnection): ApiClient =>
  createClient(connection) as ApiClient;

export const resolveDesktopConnection = async (): Promise<ApiConnection> =>
  (await resolveConnection()) as ApiConnection;
