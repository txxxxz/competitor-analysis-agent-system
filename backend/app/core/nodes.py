from __future__ import annotations

from collections import defaultdict

from app.models.schemas import (
    AgentTraceEvent,
    Claim,
    Evidence,
    GraphState,
    Report,
    ReviewTicket,
    SearchPlan,
    SearchQuery,
    Source,
    TaskBrief,
    ToolCall,
    TrustSummary,
)
from app.providers.mock_search import MockSearchProvider
from app.templates.catalog import select_template


search_provider = MockSearchProvider()


def _trace(state: GraphState, agent: str, node: str, event_type: str, summary: str, related_ids: list[str] | None = None) -> None:
    state.trace.append(
        AgentTraceEvent(
            task_id=state.task.task_id,
            agent=agent,
            node=node,
            event_type=event_type,
            summary=summary,
            related_ids=related_ids or [],
        )
    )


def planner_node(state: GraphState) -> GraphState:
    cfg = state.task.config
    state.brief = TaskBrief(
        task_id=state.task.task_id,
        domain=cfg.domain,
        target_product=cfg.target_product,
        competitors=cfg.competitors,
        goals=cfg.analysis_goals,
        strictness=cfg.evidence_strictness,
        summary=f"Analyze {cfg.target_product} against {', '.join(cfg.competitors)} for {cfg.audience}.",
    )
    _trace(state, "PlannerAgent", "planner", "brief_created", "Generated structured task brief.")
    return state


def template_node(state: GraphState) -> GraphState:
    state.template = select_template(state.task.config.domain)
    _trace(
        state,
        "TemplateAgent",
        "template",
        "template_selected",
        f"Selected {state.template.name} because domain is {state.task.config.domain}.",
        [state.template.template_id],
    )
    return state


def _query_for_ticket(state: GraphState, ticket: ReviewTicket) -> SearchQuery:
    product = ticket.product or ticket.reason.split(" lacks ", 1)[0]
    evidence_type = ticket.missing_evidence_type or "pricing"
    source_hint = ticket.source_query_hint or f"{product} {evidence_type.replace('_', ' ')} official"
    return SearchQuery(
        query=source_hint,
        product=product,
        expected_evidence=evidence_type,
        priority=ticket.severity,
        source_preference=ticket.preferred_source_type or "official",
        is_supplemental=True,
        related_ticket_id=ticket.ticket_id,
    )


def research_node(state: GraphState) -> GraphState:
    cfg = state.task.config
    products = [cfg.target_product, *cfg.competitors]
    open_research_tickets = [t for t in state.review_tickets if t.status == "open" and t.target_node == "ResearchAgent"]
    supplement = bool(open_research_tickets)

    if not state.search_plan:
        queries: list[SearchQuery] = []
        for product in products:
            queries.append(
                SearchQuery(
                    query=f"{product} official positioning",
                    product=product,
                    expected_evidence="positioning",
                    priority="high",
                    source_preference="official_homepage",
                )
            )
            queries.append(
                SearchQuery(
                    query=f"{product} pricing official",
                    product=product,
                    expected_evidence="pricing",
                    priority="high",
                    source_preference="official_pricing_page",
                )
            )
            if cfg.domain == "ai_tools":
                queries.append(
                    SearchQuery(
                        query=f"{product} AI agent coding workflow docs",
                        product=product,
                        expected_evidence="agent_capability",
                        priority="medium",
                        source_preference="official_docs",
                    )
                )
        state.search_plan = SearchPlan(
            task_id=state.task.task_id,
            queries=queries,
            preferred_source_types=["official_homepage", "official_docs", "official_pricing_page"],
        )

    queries_to_run = state.search_plan.queries
    if supplement:
        queries_to_run = [_query_for_ticket(state, ticket) for ticket in open_research_tickets]
        state.search_plan.queries.extend(queries_to_run)

    before = len(state.raw_sources)
    for query in queries_to_run:
        results = search_provider.search(state.task.task_id, query, supplement=supplement)
        state.tool_calls.append(
            ToolCall(
                task_id=state.task.task_id,
                agent="ResearchAgent",
                tool="MockSearchProvider",
                operation="search",
                query=query.query,
                results_summary=f"{len(results)} result(s) for {query.product} / {query.expected_evidence}",
            )
        )
        for result in results:
            if not any(s.get("url") == result.get("url") for s in state.raw_sources):
                state.raw_sources.append({**result, "query": query.query})

    if supplement:
        found_pairs = {(raw["product"], raw["evidence_type"]) for raw in state.raw_sources}
        for ticket in open_research_tickets:
            key = (ticket.product, ticket.missing_evidence_type)
            if key in found_pairs:
                ticket.status = "resolved"
                ticket.resolution_note = "Supplemental research added matching evidence."
            else:
                ticket.status = "dismissed"
                ticket.resolution_note = "No matching fixture source was available; related claims remain uncertain."
        state.loop_count += 1
        _trace(
            state,
            "ResearchAgent",
            "research",
            "supplemental_search",
            f"Ran {len(queries_to_run)} supplemental query/queries from Review Ticket fields and added {len(state.raw_sources) - before} source(s).",
            [ticket.ticket_id for ticket in open_research_tickets],
        )
    else:
        _trace(state, "ResearchAgent", "research", "search_completed", f"Executed search plan with {len(state.search_plan.queries)} queries.")
    return state


def source_normalizer_node(state: GraphState) -> GraphState:
    existing_urls = {source.url for source in state.sources}
    for raw in state.raw_sources:
        if raw["url"] in existing_urls:
            continue
        state.sources.append(
            Source(
                task_id=state.task.task_id,
                title=raw["title"],
                url=raw["url"],
                source_type=raw["source_type"],
                product=raw["product"],
                query=raw.get("query", ""),
                confidence="high" if raw["source_type"].startswith("official") else "medium",
                risk="Demo fixture; verify with live provider before production use.",
                content=raw["content"],
            )
        )
    _trace(state, "SourceNormalizer", "source_normalizer", "sources_normalized", f"Normalized {len(state.sources)} unique source(s).")
    return state


def evidence_extractor_node(state: GraphState) -> GraphState:
    existing = {(item.source_id, item.evidence_type) for item in state.evidence}
    raw_by_url = {raw["url"]: raw for raw in state.raw_sources}
    for source in state.sources:
        raw = raw_by_url.get(source.url)
        if not raw or (source.source_id, raw["evidence_type"]) in existing:
            continue
        state.evidence.append(
            Evidence(
                task_id=state.task.task_id,
                source_id=source.source_id,
                product=source.product,
                evidence_type=raw["evidence_type"],
                summary=raw["summary"],
                quote_or_locator=raw["locator"],
                confidence=source.confidence,
                risk=source.risk,
            )
        )
    _trace(state, "EvidenceExtractor", "evidence_extractor", "evidence_extracted", f"Extracted {len(state.evidence)} evidence item(s).")
    return state


def analyst_node(state: GraphState) -> GraphState:
    state.claims = []
    by_product: dict[str, list[Evidence]] = defaultdict(list)
    for item in state.evidence:
        by_product[item.product].append(item)

    products = [state.task.config.target_product, *state.task.config.competitors]
    for product in products:
        evidence_items = by_product.get(product, [])
        for evidence_type in ["positioning", "pricing"]:
            support = [item for item in evidence_items if item.evidence_type == evidence_type]
            if support:
                primary_summary = support[0].summary.rstrip(".")
                state.claims.append(
                    Claim(
                        task_id=state.task.task_id,
                        product=product,
                        claim=f"{primary_summary}.",
                        claim_type=evidence_type,
                        supporting_evidence=[item.evidence_id for item in support],
                        confidence="high",
                        verified_status="passed",
                        included_in_report=True,
                    )
                )
            else:
                state.claims.append(
                    Claim(
                        task_id=state.task.task_id,
                        product=product,
                        claim=f"{product} {evidence_type.replace('_', ' ')} needs verification before being stated as fact.",
                        claim_type=evidence_type,
                        supporting_evidence=[],
                        confidence="low",
                        verified_status="uncertain",
                        included_in_report=False,
                        note="Generated as an evidence gap, not a final factual conclusion.",
                    )
                )

        if state.task.config.domain == "ai_tools":
            agent_support = [item for item in evidence_items if item.evidence_type == "agent_capability"]
            if agent_support:
                agent_summary = agent_support[0].summary.rstrip(".")
                state.claims.append(
                    Claim(
                        task_id=state.task.task_id,
                        product=product,
                        claim=f"{agent_summary}.",
                        claim_type="agent_capability",
                        supporting_evidence=[item.evidence_id for item in agent_support],
                        confidence="medium",
                        verified_status="passed",
                        included_in_report=True,
                    )
                )

    positioning_evidence = [item for item in state.evidence if item.evidence_type == "positioning"]
    pricing_evidence = [item for item in state.evidence if item.evidence_type == "pricing"]
    if len(positioning_evidence) >= 2:
        compared = ", ".join(sorted({item.product for item in positioning_evidence})[:4])
        state.claims.append(
            Claim(
                task_id=state.task.task_id,
                product="Cross-product",
                claim=f"Positioning differs across {compared}; this supports a matrix-style comparison rather than a single ranked verdict.",
                claim_type="comparative_positioning",
                supporting_evidence=[item.evidence_id for item in positioning_evidence],
                confidence="medium",
                verified_status="passed",
                included_in_report=True,
            )
        )
    if pricing_evidence:
        state.claims.append(
            Claim(
                task_id=state.task.task_id,
                product="Opportunity",
                claim="Pricing coverage is strongest where official pricing pages are present; unresolved pricing gaps should be treated as follow-up research rather than final conclusions.",
                claim_type="opportunity",
                supporting_evidence=[item.evidence_id for item in pricing_evidence],
                confidence="medium",
                verified_status="passed",
                included_in_report=True,
            )
        )

    _trace(state, "AnalystAgent", "analyst", "claims_generated", f"Generated {len(state.claims)} claim(s) from evidence.")
    return state


def critic_node(state: GraphState) -> GraphState:
    open_research_ticket = any(ticket.status == "open" and ticket.target_node == "ResearchAgent" for ticket in state.review_tickets)
    if not open_research_ticket and state.loop_count < state.max_loops:
        products_with_pricing = {item.product for item in state.evidence if item.evidence_type == "pricing"}
        for product in [state.task.config.target_product, *state.task.config.competitors]:
            if product not in products_with_pricing:
                ticket = ReviewTicket(
                    task_id=state.task.task_id,
                    reviewer="CriticAgent",
                    target_node="ResearchAgent",
                    reason=f"{product} lacks official pricing evidence.",
                    required_action=f"Search official pricing page for {product}, or keep pricing claims uncertain.",
                    severity="high",
                    product=product,
                    missing_evidence_type="pricing",
                    preferred_source_type="official_pricing_page",
                    source_query_hint=f"{product} pricing official supplemental",
                )
                state.review_tickets.append(ticket)
                _trace(state, "CriticAgent", "critic", "review_ticket_created", ticket.reason, [ticket.ticket_id])
                return state

    _trace(state, "CriticAgent", "critic", "quality_review_passed", "Coverage is acceptable for demo strictness.")
    return state


def writer_node(state: GraphState) -> GraphState:
    included = [claim for claim in state.claims if claim.included_in_report and claim.verified_status == "passed"]
    uncertain = [claim for claim in state.claims if claim.verified_status != "passed"]
    trust = state.trust_summary or _build_trust_summary(state)
    lines = [
        f"# {state.task.config.target_product} 竞品分析报告",
        "",
        "## 可信度摘要",
        f"- 证据绑定率：{trust.claim_evidence_binding_rate:.0%}",
        f"- 官方来源占比：{trust.official_source_ratio:.0%}",
        f"- 已通过结论：{trust.passed_claim_count} / {trust.total_claim_count}",
        f"- 不确定 / 阻断结论：{trust.uncertain_claim_count} / {trust.blocked_claim_count}",
        f"- 未解决 Review Ticket：{trust.unresolved_ticket_count}",
        f"- 运行模式：{'Mock fixtures（无密钥演示）' if trust.fixture_mode else 'Real providers'}",
        "",
        "## 分析背景",
        f"- 目标产品：{state.task.config.target_product}",
        f"- 竞品范围：{', '.join(state.task.config.competitors)}",
        f"- 报告受众：{state.task.config.audience}",
        f"- 证据严格度：{state.task.config.evidence_strictness}",
        "",
        "## 核心结论",
    ]
    comparative_claims = [claim for claim in included if claim.claim_type.startswith("comparative")]
    opportunity_claims = [claim for claim in included if claim.claim_type == "opportunity"]
    product_claims = [claim for claim in included if claim not in comparative_claims and claim not in opportunity_claims]
    core_claims = product_claims[:6] + comparative_claims[:2] + opportunity_claims[:2]
    for claim in core_claims:
        lines.append(f"- **{claim.product}**：{claim.claim} Evidence: {', '.join(claim.supporting_evidence)}")
    lines.extend(["", "## 产品定位与能力矩阵"])
    products = [state.task.config.target_product, *state.task.config.competitors]
    claim_types = ["positioning", "agent_capability", "pricing"]
    for product in products:
        cells = []
        for claim_type in claim_types:
            claim = next((item for item in state.claims if item.product == product and item.claim_type == claim_type), None)
            if claim and claim.verified_status == "passed":
                cells.append(f"{claim_type}: {claim.confidence}, evidence {len(claim.supporting_evidence)}")
            elif claim:
                cells.append(f"{claim_type}: uncertain")
            else:
                cells.append(f"{claim_type}: not assessed")
        lines.append(f"- **{product}**：{' | '.join(cells)}")
    lines.extend(["", "## 机会点建议"])
    if opportunity_claims:
        for claim in opportunity_claims:
            lines.append(f"- {claim.claim}")
    else:
        lines.append("- 保持未覆盖证据为后续调研清单，不把缺失信息写成确定结论。")
    lines.extend(["", "## 不确定性与被阻断结论"])
    for claim in uncertain[:8]:
        lines.append(f"- **{claim.product} / {claim.claim_type}**：{claim.note or claim.claim}")
    lines.extend(["", "## 数据来源"])
    for source in state.sources:
        lines.append(f"- [{source.title}]({source.url}) - {source.source_type}, query: `{source.query}`")
    lines.extend(["", "## Agent 协作记录", f"- Review Tickets: {len(state.review_tickets)}", f"- Trace Events: {len(state.trace)}"])
    state.report = Report(task_id=state.task.task_id, title=f"{state.task.config.target_product} Competitor Analysis", markdown="\n".join(lines))
    _trace(state, "WriterAgent", "writer", "report_drafted", "Generated Markdown report draft.")
    return state


def evidence_reviewer_node(state: GraphState) -> GraphState:
    blocked = 0
    for claim in state.claims:
        if claim.included_in_report and not claim.supporting_evidence:
            claim.included_in_report = False
            claim.verified_status = "blocked"
            claim.note = "Blocked by Evidence Consistency Reviewer because no supporting evidence is bound."
            blocked += 1
    _trace(state, "EvidenceConsistencyReviewer", "evidence_reviewer", "evidence_gate_completed", f"Blocked or downgraded {blocked} unsupported claim(s).")
    return state


def _build_trust_summary(state: GraphState) -> TrustSummary:
    total_claims = len(state.claims)
    bound_claims = len([claim for claim in state.claims if claim.supporting_evidence])
    official_sources = len([source for source in state.sources if source.source_type.startswith("official")])
    unresolved_tickets = len([ticket for ticket in state.review_tickets if ticket.status == "open"])
    blocked_claims = len([claim for claim in state.claims if claim.verified_status == "blocked"])
    uncertain_claims = len([claim for claim in state.claims if claim.verified_status == "uncertain"])
    passed_claims = len([claim for claim in state.claims if claim.verified_status == "passed"])
    return TrustSummary(
        claim_evidence_binding_rate=bound_claims / total_claims if total_claims else 0,
        official_source_ratio=official_sources / len(state.sources) if state.sources else 0,
        blocked_claim_count=blocked_claims,
        uncertain_claim_count=uncertain_claims,
        unresolved_ticket_count=unresolved_tickets,
        passed_claim_count=passed_claims,
        total_claim_count=total_claims,
        total_source_count=len(state.sources),
        total_evidence_count=len(state.evidence),
        fixture_mode=True,
        summary=(
            f"{passed_claims}/{total_claims} claims passed evidence review; "
            f"{unresolved_tickets} review ticket(s) remain open."
        ),
    )


def trust_summary_node(state: GraphState) -> GraphState:
    state.trust_summary = _build_trust_summary(state)
    _trace(state, "EvidenceConsistencyReviewer", "trust_summary", "trust_summary_created", state.trust_summary.summary)
    return state


def finalize_node(state: GraphState) -> GraphState:
    state.task.status = "completed"
    _trace(state, "Workflow", "finalize", "workflow_completed", "Finalized no-key LangGraph demo result.")
    return state
