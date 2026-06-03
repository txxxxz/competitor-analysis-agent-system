from __future__ import annotations

from collections import defaultdict
import json
import time

from app.models.schemas import (
    AgentTraceEvent,
    Claim,
    Evidence,
    FeatureTree,
    FeatureTreeNode,
    GraphState,
    PricingModel,
    PricingPlan,
    Report,
    ReportSection,
    ReviewTicket,
    SearchPlan,
    SearchQuery,
    Source,
    SwotAnalysis,
    TaskBrief,
    ToolCall,
    TrustSummary,
    UserPersona,
)
from app.providers.errors import ProviderRequestError
from app.providers.factory import ProviderBundle, build_provider_bundle
from app.providers.mock_llm import MockLLMProvider
from app.providers.mock_search import MockSearchProvider
from app.templates.catalog import select_template


CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
AUDIT_PROMPTS = {
    "claim_enrichment": "Given task context and bound evidence, return only evidence-bound enriched claims.",
    "review_ticket_suggestions": "Given coverage and claim statuses, return actionable review tickets for missing or risky evidence.",
    "report_enhancement": "Given included and uncertain claims, return concise executive summary, recommendations, and caveats.",
}


def _trace(
    state: GraphState,
    agent: str,
    node: str,
    event_type: str,
    summary: str,
    related_ids: list[str] | None = None,
    *,
    input_summary: str = "",
    output_summary: str = "",
    prompt_name: str = "",
    prompt: str = "",
    input_payload: dict | None = None,
    output_payload: dict | None = None,
    token_count: int | None = None,
    latency_ms: int | None = None,
    provider: str = "",
    provider_request_id: str = "",
) -> None:
    state.trace.append(
        AgentTraceEvent(
            task_id=state.task.task_id,
            agent=agent,
            node=node,
            event_type=event_type,
            summary=summary,
            input_summary=input_summary,
            output_summary=output_summary,
            prompt_name=prompt_name,
            prompt=prompt,
            input_payload=input_payload or {},
            output_payload=output_payload or {},
            token_count=token_count,
            latency_ms=latency_ms,
            provider=provider,
            provider_request_id=provider_request_id,
            related_ids=related_ids or [],
        )
    )


def _estimate_tokens(*payloads: object) -> int:
    text = " ".join(json.dumps(payload, ensure_ascii=False, default=str) for payload in payloads)
    return max(1, len(text) // 4)


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
    priority = ticket.severity if ticket.severity in {"high", "medium", "low"} else "high"
    return SearchQuery(
        query=source_hint,
        product=product,
        expected_evidence=evidence_type,
        priority=priority,
        source_preference=ticket.preferred_source_type or "official",
        is_supplemental=True,
        related_ticket_id=ticket.ticket_id,
    )


def research_node(state: GraphState) -> GraphState:
    cfg = state.task.config
    providers = build_provider_bundle()
    for warning in providers.warnings:
        _trace(state, "ProviderFactory", "providers", "provider_fallback", warning)
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
            queries.append(
                SearchQuery(
                    query=f"{product} official product features",
                    product=product,
                    expected_evidence="feature",
                    priority="medium",
                    source_preference="official_docs",
                )
            )
            queries.append(
                SearchQuery(
                    query=f"{product} target users teams official",
                    product=product,
                    expected_evidence="target_user",
                    priority="medium",
                    source_preference="official_docs",
                )
            )
            queries.append(
                SearchQuery(
                    query=f"{product} security privacy official",
                    product=product,
                    expected_evidence="security",
                    priority="low",
                    source_preference="official_docs",
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
            preferred_source_types=["official_homepage", "official_docs", "official_pricing_page", "official_security_page"],
        )

    queries_to_run = state.search_plan.queries
    if supplement:
        queries_to_run = [_query_for_ticket(state, ticket) for ticket in open_research_tickets]
        state.search_plan.queries.extend(queries_to_run)

    before = len(state.raw_sources)
    for query in queries_to_run:
        started = time.perf_counter()
        results, provider_name, provider_note = _search_with_provider(state, providers, query, supplement)
        latency_ms = int((time.perf_counter() - started) * 1000)
        input_summary = f"Search query for {query.product}: {query.query}"
        output_summary = f"{len(results)} result(s) for {query.expected_evidence}"
        token_count = _estimate_tokens(query.model_dump(mode="json"), results)
        state.tool_calls.append(
            ToolCall(
                task_id=state.task.task_id,
                agent="ResearchAgent",
                tool=provider_name,
                operation="search",
                query=query.query,
                results_summary=(
                    f"{len(results)} result(s) for {query.product} / {query.expected_evidence}; "
                    f"mode={providers.search_mode}{provider_note}"
                ),
                input_summary=input_summary,
                output_summary=output_summary,
                token_count=token_count,
                latency_ms=latency_ms,
                provider_request_id="fixture" if provider_name.startswith("Mock") else "",
                provider_mode=providers.search_mode,
            )
        )
        _trace(
            state,
            "ResearchAgent",
            "research",
            "provider_search_completed",
            f"{provider_name} returned {len(results)} result(s) for {query.product} / {query.expected_evidence}.",
            input_summary=input_summary,
            output_summary=output_summary,
            input_payload=query.model_dump(mode="json"),
            output_payload={"result_count": len(results), "mode": providers.search_mode, "fallback_note": provider_note},
            token_count=token_count,
            latency_ms=latency_ms,
            provider=provider_name,
            provider_request_id="fixture" if provider_name.startswith("Mock") else "",
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


def _search_with_provider(state: GraphState, providers: ProviderBundle, query: SearchQuery, supplement: bool) -> tuple[list[dict], str, str]:
    try:
        results = providers.search.search(state.task.task_id, query, supplement=supplement)
    except ProviderRequestError as exc:
        if providers.search_mode.startswith("mock") or not providers.allow_provider_fallback:
            raise
        fallback = MockSearchProvider()
        results = fallback.search(state.task.task_id, query, supplement=supplement)
        _trace(
            state,
            "ProviderFactory",
            "providers",
            "provider_request_fallback",
            f"{providers.search.provider_name} failed; used MockSearchProvider fallback. Reason: {exc}",
        )
        return results, fallback.provider_name, "; fallback=request_error"

    if (
        not results
        and not providers.search_mode.startswith("mock")
        and providers.allow_provider_fallback
        and providers.allow_empty_search_fallback
    ):
        fallback = MockSearchProvider()
        fallback_results = fallback.search(state.task.task_id, query, supplement=supplement)
        if fallback_results:
            _trace(
                state,
                "ProviderFactory",
                "providers",
                "provider_empty_result_fallback",
                f"{providers.search.provider_name} returned no results; used MockSearchProvider fallback for demo continuity.",
            )
            return fallback_results, fallback.provider_name, "; fallback=empty_results"

    return results, providers.search.provider_name, ""


def source_normalizer_node(state: GraphState) -> GraphState:
    existing_urls = {source.url for source in state.sources}
    fixture_mode = any(call.tool.startswith("Mock") for call in state.tool_calls)
    source_risk = (
        "Demo fixture; verify with live provider before production use."
        if fixture_mode
        else "Live provider evidence; verify freshness and source authority before external publication."
    )
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
                risk=source_risk,
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
        for evidence_type in ["positioning", "pricing", "feature", "target_user", "security"]:
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
    feature_evidence = [item for item in state.evidence if item.evidence_type == "feature"]
    security_evidence = [item for item in state.evidence if item.evidence_type == "security"]
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
    if len(feature_evidence) >= 2:
        state.claims.append(
            Claim(
                task_id=state.task.task_id,
                product="Cross-product",
                claim="Feature coverage differs enough to require a feature-tree comparison instead of a flat checklist.",
                claim_type="comparative_feature",
                supporting_evidence=[item.evidence_id for item in feature_evidence],
                confidence="medium",
                verified_status="passed",
                included_in_report=True,
            )
        )
    if security_evidence:
        state.claims.append(
            Claim(
                task_id=state.task.task_id,
                product="Risk",
                claim="Security and privacy evidence should be treated as a product adoption gate for team and enterprise personas.",
                claim_type="security_risk",
                supporting_evidence=[item.evidence_id for item in security_evidence],
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

    enriched_count = _enrich_claims_with_llm(state)
    suffix = f" Added {enriched_count} provider-enriched claim(s)." if enriched_count else ""
    _trace(state, "AnalystAgent", "analyst", "claims_generated", f"Generated {len(state.claims)} claim(s) from evidence.{suffix}")
    return state


def _enrich_claims_with_llm(state: GraphState) -> int:
    if len(state.evidence) < 2:
        return 0
    providers = build_provider_bundle()
    payload = _claim_enrichment_payload(state)
    provider_name = providers.llm.provider_name
    started = time.perf_counter()
    try:
        response = providers.llm.complete_structured("claim_enrichment", payload)
        latency_ms = int((time.perf_counter() - started) * 1000)
        summary = f"{provider_name} returned claim enrichment."
    except ProviderRequestError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        state.tool_calls.append(
            ToolCall(
                task_id=state.task.task_id,
                agent="AnalystAgent",
                tool=provider_name,
                operation="complete_structured",
                query="claim_enrichment",
                status="failed",
                results_summary=str(exc),
                input_summary=f"Prompt claim_enrichment with {len(payload.get('evidence', []))} evidence item(s).",
                output_summary="Provider request failed.",
                token_count=_estimate_tokens(payload),
                latency_ms=latency_ms,
                provider_mode=providers.llm_mode,
            )
        )
        _trace(
            state,
            "AnalystAgent",
            "analyst",
            "llm_claim_enrichment_failed",
            str(exc),
            input_summary=f"Prompt claim_enrichment with {len(payload.get('evidence', []))} evidence item(s).",
            output_summary="Provider request failed.",
            prompt_name="claim_enrichment",
            prompt=AUDIT_PROMPTS["claim_enrichment"],
            input_payload=payload,
            output_payload={"error": str(exc)},
            token_count=_estimate_tokens(payload),
            latency_ms=latency_ms,
            provider=provider_name,
        )
        if not providers.allow_provider_fallback:
            return 0
        fallback = MockLLMProvider()
        started = time.perf_counter()
        response = fallback.complete_structured("claim_enrichment", payload)
        latency_ms = int((time.perf_counter() - started) * 1000)
        provider_name = fallback.provider_name
        summary = "Seed request failed; MockLLMProvider generated fallback claim enrichment."

    claims = _validated_enriched_claims(response, state)
    token_count = _estimate_tokens(payload, response)
    if not claims:
        state.tool_calls.append(
            ToolCall(
                task_id=state.task.task_id,
                agent="AnalystAgent",
                tool=provider_name,
                operation="complete_structured",
                query="claim_enrichment",
                status="skipped",
                results_summary="Provider returned no valid evidence-bound enriched claims.",
                input_summary=f"Prompt claim_enrichment with {len(payload.get('evidence', []))} evidence item(s).",
                output_summary="No valid evidence-bound enriched claims.",
                token_count=token_count,
                latency_ms=latency_ms,
                provider_mode=providers.llm_mode,
            )
        )
        _trace(
            state,
            "AnalystAgent",
            "analyst",
            "llm_claim_enrichment_skipped",
            "Provider returned no valid evidence-bound enriched claims.",
            input_summary=f"Prompt claim_enrichment with {len(payload.get('evidence', []))} evidence item(s).",
            output_summary="No valid evidence-bound enriched claims.",
            prompt_name="claim_enrichment",
            prompt=AUDIT_PROMPTS["claim_enrichment"],
            input_payload=payload,
            output_payload=response if isinstance(response, dict) else {},
            token_count=token_count,
            latency_ms=latency_ms,
            provider=provider_name,
            provider_request_id="fixture" if provider_name.startswith("Mock") else "",
        )
        return 0

    state.claims.extend(claims)
    state.tool_calls.append(
        ToolCall(
            task_id=state.task.task_id,
            agent="AnalystAgent",
            tool=provider_name,
            operation="complete_structured",
            query="claim_enrichment",
            status="success",
            results_summary=f"{summary} Added {len(claims)} claim(s).",
            input_summary=f"Prompt claim_enrichment with {len(payload.get('evidence', []))} evidence item(s).",
            output_summary=f"Added {len(claims)} valid evidence-bound claim(s).",
            token_count=token_count,
            latency_ms=latency_ms,
            provider_request_id="fixture" if provider_name.startswith("Mock") else "",
            provider_mode=providers.llm_mode,
        )
    )
    _trace(
        state,
        "AnalystAgent",
        "analyst",
        "llm_claim_enrichment_applied",
        f"{summary} Added {len(claims)} claim(s).",
        [claim.claim_id for claim in claims],
        input_summary=f"Prompt claim_enrichment with {len(payload.get('evidence', []))} evidence item(s).",
        output_summary=f"Added {len(claims)} valid evidence-bound claim(s).",
        prompt_name="claim_enrichment",
        prompt=AUDIT_PROMPTS["claim_enrichment"],
        input_payload=payload,
        output_payload=response if isinstance(response, dict) else {},
        token_count=token_count,
        latency_ms=latency_ms,
        provider=provider_name,
        provider_request_id="fixture" if provider_name.startswith("Mock") else "",
    )
    return len(claims)


def _claim_enrichment_payload(state: GraphState) -> dict:
    return {
        "task": {
            "domain": state.task.config.domain,
            "target_product": state.task.config.target_product,
            "competitors": state.task.config.competitors,
            "analysis_goals": state.task.config.analysis_goals,
            "evidence_strictness": state.task.config.evidence_strictness,
        },
        "evidence": [
            {
                "evidence_id": item.evidence_id,
                "product": item.product,
                "evidence_type": item.evidence_type,
                "summary": item.summary,
                "confidence": item.confidence,
                "status": item.status,
            }
            for item in state.evidence
            if item.status == "active"
        ],
        "existing_claims": [
            {
                "product": item.product,
                "claim_type": item.claim_type,
                "claim": item.claim,
                "supporting_evidence": item.supporting_evidence,
            }
            for item in state.claims
        ],
    }


def _validated_enriched_claims(response: dict, state: GraphState) -> list[Claim]:
    raw_claims = response.get("claims") if isinstance(response, dict) else None
    if not isinstance(raw_claims, list):
        return []
    valid_evidence_ids = {item.evidence_id for item in state.evidence if item.status == "active"}
    existing = {
        (
            claim.product.casefold(),
            claim.claim_type.casefold(),
            claim.claim.casefold(),
        )
        for claim in state.claims
    }
    claims: list[Claim] = []
    for item in raw_claims[:5]:
        if not isinstance(item, dict):
            continue
        product = str(item.get("product", "")).strip() or "Cross-product"
        claim_type = str(item.get("claim_type", "")).strip() or "llm_synthesis"
        claim_text = str(item.get("claim", "")).strip()
        supporting = [str(evidence_id).strip() for evidence_id in item.get("supporting_evidence", []) if str(evidence_id).strip()]
        supporting = [evidence_id for evidence_id in supporting if evidence_id in valid_evidence_ids]
        key = (product.casefold(), claim_type.casefold(), claim_text.casefold())
        if not claim_text or not supporting or key in existing:
            continue
        existing.add(key)
        confidence = str(item.get("confidence", "medium")).strip().lower()
        if confidence not in CONFIDENCE_RANK:
            confidence = "medium"
        claims.append(
            Claim(
                task_id=state.task.task_id,
                product=product,
                claim=claim_text.rstrip(".") + ".",
                claim_type=claim_type,
                supporting_evidence=supporting,
                confidence=confidence,
                verified_status="passed",
                included_in_report=True,
                note="Provider-enriched claim; accepted only because all supporting evidence IDs are bound.",
            )
        )
    return claims


def critic_node(state: GraphState) -> GraphState:
    open_research_ticket = any(ticket.status == "open" and ticket.target_node == "ResearchAgent" for ticket in state.review_tickets)
    if not open_research_ticket and state.loop_count < state.max_loops:
        created = _create_coverage_review_tickets(state)
        if created:
            _trace(
                state,
                "CriticAgent",
                "critic",
                "review_tickets_created",
                f"Created {created} coverage Review Ticket(s) across pricing, feature, user, security, and contradiction checks.",
                [ticket.ticket_id for ticket in state.review_tickets[-created:]],
                input_summary="Coverage check for required evidence dimensions.",
                output_summary=f"{created} ticket(s) created.",
                input_payload={
                    "required_evidence": ["pricing", "feature", "target_user", "security", "contradiction"],
                    "products": [state.task.config.target_product, *state.task.config.competitors],
                },
                output_payload={"created_ticket_count": created},
            )
            return state

    suggested_count = _suggest_review_tickets_with_llm(state)
    if suggested_count:
        return state

    _trace(state, "CriticAgent", "critic", "quality_review_passed", "Coverage is acceptable for demo strictness.")
    return state


def _create_coverage_review_tickets(state: GraphState) -> int:
    products = [state.task.config.target_product, *state.task.config.competitors]
    evidence_by_product: dict[str, set[str]] = {
        product: {item.evidence_type for item in state.evidence if item.product == product and item.status == "active"}
        for product in products
    }
    existing = {
        (ticket.product.casefold(), ticket.missing_evidence_type.casefold())
        for ticket in state.review_tickets
    }
    rules = [
        ("pricing", "official_pricing_page", "high", "Search official pricing page, or keep pricing model uncertain."),
        ("feature", "official_docs", "medium", "Search official feature/product documentation before finalizing the feature tree."),
        ("target_user", "official_docs", "medium", "Search official team/persona/customer material before finalizing personas."),
        ("security", "official_docs", "medium", "Search security or privacy documentation before scoring enterprise adoption risk."),
    ]
    created = 0
    for evidence_type, source_type, severity, action in rules:
        for product in products:
            key = (product.casefold(), evidence_type)
            if key in existing or evidence_type in evidence_by_product.get(product, set()):
                continue
            ticket = ReviewTicket(
                task_id=state.task.task_id,
                reviewer="CriticAgent",
                target_node="ResearchAgent",
                reason=f"{product} lacks official {evidence_type.replace('_', ' ')} evidence.",
                required_action=action,
                severity=severity,
                product=product,
                missing_evidence_type=evidence_type,
                preferred_source_type=source_type,
                source_query_hint=f"{product} {evidence_type.replace('_', ' ')} official supplemental",
            )
            state.review_tickets.append(ticket)
            created += 1
            existing.add(key)
            break

    contradiction_key = (state.task.config.target_product.casefold(), "contradiction")
    if contradiction_key not in existing and not any(item.evidence_type == "contradiction" for item in state.evidence):
        ticket = ReviewTicket(
            task_id=state.task.task_id,
            reviewer="CriticAgent",
            target_node="ResearchAgent",
            reason="Contradiction scan has no explicit confirming or conflicting evidence.",
            required_action="Run a contradiction-oriented source check before treating the comparison as externally publishable.",
            severity="medium",
            product=state.task.config.target_product,
            missing_evidence_type="contradiction",
            preferred_source_type="official_or_independent",
            source_query_hint=f"{state.task.config.target_product} contradictions limitations official independent",
        )
        state.review_tickets.append(ticket)
        created += 1
    return created


def _suggest_review_tickets_with_llm(state: GraphState) -> int:
    providers = build_provider_bundle()
    payload = _review_ticket_suggestion_payload(state)
    provider_name = providers.llm.provider_name
    started = time.perf_counter()
    try:
        response = providers.llm.complete_structured("review_ticket_suggestions", payload)
        latency_ms = int((time.perf_counter() - started) * 1000)
        summary = f"{provider_name} returned review ticket suggestions."
    except ProviderRequestError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        state.tool_calls.append(
            ToolCall(
                task_id=state.task.task_id,
                agent="CriticAgent",
                tool=provider_name,
                operation="complete_structured",
                query="review_ticket_suggestions",
                status="failed",
                results_summary=str(exc),
                input_summary=f"Prompt review_ticket_suggestions with coverage for {len(payload.get('coverage', []))} product(s).",
                output_summary="Provider request failed.",
                token_count=_estimate_tokens(payload),
                latency_ms=latency_ms,
                provider_mode=providers.llm_mode,
            )
        )
        _trace(
            state,
            "CriticAgent",
            "critic",
            "llm_review_ticket_suggestions_failed",
            str(exc),
            input_summary=f"Prompt review_ticket_suggestions with coverage for {len(payload.get('coverage', []))} product(s).",
            output_summary="Provider request failed.",
            prompt_name="review_ticket_suggestions",
            prompt=AUDIT_PROMPTS["review_ticket_suggestions"],
            input_payload=payload,
            output_payload={"error": str(exc)},
            token_count=_estimate_tokens(payload),
            latency_ms=latency_ms,
            provider=provider_name,
        )
        if not providers.allow_provider_fallback:
            return 0
        fallback = MockLLMProvider()
        started = time.perf_counter()
        response = fallback.complete_structured("review_ticket_suggestions", payload)
        latency_ms = int((time.perf_counter() - started) * 1000)
        provider_name = fallback.provider_name
        summary = "Seed request failed; MockLLMProvider generated fallback review ticket suggestions."

    tickets = _validated_review_ticket_suggestions(response, state)
    token_count = _estimate_tokens(payload, response)
    if not tickets:
        state.tool_calls.append(
            ToolCall(
                task_id=state.task.task_id,
                agent="CriticAgent",
                tool=provider_name,
                operation="complete_structured",
                query="review_ticket_suggestions",
                status="skipped",
                results_summary="Provider returned no valid review ticket suggestions.",
                input_summary=f"Prompt review_ticket_suggestions with coverage for {len(payload.get('coverage', []))} product(s).",
                output_summary="No valid review ticket suggestions.",
                token_count=token_count,
                latency_ms=latency_ms,
                provider_request_id="fixture" if provider_name.startswith("Mock") else "",
                provider_mode=providers.llm_mode,
            )
        )
        _trace(
            state,
            "CriticAgent",
            "critic",
            "llm_review_ticket_suggestions_skipped",
            "Provider returned no valid review ticket suggestions.",
            input_summary=f"Prompt review_ticket_suggestions with coverage for {len(payload.get('coverage', []))} product(s).",
            output_summary="No valid review ticket suggestions.",
            prompt_name="review_ticket_suggestions",
            prompt=AUDIT_PROMPTS["review_ticket_suggestions"],
            input_payload=payload,
            output_payload=response if isinstance(response, dict) else {},
            token_count=token_count,
            latency_ms=latency_ms,
            provider=provider_name,
            provider_request_id="fixture" if provider_name.startswith("Mock") else "",
        )
        return 0

    state.review_tickets.extend(tickets)
    state.tool_calls.append(
        ToolCall(
            task_id=state.task.task_id,
            agent="CriticAgent",
            tool=provider_name,
            operation="complete_structured",
            query="review_ticket_suggestions",
            status="success",
            results_summary=f"{summary} Added {len(tickets)} ticket(s).",
            input_summary=f"Prompt review_ticket_suggestions with coverage for {len(payload.get('coverage', []))} product(s).",
            output_summary=f"Added {len(tickets)} valid review ticket(s).",
            token_count=token_count,
            latency_ms=latency_ms,
            provider_request_id="fixture" if provider_name.startswith("Mock") else "",
            provider_mode=providers.llm_mode,
        )
    )
    _trace(
        state,
        "CriticAgent",
        "critic",
        "llm_review_ticket_suggestions_applied",
        f"{summary} Added {len(tickets)} ticket(s).",
        [ticket.ticket_id for ticket in tickets],
        input_summary=f"Prompt review_ticket_suggestions with coverage for {len(payload.get('coverage', []))} product(s).",
        output_summary=f"Added {len(tickets)} valid review ticket(s).",
        prompt_name="review_ticket_suggestions",
        prompt=AUDIT_PROMPTS["review_ticket_suggestions"],
        input_payload=payload,
        output_payload=response if isinstance(response, dict) else {},
        token_count=token_count,
        latency_ms=latency_ms,
        provider=provider_name,
        provider_request_id="fixture" if provider_name.startswith("Mock") else "",
    )
    return len(tickets)


def _review_ticket_suggestion_payload(state: GraphState) -> dict:
    return {
        "task": {
            "domain": state.task.config.domain,
            "target_product": state.task.config.target_product,
            "competitors": state.task.config.competitors,
            "analysis_goals": state.task.config.analysis_goals,
            "evidence_strictness": state.task.config.evidence_strictness,
        },
        "coverage": [
            {
                "product": product,
                "evidence_types": sorted({item.evidence_type for item in state.evidence if item.product == product and item.status == "active"}),
                "claim_statuses": sorted({claim.verified_status for claim in state.claims if claim.product == product}),
            }
            for product in [state.task.config.target_product, *state.task.config.competitors]
        ],
        "existing_review_tickets": [
            {
                "product": ticket.product,
                "missing_evidence_type": ticket.missing_evidence_type,
                "target_node": ticket.target_node,
                "status": ticket.status,
            }
            for ticket in state.review_tickets
        ],
    }


def _validated_review_ticket_suggestions(response: dict, state: GraphState) -> list[ReviewTicket]:
    raw_tickets = response.get("review_tickets") if isinstance(response, dict) else None
    if not isinstance(raw_tickets, list):
        return []
    products = {state.task.config.target_product, *state.task.config.competitors}
    existing = {
        (
            ticket.product.casefold(),
            ticket.missing_evidence_type.casefold(),
            ticket.target_node.casefold(),
        )
        for ticket in state.review_tickets
    }
    tickets: list[ReviewTicket] = []
    for item in raw_tickets[:3]:
        if not isinstance(item, dict):
            continue
        product = str(item.get("product", "")).strip()
        missing_evidence_type = str(item.get("missing_evidence_type", "")).strip() or "verification"
        target_node = str(item.get("target_node", "ResearchAgent")).strip() or "ResearchAgent"
        reason = str(item.get("reason", "")).strip()
        required_action = str(item.get("required_action", "")).strip()
        preferred_source_type = str(item.get("preferred_source_type", "official")).strip() or "official"
        severity = str(item.get("severity", "medium")).strip().lower()
        if severity not in {"critical", "high", "medium", "low"}:
            severity = "medium"
        key = (product.casefold(), missing_evidence_type.casefold(), target_node.casefold())
        if product not in products or not reason or not required_action or key in existing:
            continue
        existing.add(key)
        tickets.append(
            ReviewTicket(
                task_id=state.task.task_id,
                reviewer="CriticAgent",
                target_node=target_node,
                reason=reason,
                required_action=required_action,
                severity=severity,
                product=product,
                missing_evidence_type=missing_evidence_type,
                preferred_source_type=preferred_source_type,
                source_query_hint=str(item.get("source_query_hint", "")).strip() or f"{product} {missing_evidence_type} official",
            )
        )
    return tickets


def _build_feature_tree(state: GraphState) -> FeatureTree:
    products = [state.task.config.target_product, *state.task.config.competitors]
    children: list[FeatureTreeNode] = []
    for product in products:
        feature_evidence = [item for item in state.evidence if item.product == product and item.evidence_type == "feature" and item.status == "active"]
        agent_evidence = [item for item in state.evidence if item.product == product and item.evidence_type == "agent_capability" and item.status == "active"]
        security_evidence = [item for item in state.evidence if item.product == product and item.evidence_type == "security" and item.status == "active"]
        product_children = [
            FeatureTreeNode(
                name="Core product workflow",
                description=(feature_evidence[0].summary if feature_evidence else "Feature coverage requires supplemental evidence."),
                evidence_ids=[item.evidence_id for item in feature_evidence],
            ),
            FeatureTreeNode(
                name="Agent / AI workflow",
                description=(agent_evidence[0].summary if agent_evidence else "Agent capability is not explicitly covered by current evidence."),
                evidence_ids=[item.evidence_id for item in agent_evidence],
            ),
            FeatureTreeNode(
                name="Team / security readiness",
                description=(security_evidence[0].summary if security_evidence else "Security readiness is an open adoption-risk check."),
                evidence_ids=[item.evidence_id for item in security_evidence],
            ),
        ]
        children.append(
            FeatureTreeNode(
                name=product,
                description=f"Evidence-backed capability tree for {product}.",
                children=product_children,
            )
        )
    covered = len([node for product_node in children for node in product_node.children if node.evidence_ids])
    total = sum(len(product_node.children) for product_node in children)
    return FeatureTree(
        root=FeatureTreeNode(
            name=f"{state.task.config.target_product} competitive feature map",
            description="FeatureTree groups product workflow, agent/AI workflow, and team readiness signals.",
            children=children,
        ),
        coverage_note=f"{covered}/{total} feature-tree leaves have active evidence; uncovered leaves become review-ticket follow-up.",
    )


def _build_pricing_model(state: GraphState) -> PricingModel:
    products = [state.task.config.target_product, *state.task.config.competitors]
    plans: list[PricingPlan] = []
    for product in products:
        pricing_evidence = [item for item in state.evidence if item.product == product and item.evidence_type == "pricing" and item.status == "active"]
        if pricing_evidence:
            summary = pricing_evidence[0].summary
            tiers = _pricing_tiers_from_summary(summary)
            plans.append(
                PricingPlan(
                    product=product,
                    model="Published subscription tiers",
                    tiers=tiers,
                    monetization_signal=summary,
                    evidence_ids=[item.evidence_id for item in pricing_evidence],
                    confidence="high" if any(item.confidence == "high" for item in pricing_evidence) else "medium",
                )
            )
        else:
            plans.append(
                PricingPlan(
                    product=product,
                    model="Unverified",
                    tiers=[],
                    monetization_signal="Pricing evidence is missing; do not infer plan structure.",
                    evidence_ids=[],
                    confidence="low",
                    risk="Pricing gap should trigger supplemental research or a downgraded report claim.",
                )
            )
    missing = [plan.product for plan in plans if not plan.evidence_ids]
    summary = (
        "All compared products have pricing evidence."
        if not missing
        else f"Pricing model remains incomplete for {', '.join(missing)} until supplemental evidence resolves the gap."
    )
    return PricingModel(plans=plans, comparison_summary=summary)


def _pricing_tiers_from_summary(summary: str) -> list[str]:
    known = ["free", "individual", "team", "teams", "business", "enterprise", "plus", "pro", "paid"]
    lowered = summary.casefold()
    tiers = [item.title() for item in known if item in lowered]
    return tiers or ["Published plan structure"]


def _build_user_personas(state: GraphState) -> list[UserPersona]:
    target = state.task.config.target_product
    persona_evidence = [item for item in state.evidence if item.evidence_type == "target_user" and item.status == "active"]
    target_evidence = [item for item in persona_evidence if item.product == target] or persona_evidence[:2]
    personas = [
        UserPersona(
            name="Individual AI-assisted developer",
            segment="Builder / IC engineer",
            jobs_to_be_done=[
                "Complete coding tasks faster inside the development environment.",
                "Use codebase-aware assistance without constantly switching tools.",
            ],
            pains=[
                "Context switching between editor, docs, and chat tools.",
                "Unclear trust boundary when AI output is not evidence-backed.",
            ],
            decision_criteria=[
                "Quality of codebase context",
                "Speed of iteration",
                "Transparent pricing and usage limits",
            ],
            evidence_ids=[item.evidence_id for item in target_evidence[:2]],
        ),
        UserPersona(
            name="Engineering team lead",
            segment="Team / platform buyer",
            jobs_to_be_done=[
                "Standardize AI coding assistance across a team.",
                "Evaluate productivity upside against security, privacy, and cost controls.",
            ],
            pains=[
                "Security review slows adoption when vendor controls are unclear.",
                "Pricing comparisons are difficult when plan limits differ.",
            ],
            decision_criteria=[
                "Admin controls and security posture",
                "Team plan clarity",
                "Evidence-backed feature coverage",
            ],
            evidence_ids=[item.evidence_id for item in persona_evidence[:4]],
        ),
    ]
    return personas


def _build_swot(state: GraphState, included: list[Claim], uncertain: list[Claim]) -> SwotAnalysis:
    target = state.task.config.target_product
    evidence_ids = [evidence_id for claim in included for evidence_id in claim.supporting_evidence][:12]
    target_claims = [claim for claim in included if claim.product == target]
    competitor_count = len(state.task.config.competitors)
    unresolved_tickets = [ticket for ticket in state.review_tickets if ticket.status in {"open", "accepted", "rerun_started"}]
    downgraded_or_uncertain = [claim for claim in uncertain if claim.product == target or claim.product in {"Opportunity", "Risk"}]
    return SwotAnalysis(
        strengths=[
            (target_claims[0].claim if target_claims else f"{target} has at least one evidence-backed product-positioning signal."),
            "The report binds claims to evidence IDs, making product and PM review auditable.",
        ],
        weaknesses=[
            "Missing or downgraded evidence is excluded from final claims instead of being treated as fact.",
            f"{len(downgraded_or_uncertain)} target-adjacent claim(s) still need reviewer attention.",
        ],
        opportunities=[
            "Use feature-tree gaps to prioritize follow-up research and product messaging comparison.",
            f"Compare {competitor_count} competitor(s) through pricing and persona fit rather than a single score.",
        ],
        threats=[
            f"{len(unresolved_tickets)} unresolved Review Ticket(s) can block external publication.",
            "Live provider results may differ from demo fixtures, so provider mode must be disclosed.",
        ],
        evidence_ids=evidence_ids,
    )


def _feature_tree_markdown(node: FeatureTreeNode, depth: int = 0) -> list[str]:
    prefix = "  " * depth + "- "
    evidence = f" Evidence: {', '.join(node.evidence_ids)}" if node.evidence_ids else ""
    lines = [f"{prefix}**{node.name}**：{node.description}{evidence}"]
    for child in node.children:
        lines.extend(_feature_tree_markdown(child, depth + 1))
    return lines


def _swot_lines(swot: SwotAnalysis) -> list[str]:
    return [
        "- **Strengths**：" + "；".join(swot.strengths),
        "- **Weaknesses**：" + "；".join(swot.weaknesses),
        "- **Opportunities**：" + "；".join(swot.opportunities),
        "- **Threats**：" + "；".join(swot.threats),
        f"- Evidence: {', '.join(swot.evidence_ids) or 'none'}",
    ]


def writer_node(state: GraphState) -> GraphState:
    included = [claim for claim in state.claims if claim.included_in_report and claim.verified_status == "passed"]
    uncertain = [claim for claim in state.claims if claim.verified_status != "passed"]
    trust = state.trust_summary or _build_trust_summary(state)
    feature_tree = _build_feature_tree(state)
    pricing_model = _build_pricing_model(state)
    personas = _build_user_personas(state)
    swot = _build_swot(state, included, uncertain)
    lines = [
        f"# {state.task.config.target_product} 竞品分析报告",
        "",
        "## 可信度摘要",
        f"- 证据绑定率：{trust.claim_evidence_binding_rate:.0%}",
        f"- 官方来源占比：{trust.official_source_ratio:.0%}",
        f"- 已通过结论：{trust.passed_claim_count} / {trust.total_claim_count}",
        f"- 不确定 / 阻断 / 降级结论：{trust.uncertain_claim_count} / {trust.blocked_claim_count} / {trust.downgraded_claim_count}",
        f"- 未解决 Review Ticket：{trust.unresolved_ticket_count}",
        f"- 运行模式：{trust.provider_mode_label}",
        f"- Search provider：{trust.search_mode}",
        f"- LLM provider：{trust.llm_mode}",
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
        for claim_type in [*claim_types, "feature", "target_user", "security"]:
            claim = next((item for item in state.claims if item.product == product and item.claim_type == claim_type), None)
            if claim and claim.verified_status == "passed":
                cells.append(f"{claim_type}: {claim.confidence}, evidence {len(claim.supporting_evidence)}")
            elif claim:
                cells.append(f"{claim_type}: uncertain")
            else:
                cells.append(f"{claim_type}: not assessed")
        lines.append(f"- **{product}**：{' | '.join(cells)}")
    lines.extend(["", "## 功能树 FeatureTree"])
    lines.extend(_feature_tree_markdown(feature_tree.root))
    lines.append(f"- 覆盖说明：{feature_tree.coverage_note}")
    lines.extend(["", "## 定价模型 PricingModel"])
    for plan in pricing_model.plans:
        tier_text = " / ".join(plan.tiers) if plan.tiers else "未覆盖"
        lines.append(f"- **{plan.product}**：{plan.model}；层级：{tier_text}；信号：{plan.monetization_signal}；置信度：{plan.confidence}；Evidence: {', '.join(plan.evidence_ids) or 'none'}")
        if plan.risk:
            lines.append(f"  - 风险：{plan.risk}")
    lines.append(f"- 对比摘要：{pricing_model.comparison_summary}")
    lines.extend(["", "## 用户画像 UserPersona"])
    for persona in personas:
        lines.append(f"- **{persona.name} / {persona.segment}**")
        lines.append(f"  - JTBD：{'；'.join(persona.jobs_to_be_done)}")
        lines.append(f"  - 痛点：{'；'.join(persona.pains)}")
        lines.append(f"  - 决策标准：{'；'.join(persona.decision_criteria)}")
        lines.append(f"  - Evidence: {', '.join(persona.evidence_ids) or 'none'}")
    lines.extend(["", "## SWOT"])
    lines.extend(_swot_lines(swot))
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
    markdown = "\n".join(lines)
    markdown = _enhance_report_with_llm(state, markdown, included, uncertain, trust)
    state.report = Report(
        task_id=state.task.task_id,
        title=f"{state.task.config.target_product} Competitor Analysis",
        markdown=markdown,
        sections=_build_report_sections(markdown, state.claims),
        claim_count=len(state.claims),
        unsupported_claim_count=len([claim for claim in state.claims if claim.verified_status in {"blocked", "unsupported", "downgraded"}]),
        stale_claim_count=len([claim for claim in state.claims if claim.verified_status == "stale"]),
        evidence_coverage_rate=(
            len([claim for claim in state.claims if claim.supporting_evidence]) / len(state.claims)
            if state.claims
            else 0
        ),
        feature_tree=feature_tree,
        pricing_model=pricing_model,
        user_personas=personas,
        swot=swot,
    )
    _trace(state, "WriterAgent", "writer", "report_drafted", "Generated Markdown report draft.")
    return state


def _enhance_report_with_llm(state: GraphState, markdown: str, included: list[Claim], uncertain: list[Claim], trust: TrustSummary) -> str:
    providers = build_provider_bundle()
    payload = _report_enhancement_payload(state, included, uncertain, trust)
    provider_name = providers.llm.provider_name
    started = time.perf_counter()
    try:
        response = providers.llm.complete_structured("report_enhancement", payload)
        latency_ms = int((time.perf_counter() - started) * 1000)
        status = "success"
        summary = f"{provider_name} returned report enhancement."
    except ProviderRequestError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        state.tool_calls.append(
            ToolCall(
                task_id=state.task.task_id,
                agent="WriterAgent",
                tool=provider_name,
                operation="complete_structured",
                query="report_enhancement",
                status="failed",
                results_summary=str(exc),
                input_summary=f"Prompt report_enhancement with {len(included)} included claim(s).",
                output_summary="Provider request failed.",
                token_count=_estimate_tokens(payload),
                latency_ms=latency_ms,
                provider_mode=providers.llm_mode,
            )
        )
        _trace(
            state,
            "WriterAgent",
            "writer",
            "llm_enhancement_failed",
            str(exc),
            input_summary=f"Prompt report_enhancement with {len(included)} included claim(s).",
            output_summary="Provider request failed.",
            prompt_name="report_enhancement",
            prompt=AUDIT_PROMPTS["report_enhancement"],
            input_payload=payload,
            output_payload={"error": str(exc)},
            token_count=_estimate_tokens(payload),
            latency_ms=latency_ms,
            provider=provider_name,
        )
        if not providers.allow_provider_fallback:
            return markdown
        fallback = MockLLMProvider()
        started = time.perf_counter()
        response = fallback.complete_structured("report_enhancement", payload)
        latency_ms = int((time.perf_counter() - started) * 1000)
        provider_name = fallback.provider_name
        status = "success"
        summary = "Seed request failed; MockLLMProvider generated fallback report enhancement."

    enhancement = _format_report_enhancement(response)
    token_count = _estimate_tokens(payload, response)
    if not enhancement:
        state.tool_calls.append(
            ToolCall(
                task_id=state.task.task_id,
                agent="WriterAgent",
                tool=provider_name,
                operation="complete_structured",
                query="report_enhancement",
                status="skipped",
                results_summary="Provider returned no valid report enhancement sections.",
                input_summary=f"Prompt report_enhancement with {len(included)} included claim(s).",
                output_summary="No valid report enhancement sections.",
                token_count=token_count,
                latency_ms=latency_ms,
                provider_request_id="fixture" if provider_name.startswith("Mock") else "",
                provider_mode=providers.llm_mode,
            )
        )
        _trace(
            state,
            "WriterAgent",
            "writer",
            "llm_enhancement_skipped",
            "Provider returned no valid report enhancement sections.",
            input_summary=f"Prompt report_enhancement with {len(included)} included claim(s).",
            output_summary="No valid report enhancement sections.",
            prompt_name="report_enhancement",
            prompt=AUDIT_PROMPTS["report_enhancement"],
            input_payload=payload,
            output_payload=response if isinstance(response, dict) else {},
            token_count=token_count,
            latency_ms=latency_ms,
            provider=provider_name,
            provider_request_id="fixture" if provider_name.startswith("Mock") else "",
        )
        return markdown

    state.tool_calls.append(
        ToolCall(
            task_id=state.task.task_id,
            agent="WriterAgent",
            tool=provider_name,
            operation="complete_structured",
            query="report_enhancement",
            status=status,
            results_summary=summary,
            input_summary=f"Prompt report_enhancement with {len(included)} included claim(s).",
            output_summary="Report enhancement sections appended.",
            token_count=token_count,
            latency_ms=latency_ms,
            provider_request_id="fixture" if provider_name.startswith("Mock") else "",
            provider_mode=providers.llm_mode,
        )
    )
    _trace(
        state,
        "WriterAgent",
        "writer",
        "llm_enhancement_applied",
        summary,
        input_summary=f"Prompt report_enhancement with {len(included)} included claim(s).",
        output_summary="Report enhancement sections appended.",
        prompt_name="report_enhancement",
        prompt=AUDIT_PROMPTS["report_enhancement"],
        input_payload=payload,
        output_payload=response if isinstance(response, dict) else {},
        token_count=token_count,
        latency_ms=latency_ms,
        provider=provider_name,
        provider_request_id="fixture" if provider_name.startswith("Mock") else "",
    )
    return f"{markdown}\n\n{enhancement}"


def _report_enhancement_payload(state: GraphState, included: list[Claim], uncertain: list[Claim], trust: TrustSummary) -> dict:
    return {
        "task": {
            "domain": state.task.config.domain,
            "target_product": state.task.config.target_product,
            "competitors": state.task.config.competitors,
            "analysis_goals": state.task.config.analysis_goals,
            "audience": state.task.config.audience,
            "evidence_strictness": state.task.config.evidence_strictness,
        },
        "trust_summary": trust.model_dump(),
        "included_claims": [
            {
                "claim_id": claim.claim_id,
                "product": claim.product,
                "claim_type": claim.claim_type,
                "claim": claim.claim,
                "supporting_evidence_count": len(claim.supporting_evidence),
            }
            for claim in included[:12]
        ],
        "uncertain_claims": [
            {
                "claim_id": claim.claim_id,
                "product": claim.product,
                "claim_type": claim.claim_type,
                "status": claim.verified_status,
                "note": claim.note or claim.claim,
            }
            for claim in uncertain[:12]
        ],
        "sources": [
            {
                "source_id": source.source_id,
                "title": source.title,
                "source_type": source.source_type,
                "product": source.product,
            }
            for source in state.sources[:20]
        ],
    }


def _format_report_enhancement(response: dict) -> str:
    executive_summary = _string_list(response.get("executive_summary"))
    recommendations = _string_list(response.get("strategic_recommendations"))
    caveats = _string_list(response.get("caveats"))
    if not executive_summary and not recommendations and not caveats:
        return ""
    lines = ["## 结构化综合摘要"]
    if executive_summary:
        lines.extend(f"- {item}" for item in executive_summary)
    if recommendations:
        lines.extend(["", "## 结构化建议"])
        lines.extend(f"- {item}" for item in recommendations)
    if caveats:
        lines.extend(["", "## 结构化 Caveats"])
        lines.extend(f"- {item}" for item in caveats)
    return "\n".join(lines)


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:8]


def _build_report_sections(markdown: str, claims: list[Claim]) -> list[ReportSection]:
    section_titles: dict[str, str] = {
        "可信度摘要": "trust_summary",
        "分析背景": "background",
        "核心结论": "core_findings",
        "产品定位与能力矩阵": "comparison_matrix",
        "功能树 FeatureTree": "feature_tree",
        "定价模型 PricingModel": "pricing_model",
        "用户画像 UserPersona": "user_persona",
        "SWOT": "swot",
        "机会点建议": "opportunities",
        "不确定性与被阻断结论": "uncertainty",
        "数据来源": "sources",
        "Agent 协作记录": "agent_trace",
    }
    section_claims: dict[str, list[str]] = {
        "core_findings": [claim.claim_id for claim in claims if claim.included_in_report and claim.verified_status == "passed"],
        "comparison_matrix": [claim.claim_id for claim in claims if claim.claim_type in {"positioning", "agent_capability", "pricing", "feature", "target_user", "security"}],
        "feature_tree": [claim.claim_id for claim in claims if claim.claim_type in {"feature", "agent_capability", "security", "comparative_feature"}],
        "pricing_model": [claim.claim_id for claim in claims if claim.claim_type == "pricing"],
        "user_persona": [claim.claim_id for claim in claims if claim.claim_type == "target_user"],
        "swot": [claim.claim_id for claim in claims if claim.included_in_report or claim.verified_status != "passed"],
        "opportunities": [claim.claim_id for claim in claims if claim.claim_type == "opportunity"],
        "uncertainty": [claim.claim_id for claim in claims if claim.verified_status != "passed"],
    }
    sections: list[ReportSection] = []
    current_title = ""
    current_lines: list[str] = []
    sort_order = 0

    def flush() -> None:
        nonlocal sort_order
        if not current_title:
            return
        key = section_titles.get(current_title, current_title.lower().replace(" ", "_"))
        claim_ids = section_claims.get(key, [])
        status = "stale" if any(claim.verified_status == "stale" for claim in claims if claim.claim_id in claim_ids) else "passed"
        sections.append(
            ReportSection(
                section_key=key,
                title=current_title,
                markdown="\n".join(current_lines).strip(),
                status=status,
                claim_ids=claim_ids,
                sort_order=sort_order,
            )
        )
        sort_order += 1

    for line in markdown.splitlines():
        if line.startswith("## "):
            flush()
            current_title = line.removeprefix("## ").strip()
            current_lines = [line]
        elif current_title:
            current_lines.append(line)
    flush()
    return sections


def evidence_reviewer_node(state: GraphState) -> GraphState:
    blocked = 0
    downgraded = 0
    evidence_by_id = {item.evidence_id: item for item in state.evidence if item.status == "active"}
    source_by_id = {source.source_id: source for source in state.sources}
    for claim in state.claims:
        if not claim.included_in_report:
            continue
        supporting = [evidence_by_id[evidence_id] for evidence_id in claim.supporting_evidence if evidence_id in evidence_by_id]
        if not supporting:
            claim.included_in_report = False
            claim.verified_status = "blocked"
            claim.note = "Blocked by Evidence Consistency Reviewer because no supporting evidence is bound."
            blocked += 1
            continue
        downgrade_reason = _strictness_downgrade_reason(state.task.config.evidence_strictness, supporting, source_by_id)
        if downgrade_reason:
            claim.included_in_report = False
            claim.verified_status = "downgraded"
            claim.note = downgrade_reason
            downgraded += 1
    _trace(
        state,
        "EvidenceConsistencyReviewer",
        "evidence_reviewer",
        "evidence_gate_completed",
        f"Blocked {blocked} unsupported claim(s) and downgraded {downgraded} claim(s) under {state.task.config.evidence_strictness} strictness.",
    )
    return state


def _strictness_downgrade_reason(strictness: str, evidence: list[Evidence], source_by_id: dict[str, Source]) -> str:
    if strictness == "low":
        return ""

    minimum = "medium" if strictness == "standard" else "high"
    best_confidence = max(CONFIDENCE_RANK.get(item.confidence, 0) for item in evidence)
    if best_confidence < CONFIDENCE_RANK[minimum]:
        return f"Downgraded by {strictness} evidence strictness because no supporting evidence met {minimum} confidence."

    if strictness == "high":
        has_official_source = any(
            (source_by_id.get(item.source_id) and source_by_id[item.source_id].source_type.startswith("official"))
            for item in evidence
        )
        if not has_official_source:
            return "Downgraded by high evidence strictness because no official source was bound."

    return ""


def _build_trust_summary(state: GraphState) -> TrustSummary:
    total_claims = len(state.claims)
    bound_claims = len([claim for claim in state.claims if claim.supporting_evidence])
    official_sources = len([source for source in state.sources if source.source_type.startswith("official")])
    unresolved_tickets = len([ticket for ticket in state.review_tickets if ticket.status == "open"])
    blocked_claims = len([claim for claim in state.claims if claim.verified_status == "blocked"])
    downgraded_claims = len([claim for claim in state.claims if claim.verified_status == "downgraded"])
    uncertain_claims = len([claim for claim in state.claims if claim.verified_status in {"uncertain", "unsupported", "contradicted"}])
    passed_claims = len([claim for claim in state.claims if claim.verified_status == "passed"])
    providers = build_provider_bundle()
    fixture_mode = any(call.tool.startswith("Mock") for call in state.tool_calls) or providers.fixture_mode
    search_modes = sorted({call.provider_mode for call in state.tool_calls if call.provider_mode and call.agent == "ResearchAgent"})
    llm_modes = sorted({call.provider_mode for call in state.tool_calls if call.provider_mode and call.agent in {"AnalystAgent", "CriticAgent", "WriterAgent"}})
    return TrustSummary(
        claim_evidence_binding_rate=bound_claims / total_claims if total_claims else 0,
        official_source_ratio=official_sources / len(state.sources) if state.sources else 0,
        blocked_claim_count=blocked_claims,
        uncertain_claim_count=uncertain_claims,
        downgraded_claim_count=downgraded_claims,
        unresolved_ticket_count=unresolved_tickets,
        passed_claim_count=passed_claims,
        total_claim_count=total_claims,
        total_source_count=len(state.sources),
        total_evidence_count=len(state.evidence),
        fixture_mode=fixture_mode,
        provider_mode_label="Demo fixture run" if fixture_mode else "Live provider run",
        search_mode=", ".join(search_modes) or providers.search_mode,
        llm_mode=", ".join(llm_modes) or providers.llm_mode,
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
