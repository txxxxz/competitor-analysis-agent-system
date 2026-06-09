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
SocialPlatform = Literal["xiaohongshu", "weibo", "douyin"]
MAX_ANALYSIS_GOAL_WORDS = 1000


class SocialPlatformConfig(BaseModel):
    platform: SocialPlatform
    enabled: bool = False
    keywords: list[str] = Field(default_factory=list)
    sort_by: str = "综合"
    note_type: str = "不限"
    publish_time: str = "一周内"
    max_posts_per_keyword: int = 15
    fetch_comments: bool = True
    max_comments_per_post: int = 30


class SocialListeningConfig(BaseModel):
    enabled: bool = False
    platforms: list[SocialPlatformConfig] = Field(default_factory=list)
    manual_xhs_summary: str = ""
    manual_source_urls: list[str] = Field(default_factory=list)


class SocialComment(BaseModel):
    comment_id: str = ""
    author: str = ""
    content: str
    like_count: int = 0
    sentiment: Literal["positive", "neutral", "negative"] = "neutral"


class SocialPost(BaseModel):
    post_id: str
    platform: SocialPlatform
    title: str
    content: str = ""
    author: str = ""
    url: str = ""
    xsec_token: str = ""
    like_count: int = 0
    collect_count: int = 0
    share_count: int = 0
    comment_count: int = 0
    comments: list[SocialComment] = Field(default_factory=list)


class SentimentSummary(BaseModel):
    positive_count: int = 0
    neutral_count: int = 0
    negative_count: int = 0
    overall: Literal["positive", "neutral", "negative", "mixed"] = "neutral"
    evidence_ids: list[str] = Field(default_factory=list)


class SocialInsightFinding(BaseModel):
    finding_id: str = Field(default_factory=lambda: new_id("sf"))
    category: Literal["positive", "pain", "risk", "request", "question", "neutral"] = "neutral"
    title: str
    summary: str
    comment_refs: list[str] = Field(default_factory=list)


class SocialInsight(BaseModel):
    insight_id: str = Field(default_factory=lambda: new_id("si"))
    platform: SocialPlatform
    keyword: str = ""
    summary: str
    findings: list[SocialInsightFinding] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    pain_points: list[str] = Field(default_factory=list)
    purchase_signals: list[str] = Field(default_factory=list)
    churn_or_risk_signals: list[str] = Field(default_factory=list)
    competitor_mentions: list[str] = Field(default_factory=list)
    sentiment: SentimentSummary = Field(default_factory=SentimentSummary)
    post_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    status: Literal["collected", "manual", "login_required", "unavailable"] = "collected"
    note: str = ""


class XhsMcpStatusResponse(BaseModel):
    connected: bool = False
    logged_in: bool = False
    login_required: bool = True
    message: str = ""
    mcp_url: str = "http://localhost:18060/mcp"


class XhsLoginQrCodeResponse(BaseModel):
    connected: bool = False
    login_required: bool = True
    qrcode_base64: str = ""
    qr_url: str = ""
    qr_image_path: str = ""
    qr_id: str = ""
    code: str = ""
    expires_in_seconds: int = 0
    message: str = ""
    mcp_url: str = "http://localhost:18060/mcp"


class XhsQrCodeStatusRequest(BaseModel):
    qr_id: str = ""
    code: str = ""


class XhsQrCodeStatusResponse(BaseModel):
    connected: bool = False
    logged_in: bool = False
    login_required: bool = True
    status: str = ""
    message: str = ""
    mcp_url: str = "http://localhost:18060/mcp"


class TaskConfig(BaseModel):
    domain: Domain = "ai_tools"
    target_product: str
    competitors: list[str] = Field(default_factory=list)
    analysis_goals: list[str] = Field(default_factory=list)
    depth: Literal["quick", "standard", "deep"] = "standard"
    evidence_strictness: Strictness = "high"
    audience: str = "product team"
    notes: str = ""
    social_listening: SocialListeningConfig = Field(default_factory=SocialListeningConfig)


class AnalysisGoalPolishRequest(BaseModel):
    draft: str
    domain: Domain = "ai_tools"
    target_product: str = ""
    competitors: list[str] = Field(default_factory=list)
    audience: str = "product team"


class AnalysisGoalPolishItem(BaseModel):
    title: str
    details: list[str] = Field(default_factory=list)


class AnalysisGoalPolishResponse(BaseModel):
    goals: list[str] = Field(default_factory=list)
    items: list[AnalysisGoalPolishItem] = Field(default_factory=list)
    formatted_text: str = ""
    provider: str = ""
    provider_request_id: str = ""


class AnalysisGoalCondenseRequest(BaseModel):
    draft: str
    domain: Domain = "ai_tools"
    target_product: str = ""
    competitors: list[str] = Field(default_factory=list)
    audience: str = "product team"
    max_words: int = MAX_ANALYSIS_GOAL_WORDS


class AnalysisGoalCondenseResponse(BaseModel):
    condensed_text: str = ""
    word_count: int = 0
    provider: str = ""
    provider_request_id: str = ""


class CompetitorRecommendationRequest(BaseModel):
    target_product: str
    domain: Domain = "ai_tools"
    existing_competitors: list[str] = Field(default_factory=list)
    audience: str = "product team"
    max_results: int = 5


class CompetitorRecommendationResponse(BaseModel):
    competitors: list[str] = Field(default_factory=list)
    rationale: str = ""
    provider: str = ""
    provider_request_id: str = ""


SurveyQuestionType = Literal["screening", "single_choice", "multiple_choice", "likert", "ranking", "open_text"]


class SurveyGenerationRequest(BaseModel):
    product_name: str
    research_goal: str
    target_users: str
    scenario: str = ""
    question_count: int = 12
    language: Literal["zh-CN", "en-US"] = "zh-CN"


class SurveyQuestion(BaseModel):
    question_id: str
    type: SurveyQuestionType
    text: str
    options: list[str] = Field(default_factory=list)
    required: bool = True
    purpose: str = ""


class SurveyGenerationResponse(BaseModel):
    title: str
    research_objective: str
    target_users: str
    screening_criteria: list[str] = Field(default_factory=list)
    questions: list[SurveyQuestion] = Field(default_factory=list)
    analysis_plan: list[str] = Field(default_factory=list)
    survey_json: dict[str, Any] = Field(default_factory=dict)
    skill_source: dict[str, str] = Field(default_factory=dict)
    provider: str = ""
    provider_request_id: str = ""


PMSkillLicense = Literal["MIT", "CC BY-NC-SA 4.0", "unknown"]


class PMSkill(BaseModel):
    skill_id: str
    name: str
    description: str = ""
    intent: str = ""
    repo_url: str
    path: str
    ref: str = "main"
    license: PMSkillLicense = "unknown"
    content_hash: str
    markdown: str
    source: Literal["default", "user"] = "user"
    requires_license_ack: bool = False
    imported_at: str = Field(default_factory=now_iso)


class PMSkillAssignment(BaseModel):
    slot: str
    skill_id: str = ""
    enabled: bool = True
    license_acknowledged: bool = False
    updated_at: str = Field(default_factory=now_iso)


class PMSkillSlot(BaseModel):
    slot: str
    title: str
    description: str = ""


class PMSkillCatalogResponse(BaseModel):
    skills: list[PMSkill] = Field(default_factory=list)
    slots: list[PMSkillSlot] = Field(default_factory=list)
    defaults: list[dict[str, Any]] = Field(default_factory=list)
    assignments: list[PMSkillAssignment] = Field(default_factory=list)


class PMSkillImportRequest(BaseModel):
    github_url: str
    intent: str = ""
    license: PMSkillLicense | str = "unknown"


class PMSkillSyncResponse(BaseModel):
    imported: list[PMSkill] = Field(default_factory=list)
    warnings: list[dict[str, str]] = Field(default_factory=list)
    assignments: list[PMSkillAssignment] = Field(default_factory=list)


class PMSkillAssignmentUpdate(BaseModel):
    slot: str
    skill_id: str = ""
    enabled: bool = True
    license_acknowledged: bool = False


class PMSkillAssignmentsRequest(BaseModel):
    assignments: list[PMSkillAssignmentUpdate] = Field(default_factory=list)


class PMSkillRecommendRequest(BaseModel):
    top_level_goal: str = ""
    task_domain: str = ""
    data_sources: list[str] = Field(default_factory=list)


class PMSkillRecommendResponse(BaseModel):
    recommendations: list[dict[str, Any]] = Field(default_factory=list)


class SkillPromptContext(BaseModel):
    slot: str
    skill_id: str
    skill_name: str
    skill_repo: str
    skill_path: str
    skill_hash: str
    license: str = ""
    prompt: str = ""


def _normalized_name(value: str) -> str:
    return " ".join(value.casefold().split())


def count_goal_words(value: str) -> int:
    text = str(value or "")
    count = 0
    in_ascii_word = False
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            count += 1
            in_ascii_word = False
        elif char.isalnum():
            if not in_ascii_word:
                count += 1
                in_ascii_word = True
        else:
            in_ascii_word = False
    return count


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
    elif count_goal_words("\n".join(goals)) > MAX_ANALYSIS_GOAL_WORDS:
        errors.append(
            {
                "field": "analysis_goals",
                "message": f"Analysis goals must be within {MAX_ANALYSIS_GOAL_WORDS} words.",
                "code": "GOALS_TOO_LONG",
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
    skill_name: str = ""
    skill_repo: str = ""
    skill_path: str = ""
    skill_hash: str = ""
    skill_license: str = ""


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
    interaction_path: list[str] = Field(default_factory=list)
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
    skill_name: str = ""
    skill_repo: str = ""
    skill_path: str = ""
    skill_hash: str = ""
    skill_license: str = ""
    related_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)


class FeatureTreeNode(BaseModel):
    name: str
    description: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    interaction_path: list[str] = Field(default_factory=list)
    verification_method: str = "unverified"
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
    social_insights: list[SocialInsight] = Field(default_factory=list)
    skill_assignments: list[dict[str, str]] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)


class TrustSummary(BaseModel):
    claim_evidence_binding_rate: float = 0
    official_source_ratio: float = 0
    browser_interaction_count: int = 0
    browser_verified_product_count: int = 0
    browser_verified_product_total: int = 0
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
    social_posts: list[SocialPost] = Field(default_factory=list)
    social_insights: list[SocialInsight] = Field(default_factory=list)
    skill_assignments: list[dict[str, str]] = Field(default_factory=list)


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
    social_posts: list[SocialPost] = Field(default_factory=list)
    social_insights: list[SocialInsight] = Field(default_factory=list)
    skill_assignments: list[dict[str, str]] = Field(default_factory=list)
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
            social_posts=self.social_posts,
            social_insights=self.social_insights,
            skill_assignments=self.skill_assignments,
        )
