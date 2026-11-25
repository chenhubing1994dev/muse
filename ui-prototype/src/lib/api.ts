import {
  createApiClient as createClient,
  createCompanionRemoteClient as createRemoteCompanionClient,
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
  title?: string | null;
  reports_to?: string | null;
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

export interface CompanionPairing {
  request_id: string;
  device_name: string;
  device_fingerprint: string;
  created_at: number;
  expires_at: number;
  status: "pending" | "approved";
  approved_at: number | null;
}
export interface CompanionDevice {
  device_id: string;
  device_name: string;
  device_fingerprint: string;
  permissions: string[];
  created_at: number;
  revoked_at: number | null;
  revoked_by: string | null;
  status: "active" | "revoked";
}
export interface CompanionAuditEvent {
  event_id: string;
  device_id: string | null;
  event_type: string;
  actor: string;
  details: Record<string, unknown>;
  created_at: number;
}
export interface CompanionPairingChallenge {
  challenge_id: string;
  code: string;
  expires_at: number;
}

export interface HealthResponse {
  api_version: number;
  agent_registry_configured?: boolean;
}

export interface AgentOrgChartNode {
  agent_id: string;
  title: string;
  role: string;
  display_name: string;
  status: string;
  enabled: boolean;
  reports_to: string | null;
  capabilities: string[];
}

export interface AgentOrgChartResponse {
  nodes: AgentOrgChartNode[];
  edges: Array<{ manager_id: string; report_id: string }>;
  total: number;
  truncated: boolean;
  unresolved_parent_count: number;
}

export interface AgentsResponse {
  api_version: number;
  registry_configured?: boolean;
  count?: number;
  agents: AgentRecord[];
  org_chart?: AgentOrgChartResponse;
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

export interface ContextualInquiryReviewSummary {
  task_id: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface ContextualInquiryReviewFinding {
  finding_id: string;
  statement: string;
  observation_refs: string[];
  hypothesis_refs: string[];
  assessment: "support_candidate" | "counter_candidate" | "unresolved";
  rationale: string;
}

export interface ContextualInquiryReviewResult {
  schema_version: 1;
  inquiry_id: string;
  summary: string;
  findings: ContextualInquiryReviewFinding[];
  unknowns: string[];
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
  evolution_stage?: string; blueprint_path?: string;
  research_report_path?: string; improvement_proposal_ids?: string[];
}
export interface InspirationEvolutionResult {
  status: string;
  inspiration_id: string;
  inspiration_title: string;
  allow_external_research: boolean;
  external_network_called: boolean;
  evolution_cycle_id: string;
  research_report_path: string | null;
  synthesis_status: string | null;
  evidence_coverage_score: number;
  improvement_proposals: Array<{ proposal_id: string; title: string; status: string; dossier_path: string }>;
  improvement_proposal_ids: string[];
  approval_required: true;
  automatic_code_changes: false;
  blueprint_path: string;
  blueprint_title: string;
  queued_video_jobs: PipelineJob[];
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
export interface ProductStoryboardMatch {
  scene_id: string; source_file: string;
  scene_start_seconds: number; scene_end_seconds: number;
  overlap_start_seconds: number; overlap_end_seconds: number; overlap_seconds: number;
}
export interface ProductQualityWarning {
  code: string; message?: string; start_seconds?: number; duration_seconds?: number;
  review_threshold_seconds?: number; value?: number; unit?: string;
  mapping_status?: "mapped" | "outside_storyboard" | "not_time_based";
  storyboard_matches?: ProductStoryboardMatch[];
}
export interface ProductSceneTimeline {
  scene_id: string; source_asset: string; camera_move?: "push_in" | "drift_left" | "pull_out" | "rise" | "drift_right";
  timeline_start_seconds?: number; timeline_end_seconds?: number; duration_seconds?: number;
  transition_in_seconds?: number; transition_out_seconds?: number;
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
export interface SubtitleReviewSegment {
  index: number; start_seconds: number; end_seconds: number; asr_text: string;
  review_status: string; corrected_text: string | null;
}
export interface SubtitleReviewDocument {
  schema_version: number; review_status: string; human_approved: boolean;
  reference_script: string; raw_asr_transcript: string;
  segments: SubtitleReviewSegment[]; subtitle_cue_count: number;
}
export interface VideoVisualQualityFrame {
  frame_index: number; time_seconds: number; width: number; height: number;
  mean_luma: number; p05_luma: number; median_luma: number; p95_luma: number;
  contrast_stddev: number; sharpness_laplacian_variance: number; mean_saturation: number;
  shadow_fraction: number; highlight_fraction: number;
}
export interface VideoVisualQualitySignal {
  code: string; time_seconds?: number; severity?: string; metric?: number; note?: string;
}
export interface VideoVisualQualityReport {
  schema_version: number; status: "observed" | "unavailable"; method: string;
  semantic_recognition: false; source_name?: string; reason?: string; note?: string;
  duration_seconds?: number; source_fps?: number; source_frame_count?: number; sampled_frame_count?: number;
  frames?: VideoVisualQualityFrame[]; signals?: VideoVisualQualitySignal[]; review_required?: boolean;
  temporal?: { pair_count: number; max_mean_luma_delta: number; median_flow_pixels: number; p90_residual_flow_pixels: number; interpretation: string } | null;
}
export interface ProductRecord {
  id: string; initiative_id: string; creator_initiative_id?: string | null; creator_feedback_enabled?: boolean; version_id: string; name: string;
  kind: string; status: string; version: string; size_bytes: number;
  modified_at: string; download_path: string; preview_path: string; video_path?: string;
  video_bytes?: number; video_metrics?: Record<string, string | number>; video_mp4_sha256?: string; video_package_sha256?: string;
  quality_gate?: ProductQualityGate; quality_warnings?: ProductQualityWarning[]; motion?: ProductMotionMetadata;
  scene_timeline?: ProductSceneTimeline[];
  subtitle_review?: { status: "approved" | "pending_human_review" | "unavailable" | "ambiguous"; human_approved: boolean; review_available?: boolean; approved_by?: string; revision_version_id?: string };}
export interface InitiativeTimelineEvent {
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
export interface KnowledgeNodeNeighbor {
  id: string;
  node_type: string;
  title: string;
  status: string;
  direction: "outgoing" | "incoming";
  edge_type: string;
}
export interface KnowledgeNodeRecord {
  id: string; node_type: string; title: string; summary: string; status: string;
  provenance: string[]; created_at: string; updated_at: string;
  metadata: Record<string, string | number | boolean | null>;
}
export interface KnowledgeNodeDetailResponse {
  api_version: number;
  node: KnowledgeNodeRecord;
  neighbors?: KnowledgeNodeNeighbor[];
  evidence_summary?: { total: number; by_direction: Record<string, number>; reviewed_count: number };
}
export interface ClaimDossierEvidence {
  id: number;
  direction: string;
  evidence_type: string;
  quality: string;
  independence: string;
  source_node_id: string;
  source_title: string;
  source_ref: string | null;
  reviews: Array<{ verdict: string; reviewer_id: string; rationale: string; reviewed_at: string }>;
}
export interface ClaimDossier {
  claim_id: string;
  title: string;
  original_claim: string;
  original_source: string | null;
  claim_status: string;
  epistemic_status: string;
  evidence: ClaimDossierEvidence[];
  evidence_counts: Record<string, number>;
  evidence_coverage: { coverage_state: string; review_coverage_state: string; reviewed_evidence_count: number; unreviewed_evidence_count: number; evidence_conflict_state: string; retrieval_direction_is_not_evidence_verdict: boolean };
  confidence: { level: string; reviewer_id: string | null; basis: string; automatic_scoring: false };
  confidence_history: Array<{ sequence: number; created_at: string; reviewer_id: string; verdict: string; rationale: string; confidence_level: string; confidence_basis: string }>;
  retrieval_direction_is_not_evidence_verdict: true;
}
export interface KnowledgeGraphNode {
  id: string;
  node_type: "source" | "idea" | "experiment" | "claim" | "decision" | "failure";
  title: string;
  summary: string;
  status: string;
  updated_at: string;
  degree: number;
  epistemic_status: string;
  review_state: string;
  origin: string;
}export interface KnowledgeGraphEdge {
  id: string;
  source_id: string;
  target_id: string;
  edge_type: string;
}
export interface KnowledgeGraphResponse {
  api_version: number;
  configured: boolean;
  totals: {
    active_nodes: number;
    active_edges: number;
    by_type: Record<string, number>;
    by_status: Record<string, number>;
    by_edge_type: Record<string, number>;
    claim_evidence_total: number;
  };
  governance: {
    active_nodes: number;
    active_edges: number;
    lint_scope_isolated_nodes: number;
    claims_without_evidence: number;
    evidence_items_unreviewed: number;
    source_relationships: number;
    pending_source_identity_candidates: number;
    advanced_claims: number;
  } | null;
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
}
export interface PrometheusPrinciple {
  principle_key: string;
  title: string;
  description: string;
  practice: string;
  confidence: number;
  experience_count: number;
  experiment_count: number;
}
export interface ResourceGovernorSnapshot {
  recommended_workers: number;
  maximum_workers: number;
  cpu_cap: number;
  memory_cap: number;
  status: string;
  logical_cpu_count: number | null;
  available_memory_bytes: number | null;
  total_memory_bytes: number | null;
  gpu_available_memory_bytes: number | null;
  reasons: string[];
}
export interface CapabilityMatrixItem {
  id: string;
  title: string;
  pillar: string;
  status: "implemented" | "partial" | "backend_only" | "vision";
  status_label: string;
  summary: string;
  evidence: string;
  entry: string;
  boundary: string;
}
export interface PrometheusCoreResponse {
  api_version: number;
  mission: string;
  stages: {
    ignition: { label: string; inspirations_count: number; initiatives_count: number; reminders_count: number };
    discovery: { label: string; knowledge_nodes_total: number; nodes_by_type: Record<string, number>; claim_evidence_count: number; research_evidence_count: number; research_sites_count: number };
    synthesis: { label: string; knowledge_edges_total: number; edges_by_type: Record<string, number>; philosophy_principles_count: number; philosophy_experiences_count: number; philosophy_links_count: number; philosophy_proposals_count: number };
    creation: { label: string; products_count: number; artifacts_count: number; orchestration_runs_count: number; agent_runs_count: number; video_attempts_total: number; video_attempts_succeeded: number };
    transmission: { label: string; user_track_memories_count: number; agent_track_memories_count: number; progress_events_count: number; watchdog_checks_count: number; capabilities_count: number; improvement_proposals_count: number };
  };
  principles: PrometheusPrinciple[];
  resource_governor: ResourceGovernorSnapshot;
  capability_matrix: CapabilityMatrixItem[];
  memory_privacy_guard: { user_track_text_exposed: boolean; auto_writeback_enabled: boolean };
}
export interface SpecialistProfileItem {
  specialist_id: string;
  role: string;
  domains: string[];
  keywords: string[];
  capabilities: string[];
  description: string;
  trust_tier: string;
  live_adapter_connected: boolean;
  layer: "specialist_template_metadata";
  historical_runs: { runs_total: number; runs_succeeded: number; runs_failed: number };
}
export interface RolePolicyItem {
  role: string;
  allowed_capabilities: string[];
  trust_tier: string;
  description: string;
  updated_at: string;
}
export interface GalaxyResponse {
  api_version: number;
  commander: {
    id: string;
    title: string;
    sole_authority: boolean;
    competing_schedulers: number;
    max_workers_cap: number;
    queue_counts: QueueSnapshot["counts"];
  };
  registry_configured: boolean;
  live_agents: Array<AgentRecord & { layer?: string }>;
  role_policies: RolePolicyItem[];
  specialist_profiles: SpecialistProfileItem[];
  orchestration_summary: {
    total_runs: number;
    by_status: Record<string, number>;
    recent_runs: Array<{ run_id: string; status: string; created_at: string; updated_at: string; agent_count: number; roles: string[] }>;
  };
  capabilities: Array<{ capability_id: string; domain: string; name: string; description: string; capabilities: string[]; requirements: string[]; trust: string; license: string; status: string; version?: string | null }>;
  resource_governor: ResourceGovernorSnapshot;
}
export interface RoutePreviewCandidate {
  specialist_id: string;
  role: string;
  description: string;
  score: number;
  matched_domains: string[];
  matched_keywords: string[];
  trust_tier: string;
  live_adapter_connected: boolean;
  layer: string;
}
export interface RoutePreviewResponse {
  api_version: number;
  goal: string;
  readonly_preview: true;
  dispatched: false;
  live_ready_count: number;
  selected: RoutePreviewCandidate[];
  considered: RoutePreviewCandidate[];
}
export interface BaguaTrigramItem {
  bits_bottom_to_top: string;
  name: string;
  symbol: string;
  nature: string;
  direction: string;
  element: string;
  system_lens: string;
  inquiry_prompt: string;
}
export interface StarMansionItem {
  index: string;
  name: string;
  full_name: string;
  quadrant: "azure_dragon" | "black_tortoise" | "white_tiger" | "vermilion_bird";
  element: string;
  theme: string;
  inquiry: string;
}
export interface CelestialCatalogResponse {
  api_version: number;
  epistemic_status: string;
  is_evidence: false;
  is_prediction: false;
  calendar_reference: {
    local_iso: string;
    year_ganzhi_civil: string;
    day_ganzhi: string;
    hour_branch: string;
    hour_ganzhi: string;
    basis_note: string;
    reference_sources: string[];
  };
  bagua_trigrams: BaguaTrigramItem[];
  star_mansion_quadrants: Record<string, { name: string; season: string; color_token: string }>;
  star_mansions: StarMansionItem[];
  symbolic_lenses: Array<{ id: string; name: string; tradition: string; lens: string; questions: string[] }>;
  core_traditions: Array<{ id: string; name: string; knowledge_scope: string; interpretive_layers: string[]; school_variance_warning: string }>;
  optional_engines: Record<string, { label: string; status: string; reason: string }>;
}
export interface HexagramStructure {
  bits_bottom_to_top: string;
  king_wen_number: number;
  king_wen_name: string;
  unicode_symbol: string;
  lower_trigram: { bits_bottom_to_top: string; name: string };
  upper_trigram: { bits_bottom_to_top: string; name: string };
}
export interface YijingInquiryResponse {
  api_version: number;
  mode: "seeded_cast" | "manual_lines" | "trigram_pair";
  reading: {
    calculation_method: string;
    input_origin: string;
    line_order: string;
    lines: Array<{ position: number; value: number; label: string; yin_yang: "yin" | "yang"; changing: boolean; relating_yin_yang: "yin" | "yang" }>;
    line_values: number[];
    moving_lines: number[];
    primary: HexagramStructure;
    relating: HexagramStructure;
    structural_relations: { nuclear: HexagramStructure; opposite: HexagramStructure; inverse: HexagramStructure };
    king_wen_number: number;
    king_wen_name: string;
    unicode_symbol: string;
    epistemic_status: string;
    is_physical_cast: boolean;
    is_evidence: boolean;
    is_prediction: boolean;
    source_refs: string[];
    seed?: string;
    seed_sha256?: string;
  };
  cross_system: {
    goal: string;
    framework_ids: string[];
    comparison_axes: Array<{ axis_id: string; label: string; perspectives: Record<string, string> }>;
    shared_inquiry_questions: string[];
    divergence_prompts: string[];
    synthesis_candidates: Array<{ id: string; hypothesis: string; test_question: string; falsification_rule: string; alternative_explanations: string[]; uncertainty: string; epistemic_status: string }>;
  } | null;
  lens_seeds: Array<{ id: string; name: string; application_question?: string; hypothesis_seed?: string; falsification_rule?: string; validation_plan?: string }> | null;
  epistemic_status: string;
  is_evidence: false;
  is_prediction: false;
  automatic_memory_write: false;
  automatic_task_approval: false;
  notice: string;
}
export interface VideoJobRecord {
  id: string; status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  worker_available?: boolean;
  error?: string; product?: { initiative_id: string; version_id: string; title?: string; kind: string };
}
export interface VideoAttemptMetrics {
  total: number; succeeded: number; failed: number;
  queued: number; running: number; leased: number; pending: number; terminal: number;
  success_rate: number | null; success_rate_numerator: number; success_rate_denominator: number;
  scope: string;
}
export interface VideoAttemptSummary {
  api_version: number; metrics: VideoAttemptMetrics;
  attempts: Array<{ attempt_id: string; mode: string; status: string; created_at: string; started_at: string | null; finished_at: string | null; initiative_id: string | null; evolution_cycle_id: string | null; product_id: string | null; version_id: string | null; failure_code: string | null; error_type: string | null }>;
  attempts_truncated?: boolean;
}
export interface VideoRecipeTrace {
  read_only: true;
  attempt: { attempt_id: string; status: string; mode: string; approval_task_id: string | null; failure_code: string | null };
  candidate: { candidate_id: string | null; recipe_sha256: string | null; creator_brief_sha256: string | null; brief_fingerprint_matches_attempt: boolean; mapping_status: string | null };
  approval: { ticket_id: string | null; status: string; human_decision_verified: boolean; decision_actor: string | null; brief_matches_creator_brief?: boolean; attempt_matches_ticket?: boolean };
  product: { status: string; product_id?: string; version_id?: string; manifest_sha256?: string; manifest_matches_attempt?: boolean; recipe_matches_attempt?: boolean; mp4_sha256?: string; package_sha256?: string; quality_gate?: Record<string, unknown> };
  release_review: { status: string; can_publish: false; editorial_and_fact_review?: string; visual_and_model_rights_review?: string; privacy_and_person_release_review?: string; account_authorization?: string; official_publish_api?: string; quality_report_matches_package?: boolean; quality_report_sha256?: string | null; overall_status?: string; blocking_items?: string[]; checks?: Record<string, { status: string; evidence?: string; contact_sheet?: { status: string; evidence?: string | null; sha256?: string | null; human_review_required?: boolean }; [key: string]: unknown }> };
  chain_consistent: boolean; execution: string; publishing: "not_performed";
}
export interface VideoEngineStatus {
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

export interface DaemonStatus {
  state: string;
  running: boolean;
  heartbeat_age_seconds: number | null;
  stale: boolean;
  last_error: string | null;
  started_at: string | null;
  owner_id: string | null;
  managed_pid: number | null;
  control_log: string;
}

// ── Muse 视频创作工作室 ──
export interface StylePreset {
  id: "hk-retro" | "pixel" | "cosmic" | "mix";
  label: string;
  sub_label: string;
  palette: string[];
  description: string;
  vfx: string;
  camera_moves: string[];
}
export interface NarrationStyleOption {
  id: "yujie_canto" | "cute_canto" | "news_canto" | "none";
  label: string;
  voice_style: string;
}
export interface StudioStylesResponse {
  api_version: number;
  styles: StylePreset[];
  narration_styles: NarrationStyleOption[];
  capabilities: Record<string, string>;
}
export interface NarrationSegment {
  index: number;
  text: string;
  estimated_duration_seconds: number;
}
export interface StudioJob {
  id: string;
  title: string;
  style: string;
  narration_style: string;
  status: string;
  progress: number;
  stage: string;
  result: Record<string, unknown>;
  error: string;
  created_at: string;
  updated_at: string;
}
export interface MediaAssetResult {
  url: string;
  title: string;
  license: string;
  source_ref: string;
}
export interface StudioRenderRequest {
  topic: string;
  research_text?: string;
  inspiration?: string;
  style?: "hk-retro" | "pixel" | "cosmic" | "mix";
  narration_style?: "yujie_canto" | "cute_canto" | "news_canto" | "none";
  query?: string;
  aspect_ratio?: "9:16" | "16:9" | "1:1";
  fps?: number;
}

export interface PipelineRecipe {
  id: string;
  label: string;
  available: boolean;
  script?: string;
  output?: string;
  description?: string;
  reason?: string;
}
export interface PipelineJob {
  job_id: string;
  recipe_id: string;
  recipe_label: string;
  title: string;
  status: "queued" | "running" | "succeeded" | "failed";
  created_at: string;
  updated_at: string;
  output_dir: string;
  exists_before_run: boolean;
  progress: string;
  error: string | null;
  result: Record<string, unknown> | null;
}


export type InitiativeContentProfile = Partial<Record<"topic" | "hook_style" | "narrative_structure" | "editing_pace" | "visual_style" | "voice_language" | "cta_style" | "format", string>>;
export interface InitiativePerformanceSnapshot {
  id: string;
  initiative_id: string;
  platform: string;
  source: "official_analytics" | "manual_entry";
  currency: string;
  captured_at: string;
  published_at: string | null;
  views: number;
  likes: number;
  comments: number;
  saves: number;
  shares: number;
  cta_clicks: number;
  qualified_leads: number;
  orders: number;
  reported_revenue_minor: number;
  settled_revenue_minor: number;
  production_cost_minor: number;
  three_second_retention_pct: number | null;
  completion_rate_pct: number | null;
  avg_watch_seconds: number | null;
  content_profile: InitiativeContentProfile;
}
export interface InitiativePerformanceSummary {
  currency: string;
  views: number;
  likes: number;
  comments: number;
  saves: number;
  shares: number;
  cta_clicks: number;
  qualified_leads: number;
  orders: number;
  reported_revenue_minor: number;
  settled_revenue_minor: number;
  production_cost_minor: number;
}
export interface InitiativePerformanceRankedItem {
  source_reference: string;
  views: number;
  rates: Record<string, number | null>;
  content_profile: InitiativeContentProfile;
}
export interface InitiativePerformanceCohort {
  platform: string;
  source: string;
  currency: string;
  sample_count: number;
  min_sample_count: number;
  total_views: number;
  min_views: number;
  status: "ready_for_review" | "insufficient_sample";
  rates: Record<string, number | null>;
  item_comparisons: Record<string, {
    status: "descriptive_comparison" | "insufficient_item_samples";
    eligible_item_count: number;
    top: InitiativePerformanceRankedItem[];
    bottom: InitiativePerformanceRankedItem[];
  }>;
  item_comparison_min_views: number;
  comparison_note: string;
  counts: Record<string, number>;
  revenue_minor: { reported: number; settled: number; production_cost: number };
  warnings: string[];
  interpretation: string;
}
export interface InitiativePerformanceAnalysis {
  status: "ready" | "insufficient_data";
  cohorts: InitiativePerformanceCohort[];
  unidentified_snapshots_excluded: number;
  deduplication: string;
}
export interface InitiativePerformanceInput {
  idempotency_key: string;
  platform: "youtube" | "tiktok" | "douyin" | "instagram" | "facebook" | "bilibili" | "other";
  source: "manual_entry" | "official_analytics";
  currency?: string;
  published_at?: string;
  source_reference?: string;
  notes?: string;
  metrics: Record<string, number>;
  content_profile?: InitiativeContentProfile;
}
export interface MemPalaceStatusResponse {
  api_version: number;
  config_state: string;
  enabled: boolean;
  connection_state: string;
  palace_directory_present: boolean;
  vector_index_present: boolean;
  index_freshness: string;
  embedding_model: string;
  embedding_device: string;
  memory_store_state: string;
  projection_outbox: { initialized: boolean; status: string; total: number; pending: number; failed: number; delivered: number; oldest_pending_at: string | null; latest_delivered_at: string | null; latest_created_at: string | null; content_included: false };
  remote_process_started: false;
  private_content_exposed: false;
}

export interface SystemDashboardResponse {
  api_version: 1;
  workspace: { name: string; status: string };
  files: { status: string; count: number; items: Array<{ path: string; size_bytes: number; modified_at: string }>; truncated: boolean; content_included: false };
  spaces: { status: string; count: number; items: unknown[]; note: string };
  runner: {
    status: string; captured_at: string | null; age_seconds: number | null;
    runner_status: string; connected: boolean;
    project_count: number | null; skill_count: number | null; invalid_skill_count: number | null;
    active_job_count: number | null; running_job_count: number | null; queued_job_count: number | null; concurrency_limit: number | null;
    active_jobs: Array<{ kind: string; status: string; elapsed_secs: number | null; project: string }>;
    skill_sources: Array<{ name: string; count: number; invalid_count: number }>;
    projects: Array<{ name: string; connected: boolean }>;
    skills: Array<{ name: string }>;
    mcp_servers: string[]; plugins: string[];
  };
  projects: Array<{ name: string; connected: boolean }>;
  skills: Array<{ name: string }>;
  local_capabilities: {
    status: string; count: number;
    items: Array<{ capability_id: string; domain?: string; name?: string; description?: string; status?: string; trust?: string }>;
  };
  scheduled_tasks: {
    status: string; note: string;
    items: Array<{ name: string; state: string; next_run_time: string | null }>;
  };
  integrations: Array<{ id: string; label: string; status: string; detail: string }>;
  generated_at: string;
  read_only: true;
}



export interface VisualReviewRecord {
  schema_version: number;
  record_id: string;
  category: "visual_content";
  decision: "approved" | "changes_requested" | "rejected";
  reviewer: { label: string; role: string; identity_assurance: "self_asserted_local_session" };
  reviewed_at: string;
  subject: { initiative_id: string; version_id: string; video_sha256: string; artifact_sha256: Record<string, string>; release_manifest_sha256?: string };
  checklist: Record<string, boolean>;
  evidence_refs: string[];
  rationale: string;
  review_sha256: string;
  evidence_status?: string;
  findings?: VisualReviewFinding[];
}
export interface VisualReviewFinding {
  timecode_ms: number;
  shot_id: string;
  severity: "blocker" | "major" | "minor";
  category: "framing" | "continuity" | "visual_artifact" | "subtitle" | "audio" | "other";
  description: string;
  expected_fix: string;
}
export interface VisualRevisionBrief {
  brief_id: string; source_review_id: string; source_review_sha256: string;
  source_version: string; source_video_sha256: string; created_at: string; status: "open";
  tasks: Array<VisualReviewFinding & { task_id: string; status: "open" }>;
  evidence_status?: string;
}
export interface VisualReviewPayload {
  decision: "approved" | "changes_requested" | "rejected";
  reviewer: string;
  reviewer_role: string;
  rationale: string;
  checklist: Record<string, boolean>;
  confirmation: "RECORD-VISUAL-REVIEW";
  findings?: VisualReviewFinding[];
}

export interface FusionCapability {
  id: string; domain: string; name: string; description: string; confidence: number;
  signals: string[]; execution_state: "not_executed";
}
export interface FusionRecord {
  fusion_id: string; status: string;
  source: { kind: "url" | "archive"; label: string; sha256: string };
  analysis: {
    file_count: number; languages: Record<string, number>; manifests: string[];
    license_file_present: boolean; license_status: string; capabilities: FusionCapability[];
    integration_edges: Array<{ from: string; to: string; relationship: string; activation: string }>;
    matched_existing_capabilities: Array<{ candidate_id: string; capability_id: string; name: string; status: string; relationship: string }>;
  };
  safety: { external_code_executed: false; dependencies_installed: false; activation: string };
  registered_capability_ids?: string[]; activation?: string; approved_by?: string; duplicate?: boolean;
}
export interface ApiClient {
  companionPairings(limit?: number): Promise<{ api_version: 1; pairings: CompanionPairing[] }>;
  companionDevices(limit?: number): Promise<{ api_version: 1; devices: CompanionDevice[] }>;
  companionAudit(limit?: number): Promise<{ api_version: 1; events: CompanionAuditEvent[] }>;
  startCompanionPairing(expiresInSeconds?: number): Promise<{ api_version: 1; challenge: CompanionPairingChallenge }>;
  approveCompanionPairing(requestId: string): Promise<{ api_version: 1; pairing: CompanionPairing }>;
  denyCompanionPairing(requestId: string): Promise<{ api_version: 1; denied: boolean }>;
  revokeCompanionDevice(deviceId: string): Promise<{ api_version: 1; revoked: boolean }>;
  health(): Promise<HealthResponse>;
  agents(): Promise<AgentsResponse>;
  overview(): Promise<OverviewResponse>;
  contextualInquiryReviews(limit?: number): Promise<{ api_version: number; count: number; reviews: ContextualInquiryReviewSummary[] }>;
  contextualInquiryReviewResult(taskId: string): Promise<{ api_version: number; task_id: string; review: ContextualInquiryReviewResult }>;
  mempalaceStatus(): Promise<MemPalaceStatusResponse>;
  systemDashboard(): Promise<SystemDashboardResponse>;
  fusionJobs(limit?: number): Promise<{ api_version: 1; jobs: FusionRecord[] }>;
  submitFusion(payload: { source_url?: string; archive_base64?: string; file_name?: string }): Promise<{ api_version: 1; fusion: FusionRecord }>;
  approveFusion(fusionId: string): Promise<{ api_version: 1; fusion: FusionRecord }>;
  rollbackFusion(fusionId: string): Promise<{ api_version: 1; fusion: FusionRecord }>;
  feedback(limit?: number): Promise<{ api_version: number; count: number; feedback: FeedbackRecord[] }>;
  improvements(): Promise<{ api_version: number; count: number; proposals: ImprovementProposal[] }>;
  selfImprovement(): Promise<{ api_version: number } & SelfImprovementStatus>;
  inspirations(limit?: number): Promise<{ api_version: number; count: number; inspirations: InspirationRecord[] }>;
  artifacts(limit?: number): Promise<{ api_version: number; count: number; artifacts: ArtifactRecord[] }>;
  products(limit?: number): Promise<{ api_version: number; count: number; products: ProductRecord[] }>;
  initiativeTimeline(initiativeId: string, limit?: number): Promise<{ api_version: number } & InitiativeTimeline>;
  initiativePerformance(initiativeId: string): Promise<{ api_version: number; snapshots: InitiativePerformanceSnapshot[]; summary_by_currency: InitiativePerformanceSummary[]; analysis: InitiativePerformanceAnalysis }>;  recordInitiativePerformance(initiativeId: string, payload: InitiativePerformanceInput): Promise<{ api_version: number; snapshot: InitiativePerformanceSnapshot }>;
  knowledgeNode(nodeId: string): Promise<KnowledgeNodeDetailResponse>;
  knowledgeClaimDossier(claimId: string, evidenceLimit?: number): Promise<{ api_version: number; dossier: ClaimDossier }>;
  knowledgeGraph(options?: { limit?: number; nodeType?: string | null; q?: string }): Promise<KnowledgeGraphResponse>;
  prometheusCore(): Promise<PrometheusCoreResponse>;
  galaxy(): Promise<GalaxyResponse>;
  previewRoute(goal: string): Promise<RoutePreviewResponse>;
  celestialCatalog(): Promise<CelestialCatalogResponse>;
  castYijing(payload: { mode?: "seeded_cast" | "manual_lines" | "trigram_pair"; seed?: string; line_values?: number[]; lower_bits?: string; upper_bits?: string; goal?: string; frameworks?: string[] }): Promise<YijingInquiryResponse>;
  videoAttempts(): Promise<VideoAttemptSummary>;
  videoRecipeTrace(attemptId: string): Promise<{ api_version: number; trace: VideoRecipeTrace }>;
  videoEngines(): Promise<{ api_version: number } & VideoEngineStatus>;
  gpuResources(): Promise<GpuResourceStatus>;
  daemonStatus(): Promise<DaemonStatus | null>;
  daemonControl(action: "start" | "pause" | "resume" | "stop" | "once"): Promise<{ api_version: number; result: Record<string, unknown> }>;
  pipelineRecipes(): Promise<{ api_version: number; recipes: PipelineRecipe[] } | null>;
  pipelineJobs(limit?: number): Promise<{ api_version: number; jobs: PipelineJob[] } | null>;
  pipelineJob(id: string): Promise<{ api_version: number; job: PipelineJob } | null>;
  studioStyles(): Promise<StudioStylesResponse>;
  studioJobs(limit?: number): Promise<{ api_version: number; jobs: StudioJob[] }>;
  studioJob(id: string): Promise<{ api_version: number; job: StudioJob }>;
  studioSearchMedia(payload: StudioRenderRequest): Promise<{ api_version: number; status: string; assets: MediaAssetResult[]; reason?: string }>;
  studioGenerateScript(payload: StudioRenderRequest): Promise<{ api_version: number; status: string; segments: NarrationSegment[]; reason?: string }>;
  studioPhilosophyInject(payload: StudioRenderRequest): Promise<{ api_version: number; status: string; principles?: unknown[]; note?: string; muse_note?: string; reason?: string }>;
  studioRender(payload: StudioRenderRequest): Promise<{ api_version: number; job: StudioJob; plan: Record<string, unknown> }>;
  submitPipeline(payload: { recipe_id: string; title?: string }): Promise<{ api_version: number; job: PipelineJob }>;
  downloadProduct(initiativeId: string, versionId: string): Promise<Blob>;
  previewProduct(initiativeId: string, versionId: string): Promise<Blob>;
  downloadVideo(initiativeId: string, versionId: string): Promise<Blob>;
  videoContactSheet(initiativeId: string, versionId: string): Promise<Blob>;
  visualReviews(initiativeId: string, versionId: string): Promise<{ api_version: number; identity_assurance: string; evidence: { video_sha256: string; artifact_sha256: Record<string, string>; release_manifest_sha256: string; visual_quality_report_sha256?: string | null }; visual_quality?: VideoVisualQualityReport | null; count: number; reviews: VisualReviewRecord[] }>;
  visualRevisionBrief(initiativeId: string, versionId: string): Promise<{ api_version: number; active: boolean; brief: VisualRevisionBrief | null; can_auto_modify_source: false; new_version_created: false }>;
  submitVisualReview(initiativeId: string, versionId: string, payload: VisualReviewPayload): Promise<{ api_version: number; identity_assurance: string; record: VisualReviewRecord; append_only: true; digital_signature: false }>;
  subtitleReview(initiativeId: string, versionId: string): Promise<{ api_version: number; review: SubtitleReviewDocument }>;
  approveSubtitleReview(initiativeId: string, versionId: string, payload: { reviewed_segment_indexes: number[]; corrections: Array<{ segment_index: number; corrected_text: string }>; reviewer: string; reviewer_id: string; reviewer_role: string; rationale: string; evidence_refs: string[]; confirmation: string }): Promise<{ api_version: number; product: { initiative_id: string; version_id: string; validated: boolean; status: string; quality_gate?: ProductQualityGate } }>;  createVideoProduct(payload: { mode?: "local_assets" | "prompt_to_video"; source?: string; title: string; prompt?: string; negative_prompt?: string; duration_seconds?: number; steps?: number; seed?: number; creator_initiative_id?: string; evolution_cycle_id?: string; audio?: string; audio_mode?: "music" | "narration"; aspect_ratio?: "16:9" | "9:16" | "1:1"; fps?: number; image_seconds?: number; encoder?: "auto" | "amf" | "software"; motion_profile?: "none" | "brand_intro_outro" | "brand_scene_transitions" }): Promise<{ api_version: number; job: { id: string; status: string } }>;  videoJob(id: string): Promise<{ api_version: number; job: VideoJobRecord }>;
  submitInspiration(value: { content: string; allow_external_research: boolean; title?: string; tags?: string[]; research_domains?: string[]; source?: "chatgpt_daily_inspiration" | "muse_local_dashboard" | "user_manual" }): Promise<{ api_version: number; inspiration: InspirationRecord }>;
  evolveInspiration(payload?: { inspiration_id?: string; enqueue_video?: boolean; aspect_ratios?: Array<"9:16" | "16:9"> }): Promise<{ api_version: number; evolution: InspirationEvolutionResult }>; 
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

export interface CompanionRemoteClientOptions {
  baseUrl: string;
  fetchImpl?: typeof fetch;
  cryptoImpl?: Crypto;
  timeoutMs?: number;
}

export interface CompanionRemoteEvent {
  id: string;
  category: string;
  event: string;
  created_at: number;
  error_code?: string | null;
}

export interface CompanionRemoteStatus {
  api_version: 1;
  host: { status: string; read_only: boolean };
  commander: { queue_counts: Record<string, number> };
  workers: { active: number; stale: number };
  device: { device_id: string; permissions: string[] };
  generated_at: number;
}

export interface CompanionRemoteEventBatch {
  api_version: 1;
  reset: boolean;
  events: CompanionRemoteEvent[];
}

export interface CompanionRemoteClient {
  requestPairing(input: { challengeId: string; code: string; deviceName?: string }): Promise<{ requestId: string; status: string; expiresAt: number }>;
  claim(): Promise<{ deviceId: string; permissions: string[] }>;
  getStatus(): Promise<CompanionRemoteStatus>;
  getEvents(cursor?: string): Promise<CompanionRemoteEventBatch>;
  forget(): void;
  readonly baseUrl: string;
  readonly isPaired: boolean;
  readonly hasPendingPairing: boolean;
  readonly pendingRequestId: string | null;
  readonly deviceId: string | null;
  readonly permissions: string[];
}

export const createCompanionRemoteClient = createRemoteCompanionClient as
  (options: CompanionRemoteClientOptions) => CompanionRemoteClient;

export const resolveDesktopConnection = async (): Promise<ApiConnection> =>
  (await resolveConnection()) as ApiConnection;
