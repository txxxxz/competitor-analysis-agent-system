import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowRight,
  Ban,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  ClipboardList,
  Database,
  Download,
  FileText,
  GitBranch,
  LayoutDashboard,
  ListChecks,
  Menu,
  Play,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Table2,
  TriangleAlert,
  XCircle,
} from "lucide-react";
import {
  acceptReviewTicket,
  condenseAnalysisGoals,
  createTaskV1,
  dismissReviewTicket,
  downgradeReviewTicket,
  excludeEvidence,
  exportReport,
  getProviderStatus,
  getTask,
  getTasks,
  markReviewTicketUnavailable,
  polishAnalysisGoals,
  recommendCompetitors,
  rerunReviewTicket,
  resolveReviewTicket,
  restoreEvidence,
  streamTaskRun,
} from "./api/client";
import "./styles/app.css";

const statusCopy = {
  passed: "已通过",
  uncertain: "待确认",
  blocked: "已阻塞",
  pending: "待处理",
  unsupported: "缺少证据",
  contradicted: "存在矛盾",
  stale: "需复核",
  downgraded: "已降级",
  active: "生效中",
  excluded: "已排除",
  created: "已创建",
  draft: "草稿",
  reviewing: "复核中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  open: "待处理",
  accepted: "已接收",
  rerun_started: "重跑中",
  resolved: "已解决",
  dismissed: "已忽略",
};

const domainOptions = [
  ["ai_tools", "AI 工具"],
  ["general_product", "通用产品"],
  ["saas", "SaaS"],
];

const strictnessOptions = ["high", "standard", "low"];
const strictnessCopy = {
  high: "高：严格要求证据",
  standard: "标准：平衡覆盖与可信度",
  low: "低：允许探索性结论",
};

const dimensionCopy = {
  positioning: "定位",
  feature: "功能",
  browser_interaction: "实测路径",
  comparative_browser_interaction: "实测路径对比",
  agent_capability: "Agent 能力",
  ai_capability: "AI 能力",
  developer_workflow: "开发者工作流",
  target_user: "目标用户",
  target_users: "目标用户",
  pricing: "定价",
  security: "安全与合规",
  collaboration: "协作",
};

const evidenceTypeCopy = {
  pricing: "定价",
  feature: "功能",
  browser_interaction: "浏览器实测路径",
  target_user: "目标用户",
  target_users: "目标用户",
  security: "安全与隐私",
  contradiction: "矛盾校验",
  positioning: "定位",
  official_pricing_page: "官方定价页",
  official_docs: "官方文档",
  official_browser_walkthrough: "官方页面实测",
  browser_walkthrough: "浏览器实测",
};

const sourcePreferenceCopy = {
  official: "官方来源",
  official_docs: "官方文档",
  official_pricing_page: "官方定价页",
  browser_walkthrough: "浏览器实测",
  official_or_independent: "官方或独立来源",
  "official documentation and industry reports": "官方文档与行业报告",
  "privacy policies and security audits": "隐私政策与安全审计",
  "user reviews and academic critiques": "用户评价与学术/行业评论",
};

const nodeCopy = {
  ResearchAgent: "检索 Agent",
  CriticAgent: "复核 Agent",
  EvidenceConsistencyReviewer: "证据一致性复核",
  WriterAgent: "报告生成 Agent",
  AnalystAgent: "分析 Agent",
  InteractionAgent: "交互实测 Agent",
  review_ticket: "复核工单",
};

const goalPromptDraft = "例如：希望分析 Cursor 相比 GitHub Copilot、Windsurf、TRAE 在 AI Agent 工作流、代码理解、定价、团队协作和企业落地风险上的差异，并输出产品机会点。";
const maxAnalysisGoalWords = 1000;
const goalValueCopy = {
  positioning: "产品定位",
  ai_capability: "AI 能力",
  agent_capability: "Agent 能力",
  developer_workflow: "开发者工作流",
  pricing: "定价",
  security: "安全与合规",
  collaboration: "团队协作",
  target_user: "目标用户",
};
const goalValueReverseCopy = Object.fromEntries(Object.entries(goalValueCopy).map(([key, value]) => [value, key]));
const emptyTaskForm = {
  domain: "ai_tools",
  target_product: "",
  competitors: [],
  competitorDraft: "",
  goalsText: "",
  depth: "standard",
  evidence_strictness: "high",
  audience: "产品团队",
  notes: "",
};

function App() {
  const [taskForm, setTaskForm] = useState(() => ({ ...emptyTaskForm }));
  const [result, setResult] = useState(null);
  const [activeView, setActiveView] = useState("home");
  const [activeTab, setActiveTab] = useState("overview");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [liveTrace, setLiveTrace] = useState([]);
  const [streamState, setStreamState] = useState(null);
  const [recentTasks, setRecentTasks] = useState([]);
  const [recentLoading, setRecentLoading] = useState(false);
  const [providerStatus, setProviderStatus] = useState(null);
  const [providerLoading, setProviderLoading] = useState(true);
  const workspaceRef = useRef(null);

  useEffect(() => {
    refreshRecentTasks();
    refreshProviderStatus();
  }, []);

  const metrics = useMemo(() => {
    if (!result) {
      return [
        ["来源", "0"],
        ["证据", "0"],
        ["结论", "0"],
        ["工单", "0"],
      ];
    }
    return [
      ["来源", result.sources.length],
      ["证据", result.evidence.length],
      ["结论", result.claims.length],
      ["工单", result.review_tickets.length],
    ];
  }, [result]);
  const formValidationErrors = taskForm ? getTaskFormErrors(taskForm) : ["分析任务表单还没有准备好。"];
  const taskFormInvalid = formValidationErrors.length > 0;

  function openConfigPage() {
    setResult(null);
    setLiveTrace([]);
    setStreamState(null);
    setActionMessage("");
    setError("");
    setSidebarOpen(false);
    setActiveView("config");
    focusWorkspace();
  }

  function focusWorkspace() {
    window.setTimeout(() => {
      workspaceRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      workspaceRef.current?.focus({ preventScroll: true });
    }, 0);
  }

  function changeTab(tab) {
    setActiveTab(tab);
    focusWorkspace();
  }

  async function launchTask() {
    if (!taskForm) return;
    const latestProviderStatus = await refreshProviderStatus();
    if (!latestProviderStatus?.workflow_ready) {
      setError(latestProviderStatus?.issues?.join(" ") || "真实 Provider 尚未就绪。");
      return;
    }
    setLoading(true);
    setError("");
    setActionMessage("");
    setResult(null);
    setLiveTrace([]);
    setStreamState(null);
    setActiveView("result");
    try {
      const task = await createTaskV1(formToConfig(taskForm));
      const workflowResult = await streamTaskRun(task.task_id, {
        onTrace: (event) => setLiveTrace((current) => [...current, event]),
        onState: (state) => setStreamState(state),
        onResult: (nextResult) => setResult(nextResult),
      });
      setResult(workflowResult);
      setActiveTab("overview");
      refreshRecentTasks();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleEvidenceExclude(evidenceId) {
    setError("");
    setActionMessage("");
    try {
      const update = await excludeEvidence(evidenceId, "用户在证据复核中排除了该证据。");
      setResult((current) => applyEvidenceUpdate(current, update, "该证据被排除，相关结论需要重新复核。"));
      setActionMessage(`已排除 ${evidenceId}；关联结论和报告已标记为需复核。`);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleEvidenceRestore(evidenceId) {
    setError("");
    setActionMessage("");
    try {
      const update = await restoreEvidence(evidenceId);
      setResult((current) => applyEvidenceUpdate(current, update, "该证据已恢复，相关结论需要重新复核。"));
      setActionMessage(`已恢复 ${evidenceId}；关联结论在重新复核前仍保持需复核状态。`);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleTicketAction(ticketId, action) {
    setError("");
    setActionMessage("");
    try {
      const handlers = {
        accept: () => acceptReviewTicket(ticketId, "已从复核队列接收该工单。"),
        rerun: () => rerunReviewTicket(ticketId),
        resolve: () => resolveReviewTicket(ticketId, "已从复核队列解决该工单。"),
        dismiss: () => dismissReviewTicket(ticketId, "已从复核队列忽略该工单。"),
        unavailable: () => markReviewTicketUnavailable(ticketId, "复核后确认所需证据不可得。"),
        downgrade: () => downgradeReviewTicket(ticketId, "复核后已降级相关结论。"),
      };
      const ticket = await handlers[action]();
      if (ticket.workflow_result) {
        setResult(ticket.workflow_result);
      } else {
        setResult((current) => applyTicketUpdate(current, ticket));
      }
      setActionMessage(`${ticket.ticket_id} 当前状态：${statusCopy[ticket.status] || ticket.status}。`);
    } catch (err) {
      setError(err.message);
    }
  }

  async function refreshRecentTasks() {
    setRecentLoading(true);
    try {
      setRecentTasks((await getTasks()).slice(0, 6));
    } catch (err) {
      setError(err.message);
    } finally {
      setRecentLoading(false);
    }
  }

  async function refreshProviderStatus() {
    setProviderLoading(true);
    try {
      const status = await getProviderStatus();
      setProviderStatus(status);
      return status;
    } catch (err) {
      setProviderStatus({ workflow_ready: false, issues: [err.message] });
      return null;
    } finally {
      setProviderLoading(false);
    }
  }

  async function loadRecentTask(taskId) {
    setError("");
    setActionMessage("");
    setLoading(true);
    try {
      const saved = await getTask(taskId);
      if (!saved.claims || !saved.report) {
        setActionMessage("这个任务已创建，但还没有完成的工作流结果。");
        return;
      }
      setResult(saved);
      setLiveTrace([]);
      setStreamState(null);
      setActiveTab("overview");
      setActiveView("result");
      setSidebarOpen(false);
      setActionMessage(`已加载 ${saved.task.config.target_product} 的历史分析结果。`);
      focusWorkspace();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="shell">
      <a className="skip-link" href="#main-workspace">跳到主内容</a>
      <div className="mobile-bar">
        <div className="brand">
          <div className="brand-mark"><GitBranch size={20} /></div>
          <div>
            <strong>EvidenceGraph</strong>
            <span>竞品分析 Agent 协作系统</span>
          </div>
        </div>
        <button type="button" className="mobile-menu-button" onClick={() => setSidebarOpen((current) => !current)} aria-expanded={sidebarOpen} aria-controls="sidebar-navigation">
          <Menu size={19} />
          {sidebarOpen ? "关闭导航" : "打开导航"}
        </button>
      </div>
      {sidebarOpen && <button type="button" className="sidebar-backdrop" aria-label="关闭导航" onClick={() => setSidebarOpen(false)} />}
      <aside id="sidebar-navigation" className={`sidebar ${sidebarOpen ? "open" : ""}`}>
        <div className="brand desktop-brand">
          <div className="brand-mark"><GitBranch size={20} /></div>
          <div>
            <strong>EvidenceGraph</strong>
            <span>竞品分析 Agent 协作系统</span>
          </div>
        </div>

        <button type="button" className="new-analysis-button" onClick={openConfigPage}>
          <Play size={16} />
          新建真实分析
        </button>

        <RecentRunsPanel
          tasks={recentTasks}
          activeTaskId={result?.task?.task_id}
          loading={recentLoading}
          onRefresh={refreshRecentTasks}
          onSelect={loadRecentTask}
        />

        <ProviderStatusPanel status={providerStatus} loading={providerLoading} onRefresh={refreshProviderStatus} />
      </aside>

      <section id="main-workspace" className={`workspace ${result ? "has-result" : ""}`} ref={workspaceRef} tabIndex="-1">
        <header className="topbar">
          <div>
            <p className="eyebrow">{result ? "分析结果工作台" : "V1.2.1 产品化闭环"}</p>
            <h1>{result ? `${result.task.config.target_product} 竞品分析` : "以证据为核心的竞品分析系统，支持检索、结论复核与可信度追踪。"}</h1>
          </div>
          <div className="status-pill">
            <Activity size={16} />
            {activeView === "config" ? "配置中" : result ? "已完成" : loading ? "运行中" : "就绪"}
          </div>
        </header>

        {loading && (
          <>
            <div className="metric-grid">
              {metrics.map(([label, value]) => (
                <div className="metric" key={label}>
                  <span>{label}</span>
                  <strong>{value}</strong>
                </div>
              ))}
            </div>
            <WorkflowStepper trace={result?.trace || liveTrace} running={loading} />
          </>
        )}

        {activeView === "config" ? (
          <ConfigView
            form={taskForm}
            setForm={setTaskForm}
            loading={loading}
            invalid={taskFormInvalid}
            validationErrors={formValidationErrors}
            error={error}
            onRun={launchTask}
            providerStatus={providerStatus}
            providerLoading={providerLoading}
            onReset={() => setTaskForm({ ...emptyTaskForm })}
          />
        ) : result ? (
          <>
            {liveTrace.length > 0 && <StreamSummary liveTrace={liveTrace} streamState={streamState} />}
            {actionMessage && <p className="success">{actionMessage}</p>}
            <nav className="tabs" role="tablist" aria-label="分析结果视图">
              {[
                ["overview", "概览", LayoutDashboard],
                ["plan", "检索计划", ListChecks],
                ["matrix", "对比矩阵", Table2],
                ["claims", "证据与结论", Database],
                ["report", "最终报告", FileText],
                ["trace", "运行详情", GitBranch],
              ].map(([id, label, Icon]) => (
                <button key={id} role="tab" aria-selected={activeTab === id} className={activeTab === id ? "active" : ""} onClick={() => changeTab(id)}>
                  <Icon size={16} />
                  {label}
                </button>
              ))}
            </nav>
            {activeTab === "overview" && <OverviewView result={result} onChangeTab={changeTab} onConfigure={openConfigPage} />}
            {activeTab === "trace" && <TraceView result={result} onTicketAction={handleTicketAction} />}
            {activeTab === "plan" && <SearchPlanView result={result} />}
            {activeTab === "matrix" && <MatrixView result={result} />}
            {activeTab === "claims" && <ClaimsView result={result} onExcludeEvidence={handleEvidenceExclude} onRestoreEvidence={handleEvidenceRestore} />}
            {activeTab === "report" && <ReportView result={result} />}
          </>
        ) : (
          <EmptyState loading={loading} liveTrace={liveTrace} streamState={streamState} onConfigure={openConfigPage} providerStatus={providerStatus} />
        )}
      </section>
    </main>
  );
}

function RecentRunsPanel({ tasks, activeTaskId, loading, onRefresh, onSelect }) {
  return (
    <section className="panel recent-runs">
      <div className="panel-heading split">
        <span>历史分析</span>
        <button type="button" className="icon-button" onClick={onRefresh} disabled={loading} aria-label="刷新历史分析">
          <RefreshCw size={14} />
        </button>
      </div>
      {tasks.length === 0 ? (
        <p>还没有保存过的分析结果。</p>
      ) : (
        <div className="recent-list">
          {tasks.map((task) => (
            <button
              type="button"
              className={`recent-item ${activeTaskId === task.task_id ? "active" : ""}`}
              key={task.task_id}
              onClick={() => onSelect(task.task_id)}
            >
              <span className={`badge ${task.status}`}>{statusCopy[task.status] || task.status}</span>
              <strong>{task.config.target_product}</strong>
              <span>{task.config.competitors.join(", ")}</span>
              <small>{formatDate(task.updated_at || task.created_at)}</small>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function formToConfig(form) {
  return {
    domain: form.domain,
    target_product: form.target_product.trim(),
    competitors: form.competitors,
    analysis_goals: splitList(form.goalsText).map((goal) => goalValueReverseCopy[goal] || goal),
    depth: form.depth || "standard",
    evidence_strictness: form.evidence_strictness,
    audience: form.audience,
    notes: form.notes || "",
  };
}

function splitList(value) {
  return value
    .split(/[,，、\n]/)
    .map((item) => item.trim().replace(/^\d+[\.)、]\s*/, ""))
    .filter(Boolean);
}

function getTaskFormErrors(form) {
  const competitors = form.competitors.map(normalizeName);
  const goals = splitList(form.goalsText);
  const goalWordCount = countGoalWords(form.goalsText);
  const errors = [];
  if (!form.target_product.trim()) errors.push("请填写目标产品。");
  if (competitors.length === 0) errors.push("请至少添加一个竞品。");
  if (competitors.length > 5) errors.push("MVP 最多支持 5 个竞品。");
  if (competitors.includes(normalizeName(form.target_product))) errors.push("目标产品不能同时作为竞品。");
  if (new Set(competitors).size !== competitors.length) errors.push("竞品名称去重后必须唯一。");
  if (goals.length === 0) errors.push("请填写分析目标。");
  if (goalWordCount > maxAnalysisGoalWords) errors.push(`分析目标需控制在 ${maxAnalysisGoalWords} 词以内。`);
  return errors;
}

function normalizeName(value) {
  return value.trim().toLowerCase().replace(/\s+/g, " ");
}

function countGoalWords(value) {
  let count = 0;
  let inAsciiWord = false;
  Array.from(value || "").forEach((char) => {
    if (/[\u4e00-\u9fff]/.test(char)) {
      count += 1;
      inAsciiWord = false;
    } else if (/[A-Za-z0-9]/.test(char)) {
      if (!inAsciiWord) count += 1;
      inAsciiWord = true;
    } else {
      inAsciiWord = false;
    }
  });
  return count;
}

function applyEvidenceUpdate(current, update, note) {
  if (!current) return current;
  const staleClaims = new Set(update.stale_claims || []);
  return {
    ...current,
    evidence: current.evidence.map((item) => (
      item.evidence_id === update.evidence_id
        ? { ...item, status: update.status, excluded_reason: update.status === "excluded" ? "用户在证据复核中排除了该证据。" : "" }
        : item
    )),
    claims: current.claims.map((claim) => (
      staleClaims.has(claim.claim_id)
        ? { ...claim, verified_status: "stale", included_in_report: false, note }
        : claim
    )),
    report: current.report
      ? {
          ...current.report,
          status: update.report_status,
          sections: (current.report.sections || []).map((section) => (
            section.claim_ids?.some((claimId) => staleClaims.has(claimId))
              ? { ...section, status: "stale" }
              : section
          )),
        }
      : current.report,
  };
}

function applyTicketUpdate(current, ticketUpdate) {
  if (!current) return current;
  return {
    ...current,
    task: ticketUpdate.status === "blocked"
      ? { ...current.task, status: "blocked" }
      : current.task,
    review_tickets: current.review_tickets.map((ticket) => (
      ticket.ticket_id === ticketUpdate.ticket_id
        ? {
            ...ticket,
            ...ticketUpdate,
            resolution_note: ticketUpdate.resolution_summary || ticket.resolution_note,
          }
        : ticket
    )),
  };
}

function TaskForm({ form, setForm, validationErrors = [] }) {
  const update = (patch) => setForm((current) => ({ ...current, ...patch }));
  const [polishing, setPolishing] = useState(false);
  const [condensing, setCondensing] = useState(false);
  const [polishError, setPolishError] = useState("");
  const [recommendations, setRecommendations] = useState([]);
  const [recommending, setRecommending] = useState(false);
  const [recommendError, setRecommendError] = useState("");
  const normalizedCompetitors = form.competitors.map(normalizeName);
  const duplicateTarget = normalizedCompetitors.includes(normalizeName(form.target_product));
  const duplicateCompetitors = new Set(normalizedCompetitors).size !== normalizedCompetitors.length;
  const tooManyCompetitors = form.competitors.length > 5;
  const targetMissing = !form.target_product.trim();
  const goals = splitList(form.goalsText);
  const goalsMissing = goals.length === 0;
  const goalWordCount = countGoalWords(form.goalsText);
  const goalsTooLong = goalWordCount > maxAnalysisGoalWords;
  const availableSuggestions = recommendations
    .filter((item) => normalizeName(item) !== normalizeName(form.target_product))
    .filter((item) => !normalizedCompetitors.includes(normalizeName(item)))
    .slice(0, Math.max(0, 5 - form.competitors.length));

  useEffect(() => {
    const target = form.target_product.trim();
    if (target.length < 2 || form.competitors.length >= 5) {
      setRecommendations([]);
      setRecommendError("");
      setRecommending(false);
      return undefined;
    }
    let ignore = false;
    const timer = window.setTimeout(async () => {
      setRecommending(true);
      setRecommendError("");
      try {
        const response = await recommendCompetitors({
          target_product: target,
          domain: form.domain,
          existing_competitors: form.competitors,
          audience: form.audience,
          max_results: 5,
        });
        if (!ignore) {
          setRecommendations(response.competitors || []);
        }
      } catch (err) {
        if (!ignore) {
          setRecommendations([]);
          setRecommendError(err.message);
        }
      } finally {
        if (!ignore) setRecommending(false);
      }
    }, 700);
    return () => {
      ignore = true;
      window.clearTimeout(timer);
    };
  }, [form.target_product, form.domain, form.audience, form.competitors]);

  function addCompetitors(value) {
    const items = splitList(value);
    if (!items.length) return;
    setForm((current) => {
      const existing = new Set(current.competitors.map(normalizeName));
      const next = [...current.competitors];
      items.forEach((item) => {
        const key = normalizeName(item);
        if (key && !existing.has(key) && next.length < 5) {
          existing.add(key);
          next.push(item);
        }
      });
      return { ...current, competitors: next, competitorDraft: "" };
    });
  }

  function removeCompetitor(value) {
    setForm((current) => ({
      ...current,
      competitors: current.competitors.filter((item) => normalizeName(item) !== normalizeName(value)),
    }));
  }

  function handleCompetitorKeyDown(event) {
    if (["Enter", ","].includes(event.key)) {
      event.preventDefault();
      addCompetitors(form.competitorDraft);
    }
    if (event.key === "Backspace" && !form.competitorDraft && form.competitors.length) {
      removeCompetitor(form.competitors[form.competitors.length - 1]);
    }
  }

  async function handlePolishGoals() {
    const draft = form.goalsText.trim() || goalPromptDraft;
    setPolishError("");
    setPolishing(true);
    try {
      const polished = await polishAnalysisGoals({
        draft,
        domain: form.domain,
        target_product: form.target_product,
        competitors: form.competitors,
        audience: form.audience,
      });
      update({ goalsText: polished.formatted_text || polished.goals?.map((goal, index) => `${index + 1}. ${goal}`).join("\n") || draft });
    } catch (err) {
      setPolishError(err.message);
    } finally {
      setPolishing(false);
    }
  }

  async function handleCondenseGoals() {
    const draft = form.goalsText.trim();
    if (!draft) return;
    setPolishError("");
    setCondensing(true);
    try {
      const condensed = await condenseAnalysisGoals({
        draft,
        domain: form.domain,
        target_product: form.target_product,
        competitors: form.competitors,
        audience: form.audience,
        max_words: maxAnalysisGoalWords,
      });
      update({ goalsText: condensed.condensed_text || draft });
    } catch (err) {
      setPolishError(err.message);
    } finally {
      setCondensing(false);
    }
  }

  return (
    <section className="panel task-form">
      <div className="panel-heading">
        <ClipboardList size={16} />
        <span>新建配置</span>
      </div>
      <label className={targetMissing ? "field-invalid" : ""}>
        <span>目标产品</span>
        <small className="field-help">目标产品是本次要重点分析的对象，例如你正在评估或负责的产品；系统会围绕它生成报告和机会点。</small>
        <input value={form.target_product} onChange={(event) => update({ target_product: event.target.value })} placeholder="例如：Cursor" aria-invalid={targetMissing} />
      </label>
      {targetMissing && <p className="field-warning">请填写要重点分析的产品，不要把对照产品写在这里。</p>}
      <label>
        <span>竞品</span>
        <small className="field-help">竞品是用来和目标产品做对比的产品，例如同类替代品、相邻方案或用户会一起评估的工具；目标产品不能重复放入竞品。</small>
        <div className={`chip-input ${duplicateTarget || duplicateCompetitors || tooManyCompetitors ? "invalid" : ""}`}>
          <div className="chip-list">
            {form.competitors.map((competitor) => (
              <span className="chip" key={competitor}>
                {competitor}
                <button type="button" onClick={() => removeCompetitor(competitor)} aria-label={`移除 ${competitor}`}>
                  <XCircle size={13} />
                </button>
              </span>
            ))}
          </div>
          <div className="chip-entry">
            <input
              value={form.competitorDraft}
              onChange={(event) => update({ competitorDraft: event.target.value })}
              onKeyDown={handleCompetitorKeyDown}
              onBlur={() => addCompetitors(form.competitorDraft)}
              placeholder={form.competitors.length >= 5 ? "最多 5 个竞品" : "输入竞品后按 Enter"}
              disabled={form.competitors.length >= 5}
            />
            <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => addCompetitors(form.competitorDraft)} disabled={!form.competitorDraft.trim() || form.competitors.length >= 5}>
              添加
            </button>
          </div>
        </div>
      </label>
      {form.competitors.length === 0 && <p className="field-warning">请至少添加一个竞品。</p>}
      {duplicateTarget && <p className="field-warning">目标产品不能同时作为竞品。</p>}
      {duplicateCompetitors && <p className="field-warning">竞品名称去重后必须唯一。</p>}
      {tooManyCompetitors && <p className="field-warning">MVP 最多支持 5 个竞品。</p>}
      {!targetMissing && form.competitors.length < 5 && (
        <div className={`suggestion-row ${recommending ? "loading" : ""}`} aria-label="AI 推荐竞品">
          <span>{recommending ? "AI 正在推荐竞品..." : availableSuggestions.length ? "AI 推荐竞品" : "AI 暂无推荐"}</span>
          {availableSuggestions.map((item) => (
            <button type="button" key={item} onClick={() => addCompetitors(item)}>
              {item}
            </button>
          ))}
        </div>
      )}
      {recommendError && <p className="field-warning">AI 推荐竞品失败：{recommendError}</p>}
      <label>
        <span>产品领域</span>
        <select value={form.domain} onChange={(event) => update({ domain: event.target.value })}>
          {domainOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label>
        <span>分析目标</span>
        <div className="goal-field">
          <textarea
            value={form.goalsText}
            onChange={(event) => update({ goalsText: event.target.value })}
            placeholder={goalPromptDraft}
            rows={7}
            aria-invalid={goalsMissing || goalsTooLong}
          />
          <div className="goal-actions">
            <button type="button" className="polish-button" onClick={handlePolishGoals} disabled={polishing || condensing}>
              <Sparkles size={14} />
              {polishing ? "润色中..." : "AI 润色"}
            </button>
            {goalsTooLong && (
              <button type="button" className="polish-button urgent" onClick={handleCondenseGoals} disabled={polishing || condensing}>
                <Sparkles size={14} />
                {condensing ? "缩写中..." : "AI 缩写"}
              </button>
            )}
          </div>
        </div>
      </label>
      {goalsMissing && <p className="field-warning">请至少填写一个分析目标，例如“产品定位、定价、AI 能力”。</p>}
      <p className={`field-counter ${goalsTooLong ? "over-limit" : ""}`}>当前约 {goalWordCount} / {maxAnalysisGoalWords} 词。超过后可用 AI 缩写自动压缩。</p>
      {goalsTooLong && <p className="field-warning">分析目标太长，请控制在 {maxAnalysisGoalWords} 词以内，或点击“AI 缩写”。</p>}
      {polishError && <p className="field-warning">AI 润色失败：{polishError}</p>}
      <div className="form-row">
        <label>
          <span>证据严格度</span>
          <select value={form.evidence_strictness} onChange={(event) => update({ evidence_strictness: event.target.value })}>
            {strictnessOptions.map((item) => <option key={item} value={item}>{strictnessCopy[item]}</option>)}
          </select>
        </label>
        <label>
          <span>目标读者</span>
          <input list="audience-options" value={form.audience} onChange={(event) => update({ audience: event.target.value })} placeholder="例如：产品团队" />
          <datalist id="audience-options">
            <option value="产品团队" />
            <option value="AI 工具产品团队" />
            <option value="产品与市场团队" />
            <option value="管理层与决策者" />
          </datalist>
        </label>
      </div>
      <label>
        <span>补充要求</span>
        <textarea value={form.notes || ""} onChange={(event) => update({ notes: event.target.value })} rows={3} placeholder="可选：指定地区、时间范围、重点来源或输出格式。" />
      </label>
      {validationErrors.length > 0 && (
        <div className="validation-summary" role="alert">
          <strong>还需要完成 {validationErrors.length} 项配置</strong>
          <span>{validationErrors.join(" ")}</span>
        </div>
      )}
    </section>
  );
}

function ConfigView({ form, setForm, loading, invalid, validationErrors, error, onRun, providerStatus, providerLoading, onReset }) {
  if (!form) {
    return (
      <section className="config-page">
        <div className="config-head">
          <div>
            <p className="eyebrow">新建配置</p>
            <h2>先选择或填写分析配置，再启动完整 Agent 工作流。</h2>
          </div>
        </div>
        <p className="field-warning">分析任务表单尚未准备好，请刷新页面后重试。</p>
      </section>
    );
  }
  return (
    <section className="config-page">
      <div className="config-head">
        <div>
          <p className="eyebrow">新建配置</p>
          <h2>配置一个分析任务</h2>
          <p>填写真实分析范围后，系统会调用当前配置的搜索和 LLM Provider，生成可复核的来源、证据、结论与报告。</p>
          <span className={`template-source ${providerStatus?.workflow_ready ? "ready" : "blocked"}`}>
            {providerLoading ? "正在检查 Provider..." : providerStatus?.workflow_ready ? "真实 Provider 已就绪" : "真实 Provider 未就绪"}
          </span>
        </div>
        <div className="config-actions">
          <button type="button" className="secondary-button" onClick={onReset} disabled={loading}>清空配置</button>
          <button className="run-button" onClick={onRun} disabled={loading || invalid || !providerStatus?.workflow_ready} title={invalid ? validationErrors.join(" ") : !providerStatus?.workflow_ready ? providerStatus?.issues?.join(" ") : "开始分析"}>
            <Play size={17} fill="currentColor" />
            {loading ? "正在运行 Agent 工作流..." : "开始分析"}
          </button>
        </div>
      </div>
      {!providerLoading && !providerStatus?.workflow_ready && (
        <div className="provider-blocker" role="alert">
          <TriangleAlert size={18} />
          <div>
            <strong>真实分析暂不可运行</strong>
            {(providerStatus?.issues || ["请检查后端 Provider 配置。"]).map((issue) => <span key={issue}>{issue}</span>)}
          </div>
        </div>
      )}
      <TaskForm form={form} setForm={setForm} validationErrors={validationErrors} />
      {error && <p className="error">{error}</p>}
    </section>
  );
}

function EmptyState({ loading, liveTrace = [], streamState = null, onConfigure, providerStatus }) {
  if (loading && liveTrace.length) {
    return <LiveTracePanel liveTrace={liveTrace} streamState={streamState} />;
  }
  return (
    <section className="empty-state">
      <GitBranch size={38} />
      <h2>{loading ? "正在执行 Agent 工作流" : "创建一份真实竞品分析"}</h2>
      <p>填写目标产品、竞品和分析目标。系统将调用真实搜索与 LLM Provider，输出来源、证据、结论和最终报告。</p>
      {!loading && !providerStatus?.workflow_ready && <p className="empty-warning">当前真实 Provider 尚未就绪，进入配置页可查看具体缺失项。</p>}
      {!loading && (
        <button type="button" className="empty-action" onClick={onConfigure}>
          去配置
          <ArrowRight size={16} />
        </button>
      )}
    </section>
  );
}

function ProviderStatusPanel({ status, loading, onRefresh }) {
  return (
    <section className={`panel provider-status ${status?.workflow_ready ? "ready" : "blocked"}`}>
      <div className="panel-heading split">
        <span><ShieldCheck size={16} />真实 Provider 状态</span>
        <button type="button" className="icon-button" onClick={onRefresh} disabled={loading} aria-label="刷新 Provider 状态">
          <RefreshCw size={14} />
        </button>
      </div>
      {loading ? (
        <p>正在检查后端配置...</p>
      ) : (
        <>
          <div className="provider-row"><span>搜索</span><strong>{status?.search?.provider || "未配置"}</strong><span className={`badge ${status?.search?.ready ? "passed" : "blocked"}`}>{status?.search?.ready ? "已就绪" : "未就绪"}</span></div>
          <div className="provider-row"><span>LLM</span><strong>{status?.llm?.provider || "未配置"}</strong><span className={`badge ${status?.llm?.ready ? "passed" : "blocked"}`}>{status?.llm?.ready ? "已就绪" : "未就绪"}</span></div>
          {(status?.issues || []).map((issue) => <p className="provider-issue" key={issue}>{issue}</p>)}
        </>
      )}
    </section>
  );
}

function LiveTracePanel({ liveTrace, streamState }) {
  return (
    <section className="live-trace">
      <div className="live-head">
        <div>
          <p className="eyebrow">实时 Agent 追踪</p>
          <h2>工作流运行时会持续推送节点事件。</h2>
        </div>
        <span className="status-pill">
          <Activity size={16} />
          运行中
        </span>
      </div>
      {streamState && (
        <div className="live-metrics">
          <span>{streamState.trace_count} 条追踪</span>
          <span>{streamState.source_count} 个来源</span>
          <span>{streamState.evidence_count} 条证据</span>
          <span>{streamState.claim_count} 条结论</span>
          <span>{streamState.ticket_count} 个工单</span>
        </div>
      )}
      <div className="timeline">
        {liveTrace.map((event, index) => (
          <article className="trace-item" key={event.event_id}>
            <div className="trace-index">{String(index + 1).padStart(2, "0")}</div>
            <div>
              <div className="trace-head">
                <strong>{translateNodeName(event.agent)}</strong>
                <span>{translateEventType(event.event_type)}</span>
              </div>
              <p>{translateStructuredText(event.summary)}</p>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function StreamSummary({ liveTrace, streamState }) {
  return (
    <section className="stream-summary">
      <strong>实时流已捕获 {liveTrace.length} 条追踪事件。</strong>
      {streamState && (
        <span>
          最终流状态：{streamState.source_count} 个来源、{streamState.evidence_count} 条证据、{streamState.claim_count} 条结论、{streamState.ticket_count} 个工单。
        </span>
      )}
    </section>
  );
}

function WorkflowStepper({ trace = [], running = false }) {
  const steps = [
    ["planner", "规划"],
    ["template", "模板"],
    ["research", "检索"],
    ["source_normalizer", "来源"],
    ["evidence_extractor", "证据"],
    ["interaction", "实测"],
    ["analyst", "结论"],
    ["critic", "复核"],
    ["evidence_reviewer", "门禁"],
    ["trust_summary", "可信度"],
    ["writer", "报告"],
    ["finalize", "完成"],
  ];
  const seenNodes = new Set(trace.map((event) => event.node));
  const latestNode = trace[trace.length - 1]?.node || "";
  return (
    <section className="workflow-stepper" aria-label="工作流进度">
      {steps.map(([node, label]) => {
        const status = running && latestNode === node ? "active" : seenNodes.has(node) ? "done" : "pending";
        return (
          <div className={`workflow-step ${status}`} key={node}>
            <span>{label}</span>
          </div>
        );
      })}
    </section>
  );
}

function ResultSummary({ result }) {
  return (
    <section className="result-summary">
      <TrustSummary summary={result.trust_summary} />
      <TaskConfig result={result} />
    </section>
  );
}

function TrustSummary({ summary }) {
  if (!summary) return null;
  const items = [
    ["证据绑定率", percent(summary.claim_evidence_binding_rate)],
    ["官方来源占比", percent(summary.official_source_ratio)],
    ["实测路径", `${summary.browser_interaction_count || 0} 条`],
    ["已通过结论", `${summary.passed_claim_count}/${summary.total_claim_count}`],
    ["未解决工单", summary.unresolved_ticket_count],
  ];
  return (
    <section className="trust-strip">
      <div className="trust-title">
        <ShieldCheck size={18} />
        <div>
          <strong>可信度摘要</strong>
          <span>{formatProviderMode(summary)}</span>
          <small>搜索 Provider：{summary.search_mode || "-"} / LLM Provider：{summary.llm_mode || "-"}</small>
        </div>
      </div>
      {items.map(([label, value]) => (
        <div className="trust-metric" key={label}>
          <span>{label}</span>
          <strong>{value}</strong>
        </div>
      ))}
    </section>
  );
}

function formatProviderMode(summary) {
  if (summary.fixture_mode || /fixture|mock/i.test(summary.provider_mode_label || "")) {
    return "非真实 Provider 数据（已隐藏）";
  }
  return summary.provider_mode_label || "真实 Provider 模式";
}

function TaskConfig({ result }) {
  const config = result.task.config;
  return (
    <section className="task-config">
      <div className="task-strip">
        <div>
          <span>目标产品</span>
          <strong>{config.target_product}</strong>
        </div>
        <div>
          <span>竞品</span>
          <strong>{config.competitors.join(", ")}</strong>
        </div>
        <div>
          <span>模板</span>
          <strong>{result.template?.name}</strong>
        </div>
        <div>
          <span>检索计划</span>
          <strong>{result.search_plan?.queries.length || 0} 条查询</strong>
        </div>
      </div>
      <details className="analysis-details">
        <summary>查看分析配置与规则</summary>
        <div className="template-details">
          <TemplateBlock title="报告章节" items={(result.template?.sections || []).map(displayReportSectionTitle)} />
          <TemplateBlock title="结论类型" items={(result.template?.claim_types || []).map((item) => dimensionCopy[item] || item)} />
          <TemplateBlock title="证据规则" items={(result.template?.evidence_rules || []).map(translateStructuredText)} />
          <TemplateBlock title="复核门禁" items={(result.template?.review_gates || []).map((item) => translateTaxonomyValue(item, evidenceTypeCopy))} />
        </div>
      </details>
    </section>
  );
}

function OverviewView({ result, onChangeTab, onConfigure }) {
  const passedClaims = result.claims.filter((claim) => claim.verified_status === "passed" && claim.included_in_report !== false);
  const attentionClaims = result.claims.filter((claim) => claim.verified_status !== "passed");
  const openTickets = result.review_tickets.filter((ticket) => !["resolved", "dismissed"].includes(ticket.status));
  const highlights = passedClaims.slice(0, 4);
  return (
    <section className="overview">
      <div className="overview-head">
        <div>
          <h2>{result.task.config.target_product} 分析概览</h2>
          <p>先看关键结论与风险，再进入证据、报告或运行详情。</p>
        </div>
        <div className="overview-actions">
          <button type="button" className="primary-action" onClick={() => onChangeTab("report")}><FileText size={16} />查看最终报告</button>
          <button type="button" onClick={() => onChangeTab("claims")}><Database size={16} />核查证据与结论</button>
          <button type="button" onClick={onConfigure}><Play size={16} />开始新分析</button>
        </div>
      </div>
      <div className="overview-grid">
        <article className="overview-card overview-highlights">
          <div className="overview-card-head">
            <h3>关键结论</h3>
            <span>{passedClaims.length} 条已通过</span>
          </div>
          {highlights.map((claim) => (
            <div className="overview-claim" key={claim.claim_id}>
              <strong>{claim.product} · {dimensionCopy[claim.claim_type] || claim.claim_type}</strong>
              <p>{translateStructuredText(claim.claim)}</p>
            </div>
          ))}
        </article>
        <article className="overview-card">
          <div className="overview-card-head">
            <h3>需要关注</h3>
            <span>{attentionClaims.length + openTickets.length} 项</span>
          </div>
          <div className="attention-list">
            <button type="button" onClick={() => onChangeTab("claims")}>
              <TriangleAlert size={17} />
              <span><strong>{attentionClaims.length} 条结论待确认</strong><small>查看缺少证据、矛盾或需复核的结论</small></span>
            </button>
            <button type="button" onClick={() => onChangeTab("trace")}>
              <ClipboardList size={17} />
              <span><strong>{openTickets.length} 个未完成复核工单</strong><small>查看补充检索与人工处理状态</small></span>
            </button>
            <button type="button" onClick={() => onChangeTab("matrix")}>
              <Table2 size={17} />
              <span><strong>查看产品差异</strong><small>按相同维度比较目标产品与竞品</small></span>
            </button>
          </div>
        </article>
      </div>
      <ResultSummary result={result} />
    </section>
  );
}

function TemplateBlock({ title, items }) {
  return (
    <div>
      <strong>{title}</strong>
      <div className="tag-row">
        {items.map((item) => <span key={item}>{item}</span>)}
      </div>
    </div>
  );
}

function TraceView({ result, onTicketAction }) {
  return (
    <section className="content-grid trace-grid">
      <div className="timeline">
        {result.trace.map((event, index) => (
          <article className="trace-item" key={event.event_id}>
            <div className="trace-index">{String(index + 1).padStart(2, "0")}</div>
            <div>
              <div className="trace-head">
                <strong>{translateNodeName(event.agent)}</strong>
                <span>{translateEventType(event.event_type)}</span>
              </div>
              <p>{translateStructuredText(event.summary)}</p>
              {(event.provider || event.provider_request_id || event.token_count != null || event.latency_ms != null || event.prompt_name) && (
                <dl className="trace-meta">
                  <div><dt>Provider</dt><dd>{event.provider || "-"}</dd></div>
                  <div><dt>请求 ID</dt><dd>{event.provider_request_id || "-"}</dd></div>
                  <div><dt>Token</dt><dd>{event.token_count ?? "-"}</dd></div>
                  <div><dt>耗时</dt><dd>{event.latency_ms ?? "-"} ms</dd></div>
                </dl>
              )}
              {(event.input_summary || event.output_summary || event.prompt_name) && (
                <div className="trace-audit">
                  {event.prompt_name && (
                    <div>
                      <strong>提示词</strong>
                      <span>{event.prompt_name}: {event.prompt}</span>
                    </div>
                  )}
                  {event.input_summary && (
                    <div>
                      <strong>输入</strong>
                      <span>{event.input_summary}</span>
                    </div>
                  )}
                  {event.output_summary && (
                    <div>
                      <strong>输出</strong>
                      <span>{event.output_summary}</span>
                    </div>
                  )}
                </div>
              )}
              {event.related_ids?.length > 0 && <div className="related-row">{event.related_ids.map((id) => <span key={id}>{id}</span>)}</div>}
            </div>
          </article>
        ))}
      </div>
      <div className="ticket-stack">
        <h2>复核工单</h2>
        {result.review_tickets.map((ticket) => {
          const resolutionText = ticket.resolution_summary || ticket.resolution_note;
          return (
            <article className="ticket" key={ticket.ticket_id}>
              <div className="ticket-top">
                <span className={`badge ${ticket.status}`}>{statusCopy[ticket.status] || ticket.status}</span>
                <span>{translateNodeName(ticket.target_node)}</span>
              </div>
              <strong>{translateTicketText(ticket.reason)}</strong>
              <p>{translateTicketText(ticket.required_action)}</p>
              <dl className="ticket-fields">
                <div><dt>产品</dt><dd>{ticket.product || "-"}</dd></div>
                <div><dt>缺失证据</dt><dd>{translateTaxonomyValue(ticket.missing_evidence_type, evidenceTypeCopy)}</dd></div>
                <div><dt>来源偏好</dt><dd>{translateTaxonomyValue(ticket.preferred_source_type, sourcePreferenceCopy)}</dd></div>
                <div><dt>重跑次数</dt><dd>{ticket.rerun_count || 0}/{ticket.max_reruns || 0}</dd></div>
              </dl>
              {resolutionText && <p className="note">{translateTicketText(resolutionText)}</p>}
              <div className="action-row">
                <button type="button" onClick={() => onTicketAction(ticket.ticket_id, "accept")} disabled={!["open", "accepted"].includes(ticket.status)}>
                  <CheckCircle2 size={14} />
                  接收
                </button>
                <button type="button" onClick={() => onTicketAction(ticket.ticket_id, "rerun")} disabled={ticket.status === "blocked"}>
                  <RefreshCw size={14} />
                  重跑
                </button>
                <button type="button" onClick={() => onTicketAction(ticket.ticket_id, "unavailable")} disabled={["dismissed", "blocked"].includes(ticket.status)}>
                  <TriangleAlert size={14} />
                  证据不可得
                </button>
                <button type="button" onClick={() => onTicketAction(ticket.ticket_id, "downgrade")} disabled={["dismissed", "blocked"].includes(ticket.status)}>
                  <ArrowRight size={14} />
                  降级结论
                </button>
                <button type="button" onClick={() => onTicketAction(ticket.ticket_id, "resolve")} disabled={ticket.status === "resolved"}>
                  <ShieldCheck size={14} />
                  解决
                </button>
                <button type="button" onClick={() => onTicketAction(ticket.ticket_id, "dismiss")} disabled={ticket.status === "dismissed"}>
                  <XCircle size={14} />
                  忽略
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function SearchPlanView({ result }) {
  const ticketById = new Map(result.review_tickets.map((ticket) => [ticket.ticket_id, ticket]));
  return (
    <section className="plan-list">
      {result.search_plan?.queries.map((query, index) => {
        const ticket = ticketById.get(query.related_ticket_id);
        return (
          <article className={`plan-item ${query.is_supplemental ? "supplemental" : ""}`} key={`${query.query}-${index}`}>
            <div className="plan-index">{String(index + 1).padStart(2, "0")}</div>
            <div>
              <div className="plan-head">
                <strong>{query.query}</strong>
                <span className={`badge ${query.priority}`}>{translatePriority(query.priority)}</span>
              </div>
              <dl className="plan-fields">
                <div><dt>产品</dt><dd>{query.product}</dd></div>
                <div><dt>预期证据</dt><dd>{translateTaxonomyValue(query.expected_evidence, evidenceTypeCopy)}</dd></div>
                <div><dt>来源偏好</dt><dd>{translateTaxonomyValue(query.source_preference, sourcePreferenceCopy)}</dd></div>
                <div><dt>来源</dt><dd>{query.is_supplemental ? "复核工单补充检索" : "初始检索计划"}</dd></div>
              </dl>
              {ticket && <p className="note">由 {ticket.ticket_id} 触发：{ticket.reason}</p>}
            </div>
          </article>
        );
      })}
    </section>
  );
}

function MatrixView({ result }) {
  const products = [result.task.config.target_product, ...result.task.config.competitors];
  const dimensions = result.task.config.domain === "ai_tools"
    ? ["positioning", "feature", "agent_capability", "target_user", "pricing", "security"]
    : ["positioning", "feature", "target_user", "pricing", "security"];
  const rows = dimensions.map((dimension) => ({
    dimension,
    claims: products.map((product) => ({
      product,
      claim: result.claims.find((item) => item.product === product && item.claim_type === dimension),
    })),
  }));
  return (
    <section className="matrix-stack">
      <FeatureExplorationPanel result={result} products={products} />

      <div className="matrix-section">
        <div className="section-heading">
          <h2>具体差异对比表</h2>
          <p>这里优先展示每个产品在同一维度上的内容差异，符合用户预期的“竞品对比表”。</p>
        </div>
        <div className="matrix-wrap">
          <table className="matrix comparison-table">
            <thead>
              <tr>
                <th>对比维度</th>
                {products.map((product) => <th key={product}>{product}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map(({ dimension, claims }) => (
                <tr key={dimension}>
                  <th>{dimensionCopy[dimension] || dimension}</th>
                  {claims.map(({ product, claim }) => (
                    <td key={`${product}-${dimension}`}>
                      {claim ? (
                        <>
                          <p>{claim.claim}</p>
                          {claim.note && <span>{claim.note}</span>}
                        </>
                      ) : (
                        <span>当前证据不足，尚未形成可对比结论。</span>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="matrix-section">
        <div className="section-heading">
          <h2>证据覆盖对比</h2>
          <p>这张表用于审计每个结论是否有证据支撑，不等同于功能差异本身。</p>
        </div>
        <div className="matrix-wrap">
          <table className="matrix evidence-matrix">
            <thead>
              <tr>
                <th>维度</th>
                {products.map((product) => <th key={product}>{product}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map(({ dimension, claims }) => (
                <tr key={dimension}>
                  <th>{dimensionCopy[dimension] || dimension}</th>
                  {claims.map(({ product, claim }) => (
                    <td key={`${product}-${dimension}`}>
                      {claim ? (
                        <>
                          <strong>{statusCopy[claim.verified_status]}</strong>
                          <span>{claim.supporting_evidence.length} 条证据</span>
                        </>
                      ) : (
                        <span>未评估</span>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function FeatureExplorationPanel({ result, products }) {
  const productSources = new Map();
  products.forEach((product) => {
    const source = result.sources.find((item) => item.product === product && item.url) || null;
    productSources.set(product, source);
  });
  const defaultFeature = result.task.config.analysis_goals.find((goal) => /搜索|search/i.test(goal)) || "搜索功能";
  return (
    <section className="feature-path-panel">
      <div className="section-heading">
        <h2>具体功能探索路径</h2>
        <p>如果要比较两个网站的某个具体功能，例如搜索功能差异，应使用 Playwright 进入真实页面、点击同一用户路径并记录结果，而不是只看静态功能层级。</p>
      </div>
      <div className="feature-path-grid">
        {products.map((product) => {
          const source = productSources.get(product);
          const url = source?.url || "";
          return (
            <article key={product}>
              <div className="ticket-top">
                <strong>{product}</strong>
                {url ? <a href={url} target="_blank" rel="noreferrer">打开入口</a> : <span className="badge pending">缺少入口</span>}
              </div>
              <ol>
                <li>打开产品官网或当前最高相关来源。</li>
                <li>定位“{defaultFeature}”入口，例如搜索框、搜索图标或导航搜索。</li>
                <li>输入同一关键词，记录联想词、筛选项、结果排序、空状态和加载反馈。</li>
                <li>截图保存关键状态，用同一维度和竞品结果做表格对比。</li>
              </ol>
              {url && <code>{`open ${url} -> click 搜索入口 -> type 统一关键词 -> compare result UI`}</code>}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function ClaimsView({ result, onExcludeEvidence, onRestoreEvidence }) {
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [productFilter, setProductFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [sortBy, setSortBy] = useState("product");
  const [expandedIds, setExpandedIds] = useState(() => new Set(result.claims.slice(0, 3).map((claim) => claim.claim_id)));
  const evidenceById = new Map(result.evidence.map((item) => [item.evidence_id, item]));
  const sourceById = new Map(result.sources.map((source) => [source.source_id, source]));
  const products = unique(result.claims.map((claim) => claim.product));
  const statuses = unique(result.claims.map((claim) => claim.verified_status));
  const claimTypes = unique(result.claims.map((claim) => claim.claim_type));
  const filteredClaims = result.claims
    .filter((claim) => statusFilter === "all" || claim.verified_status === statusFilter)
    .filter((claim) => productFilter === "all" || claim.product === productFilter)
    .filter((claim) => typeFilter === "all" || claim.claim_type === typeFilter)
    .filter((claim) => {
      const needle = query.trim().toLowerCase();
      if (!needle) return true;
      return `${claim.product} ${claim.claim_type} ${claim.claim} ${claim.note}`.toLowerCase().includes(needle);
    })
    .sort((a, b) => {
      if (sortBy === "status") return a.verified_status.localeCompare(b.verified_status) || a.product.localeCompare(b.product);
      if (sortBy === "evidence") return b.supporting_evidence.length - a.supporting_evidence.length || a.product.localeCompare(b.product);
      if (sortBy === "type") return a.claim_type.localeCompare(b.claim_type) || a.product.localeCompare(b.product);
      return a.product.localeCompare(b.product) || a.claim_type.localeCompare(b.claim_type);
    });

  useEffect(() => {
    setExpandedIds(new Set(result.claims.slice(0, 3).map((claim) => claim.claim_id)));
  }, [result.task.task_id]);

  function toggleClaim(claimId) {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(claimId)) {
        next.delete(claimId);
      } else {
        next.add(claimId);
      }
      return next;
    });
  }

  return (
    <section className="claim-list">
      <div className="claim-toolbar">
        <label>
          <span>搜索</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="产品、结论、备注..." />
        </label>
        <label>
          <span>状态</span>
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="all">全部状态</option>
            {statuses.map((status) => <option key={status} value={status}>{statusCopy[status] || status}</option>)}
          </select>
        </label>
        <label>
          <span>产品</span>
          <select value={productFilter} onChange={(event) => setProductFilter(event.target.value)}>
            <option value="all">全部产品</option>
            {products.map((product) => <option key={product} value={product}>{product}</option>)}
          </select>
        </label>
        <label>
          <span>类型</span>
          <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
            <option value="all">全部结论类型</option>
            {claimTypes.map((claimType) => <option key={claimType} value={claimType}>{dimensionCopy[claimType] || claimType}</option>)}
          </select>
        </label>
        <label>
          <span>排序</span>
          <select value={sortBy} onChange={(event) => setSortBy(event.target.value)}>
            <option value="product">按产品</option>
            <option value="status">按状态</option>
            <option value="evidence">按证据数量</option>
            <option value="type">按结论类型</option>
          </select>
        </label>
      </div>
      <div className="claim-count">当前显示 {filteredClaims.length} / {result.claims.length} 条结论</div>
      {filteredClaims.length === 0 && (
        <div className="empty-filter">
          当前筛选条件下没有匹配的结论。
        </div>
      )}
      {filteredClaims.map((claim) => {
        const expanded = expandedIds.has(claim.claim_id);
        return (
          <article className="claim" key={claim.claim_id}>
            <div className="claim-status">
              {claim.verified_status === "passed" ? <CheckCircle2 size={18} /> : <TriangleAlert size={18} />}
              <span className={`badge ${claim.verified_status}`}>{statusCopy[claim.verified_status]}</span>
              <button type="button" className="link-button" onClick={() => toggleClaim(claim.claim_id)}>
                {expanded ? "收起证据" : "展开证据"}
              </button>
            </div>
            <div>
              <div className="claim-title">
                <strong>{claim.product}</strong>
                <span>{dimensionCopy[claim.claim_type] || claim.claim_type}</span>
              </div>
              <p>{claim.claim}</p>
              {claim.note && <p className="note">{claim.note}</p>}
              {expanded && (
                <div className="evidence-detail-list">
                  {claim.supporting_evidence.length ? (
                    claim.supporting_evidence.map((id) => {
                      const evidence = evidenceById.get(id);
                      const source = evidence ? sourceById.get(evidence.source_id) : null;
                      return (
                        <div className={`evidence-detail ${evidence?.status || "active"}`} key={id}>
                          <div>
                            <strong>{evidence ? `${translateTaxonomyValue(evidence.evidence_type, evidenceTypeCopy)}：${translateStructuredText(evidence.summary)}` : id}</strong>
                            {source && <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>}
                          </div>
                          {source && (
                            <dl>
                              <div><dt>来源</dt><dd>{translateTaxonomyValue(source.source_type, sourcePreferenceCopy)}</dd></div>
                              <div><dt>定位</dt><dd>{evidence.quote_or_locator}</dd></div>
                              <div><dt>置信度</dt><dd>{translateConfidence(evidence.confidence)}</dd></div>
                              <div><dt>风险</dt><dd>{translateRisk(evidence.risk)}</dd></div>
                              <div><dt>状态</dt><dd>{statusCopy[evidence.status || "active"]}</dd></div>
                            </dl>
                          )}
                          {evidence?.excluded_reason && <p className="note">{evidence.excluded_reason}</p>}
                          {evidence && (
                            <div className="action-row compact">
                              {evidence.status === "excluded" ? (
                                <button type="button" onClick={() => onRestoreEvidence(evidence.evidence_id)}>
                                  <RefreshCw size={14} />
                                  恢复证据
                                </button>
                              ) : (
                                <button type="button" onClick={() => onExcludeEvidence(evidence.evidence_id)}>
                                  <Ban size={14} />
                                  排除证据
                                </button>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })
                  ) : (
                    <span className="missing">还没有绑定支撑证据</span>
                  )}
                </div>
              )}
            </div>
          </article>
        );
      })}
    </section>
  );
}

function unique(values) {
  return [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

function translateTaxonomyValue(value, dictionary) {
  if (!value) return "-";
  return dictionary[value] || dictionary[value.toLowerCase?.()] || value;
}

function translateNodeName(value) {
  return translateTaxonomyValue(value, nodeCopy);
}

function translateEventType(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replaceAll("completed", "完成")
    .replaceAll("created", "已创建")
    .replaceAll("selected", "已选择")
    .replaceAll("generated", "已生成")
    .replaceAll("normalized", "已标准化")
    .replaceAll("extracted", "已提取")
    .replaceAll("applied", "已应用");
}

function translatePriority(value) {
  return { high: "高优先级", medium: "中优先级", low: "低优先级" }[value] || value;
}

function translateTicketText(value) {
  if (!value) return "";
  const text = String(value).trim();
  const exact = {
    "Search official pricing page, or keep pricing model uncertain.": "检索官方定价页；如果仍无证据，定价模型应保持不确定状态。",
    "Search official feature/product documentation before finalizing the feature tree.": "在最终确定功能证据地图前，请补充检索官方功能或产品文档。",
    "Search official team/persona/customer material before finalizing personas.": "在最终确定用户画像前，请补充检索官方团队、用户画像或客户材料。",
    "Run a contradiction-oriented source check before treating the comparison as externally publishable.": "在对外发布前，请执行一次面向矛盾信息的来源检查。",
    "Collect detailed pricing data from official sources and third-party reports.": "从官方来源和第三方报告中补充收集更详细的定价数据。",
    "Research security practices and user data handling.": "补充调研安全实践和用户数据处理方式。",
    "Search for contradictory reports or studies about YouTube's homepage design or monetization.": "检索关于 YouTube 首页设计或商业化策略的矛盾报告与研究。",
    "Contradiction scan has no explicit confirming or conflicting evidence.": "矛盾扫描尚未找到明确的确认或冲突证据。",
    "Supplemental research added matching evidence.": "补充检索已找到匹配证据。",
    "No matching fixture source was available; related claims remain uncertain.": "没有可用的匹配来源；相关结论仍保持不确定状态。",
    "Review Ticket reached the maximum rerun count and requires manual intervention.": "复核工单已达到最大重跑次数，需要人工处理。",
    "Required evidence was marked unavailable by reviewer.": "复核人已标记所需证据不可得。",
    "Related conclusion was downgraded by reviewer.": "复核人已降级相关结论。",
    "Dismissed by user.": "用户已忽略该工单。",
    "Resolved.": "已解决。",
  };
  if (exact[text]) return exact[text];

  let match = text.match(/^(.+) lacks official (.+) evidence\.$/);
  if (match) {
    return `${match[1]} 缺少官方${translateTaxonomyValue(match[2].replaceAll(" ", "_"), evidenceTypeCopy)}证据。`;
  }

  match = text.match(/^Uncertain claims exist regarding pricing strategies for monetization \(e\.g\., membership tiers, ad pricing\)\.$/);
  if (match) {
    return "关于商业化定价策略的结论仍不确定，例如会员层级或广告定价。";
  }

  match = text.match(/^Uncertain claims about security features or data privacy\.$/);
  if (match) {
    return "关于安全功能或数据隐私的结论仍不确定。";
  }

  match = text.match(/^No contradiction evidence collected, which may weaken comparative analysis\.$/);
  if (match) {
    return "尚未收集到矛盾校验证据，可能削弱对比分析的可靠性。";
  }

  return text
    .replaceAll("Review Ticket", "复核工单")
    .replaceAll("review ticket", "复核工单")
    .replaceAll("official pricing", "官方定价")
    .replaceAll("official feature", "官方功能")
    .replaceAll("official target user", "官方目标用户")
    .replaceAll("official", "官方")
    .replaceAll("evidence", "证据")
    .replaceAll("external publication", "对外发布")
    .replaceAll("manual intervention", "人工处理");
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function StructuredReportPanel({ report }) {
  if (!report) return null;
  return (
    <div className="structured-report">
      {report.feature_tree && (
        <section>
          <FeatureEvidenceMapPanel tree={report.feature_tree} />
        </section>
      )}
      {report.pricing_model && (
        <section>
          <h3>定价模型</h3>
          <div className="pricing-grid">
            {report.pricing_model.plans.map((plan) => (
              <article key={plan.product}>
                <div className="ticket-top">
                  <strong>{plan.product}</strong>
                  <span className={`badge ${plan.confidence}`}>{translateConfidence(plan.confidence)}</span>
                </div>
                <p>{translateStructuredText(plan.model)}</p>
                <span>{plan.tiers.length ? plan.tiers.map(translateStructuredText).join(" / ") : "没有已验证的价格层级"}</span>
                <small>{translateStructuredText(plan.monetization_signal)}</small>
              </article>
            ))}
          </div>
          <p className="note">{translateStructuredText(report.pricing_model.comparison_summary)}</p>
        </section>
      )}
      {report.user_personas?.length > 0 && (
        <section>
          <h3>用户画像</h3>
          <div className="persona-grid">
            {report.user_personas.map((persona) => (
              <article key={persona.persona_id}>
                <strong>{translateStructuredText(persona.name)}</strong>
                <span>{translateStructuredText(persona.segment)}</span>
                <p>{persona.jobs_to_be_done.map(translateStructuredText).join("；")}</p>
                <small>决策标准：{persona.decision_criteria.map(translateStructuredText).join(" / ")}</small>
              </article>
            ))}
          </div>
        </section>
      )}
      {report.swot && (
        <section>
          <h3>SWOT</h3>
          <div className="swot-grid">
            {[
              ["优势", report.swot.strengths],
              ["劣势", report.swot.weaknesses],
              ["机会", report.swot.opportunities],
              ["威胁", report.swot.threats],
            ].map(([label, items]) => (
              <article key={label}>
                <strong>{label}</strong>
                {items.map((item) => <span key={item}>{translateStructuredText(item)}</span>)}
              </article>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function FeatureEvidenceMapPanel({ tree }) {
  const [query, setQuery] = useState("");
  const [expandedKeys, setExpandedKeys] = useState(() => new Set(["root"]));
  const normalizedQuery = query.trim().toLowerCase();
  const allKeys = useMemo(() => collectFeatureKeys(tree.root), [tree]);
  const visibleKeys = useMemo(
    () => normalizedQuery ? new Set(collectVisibleFeatureKeys(tree.root, normalizedQuery)) : null,
    [tree, normalizedQuery],
  );
  const matchCount = useMemo(() => countFeatureMatches(tree.root, normalizedQuery), [tree, normalizedQuery]);

  useEffect(() => {
    setExpandedKeys(normalizedQuery ? new Set(collectVisibleFeatureKeys(tree.root, normalizedQuery)) : new Set(["root"]));
  }, [tree, normalizedQuery]);

  function toggleNode(path) {
    setExpandedKeys((current) => {
      const next = new Set(current);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }

  return (
    <div className="feature-tree-panel">
      <div className="feature-tree-head">
        <div>
          <h3>功能证据地图</h3>
          <p className="note">实测节点来自明确的浏览器点击路径；文档/搜索推断节点只能作为辅助研究线索。</p>
        </div>
        <div className="feature-tree-actions">
          <button type="button" onClick={() => setExpandedKeys(new Set(allKeys))}>全部展开</button>
          <button type="button" onClick={() => setExpandedKeys(new Set(["root"]))}>只看根节点</button>
        </div>
      </div>
      <label className="feature-tree-search">
        <span>搜索功能证据地图</span>
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入功能、描述或 evidence id" />
      </label>
      {normalizedQuery && <p className="claim-count">找到 {matchCount} 个直接匹配节点，相关路径已自动展开。</p>}
      <FeatureNode
        node={tree.root}
        path="root"
        query={normalizedQuery}
        expandedKeys={expandedKeys}
        visibleKeys={visibleKeys}
        onToggle={toggleNode}
      />
      {normalizedQuery && matchCount === 0 && <p className="empty-filter">功能证据地图中没有匹配内容。</p>}
      {tree.coverage_note && <p className="note">{translateStructuredText(tree.coverage_note)}</p>}
    </div>
  );
}

function FeatureNode({ node, path, query, expandedKeys, visibleKeys, onToggle }) {
  if (visibleKeys && !visibleKeys.has(path)) return null;
  const hasChildren = node.children?.length > 0;
  const expanded = expandedKeys.has(path);
  const matched = Boolean(query && featureNodeMatches(node, query));
  return (
    <div className={`feature-node ${matched ? "matched" : ""}`}>
      <div className="feature-node-row">
        <button
          type="button"
          className="feature-toggle"
          onClick={() => onToggle(path)}
          disabled={!hasChildren}
          aria-label={expanded ? `收起 ${node.name}` : `展开 ${node.name}`}
        >
          {hasChildren ? (expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />) : null}
        </button>
        <div>
          <div className="feature-title-line">
            <strong>{translateFeatureNodeText(node.name)}</strong>
            {node.verification_method && <span className={`method-badge ${node.verification_method}`}>{translateVerificationMethod(node.verification_method)}</span>}
          </div>
          <span>{translateStructuredText(node.description)}</span>
          {node.interaction_path?.length > 0 && <small className="feature-path">路径：{node.interaction_path.map(translateStructuredText).join(" > ")}</small>}
          {node.evidence_ids?.length > 0 && <small>证据：{node.evidence_ids.join(", ")}</small>}
        </div>
      </div>
      {hasChildren && expanded && (
        <div className="feature-children">
          {node.children.map((child, index) => (
            <FeatureNode
              key={`${path}-${child.name}-${index}`}
              node={child}
              path={`${path}.${index}`}
              query={query}
              expandedKeys={expandedKeys}
              visibleKeys={visibleKeys}
              onToggle={onToggle}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function collectFeatureKeys(node, path = "root") {
  return [
    path,
    ...(node.children || []).flatMap((child, index) => collectFeatureKeys(child, `${path}.${index}`)),
  ];
}

function collectVisibleFeatureKeys(node, query, path = "root") {
  const childKeys = (node.children || []).flatMap((child, index) => collectVisibleFeatureKeys(child, query, `${path}.${index}`));
  if (featureNodeMatches(node, query) || childKeys.length) {
    return [path, ...childKeys];
  }
  return [];
}

function countFeatureMatches(node, query) {
  if (!query) return 0;
  const own = featureNodeMatches(node, query) ? 1 : 0;
  return own + (node.children || []).reduce((total, child) => total + countFeatureMatches(child, query), 0);
}

function featureNodeMatches(node, query) {
  return [
    node.name,
    node.description,
    node.verification_method,
    ...(node.interaction_path || []),
    translateFeatureNodeText(node.name),
    translateStructuredText(node.description),
    ...(node.evidence_ids || []),
  ].join(" ").toLowerCase().includes(query);
}

function translateVerificationMethod(value) {
  return {
    browser_walkthrough: "实测",
    source_inference: "文档推断",
    unverified: "未实测",
    mixed: "混合",
  }[value] || value;
}

function translateConfidence(value) {
  return {
    high: "高",
    medium: "中",
    low: "低",
  }[value] || value;
}

function translateRisk(value) {
  return {
    low: "低",
    medium: "中",
    high: "高",
    none: "无明显风险",
  }[value] || translateStructuredText(value);
}

function translateFeatureNodeText(value) {
  return translateStructuredText(value)
    .replace("competitive feature map", "功能证据地图");
}

function translateStructuredText(value) {
  return String(value || "")
    .replaceAll("FeatureTree groups product workflow, agent/AI workflow, and team readiness signals.", "功能证据地图按产品工作流、AI/Agent 工作流和团队就绪度组织能力信号。")
    .replace(/Evidence-backed capability tree for (.+?)\./g, "$1 的证据支撑能力地图。")
    .replace(/(\d+)\/(\d+) feature-tree leaves have active evidence; uncovered leaves become review-ticket follow-up\./g, "$1/$2 个功能叶子节点已有有效证据；未覆盖节点会进入复核工单跟进。")
    .replaceAll("Published subscription tiers", "公开订阅层级")
    .replaceAll("Published plan structure", "已公开的方案结构")
    .replaceAll("All compared products have pricing evidence.", "所有对比产品都有定价证据。")
    .replaceAll("Individual AI-assisted developer", "使用 AI 辅助的个人开发者")
    .replaceAll("Builder / IC engineer", "一线开发者 / 独立贡献工程师")
    .replaceAll("Engineering team lead", "工程团队负责人")
    .replaceAll("Team / platform buyer", "团队或平台采购决策者")
    .replaceAll("Complete coding tasks faster inside the development environment.", "在开发环境中更快完成编码任务。")
    .replaceAll("Use codebase-aware assistance without constantly switching tools.", "在不频繁切换工具的情况下使用代码库感知辅助。")
    .replaceAll("Standardize AI coding assistance across a team.", "在团队范围内标准化 AI 编码辅助。")
    .replaceAll("Evaluate productivity upside against security, privacy, and cost controls.", "在评估生产力收益时同时衡量安全、隐私和成本控制。")
    .replaceAll("Quality of codebase context", "代码库上下文质量")
    .replaceAll("Speed of iteration", "迭代速度")
    .replaceAll("Transparent pricing and usage limits", "透明的定价和使用限制")
    .replaceAll("Admin controls and security posture", "管理控制与安全姿态")
    .replaceAll("Team plan clarity", "团队方案清晰度")
    .replaceAll("Evidence-backed feature coverage", "有证据支撑的功能覆盖")
    .replaceAll("The report binds claims to evidence IDs, making product and PM review auditable.", "报告将结论绑定到证据 ID，方便产品和 PM 审计复核。")
    .replaceAll("Missing or downgraded evidence is excluded from final claims instead of being treated as fact.", "缺失或降级的证据不会被当作事实写入最终结论。")
    .replace(/(\d+) target-adjacent claim\(s\) still need reviewer attention\./g, "$1 条目标相关结论仍需复核关注。")
    .replaceAll("Use feature-tree gaps to prioritize follow-up research and product messaging comparison.", "利用功能证据缺口确定后续调研和产品话术对比优先级。")
    .replace(/Compare (\d+) competitor\(s\) through pricing and persona fit rather than a single score\./g, "围绕定价和用户画像适配度比较 $1 个竞品，而不是只给单一分数。")
    .replace(/(\d+) unresolved Review Ticket\(s\) can block external publication\./g, "$1 个未解决复核工单可能阻塞对外发布。")
    .replaceAll("Live provider results may differ from demo fixtures, so provider mode must be disclosed.", "实时 Provider 结果可能因来源变化而不同，因此需要披露 Provider 模式。")
    .replaceAll("Review Ticket(s)", "复核工单")
    .replaceAll("feature-tree", "功能证据地图");
}

function ReportView({ result }) {
  const report = result.report;
  const [exported, setExported] = useState(null);
  const [exportError, setExportError] = useState("");

  async function handleExport(allowDraft) {
    setExportError("");
    setExported(null);
    try {
      setExported(await exportReport(result.task.task_id, allowDraft));
    } catch (err) {
      setExportError(err.message);
    }
  }

  return (
    <section className="report">
      <div className="report-head">
        <div>
          <h2>{displayReportSectionTitle(report?.title)}</h2>
          <div className="report-meta">
            <span className={`badge ${report?.status || "draft"}`}>{statusCopy[report?.status] || report?.status}</span>
            <span>{report?.claim_count || 0} 条结论</span>
            <span>{percent(report?.evidence_coverage_rate)} 证据覆盖率</span>
          </div>
        </div>
        <div className="action-row">
          <button type="button" onClick={() => handleExport(false)}>
            <Download size={14} />
            导出
          </button>
          <button type="button" onClick={() => handleExport(true)}>
            <ArrowRight size={14} />
            导出草稿
          </button>
        </div>
      </div>
      {report?.status === "reviewing" && (
        <div className="reviewing-banner">
          <TriangleAlert size={16} />
          <span>这份报告仍有未解决的复核工单；在复核队列清空前，请使用“导出草稿”。</span>
        </div>
      )}
      {report?.sections?.length > 0 && (
        <details className="report-directory">
          <summary>查看报告章节状态（{report.sections.length} 个章节）</summary>
          <div className="section-strip">
            {report.sections.map((section) => (
              <div key={section.section_id}>
                <span className={`badge ${section.status}`}>{statusCopy[section.status] || section.status}</span>
                <strong>{displayReportSectionTitle(section.title)}</strong>
              </div>
            ))}
          </div>
        </details>
      )}
      <StructuredReportPanel report={report} />
      {exportError && <p className="error inline">{exportError}</p>}
      {exported && (
        <div className="export-box">
          <strong>{exported.filename}</strong>
          {exported.warning && <p className="field-warning">{exported.warning}</p>}
          <textarea readOnly value={exported.content} rows={8} />
        </div>
      )}
      <details className="raw-report">
        <summary>查看原始 Markdown 报告</summary>
        <p>保留结构化报告生成时的完整文本与必要英文术语，便于审计和导出。</p>
        <MarkdownReport markdown={normalizeReportMarkdown(report?.markdown || "")} />
      </details>
    </section>
  );
}

function displayReportSectionTitle(title) {
  return String(title || "")
    .replace("功能树 FeatureTree", "功能证据地图")
    .replaceAll("Competitor Analysis", "竞品分析")
    .replace("定价模型 PricingModel", "定价模型")
    .replace("用户画像 UserPersona", "用户画像")
    .replaceAll("Caveats", "限制说明")
    .replace(/^(定价模型|用户画像)\s+\1$/, "$1");
}

function normalizeReportMarkdown(markdown) {
  return String(markdown || "")
    .replaceAll("功能树 FeatureTree", "功能证据地图")
    .replaceAll("## 功能树", "## 功能证据地图")
    .replaceAll("FeatureTree", "功能证据地图")
    .replaceAll("Competitor Analysis", "竞品分析")
    .replaceAll("PricingModel", "定价模型")
    .replaceAll("UserPersona", "用户画像")
    .replaceAll("Review Tickets", "复核工单")
    .replaceAll("Review Ticket", "复核工单")
    .replaceAll("Trace Events", "追踪事件")
    .replaceAll("Caveats", "限制说明")
    .replaceAll("Search provider", "搜索提供方")
    .replaceAll("LLM provider", "LLM Provider")
    .replace(/## (定价模型|用户画像)\s+\1/g, "## $1");
}

function MarkdownReport({ markdown }) {
  const lines = markdown.split(/\r?\n/);
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    if (/^\|(.+\|)+$/.test(line) && /^\|?[-:\s|]+\|?$/.test(lines[index + 1] || "")) {
      const tableLines = [line];
      index += 2;
      while (index < lines.length && /^\|(.+\|)+$/.test(lines[index])) {
        tableLines.push(lines[index]);
        index += 1;
      }
      blocks.push(<MarkdownTable key={`table-${index}`} lines={tableLines} />);
      continue;
    }
    if (line.startsWith("### ")) {
      blocks.push(<h4 key={index}>{renderInlineMarkdown(line.slice(4))}</h4>);
      index += 1;
      continue;
    }
    if (line.startsWith("## ")) {
      blocks.push(<h3 key={index}>{renderInlineMarkdown(line.slice(3))}</h3>);
      index += 1;
      continue;
    }
    if (line.startsWith("# ")) {
      blocks.push(<h2 key={index}>{renderInlineMarkdown(line.slice(2))}</h2>);
      index += 1;
      continue;
    }
    if (line.startsWith("> ")) {
      const quoteLines = [];
      while (index < lines.length && lines[index].startsWith("> ")) {
        quoteLines.push(lines[index].slice(2));
        index += 1;
      }
      blocks.push(<blockquote key={`quote-${index}`}>{quoteLines.map((item, itemIndex) => <p key={itemIndex}>{renderInlineMarkdown(item)}</p>)}</blockquote>);
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*]\s+/, ""));
        index += 1;
      }
      blocks.push(<ul key={`list-${index}`}>{items.map((item, itemIndex) => <li key={itemIndex}>{renderInlineMarkdown(item)}</li>)}</ul>);
      continue;
    }
    if (/^\s*\d+[.)、]\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^\s*\d+[.)、]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*\d+[.)、]\s+/, ""));
        index += 1;
      }
      blocks.push(<ol key={`olist-${index}`}>{items.map((item, itemIndex) => <li key={itemIndex}>{renderInlineMarkdown(item)}</li>)}</ol>);
      continue;
    }

    const paragraph = [line.trim()];
    index += 1;
    while (index < lines.length && lines[index].trim() && !/^(#{1,3}\s|>\s|\s*[-*]\s+|\s*\d+[.)、]\s+|\|)/.test(lines[index])) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(<p key={`p-${index}`}>{renderInlineMarkdown(paragraph.join(" "))}</p>);
  }

  return <article className="markdown-report">{blocks}</article>;
}

function MarkdownTable({ lines }) {
  const rows = lines.map((line) => line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim()));
  const [head, ...body] = rows;
  return (
    <div className="markdown-table-wrap">
      <table className="markdown-table">
        <thead>
          <tr>{head.map((cell, index) => <th key={index}>{renderInlineMarkdown(cell)}</th>)}</tr>
        </thead>
        <tbody>
          {body.map((row, rowIndex) => (
            <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{renderInlineMarkdown(cell)}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderInlineMarkdown(text) {
  const parts = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let lastIndex = 0;
  String(text).replace(pattern, (match, _token, offset) => {
    if (offset > lastIndex) parts.push(String(text).slice(lastIndex, offset));
    if (match.startsWith("**")) {
      parts.push(<strong key={`${offset}-strong`}>{match.slice(2, -2)}</strong>);
    } else if (match.startsWith("`")) {
      parts.push(<code key={`${offset}-code`}>{match.slice(1, -1)}</code>);
    } else {
      const linkMatch = match.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      parts.push(<a key={`${offset}-link`} href={linkMatch?.[2] || "#"} target="_blank" rel="noreferrer">{linkMatch?.[1] || match}</a>);
    }
    lastIndex = offset + match.length;
    return match;
  });
  if (lastIndex < String(text).length) parts.push(String(text).slice(lastIndex));
  return parts;
}

function percent(value) {
  return `${Math.round((value || 0) * 100)}%`;
}

createRoot(document.getElementById("root")).render(<App />);
