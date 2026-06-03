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
ClaimStatus = Literal["passed", "uncertain", "blocked", "pending", "unsupported", "contradicted", "stale", "downgraded"]
TicketStatus = Literal["open", "accepted", "rerun_started", "resolved", "dismissed", "blocked"]
EvidenceStatus = Literal["active", "excluded", "stale"]
ReportStatus = Literal["draft", "reviewing", "blocked", "stale", "passed"]


class TaskConfig(BaseModel):
    domain: Domain = "ai_tools"
    target_product: str
    competitors: list[str] = Field(default_factory=list)
    analysis_goals: list[str] = Field(default_factory=list)
    depth: Literal["quick", "standard", "deep"] = "standard"
    evidence_strictness: Strictness = "high"
    audience: str = "product team"
    notes: str = ""


def _normalized_name(value: str) -> str:
    return " ".join(value.casefold().split())


def validate_task_config_fields(config: Any) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    target = str(getattr(config, "target_product", "") or "").strip()
    competitors = [str(item).strip() for item in getattr(config, "competitors", []) if str(item).strip()]
    goals = [str(item).strip() for item in getattr(config, "analysis_goals", []) if str(item).strip()]

    if not target:
        errors.append(
            {
                "field": "target_product",
                "message": "Target product is required.",
                "code": "TARGET_REQUIRED",
            }
        )

    if not competitors:
        errors.append(
            {
                "field": "competitors",
                "message": "At least one competitor is required.",
                "code": "COMPETITORS_REQUIRED",
            }
        )
    elif len(competitors) > 5:
        errors.append(
            {
                "field": "competitors",
                "message": "MVP supports at most 5 competitors.",
                "code": "TOO_MANY_COMPETITORS",
            }
        )

    target_key = _normalized_name(target)
    competitor_keys = [_normalized_name(item) for item in competitors]
    if target_key and target_key in competitor_keys:
        errors.append(
            {
                "field": "competitors",
                "message": "Target product cannot appear in competitors.",
                "code": "TARGET_IN_COMPETITORS",
            }
        )

    if len(set(competitor_keys)) != len(competitor_keys):
        errors.append(
            {
                "field": "competitors",
                "message": "Competitors must be unique after normalization.",
                "code": "DUPLICATE_COMPETITORS",
            }
        )

    if not goals:
        errors.append(
            {
                "field": "analysis_goals",
                "message": "At least one analysis goal is required.",
                "code": "GOALS_REQUIRED",
            }
        )
    elif len(goals) > 8:
        errors.append(
            {
                "field": "analysis_goals",
                "message": "MVP supports at most 8 analysis goals.",
                "code": "TOO_MANY_GOALS",
            }
        )

    return errors


class Task(BaseModel):
    task_id: str = Field(default_factory=lambda: new_id("task"))
    config: TaskConfig
    status: Literal["created", "running", "completed", "failed", "blocked", "cancelled"] = "created"
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
    input_summary: str = ""
    output_summary: str = ""
    token_count: int | None = None
    latency_ms: int | None = None
    provider_request_id: str = ""
    provider_mode: str = ""


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
    status: EvidenceStatus = "active"
    excluded_reason: str = ""


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
    source_node: str = ""
    target_node: str
    reason: str
    required_action: str
    severity: Literal["critical", "high", "medium", "low"] = "medium"
    affected_artifacts: list[str] = Field(default_factory=list)
    rerun_count: int = 0
    max_reruns: int = 2
    product: str = ""
    missing_evidence_type: str = ""
    preferred_source_type: str = "official"
    source_query_hint: str = ""
    resolution_note: str = ""
    resolution_summary: str = ""
    created_at: str = Field(default_factory=now_iso)
    resolved_at: str = ""


class AgentTraceEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: new_id("evt"))
    task_id: str
    agent: str
    node: str
    event_type: str
    summary: str
    input_summary: str = ""
    output_summary: str = ""
    prompt_name: str = ""
    prompt: str = ""
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    token_count: int | None = None
    latency_ms: int | None = None
    provider: str = ""
    provider_request_id: str = ""
    related_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)


class FeatureTreeNode(BaseModel):
    name: str
    description: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    children: list["FeatureTreeNode"] = Field(default_factory=list)


class FeatureTree(BaseModel):
    root: FeatureTreeNode
    coverage_note: str = ""


class PricingPlan(BaseModel):
    product: str
    model: str
    tiers: list[str] = Field(default_factory=list)
    monetization_signal: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    risk: str = ""


class PricingModel(BaseModel):
    plans: list[PricingPlan] = Field(default_factory=list)
    comparison_summary: str = ""


class UserPersona(BaseModel):
    persona_id: str = Field(default_factory=lambda: new_id("persona"))
    name: str
    segment: str
    jobs_to_be_done: list[str] = Field(default_factory=list)
    pains: list[str] = Field(default_factory=list)
    decision_criteria: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class SwotAnalysis(BaseModel):
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    threats: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ReportSection(BaseModel):
    section_id: str = Field(default_factory=lambda: new_id("rs"))
    section_key: str
    title: str
    markdown: str
    status: ReportStatus = "passed"
    claim_ids: list[str] = Field(default_factory=list)
    sort_order: int = 0
    created_at: str = Field(default_factory=now_iso)


class Report(BaseModel):
    report_id: str = Field(default_factory=lambda: new_id("rep"))
    task_id: str
    title: str
    markdown: str
    status: ReportStatus = "passed"
    sections: list[ReportSection] = Field(default_factory=list)
    claim_count: int = 0
    unsupported_claim_count: int = 0
    stale_claim_count: int = 0
    evidence_coverage_rate: float = 0
    feature_tree: FeatureTree | None = None
    pricing_model: PricingModel | None = None
    user_personas: list[UserPersona] = Field(default_factory=list)
    swot: SwotAnalysis | None = None
    created_at: str = Field(default_factory=now_iso)


class TrustSummary(BaseModel):
    claim_evidence_binding_rate: float = 0
    official_source_ratio: float = 0
    blocked_claim_count: int = 0
    uncertain_claim_count: int = 0
    downgraded_claim_count: int = 0
    unresolved_ticket_count: int = 0
    passed_claim_count: int = 0
    total_claim_count: int = 0
    total_source_count: int = 0
    total_evidence_count: int = 0
    fixture_mode: bool = True
    provider_mode_label: str = "Demo fixture run"
    search_mode: str = "mock"
    llm_mode: str = "mock"
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
