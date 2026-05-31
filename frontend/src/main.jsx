import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowRight,
  BarChart3,
  CheckCircle2,
  ClipboardList,
  Database,
  FileText,
  GitBranch,
  ListChecks,
  Play,
  Search,
  ShieldCheck,
  Table2,
  TriangleAlert,
} from "lucide-react";
import { createTask, getDemoTasks, runTask } from "./api/client";
import "./styles/app.css";

const statusCopy = {
  passed: "Passed",
  uncertain: "Uncertain",
  blocked: "Blocked",
};

const domainOptions = [
  ["ai_tools", "AI tools"],
  ["general_product", "General product"],
  ["saas", "SaaS"],
];

const strictnessOptions = ["high", "standard", "low"];

function App() {
  const [demoTasks, setDemoTasks] = useState([]);
  const [selectedDemo, setSelectedDemo] = useState(null);
  const [taskForm, setTaskForm] = useState(null);
  const [result, setResult] = useState(null);
  const [activeTab, setActiveTab] = useState("trace");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

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
    setResult(null);
    try {
      const task = await createTask(formToConfig(taskForm));
      const workflowResult = await runTask(task.task_id);
      setResult(workflowResult);
      setActiveTab("trace");
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

        <section className="panel quiet">
          <div className="panel-heading">
            <ShieldCheck size={16} />
            <span>Provider Mode</span>
          </div>
          <p><strong>Mock search + mock LLM.</strong> AnySearch / Seed keys are not used by this build. Fixture mode is visible in Trust Summary and tool calls.</p>
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
            {activeTab === "trace" && <TraceView result={result} />}
            {activeTab === "plan" && <SearchPlanView result={result} />}
            {activeTab === "matrix" && <MatrixView result={result} />}
            {activeTab === "claims" && <ClaimsView result={result} />}
            {activeTab === "report" && <ReportView report={result.report} />}
          </>
        ) : (
          <EmptyState loading={loading} />
        )}
      </section>
    </main>
  );
}

function configToForm(config) {
  return {
    ...config,
    competitorsText: config.competitors.join(", "),
    goalsText: config.analysis_goals.join(", "),
  };
}

function formToConfig(form) {
  return {
    domain: form.domain,
    target_product: form.target_product.trim(),
    competitors: splitList(form.competitorsText),
    analysis_goals: splitList(form.goalsText),
    depth: form.depth || "standard",
    evidence_strictness: form.evidence_strictness,
    audience: form.audience,
    notes: form.notes || "",
  };
}

function splitList(value) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function TaskForm({ form, setForm }) {
  const update = (patch) => setForm((current) => ({ ...current, ...patch }));
  const duplicateTarget = splitList(form.competitorsText).includes(form.target_product.trim());
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
        <input value={form.competitorsText} onChange={(event) => update({ competitorsText: event.target.value })} />
      </label>
      {duplicateTarget && <p className="field-warning">Target product should not also be listed as a competitor.</p>}
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

function EmptyState({ loading }) {
  return (
    <section className="empty-state">
      <GitBranch size={38} />
      <h2>{loading ? "Executing LangGraph workflow" : "Configure an analysis task to inspect the collaboration loop"}</h2>
      <p>The run exposes template selection, search planning, Review Ticket routing, supplemental research, evidence review, and final reporting.</p>
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
          <span>{summary.fixture_mode ? "Fixture-backed no-key run" : "Real-provider run"}</span>
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

function TraceView({ result }) {
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
            </dl>
            {ticket.resolution_note && <p className="note">{ticket.resolution_note}</p>}
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
    ? ["positioning", "agent_capability", "pricing"]
    : ["positioning", "pricing", "opportunity"];
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

function ClaimsView({ result }) {
  const evidenceById = new Map(result.evidence.map((item) => [item.evidence_id, item]));
  const sourceById = new Map(result.sources.map((source) => [source.source_id, source]));
  return (
    <section className="claim-list">
      {result.claims.map((claim) => (
        <article className="claim" key={claim.claim_id}>
          <div className="claim-status">
            {claim.verified_status === "passed" ? <CheckCircle2 size={18} /> : <TriangleAlert size={18} />}
            <span className={`badge ${claim.verified_status}`}>{statusCopy[claim.verified_status]}</span>
          </div>
          <div>
            <div className="claim-title">
              <strong>{claim.product}</strong>
              <span>{claim.claim_type}</span>
            </div>
            <p>{claim.claim}</p>
            {claim.note && <p className="note">{claim.note}</p>}
            <div className="evidence-detail-list">
              {claim.supporting_evidence.length ? (
                claim.supporting_evidence.map((id) => {
                  const evidence = evidenceById.get(id);
                  const source = evidence ? sourceById.get(evidence.source_id) : null;
                  return (
                    <div className="evidence-detail" key={id}>
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
                        </dl>
                      )}
                    </div>
                  );
                })
              ) : (
                <span className="missing">No supporting evidence bound</span>
              )}
            </div>
          </div>
        </article>
      ))}
    </section>
  );
}

function ReportView({ report }) {
  return (
    <section className="report">
      <div className="report-head">
        <h2>{report?.title}</h2>
        <ArrowRight size={18} />
      </div>
      <pre>{report?.markdown}</pre>
    </section>
  );
}

function percent(value) {
  return `${Math.round((value || 0) * 100)}%`;
}

createRoot(document.getElementById("root")).render(<App />);
