const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);

const COMPANION_EMPTY_BODY = new Uint8Array();

function companionHex(bytes) {
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function companionBase64Url(bytes) {
  const binary = Array.from(bytes, (value) => String.fromCharCode(value)).join("");
  return globalThis.btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function isPrivateCompanionIPv4(host) {
  const octets = host.split(".");
  if (octets.length !== 4 || octets.some((part) => !/^\d{1,3}$/.test(part) || Number(part) > 255)) {
    return false;
  }
  const [first, second] = octets.map(Number);
  return first === 0 || first === 10 || first === 127
    || (first === 169 && second === 254)
    || (first === 172 && second >= 16 && second <= 31)
    || (first === 192 && second === 168)
    || (first === 100 && second >= 64 && second <= 127)
    || first >= 224;
}

function parseCompanionIPv6(hostname) {
  let host = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (!host.includes(":") || host.includes("%")) return null;
  if (host.includes(".")) {
    const lastColon = host.lastIndexOf(":");
    if (lastColon < 0) return null;
    const octets = host.slice(lastColon + 1).split(".");
    if (octets.length !== 4 || octets.some((part) => !/^\d{1,3}$/.test(part) || Number(part) > 255)) {
      return null;
    }
    const [a, b, c, d] = octets.map(Number);
    host = host.slice(0, lastColon + 1) + ((a << 8) | b).toString(16) + ":" + ((c << 8) | d).toString(16);
  }

  const halves = host.split("::");
  if (halves.length > 2) return null;
  const compressed = halves.length === 2;
  const left = halves[0].split(":").filter(Boolean);
  const right = compressed ? halves[1].split(":").filter(Boolean) : [];
  const explicitCount = left.length + right.length;
  if (compressed ? explicitCount >= 8 : explicitCount !== 8) return null;
  const groups = compressed
    ? [...left, ...new Array(8 - explicitCount).fill("0"), ...right]
    : left;
  if (groups.length !== 8 || groups.some((part) => !/^[a-f0-9]{1,4}$/.test(part))) return null;
  return groups.map((part) => Number.parseInt(part, 16));
}

function isPrivateCompanionHost(hostname) {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
  if (host === "localhost" || /\.(?:localhost|local|lan|home|internal)$/.test(host)) return true;
  if (host.includes(":")) {
    const words = parseCompanionIPv6(host);
    if (!words) return false;
    const allZero = words.every((word) => word === 0);
    const loopback = words.slice(0, 7).every((word) => word === 0) && words[7] === 1;
    if (allZero || loopback
        || (words[0] & 0xfe00) === 0xfc00
        || (words[0] & 0xffc0) === 0xfe80
        || (words[0] & 0xff00) === 0xff00) {
      return true;
    }

    const mappedIPv4 = words.slice(0, 5).every((word) => word === 0) && words[5] === 0xffff;
    const compatibleIPv4 = words.slice(0, 6).every((word) => word === 0);
    if (mappedIPv4 || compatibleIPv4) {
      const a = words[6] >>> 8;
      const b = words[6] & 0xff;
      const c = words[7] >>> 8;
      const d = words[7] & 0xff;
      return isPrivateCompanionIPv4([a, b, c, d].join("."));
    }
    return false;
  }

  return isPrivateCompanionIPv4(host);
}

export function createCompanionRemoteClient({
  baseUrl,
  fetchImpl = globalThis.fetch,
  cryptoImpl = globalThis.crypto,
  timeoutMs = 10000,
} = {}) {
  if (typeof baseUrl !== "string" || typeof fetchImpl !== "function"
      || !cryptoImpl?.subtle || typeof cryptoImpl.getRandomValues !== "function") {
    throw new Error("Companion 连接参数或安全加密能力不可用");
  }
  if (!Number.isInteger(timeoutMs) || timeoutMs < 500 || timeoutMs > 60000) {
    throw new RangeError("Companion 超时时间必须介于 500 和 60000 毫秒之间");
  }
  let origin;
  try { origin = new URL(baseUrl); } catch { throw new Error("Host 地址格式无效"); }
  if (origin.protocol !== "https:" || origin.username || origin.password
      || origin.pathname !== "/" || origin.search || origin.hash) {
    throw new Error("Companion 只允许经过系统证书与主机名验证的 HTTPS Host 地址");
  }
  if (isPrivateCompanionHost(origin.hostname)) {
    throw new Error("跨网络 Companion 必须使用公网可达的 HTTPS 域名；localhost 与局域网地址不可用");
  }

  const encoder = new TextEncoder();
  let claimSecret = null;
  let pendingRequestId = null;
  let deviceId = null;
  let deviceToken = null;
  let sessionId = null;
  let permissions = [];

  const randomBytes = (length) => {
    const bytes = new Uint8Array(length);
    cryptoImpl.getRandomValues(bytes);
    return bytes;
  };
  const digestHex = async (bytes) => companionHex(new Uint8Array(await cryptoImpl.subtle.digest("SHA-256", bytes)));

  async function requestJson(path, { method = "GET", body, headers = {} } = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    const requestHeaders = { Accept: "application/json", ...headers };
    if (body !== undefined) requestHeaders["Content-Type"] = "application/json";
    try {
      const response = await fetchImpl(new URL(path, origin), {
        method,
        headers: requestHeaders,
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store",
        credentials: "omit",
        mode: "cors",
        redirect: "error",
        referrerPolicy: "no-referrer",
        signal: controller.signal,
      });
      if (!response.ok) {
        let detail = "";
        try { detail = (await response.json())?.error || ""; } catch {}
        throw new Error(detail || "Companion HTTP " + response.status);
      }
      const payload = await response.json();
      if (!payload || payload.api_version !== 1) throw new Error("Companion 协议版本不兼容");
      return payload;
    } catch (error) {
      if (error?.name === "AbortError") throw new Error("Companion HTTPS 请求超时");
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }

  async function requestPairing({ challengeId, code, deviceName = "Muse Companion" } = {}) {
    if (deviceId || pendingRequestId) throw new Error("已有配对会话；请先断开后再发起新配对");
    if (typeof challengeId !== "string" || !/^[a-f0-9]{32}$/.test(challengeId)) {
      throw new Error("Host 配对挑战编号无效");
    }
    if (typeof code !== "string" || !/^[A-Za-z0-9_-]{22,128}$/.test(code)) {
      throw new Error("Host 一次性挑战码格式无效");
    }
    if (typeof deviceName !== "string" || !deviceName.trim() || deviceName.trim().length > 64
        || /[\u0000-\u001f\u007f]/.test(deviceName)) {
      throw new Error("设备名称必须为 1-64 个可打印字符");
    }
    claimSecret = companionBase64Url(randomBytes(32));
    const fingerprint = await digestHex(randomBytes(32));
    let response;
    try {
      response = await requestJson("/api/v1/companion/pairing/request", {
        method: "POST",
        body: {
          challenge_id: challengeId,
          code,
          device_name: deviceName.trim(),
          device_fingerprint: fingerprint,
          claim_secret: claimSecret,
        },
      });
    } catch (error) {
      claimSecret = null;
      throw error;
    }
    pendingRequestId = response.pairing?.request_id;
    if (typeof pendingRequestId !== "string" || !/^[a-f0-9]{32}$/.test(pendingRequestId)) {
      claimSecret = null;
      pendingRequestId = null;
      throw new Error("Host 返回的配对请求编号无效");
    }
    return {
      requestId: pendingRequestId,
      status: response.pairing.status,
      expiresAt: response.pairing.expires_at,
    };
  }
  async function claim() {
    if (!pendingRequestId || !claimSecret) throw new Error("当前没有待领取的配对请求");
    const response = await requestJson("/api/v1/companion/pairing/claim", {
      method: "POST",
      body: { request_id: pendingRequestId, claim_secret: claimSecret },
    });
    const device = response.device;
    if (!device || !/^[a-f0-9]{32}$/.test(device.device_id || "")
        || typeof device.token !== "string" || device.token.length < 40
        || !Array.isArray(device.permissions)) {
      throw new Error("Host 返回的设备凭据格式无效");
    }
    deviceId = device.device_id;
    deviceToken = device.token;
    permissions = [...device.permissions];
    sessionId = companionHex(randomBytes(16));
    claimSecret = null;
    pendingRequestId = null;
    return { deviceId, permissions: [...permissions] };
  }

  async function signedGet(path, cursor = "") {
    if (!deviceId || !deviceToken || !sessionId) throw new Error("Companion 尚未完成配对");
    if (cursor && (typeof cursor !== "string" || !/^[a-f0-9-]{16,64}$/.test(cursor))) {
      throw new Error("事件游标无效");
    }
    const nonce = companionBase64Url(randomBytes(24));
    const timestamp = Date.now();
    const bodyDigest = await digestHex(COMPANION_EMPTY_BODY);
    const canonical = [
      "muse-companion-v1", deviceId, sessionId, nonce, String(timestamp),
      "GET", path, cursor, bodyDigest,
    ].join("\n");
    const key = await cryptoImpl.subtle.importKey(
      "raw", encoder.encode(deviceToken), { name: "HMAC", hash: "SHA-256" },
      false, ["sign"],
    );
    const signature = companionHex(new Uint8Array(
      await cryptoImpl.subtle.sign("HMAC", key, encoder.encode(canonical)),
    ));
    const headers = {
      "X-Muse-Device-ID": deviceId,
      "X-Muse-Device-Token": deviceToken,
      "X-Muse-Session-ID": sessionId,
      "X-Muse-Nonce": nonce,
      "X-Muse-Timestamp": String(timestamp),
      "X-Muse-Signature": signature,
    };
    if (cursor) headers["Last-Event-ID"] = cursor;
    return requestJson(path, { headers });
  }

  return {
    requestPairing,
    claim,
    getStatus: () => signedGet("/api/v1/companion/status"),
    getEvents: (cursor = "") => signedGet("/api/v1/companion/events", cursor),
    forget() {
      claimSecret = null;
      pendingRequestId = null;
      deviceId = null;
      deviceToken = null;
      sessionId = null;
      permissions = [];
    },
    get baseUrl() { return origin.origin; },
    get isPaired() { return Boolean(deviceId && deviceToken); },
    get hasPendingPairing() { return Boolean(pendingRequestId && claimSecret); },
    get pendingRequestId() { return pendingRequestId; },
    get deviceId() { return deviceId; },
    get permissions() { return [...permissions]; },
  };
}

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
        || !["download", "preview", "video", "contact-sheet"].includes(action)) {
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
    companionPairings: (limit = 20) => {
      if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new RangeError("配对请求数量必须介于 1 和 100 之间");
      return get(`/api/v1/companion/pairings?limit=${limit}`);
    },
    companionDevices: (limit = 50) => {
      if (!Number.isInteger(limit) || limit < 1 || limit > 200) throw new RangeError("设备数量必须介于 1 和 200 之间");
      return get(`/api/v1/companion/devices?limit=${limit}`);
    },
    companionAudit: (limit = 100) => {
      if (!Number.isInteger(limit) || limit < 1 || limit > 500) throw new RangeError("审计记录数量必须介于 1 和 500 之间");
      return get(`/api/v1/companion/audit?limit=${limit}`);
    },
    startCompanionPairing: (expiresInSeconds = 180) => {
      if (!Number.isInteger(expiresInSeconds) || expiresInSeconds < 60 || expiresInSeconds > 600) {
        throw new RangeError("配对挑战有效期必须介于 60 和 600 秒之间");
      }
      return request("/api/v1/companion/pairing/start", {
        method: "POST", body: { expires_in_seconds: expiresInSeconds },
      });
    },
    approveCompanionPairing: (requestId) => {
      if (typeof requestId !== "string" || !/^[a-f0-9]{32}$/.test(requestId)) throw new Error("配对请求编号无效");
      return request(`/api/v1/companion/pairings/${requestId}/approve`, { method: "POST", body: {} });
    },
    denyCompanionPairing: (requestId) => {
      if (typeof requestId !== "string" || !/^[a-f0-9]{32}$/.test(requestId)) throw new Error("配对请求编号无效");
      return request(`/api/v1/companion/pairings/${requestId}/deny`, { method: "POST", body: {} });
    },
    revokeCompanionDevice: (deviceId) => {
      if (typeof deviceId !== "string" || !/^[a-f0-9]{32}$/.test(deviceId)) throw new Error("设备编号无效");
      return request(`/api/v1/companion/devices/${deviceId}/revoke`, { method: "POST", body: {} });
    },

    health: () => get("/api/v1/health"),
    agents: () => get("/api/v1/agents?limit=200"),
    contextualInquiryReviews: (limit = 50) => {
      if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new RangeError("审查结果数量必须介于 1 和 100 之间");
      return get(`/api/v1/contextual-inquiry-reviews?limit=${limit}`);
    },
    contextualInquiryReviewResult: (taskId) => {
      if (typeof taskId !== "string" || !/^[0-9a-f]{32}$/.test(taskId)) throw new Error("个案审查任务编号无效");
      return get(`/api/v1/contextual-inquiry-reviews/${taskId}`);
    },
    feedback: (limit = 20) => {
      if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new RangeError("反馈数量必须介于 1 和 100 之间");
      return get(`/api/v1/feedback?limit=${limit}`);
    },
    improvements: () => get("/api/v1/improvements"),
    inspirations: (limit = 50) => get(`/api/v1/inspirations?limit=${limit}`),
    selfImprovement: () => get("/api/v1/self-improvement"),    artifacts: (limit = 50) => get(`/api/v1/artifacts?limit=${limit}`),
    products: (limit = 50) => {
      if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new RangeError("产品数量必须介于 1 和 100 之间");
      // Product listing verifies package hashes on disk and routinely needs
      // several seconds on a large library; the shared 4s default is too tight.
      return request(`/api/v1/products?limit=${limit}`, { requestTimeoutMs: 20000 });
    },
    videoAttempts: () => get("/api/v1/video-attempts"),
    videoRecipeTrace: (attemptId) => {
      if (typeof attemptId !== "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(attemptId)) throw new Error("视频任务编号无效");
      return get(`/api/v1/video-recipe-traces/${attemptId}`);
    },
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
    videoContactSheet: (initiativeId, versionId) => productBlob(initiativeId, versionId, "contact-sheet"),
    visualRevisionBrief: (initiativeId, versionId) => {
      if (typeof initiativeId !== "string" || !/^[0-9a-f]{32}$/.test(initiativeId)
          || typeof versionId !== "string" || !/^[0-9a-f]{20}$/.test(versionId)) {
        throw new Error("产品编号无效");
      }
      return get("/api/v1/products/" + initiativeId + "/" + versionId + "/revision-brief");
    },
    visualReviews: (initiativeId, versionId) => {
      if (typeof initiativeId !== "string" || !/^[0-9a-f]{32}$/.test(initiativeId)
          || typeof versionId !== "string" || !/^[0-9a-f]{20}$/.test(versionId)) {
        throw new Error("产品编号无效");
      }
      return get("/api/v1/products/" + initiativeId + "/" + versionId + "/visual-review");
    },
    submitVisualReview: (initiativeId, versionId, payload) => {
      if (typeof initiativeId !== "string" || !/^[0-9a-f]{32}$/.test(initiativeId)
          || typeof versionId !== "string" || !/^[0-9a-f]{20}$/.test(versionId)) {
        throw new Error("产品编号无效");
      }
      return request("/api/v1/products/" + initiativeId + "/" + versionId + "/visual-review", {
        method: "POST", body: payload,
      });
    },
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
    systemDashboard: () => get("/api/v1/system-dashboard"),
    fusionJobs: (limit = 20) => {
      if (!Number.isInteger(limit) || limit < 1 || limit > 50) throw new RangeError("融合记录数量必须介于 1 和 50 之间");
      return get(`/api/v1/fusion/jobs?limit=${limit}`);
    },
    submitFusion: (payload) => {
      if (!payload || typeof payload !== "object") throw new Error("融合来源无效");
      const hasUrl = typeof payload.source_url === "string" && payload.source_url.trim().length > 0;
      const hasArchive = typeof payload.archive_base64 === "string" && payload.archive_base64.length > 0;
      if (hasUrl === hasArchive) throw new Error("请只提供一个 GitHub 仓库地址，或 GitHub/GitLab ZIP 压缩包");
      if (hasUrl && payload.source_url.length > 2048) throw new Error("源代码地址不能超过 2048 个字符");
      if (hasArchive && payload.archive_base64.length > 11184812) throw new Error("ZIP 压缩包超过 8 MiB 上传限制");
      return request("/api/v1/fusion/submit", { method: "POST", body: payload, requestTimeoutMs: 120000 });
    },
    approveFusion: (fusionId) => {
      if (typeof fusionId !== "string" || !/^[a-f0-9]{32}$/.test(fusionId)) throw new Error("融合编号无效");
      return request("/api/v1/fusion/jobs/" + fusionId + "/approve", {
        method: "POST", body: { confirmation: "FUSE-CAPABILITIES" },
      });
    },
    rollbackFusion: (fusionId) => {
      if (typeof fusionId !== "string" || !/^[a-f0-9]{32}$/.test(fusionId)) throw new Error("融合编号无效");
      return request("/api/v1/fusion/jobs/" + fusionId + "/rollback", {
        method: "POST", body: { confirmation: "ROLLBACK-FUSION" },
      });
    },
    plugins: (limit = 100) => {
      if (!Number.isInteger(limit) || limit < 1 || limit > 500) throw new RangeError("插件数量必须介于 1 和 500 之间");
      return get(`/api/v1/plugins?limit=${limit}`);
    },
    pluginAudit: (limit = 100) => {
      if (!Number.isInteger(limit) || limit < 1 || limit > 500) throw new RangeError("插件审计数量必须介于 1 和 500 之间");
      return get(`/api/v1/plugins/audit?limit=${limit}`);
    },
    discoverPlugin: (manifest) => {
      if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) throw new Error("插件清单必须是 JSON 对象");
      if (new TextEncoder().encode(JSON.stringify(manifest)).length > 65536) throw new RangeError("插件清单不能超过 64 KiB");
      return request("/api/v1/plugins/discover", { method: "POST", body: manifest });
    },
    approvePlugin: (pluginId) => {
      if (typeof pluginId !== "string" || pluginId.length > 96 || !/^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$/.test(pluginId)) throw new Error("插件编号无效");
      return request("/api/v1/plugins/" + pluginId + "/approve", { method: "POST", body: { confirmation: "APPROVE-PLUGIN" } });
    },
    enablePlugin: (pluginId) => {
      if (typeof pluginId !== "string" || pluginId.length > 96 || !/^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$/.test(pluginId)) throw new Error("插件编号无效");
      return request("/api/v1/plugins/" + pluginId + "/enable", { method: "POST", body: { confirmation: "ENABLE-PLUGIN" } });
    },
    disablePlugin: (pluginId) => {
      if (typeof pluginId !== "string" || pluginId.length > 96 || !/^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$/.test(pluginId)) throw new Error("插件编号无效");
      return request("/api/v1/plugins/" + pluginId + "/disable", { method: "POST", body: { confirmation: "DISABLE-PLUGIN" } });
    },
    requestPluginCapability: (pluginId, capabilityId, payload) => {
      if (typeof pluginId !== "string" || pluginId.length > 96 || !/^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$/.test(pluginId)) throw new Error("插件编号无效");
      if (typeof capabilityId !== "string" || capabilityId.length > 96 || !/^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$/.test(capabilityId)) throw new Error("插件能力编号无效");
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("能力请求 payload 必须是 JSON 对象");
      if (new TextEncoder().encode(JSON.stringify(payload)).length > 65536) throw new RangeError("能力请求不能超过 64 KiB");
      return request("/api/v1/plugins/" + pluginId + "/capabilities/" + capabilityId + "/request", {
        method: "POST", body: { payload },
      });
    },
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
