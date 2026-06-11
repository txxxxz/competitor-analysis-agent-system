const API_BASE =
  import.meta.env.VITE_API_BASE || (import.meta.env.PROD ? "" : "http://localhost:8000");

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
  } catch (err) {
    throw new Error(`无法连接后端 API（${API_BASE}）：${err.message || "网络请求失败"}`);
  }
  if (!response.ok) {
    throw new Error(await formatError(response));
  }
  return response.json();
}

async function formatError(response) {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    return (await response.text()) || `Request failed: ${response.status}`;
  }
  const body = await response.json();
  if (Array.isArray(body.detail)) {
    return body.detail.map((item) => item.message || item.msg || JSON.stringify(item)).join(" ");
  }
  if (Array.isArray(body.errors)) {
    return body.errors.map((item) => `${item.field}: ${item.message}`).join(" ");
  }
  if (body.detail) return body.detail;
  if (body.title) return body.title;
  return `Request failed: ${response.status}`;
}

export function createTask(config) {
  return request("/api/tasks", {
    method: "POST",
    body: JSON.stringify(config),
  });
}

export async function getProviderStatus() {
  const envelope = await request("/api/v1/provider-status");
  return envelope.data;
}

export async function getAppSettings() {
  const envelope = await request("/api/v1/settings");
  return envelope.data;
}

export async function updateAppSettings(values) {
  const envelope = await request("/api/v1/settings", {
    method: "PUT",
    body: JSON.stringify({ values }),
  });
  return envelope.data;
}

export async function createTaskV1(config) {
  const envelope = await request("/api/v1/tasks", {
    method: "POST",
    body: JSON.stringify(config),
  });
  return envelope.data;
}

export async function polishAnalysisGoals(payload) {
  const envelope = await request("/api/v1/analysis-goals/polish", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return envelope.data;
}

export async function condenseAnalysisGoals(payload) {
  const envelope = await request("/api/v1/analysis-goals/condense", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return envelope.data;
}

export async function recommendCompetitors(payload) {
  const envelope = await request("/api/v1/competitors/recommend", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return envelope.data;
}

export async function generateUserResearchSurvey(payload) {
  const envelope = await request("/api/v1/surveys/generate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return envelope.data;
}

export async function getSkillCatalog() {
  const envelope = await request("/api/v1/skills/catalog");
  return envelope.data;
}

export async function importGithubSkill(payload) {
  const envelope = await request("/api/v1/skills/import-github", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return envelope.data;
}

export async function syncDefaultSkills() {
  const envelope = await request("/api/v1/skills/sync-defaults", { method: "POST" });
  return envelope.data;
}

export async function updateSkillAssignments(assignments) {
  const envelope = await request("/api/v1/skills/assignments", {
    method: "PUT",
    body: JSON.stringify({ assignments }),
  });
  return envelope.data;
}

export async function recommendSkills(payload) {
  const envelope = await request("/api/v1/skills/recommend", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return envelope.data;
}

export async function getXhsStatus() {
  const envelope = await request("/api/v1/social/xhs/status");
  return envelope.data;
}

export async function getXhsLoginQrCode() {
  const envelope = await request("/api/v1/social/xhs/login-qrcode", {
    method: "POST",
    body: JSON.stringify({}),
  });
  return envelope.data;
}

export async function checkXhsQrCodeStatus(payload) {
  const envelope = await request("/api/v1/social/xhs/qrcode-status", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return envelope.data;
}

export function runTask(taskId) {
  return request(`/api/tasks/${taskId}/run`, { method: "POST" });
}

export function streamTaskRun(taskId, handlers = {}) {
  return new Promise((resolve, reject) => {
    const events = new EventSource(`${API_BASE}/api/v1/tasks/${taskId}/run/stream`);
    let finalResult = null;
    events.addEventListener("workflow_started", (event) => {
      handlers.onStart?.(JSON.parse(event.data));
    });
    events.addEventListener("state", (event) => {
      handlers.onState?.(JSON.parse(event.data));
    });
    events.addEventListener("trace", (event) => {
      handlers.onTrace?.(JSON.parse(event.data));
    });
    events.addEventListener("result", (event) => {
      finalResult = JSON.parse(event.data);
      handlers.onResult?.(finalResult);
    });
    events.addEventListener("workflow_completed", (event) => {
      handlers.onDone?.(JSON.parse(event.data));
      events.close();
      resolve(finalResult);
    });
    events.addEventListener("workflow_error", (event) => {
      events.close();
      const message = event.data ? JSON.parse(event.data).message : "Streaming run failed.";
      reject(new Error(message));
    });
    events.onerror = () => {
      events.close();
      reject(new Error("Streaming run connection failed."));
    };
  });
}

export async function streamTaskRunFromConfig(config, handlers = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE}/api/v1/tasks/run/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
  } catch (err) {
    throw new Error(`无法连接后端 API（${API_BASE}）：${err.message || "网络请求失败"}`);
  }
  if (!response.ok) {
    throw new Error(await formatError(response));
  }
  if (!response.body) {
    throw new Error("Streaming run response has no body.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResult = null;

  function consume(rawEvent) {
    const lines = rawEvent.split(/\r?\n/);
    let eventName = "message";
    const dataLines = [];
    for (const line of lines) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) return;
    const data = JSON.parse(dataLines.join("\n"));
    if (eventName === "workflow_started") handlers.onStart?.(data);
    if (eventName === "state") handlers.onState?.(data);
    if (eventName === "trace") handlers.onTrace?.(data);
    if (eventName === "result") {
      finalResult = data;
      handlers.onResult?.(data);
    }
    if (eventName === "workflow_completed") handlers.onDone?.(data);
    if (eventName === "workflow_error") {
      throw new Error(data.message || "Streaming run failed.");
    }
  }

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const events = buffer.split(/\n\n/);
    buffer = events.pop() || "";
    for (const rawEvent of events) {
      consume(rawEvent);
    }
    if (done) break;
  }
  if (buffer.trim()) consume(buffer);
  return finalResult;
}

export function getTasks() {
  return request("/api/tasks");
}

export function getTask(taskId) {
  return request(`/api/tasks/${taskId}`);
}

export async function excludeEvidence(evidenceId, reason) {
  const envelope = await request(`/api/v1/evidence/${evidenceId}/exclude`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
  return envelope.data;
}

export async function restoreEvidence(evidenceId) {
  const envelope = await request(`/api/v1/evidence/${evidenceId}/restore`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  return envelope.data;
}

export async function acceptReviewTicket(ticketId, note = "") {
  const envelope = await request(`/api/v1/review-tickets/${ticketId}/accept`, {
    method: "POST",
    body: JSON.stringify({ note }),
  });
  return envelope.data;
}

export async function rerunReviewTicket(ticketId) {
  const envelope = await request(`/api/v1/review-tickets/${ticketId}/rerun`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  return envelope.data;
}

export async function dismissReviewTicket(ticketId, reason = "") {
  const envelope = await request(`/api/v1/review-tickets/${ticketId}/dismiss`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
  return envelope.data;
}

export async function resolveReviewTicket(ticketId, resolutionSummary = "") {
  const envelope = await request(`/api/v1/review-tickets/${ticketId}/resolve`, {
    method: "POST",
    body: JSON.stringify({ resolution_summary: resolutionSummary }),
  });
  return envelope.data;
}

export async function markReviewTicketUnavailable(ticketId, reason = "") {
  const envelope = await request(`/api/v1/review-tickets/${ticketId}/mark-unavailable`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
  return envelope.data;
}

export async function downgradeReviewTicket(ticketId, reason = "") {
  const envelope = await request(`/api/v1/review-tickets/${ticketId}/downgrade`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
  return envelope.data;
}

export async function exportReport(taskId, allowDraft = false) {
  const envelope = await request(`/api/v1/tasks/${taskId}/report/export?format=markdown&allow_draft=${allowDraft}`);
  return envelope.data;
}
