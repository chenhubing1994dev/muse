const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);

export function createApiClient({ baseUrl, token, fetchImpl = globalThis.fetch, timeoutMs = 4000 }) {
  if (typeof baseUrl !== "string" || typeof token !== "string" || token.length < 32) {
    throw new Error("本地 API 会话信息无效");
  }
  if (!Number.isInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 30000) {
    throw new RangeError("API 超时时间必须介于 100 和 30000 毫秒之间");
  }
  const origin = new URL(baseUrl);
  const sameOriginDashboard = globalThis.window?.location?.origin === origin.origin;
  if (origin.protocol !== "http:" || (!LOOPBACK_HOSTS.has(origin.hostname) && !sameOriginDashboard)
      || origin.username || origin.password || origin.pathname !== "/" || origin.search || origin.hash) {
    throw new Error("Muse API 只允许连接本机或已配对的同源控制台");
  }
  if (typeof fetchImpl !== "function") throw new Error("当前环境不支持 Fetch API");
  async function request(path, { method = "GET", body, requestTimeoutMs } = {}) {
    const controller = new AbortController();
    const effectiveTimeout = (Number.isInteger(requestTimeoutMs) && requestTimeoutMs >= 100 && requestTimeoutMs <= 120000)
      ? requestTimeoutMs
      : timeoutMs;
    const timeout = setTimeout(() => controller.abort(), effectiveTimeout);
    const headers = { Authorization: "Bearer " + token, Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    try {
      const response = await fetchImpl(new URL(path, origin), {
        method, headers, body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store", redirect: "error", signal: controller.signal,
      });
      if (!response.ok) {
        let detail = "";
        try { detail = (await response.json())?.error || ""; } catch {}
        throw new Error(detail || "本地 API 返回 HTTP " + response.status);
      }
      const payload = await response.json();
      if (!payload || payload.api_version !== 1) throw new Error("本地 API 版本不兼容");
      return payload;
    } catch (error) {
      if (error?.name === "AbortError") throw new Error("连接本地 API 超时");
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }
  const get = (path) => request(path);
  async function streamEvents({ lastEventId = "", signal, onEvent }) {
    if (typeof onEvent !== "function") throw new TypeError("事件回调必须是函数");
    const headers = { Authorization: "Bearer " + token, Accept: "text/event-stream" };
    if (lastEventId) {
      if (typeof lastEventId !== "string" || !/^[a-f0-9-]{16,64}$/.test(lastEventId)) throw new Error("事件游标无效");
      headers["Last-Event-ID"] = lastEventId;
    }
    const response = await fetchImpl(new URL("/api/v1/events/stream", origin), {
      method: "GET", headers, cache: "no-store", redirect: "error", signal,
    });
    if (!response.ok) throw new Error("事件流返回 HTTP " + response.status);
    if (!(response.headers?.get?.("content-type") || "").toLowerCase().startsWith("text/event-stream")) throw new Error("事件流响应类型不兼容");
    const body = await response.text();
    let cursor = lastEventId;
    for (const frame of body.split(/\r?\n\r?\n/)) {
      if (!frame || frame.startsWith(":")) continue;
      let id = "";
      let eventName = "message";
      const data = [];
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith("id: ")) id = line.slice(4).trim();
        else if (line.startsWith("event: ")) eventName = line.slice(7).trim();
        else if (line.startsWith("data: ")) data.push(line.slice(6));
      }
      if (eventName === "muse-reset") {
        cursor = "";
        continue;
      }
      if (eventName !== "muse-progress" || !id || !/^[a-f0-9-]{16,64}$/.test(id) || !data.length) continue;
      const payload = JSON.parse(data.join("\n"));
      if (!payload || typeof payload.category !== "string" || typeof payload.event !== "string" || typeof payload.created_at !== "string") continue;
      onEvent({ id, ...payload });
      cursor = id;
    }
    return cursor;
  }
  async function productBlob(initiativeId, versionId, action) {
    if (typeof initiativeId !== "string" || !/^[0-9a-f]{32}$/.test(initiativeId)
        || typeof versionId !== "string" || !/^[0-9a-f]{20}$/.test(versionId)
        || !["download", "preview", "video"].includes(action)) {
      throw new Error("产品编号无效");
    }
    const response = await fetchImpl(new URL(
      "/api/v1/products/" + initiativeId + "/" + versionId + "/" + action, origin
    ), {
      headers: { Authorization: "Bearer " + token },
      cache: "no-store", redirect: "error",
    });
    if (!response.ok) throw new Error("产品读取失败，HTTP " + response.status);
    return response.blob();
  }
  return {
    streamEvents,

    health: () => get("/api/v1/health"),
    agents: () => get("/api/v1/agents?limit=200"),
    feedback: (limit = 20) => {
      if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new RangeError("反馈数量必须介于 1 和 100 之间");
      return get(`/api/v1/feedback?limit=${limit}`);
    },
    improvements: () => get("/api/v1/improvements"),
    inspirations: (limit = 50) => get(`/api/v1/inspirations?limit=${limit}`),
    selfImprovement: () => get("/api/v1/self-improvement"),    artifacts: (limit = 50) => get(`/api/v1/artifacts?limit=${limit}`),
    products: (limit = 50) => {
      if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new RangeError("产品数量必须介于 1 和 100 之间");
      return get(`/api/v1/products?limit=${limit}`);
    },
    videoAttempts: () => get("/api/v1/video-attempts"),
    initiativeTimeline: (initiativeId, limit = 100) => {
      if (typeof initiativeId !== "string" || !/^[0-9a-f]{32}$/.test(initiativeId)) throw new Error("创作项目编号无效");
      if (!Number.isInteger(limit) || limit < 1 || limit > 200) {
        throw new RangeError("时间线数量必须介于 1 和 200 之间");
      }
      return get(`/api/v1/initiatives/${initiativeId}/timeline?limit=${limit}`);
    },
    initiativePerformance: (initiativeId) => {
      if (typeof initiativeId !== "string" || !/^[0-9a-f]{32}$/.test(initiativeId)) throw new Error("创作项目编号无效");
      return get(`/api/v1/initiatives/${initiativeId}/performance`);
    },
    recordInitiativePerformance: (initiativeId, payload) => {
      if (typeof initiativeId !== "string" || !/^[0-9a-f]{32}$/.test(initiativeId)) throw new Error("创作项目编号无效");
      if (!payload || typeof payload !== "object" || !/^[A-Za-z0-9._:-]{8,128}$/.test(payload.idempotency_key || "")) throw new Error("表现快照幂等键无效");
      if (!["youtube", "tiktok", "douyin", "instagram", "facebook", "bilibili", "other"].includes(payload.platform)) throw new Error("平台无效");
      if (!["manual_entry", "official_analytics"].includes(payload.source)) throw new Error("数据来源无效");
      if (!payload.metrics || typeof payload.metrics !== "object" || Array.isArray(payload.metrics)) throw new Error("表现指标无效");
      return request(`/api/v1/initiatives/${initiativeId}/performance`, { method: "POST", body: payload });
    },
    knowledgeNode: (nodeId) => {
      if (typeof nodeId !== "string" || !/^[0-9a-f]{32}$/.test(nodeId)) throw new Error("知识节点编号无效");
      return get(`/api/v1/knowledge/nodes/${nodeId}`);
    },
    knowledgeClaimDossier: (claimId, evidenceLimit = 12) => {
      if (typeof claimId !== "string" || !/^[0-9a-f]{32}$/.test(claimId)) throw new Error("Claim 编号无效");
      if (!Number.isInteger(evidenceLimit) || evidenceLimit < 1 || evidenceLimit > 100) throw new RangeError("证据条数必须介于 1 和 100 之间");
      return get(`/api/v1/knowledge/claims/${claimId}/dossier?evidence_limit=${evidenceLimit}`);
    },
    knowledgeGraph: ({ limit = 160, nodeType = null, q = "" } = {}) => {
      if (!Number.isInteger(limit) || limit < 1 || limit > 250) {
        throw new RangeError("星图节点数量必须介于 1 和 250 之间");
      }
      const params = new URLSearchParams({ limit: String(limit) });
      if (nodeType) {
        if (typeof nodeType !== "string" || !["source", "idea", "experiment", "claim", "decision", "failure"].includes(nodeType)) {
          throw new Error("知识节点类型无效");
        }
        params.set("node_type", nodeType);
      }
      if (typeof q === "string" && q.trim()) {
        if (q.trim().length > 120) throw new Error("搜索关键词不能超过 120 个字符");
        params.set("q", q.trim());
      }
      return get(`/api/v1/knowledge/graph?${params.toString()}`);
    },
    prometheusCore: () => get("/api/v1/prometheus/core"),
    galaxy: () => get("/api/v1/galaxy"),
    previewRoute: (goal) => {
      if (typeof goal !== "string" || !goal.trim() || goal.trim().length > 500) {
        throw new Error("路由预演目标必须为 1-500 个字符");
      }
      return request("/api/v1/galaxy/route-preview", { method: "POST", body: { goal: goal.trim() } });
    },
    celestialCatalog: () => get("/api/v1/metaphysics/celestial"),
    castYijing: (payload) => {
      if (!payload || typeof payload !== "object") throw new Error("易经推演参数无效");
      const mode = payload.mode ?? "seeded_cast";
      if (!["seeded_cast", "manual_lines", "trigram_pair"].includes(mode)) {
        throw new Error("易经推演模式无效");
      }
      return request("/api/v1/metaphysics/yijing", { method: "POST", body: { ...payload, mode } });
    },
    videoEngines: () => get("/api/v1/video-engines"),
    gpuResources: () => get("/api/v1/gpu-resources"),
    daemonStatus: () => get("/api/v1/daemon/status"),
    daemonControl: (action) => {
      if (!["start", "pause", "resume", "stop", "once"].includes(action)) {
        throw new Error("运行时控制动作无效");
      }
      return request("/api/v1/daemon/" + action, { method: "POST" });
    },
    // ── Muse 视频创作工作室 ──
    studioStyles: () => get("/api/v1/studio/styles"),
    studioJobs: (limit = 30) => {
      if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new RangeError("任务数量必须介于 1 和 100 之间");
      return get(`/api/v1/studio/render?limit=${limit}`);
    },
    studioJob: (id) => {
      if (typeof id !== "string" || !/^[0-9a-f]{32}$/.test(id)) throw new Error("渲染任务编号无效");
      return get(`/api/v1/studio/render/${id}`);
    },
    studioSearchMedia: (payload) => request("/api/v1/studio/search-media", { method: "POST", body: payload, requestTimeoutMs: 30000 }),
    studioGenerateScript: (payload) => request("/api/v1/studio/generate-script", { method: "POST", body: payload, requestTimeoutMs: 30000 }),
    studioPhilosophyInject: (payload) => request("/api/v1/studio/philosophy-inject", { method: "POST", body: payload }),
    studioRender: (payload) => request("/api/v1/studio/render", { method: "POST", body: payload, requestTimeoutMs: 60000 }),
    pipelineRecipes: () => get("/api/v1/pipelines/recipes"),
    pipelineJobs: (limit = 30) => {
      if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new RangeError("任务数量必须介于 1 和 100 之间");
      return get(`/api/v1/pipelines/jobs?limit=${limit}`);
    },
    pipelineJob: (id) => {
      if (typeof id !== "string" || !/^[0-9a-f]{32}$/.test(id)) throw new Error("流水线任务编号无效");
      return get(`/api/v1/pipelines/jobs/${id}`);
    },
    submitPipeline: (payload) => {
      if (!payload || typeof payload.recipe_id !== "string" || !payload.recipe_id) {
        throw new Error("缺少流水线配方编号");
      }
      return request("/api/v1/pipelines/submit", { method: "POST", body: payload });
    },
    downloadProduct: (initiativeId, versionId) => productBlob(initiativeId, versionId, "download"),
    previewProduct: (initiativeId, versionId) => productBlob(initiativeId, versionId, "preview"),
    downloadVideo: (initiativeId, versionId) => productBlob(initiativeId, versionId, "video"),
    subtitleReview: (initiativeId, versionId) => {
      if (typeof initiativeId !== "string" || !/^[0-9a-f]{32}$/.test(initiativeId)
          || typeof versionId !== "string" || !/^[0-9a-f]{20}$/.test(versionId)) {
        throw new Error("产品编号无效");
      }
      return get("/api/v1/products/" + initiativeId + "/" + versionId + "/subtitle-review");
    },
    approveSubtitleReview: (initiativeId, versionId, payload) => {
      if (typeof initiativeId !== "string" || !/^[0-9a-f]{32}$/.test(initiativeId)
          || typeof versionId !== "string" || !/^[0-9a-f]{20}$/.test(versionId)) {
        throw new Error("产品编号无效");
      }
      return request("/api/v1/products/" + initiativeId + "/" + versionId + "/subtitle-review/approve", {
        method: "POST", body: payload,
      });
    },
    createVideoProduct: (payload) => request("/api/v1/video-products", { method: "POST", body: payload }),
    videoJob: (id) => {
      if (typeof id !== "string" || !/^[0-9a-f]{32}$/.test(id)) throw new Error("视频任务编号无效");
      return get(`/api/v1/video-jobs/${id}`);
    },
    submitInspiration: (inspiration) => request("/api/v1/inspirations", { method: "POST", body: inspiration }),
    evolveInspiration: (payload = {}) => {
      if (payload.inspiration_id !== undefined && (typeof payload.inspiration_id !== "string" || !/^[0-9a-f]{32}$/.test(payload.inspiration_id))) {
        throw new Error("灵感编号无效");
      }
      return request("/api/v1/inspirations/evolve", {
        method: "POST",
        body: payload,
        requestTimeoutMs: 90000,
      });
    },
    downloadArtifact: async (name) => {
      if (typeof name !== "string" || !/^[A-Za-z0-9_.-]{1,180}\.md$/.test(name)) throw new Error("成品文件名无效");
      const response = await fetchImpl(new URL("/api/v1/artifacts/" + encodeURIComponent(name), origin), {
        headers: { Authorization: "Bearer " + token }, cache: "no-store", redirect: "error",
      });
      if (!response.ok) throw new Error("下载成品失败，HTTP " + response.status);
      return response.blob();
    },

    improvementDetail: (id) => {
      if (typeof id !== "string" || !/^[0-9a-f]{32}$/.test(id)) throw new Error("提案编号无效");
      return get(`/api/v1/improvements/${id}`);
    },
    submitInitiativeFeedback: (initiativeId, review) => {
      if (typeof initiativeId !== "string" || !/^[0-9a-f]{32}$/.test(initiativeId)) {
        throw new Error("创作项目编号无效");
      }
      if (!review || typeof review !== "object" || !["accept", "revise", "reject"].includes(review.decision)) {
        throw new Error("评审决定无效");
      }
      if (typeof review.feedback !== "string" || !review.feedback.trim() || review.feedback.length > 8000) {
        throw new Error("反馈内容必须为 1-8000 个字符");
      }
      if (review.evolution_cycle_id !== undefined
          && (typeof review.evolution_cycle_id !== "string" || !/^cycle_[0-9a-f]{32}$/.test(review.evolution_cycle_id))) {
        throw new Error("Evolution Cycle 编号无效");
      }
      const body = {
        feedback: review.feedback.trim(), decision: review.decision,
        reviewer: "creator", source: "dashboard",
      };
      if (review.evolution_cycle_id) body.evolution_cycle_id = review.evolution_cycle_id;
      return request("/api/v1/initiatives/" + initiativeId + "/feedback", { method: "POST", body });
    },
    submitFeedback: (feedback) => request("/api/v1/feedback", { method: "POST", body: feedback }),
    runRadarScan: () => request("/api/v1/radar/scan", { method: "POST" }),
    overview: () => get("/api/v1/overview"),
    mempalaceStatus: () => get("/api/v1/mempalace/status"),
    events: (limit = 30) => {
      if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
        throw new RangeError("事件数量必须介于 1 和 100 之间");
      }
      return get(`/api/v1/events?limit=${limit}`);
    },
  };
}

export async function resolveDesktopConnection() {
  const invoke = globalThis.window?.__TAURI__?.core?.invoke;
  if (typeof invoke === "function") return invoke("get_status_connection");
  const localConnection = globalThis.window?.__MUSE_LOCAL_CONNECTION__;
  if (localConnection && typeof localConnection === "object") return localConnection;
  throw new Error("请通过 Muse 本地启动器打开控制台；浏览器原型不会保存或请求 API 密钥");
}
