from __future__ import annotations

from collections import defaultdict
import json
import re
import time
from urllib.parse import parse_qs, urlparse

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
    SentimentSummary,
    Source,
    SocialComment,
    SocialInsightFinding,
    SocialInsight,
    SocialPlatformConfig,
    SocialPost,
    SwotAnalysis,
    TaskBrief,
    ToolCall,
    TrustSummary,
    UserPersona,
    now_iso,
)
from app.providers.errors import ProviderRequestError
from app.providers.factory import ProviderBundle, build_provider_bundle
from app.providers.mock_llm import MockLLMProvider
from app.providers.mock_search import MockSearchProvider
from app.providers.xhs_mcp import XhsMcpClient
from app.skills.registry import SkillPromptComposer, skill_snapshot, skill_trace_fields
from app.storage.sqlite import SQLiteStore
from app.templates.catalog import select_template


CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
skill_store = SQLiteStore()
PRODUCT_AUTHORITY = {
    "飞书": {
        "aliases": ["飞书", "feishu", "lark"],
        "official_domains": ["feishu.cn", "larksuite.com", "open.feishu.cn", "www.feishu.cn"],
        "official_url_markers": ["github.com/larksuite/"],
        "qualifiers": ["协同", "文档", "开放平台", "openclaw", "cli", "agent", "mcp", "工作流", "ai"],
        "noise": [],
        "query_context": "飞书 Feishu Lark Open Platform",
    },
    "钉钉": {
        "aliases": ["钉钉", "dingtalk"],
        "official_domains": ["dingtalk.com", "developers.dingtalk.com", "open.dingtalk.com"],
        "official_url_markers": ["github.com/open-dingtalk/", "github.com/alibaba/dingtalk"],
        "qualifiers": ["协同", "开放平台", "dingtalk", "cli", "agent", "工作流", "ai", "钉钉"],
        "noise": [],
        "query_context": "钉钉 DingTalk Open Platform",
    },
    "企业微信": {
        "aliases": ["企业微信", "wecom", "work weixin", "企微"],
        "official_domains": ["work.weixin.qq.com", "developer.work.weixin.qq.com", "wecom.tencent.com"],
        "official_url_markers": ["github.com/tencent/wecom", "github.com/wecom"],
        "qualifiers": ["企业微信", "开放接口", "开发者中心", "wecom", "cli", "agent", "机器人", "mcp"],
        "noise": [],
        "query_context": "企业微信 WeCom developer center",
    },
    "cursor": {
        "aliases": ["cursor", "anysphere"],
        "official_domains": ["cursor.com", "docs.cursor.com"],
        "qualifiers": ["ai", "code", "coding", "editor", "agent", "developer", "anysphere", "mcp"],
        "noise": ["css", "mouse cursor", "pointer", "mozilla", "mdn", "w3schools"],
        "query_context": "Anysphere Cursor AI code editor",
    },
    "github copilot": {
        "aliases": ["github copilot", "copilot"],
        "official_domains": ["github.com", "docs.github.com"],
        "qualifiers": ["ai", "code", "coding", "developer", "agent", "github", "copilot"],
        "noise": ["pilot", "aircraft"],
        "query_context": "GitHub Copilot AI coding assistant",
    },
    "windsurf": {
        "aliases": ["windsurf", "codeium"],
        "official_domains": ["windsurf.com", "codeium.com", "docs.windsurf.com"],
        "qualifiers": ["ai", "code", "coding", "editor", "agent", "developer", "cascade", "codeium"],
        "noise": ["sport", "sailing", "board", "weather", "kite", "water"],
        "query_context": "Windsurf Codeium AI code editor",
    },
    "trae": {
        "aliases": ["trae"],
        "official_domains": ["trae.ai"],
        "qualifiers": ["ai", "code", "coding", "agent", "developer", "ide"],
        "noise": [],
        "query_context": "TRAE AI coding agent IDE",
    },
}
AUDIT_PROMPTS = {
    "claim_enrichment": "Given task context and bound evidence, return only evidence-bound enriched claims.",
    "review_ticket_suggestions": "Given coverage and claim statuses, return actionable review tickets for missing or risky evidence.",
    "report_enhancement": (
        "Given included and uncertain claims, return a polished PM-style synthesis in plain Chinese. "
        "Paraphrase the evidence into natural analysis language; do not paste source wording except inside Resources."
    ),
}
THIRD_PARTY_SOURCE_RATIO_TARGET = 0.35
OFFICIAL_SOURCE_RATIO_REVIEW_THRESHOLD = 0.65


def _profile_for_product(product: str) -> dict:
    key = product.casefold().strip()
    return PRODUCT_AUTHORITY.get(key, {"aliases": [product.casefold()], "official_domains": [], "qualifiers": [], "noise": [], "query_context": product})


def _query_product_name(product: str) -> str:
    return str(_profile_for_product(product).get("query_context") or product)


def _netloc(url: str) -> str:
    return urlparse(url or "").netloc.casefold().removeprefix("www.")


def _domain_matches(netloc: str, domains: list[str]) -> bool:
    return any(netloc == domain or netloc.endswith(f".{domain}") for domain in domains)


def _contains_noise_term(text: str, term: str) -> bool:
    term = term.strip().casefold()
    if not term:
        return False
    if re.fullmatch(r"[a-z0-9][a-z0-9 -]*", term):
        pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
        return bool(re.search(pattern, text))
    return term in text


def _source_authority(raw: dict, query: SearchQuery) -> tuple[str, str, str]:
    raw_source_type = str(raw.get("source_type") or "")
    if raw_source_type.startswith("social_"):
        return raw_source_type, str(raw.get("confidence") or "medium"), "Social listening source imported from configured platform or user-provided summary."

    profile = _profile_for_product(query.product)
    official_domains = [str(item).casefold() for item in profile.get("official_domains", [])]
    official_markers = [str(item).casefold() for item in profile.get("official_url_markers", []) if str(item).strip()]
    aliases = [str(item).casefold() for item in profile.get("aliases", []) if str(item).strip()]
    qualifiers = [str(item).casefold() for item in profile.get("qualifiers", []) if str(item).strip()]
    noise = [str(item).casefold() for item in profile.get("noise", []) if str(item).strip()]
    url = str(raw.get("url") or "")
    domain = _netloc(url)
    haystack = " ".join(
        str(raw.get(key) or "")
        for key in ("title", "summary", "content", "locator", "url")
    ).casefold()
    is_official = _domain_matches(domain, official_domains) or any(marker in url.casefold() for marker in official_markers)
    has_alias = any(alias in haystack for alias in aliases)
    has_qualifier = not qualifiers or any(term in haystack for term in qualifiers)
    has_noise = any(_contains_noise_term(haystack, term) for term in noise)

    if is_official:
        source_type = query.source_preference if query.source_preference.startswith("official") else "official_verified"
        return source_type, "high", "Verified official product domain."
    if query.source_preference in {"independent_web", "official_or_independent"} and has_alias and not has_noise:
        return "third_party_relevant", "medium", "Independent web source matched product alias without known entity noise."
    if has_alias and has_qualifier and not has_noise:
        return "third_party_relevant", "medium", "Third-party source matched product alias and domain context."
    return "irrelevant", "low", "Rejected by source relevance gate: source did not match product entity/domain context."


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
    skill_fields: dict[str, str] | None = None,
) -> None:
    skill_fields = skill_fields or {}
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
            skill_name=skill_fields.get("skill_name", ""),
            skill_repo=skill_fields.get("skill_repo", ""),
            skill_path=skill_fields.get("skill_path", ""),
            skill_hash=skill_fields.get("skill_hash", ""),
            skill_license=skill_fields.get("skill_license", ""),
            related_ids=related_ids or [],
        )
    )


def _skill_context(slot: str):
    return SkillPromptComposer(skill_store).context_for_slot(slot)


def _complete_with_skill(llm, purpose: str, payload: dict, skill_prompt: str = "") -> dict:
    try:
        return llm.complete_structured(purpose, payload, skill_prompt=skill_prompt)
    except TypeError as exc:
        if "skill_prompt" not in str(exc):
            raise
        return llm.complete_structured(purpose, payload)


def _estimate_tokens(*payloads: object) -> int:
    text = " ".join(json.dumps(payload, ensure_ascii=False, default=str) for payload in payloads)
    return max(1, len(text) // 4)


def _provider_meta(response: object) -> dict:
    if isinstance(response, dict):
        meta = response.get("__provider_meta")
        if isinstance(meta, dict):
            return meta
    return {}


def _provider_request_id(response: object, provider_name: str) -> str:
    if provider_name.startswith("Mock"):
        return "fixture"
    return str(_provider_meta(response).get("request_id") or "")


def _provider_token_count(response: object, fallback: int) -> int:
    usage = _provider_meta(response).get("usage")
    if isinstance(usage, dict):
        total = usage.get("total_tokens")
        if isinstance(total, int):
            return total
    return fallback


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
    product_query = _query_product_name(product)
    source_hint = ticket.source_query_hint or _supplemental_query_hint(product_query, evidence_type)
    priority = ticket.severity if ticket.severity in {"high", "medium", "low"} else "high"
    return SearchQuery(
        query=source_hint,
        product=product,
        expected_evidence=evidence_type,
        priority=priority,
        source_preference=ticket.preferred_source_type or "official_or_independent",
        is_supplemental=True,
        related_ticket_id=ticket.ticket_id,
    )


def _matching_ticket_evidence_ids(state: GraphState, ticket: ReviewTicket) -> list[str]:
    if not ticket.product or not ticket.missing_evidence_type:
        return []
    return [
        item.evidence_id
        for item in state.evidence
        if item.status == "active"
        and item.product.casefold() == ticket.product.casefold()
        and item.evidence_type.casefold() == ticket.missing_evidence_type.casefold()
    ]


def _matching_ticket_claim_ids(state: GraphState, ticket: ReviewTicket) -> list[str]:
    evidence_ids = set(_matching_ticket_evidence_ids(state, ticket))
    if not evidence_ids:
        return []
    return [
        claim.claim_id
        for claim in state.claims
        if claim.verified_status == "passed"
        and claim.included_in_report
        and claim.product.casefold() == ticket.product.casefold()
        and claim.claim_type.casefold() == ticket.missing_evidence_type.casefold()
        and evidence_ids.intersection(claim.supporting_evidence)
    ]


def _matching_ticket_claim_statuses(state: GraphState, ticket: ReviewTicket) -> list[dict[str, object]]:
    if not ticket.product or not ticket.missing_evidence_type:
        return []
    return [
        {
            "claim_id": claim.claim_id,
            "product": claim.product,
            "claim_type": claim.claim_type,
            "verified_status": claim.verified_status,
            "included_in_report": claim.included_in_report,
            "supporting_evidence": list(claim.supporting_evidence),
        }
        for claim in state.claims
        if claim.product.casefold() == ticket.product.casefold()
        and claim.claim_type.casefold() == ticket.missing_evidence_type.casefold()
    ]


def _start_ticket_rerun_snapshot(state: GraphState, ticket: ReviewTicket) -> None:
    ticket.status = "rerun_started"
    if not ticket.added_evidence_ids and not ticket.improved_claim_ids:
        ticket.before_evidence_ids = _matching_ticket_evidence_ids(state, ticket)
        ticket.before_claim_statuses = _matching_ticket_claim_statuses(state, ticket)
    ticket.added_evidence_ids = []
    ticket.improved_claim_ids = []
    ticket.after_claim_statuses = []


def start_review_ticket_rerun(state: GraphState, ticket: ReviewTicket) -> None:
    _start_ticket_rerun_snapshot(state, ticket)


def resolve_review_ticket_improvements(state: GraphState) -> int:
    resolved = 0
    for ticket in state.review_tickets:
        if ticket.status != "rerun_started":
            continue
        current = set(_matching_ticket_evidence_ids(state, ticket))
        before = set(ticket.before_evidence_ids)
        added = sorted(current - before)
        after_statuses = _matching_ticket_claim_statuses(state, ticket)
        before_passed = any(item.get("verified_status") == "passed" and item.get("included_in_report") for item in ticket.before_claim_statuses)
        improved = [
            claim_id
            for claim_id in _matching_ticket_claim_ids(state, ticket)
            if any(
                claim.claim_id == claim_id and set(claim.supporting_evidence).intersection(added)
                for claim in state.claims
            )
        ]
        ticket.added_evidence_ids = added
        ticket.improved_claim_ids = improved
        ticket.after_claim_statuses = after_statuses
        if added and improved and not before_passed:
            ticket.status = "resolved"
            ticket.resolution_summary = (
                f"Rerun added {len(added)} matching evidence item(s) and improved {len(improved)} bound claim(s)."
            )
            ticket.resolved_at = now_iso()
            resolved += 1
        else:
            ticket.resolution_summary = (
                "Rerun did not prove a before/after claim improvement with newly bound evidence; keep this ticket in reviewer attention."
            )
    if resolved:
        _trace(
            state,
            "CriticAgent",
            "critic",
            "review_ticket_improvement_verified",
            f"Verified {resolved} Review Ticket improvement(s) through added evidence and improved claims.",
            [ticket.ticket_id for ticket in state.review_tickets if ticket.status == "resolved" and ticket.added_evidence_ids],
            output_payload={
                ticket.ticket_id: {
                    "added_evidence_ids": ticket.added_evidence_ids,
                    "improved_claim_ids": ticket.improved_claim_ids,
                }
                for ticket in state.review_tickets
                if ticket.status == "resolved" and ticket.added_evidence_ids
            },
        )
    return resolved


def _supplemental_query_hint(product_query: str, evidence_type: str) -> str:
    hints = {
        "pricing": f"{product_query} pricing plans review comparison alternatives",
        "feature": f"{product_query} feature review walkthrough integration case study",
        "target_user": f"{product_query} customer case study team adoption community discussion use cases",
        "security": f"{product_query} security privacy incident controversy compliance limitation",
        "contradiction": f"{product_query} contradictions limitations official independent",
        "third_party_context": f"{product_query} third party review benchmark user feedback community discussion",
    }
    return hints.get(evidence_type, f"{product_query} {evidence_type.replace('_', ' ')} official independent review")


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
            product_query = _query_product_name(product)
            queries.append(
                SearchQuery(
                    query=f"{product_query} official homepage product overview",
                    product=product,
                    expected_evidence="positioning",
                    priority="high",
                    source_preference="official_homepage",
                )
            )
            queries.append(
                SearchQuery(
                    query=f"{product_query} pricing official",
                    product=product,
                    expected_evidence="pricing",
                    priority="high",
                    source_preference="official_pricing_page",
                )
            )
            queries.append(
                SearchQuery(
                    query=f"{product_query} official product features docs",
                    product=product,
                    expected_evidence="feature",
                    priority="medium",
                    source_preference="official_docs",
                )
            )
            queries.append(
                SearchQuery(
                    query=f"{product_query} target users teams official",
                    product=product,
                    expected_evidence="target_user",
                    priority="medium",
                    source_preference="official_docs",
                )
            )
            queries.append(
                SearchQuery(
                    query=f"{product_query} security privacy official",
                    product=product,
                    expected_evidence="security",
                    priority="low",
                    source_preference="official_docs",
                )
            )
            queries.append(
                SearchQuery(
                    query=f"{product_query} 用户反馈 评测 缺点 体验",
                    product=product,
                    expected_evidence="positioning",
                    priority="medium",
                    source_preference="independent_web",
                )
            )
            queries.append(
                SearchQuery(
                    query=f"{product_query} 教程 使用流程 集成 案例",
                    product=product,
                    expected_evidence="feature",
                    priority="medium",
                    source_preference="independent_web",
                )
            )
            queries.append(
                SearchQuery(
                    query=f"{product_query} 社区讨论 forum reddit 知乎 使用反馈",
                    product=product,
                    expected_evidence="target_user",
                    priority="medium",
                    source_preference="independent_web",
                )
            )
            queries.append(
                SearchQuery(
                    query=f"{product_query} comparison alternatives vs benchmark",
                    product=product,
                    expected_evidence="positioning",
                    priority="medium",
                    source_preference="independent_web",
                )
            )
            queries.append(
                SearchQuery(
                    query=f"{product_query} third party review benchmark user feedback",
                    product=product,
                    expected_evidence="third_party_context",
                    priority="medium",
                    source_preference="independent_web",
                )
            )
            queries.append(
                SearchQuery(
                    query=f"{product_query} customer case study team adoption review",
                    product=product,
                    expected_evidence="target_user",
                    priority="medium",
                    source_preference="independent_web",
                )
            )
            queries.append(
                SearchQuery(
                    query=f"{product_query} security privacy incident controversy compliance limitation",
                    product=product,
                    expected_evidence="security",
                    priority="medium",
                    source_preference="independent_web",
                )
            )
            if cfg.domain == "ai_tools":
                queries.append(
                    SearchQuery(
                        query=f"{product_query} AI agent coding workflow docs",
                        product=product,
                        expected_evidence="agent_capability",
                        priority="medium",
                        source_preference="official_docs",
                    )
                )
                queries.append(
                    SearchQuery(
                        query=f"{product_query} developer experience review agent workflow community",
                        product=product,
                        expected_evidence="agent_capability",
                        priority="medium",
                        source_preference="independent_web",
                    )
                )
        state.search_plan = SearchPlan(
            task_id=state.task.task_id,
            queries=queries,
            preferred_source_types=[
                "official_homepage",
                "official_docs",
                "official_pricing_page",
                "official_security_page",
                "independent_web",
                "third_party_relevant",
            ],
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
            _start_ticket_rerun_snapshot(state, ticket)
            key = (ticket.product, ticket.missing_evidence_type)
            if key in found_pairs:
                ticket.resolution_note = "Supplemental research found matching raw sources; evidence binding will decide whether this ticket resolves."
            else:
                ticket.resolution_note = "No matching source was available yet; related claims remain uncertain."
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
        if providers.search_mode.startswith("mock"):
            raise
        if not providers.allow_provider_fallback:
            _trace(
                state,
                "ProviderFactory",
                "providers",
                "provider_request_failed_without_fallback",
                f"{providers.search.provider_name} failed and mock fallback is disabled. Continuing with zero results. Reason: {exc}",
                input_payload=query.model_dump(mode="json"),
                output_payload={"error": str(exc), "fallback": "disabled"},
                provider=providers.search.provider_name,
            )
            return [], providers.search.provider_name, "; provider_error=no_fallback"
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
    query_by_text = {query.query: query for query in state.search_plan.queries} if state.search_plan else {}
    rejected = 0
    rejected_sources: list[dict[str, str]] = []
    for raw in state.raw_sources:
        if raw["url"] in existing_urls:
            continue
        query = query_by_text.get(raw.get("query", "")) or SearchQuery(
            query=raw.get("query", ""),
            product=raw.get("product", ""),
            expected_evidence=raw.get("evidence_type", ""),
            source_preference=raw.get("source_type", "web"),
        )
        source_type, confidence, authority_note = _source_authority(raw, query)
        if source_type == "irrelevant":
            rejected += 1
            rejected_sources.append(
                {
                    "title": str(raw.get("title") or ""),
                    "url": str(raw.get("url") or ""),
                    "product": str(raw.get("product") or ""),
                    "query": str(raw.get("query") or ""),
                    "reason": authority_note,
                }
            )
            continue
        source_risk = (
            "Demo fixture; verify with live provider before production use."
            if fixture_mode
            else authority_note
        )
        state.sources.append(
            Source(
                task_id=state.task.task_id,
                title=raw["title"],
                url=raw["url"],
                source_type=source_type,
                product=raw["product"],
                query=raw.get("query", ""),
                confidence=confidence,
                risk=source_risk,
                content=raw["content"],
            )
        )
    _trace(
        state,
        "SourceNormalizer",
        "source_normalizer",
        "sources_normalized",
        f"Normalized {len(state.sources)} unique source(s); rejected {rejected} irrelevant search result(s).",
        output_payload={"source_count": len(state.sources), "rejected_irrelevant_count": rejected, "rejected_sources": rejected_sources[:20]},
    )
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
                interaction_path=_interaction_steps(raw),
                confidence=source.confidence,
                risk=source.risk,
            )
        )
    _trace(state, "EvidenceExtractor", "evidence_extractor", "evidence_extracted", f"Extracted {len(state.evidence)} evidence item(s).")
    return state


def _interaction_steps(raw: dict | None) -> list[str]:
    if not raw:
        return []
    value = raw.get("interaction_steps") or raw.get("interaction_path") or []
    if isinstance(value, str):
        return [item.strip() for item in value.replace("->", ">").split(">") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _source_interaction_evidence(state: GraphState, source: Source) -> Evidence | None:
    return next(
        (
            evidence
            for evidence in state.evidence
            if evidence.source_id == source.source_id
            and evidence.product == source.product
            and evidence.interaction_path
            and evidence.evidence_type != "browser_interaction"
        ),
        None,
    )


def _is_real_browser_walkthrough_source(source: Source | None) -> bool:
    return bool(source and source.source_type in {"browser_walkthrough", "official_browser_walkthrough"})


def _interaction_verification_method(evidence: Evidence, source_by_id: dict[str, Source]) -> str:
    source = source_by_id.get(evidence.source_id)
    if _is_real_browser_walkthrough_source(source):
        return "browser_walkthrough"
    if source and source.source_type in {"fixture_walkthrough", "official_fixture_walkthrough"}:
        return "fixture_walkthrough"
    return "source_inference"


def interaction_node(state: GraphState) -> GraphState:
    if state.task.config.domain != "ai_tools":
        _trace(state, "InteractionAgent", "interaction", "interaction_skipped", "Browser walkthrough evidence is required only for AI product analysis.")
        return state

    raw_by_url = {raw.get("url"): raw for raw in state.raw_sources}
    existing_keys = {
        (item.product.casefold(), item.evidence_type, " > ".join(item.interaction_path).casefold())
        for item in state.evidence
    }
    created = 0
    skipped_products: set[str] = set()

    for source in list(state.sources):
        raw = raw_by_url.get(source.url)
        steps = _interaction_steps(raw)
        fallback_evidence = None
        if not steps:
            fallback_evidence = _source_interaction_evidence(state, source)
            steps = list(fallback_evidence.interaction_path) if fallback_evidence else []
        if not steps:
            skipped_products.add(source.product)
            continue
        key = (source.product.casefold(), "browser_interaction", " > ".join(steps).casefold())
        if key in existing_keys:
            continue
        source_type = "fixture_walkthrough"
        walkthrough_source = Source(
            task_id=state.task.task_id,
            title=f"{source.product} fixture walkthrough",
            url=source.url,
            source_type=source_type,
            product=source.product,
            query=f"fixture walkthrough from {source.query}",
            confidence=source.confidence,
            risk="Structured walkthrough derived from source fields; not a live Browser/Playwright observation.",
            content="Clicked path: " + " > ".join(steps),
        )
        state.sources.append(walkthrough_source)
        state.evidence.append(
            Evidence(
                task_id=state.task.task_id,
                source_id=walkthrough_source.source_id,
                product=source.product,
                evidence_type="browser_interaction",
                summary=str(
                    (raw or {}).get("interaction_summary")
                    or (fallback_evidence.summary if fallback_evidence else "")
                    or f"{source.product} workflow was observed through a click path: {' > '.join(steps)}."
                ),
                quote_or_locator=" > ".join(steps),
                interaction_path=steps,
                confidence=source.confidence,
                risk=walkthrough_source.risk,
            )
        )
        existing_keys.add(key)
        created += 1

    state.tool_calls.append(
        ToolCall(
            task_id=state.task.task_id,
            agent="InteractionAgent",
            tool="StructuredWalkthroughExtractor",
            operation="fixture_walkthrough",
            status="success" if created else "skipped",
            results_summary=f"Created {created} structured interaction-path evidence item(s).",
            input_summary="Read interaction_steps attached to official product sources.",
            output_summary=f"{created} browser_interaction evidence item(s) with fixture_walkthrough provenance.",
            provider_mode="fixture" if created else "",
        )
    )
    _trace(
        state,
        "InteractionAgent",
        "interaction",
        "fixture_walkthrough_completed" if created else "browser_walkthrough_missing",
        (
            f"Created {created} structured interaction-path evidence item(s) from fixture/source fields; not counted as live browser verification."
            if created
            else "No explicit browser click-path observations were available; feature tree must mark interaction coverage as unverified."
        ),
        input_summary="InteractionAgent requires explicit click paths, not prose-only docs.",
        output_summary=f"{created} fixture_walkthrough evidence item(s); skipped products: {', '.join(sorted(skipped_products)) or '-'}",
        output_payload={"created_count": created, "skipped_products": sorted(skipped_products), "provenance": "fixture_walkthrough"},
    )
    return state


def social_listening_node(state: GraphState) -> GraphState:
    cfg = state.task.config.social_listening
    if not cfg.enabled:
        _trace(state, "SocialListeningAgent", "social_listening", "social_listening_skipped", "Social listening is disabled for this task.")
        return state

    created = 0
    manual_created = _add_manual_xhs_summary(state)
    created += manual_created
    for platform in cfg.platforms:
        if not platform.enabled:
            continue
        if platform.platform == "xiaohongshu":
            created += _collect_xiaohongshu_platform(state, platform)
        else:
            _mark_social_platform_unavailable(state, platform, f"{platform.platform} 采集暂未接入；不会阻塞小红书舆情分析。")

    _trace(
        state,
        "SocialListeningAgent",
        "social_listening",
        "social_listening_completed",
        f"Created {created} social listening raw source(s); collected {len(state.social_posts)} social post(s).",
        output_payload={"raw_social_sources": created, "social_posts": len(state.social_posts)},
    )
    return state


def _add_manual_xhs_summary(state: GraphState) -> int:
    summary = _manual_xhs_summary_text(state)
    if not summary:
        return 0
    if any(
        source.get("source_type") == "social_xiaohongshu_manual" and " ".join(str(source.get("content") or "").split()) == summary
        for source in state.raw_sources
        if isinstance(source, dict)
    ):
        return 0
    cfg = state.task.config.social_listening
    title = f"小红书点点 AI 舆情总结 - {state.task.config.target_product}"
    url = cfg.manual_source_urls[0] if cfg.manual_source_urls else f"manual://xiaohongshu/{state.task.task_id}"
    state.raw_sources.append(
        {
            "title": title,
            "url": url,
            "source_type": "social_xiaohongshu_manual",
            "product": state.task.config.target_product,
            "evidence_type": "social_sentiment",
            "summary": summary[:360],
            "locator": "用户粘贴的小红书点点 AI 总结",
            "content": summary,
            "query": "manual_xhs_summary",
            "confidence": "medium",
        }
    )
    state.social_insights.append(
        SocialInsight(
            platform="xiaohongshu",
            summary=summary[:420],
            themes=_extract_social_themes(summary),
            pain_points=_extract_social_phrases(summary, ["痛", "吐槽", "问题", "难", "贵", "慢", "坑"]),
            purchase_signals=_extract_social_phrases(summary, ["买", "入手", "种草", "推荐", "值得", "付费"]),
            churn_or_risk_signals=_extract_social_phrases(summary, ["退", "弃用", "卸载", "避雷", "不推荐", "踩雷"]),
            competitor_mentions=_competitor_mentions(state, summary),
            status="manual",
            note="来自用户粘贴的小红书点点 AI 总结。",
        )
    )
    return 1


def _manual_xhs_summary_text(state: GraphState) -> str:
    cfg = state.task.config.social_listening
    return " ".join(str(cfg.manual_xhs_summary or "").split())


def _collect_xiaohongshu_platform(state: GraphState, platform: SocialPlatformConfig) -> int:
    client = XhsMcpClient()
    try:
        status = client.check_login_status()
    except ProviderRequestError as exc:
        _add_social_ticket(state, "xiaohongshu", "XHS_MCP_UNAVAILABLE", f"无法连接 xiaohongshu-mcp：{exc}", "启动 xiaohongshu-mcp 后重试。")
        return 0

    if not _is_xhs_logged_in(status):
        _add_social_ticket(
            state,
            "xiaohongshu",
            "XHS_LOGIN_REQUIRED",
            "xiaohongshu-mcp 已连接但尚未登录。",
            "进入小红书登录引导页扫码登录，然后重新运行舆情采集。",
        )
        _trace(
            state,
            "SocialListeningAgent",
            "social_listening",
            "xhs_login_required",
            "xiaohongshu-mcp requires login before search_feeds/get_feed_detail can be used.",
            output_payload={"status": status},
            provider=client.provider_name,
        )
        return 0

    keywords = [item.strip() for item in platform.keywords if item.strip()]
    if not keywords:
        keywords = [state.task.config.target_product, *state.task.config.competitors]

    created = 0
    for keyword in keywords:
        remaining_posts = max(0, platform.max_posts_per_keyword - len([post for post in state.social_posts if post.platform == "xiaohongshu"]))
        if remaining_posts <= 0:
            break
        try:
            search_response = client.search_feeds(keyword, filters=_xhs_filters(platform))
        except ProviderRequestError as exc:
            _add_social_ticket(state, "xiaohongshu", "XHS_SEARCH_FAILED", f"小红书关键词 {keyword} 搜索失败：{exc}", "检查 MCP 登录状态和小红书搜索接口返回后重试。")
            continue
        search_error = _xhs_response_error_message(search_response)
        if search_error:
            code = "XHS_LOGIN_REQUIRED" if _xhs_error_requires_login(search_error) else "XHS_SEARCH_FAILED"
            action = "重新扫码登录小红书，确认 MCP 状态显示为已登录后再运行。"
            if code == "XHS_SEARCH_FAILED":
                action = "检查 MCP 登录状态、关键词或小红书接口返回后重试。"
            _add_social_ticket(state, "xiaohongshu", code, f"小红书关键词 {keyword} 搜索失败：{search_error}", action)
            _trace(
                state,
                "SocialListeningAgent",
                "social_listening",
                "xhs_search_failed",
                f"xiaohongshu-mcp returned an unusable search response for {keyword}: {search_error}",
                output_payload={"keyword": keyword, "response": search_response},
                provider=client.provider_name,
            )
            continue
        candidate_limit = max(remaining_posts, min(max(platform.max_posts_per_keyword * 4, platform.max_posts_per_keyword), 50))
        posts = _xhs_posts_from_search(search_response, keyword, candidate_limit)
        if not posts:
            _trace(
                state,
                "SocialListeningAgent",
                "social_listening",
                "xhs_search_empty",
                f"xiaohongshu-mcp returned 0 parsed feeds for {keyword}.",
                output_payload={"keyword": keyword, "response": search_response},
                provider=client.provider_name,
            )
        for post in posts:
            detail_response: dict = {}
            comment_response: dict = {}
            if post.post_id and post.xsec_token:
                try:
                    detail_response = client.get_feed_detail(post.post_id, post.xsec_token, load_all_comments=platform.fetch_comments)
                except ProviderRequestError as exc:
                    _trace(
                        state,
                        "SocialListeningAgent",
                        "social_listening",
                        "xhs_detail_failed",
                        f"Failed to load detail for {post.post_id}: {exc}",
                        provider=client.provider_name,
                    )
            if platform.fetch_comments and post.post_id:
                try:
                    comment_response = client.get_feed_comments(post.post_id, limit=platform.max_comments_per_post, xsec_token=post.xsec_token)
                except ProviderRequestError as exc:
                    _trace(
                        state,
                        "SocialListeningAgent",
                        "social_listening",
                        "xhs_comments_failed",
                        f"Failed to load comments for {post.post_id}: {exc}",
                        provider=client.provider_name,
                    )
            post = _merge_xhs_detail(post, detail_response, platform.max_comments_per_post)
            comment_error = _xhs_response_error_message(comment_response) if comment_response else ""
            if comment_error:
                _trace(
                    state,
                    "SocialListeningAgent",
                    "social_listening",
                    "xhs_comments_failed",
                    f"xiaohongshu-mcp returned an unusable comment response for {post.post_id}: {comment_error}",
                    output_payload={"post_id": post.post_id, "response": comment_response},
                    provider=client.provider_name,
                )
            else:
                post = _merge_xhs_detail(post, comment_response, platform.max_comments_per_post)
            if platform.fetch_comments and len(post.comments) < platform.max_comments_per_post:
                _trace(
                    state,
                    "SocialListeningAgent",
                    "social_listening",
                    "xhs_comments_insufficient",
                    f"Skipped {post.post_id}: collected {len(post.comments)}/{platform.max_comments_per_post} comments.",
                    output_payload={"post_id": post.post_id, "comments": len(post.comments), "required": platform.max_comments_per_post},
                    provider=client.provider_name,
                )
                continue
            if any(existing.platform == "xiaohongshu" and existing.post_id == post.post_id for existing in state.social_posts):
                continue
            state.social_posts.append(post)
            state.raw_sources.append(_raw_source_from_social_post(state, post, keyword))
            created += 1
            if len([item for item in state.social_posts if item.platform == "xiaohongshu"]) >= platform.max_posts_per_keyword:
                break
    return created


def _xhs_filters(platform: SocialPlatformConfig) -> dict[str, str]:
    return {
        "sort_by": platform.sort_by or "综合",
        "note_type": platform.note_type or "不限",
        "publish_time": platform.publish_time or "一周内",
        "limit": str(min(max((platform.max_posts_per_keyword or 15) * 4, platform.max_posts_per_keyword or 15), 50)),
    }


def _is_xhs_logged_in(response: dict) -> bool:
    explicit = _find_login_bool(response)
    if explicit is not None:
        return explicit
    text = json.dumps(response, ensure_ascii=False).casefold()
    if any(term in text for term in _XHS_LOGIN_NEGATIVE_TERMS):
        return False
    if any(term in text for term in ["登录成功", "login successful", "cookies saved", "logged in as", "当前登录"]):
        return True
    return any(
        term in text
        for term in [
            "已登录",
            "logged_in",
            "logged in",
            "login: true",
            '"login": true',
            '"success": true',
        ]
    )


_XHS_LOGIN_NEGATIVE_TERMS = [
    "未登录",
    "not logged",
    "login_required",
    "请登录",
    "无登录信息",
    "登录信息为空",
    "没有权限访问",
    "use get_login_qrcode",
    "login check failed",
]


def _xhs_response_error_message(response: dict) -> str:
    for key in ["error", "message", "msg", "status"]:
        value = response.get(key)
        if not value:
            continue
        text = str(value)
        if _xhs_error_requires_login(text) or "search failed" in text.casefold() or "失败" in text:
            return text[:500]
    return ""


def _xhs_error_requires_login(text: str) -> bool:
    normalized = text.casefold()
    return any(term in normalized for term in _XHS_LOGIN_NEGATIVE_TERMS)


def _find_login_bool(value: object) -> bool | None:
    if isinstance(value, dict):
        for key in ["logged_in", "is_logged_in", "login", "isLogin", "success"]:
            if isinstance(value.get(key), bool):
                return bool(value[key])
        for item in value.values():
            found = _find_login_bool(item)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_login_bool(item)
            if found is not None:
                return found
    return None


def _xhs_posts_from_search(response: dict, keyword: str, limit: int) -> list[SocialPost]:
    items = _candidate_items(response)
    posts: list[SocialPost] = []
    for index, item in enumerate(items[: max(1, min(limit, 50))], start=1):
        if not isinstance(item, dict):
            continue
        post_id = _first_text(item, ["feed_id", "note_id", "id", "noteId", "note_id_str", "note_card.note_id"]) or f"{keyword}_{index}"
        xsec_token = _xhs_xsec_token(item)
        title = _first_text(item, ["title", "display_title", "name", "note_card.display_title"]) or f"{keyword} 小红书笔记 {index}"
        content = _first_text(item, ["desc", "description", "content", "summary", "note_card.desc"])
        author = _first_text(item, ["author", "nickname", "user", "user.nickname", "user.name", "note_card.user.nickname"])
        url = _first_text(item, ["url", "link", "share_url", "shareLink"]) or f"https://www.xiaohongshu.com/explore/{post_id}"
        posts.append(
            SocialPost(
                post_id=str(post_id),
                platform="xiaohongshu",
                title=title,
                content=content,
                author=author,
                url=url,
                xsec_token=xsec_token,
                like_count=_first_int(item, ["like_count", "liked_count", "likedCount", "likes", "interact_info.liked_count", "note_card.interact_info.liked_count"]),
                collect_count=_first_int(item, ["collect_count", "collected_count", "collectedCount", "interact_info.collected_count"]),
                share_count=_first_int(item, ["share_count", "shareCount", "interact_info.share_count"]),
                comment_count=_first_int(item, ["comment_count", "comments_count", "commentCount", "interact_info.comment_count"]),
            )
        )
    return posts


def _merge_xhs_detail(post: SocialPost, response: dict, max_comments: int) -> SocialPost:
    if not response:
        return post
    text_source = _first_dict(response, ["data", "note", "detail", "feed"]) or response
    comments = []
    for item in _candidate_comments(response)[: max(0, min(max_comments, 100))]:
        if not isinstance(item, dict):
            continue
        content = _first_text(item, ["content", "text", "desc"])
        if not content:
            continue
        raw_comment_id = _first_text(item, ["id", "comment_id", "commentId"])
        comment_id = f"{post.post_id}_{raw_comment_id}" if raw_comment_id and not raw_comment_id.startswith(f"{post.post_id}_") else raw_comment_id
        comments.append(
            SocialComment(
                comment_id=comment_id or f"{post.post_id}_c{len(comments) + 1}",
                author=_first_text(item, ["nickname", "author", "user.nickname", "user.name", "user_info.nickname", "user_info.name"]),
                content=content,
                like_count=_first_int(item, ["like_count", "liked_count", "likeCount", "like_count"]),
                sentiment=_classify_sentiment(content),
            )
        )
    return post.model_copy(
        update={
            "title": _first_text(text_source, ["title", "display_title"]) or post.title,
            "content": _first_text(text_source, ["desc", "description", "content"]) or post.content,
            "like_count": _first_int(text_source, ["like_count", "liked_count", "likedCount", "interact_info.liked_count"]) or post.like_count,
            "collect_count": _first_int(text_source, ["collect_count", "collected_count", "collectedCount", "interact_info.collected_count"]) or post.collect_count,
            "share_count": _first_int(text_source, ["share_count", "shareCount", "interact_info.share_count"]) or post.share_count,
            "comment_count": _first_int(text_source, ["comment_count", "comments_count", "commentCount", "interact_info.comment_count"]) or post.comment_count,
            "comments": comments or post.comments,
        }
    )


def _raw_source_from_social_post(state: GraphState, post: SocialPost, keyword: str) -> dict:
    comments_text = "；".join(comment.content for comment in post.comments[:5])
    summary = _social_post_summary(post)
    content = "\n".join(
        item
        for item in [
            post.title,
            post.content,
            f"互动：点赞 {post.like_count}，收藏 {post.collect_count}，评论 {post.comment_count}，分享 {post.share_count}",
            f"代表评论：{comments_text}" if comments_text else "",
        ]
        if item
    )
    return {
        "title": f"小红书舆情：{post.title}",
        "url": post.url or f"https://www.xiaohongshu.com/explore/{post.post_id}",
        "source_type": "social_xiaohongshu",
        "product": state.task.config.target_product,
        "evidence_type": "social_sentiment",
        "summary": summary,
        "locator": f"keyword={keyword}; feed_id={post.post_id}",
        "content": content,
        "query": f"xiaohongshu:{keyword}",
        "confidence": "medium",
    }


def _social_post_summary(post: SocialPost) -> str:
    fragments = [post.title]
    if post.content:
        fragments.append(post.content[:180])
    if post.comments:
        fragments.append("评论信号：" + "；".join(comment.content[:80] for comment in post.comments[:3]))
    fragments.append(f"互动数据：点赞 {post.like_count}，收藏 {post.collect_count}，评论 {post.comment_count}")
    return "。".join(item for item in fragments if item).strip("。") + "。"


def _candidate_items(value: object) -> list:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return []
    for key in ["items", "feeds", "notes", "list", "data", "results", "result", "feed_list", "note_list"]:
        item = value.get(key)
        if isinstance(item, list):
            return item
        if isinstance(item, dict):
            nested = _candidate_items(item)
            if nested:
                return nested
    for item in value.values():
        if isinstance(item, dict):
            nested = _candidate_items(item)
            if nested:
                return nested
    return []


def _candidate_comments(value: object) -> list:
    if isinstance(value, list):
        comments = []
        for item in value:
            if isinstance(item, dict):
                comments.append(item)
                sub_comments = item.get("sub_comments")
                if isinstance(sub_comments, list):
                    comments.extend(comment for comment in sub_comments if isinstance(comment, dict))
        return comments
    if not isinstance(value, dict):
        return []
    for key in ["comments", "comment_list", "commentList"]:
        item = value.get(key)
        if isinstance(item, list):
            return _candidate_comments(item)
        if isinstance(item, dict):
            nested = _candidate_comments(item)
            if nested:
                return nested
    for item in value.values():
        if isinstance(item, dict):
            nested = _candidate_comments(item)
            if nested:
                return nested
    return []


def _first_dict(value: dict, paths: list[str]) -> dict:
    for path in paths:
        current = _path_value(value, path)
        if isinstance(current, dict):
            return current
    return {}


def _first_text(value: dict, paths: list[str]) -> str:
    for path in paths:
        current = _path_value(value, path)
        if current not in (None, ""):
            return str(current).strip()
    return ""


def _xhs_xsec_token(item: dict) -> str:
    direct = _first_text(item, ["xsec_token", "xsecToken", "xsec"])
    if direct:
        return direct
    for path in ["url", "link", "share_url", "shareLink"]:
        current = _first_text(item, [path])
        if not current or "xsec_token=" not in current:
            continue
        values = parse_qs(urlparse(current).query).get("xsec_token")
        if values and values[0]:
            return values[0].strip()
    return ""


def _first_int(value: dict, paths: list[str]) -> int:
    for path in paths:
        current = _path_value(value, path)
        if isinstance(current, int):
            return current
        if isinstance(current, str):
            digits = "".join(char for char in current if char.isdigit())
            if digits:
                return int(digits)
    return 0


def _path_value(value: dict, path: str):
    current = value
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _classify_sentiment(text: str) -> str:
    negative = ["差", "贵", "坑", "难用", "失望", "不推荐", "踩雷", "吐槽", "慢", "崩"]
    positive = ["好用", "推荐", "喜欢", "值得", "种草", "满意", "省心", "高效", "惊喜"]
    if any(term in text for term in negative):
        return "negative"
    if any(term in text for term in positive):
        return "positive"
    return "neutral"


def _mark_social_platform_unavailable(state: GraphState, platform: SocialPlatformConfig, reason: str) -> None:
    _add_social_ticket(state, platform.platform, "SOCIAL_PLATFORM_UNAVAILABLE", reason, "先完成小红书舆情分析；该平台后续接入采集 Provider。")
    state.social_insights.append(
        SocialInsight(
            platform=platform.platform,
            summary=reason,
            status="unavailable",
            note=reason,
        )
    )


def _add_social_ticket(state: GraphState, platform: str, code: str, reason: str, action: str) -> None:
    existing = any(ticket.reason == reason and ticket.target_node == "SocialListeningAgent" for ticket in state.review_tickets)
    if existing:
        return
    state.review_tickets.append(
        ReviewTicket(
            task_id=state.task.task_id,
            reviewer="SocialListeningAgent",
            source_node="social_listening",
            target_node="SocialListeningAgent",
            reason=reason,
            required_action=action,
            severity="critical" if platform == "xiaohongshu" and code == "XHS_LOGIN_REQUIRED" else "medium",
            product=state.task.config.target_product,
            missing_evidence_type="social_sentiment",
            preferred_source_type=f"social_{platform}",
            source_query_hint=code,
        )
    )


def _build_social_insights_from_evidence(state: GraphState) -> list[SocialInsight]:
    existing = [
        insight
        for insight in state.social_insights
        if not (insight.platform == "xiaohongshu" and insight.status == "collected")
    ]
    if state.social_posts:
        evidence_ids = [item.evidence_id for item in state.evidence if item.evidence_type == "social_sentiment" and item.status == "active"]
        text = "\n".join([post.title + "\n" + post.content + "\n" + " ".join(comment.content for comment in post.comments) for post in state.social_posts])
        comments = [comment for post in state.social_posts for comment in post.comments]
        sentiment = SentimentSummary(
            positive_count=len([item for item in comments if item.sentiment == "positive"]),
            neutral_count=len([item for item in comments if item.sentiment == "neutral"]),
            negative_count=len([item for item in comments if item.sentiment == "negative"]),
            overall=_overall_sentiment(comments),
            evidence_ids=evidence_ids,
        )
        findings = _synthesize_social_findings(state, state.social_posts, comments)
        existing.append(
            SocialInsight(
                platform="xiaohongshu",
                summary=f"小红书共采集 {len(state.social_posts)} 条笔记、{len(comments)} 条评论；主要反馈集中在：{'、'.join(_extract_social_themes(text)[:4]) or '产品体验与购买决策'}。",
                findings=findings,
                themes=_extract_social_themes(text),
                pain_points=_extract_social_phrases(text, ["痛", "吐槽", "问题", "难", "贵", "慢", "坑"]),
                purchase_signals=_extract_social_phrases(text, ["买", "入手", "种草", "推荐", "值得", "付费", "好用", "感谢", "学到", "爽"]),
                churn_or_risk_signals=_extract_social_phrases(text, ["退", "弃用", "卸载", "避雷", "不推荐", "踩雷"]),
                competitor_mentions=_competitor_mentions(state, text),
                sentiment=sentiment,
                post_ids=[post.post_id for post in state.social_posts],
                evidence_ids=evidence_ids,
                status="collected",
            )
        )
    return existing


def _synthesize_social_findings(state: GraphState, posts: list[SocialPost], comments: list[SocialComment]) -> list[SocialInsightFinding]:
    if not comments:
        return []
    known_comment_ids = {comment.comment_id for comment in comments if comment.comment_id}
    payload = {
        "task": {
            "target_product": state.task.config.target_product,
            "competitors": state.task.config.competitors,
        },
        "posts": [
            {
                "post_id": post.post_id,
                "title": post.title,
                "content": post.content[:220],
                "comment_count": post.comment_count,
                "sample_comments": [
                    {
                        "comment_id": comment.comment_id,
                        "content": comment.content[:180],
                        "sentiment": comment.sentiment,
                        "like_count": comment.like_count,
                    }
                    for comment in post.comments[:12]
                ],
            }
            for post in posts[:15]
        ],
        "comment_limit": min(len(comments), 180),
    }
    try:
        providers = build_provider_bundle()
        response = _complete_with_skill(providers.llm, "social_insight_synthesis", payload)
        findings = _findings_from_llm_response(response, known_comment_ids)
        if findings:
            return findings
    except Exception:
        pass
    return _fallback_social_findings(comments)


def _findings_from_llm_response(response: dict, known_comment_ids: set[str]) -> list[SocialInsightFinding]:
    raw_findings = response.get("findings")
    if not isinstance(raw_findings, list):
        return []
    findings: list[SocialInsightFinding] = []
    for item in raw_findings[:8]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if not title or not summary:
            continue
        refs = [str(ref).strip() for ref in item.get("comment_refs") or [] if str(ref).strip() in known_comment_ids]
        findings.append(
            SocialInsightFinding(
                category=_normalize_finding_category(str(item.get("category") or "")),
                title=title[:60],
                summary=summary[:240],
                comment_refs=refs[:5],
            )
        )
    return findings


def _fallback_social_findings(comments: list[SocialComment]) -> list[SocialInsightFinding]:
    buckets = [
        (
            "positive",
            "用户认可效率与教程价值",
            "正向反馈主要来自“好用、感谢、学到、爽、推荐”等表达，说明内容或产品在降低上手成本、提升效率方面有吸引力。",
            ["好用", "感谢", "学到", "爽", "推荐", "值得", "来了", "可以"],
        ),
        (
            "pain",
            "价格和 Token 成本仍是主要顾虑",
            "负向反馈集中在“贵、烧 token、充值、成本”等关键词，用户会先评估持续使用成本再决定是否采用。",
            ["贵", "token", "充值", "价格", "成本", "烧"],
        ),
        (
            "pain",
            "配置和连接问题影响新手上手",
            "评论中出现配置、登录、连接、报错、no content 等问题，说明新手路径需要更明确的检查清单和异常提示。",
            ["配置", "登录", "连接", "报错", "no content", "卡", "修复"],
        ),
        (
            "request",
            "用户主动提出适配和功能扩展需求",
            "部分用户在评论中直接提出适配 Codex、恢复会话、CLI 等需求，这些是后续产品集成或教程补充的机会点。",
            ["适配", "codex", "resume", "cli", "求问", "怎么", "必须"],
        ),
    ]
    findings: list[SocialInsightFinding] = []
    used_refs: set[str] = set()
    for category, title, summary, terms in buckets:
        refs = []
        for comment in comments:
            if comment.comment_id in used_refs:
                continue
            if any(term.casefold() in comment.content.casefold() for term in terms):
                refs.append(comment.comment_id)
                used_refs.add(comment.comment_id)
            if len(refs) >= 4:
                break
        if refs:
            findings.append(SocialInsightFinding(category=category, title=title, summary=summary, comment_refs=refs))
    if not findings:
        findings.append(
            SocialInsightFinding(
                category="neutral",
                title="评论整体偏信息交流",
                summary="当前评论更偏向问答、补充经验和使用细节交流，建议结合更多样本继续观察明确情绪趋势。",
                comment_refs=[comment.comment_id for comment in comments[:4] if comment.comment_id],
            )
        )
    return findings


def _normalize_finding_category(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"positive", "pain", "risk", "request", "question", "neutral"}:
        return normalized
    if normalized in {"good", "pro", "strength", "亮点", "好评"}:
        return "positive"
    if normalized in {"bad", "con", "issue", "痛点", "问题"}:
        return "pain"
    return "neutral"


def _overall_sentiment(comments: list[SocialComment]) -> str:
    if not comments:
        return "neutral"
    counts = {item: len([comment for comment in comments if comment.sentiment == item]) for item in ["positive", "neutral", "negative"]}
    if counts["positive"] and counts["negative"]:
        return "mixed"
    return max(counts, key=counts.get)


def _extract_social_themes(text: str) -> list[str]:
    candidates = [
        ("价格/性价比", ["贵", "价格", "便宜", "性价比", "付费"]),
        ("功能体验", ["功能", "体验", "好用", "难用", "流程"]),
        ("效果与效率", ["效果", "效率", "省时", "快", "慢"]),
        ("购买决策", ["种草", "入手", "推荐", "避雷", "不推荐"]),
        ("服务与稳定性", ["客服", "售后", "崩", "稳定", "bug"]),
    ]
    themes = [name for name, terms in candidates if any(term in text for term in terms)]
    return themes[:6]


def _extract_social_phrases(text: str, triggers: list[str]) -> list[str]:
    sentences = [item.strip(" 。！？!?；;\n\t") for item in text.replace("\n", "。").split("。")]
    phrases = []
    for sentence in sentences:
        if sentence and any(term in sentence for term in triggers):
            phrases.append(sentence[:90])
        if len(phrases) >= 6:
            break
    return phrases


def _competitor_mentions(state: GraphState, text: str) -> list[str]:
    products = [state.task.config.target_product, *state.task.config.competitors]
    return [product for product in products if product and product.casefold() in text.casefold()]


def analyst_node(state: GraphState) -> GraphState:
    state.claims = []
    state.social_insights = _build_social_insights_from_evidence(state)
    by_product: dict[str, list[Evidence]] = defaultdict(list)
    for item in state.evidence:
        by_product[item.product].append(item)

    products = [state.task.config.target_product, *state.task.config.competitors]
    for product in products:
        evidence_items = by_product.get(product, [])
        evidence_types = ["positioning", "pricing", "feature", "target_user", "security", "third_party_context", "contradiction"]
        if state.task.config.social_listening.enabled:
            evidence_types.append("social_sentiment")
        if state.task.config.domain == "ai_tools":
            evidence_types.insert(3, "browser_interaction")
        for evidence_type in evidence_types:
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
                        confidence="high" if evidence_type != "browser_interaction" else "medium",
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
    social_evidence = [item for item in state.evidence if item.evidence_type == "social_sentiment"]
    feature_evidence = [item for item in state.evidence if item.evidence_type == "feature"]
    interaction_evidence = [item for item in state.evidence if item.evidence_type == "browser_interaction"]
    security_evidence = [item for item in state.evidence if item.evidence_type == "security"]
    third_party_evidence = [item for item in state.evidence if item.evidence_type == "third_party_context"]
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
                claim="Feature coverage differs enough to require a user-journey comparison instead of a flat checklist.",
                claim_type="comparative_feature",
                supporting_evidence=[item.evidence_id for item in feature_evidence],
                confidence="medium",
                verified_status="passed",
                included_in_report=True,
            )
        )
    if len(interaction_evidence) >= 2:
        state.claims.append(
            Claim(
                task_id=state.task.task_id,
                product="Cross-product",
                claim="Browser-observed workflow paths are available for multiple products, so the feature tree can separate real interaction coverage from source-only feature claims.",
                claim_type="comparative_browser_interaction",
                supporting_evidence=[item.evidence_id for item in interaction_evidence],
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
    if third_party_evidence:
        state.claims.append(
            Claim(
                task_id=state.task.task_id,
                product="External signal",
                claim="Third-party evidence is available and should be used to cross-check official positioning rather than simply repeat vendor messaging.",
                claim_type="third_party_context",
                supporting_evidence=[item.evidence_id for item in third_party_evidence],
                confidence="medium",
                verified_status="passed",
                included_in_report=True,
            )
        )
    if social_evidence:
        state.claims.append(
            Claim(
                task_id=state.task.task_id,
                product="Social listening",
                claim="小红书舆情证据已纳入分析；社媒结论应与产品事实证据分开解读。",
                claim_type="social_sentiment",
                supporting_evidence=[item.evidence_id for item in social_evidence],
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
    skill_context = _skill_context("competitor_analysis")
    skill_fields = skill_trace_fields(skill_context)
    provider_name = providers.llm.provider_name
    started = time.perf_counter()
    try:
        response = _complete_with_skill(providers.llm, "claim_enrichment", payload, skill_context.prompt if skill_context else "")
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
                **skill_fields,
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
            skill_fields=skill_fields,
        )
        if not providers.allow_provider_fallback:
            return 0
        fallback = MockLLMProvider()
        started = time.perf_counter()
        response = _complete_with_skill(fallback, "claim_enrichment", payload, skill_context.prompt if skill_context else "")
        latency_ms = int((time.perf_counter() - started) * 1000)
        provider_name = fallback.provider_name
        summary = "LLM request failed; MockLLMProvider generated fallback claim enrichment."

    claims = _validated_enriched_claims(response, state)
    token_count = _provider_token_count(response, _estimate_tokens(payload, response))
    provider_request_id = _provider_request_id(response, provider_name)
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
                provider_request_id=provider_request_id,
                provider_mode=providers.llm_mode,
                **skill_fields,
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
            provider_request_id=provider_request_id,
            skill_fields=skill_fields,
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
            provider_request_id=provider_request_id,
            provider_mode=providers.llm_mode,
            **skill_fields,
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
        provider_request_id=provider_request_id,
        skill_fields=skill_fields,
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
    resolve_review_ticket_improvements(state)
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
                    "required_evidence": ["pricing", "feature", "browser_interaction", "target_user", "security", "contradiction"],
                    "products": [state.task.config.target_product, *state.task.config.competitors],
                },
                output_payload={"created_ticket_count": created},
            )
            return state
        source_mix_created = _create_source_mix_review_tickets(state)
        if source_mix_created:
            _trace(
                state,
                "CriticAgent",
                "critic",
                "source_mix_review_tickets_created",
                f"Created {source_mix_created} source-mix Review Ticket(s) to reduce official-source overdependence.",
                [ticket.ticket_id for ticket in state.review_tickets[-source_mix_created:]],
                input_summary="Source mix check for official vs third-party support.",
                output_summary=f"{source_mix_created} ticket(s) created.",
                input_payload=_source_mix_summary(state),
                output_payload={"created_ticket_count": source_mix_created},
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
        ("pricing", "official_or_independent", "high", "Search pricing pages, reviews, or comparison coverage; keep pricing model uncertain if terms remain inconsistent.", "ResearchAgent"),
        ("feature", "official_or_independent", "medium", "Search product docs, walkthroughs, or third-party reviews before finalizing the source-inferred journey context.", "ResearchAgent"),
        ("target_user", "official_or_independent", "medium", "Search customer stories, community discussions, or persona material before finalizing personas.", "ResearchAgent"),
        ("security", "official_or_independent", "medium", "Search security, privacy, incident, or compliance coverage before scoring enterprise adoption risk.", "ResearchAgent"),
    ]
    if state.task.config.domain == "ai_tools":
        rules.insert(
            2,
            (
                "browser_interaction",
                "browser_walkthrough",
                "high",
                "Use Browser or Playwright to click through the product UI and record a real interaction path before treating the feature tree as verified.",
                "InteractionAgent",
            ),
        )
    created = 0
    for evidence_type, source_type, severity, action, target_node in rules:
        for product in products:
            key = (product.casefold(), evidence_type)
            if key in existing or evidence_type in evidence_by_product.get(product, set()):
                continue
            ticket = ReviewTicket(
                task_id=state.task.task_id,
                reviewer="CriticAgent",
                target_node=target_node,
                reason=f"{product} lacks reviewed {evidence_type.replace('_', ' ')} evidence.",
                required_action=action,
                severity=severity,
                product=product,
                missing_evidence_type=evidence_type,
                preferred_source_type=source_type,
                source_query_hint=_supplemental_query_hint(_query_product_name(product), evidence_type),
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


def _create_source_mix_review_tickets(state: GraphState) -> int:
    mix = _source_mix_summary(state)
    if float(mix["third_party_ratio"]) >= THIRD_PARTY_SOURCE_RATIO_TARGET:
        return 0
    if float(mix["official_ratio"]) < OFFICIAL_SOURCE_RATIO_REVIEW_THRESHOLD and int(mix["third_party_count"]) > 0:
        return 0

    products = [state.task.config.target_product, *state.task.config.competitors]
    existing = {
        (ticket.product.casefold(), ticket.missing_evidence_type.casefold(), ticket.target_node.casefold())
        for ticket in state.review_tickets
    }
    third_party_products = {
        source.product.casefold()
        for source in state.sources
        if _is_third_party_source(source)
    }
    created = 0
    for product in products:
        key = (product.casefold(), "third_party_context", "researchagent")
        if key in existing or product.casefold() in third_party_products:
            continue
        state.review_tickets.append(
            ReviewTicket(
                task_id=state.task.task_id,
                reviewer="CriticAgent",
                target_node="ResearchAgent",
                reason=f"{product} lacks third-party support; official sources dominate the current report.",
                required_action="Add independent reviews, community feedback, customer cases, comparison benchmarks, or social listening evidence before treating the report as publishable.",
                severity="medium",
                product=product,
                missing_evidence_type="third_party_context",
                preferred_source_type="independent_web",
                source_query_hint=f"{_query_product_name(product)} third party review benchmark user feedback community discussion",
            )
        )
        created += 1
        break
    return created


def _suggest_review_tickets_with_llm(state: GraphState) -> int:
    providers = build_provider_bundle()
    payload = _review_ticket_suggestion_payload(state)
    skill_context = _skill_context("competitor_analysis")
    skill_fields = skill_trace_fields(skill_context)
    provider_name = providers.llm.provider_name
    started = time.perf_counter()
    try:
        response = _complete_with_skill(providers.llm, "review_ticket_suggestions", payload, skill_context.prompt if skill_context else "")
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
                **skill_fields,
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
            skill_fields=skill_fields,
        )
        if not providers.allow_provider_fallback:
            return 0
        fallback = MockLLMProvider()
        started = time.perf_counter()
        response = _complete_with_skill(fallback, "review_ticket_suggestions", payload, skill_context.prompt if skill_context else "")
        latency_ms = int((time.perf_counter() - started) * 1000)
        provider_name = fallback.provider_name
        summary = "LLM request failed; MockLLMProvider generated fallback review ticket suggestions."

    tickets = _validated_review_ticket_suggestions(response, state)
    token_count = _provider_token_count(response, _estimate_tokens(payload, response))
    provider_request_id = _provider_request_id(response, provider_name)
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
                provider_request_id=provider_request_id,
                provider_mode=providers.llm_mode,
                **skill_fields,
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
            provider_request_id=provider_request_id,
            skill_fields=skill_fields,
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
            provider_request_id=provider_request_id,
            provider_mode=providers.llm_mode,
            **skill_fields,
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
        provider_request_id=provider_request_id,
        skill_fields=skill_fields,
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
        "source_mix": _source_mix_summary(state),
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
    source_by_id = {source.source_id: source for source in state.sources}
    children: list[FeatureTreeNode] = []
    for product in products:
        feature_evidence = [item for item in state.evidence if item.product == product and item.evidence_type == "feature" and item.status == "active"]
        interaction_evidence = [item for item in state.evidence if item.product == product and item.evidence_type == "browser_interaction" and item.status == "active"]
        real_interaction_evidence = [item for item in interaction_evidence if _interaction_verification_method(item, source_by_id) == "browser_walkthrough"]
        agent_evidence = [item for item in state.evidence if item.product == product and item.evidence_type == "agent_capability" and item.status == "active"]
        security_evidence = [item for item in state.evidence if item.product == product and item.evidence_type == "security" and item.status == "active"]
        interaction_children = [
            FeatureTreeNode(
                name=_interaction_leaf_name(item),
                description=item.summary,
                evidence_ids=[item.evidence_id],
                interaction_path=item.interaction_path,
                verification_method=_interaction_verification_method(item, source_by_id),
            )
            for item in interaction_evidence
        ]
        product_children = [
            FeatureTreeNode(
                name="交互路径",
                description=(
                    "已有真实浏览器点击路径证据，可把这些叶子节点作为已验证上手链路来评估。"
                    if real_interaction_evidence
                    else "当前只有结构化交互路径，适合用于旅程假设，不应当作真实浏览器实测。"
                    if interaction_children
                    else "当前没有浏览器实测证据，功能树不能视为已验证交互路径。"
                ),
                evidence_ids=[item.evidence_id for item in interaction_evidence],
                verification_method="browser_walkthrough" if real_interaction_evidence else "fixture_walkthrough" if interaction_children else "unverified",
                children=interaction_children,
            ),
            FeatureTreeNode(
                name="资料推断工作流",
                description=(feature_evidence[0].summary if feature_evidence else "功能覆盖仍需补充证据。"),
                evidence_ids=[item.evidence_id for item in feature_evidence],
                verification_method="source_inference" if feature_evidence else "unverified",
            ),
            FeatureTreeNode(
                name="Agent / AI 链路",
                description=(agent_evidence[0].summary if agent_evidence else "当前证据没有明确覆盖 Agent 能力。"),
                evidence_ids=[item.evidence_id for item in agent_evidence],
                verification_method="source_inference" if agent_evidence else "unverified",
            ),
            FeatureTreeNode(
                name="团队与安全准备度",
                description=(security_evidence[0].summary if security_evidence else "安全准备度仍是开放的采用风险检查项。"),
                evidence_ids=[item.evidence_id for item in security_evidence],
                verification_method="source_inference" if security_evidence else "unverified",
            ),
        ]
        children.append(
            FeatureTreeNode(
                name=product,
                description=f"{product} 的能力树会区分结构化路径、真实浏览器链路和资料推断能力，避免把功能描述误当成真实体验。",
                verification_method="mixed",
                children=product_children,
            )
        )
    browser_verified_products = len(
        [
            product_node
            for product_node in children
            if product_node.children and product_node.children[0].verification_method == "browser_walkthrough"
        ]
    )
    total = len(children)
    return FeatureTree(
        root=FeatureTreeNode(
            name=f"{state.task.config.target_product} 竞品用户旅程",
            description="用户旅程会区分真实浏览器链路、结构化路径和资料推断结论，帮助 PM 判断哪些体验已经验证、哪些仍需补测。",
            verification_method="mixed",
            children=children,
        ),
        coverage_note=(
            f"{browser_verified_products}/{total} 个产品已有浏览器实测工作流证据；资料推断叶子可用于调研，但不能当作已验证功能路径。"
        ),
    )


def _interaction_leaf_name(item: Evidence) -> str:
    if item.interaction_path:
        return " > ".join(item.interaction_path[-3:])
    return item.quote_or_locator or "Observed workflow"


def _build_pricing_model(state: GraphState) -> PricingModel:
    products = [state.task.config.target_product, *state.task.config.competitors]
    plans: list[PricingPlan] = []
    source_by_id = {source.source_id: source for source in state.sources}
    for product in products:
        pricing_evidence = [item for item in state.evidence if item.product == product and item.evidence_type == "pricing" and item.status == "active"]
        if pricing_evidence:
            summary = pricing_evidence[0].summary
            tiers = _pricing_tiers_from_summary(summary)
            detail_text = " ".join([item.summary for item in pricing_evidence])
            details = _pricing_details_from_text(detail_text)
            plans.append(
                PricingPlan(
                    product=product,
                    model="Published subscription tiers",
                    tiers=tiers,
                    price_points=list(details["price_points"]),
                    billing_unit=str(details["billing_unit"]),
                    usage_limits=list(details["usage_limits"]),
                    trial_or_free=str(details["trial_or_free"]),
                    enterprise_terms=str(details["enterprise_terms"]),
                    data_gaps=list(details["data_gaps"]),
                    monetization_signal=summary,
                    evidence_ids=[item.evidence_id for item in pricing_evidence],
                    confidence=_bounded_claim_confidence("high", pricing_evidence, source_by_id),
                    risk=(
                        f"缺少 {', '.join(details['data_gaps'])}，不能做价格优劣排序。"
                        if details["data_gaps"]
                        else ""
                    ),
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
                    data_gaps=["金额", "计费单位", "额度/限制", "试用/免费策略", "企业条款"],
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


def _pricing_details_from_text(text: str) -> dict[str, list[str] | str]:
    normalized = " ".join(str(text or "").split())
    lowered = normalized.casefold()
    price_points = sorted(set(re.findall(r"(?:\$|￥|¥)\s?\d+(?:\.\d+)?(?:\s?/\s?(?:month|mo|year|yr|seat|user|月|年|席位|用户))?", normalized, flags=re.IGNORECASE)))
    billing_terms = []
    for term in ["per month", "monthly", "per year", "annual", "per seat", "per user", "按月", "按年", "席位", "用户"]:
        if term in lowered or term in normalized:
            billing_terms.append(term)
    usage_limits = []
    for pattern in [
        r"\b\d+\s?(?:requests|messages|credits|seats|users|tokens)\b",
        r"\b(?:usage limits|rate limits|limited|unlimited)\b",
        r"(?:额度|限制|席位|用量|调用)\S{0,12}",
    ]:
        usage_limits.extend(re.findall(pattern, normalized, flags=re.IGNORECASE))
    trial_or_free = ""
    if any(term in lowered for term in ["free", "trial", "免费", "试用"]):
        trial_or_free = "包含免费或试用信号"
    enterprise_terms = ""
    if any(term in lowered for term in ["enterprise", "business", "team", "teams", "企业", "团队", "商业"]):
        enterprise_terms = "包含团队/企业条款信号"
    data_gaps = []
    if not price_points:
        data_gaps.append("金额")
    if not billing_terms:
        data_gaps.append("计费单位")
    if not usage_limits:
        data_gaps.append("额度/限制")
    if not trial_or_free:
        data_gaps.append("试用/免费策略")
    if not enterprise_terms:
        data_gaps.append("企业条款")
    return {
        "price_points": price_points,
        "billing_unit": " / ".join(sorted(set(billing_terms))),
        "usage_limits": sorted(set(usage_limits))[:4],
        "trial_or_free": trial_or_free,
        "enterprise_terms": enterprise_terms,
        "data_gaps": data_gaps,
    }


def _analysis_context_text(state: GraphState) -> str:
    parts = [
        state.task.config.domain,
        state.task.config.target_product,
        *state.task.config.competitors,
        state.task.config.audience,
        *state.task.config.analysis_goals,
        *[item.summary for item in state.evidence[:20]],
    ]
    return " ".join(str(part or "") for part in parts).casefold()


def _analysis_context(state: GraphState) -> str:
    text = _analysis_context_text(state)
    collaboration_keywords = [
        "飞书",
        "钉钉",
        "lark",
        "dingtalk",
        "协同",
        "协作",
        "开放接口",
        "配置",
        "权限",
        "知识库",
        "审批",
        "组织",
    ]
    ai_coding_keywords = [
        "cursor",
        "copilot",
        "windsurf",
        "trae",
        "code",
        "coding",
        "developer",
        "editor",
        "代码",
        "编程",
        "开发者",
        "工程",
    ]
    if any(keyword in text for keyword in collaboration_keywords):
        return "collaboration_saas"
    if state.task.config.domain == "ai_tools" and any(keyword in text for keyword in ai_coding_keywords):
        return "ai_coding"
    if state.task.config.domain == "saas":
        return "saas"
    return "general_product"


def _decision_judgment_line(state: GraphState, target_flow: Claim | None) -> str:
    target = state.task.config.target_product
    context = _analysis_context(state)
    if not target_flow:
        if context == "collaboration_saas":
            return (
                f"- **推荐判断**：{target} 目前缺少可验证协同旅程，不能只凭功能描述做采购判断；下一轮应优先验证跨团队任务、开放接口和配置成本是否真的形成低摩擦协同闭环。"
            )
        if context == "ai_coding":
            return f"- **推荐判断**：{target} 的可发布判断仍缺少可验证开发工作流，建议先补用户旅程再比较定位。"
        return f"- **推荐判断**：{target} 的可发布判断仍缺少可验证用户旅程，建议先补高频任务链路再比较定位。"
    if context == "collaboration_saas":
        return (
            f"- **推荐判断**：{target} 不应只按“协同套件功能多少”来评审，当前更适合判断它能否把跨团队任务、开放接口和配置成本收敛为低摩擦协同闭环。"
        )
    if context == "ai_coding":
        return (
            f"- **推荐判断**：{target} 不应只按“AI 编程工具”定位参赛，当前更适合主打“编辑器内闭环工作流 + 代码上下文”的效率叙事。"
        )
    if context == "saas":
        return (
            f"- **推荐判断**：{target} 不应只按功能清单参赛，当前更适合围绕核心业务流程、权限治理和实施成本讲清楚采用理由。"
        )
    return f"- **推荐判断**：{target} 的比较重点应从“有什么功能”转向“解决哪类高频任务、降低哪类决策成本、还缺哪些采用证据”。"


def _procurement_response_terms(state: GraphState) -> str:
    context = _analysis_context(state)
    if context == "collaboration_saas":
        return "组织权限、流程配置、开放接口、迁移成本和跨部门采用阻力"
    if context == "ai_coding":
        return "团队治理、安全、代码上下文质量和迁移成本"
    if context == "saas":
        return "权限治理、实施成本、数据安全和续费风险"
    return "采用门槛、切换成本、风险控制和核心场景留存"


def _trial_response_terms(state: GraphState) -> str:
    context = _analysis_context(state)
    if context == "collaboration_saas":
        return "首周协同任务能否形成稳定复用，而不是只完成账号开通"
    if context == "ai_coding":
        return "试用后留存来自真实开发链路而不是短期新鲜感"
    if context == "saas":
        return "试用期是否覆盖真实业务流程和团队协作角色"
    return "试用体验是否能转成稳定高频使用"


def _build_user_personas(state: GraphState) -> list[UserPersona]:
    target = state.task.config.target_product
    persona_evidence = [item for item in state.evidence if item.evidence_type == "target_user" and item.status == "active"]
    target_evidence = [item for item in persona_evidence if item.product == target] or persona_evidence[:2]
    context = _analysis_context(state)
    if context == "collaboration_saas":
        return [
            UserPersona(
                name="跨团队协同负责人",
                segment="业务团队 / 协同流程 owner",
                jobs_to_be_done=[
                    "把会议、文档、任务、审批或知识沉淀串成可复用流程。",
                    "降低多团队协作中的信息丢失、重复配置和沟通成本。",
                ],
                pains=[
                    "工具功能多但落不到稳定流程，培训和推广成本高。",
                    "跨系统集成、权限配置和组织边界容易拖慢上线。",
                ],
                decision_criteria=[
                    "高频协同任务的端到端完成率",
                    "开放接口和集成生态",
                    "权限、审计和组织治理能力",
                ],
                evidence_ids=[item.evidence_id for item in target_evidence[:2]],
            ),
            UserPersona(
                name="IT 与平台管理员",
                segment="企业采购 / 信息化团队",
                jobs_to_be_done=[
                    "评估协同平台是否能安全接入现有身份、权限和数据体系。",
                    "在推广效率与治理风险之间形成可落地的采购建议。",
                ],
                pains=[
                    "安全、权限和数据留存材料不足会阻断采购评审。",
                    "接口配置门槛高会让业务团队把平台视为额外负担。",
                ],
                decision_criteria=[
                    "安全与合规材料完整度",
                    "集成实施成本",
                    "组织级管理与审计能力",
                ],
                evidence_ids=[item.evidence_id for item in persona_evidence[:4]],
            ),
        ]
    if context not in {"ai_coding", "collaboration_saas"}:
        return [
            UserPersona(
                name="核心场景使用者",
                segment="一线用户 / 高频任务执行者",
                jobs_to_be_done=[
                    "更快完成当前产品承诺的核心任务。",
                    "在不增加学习成本的前提下获得稳定结果。",
                ],
                pains=[
                    "功能描述容易停留在表层，难以判断是否真的解决日常任务。",
                    "缺少第三方或真实使用样本时，采用风险难以评估。",
                ],
                decision_criteria=[
                    "核心任务完成质量",
                    "学习与迁移成本",
                    "真实用户反馈和第三方验证",
                ],
                evidence_ids=[item.evidence_id for item in target_evidence[:2]],
            ),
            UserPersona(
                name="采购与增长决策者",
                segment="业务负责人 / 产品团队",
                jobs_to_be_done=[
                    "判断产品差异点是否足以支持定位、定价或增长动作。",
                    "把风险和不确定性转成下一轮调研或实验清单。",
                ],
                pains=[
                    "只有功能罗列时，难以形成资源投入优先级。",
                    "价格、风险和外部评价缺口会影响正式决策。",
                ],
                decision_criteria=[
                    "差异化是否具体",
                    "商业化信号是否完整",
                    "下一步行动是否可执行",
                ],
                evidence_ids=[item.evidence_id for item in persona_evidence[:4]],
            ),
        ]
    personas = [
        UserPersona(
            name="个人 AI 辅助开发者",
            segment="Builder / IC 工程师",
            jobs_to_be_done=[
                "在开发环境内更快完成编码、理解和修改任务。",
                "减少编辑器、文档和对话工具之间的上下文切换。",
            ],
            pains=[
                "AI 输出质量依赖代码上下文，但上下文边界经常不透明。",
                "价格、额度和试用限制不清晰时，难以评估长期使用成本。",
            ],
            decision_criteria=[
                "代码库上下文质量",
                "迭代速度",
                "清晰的价格、额度和使用限制",
            ],
            evidence_ids=[item.evidence_id for item in target_evidence[:2]],
        ),
        UserPersona(
            name="工程团队负责人",
            segment="团队 / 平台采购者",
            jobs_to_be_done=[
                "评估 AI 编程工具是否能在团队内标准化采用。",
                "在效率提升、安全隐私和成本控制之间形成采购建议。",
            ],
            pains=[
                "厂商安全控制不清晰会拖慢采购和安全评审。",
                "不同套餐额度不可比时，价格优劣很容易被误判。",
            ],
            decision_criteria=[
                "管理员控制与安全姿态",
                "团队套餐清晰度",
                "真实工作流覆盖",
            ],
            evidence_ids=[item.evidence_id for item in persona_evidence[:4]],
        ),
    ]
    return personas


def _build_swot(state: GraphState, included: list[Claim], uncertain: list[Claim]) -> SwotAnalysis:
    target = state.task.config.target_product
    evidence_ids = [evidence_id for claim in included for evidence_id in claim.supporting_evidence][:12]
    target_claims = [claim for claim in included if claim.product == target]
    target_feature_claim = next((claim for claim in target_claims if claim.claim_type in {"feature", "browser_interaction", "agent_capability"}), None)
    target_pricing_claim = next((claim for claim in target_claims if claim.claim_type == "pricing"), None)
    target_security_claim = next((claim for claim in target_claims if claim.claim_type == "security"), None)
    competitor_count = len(state.task.config.competitors)
    unresolved_tickets = [ticket for ticket in state.review_tickets if ticket.status in {"open", "accepted", "rerun_started"}]
    downgraded_or_uncertain = [claim for claim in uncertain if claim.product == target or claim.product in {"Opportunity", "Risk"}]
    return SwotAnalysis(
        strengths=[
            (
                f"{target} 的优势不应只写成“AI 工具定位”，而是可落在实测工作流和代码上下文能力上：{_plain_summary(target_feature_claim.claim)}。"
                if target_feature_claim
                else f"{target} 目前至少有定位证据，但需要补充可操作工作流证据。"
            ),
            (
                f"商业化表达已有官方证据支撑：{_plain_summary(target_pricing_claim.claim)}，适合进入套餐与额度的下一轮拆解。"
                if target_pricing_claim
                else "商业化证据不足，不能支撑价格竞争判断。"
            ),
        ],
        weaknesses=[
            (
                f"安全与企业采购材料仍需要和实际客户案例交叉验证：{_plain_summary(target_security_claim.claim)}。"
                if target_security_claim
                else "安全、隐私和企业采购证据不足，会影响团队版落地判断。"
            ),
            f"{len(downgraded_or_uncertain)} 个目标产品相关判断仍需复核，不能进入对外发布结论。",
        ],
        opportunities=[
            "把用户旅程中的“实测路径”转成产品卖点和 onboarding 对比，而不是继续堆功能清单。",
            f"对 {competitor_count} 个竞品按采购场景分层比较：个人效率、团队治理、企业安全、价格额度。",
        ],
        threats=[
            f"{len(unresolved_tickets)} 个未解决 Review Ticket 会直接影响报告是否可发布。",
            "如果第三方评测和社媒样本不足，报告容易偏向厂商叙述，难以支撑真实用户侧优先级。",
        ],
        evidence_ids=evidence_ids,
    )


def _feature_tree_markdown(node: FeatureTreeNode, depth: int = 0) -> list[str]:
    prefix = "  " * depth + "- "
    evidence = f"（证据：{', '.join(node.evidence_ids)}）" if node.evidence_ids else ""
    method = {
        "browser_walkthrough": "实测",
        "fixture_walkthrough": "结构化路径",
        "source_inference": "文档/搜索推断",
        "unverified": "未实测",
        "mixed": "混合",
    }.get(node.verification_method, node.verification_method)
    path = f"；路径：{' > '.join(node.interaction_path)}" if node.interaction_path else ""
    description = _plain_summary(node.description) if node.description else ""
    lines = [f"{prefix}**{node.name}** [{method}]：{description}{path}{evidence}"]
    for child in node.children:
        lines.extend(_feature_tree_markdown(child, depth + 1))
    return lines


def _swot_lines(swot: SwotAnalysis) -> list[str]:
    return [
        "- **Strengths**：" + "；".join(swot.strengths),
        "- **Weaknesses**：" + "；".join(swot.weaknesses),
        "- **Opportunities**：" + "；".join(swot.opportunities),
        "- **Threats**：" + "；".join(swot.threats),
        f"- {_evidence_ref(swot.evidence_ids)}",
    ]


def _claim_lookup(claims: list[Claim]) -> dict[tuple[str, str], Claim]:
    return {(claim.product, claim.claim_type): claim for claim in claims}


def _product_claim(claims_by_key: dict[tuple[str, str], Claim], product: str, claim_type: str) -> Claim | None:
    return claims_by_key.get((product, claim_type))


def _decision_summary_lines(state: GraphState, included: list[Claim], trust: TrustSummary, source_mix: dict[str, float | int | str]) -> list[str]:
    target = state.task.config.target_product
    competitors = state.task.config.competitors
    claims_by_key = _claim_lookup(included)
    target_flow = _product_claim(claims_by_key, target, "browser_interaction") or _product_claim(claims_by_key, target, "feature")
    target_pricing = _product_claim(claims_by_key, target, "pricing")
    competitor_with_enterprise = next(
        (
            competitor
            for competitor in competitors
            if (claim := _product_claim(claims_by_key, competitor, "pricing")) and "enterprise" in claim.claim.casefold()
        ),
        "",
    )
    competitor_with_free = next(
        (
            competitor
            for competitor in competitors
            if (claim := _product_claim(claims_by_key, competitor, "pricing")) and "free" in claim.claim.casefold()
        ),
        "",
    )
    lines = [
        "",
        "## 决策摘要",
        _decision_judgment_line(state, target_flow),
        (
            f"- **商业化判断**：已有套餐结构证据，但报告未拿到金额、额度和计费单位，因此只能比较包装策略，不能下价格高低结论。{_evidence_ref(target_pricing.supporting_evidence if target_pricing else [])}"
        ),
    ]
    if competitor_with_enterprise:
        lines.append(f"- **采购场景**：若受众是中大型团队，{competitor_with_enterprise} 的 Business/Enterprise 叙事会天然进入候选；{target} 需要用{_procurement_response_terms(state)}来回应。")
    if competitor_with_free:
        lines.append(f"- **试用场景**：{competitor_with_free} 有免费/付费组合信号，可能降低首次试用门槛；{target} 需要证明{_trial_response_terms(state)}。")
    lines.append(
        f"- **可信边界**：当前第三方来源占比 {float(source_mix['third_party_ratio']):.0%}、未解决工单 {trust.unresolved_ticket_count} 个；低第三方覆盖时，报告更适合做内部评审初稿，而不是直接对外引用。"
    )
    return lines


def _decision_evidence_grade(trust: TrustSummary, source_mix: dict[str, float | int | str]) -> str:
    third_party_ratio = float(source_mix["third_party_ratio"])
    if trust.unresolved_ticket_count or trust.blocked_claim_count:
        return "C"
    if trust.claim_evidence_binding_rate >= 0.9 and third_party_ratio >= THIRD_PARTY_SOURCE_RATIO_TARGET and trust.browser_verified_product_count:
        return "A"
    if trust.claim_evidence_binding_rate >= 0.75:
        return "B"
    return "C"


def _external_use_status(trust: TrustSummary, source_mix: dict[str, float | int | str]) -> str:
    if trust.unresolved_ticket_count or trust.blocked_claim_count:
        return "不可外发，先处理阻断项"
    if float(source_mix["third_party_ratio"]) < THIRD_PARTY_SOURCE_RATIO_TARGET:
        return "内部评审可用，正式外发前需补第三方样本"
    return "可进入正式评审"


def _pricing_gap_summary(pricing_model: PricingModel) -> str:
    gap_products = [f"{plan.product}({', '.join(plan.data_gaps)})" for plan in pricing_model.plans if plan.data_gaps]
    return "；".join(gap_products) if gap_products else "定价结构字段较完整"


def _quality_rubric_lines(state: GraphState, included: list[Claim], trust: TrustSummary, source_mix: dict[str, float | int | str], pricing_model: PricingModel) -> list[str]:
    comparative_count = len([claim for claim in included if claim.claim_type.startswith("comparative")])
    opportunity_count = len([claim for claim in included if claim.claim_type == "opportunity"])
    pricing_gap_count = sum(len(plan.data_gaps) for plan in pricing_model.plans)
    scenario_fit = "协同 SaaS" if _analysis_context(state) == "collaboration_saas" else "AI 编程/开发工具" if _analysis_context(state) == "ai_coding" else "通用产品"
    return [
        f"- 差异洞察：{comparative_count} 条横向结论、{opportunity_count} 条机会结论；低于 2 条时不能作为路线决策稿。",
        f"- 行动可执行性：已生成补外部样本、补价格深度、补真实用户验证、补采购风险四类动作；定价字段缺口 {pricing_gap_count} 项。",
        f"- 风险边界：证据等级 {_decision_evidence_grade(trust, source_mix)}，外发状态为“{_external_use_status(trust, source_mix)}”。",
        f"- 场景适配：当前按“{scenario_fit}”语境组织画像和推荐判断，避免把一种产品模板套到另一种场景。",
    ]


def _pm_decision_page_lines(
    state: GraphState,
    included: list[Claim],
    trust: TrustSummary,
    source_mix: dict[str, float | int | str],
    pricing_model: PricingModel,
) -> list[str]:
    target = state.task.config.target_product
    external_status = _external_use_status(trust, source_mix)
    evidence_grade = _decision_evidence_grade(trust, source_mix)
    priority = "P0" if trust.unresolved_ticket_count or trust.blocked_claim_count else "P1" if float(source_mix["third_party_ratio"]) < THIRD_PARTY_SOURCE_RATIO_TARGET else "P2"
    risk_level = "高" if trust.unresolved_ticket_count or trust.blocked_claim_count else "中" if evidence_grade == "B" else "低"
    recommended_action = (
        "先关闭阻断工单，再进入产品评审"
        if trust.unresolved_ticket_count or trust.blocked_claim_count
        else "作为 PM 内部评审稿使用，并并行补齐外部样本与定价字段"
        if float(source_mix["third_party_ratio"]) < THIRD_PARTY_SOURCE_RATIO_TARGET or any(plan.data_gaps for plan in pricing_model.plans)
        else "进入正式评审，重点讨论路线取舍和采购风险"
    )
    lines = [
        "",
        "## PM 决策页",
        f"- **一句话结论**：{target} 当前可以进入“{external_status}”的决策流程；不要把它当作单一排名报告，而应作为场景取舍、风险清单和下一轮验证计划。",
        f"- **建议动作**：{recommended_action}。",
        f"- **优先级 / 风险 / 证据等级**：{priority} / {risk_level} / {evidence_grade}。",
        f"- **定价决策缺口**：{_pricing_gap_summary(pricing_model)}；缺少金额、额度或计费单位时，只能比较商业化包装，不能判断价格高低。",
        f"- **是否可对外**：{external_status}。",
        "- **质量 rubric**：",
        *_quality_rubric_lines(state, included, trust, source_mix, pricing_model),
    ]
    return lines


def _differentiated_insight_lines(state: GraphState, included: list[Claim]) -> list[str]:
    products = [state.task.config.target_product, *state.task.config.competitors]
    claims_by_key = _claim_lookup(included)
    lines = ["", "## 关键差异洞察"]
    for product in products:
        flow = _product_claim(claims_by_key, product, "browser_interaction")
        pricing = _product_claim(claims_by_key, product, "pricing")
        security = _product_claim(claims_by_key, product, "security")
        persona = _product_claim(claims_by_key, product, "target_user")
        if not any([flow, pricing, security, persona]):
            lines.append(f"- **{product}**：当前证据不足以形成差异判断，应先补定位、价格、用户和安全证据。")
            continue
        flow_text = _plain_summary(flow.claim) if flow else "缺少可验证点击路径，暂不能证明真实上手链路"
        pricing_text = _plain_summary(pricing.claim) if pricing else "缺少套餐证据，不能判断商业化门槛"
        security_text = _plain_summary(security.claim) if security else "安全/隐私证据不足，会阻断团队采购讨论"
        persona_text = _plain_summary(persona.claim) if persona else "目标用户证据不足，难以判断主攻人群"
        evidence_ids = []
        for claim in [flow, pricing, security, persona]:
            if claim:
                evidence_ids.extend(claim.supporting_evidence[:1])
        lines.append(
            f"- **{product}**：产品叙事应围绕“{flow_text}”；商业化只可说“{pricing_text}”；采购风险看“{security_text}”；主攻人群判断为“{persona_text}”。{_evidence_ref(evidence_ids)}"
        )
    return lines


def _next_action_lines(state: GraphState, trust: TrustSummary, source_mix: dict[str, float | int | str]) -> list[str]:
    actions = ["", "## 下一步行动清单"]
    if trust.unresolved_ticket_count:
        actions.append("- **先处理阻断项**：逐个关闭未解决 Review Ticket；未补到证据的内容继续留在不确定性章节，不能写成结论。")
    if float(source_mix["third_party_ratio"]) < THIRD_PARTY_SOURCE_RATIO_TARGET:
        actions.append("- **补外部样本**：补充第三方评测、社区讨论、客户案例或社媒样本，把第三方来源占比提升到 35% 以上，再用于正式评审。")
    actions.extend(
        [
            "- **补价格深度**：下一轮必须抽取金额、额度、计费单位、试用策略和企业条款，否则不要做价格优劣排序。",
            "- **补真实用户验证**：围绕报告中的用户旅程设计 5-8 个访谈问题，验证“实测路径”是否真的对应高频任务。",
            "- **补采购风险**：对安全、隐私、权限、团队管理和数据留存做单独表格，避免把个人开发者体验误判为团队可采购能力。",
        ]
    )
    return actions


def _pm_acceptance_lines(state: GraphState, trust: TrustSummary, source_mix: dict[str, float | int | str]) -> list[str]:
    products = [state.task.config.target_product, *state.task.config.competitors]
    unresolved = [ticket for ticket in state.review_tickets if ticket.status in {"open", "accepted", "rerun_started"}]
    resolved = [ticket for ticket in state.review_tickets if ticket.status in {"resolved", "dismissed", "blocked"}]
    schema_ready = [
        "User Journey" if any(item.evidence_type in {"feature", "browser_interaction"} for item in state.evidence) else "",
        "PricingModel" if any(item.evidence_type == "pricing" for item in state.evidence) else "",
        "UserPersona" if any(item.evidence_type == "target_user" for item in state.evidence) else "",
        "SWOT",
    ]
    schema_ready = [item for item in schema_ready if item]
    structured_interaction_count = len(
        [
            item
            for item in state.evidence
            if item.evidence_type == "browser_interaction"
            and any(source.source_id == item.source_id and source.source_type in {"fixture_walkthrough", "official_fixture_walkthrough"} for source in state.sources)
        ]
    )
    third_party_ratio = float(source_mix["third_party_ratio"])
    if trust.unresolved_ticket_count or trust.blocked_claim_count:
        status = "需人工复核"
        status_note = "仍有未解决或阻断项，不能外部发布。"
    elif third_party_ratio < THIRD_PARTY_SOURCE_RATIO_TARGET:
        status = "可进入内部评审"
        status_note = "主结论可供 PM 评审，但正式发布前需补外部样本。"
    else:
        status = "可发布结论"
        status_note = "来源结构和结论状态已满足正式评审基线。"
    lines = [
        "",
        "## PM 验收检查",
        f"- 决策状态：{status}；未解决工单 {trust.unresolved_ticket_count} 个，阻断结论 {trust.blocked_claim_count} 个；{status_note}",
        f"- 效率证据：一次任务覆盖 {len(products)} 个产品、{trust.total_source_count} 个来源、{trust.total_evidence_count} 条证据、{trust.total_claim_count} 条结论和 {len(state.trace)} 条 Agent Trace，可替代人工分散检索后的初稿整理。",
        f"- 覆盖度证据：官方来源占比 {trust.official_source_ratio:.0%}，第三方来源占比 {third_party_ratio:.0%}，浏览器实测覆盖 {trust.browser_verified_product_count}/{trust.browser_verified_product_total} 个产品，结构化交互路径 {structured_interaction_count} 条。",
        f"- 一致性证据：已生成 {' / '.join(schema_ready)}；证据绑定率 {trust.claim_evidence_binding_rate:.0%}，所有进入报告的结论必须绑定 Evidence ID。",
        f"- 人工介入：已处理 Review Ticket {len(resolved)} 个；{'无未解决工单，可进入 PM 内部评审。' if not unresolved else '仍需处理以下工单后再外部发布。'}",
    ]
    for ticket in unresolved[:3]:
        lines.append(f"  - {ticket.product or '全局'} / {ticket.missing_evidence_type or 'unknown'}：{ticket.required_action}")
    return lines


def _evidence_ref(ids: list[str]) -> str:
    return f"（证据：{', '.join(ids)}）" if ids else "（证据待补充）"


def _plain_summary(text: str) -> str:
    text = " ".join(str(text or "").split())
    text = re.sub(r"\s*Evidence:\s*[\w_, -]+", "", text, flags=re.IGNORECASE).strip()
    replacements = {
        "Cursor is positioned as an AI-native code editor": "Cursor 的核心定位是 AI 原生代码编辑器",
        "Cursor offers individual and team subscription plans": "Cursor 已覆盖个人与团队订阅包装",
        "Cursor feature coverage centers on editor-native AI, codebase context, and agent workflows": "Cursor 的能力重心在编辑器内 AI、代码库上下文和 Agent 工作流",
        "Cursor's agent workflow is represented as an editor path from the agent panel through context selection to applying code changes": "Cursor 的结构化路径覆盖 Agent 面板、上下文选择到代码变更应用，适合形成待实测的编辑器内闭环假设",
        "Cursor targets individual developers and engineering teams adopting AI coding workflows": "Cursor 同时面向个人开发者和正在规模化采用 AI 编程的工程团队",
        "Cursor publishes security and privacy controls for team or enterprise adoption": "Cursor 已提供团队/企业采用所需的安全与隐私控制材料",
        "GitHub Copilot workflow is represented as a path from repository context into Copilot chat, agent task handling, and pull request review": "GitHub Copilot 的结构化路径连接仓库上下文、Copilot Chat、Agent 任务和 PR Review",
        "GitHub Copilot has individual, business, and enterprise plan tiers": "GitHub Copilot 的套餐覆盖个人、商业和企业层级",
        "GitHub Copilot surfaces enterprise trust and security controls": "GitHub Copilot 已把企业信任与安全控制作为采购叙事的一部分",
        "GitHub Copilot targets individual developers plus business and enterprise engineering organizations": "GitHub Copilot 同时覆盖个人开发者、商业团队和企业工程组织",
        "Windsurf's agentic coding workflow is represented as an editor path through Cascade, prompt input, context selection, and suggested code changes": "Windsurf 的结构化路径围绕 Cascade、提示输入、上下文选择和建议变更运行",
        "Windsurf publishes free and paid plans for individual and team usage": "Windsurf 有免费与付费组合，覆盖个人和团队使用",
        "Windsurf publishes security and privacy signals for team adoption": "Windsurf 已提供团队采用所需的安全与隐私信号",
        "Windsurf targets developers and teams seeking AI-assisted coding workflows": "Windsurf 面向希望引入 AI 辅助开发链路的开发者和团队",
        "Positioning differs across Cursor, GitHub Copilot, Windsurf; this supports a matrix-style comparison rather than a single ranked verdict": "三者定位差异足够明显，应按使用场景矩阵比较，而不是给单一排名",
        "Feature coverage differs enough to require a user-journey comparison instead of a flat checklist": "功能差异需要落到用户旅程比较，单纯功能清单无法解释真实选择",
        "Browser-observed workflow paths are available for multiple products, so the feature tree can separate real interaction coverage from source-only feature claims": "多个产品已有结构化路径，可把待实测体验假设和文档推断能力分开评估",
        "Pricing coverage is strongest where official pricing pages are present; unresolved pricing gaps should be treated as follow-up research rather than final conclusions": "只有官方定价页支撑的商业化判断可进入正文，价格缺口应保留为后续调研",
    }
    for source, replacement in replacements.items():
        text = text.replace(source, replacement)
    return text.rstrip("。.")


def _claim_narrative(claim: Claim) -> str:
    summary = _plain_summary(claim.claim)
    type_labels = {
        "positioning": "定位",
        "pricing": "商业化",
        "feature": "能力覆盖",
        "target_user": "目标用户",
        "security": "安全与合规",
        "agent_capability": "AI/Agent 能力",
        "browser_interaction": "实际链路",
        "third_party_context": "外部视角",
        "social_sentiment": "社媒反馈",
    }
    if claim.product == "Cross-product":
        return f"横向比较来看，{summary}。{_evidence_ref(claim.supporting_evidence)}"
    if claim.product in {"Opportunity", "Risk", "External signal", "Social listening"}:
        return f"{summary}。{_evidence_ref(claim.supporting_evidence)}"
    label = type_labels.get(claim.claim_type, claim.claim_type.replace("_", " "))
    return f"从{label}看，{claim.product} 的关键信号是：{summary}。{_evidence_ref(claim.supporting_evidence)}"


def _matrix_cell_narrative(claim: Claim | None) -> str:
    if not claim:
        return "暂未评估"
    if claim.verified_status != "passed":
        return "证据不足，需复核"
    return f"{claim.confidence}，{len(claim.supporting_evidence)} 条证据"


def _source_type_label(source_type: str) -> str:
    if source_type.startswith("official"):
        return "官方来源"
    if source_type.startswith("social_"):
        return "社媒/用户反馈"
    if source_type in {"third_party_relevant", "independent_web", "community_forum", "review_site"}:
        return "第三方来源"
    if source_type in {"browser_walkthrough", "official_browser_walkthrough"}:
        return "真实浏览器实测"
    if source_type in {"fixture_walkthrough", "official_fixture_walkthrough"}:
        return "结构化交互路径"
    return source_type


def _clean_resource_excerpt(text: str, limit: int = 260) -> str:
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[[^\]]+\]\(([^)]+)\)", r"\1", text)
    text = re.sub(r"^[#>*\-\s]+", "", text, flags=re.MULTILINE)
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "..."
    return text


def writer_node(state: GraphState) -> GraphState:
    included = [claim for claim in state.claims if claim.included_in_report and claim.verified_status == "passed"]
    uncertain = [claim for claim in state.claims if claim.verified_status != "passed"]
    trust = state.trust_summary or _build_trust_summary(state)
    state.skill_assignments = skill_snapshot(skill_store)
    feature_tree = _build_feature_tree(state)
    pricing_model = _build_pricing_model(state)
    personas = _build_user_personas(state)
    swot = _build_swot(state, included, uncertain)
    social_insights = state.social_insights
    source_mix = _source_mix_summary(state)
    structured_interaction_count = len(
        [
            item
            for item in state.evidence
            if item.evidence_type == "browser_interaction"
            and any(source.source_id == item.source_id and source.source_type in {"fixture_walkthrough", "official_fixture_walkthrough"} for source in state.sources)
        ]
    )
    lines = [
        f"# {state.task.config.target_product} 竞品分析报告",
        "",
        "## 可信度摘要",
        f"- 证据绑定率：{trust.claim_evidence_binding_rate:.0%}",
        f"- 官方来源占比：{trust.official_source_ratio:.0%}",
        f"- 第三方来源占比：{float(source_mix['third_party_ratio']):.0%}（{int(source_mix['third_party_count'])} / {int(source_mix['total'])} 个可计入来源）",
        f"- 浏览器实测证据：{trust.browser_interaction_count} 条（覆盖 {trust.browser_verified_product_count} / {trust.browser_verified_product_total} 个产品）",
        f"- 结构化交互路径证据：{structured_interaction_count} 条（用于旅程假设，不等同真实浏览器实测）",
        f"- 已通过结论：{trust.passed_claim_count} / {trust.total_claim_count}",
        f"- 不确定 / 阻断 / 降级结论：{trust.uncertain_claim_count} / {trust.blocked_claim_count} / {trust.downgraded_claim_count}",
        f"- 未解决 Review Ticket：{trust.unresolved_ticket_count}",
        f"- 运行模式：{trust.provider_mode_label}",
        f"- Search provider：{trust.search_mode}",
        f"- LLM provider：{trust.llm_mode}",
        *(
            ["- 状态提示：仍有未解决 Review Ticket，报告需人工复核后再外部发布。"]
            if trust.unresolved_ticket_count
            else []
        ),
        "",
        "## 分析背景",
        f"- 目标产品：{state.task.config.target_product}",
        f"- 竞品范围：{', '.join(state.task.config.competitors)}",
        f"- 报告受众：{state.task.config.audience}",
        f"- 证据严格度：{state.task.config.evidence_strictness}",
        f"- 来源结构判断：{source_mix['note']}",
    ]
    comparative_claims = [claim for claim in included if claim.claim_type.startswith("comparative")]
    opportunity_claims = [claim for claim in included if claim.claim_type == "opportunity"]
    lines.extend(_pm_decision_page_lines(state, included, trust, source_mix, pricing_model))
    lines.extend(_decision_summary_lines(state, included, trust, source_mix))
    lines.extend(_differentiated_insight_lines(state, included))
    lines.extend(["", "## 核心结论"])
    if comparative_claims:
        for claim in comparative_claims[:3]:
            lines.append(f"- {_claim_narrative(claim)}")
    if opportunity_claims:
        for claim in opportunity_claims[:2]:
            lines.append(f"- {_claim_narrative(claim)}")
    if not comparative_claims and not opportunity_claims:
        lines.append("- 当前证据更适合先形成产品事实底稿；还需要补充竞品间可比证据后再输出排序或优先级判断。")
    lines.extend(["", "## 产品定位与能力矩阵"])
    products = [state.task.config.target_product, *state.task.config.competitors]
    claim_types = ["positioning", "agent_capability", "pricing"]
    for product in products:
        cells = []
        for claim_type in [*claim_types, "feature", "target_user", "security"]:
            claim = next((item for item in state.claims if item.product == product and item.claim_type == claim_type), None)
            cells.append(f"{claim_type}: {_matrix_cell_narrative(claim)}")
        lines.append(f"- **{product}**：{' | '.join(cells)}")
    lines.extend(["", "## 用户旅程 User Journey"])
    lines.extend(_feature_tree_markdown(feature_tree.root))
    lines.append(f"- 覆盖说明：{feature_tree.coverage_note}")
    lines.extend(["", "## 定价模型 PricingModel"])
    for plan in pricing_model.plans:
        tier_text = " / ".join(plan.tiers) if plan.tiers else "未覆盖"
        price_text = " / ".join(plan.price_points) if plan.price_points else "未抽取到公开金额"
        limit_text = " / ".join(plan.usage_limits) if plan.usage_limits else "未抽取到额度限制"
        gap_text = " / ".join(plan.data_gaps) if plan.data_gaps else "无明显结构缺口"
        lines.append(
            f"- **{plan.product}**：收费结构 {plan.model}；层级信号 {tier_text}；金额 {price_text}；计费单位 {plan.billing_unit or '未抽取'}；额度 {limit_text}；试用/免费 {plan.trial_or_free or '未明确'}；企业条款 {plan.enterprise_terms or '未明确'}。置信度：{plan.confidence}；{_evidence_ref(plan.evidence_ids)}"
        )
        lines.append(f"  - 缺口：{gap_text}；商业化判断：{_plain_summary(plan.monetization_signal)}。")
        if plan.risk:
            lines.append(f"  - 风险：{plan.risk}")
    lines.append(f"- 对比摘要：{pricing_model.comparison_summary}")
    lines.extend(["", "## 用户画像 UserPersona"])
    for persona in personas:
        lines.append(f"- **{persona.name} / {persona.segment}**")
        lines.append(f"  - JTBD：{'；'.join(persona.jobs_to_be_done)}")
        lines.append(f"  - 痛点：{'；'.join(persona.pains)}")
        lines.append(f"  - 决策标准：{'；'.join(persona.decision_criteria)}")
        lines.append(f"  - {_evidence_ref(persona.evidence_ids)}")
    lines.extend(["", "## SWOT"])
    lines.extend(_swot_lines(swot))
    if state.task.config.social_listening.enabled:
        lines.extend(["", "## 社媒舆情洞察"])
        if social_insights:
            for insight in social_insights:
                lines.append(f"- **{_social_platform_label(insight.platform)} / {insight.status}**：{insight.summary}")
                if insight.themes:
                    lines.append(f"  - 主题：{'；'.join(insight.themes)}")
                if insight.pain_points:
                    lines.append(f"  - 高频痛点：{'；'.join(insight.pain_points[:4])}")
                if insight.purchase_signals:
                    lines.append(f"  - 购买/种草信号：{'；'.join(insight.purchase_signals[:4])}")
                if insight.churn_or_risk_signals:
                    lines.append(f"  - 弃用/避雷信号：{'；'.join(insight.churn_or_risk_signals[:4])}")
                if insight.competitor_mentions:
                    lines.append(f"  - 被提及产品：{'；'.join(insight.competitor_mentions)}")
                lines.append(f"  - 情绪：{insight.sentiment.overall}（正 {insight.sentiment.positive_count} / 中 {insight.sentiment.neutral_count} / 负 {insight.sentiment.negative_count}）")
                lines.append(f"  - {_evidence_ref(insight.evidence_ids)}")
        else:
            lines.append("- 已启用社媒舆情，但当前没有可用采集结果；请检查小红书 MCP 登录状态或粘贴点点 AI 总结。")
        social_tickets = [
            ticket
            for ticket in state.review_tickets
            if ticket.missing_evidence_type == "social_sentiment" or ticket.preferred_source_type.startswith("social_")
        ]
        seen_social_reasons: set[str] = set()
        for ticket in social_tickets[:3]:
            reason = " ".join(str(ticket.reason or "").split())
            action = " ".join(str(ticket.required_action or "").split())
            message = f"{reason} 建议：{action}".strip()
            if not message or message in seen_social_reasons:
                continue
            seen_social_reasons.add(message)
            lines.append(f"- 当前阻断：{message}")
    lines.extend(_pm_acceptance_lines(state, trust, source_mix))
    lines.extend(["", "## 机会点建议"])
    if opportunity_claims:
        for claim in opportunity_claims:
            lines.append(f"- {_claim_narrative(claim)}")
    else:
        lines.append("- 保持未覆盖证据为后续调研清单，不把缺失信息写成确定结论。")
    lines.extend(_next_action_lines(state, trust, source_mix))
    lines.extend(["", "## 不确定性与被阻断结论"])
    for claim in uncertain[:8]:
        lines.append(f"- **{claim.product} / {claim.claim_type}**：{claim.note or claim.claim}")
    lines.extend(["", "## 数据来源（Resources）"])
    for source in state.sources:
        excerpt = _clean_resource_excerpt(source.content)
        lines.append(f"- [{source.title}]({source.url})")
        lines.append(f"  - 类型：{_source_type_label(source.source_type)} / {source.source_type}；产品：{source.product}；置信度：{source.confidence}")
        lines.append(f"  - Query：`{source.query}`")
        if excerpt:
            lines.append("  - 原文摘录：")
            lines.append(f"    > {excerpt}")
    lines.extend(["", "## Agent 协作记录", f"- Review Tickets: {len(state.review_tickets)}", f"- Trace Events: {len(state.trace)}"])
    markdown = "\n".join(lines)
    markdown = _enhance_report_with_llm(state, markdown, included, uncertain, trust)
    report_status = "reviewing" if trust.unresolved_ticket_count else "passed"
    if any(claim.verified_status == "blocked" for claim in state.claims):
        report_status = "blocked"
    state.report = Report(
        task_id=state.task.task_id,
        title=f"{state.task.config.target_product} Competitor Analysis",
        markdown=markdown,
        status=report_status,
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
        social_insights=social_insights,
        skill_assignments=state.skill_assignments,
    )
    _trace(state, "WriterAgent", "writer", "report_drafted", "Generated Markdown report draft.")
    return state


def _social_platform_label(platform: str) -> str:
    return {
        "xiaohongshu": "小红书",
        "weibo": "微博",
        "douyin": "抖音",
    }.get(platform, platform)


def _enhance_report_with_llm(state: GraphState, markdown: str, included: list[Claim], uncertain: list[Claim], trust: TrustSummary) -> str:
    providers = build_provider_bundle()
    payload = _report_enhancement_payload(state, included, uncertain, trust)
    skill_contexts = SkillPromptComposer(skill_store).contexts_for_slots(
        [
            "competitor_analysis",
            "sentiment_analysis" if state.task.config.social_listening.enabled else "",
            "pricing_strategy",
            "finance_pricing",
            "user_personas",
            "customer_journey",
            "swot_analysis",
            "jtbd_opportunity",
            "report_enhancement",
        ]
    )
    skill_context = skill_contexts[0] if skill_contexts else None
    skill_prompt = "\n\n".join(context.prompt for context in skill_contexts)
    skill_fields = skill_trace_fields(skill_context)
    provider_name = providers.llm.provider_name
    started = time.perf_counter()
    try:
        response = _complete_with_skill(providers.llm, "report_enhancement", payload, skill_prompt)
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
                **skill_fields,
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
            skill_fields=skill_fields,
        )
        if not providers.allow_provider_fallback:
            return markdown
        fallback = MockLLMProvider()
        started = time.perf_counter()
        response = _complete_with_skill(fallback, "report_enhancement", payload, skill_prompt)
        latency_ms = int((time.perf_counter() - started) * 1000)
        provider_name = fallback.provider_name
        status = "success"
        summary = "LLM request failed; MockLLMProvider generated fallback report enhancement."

    enhancement = _format_report_enhancement(response, included)
    token_count = _provider_token_count(response, _estimate_tokens(payload, response))
    provider_request_id = _provider_request_id(response, provider_name)
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
                provider_request_id=provider_request_id,
                provider_mode=providers.llm_mode,
                **skill_fields,
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
            provider_request_id=provider_request_id,
            skill_fields=skill_fields,
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
            output_summary="Report enhancement sections inserted before decision analysis.",
            token_count=token_count,
            latency_ms=latency_ms,
            provider_request_id=provider_request_id,
            provider_mode=providers.llm_mode,
            **skill_fields,
        )
    )
    _trace(
        state,
        "WriterAgent",
        "writer",
        "llm_enhancement_applied",
        summary,
        input_summary=f"Prompt report_enhancement with {len(included)} included claim(s).",
        output_summary="Report enhancement sections inserted before decision analysis.",
        prompt_name="report_enhancement",
        prompt=AUDIT_PROMPTS["report_enhancement"],
        input_payload=payload,
        output_payload=response if isinstance(response, dict) else {},
        token_count=token_count,
        latency_ms=latency_ms,
        provider=provider_name,
        provider_request_id=provider_request_id,
        skill_fields=skill_fields,
    )
    return _insert_report_enhancement(markdown, enhancement)


def _insert_report_enhancement(markdown: str, enhancement: str) -> str:
    marker = "\n## PM 决策页"
    if marker in markdown:
        return markdown.replace(marker, f"\n\n{enhancement}{marker}", 1)
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
        "source_mix": _source_mix_summary(state),
        "writing_rules": [
            "Use natural, polished Chinese suitable for a formal PM analysis report.",
            "Paraphrase evidence into plain-language analysis; do not paste source wording in the body.",
            "Use original wording only in the Resources section and keep excerpts short and cleaned.",
            "Call out third-party support separately from official vendor claims.",
            "Every executive_summary and strategic_recommendations item must include claim_ids or evidence_ids from included_claims.",
            "Unbound synthesis must be returned as caveats, not as a factual conclusion.",
        ],
        "included_claims": [
            {
                "claim_id": claim.claim_id,
                "product": claim.product,
                "claim_type": claim.claim_type,
                "claim": claim.claim,
                "supporting_evidence": claim.supporting_evidence,
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


def _format_report_enhancement(response: dict, included: list[Claim]) -> str:
    executive_summary, unbound_summary = _bound_enhancement_items(response.get("executive_summary"), included)
    recommendations, unbound_recommendations = _bound_enhancement_items(response.get("strategic_recommendations"), included)
    caveats = _string_list(response.get("caveats"))
    caveats.extend(f"未绑定证据，需复核：{item}" for item in [*unbound_summary, *unbound_recommendations])
    if not executive_summary and not recommendations and not caveats:
        return ""
    lines = ["## 结构化综合摘要"]
    if executive_summary:
        lines.extend(f"- {item}" for item in executive_summary)
    if recommendations:
        lines.extend(["", "## 结构化建议"])
        lines.extend(f"- {item}" for item in recommendations)
    if caveats:
        lines.extend(["", "## 结构化注意事项"])
        lines.extend(f"- {item}" for item in caveats)
    return "\n".join(lines)


def _bound_enhancement_items(value, included: list[Claim]) -> tuple[list[str], list[str]]:
    if not isinstance(value, list):
        return [], []
    claim_by_id = {claim.claim_id: claim for claim in included if claim.supporting_evidence}
    evidence_to_claim = {
        evidence_id: claim
        for claim in included
        for evidence_id in claim.supporting_evidence
    }
    bound: list[str] = []
    unbound: list[str] = []
    for raw in value[:8]:
        text = ""
        claim_ids: list[str] = []
        evidence_ids: list[str] = []
        if isinstance(raw, dict):
            text = str(raw.get("text") or raw.get("summary") or raw.get("recommendation") or "").strip()
            claim_ids = [str(item).strip() for item in raw.get("claim_ids", []) if str(item).strip()]
            evidence_ids = [str(item).strip() for item in raw.get("evidence_ids", []) if str(item).strip()]
        else:
            text = str(raw).strip()
            claim_ids = []
            evidence_ids = []

        valid_claim_ids = [claim_id for claim_id in claim_ids if claim_id in claim_by_id]
        valid_evidence_ids = [
            evidence_id
            for evidence_id in evidence_ids
            if evidence_id in evidence_to_claim
            and (not valid_claim_ids or evidence_to_claim[evidence_id].claim_id in valid_claim_ids)
        ]
        if not text:
            continue
        if not valid_claim_ids and not valid_evidence_ids:
            unbound.append(text)
            continue
        if not valid_claim_ids:
            valid_claim_ids = sorted({evidence_to_claim[evidence_id].claim_id for evidence_id in valid_evidence_ids})
        if not valid_evidence_ids:
            valid_evidence_ids = [evidence_id for claim_id in valid_claim_ids for evidence_id in claim_by_id[claim_id].supporting_evidence[:2]]
        refs = f"（依据：{', '.join(valid_claim_ids[:3])}；证据：{', '.join(valid_evidence_ids[:4])}）"
        bound.append(f"{text}{refs}")
    return bound, unbound


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:8]


def _build_report_sections(markdown: str, claims: list[Claim]) -> list[ReportSection]:
    section_titles: dict[str, str] = {
        "可信度摘要": "trust_summary",
        "分析背景": "background",
        "结构化综合摘要": "structured_summary",
        "结构化建议": "structured_recommendations",
        "结构化注意事项": "structured_caveats",
        "PM 决策页": "pm_decision_page",
        "决策摘要": "decision_summary",
        "关键差异洞察": "differentiated_insights",
        "核心结论": "core_findings",
        "产品定位与能力矩阵": "comparison_matrix",
        "用户旅程 User Journey": "feature_tree",
        "定价模型 PricingModel": "pricing_model",
        "用户画像 UserPersona": "user_persona",
        "SWOT": "swot",
        "社媒舆情洞察": "social_listening",
        "PM 验收检查": "pm_acceptance",
        "机会点建议": "opportunities",
        "下一步行动清单": "next_actions",
        "不确定性与被阻断结论": "uncertainty",
        "数据来源": "sources",
        "数据来源（Resources）": "sources",
        "Agent 协作记录": "agent_trace",
    }
    section_claims: dict[str, list[str]] = {
        "core_findings": [claim.claim_id for claim in claims if claim.included_in_report and claim.verified_status == "passed"],
        "pm_decision_page": [claim.claim_id for claim in claims],
        "decision_summary": [claim.claim_id for claim in claims if claim.included_in_report and claim.verified_status == "passed"],
        "differentiated_insights": [claim.claim_id for claim in claims if claim.included_in_report and claim.verified_status == "passed"],
        "comparison_matrix": [claim.claim_id for claim in claims if claim.claim_type in {"positioning", "agent_capability", "pricing", "feature", "target_user", "security", "third_party_context"}],
        "feature_tree": [claim.claim_id for claim in claims if claim.claim_type in {"feature", "agent_capability", "security", "comparative_feature"}],
        "pricing_model": [claim.claim_id for claim in claims if claim.claim_type == "pricing"],
        "user_persona": [claim.claim_id for claim in claims if claim.claim_type == "target_user"],
        "swot": [claim.claim_id for claim in claims if claim.included_in_report or claim.verified_status != "passed"],
        "social_listening": [claim.claim_id for claim in claims if claim.claim_type == "social_sentiment"],
        "pm_acceptance": [claim.claim_id for claim in claims],
        "opportunities": [claim.claim_id for claim in claims if claim.claim_type == "opportunity"],
        "next_actions": [claim.claim_id for claim in claims if claim.included_in_report and claim.verified_status == "passed"],
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
        downgrade_reason = _semantic_downgrade_reason(claim, supporting, source_by_id) or _strictness_downgrade_reason(state.task.config.evidence_strictness, supporting, source_by_id)
        if downgrade_reason:
            claim.included_in_report = False
            claim.verified_status = "downgraded"
            claim.note = downgrade_reason
            downgraded += 1
            continue
        claim.confidence = _bounded_claim_confidence(claim.confidence, supporting, source_by_id)
    _trace(
        state,
        "EvidenceConsistencyReviewer",
        "evidence_reviewer",
        "evidence_gate_completed",
        f"Blocked {blocked} unsupported claim(s) and downgraded {downgraded} claim(s) under {state.task.config.evidence_strictness} strictness.",
    )
    return state


def _semantic_downgrade_reason(claim: Claim, evidence: list[Evidence], source_by_id: dict[str, Source]) -> str:
    if claim.product not in {"Cross-product", "Risk", "Opportunity", "External signal", "Social listening"}:
        mismatched_product = [item.evidence_id for item in evidence if item.product != claim.product]
        if mismatched_product and len(mismatched_product) == len(evidence):
            return "Downgraded by semantic evidence gate because supporting evidence belongs to a different product entity."

    comparable_types = {
        "positioning",
        "pricing",
        "feature",
        "browser_interaction",
        "target_user",
        "security",
        "agent_capability",
        "social_sentiment",
        "third_party_context",
        "contradiction",
    }
    if claim.claim_type in comparable_types:
        mismatched_type = [item.evidence_id for item in evidence if item.evidence_type != claim.claim_type]
        if mismatched_type and len(mismatched_type) == len(evidence):
            return "Downgraded by semantic evidence gate because supporting evidence type does not match the claim type."

    low_authority = []
    for item in evidence:
        source = source_by_id.get(item.source_id)
        if source and source.source_type == "irrelevant":
            low_authority.append(item.evidence_id)
    if low_authority:
        return "Downgraded by semantic evidence gate because supporting evidence failed source relevance validation."

    return ""


def _strictness_downgrade_reason(strictness: str, evidence: list[Evidence], source_by_id: dict[str, Source]) -> str:
    if strictness == "low":
        return ""
    if not evidence:
        return "Downgraded by evidence strictness because no supporting evidence was bound."

    minimum = "medium"
    best_confidence = max(CONFIDENCE_RANK.get(item.confidence, 0) for item in evidence)
    if best_confidence < CONFIDENCE_RANK[minimum]:
        return f"Downgraded by {strictness} evidence strictness because no supporting evidence met {minimum} confidence."
    return ""


def _is_official_source(evidence: Evidence, source_by_id: dict[str, Source]) -> bool:
    source = source_by_id.get(evidence.source_id)
    return bool(source and source.source_type.startswith("official"))


def _is_direct_product_source(evidence: Evidence, source_by_id: dict[str, Source]) -> bool:
    source = source_by_id.get(evidence.source_id)
    if not source:
        return False
    return source.source_type.startswith("official") or source.source_type in {"browser_walkthrough", "official_browser_walkthrough"}


def _bounded_claim_confidence(preferred_confidence: str, evidence: list[Evidence], source_by_id: dict[str, Source]) -> str:
    preferred_rank = CONFIDENCE_RANK.get(preferred_confidence, CONFIDENCE_RANK["medium"])
    if not evidence:
        return "low"

    evidence_rank = max(CONFIDENCE_RANK.get(item.confidence, 0) for item in evidence)
    bounded_rank = min(preferred_rank, evidence_rank)
    if all(not _is_direct_product_source(item, source_by_id) for item in evidence):
        bounded_rank = min(bounded_rank, CONFIDENCE_RANK["medium"])
    if bounded_rank >= CONFIDENCE_RANK["high"]:
        return "high"
    if bounded_rank >= CONFIDENCE_RANK["medium"]:
        return "medium"
    return "low"


def _is_relevant_non_official_source(evidence: Evidence, source_by_id: dict[str, Source]) -> bool:
    source = source_by_id.get(evidence.source_id)
    if not source:
        return False
    return source.source_type in {"third_party_relevant", "official_or_independent", "browser_walkthrough"} or source.source_type.startswith("social_")


def _is_source_mix_counted(source: Source) -> bool:
    return source.source_type not in {"browser_walkthrough", "official_browser_walkthrough", "fixture_walkthrough", "official_fixture_walkthrough"}


def _is_third_party_source(source: Source) -> bool:
    return (
        source.source_type in {"third_party_relevant", "independent_web", "community_forum", "review_site"}
        or source.source_type.startswith("social_")
    )


def _source_mix_summary(state: GraphState) -> dict[str, float | int | str]:
    counted = [source for source in state.sources if _is_source_mix_counted(source)]
    official_count = len([source for source in counted if source.source_type.startswith("official")])
    third_party_count = len([source for source in counted if _is_third_party_source(source)])
    total = len(counted)
    official_ratio = official_count / total if total else 0
    third_party_ratio = third_party_count / total if total else 0
    if not total:
        note = "当前没有可计入的来源，报告只能作为结构草稿。"
    elif third_party_ratio >= THIRD_PARTY_SOURCE_RATIO_TARGET:
        note = "来源结构已包含足够第三方支撑，可用于交叉验证官方叙述。"
    else:
        note = "第三方来源仍偏少，建议补充评测、社区反馈、客户案例或社媒样本后再外部发布。"
    return {
        "total": total,
        "official_count": official_count,
        "third_party_count": third_party_count,
        "official_ratio": official_ratio,
        "third_party_ratio": third_party_ratio,
        "note": note,
    }


def _build_trust_summary(state: GraphState) -> TrustSummary:
    total_claims = len(state.claims)
    bound_claims = len([claim for claim in state.claims if claim.supporting_evidence])
    official_sources = len([source for source in state.sources if source.source_type.startswith("official")])
    products = [state.task.config.target_product, *state.task.config.competitors]
    source_by_id = {source.source_id: source for source in state.sources}
    browser_interactions = [
        item
        for item in state.evidence
        if item.evidence_type == "browser_interaction"
        and item.status == "active"
        and _is_real_browser_walkthrough_source(source_by_id.get(item.source_id))
    ]
    browser_verified_products = {item.product for item in browser_interactions}
    unresolved_tickets = len([ticket for ticket in state.review_tickets if ticket.status in {"open", "accepted", "rerun_started"}])
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
        browser_interaction_count=len(browser_interactions),
        browser_verified_product_count=len(browser_verified_products),
        browser_verified_product_total=len(products),
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
    _trace(state, "Workflow", "finalize", "workflow_completed", "Finalized provider-configured LangGraph workflow result.")
    return state
