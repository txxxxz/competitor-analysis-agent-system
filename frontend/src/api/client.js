const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return response.json();
}

export function getDemoTasks() {
  return request("/api/demo-tasks");
}

export function createTask(config) {
  return request("/api/tasks", {
    method: "POST",
    body: JSON.stringify(config),
  });
}

export function runTask(taskId) {
  return request(`/api/tasks/${taskId}/run`, { method: "POST" });
}

export function getTasks() {
  return request("/api/tasks");
}
