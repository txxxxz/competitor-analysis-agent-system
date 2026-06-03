import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowRight,
  Ban,
  CheckCircle2,
  ClipboardList,
  Database,
  Download,
  FileText,
  GitBranch,
  ListChecks,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  Table2,
  TriangleAlert,
  XCircle,
} from "lucide-react";
import {
  acceptReviewTicket,
  createTaskV1,
  dismissReviewTicket,
  downgradeReviewTicket,
  excludeEvidence,
  exportReport,
  getDemoTasks,
  getTask,
  getTasks,
  markReviewTicketUnavailable,
  rerunReviewTicket,
  resolveReviewTicket,
  restoreEvidence,
  streamTaskRun,
} from "./api/client";
import "./styles/app.css";

const statusCopy = {
  passed: "Passed",
  uncertain: "Uncertain",
  blocked: "Blocked",
  pending: "Pending",
  unsupported: "Unsupported",
  contradicted: "Contradicted",
  stale: "Stale",
  downgraded: "Downgraded",
  active: "Active",
  excluded: "Excluded",
  created: "Created",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  open: "Open",
  accepted: "Accepted",
  rerun_started: "Rerun started",
  resolved: "Resolved",
  dismissed: "Dismissed",
};

const domainOptions = [
  ["ai_tools", "AI tools"],
  ["general_product", "General product"],
  ["saas", "SaaS"],
];

const strictnessOptions = ["high", "standard", "low"];

const competitorSuggestions = {
  ai_tools: ["GitHub Copilot", "Windsurf", "TRAE", "Codeium", "Replit"],
  general_product: ["Coda", "Airtable", "Confluence", "Asana", "Monday.com"],
  saas: ["HubSpot", "Salesforce", "Intercom", "Zendesk", "Pipedrive"],
};

function App() {
  const [demoTasks, setDemoTasks] = useState([]);
  const [selectedDemo, setSelectedDemo] = useState(null);
  const [taskForm, setTaskForm] = useState(null);
  const [result, setResult] = useState(null);
  const [activeTab, setActiveTab] = useState("trace");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [liveTrace, setLiveTrace] = useState([]);
  const [streamState, setStreamState] = useState(null);
  const [recentTasks, setRecentTasks] = useState([]);
  const [recentLoading, setRecentLoading] = useState(false);

  useEffect(() => {
    getDemoTasks()
      .then((tasks) => {
        setDemoTasks(tasks);
        if (tasks[0]) {
          setSelectedDemo(tasks[0]);
          setTaskForm(configToForm(tasks[0].config));
        }
      })
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    refreshRecentTasks();
  }, []);

  const metrics = useMemo(() => {
    if (!result) {
      return [
        ["Sources", "0"],
        ["Evidence", "0"],
        ["Claims", "0"],
        ["Tickets", "0"],
      ];
    }
    return [
      ["Sources", result.sources.length],
      ["Evidence", result.evidence.length],
      ["Claims", result.claims.length],
      ["Tickets", result.review_tickets.length],
    ];
  }, [result]);

  function selectDemo(demo) {
    setSelectedDemo(demo);
    setTaskForm(configToForm(demo.config));
  }

  async function launchTask() {
    if (!taskForm) return;
    setLoading(true);
    setError("");
    setActionMessage("");
    setResult(null);
    setLiveTrace([]);
    setStreamState(null);
    try {
      const task = await createTaskV1(formToConfig(taskForm));
      const workflowResult = await streamTaskRun(task.task_id, {
        onTrace: (event) => setLiveTrace((current) => [...current, event]),
        onState: (state) => setStreamState(state),
        onResult: (nextResult) => setResult(nextResult),
      });
      setResult(workflowResult);
      setActiveTab("trace");
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
      const update = await excludeEvidence(evidenceId, "Excluded during evidence review.");
      setResult((current) => applyEvidenceUpdate(current, update, "Marked stale because evidence was excluded."));
      setActionMessage(`Excluded ${evidenceId}; linked claims and report are now stale.`);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleEvidenceRestore(evidenceId) {
    setError("");
    setActionMessage("");
    try {
      const update = await restoreEvidence(evidenceId);
      setResult((current) => applyEvidenceUpdate(current, update, "Marked stale because evidence was restored and needs re-review."));
      setActionMessage(`Restored ${evidenceId}; linked claims remain stale until re-reviewed.`);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleTicketAction(ticketId, action) {
    setError("");
    setActionMessage("");
    try {
      const handlers = {
        accept: () => acceptReviewTicket(ticketId, "Accepted from review queue."),
        rerun: () => rerunReviewTicket(ticketId),
        resolve: () => resolveReviewTicket(ticketId, "Resolved from review queue."),
        dismiss: () => dismissReviewTicket(ticketId, "Dismissed from review queue."),
        unavailable: () => markReviewTicketUnavailable(ticketId, "Required evidence is unavailable after review."),
        downgrade: () => downgradeReviewTicket(ticketId, "Conclusion downgraded after review."),
      };
      const ticket = await handlers[action]();
      if (ticket.workflow_result) {
        setResult(ticket.workflow_result);
      } else {
        setResult((current) => applyTicketUpdate(current, ticket));
      }
      setActionMessage(`${ticket.ticket_id} is now ${statusCopy[ticket.status] || ticket.status}.`);
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

  async function loadRecentTask(taskId) {
    setError("");
    setActionMessage("");
    setLoading(true);
    try {
      const saved = await getTask(taskId);
      if (!saved.claims || !saved.report) {
        setActionMessage("This task exists but does not have a completed workflow result yet.");
        return;
      }
      setResult(saved);
      setLiveTrace([]);
      setStreamState(null);
      setActiveTab("trace");
      setActionMessage(`Loaded saved run for ${saved.task.config.target_product}.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><GitBranch size={20} /></div>
          <div>
            <strong>EvidenceGraph</strong>
            <span>Competitor Agent System</span>
          </div>
        </div>

        <section className="panel">
          <div className="panel-heading">
            <Search size={16} />
            <span>Demo Templates</span>
          </div>
          <div className="demo-list">
            {demoTasks.map((demo) => (
              <button
                key={demo.id}
                className={`demo-option ${selectedDemo?.id === demo.id ? "active" : ""}`}
                onClick={() => selectDemo(demo)}
              >
                <strong>{demo.name}</strong>
                <span>{demo.description}</span>
              </button>
            ))}
          </div>
        </section>

        {taskForm && <TaskForm form={taskForm} setForm={setTaskForm} />}

        <button className="run-button" onClick={launchTask} disabled={loading || !taskForm}>
          <Play size={17} fill="currentColor" />
          {loading ? "Running LangGraph..." : "Run analysis"}
        </button>
        {error && <p className="error">{error}</p>}

        <RecentRunsPanel
          tasks={recentTasks}
          activeTaskId={result?.task?.task_id}
          loading={recentLoading}
          onRefresh={refreshRecentTasks}
          onSelect={loadRecentTask}
        />

        <section className="panel quiet">
          <div className="panel-heading">
            <ShieldCheck size={16} />
            <span>Provider Mode</span>
          </div>
          <p><strong>Configured provider + safe fallback.</strong> Backend uses AnySearch when enabled in `.env`; mock fixtures remain available for empty results, request failures, and no-key demo runs.</p>
        </section>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">V1.2.1 Productized Closed Loop</p>
            <h1>Evidence-first competitor analysis with reviewable search, claims, and trust signals.</h1>
          </div>
          <div className="status-pill">
            <Activity size={16} />
            {result ? "Completed" : loading ? "Running" : "Ready"}
          </div>
        </header>

        <div className="metric-grid">
          {metrics.map(([label, value]) => (
            <div className="metric" key={label}>
              <span>{label}</span>
              <strong>{value}</strong>
            </div>
          ))}
        </div>

        {result ? (
          <>
            <TrustSummary summary={result.trust_summary} />
            <TaskConfig result={result} />
            {liveTrace.length > 0 && <StreamSummary liveTrace={liveTrace} streamState={streamState} />}
            {actionMessage && <p className="success">{actionMessage}</p>}
            <nav className="tabs">
              {[
                ["trace", "Agent Trace", GitBranch],
                ["plan", "Search Plan", ListChecks],
                ["matrix", "Comparison Matrix", Table2],
                ["claims", "Evidence & Claims", Database],
                ["report", "Final Report", FileText],
              ].map(([id, label, Icon]) => (
                <button key={id} className={activeTab === id ? "active" : ""} onClick={() => setActiveTab(id)}>
                  <Icon size={16} />
                  {label}
                </button>
              ))}
            </nav>
            {activeTab === "trace" && <TraceView result={result} onTicketAction={handleTicketAction} />}
            {activeTab === "plan" && <SearchPlanView result={result} />}
            {activeTab === "matrix" && <MatrixView result={result} />}
            {activeTab === "claims" && <ClaimsView result={result} onExcludeEvidence={handleEvidenceExclude} onRestoreEvidence={handleEvidenceRestore} />}
            {activeTab === "report" && <ReportView result={result} />}
          </>
        ) : (
          <EmptyState loading={loading} liveTrace={liveTrace} streamState={streamState} />
        )}
      </section>
    </main>
  );
}

function RecentRunsPanel({ tasks, activeTaskId, loading, onRefresh, onSelect }) {
  return (
    <section className="panel recent-runs">
      <div className="panel-heading split">
        <span>Recent Runs</span>
        <button type="button" className="icon-button" onClick={onRefresh} disabled={loading} aria-label="Refresh recent runs">
          <RefreshCw size={14} />
        </button>
      </div>
      {tasks.length === 0 ? (
        <p>No saved runs yet.</p>
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

function configToForm(config) {
  return {
    ...config,
    competitors: [...config.competitors],
    competitorDraft: "",
    goalsText: config.analysis_goals.join(", "),
  };
}

function formToConfig(form) {
  return {
    domain: form.domain,
    target_product: form.target_product.trim(),
    competitors: form.competitors,
    analysis_goals: splitList(form.goalsText),
    depth: form.depth || "standard",
    evidence_strictness: form.evidence_strictness,
    audience: form.audience,
    notes: form.notes || "",
  };
}

function splitList(value) {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function normalizeName(value) {
  return value.trim().toLowerCase().replace(/\s+/g, " ");
}

function applyEvidenceUpdate(current, update, note) {
  if (!current) return current;
  const staleClaims = new Set(update.stale_claims || []);
  return {
    ...current,
    evidence: current.evidence.map((item) => (
      item.evidence_id === update.evidence_id
        ? { ...item, status: update.status, excluded_reason: update.status === "excluded" ? "Excluded during evidence review." : "" }
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

function TaskForm({ form, setForm }) {
  const update = (patch) => setForm((current) => ({ ...current, ...patch }));
  const normalizedCompetitors = form.competitors.map(normalizeName);
  const duplicateTarget = normalizedCompetitors.includes(normalizeName(form.target_product));
  const duplicateCompetitors = new Set(normalizedCompetitors).size !== normalizedCompetitors.length;
  const tooManyCompetitors = form.competitors.length > 5;
  const availableSuggestions = (competitorSuggestions[form.domain] || [])
    .filter((item) => normalizeName(item) !== normalizeName(form.target_product))
    .filter((item) => !normalizedCompetitors.includes(normalizeName(item)))
    .slice(0, Math.max(0, 5 - form.competitors.length));

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

  return (
    <section className="panel task-form">
      <div className="panel-heading">
        <ClipboardList size={16} />
        <span>New Analysis</span>
      </div>
      <label>
        <span>Target product</span>
        <input value={form.target_product} onChange={(event) => update({ target_product: event.target.value })} />
      </label>
      <label>
        <span>Competitors</span>
        <div className={`chip-input ${duplicateTarget || duplicateCompetitors || tooManyCompetitors ? "invalid" : ""}`}>
          <div className="chip-list">
            {form.competitors.map((competitor) => (
              <span className="chip" key={competitor}>
                {competitor}
                <button type="button" onClick={() => removeCompetitor(competitor)} aria-label={`Remove ${competitor}`}>
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
              placeholder={form.competitors.length >= 5 ? "Maximum 5 competitors" : "Type competitor and press Enter"}
              disabled={form.competitors.length >= 5}
            />
            <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => addCompetitors(form.competitorDraft)} disabled={!form.competitorDraft.trim() || form.competitors.length >= 5}>
              Add
            </button>
          </div>
        </div>
      </label>
      {form.competitors.length === 0 && <p className="field-warning">Add at least one competitor.</p>}
      {duplicateTarget && <p className="field-warning">Target product should not also be listed as a competitor.</p>}
      {duplicateCompetitors && <p className="field-warning">Competitors must be unique after normalization.</p>}
      {tooManyCompetitors && <p className="field-warning">MVP supports at most 5 competitors.</p>}
      {availableSuggestions.length > 0 && (
        <div className="suggestion-row" aria-label="Suggested competitors">
          <span>Quick add</span>
          {availableSuggestions.map((item) => (
            <button type="button" key={item} onClick={() => addCompetitors(item)}>
              {item}
            </button>
          ))}
        </div>
      )}
      <label>
        <span>Domain</span>
        <select value={form.domain} onChange={(event) => update({ domain: event.target.value })}>
          {domainOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label>
        <span>Analysis goals</span>
        <input value={form.goalsText} onChange={(event) => update({ goalsText: event.target.value })} />
      </label>
      <div className="form-row">
        <label>
          <span>Strictness</span>
          <select value={form.evidence_strictness} onChange={(event) => update({ evidence_strictness: event.target.value })}>
            {strictnessOptions.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label>
          <span>Audience</span>
          <input value={form.audience} onChange={(event) => update({ audience: event.target.value })} />
        </label>
      </div>
      <label>
        <span>Additional instructions</span>
        <textarea value={form.notes || ""} onChange={(event) => update({ notes: event.target.value })} rows={3} />
      </label>
    </section>
  );
}

function EmptyState({ loading, liveTrace = [], streamState = null }) {
  if (loading && liveTrace.length) {
    return <LiveTracePanel liveTrace={liveTrace} streamState={streamState} />;
  }
  return (
    <section className="empty-state">
      <GitBranch size={38} />
      <h2>{loading ? "Executing LangGraph workflow" : "Configure an analysis task to inspect the collaboration loop"}</h2>
      <p>The run exposes template selection, search planning, Review Ticket routing, supplemental research, evidence review, and final reporting.</p>
    </section>
  );
}

function LiveTracePanel({ liveTrace, streamState }) {
  return (
    <section className="live-trace">
      <div className="live-head">
        <div>
          <p className="eyebrow">Live Agent Trace</p>
          <h2>Streaming workflow events as the graph runs.</h2>
        </div>
        <span className="status-pill">
          <Activity size={16} />
          Running
        </span>
      </div>
      {streamState && (
        <div className="live-metrics">
          <span>{streamState.trace_count} trace</span>
          <span>{streamState.source_count} sources</span>
          <span>{streamState.evidence_count} evidence</span>
          <span>{streamState.claim_count} claims</span>
          <span>{streamState.ticket_count} tickets</span>
        </div>
      )}
      <div className="timeline">
        {liveTrace.map((event, index) => (
          <article className="trace-item" key={event.event_id}>
            <div className="trace-index">{String(index + 1).padStart(2, "0")}</div>
            <div>
              <div className="trace-head">
                <strong>{event.agent}</strong>
                <span>{event.event_type}</span>
              </div>
              <p>{event.summary}</p>
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
      <strong>Live stream captured {liveTrace.length} trace event(s).</strong>
      {streamState && (
        <span>
          Final stream state: {streamState.source_count} sources, {streamState.evidence_count} evidence, {streamState.claim_count} claims, {streamState.ticket_count} tickets.
        </span>
      )}
    </section>
  );
}

function TrustSummary({ summary }) {
  if (!summary) return null;
  const items = [
    ["Evidence binding", percent(summary.claim_evidence_binding_rate)],
    ["Official sources", percent(summary.official_source_ratio)],
    ["Passed claims", `${summary.passed_claim_count}/${summary.total_claim_count}`],
    ["Unresolved tickets", summary.unresolved_ticket_count],
  ];
  return (
    <section className="trust-strip">
      <div className="trust-title">
        <ShieldCheck size={18} />
        <div>
          <strong>Trust Summary</strong>
          <span>{summary.provider_mode_label || (summary.fixture_mode ? "Demo fixture run" : "Live provider run")}</span>
          <small>Search: {summary.search_mode || "-"} / LLM: {summary.llm_mode || "-"}</small>
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

function TaskConfig({ result }) {
  const config = result.task.config;
  return (
    <section className="task-config">
      <div className="task-strip">
        <div>
          <span>Target</span>
          <strong>{config.target_product}</strong>
        </div>
        <div>
          <span>Competitors</span>
          <strong>{config.competitors.join(", ")}</strong>
        </div>
        <div>
          <span>Template</span>
          <strong>{result.template?.name}</strong>
        </div>
        <div>
          <span>Search Plan</span>
          <strong>{result.search_plan?.queries.length || 0} queries</strong>
        </div>
      </div>
      <div className="template-details">
        <TemplateBlock title="Sections" items={result.template?.sections || []} />
        <TemplateBlock title="Claim Types" items={result.template?.claim_types || []} />
        <TemplateBlock title="Evidence Rules" items={result.template?.evidence_rules || []} />
        <TemplateBlock title="Review Gates" items={result.template?.review_gates || []} />
      </div>
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
                <strong>{event.agent}</strong>
                <span>{event.event_type}</span>
              </div>
              <p>{event.summary}</p>
              {(event.provider || event.provider_request_id || event.token_count != null || event.latency_ms != null || event.prompt_name) && (
                <dl className="trace-meta">
                  <div><dt>Provider</dt><dd>{event.provider || "-"}</dd></div>
                  <div><dt>Request ID</dt><dd>{event.provider_request_id || "-"}</dd></div>
                  <div><dt>Tokens</dt><dd>{event.token_count ?? "-"}</dd></div>
                  <div><dt>Latency</dt><dd>{event.latency_ms ?? "-"} ms</dd></div>
                </dl>
              )}
              {(event.input_summary || event.output_summary || event.prompt_name) && (
                <div className="trace-audit">
                  {event.prompt_name && (
                    <div>
                      <strong>Prompt</strong>
                      <span>{event.prompt_name}: {event.prompt}</span>
                    </div>
                  )}
                  {event.input_summary && (
                    <div>
                      <strong>Input</strong>
                      <span>{event.input_summary}</span>
                    </div>
                  )}
                  {event.output_summary && (
                    <div>
                      <strong>Output</strong>
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
        <h2>Review Tickets</h2>
        {result.review_tickets.map((ticket) => (
          <article className="ticket" key={ticket.ticket_id}>
            <div className="ticket-top">
              <span className={`badge ${ticket.status}`}>{ticket.status}</span>
              <span>{ticket.target_node}</span>
            </div>
            <strong>{ticket.reason}</strong>
            <p>{ticket.required_action}</p>
            <dl className="ticket-fields">
              <div><dt>Product</dt><dd>{ticket.product || "-"}</dd></div>
              <div><dt>Missing</dt><dd>{ticket.missing_evidence_type || "-"}</dd></div>
              <div><dt>Source</dt><dd>{ticket.preferred_source_type || "-"}</dd></div>
              <div><dt>Reruns</dt><dd>{ticket.rerun_count || 0}/{ticket.max_reruns || 0}</dd></div>
            </dl>
            {(ticket.resolution_note || ticket.resolution_summary) && <p className="note">{ticket.resolution_summary || ticket.resolution_note}</p>}
            <div className="action-row">
              <button type="button" onClick={() => onTicketAction(ticket.ticket_id, "accept")} disabled={!["open", "accepted"].includes(ticket.status)}>
                <CheckCircle2 size={14} />
                Accept
              </button>
              <button type="button" onClick={() => onTicketAction(ticket.ticket_id, "rerun")} disabled={ticket.status === "blocked"}>
                <RefreshCw size={14} />
                Rerun
              </button>
              <button type="button" onClick={() => onTicketAction(ticket.ticket_id, "unavailable")} disabled={["dismissed", "blocked"].includes(ticket.status)}>
                <TriangleAlert size={14} />
                Unavailable
              </button>
              <button type="button" onClick={() => onTicketAction(ticket.ticket_id, "downgrade")} disabled={["dismissed", "blocked"].includes(ticket.status)}>
                <ArrowRight size={14} />
                Downgrade
              </button>
              <button type="button" onClick={() => onTicketAction(ticket.ticket_id, "resolve")} disabled={ticket.status === "resolved"}>
                <ShieldCheck size={14} />
                Resolve
              </button>
              <button type="button" onClick={() => onTicketAction(ticket.ticket_id, "dismiss")} disabled={ticket.status === "dismissed"}>
                <XCircle size={14} />
                Dismiss
              </button>
            </div>
          </article>
        ))}
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
                <span className={`badge ${query.priority}`}>{query.priority}</span>
              </div>
              <dl className="plan-fields">
                <div><dt>Product</dt><dd>{query.product}</dd></div>
                <div><dt>Expected evidence</dt><dd>{query.expected_evidence}</dd></div>
                <div><dt>Source preference</dt><dd>{query.source_preference}</dd></div>
                <div><dt>Origin</dt><dd>{query.is_supplemental ? "Review Ticket supplemental" : "Initial plan"}</dd></div>
              </dl>
              {ticket && <p className="note">Triggered by {ticket.ticket_id}: {ticket.reason}</p>}
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
  return (
    <section className="matrix-wrap">
      <table className="matrix">
        <thead>
          <tr>
            <th>Dimension</th>
            {products.map((product) => <th key={product}>{product}</th>)}
          </tr>
        </thead>
        <tbody>
          {dimensions.map((dimension) => (
            <tr key={dimension}>
              <th>{dimension}</th>
              {products.map((product) => {
                const claim = result.claims.find((item) => item.product === product && item.claim_type === dimension);
                return (
                  <td key={`${product}-${dimension}`}>
                    {claim ? (
                      <>
                        <strong>{statusCopy[claim.verified_status]}</strong>
                        <span>{claim.supporting_evidence.length} evidence</span>
                      </>
                    ) : (
                      <span>Not assessed</span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
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
          <span>Search</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Product, claim, note..." />
        </label>
        <label>
          <span>Status</span>
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="all">All statuses</option>
            {statuses.map((status) => <option key={status} value={status}>{statusCopy[status] || status}</option>)}
          </select>
        </label>
        <label>
          <span>Product</span>
          <select value={productFilter} onChange={(event) => setProductFilter(event.target.value)}>
            <option value="all">All products</option>
            {products.map((product) => <option key={product} value={product}>{product}</option>)}
          </select>
        </label>
        <label>
          <span>Type</span>
          <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
            <option value="all">All claim types</option>
            {claimTypes.map((claimType) => <option key={claimType} value={claimType}>{claimType}</option>)}
          </select>
        </label>
        <label>
          <span>Sort</span>
          <select value={sortBy} onChange={(event) => setSortBy(event.target.value)}>
            <option value="product">Product</option>
            <option value="status">Status</option>
            <option value="evidence">Evidence count</option>
            <option value="type">Claim type</option>
          </select>
        </label>
      </div>
      <div className="claim-count">{filteredClaims.length} of {result.claims.length} claims</div>
      {filteredClaims.length === 0 && (
        <div className="empty-filter">
          No claims match the current filters.
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
                {expanded ? "Collapse" : "Expand"}
              </button>
            </div>
            <div>
              <div className="claim-title">
                <strong>{claim.product}</strong>
                <span>{claim.claim_type}</span>
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
                            <strong>{evidence ? `${evidence.evidence_type}: ${evidence.summary}` : id}</strong>
                            {source && <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>}
                          </div>
                          {source && (
                            <dl>
                              <div><dt>Source</dt><dd>{source.source_type}</dd></div>
                              <div><dt>Locator</dt><dd>{evidence.quote_or_locator}</dd></div>
                              <div><dt>Confidence</dt><dd>{evidence.confidence}</dd></div>
                              <div><dt>Risk</dt><dd>{evidence.risk}</dd></div>
                              <div><dt>Status</dt><dd>{statusCopy[evidence.status || "active"]}</dd></div>
                            </dl>
                          )}
                          {evidence?.excluded_reason && <p className="note">{evidence.excluded_reason}</p>}
                          {evidence && (
                            <div className="action-row compact">
                              {evidence.status === "excluded" ? (
                                <button type="button" onClick={() => onRestoreEvidence(evidence.evidence_id)}>
                                  <RefreshCw size={14} />
                                  Restore
                                </button>
                              ) : (
                                <button type="button" onClick={() => onExcludeEvidence(evidence.evidence_id)}>
                                  <Ban size={14} />
                                  Exclude
                                </button>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })
                  ) : (
                    <span className="missing">No supporting evidence bound</span>
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

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
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
          <h3>FeatureTree</h3>
          <FeatureNode node={report.feature_tree.root} />
          <p className="note">{report.feature_tree.coverage_note}</p>
        </section>
      )}
      {report.pricing_model && (
        <section>
          <h3>PricingModel</h3>
          <div className="pricing-grid">
            {report.pricing_model.plans.map((plan) => (
              <article key={plan.product}>
                <div className="ticket-top">
                  <strong>{plan.product}</strong>
                  <span className={`badge ${plan.confidence}`}>{plan.confidence}</span>
                </div>
                <p>{plan.model}</p>
                <span>{plan.tiers.length ? plan.tiers.join(" / ") : "No verified tiers"}</span>
                <small>{plan.monetization_signal}</small>
              </article>
            ))}
          </div>
          <p className="note">{report.pricing_model.comparison_summary}</p>
        </section>
      )}
      {report.user_personas?.length > 0 && (
        <section>
          <h3>UserPersona</h3>
          <div className="persona-grid">
            {report.user_personas.map((persona) => (
              <article key={persona.persona_id}>
                <strong>{persona.name}</strong>
                <span>{persona.segment}</span>
                <p>{persona.jobs_to_be_done.join("；")}</p>
                <small>Decision: {persona.decision_criteria.join(" / ")}</small>
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
              ["Strengths", report.swot.strengths],
              ["Weaknesses", report.swot.weaknesses],
              ["Opportunities", report.swot.opportunities],
              ["Threats", report.swot.threats],
            ].map(([label, items]) => (
              <article key={label}>
                <strong>{label}</strong>
                {items.map((item) => <span key={item}>{item}</span>)}
              </article>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function FeatureNode({ node }) {
  return (
    <div className="feature-node">
      <strong>{node.name}</strong>
      <span>{node.description}</span>
      {node.evidence_ids?.length > 0 && <small>Evidence: {node.evidence_ids.join(", ")}</small>}
      {node.children?.length > 0 && (
        <div>
          {node.children.map((child) => <FeatureNode key={`${node.name}-${child.name}`} node={child} />)}
        </div>
      )}
    </div>
  );
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
          <h2>{report?.title}</h2>
          <div className="report-meta">
            <span className={`badge ${report?.status || "draft"}`}>{statusCopy[report?.status] || report?.status}</span>
            <span>{report?.claim_count || 0} claims</span>
            <span>{percent(report?.evidence_coverage_rate)} evidence coverage</span>
          </div>
        </div>
        <div className="action-row">
          <button type="button" onClick={() => handleExport(false)}>
            <Download size={14} />
            Export
          </button>
          <button type="button" onClick={() => handleExport(true)}>
            <ArrowRight size={14} />
            Export draft
          </button>
        </div>
      </div>
      {report?.sections?.length > 0 && (
        <div className="section-strip">
          {report.sections.map((section) => (
            <div key={section.section_id}>
              <span className={`badge ${section.status}`}>{statusCopy[section.status] || section.status}</span>
              <strong>{section.title}</strong>
            </div>
          ))}
        </div>
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
      <pre>{report?.markdown}</pre>
    </section>
  );
}

function percent(value) {
  return `${Math.round((value || 0) * 100)}%`;
}

createRoot(document.getElementById("root")).render(<App />);
