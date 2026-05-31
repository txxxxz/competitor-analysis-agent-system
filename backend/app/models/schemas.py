from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


Domain = Literal["general_product", "saas", "ai_tools"]
Strictness = Literal["low", "standard", "high"]
ClaimStatus = Literal["passed", "uncertain", "blocked"]
TicketStatus = Literal["open", "resolved", "dismissed"]


class TaskConfig(BaseModel):
    domain: Domain = "ai_tools"
    target_product: str
    competitors: list[str] = Field(default_factory=list)
    analysis_goals: list[str] = Field(default_factory=list)
    depth: Literal["quick", "standard", "deep"] = "standard"
    evidence_strictness: Strictness = "high"
    audience: str = "product team"
    notes: str = ""


class Task(BaseModel):
    task_id: str = Field(default_factory=lambda: new_id("task"))
    config: TaskConfig
    status: Literal["created", "running", "completed", "failed"] = "created"
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class TaskBrief(BaseModel):
    task_id: str
    domain: Domain
    target_product: str
    competitors: list[str]
    goals: list[str]
    strictness: Strictness
    summary: str


class AnalysisTemplate(BaseModel):
    template_id: str
    name: str
    sections: list[str]
    evidence_rules: list[str]
    claim_types: list[str]
    review_gates: list[str]


class SearchQuery(BaseModel):
    query: str
    product: str
    expected_evidence: str
    priority: Literal["high", "medium", "low"] = "medium"
    source_preference: str = "official"
    is_supplemental: bool = False
    related_ticket_id: str = ""


class SearchPlan(BaseModel):
    task_id: str
    queries: list[SearchQuery] = Field(default_factory=list)
    preferred_source_types: list[str] = Field(default_factory=list)


class ToolCall(BaseModel):
    tool_call_id: str = Field(default_factory=lambda: new_id("tool"))
    task_id: str
    agent: str
    tool: str
    operation: str
    query: str = ""
    status: Literal["success", "failed", "skipped"] = "success"
    retrieved_at: str = Field(default_factory=now_iso)
    results_summary: str = ""


class Source(BaseModel):
    source_id: str = Field(default_factory=lambda: new_id("src"))
    task_id: str
    title: str
    url: str
    source_type: str
    product: str
    query: str
    retrieved_at: str = Field(default_factory=now_iso)
    confidence: Literal["high", "medium", "low"] = "medium"
    risk: str = ""
    content: str = ""


class Evidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: new_id("ev"))
    task_id: str
    source_id: str
    product: str
    evidence_type: str
    summary: str
    quote_or_locator: str
    confidence: Literal["high", "medium", "low"] = "medium"
    risk: str = ""


class Claim(BaseModel):
    claim_id: str = Field(default_factory=lambda: new_id("cl"))
    task_id: str
    claim: str
    product: str
    claim_type: str
    supporting_evidence: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    verified_status: ClaimStatus = "uncertain"
    included_in_report: bool = False
    note: str = ""


class ReviewTicket(BaseModel):
    ticket_id: str = Field(default_factory=lambda: new_id("rt"))
    task_id: str
    reviewer: str
    status: TicketStatus = "open"
    target_node: str
    reason: str
    required_action: str
    severity: Literal["high", "medium", "low"] = "medium"
    product: str = ""
    missing_evidence_type: str = ""
    preferred_source_type: str = "official"
    source_query_hint: str = ""
    resolution_note: str = ""
    created_at: str = Field(default_factory=now_iso)


class AgentTraceEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: new_id("evt"))
    task_id: str
    agent: str
    node: str
    event_type: str
    summary: str
    input_summary: str = ""
    output_summary: str = ""
    related_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)


class Report(BaseModel):
    report_id: str = Field(default_factory=lambda: new_id("rep"))
    task_id: str
    title: str
    markdown: str
    created_at: str = Field(default_factory=now_iso)


class TrustSummary(BaseModel):
    claim_evidence_binding_rate: float = 0
    official_source_ratio: float = 0
    blocked_claim_count: int = 0
    uncertain_claim_count: int = 0
    unresolved_ticket_count: int = 0
    passed_claim_count: int = 0
    total_claim_count: int = 0
    total_source_count: int = 0
    total_evidence_count: int = 0
    fixture_mode: bool = True
    summary: str = ""


class WorkflowResult(BaseModel):
    task: Task
    brief: TaskBrief | None = None
    template: AnalysisTemplate | None = None
    search_plan: SearchPlan | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    review_tickets: list[ReviewTicket] = Field(default_factory=list)
    trace: list[AgentTraceEvent] = Field(default_factory=list)
    trust_summary: TrustSummary | None = None
    report: Report | None = None


class GraphState(BaseModel):
    task: Task
    brief: TaskBrief | None = None
    template: AnalysisTemplate | None = None
    search_plan: SearchPlan | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    raw_sources: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    review_tickets: list[ReviewTicket] = Field(default_factory=list)
    trace: list[AgentTraceEvent] = Field(default_factory=list)
    trust_summary: TrustSummary | None = None
    report: Report | None = None
    loop_count: int = 0
    max_loops: int = 2

    def result(self) -> WorkflowResult:
        return WorkflowResult(
            task=self.task,
            brief=self.brief,
            template=self.template,
            search_plan=self.search_plan,
            tool_calls=self.tool_calls,
            sources=self.sources,
            evidence=self.evidence,
            claims=self.claims,
            review_tickets=self.review_tickets,
            trace=self.trace,
            trust_summary=self.trust_summary,
            report=self.report,
        )
