import pytest

from app.core.graph import run_workflow
from app.models.schemas import Claim, Evidence, GraphState, Source, Task, TaskConfig


@pytest.fixture(autouse=True)
def use_mock_providers(monkeypatch):
    monkeypatch.setenv("USE_MOCK_SEARCH", "true")
    monkeypatch.setenv("USE_MOCK_LLM", "true")


def test_provider_factory_selects_anysearch_when_configured():
    from app.providers.anysearch import AnySearchProvider
    from app.providers.factory import ProviderSettings, build_provider_bundle
    from app.providers.mock_llm import MockLLMProvider

    bundle = build_provider_bundle(
        ProviderSettings(
            use_mock_search=False,
            use_mock_llm=True,
            anysearch_api_key="test-key",
            anysearch_base_url="https://api.anysearch.com/v1/search",
            anysearch_max_results=3,
            anysearch_content_types=(),
            seed_api_key="",
            seed_base_url="",
            seed_model="",
            allow_provider_fallback=True,
            allow_empty_search_fallback=True,
        )
    )

    assert isinstance(bundle.search, AnySearchProvider)
    assert isinstance(bundle.llm, MockLLMProvider)
    assert bundle.search_mode == "anysearch"
    assert bundle.llm_mode == "mock"
    assert bundle.fixture_mode is True


def test_provider_factory_falls_back_to_mock_when_anysearch_key_missing():
    from app.providers.factory import ProviderSettings, build_provider_bundle
    from app.providers.mock_search import MockSearchProvider

    bundle = build_provider_bundle(
        ProviderSettings(
            use_mock_search=False,
            use_mock_llm=True,
            anysearch_api_key="",
            anysearch_base_url="https://api.anysearch.com/v1/search",
            anysearch_max_results=5,
            anysearch_content_types=(),
            seed_api_key="",
            seed_base_url="",
            seed_model="",
            allow_provider_fallback=True,
            allow_empty_search_fallback=True,
        )
    )

    assert isinstance(bundle.search, MockSearchProvider)
    assert bundle.search_mode == "mock_fallback"
    assert bundle.warnings


def test_v1_create_task_returns_envelope_and_normalized_contract(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import routes
    from app.main import app
    from app.storage.sqlite import SQLiteStore

    monkeypatch.setattr(routes, "store", SQLiteStore(str(tmp_path / "app.db")))
    client = TestClient(app)

    response = client.post(
        "/api/v1/tasks",
        json={
            "product_domain": "generic",
            "target_product": "Notion",
            "competitors": ["Coda", "Airtable"],
            "analysis_goals": ["positioning", "pricing"],
            "report_depth": "brief",
            "evidence_strictness": "high",
            "output_audience": "product_team",
            "natural_language_notes": "Focus on collaboration.",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["data"]["status"] == "draft"
    assert body["data"]["task_id"].startswith("task_")
    assert body["data"]["task_config"]["product_domain"] == "generic"
    assert body["data"]["task_config"]["report_depth"] == "brief"
    assert body["meta"]["request_id"].startswith("req_")


def test_v1_create_task_blocks_target_in_competitors(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import routes
    from app.main import app
    from app.storage.sqlite import SQLiteStore

    monkeypatch.setattr(routes, "store", SQLiteStore(str(tmp_path / "app.db")))
    client = TestClient(app)

    response = client.post(
        "/api/v1/tasks",
        json={
            "product_domain": "ai_tools",
            "target_product": "Cursor",
            "competitors": ["GitHub Copilot", " cursor "],
            "analysis_goals": ["positioning"],
            "report_depth": "standard",
            "evidence_strictness": "high",
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["title"] == "Validation Error"
    assert body["errors"] == [
        {
            "field": "competitors",
            "message": "Target product cannot appear in competitors.",
            "code": "TARGET_IN_COMPETITORS",
        }
    ]


def test_v1_exclude_and_restore_evidence_marks_dependents_stale(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import routes
    from app.main import app
    from app.storage.sqlite import SQLiteStore

    store = SQLiteStore(str(tmp_path / "app.db"))
    monkeypatch.setattr(routes, "store", store)
    client = TestClient(app)
    task = store.create_task(
        Task(
            config=TaskConfig(
                domain="ai_tools",
                target_product="Cursor",
                competitors=["GitHub Copilot"],
                analysis_goals=["positioning", "pricing"],
            )
        )
    )
    result = run_workflow(task)
    store.save_result(result)
    included_claim = next(claim for claim in result.claims if claim.included_in_report and claim.supporting_evidence)
    evidence_id = included_claim.supporting_evidence[0]

    exclude_response = client.post(
        f"/api/v1/evidence/{evidence_id}/exclude",
        json={"reason": "Evidence is outdated.", "trigger_recompute": True},
    )

    assert exclude_response.status_code == 200
    exclude_body = exclude_response.json()["data"]
    assert exclude_body["status"] == "excluded"
    assert included_claim.claim_id in exclude_body["stale_claims"]
    assert exclude_body["report_status"] == "stale"
    loaded = store.find_result_by_evidence_id(evidence_id)
    assert loaded is not None
    excluded = next(item for item in loaded.evidence if item.evidence_id == evidence_id)
    stale_claim = next(claim for claim in loaded.claims if claim.claim_id == included_claim.claim_id)
    assert excluded.status == "excluded"
    assert excluded.excluded_reason == "Evidence is outdated."
    assert stale_claim.verified_status == "stale"
    assert stale_claim.included_in_report is False
    assert loaded.report.status == "stale"

    restore_response = client.post(f"/api/v1/evidence/{evidence_id}/restore", json={"trigger_recompute": True})

    assert restore_response.status_code == 200
    assert restore_response.json()["data"]["status"] == "active"
    restored = store.find_result_by_evidence_id(evidence_id)
    restored_evidence = next(item for item in restored.evidence if item.evidence_id == evidence_id)
    assert restored_evidence.status == "active"
    assert restored_evidence.excluded_reason == ""
    assert restored.report.status == "stale"


def test_v1_review_ticket_accept_rerun_and_resolve(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import routes
    from app.main import app
    from app.storage.sqlite import SQLiteStore

    store = SQLiteStore(str(tmp_path / "app.db"))
    monkeypatch.setattr(routes, "store", store)
    client = TestClient(app)
    task = store.create_task(
        Task(
            config=TaskConfig(
                domain="ai_tools",
                target_product="Cursor",
                competitors=["GitHub Copilot", "Windsurf", "TRAE"],
                analysis_goals=["positioning", "pricing"],
            )
        )
    )
    result = run_workflow(task)
    ticket = result.review_tickets[0]
    ticket.status = "open"
    ticket.rerun_count = 0
    ticket.max_reruns = 2
    store.save_result(result)

    accept_response = client.post(f"/api/v1/review-tickets/{ticket.ticket_id}/accept", json={"note": "Run research."})
    assert accept_response.status_code == 200
    assert accept_response.json()["data"]["status"] == "accepted"

    rerun_response = client.post(f"/api/v1/review-tickets/{ticket.ticket_id}/rerun", json={"preserve_existing_artifacts": True})
    assert rerun_response.status_code == 202
    rerun_data = rerun_response.json()["data"]
    assert rerun_data["status"] == "resolved"
    assert rerun_data["rerun_count"] == 1
    assert rerun_data["workflow_result"]["report"]["markdown"]
    assert any(event["event_type"] == "ticket_local_rerun_completed" for event in rerun_data["workflow_result"]["trace"])
    loaded_after_rerun = store.find_result_by_ticket_id(ticket.ticket_id)
    loaded_ticket = next(item for item in loaded_after_rerun.review_tickets if item.ticket_id == ticket.ticket_id)
    assert loaded_ticket.status == "resolved"
    assert loaded_after_rerun.report is not None

    resolve_response = client.post(
        f"/api/v1/review-tickets/{ticket.ticket_id}/resolve",
        json={"resolution_summary": "Supplemental source collected."},
    )
    assert resolve_response.status_code == 200
    resolve_data = resolve_response.json()["data"]
    assert resolve_data["status"] == "resolved"
    assert resolve_data["resolution_summary"] == "Supplemental source collected."
    assert resolve_data["resolved_at"]


def test_v1_review_ticket_rerun_cap_blocks_task(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import routes
    from app.main import app
    from app.storage.sqlite import SQLiteStore

    store = SQLiteStore(str(tmp_path / "app.db"))
    monkeypatch.setattr(routes, "store", store)
    client = TestClient(app)
    task = store.create_task(
        Task(
            config=TaskConfig(
                domain="ai_tools",
                target_product="Cursor",
                competitors=["TRAE"],
                analysis_goals=["positioning", "pricing"],
            )
        )
    )
    result = run_workflow(task)
    ticket = result.review_tickets[0]
    ticket.status = "accepted"
    ticket.rerun_count = 1
    ticket.max_reruns = 1
    store.save_result(result)

    response = client.post(f"/api/v1/review-tickets/{ticket.ticket_id}/rerun", json={"preserve_existing_artifacts": True})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "blocked"
    assert data["rerun_count"] == 1
    loaded = store.find_result_by_ticket_id(ticket.ticket_id)
    assert loaded.task.status == "blocked"
    assert loaded.review_tickets[0].status == "blocked"


def test_v1_review_ticket_mark_unavailable_updates_claim_and_report(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import routes
    from app.main import app
    from app.storage.sqlite import SQLiteStore

    store = SQLiteStore(str(tmp_path / "app.db"))
    monkeypatch.setattr(routes, "store", store)
    client = TestClient(app)
    task = store.create_task(
        Task(
            config=TaskConfig(
                domain="ai_tools",
                target_product="Cursor",
                competitors=["GitHub Copilot", "Windsurf", "TRAE"],
                analysis_goals=["positioning", "pricing"],
            )
        )
    )
    result = run_workflow(task)
    ticket = result.review_tickets[0]
    ticket.status = "open"
    store.save_result(result)

    response = client.post(f"/api/v1/review-tickets/{ticket.ticket_id}/mark-unavailable", json={"reason": "Official evidence is unavailable."})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "resolved"
    claim = next(item for item in data["workflow_result"]["claims"] if item["product"] == ticket.product and item["claim_type"] == ticket.missing_evidence_type)
    assert claim["verified_status"] == "unsupported"
    assert claim["included_in_report"] is False
    assert "Official evidence is unavailable." in data["workflow_result"]["report"]["markdown"]


def test_v1_review_ticket_downgrade_updates_claim_and_report(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import routes
    from app.main import app
    from app.storage.sqlite import SQLiteStore

    store = SQLiteStore(str(tmp_path / "app.db"))
    monkeypatch.setattr(routes, "store", store)
    client = TestClient(app)
    task = store.create_task(
        Task(
            config=TaskConfig(
                domain="ai_tools",
                target_product="Cursor",
                competitors=["GitHub Copilot", "Windsurf", "TRAE"],
                analysis_goals=["positioning", "pricing"],
            )
        )
    )
    result = run_workflow(task)
    ticket = result.review_tickets[0]
    ticket.status = "open"
    store.save_result(result)

    response = client.post(f"/api/v1/review-tickets/{ticket.ticket_id}/downgrade", json={"reason": "Evidence is weaker than required."})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "resolved"
    claim = next(item for item in data["workflow_result"]["claims"] if item["product"] == ticket.product and item["claim_type"] == ticket.missing_evidence_type)
    assert claim["verified_status"] == "downgraded"
    assert claim["included_in_report"] is False
    assert "Evidence is weaker than required." in data["workflow_result"]["report"]["markdown"]


def test_v1_report_sections_and_export_respect_status(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import routes
    from app.main import app
    from app.storage.sqlite import SQLiteStore

    store = SQLiteStore(str(tmp_path / "app.db"))
    monkeypatch.setattr(routes, "store", store)
    client = TestClient(app)
    task = store.create_task(
        Task(
            config=TaskConfig(
                domain="ai_tools",
                target_product="Cursor",
                competitors=["GitHub Copilot", "Windsurf"],
                analysis_goals=["positioning", "pricing"],
            )
        )
    )
    result = run_workflow(task)
    store.save_result(result)

    report_response = client.get(f"/api/v1/tasks/{task.task_id}/report")
    assert report_response.status_code == 200
    report = report_response.json()["data"]
    assert report["status"] == "passed"
    assert report["claim_count"] == len(result.claims)
    assert report["evidence_coverage_rate"] > 0

    sections_response = client.get(f"/api/v1/tasks/{task.task_id}/report/sections")
    assert sections_response.status_code == 200
    sections = sections_response.json()["data"]
    assert {section["section_key"] for section in sections} >= {"trust_summary", "core_findings", "sources"}

    export_response = client.get(f"/api/v1/tasks/{task.task_id}/report/export")
    assert export_response.status_code == 200
    export = export_response.json()["data"]
    assert export["filename"] == f"{task.task_id}_report.md"
    assert export["content_type"] == "text/markdown"
    assert export["status"] == "passed"
    assert export["warning"] is None


def test_v1_report_export_blocks_stale_without_draft_override(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import routes
    from app.main import app
    from app.storage.sqlite import SQLiteStore

    store = SQLiteStore(str(tmp_path / "app.db"))
    monkeypatch.setattr(routes, "store", store)
    client = TestClient(app)
    task = store.create_task(
        Task(
            config=TaskConfig(
                domain="ai_tools",
                target_product="Cursor",
                competitors=["GitHub Copilot"],
                analysis_goals=["positioning", "pricing"],
            )
        )
    )
    result = run_workflow(task)
    store.save_result(result)
    claim = next(item for item in result.claims if item.included_in_report and item.supporting_evidence)
    evidence_id = claim.supporting_evidence[0]
    client.post(f"/api/v1/evidence/{evidence_id}/exclude", json={"reason": "Outdated."})

    blocked_export = client.get(f"/api/v1/tasks/{task.task_id}/report/export")
    assert blocked_export.status_code == 409

    draft_export = client.get(f"/api/v1/tasks/{task.task_id}/report/export?allow_draft=true")
    assert draft_export.status_code == 200
    draft = draft_export.json()["data"]
    assert draft["status"] == "stale"
    assert draft["warning"] == "Draft export: report status is stale."
    assert draft["content"].startswith("> Draft export")
    reloaded = store.get_result(task.task_id)
    assert any(section.status == "stale" for section in reloaded.report.sections)


def test_v1_run_stream_emits_trace_and_saves_result(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import routes
    from app.main import app
    from app.storage.sqlite import SQLiteStore

    store = SQLiteStore(str(tmp_path / "app.db"))
    monkeypatch.setattr(routes, "store", store)
    client = TestClient(app)
    task = store.create_task(
        Task(
            config=TaskConfig(
                domain="ai_tools",
                target_product="Cursor",
                competitors=["GitHub Copilot"],
                analysis_goals=["positioning", "pricing"],
            )
        )
    )

    with client.stream("GET", f"/api/v1/tasks/{task.task_id}/run/stream") as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: workflow_started" in body
    assert "event: trace" in body
    assert "event: result" in body
    assert "event: workflow_completed" in body
    loaded = store.get_result(task.task_id)
    assert loaded is not None
    assert loaded.report is not None
    assert loaded.trace


def test_ai_tools_workflow_has_review_loop_and_report():
    task = Task(
        config=TaskConfig(
            domain="ai_tools",
            target_product="Cursor",
            competitors=["GitHub Copilot", "Windsurf", "TRAE"],
            analysis_goals=["positioning", "pricing", "agent_capability"],
            evidence_strictness="high",
        )
    )

    result = run_workflow(task)

    assert result.report is not None
    assert result.review_tickets
    assert any(ticket.target_node == "ResearchAgent" for ticket in result.review_tickets)
    assert any(ticket.product == "TRAE" and ticket.missing_evidence_type == "pricing" for ticket in result.review_tickets)
    assert any(event.event_type == "supplemental_search" for event in result.trace)
    assert any(query.is_supplemental and query.product == "TRAE" for query in result.search_plan.queries)
    assert result.trust_summary is not None
    assert result.trust_summary.claim_evidence_binding_rate > 0
    assert all(claim.supporting_evidence for claim in result.claims if claim.included_in_report)
    assert any(claim.claim_type == "comparative_positioning" for claim in result.claims)
    assert "可信度摘要" in result.report.markdown


def test_workflow_falls_back_to_fixture_when_real_search_returns_empty(monkeypatch):
    from app.core import nodes
    from app.models.schemas import SearchQuery
    from app.providers.factory import ProviderBundle
    from app.providers.llm import LLMProvider
    from app.providers.search import SearchProvider

    class EmptySearchProvider(SearchProvider):
        provider_name = "AnySearchProvider"

        def search(self, task_id: str, query: SearchQuery, supplement: bool = False) -> list[dict]:
            return []

    class FakeLLMProvider(LLMProvider):
        provider_name = "MockLLMProvider"

        def complete_structured(self, purpose: str, payload: dict) -> dict:
            return {}

    monkeypatch.setattr(
        nodes,
        "build_provider_bundle",
        lambda: ProviderBundle(
            search=EmptySearchProvider(),
            llm=FakeLLMProvider(),
            fixture_mode=False,
            search_mode="anysearch",
            llm_mode="mock",
            allow_provider_fallback=True,
            allow_empty_search_fallback=True,
        ),
    )
    task = Task(
        config=TaskConfig(
            domain="ai_tools",
            target_product="Cursor",
            competitors=["GitHub Copilot"],
            analysis_goals=["positioning", "pricing"],
        )
    )

    result = run_workflow(task)

    assert result.sources
    assert any(call.tool == "MockSearchProvider" and "fallback=empty_results" in call.results_summary for call in result.tool_calls)
    assert any(event.event_type == "provider_empty_result_fallback" for event in result.trace)


def test_high_strictness_downgrades_non_official_high_confidence_evidence():
    from app.core.nodes import evidence_reviewer_node

    state = _strictness_state("high", source_type="community_forum", confidence="high")

    reviewed = evidence_reviewer_node(state)
    claim = reviewed.claims[0]

    assert claim.verified_status == "downgraded"
    assert claim.included_in_report is False
    assert "high evidence strictness" in claim.note
    assert "official source" in claim.note


def test_standard_strictness_accepts_medium_non_official_evidence():
    from app.core.nodes import evidence_reviewer_node

    state = _strictness_state("standard", source_type="community_forum", confidence="medium")

    reviewed = evidence_reviewer_node(state)
    claim = reviewed.claims[0]

    assert claim.verified_status == "passed"
    assert claim.included_in_report is True


def test_low_strictness_accepts_low_confidence_bound_evidence():
    from app.core.nodes import evidence_reviewer_node

    state = _strictness_state("low", source_type="community_forum", confidence="low")

    reviewed = evidence_reviewer_node(state)
    claim = reviewed.claims[0]

    assert claim.verified_status == "passed"
    assert claim.included_in_report is True


def test_writer_uses_structured_llm_report_enhancement(monkeypatch):
    from app.core import nodes
    from app.core.nodes import writer_node
    from app.providers.factory import ProviderBundle
    from app.providers.llm import LLMProvider
    from app.providers.mock_search import MockSearchProvider

    class FakeSeedProvider(LLMProvider):
        provider_name = "SeedLLMProvider"

        def complete_structured(self, purpose: str, payload: dict) -> dict:
            assert purpose == "report_enhancement"
            assert payload["task"]["target_product"] == "Cursor"
            return {
                "executive_summary": ["Seed synthesized an executive summary."],
                "strategic_recommendations": ["Seed recommended validating pricing evidence."],
                "caveats": ["Seed caveat stays evidence-bound."],
            }

    monkeypatch.setattr(
        nodes,
        "build_provider_bundle",
        lambda: ProviderBundle(
            search=MockSearchProvider(),
            llm=FakeSeedProvider(),
            fixture_mode=False,
            search_mode="mock",
            llm_mode="seed",
            allow_provider_fallback=True,
            allow_empty_search_fallback=True,
        ),
    )
    state = _strictness_state("high", source_type="official_homepage", confidence="high")

    written = writer_node(state)

    assert written.report is not None
    assert "## 结构化综合摘要" in written.report.markdown
    assert "Seed synthesized an executive summary." in written.report.markdown
    assert any(call.tool == "SeedLLMProvider" and call.operation == "complete_structured" for call in written.tool_calls)
    assert any(event.event_type == "llm_enhancement_applied" for event in written.trace)


def test_analyst_uses_structured_llm_claim_enrichment(monkeypatch):
    from app.core import nodes
    from app.core.nodes import analyst_node
    from app.providers.factory import ProviderBundle
    from app.providers.llm import LLMProvider
    from app.providers.mock_search import MockSearchProvider

    class FakeSeedProvider(LLMProvider):
        provider_name = "SeedLLMProvider"

        def complete_structured(self, purpose: str, payload: dict) -> dict:
            assert purpose == "claim_enrichment"
            evidence_id = payload["evidence"][0]["evidence_id"]
            return {
                "claims": [
                    {
                        "product": "Cross-product",
                        "claim_type": "llm_synthesis",
                        "claim": "Seed added a bound synthesis claim",
                        "supporting_evidence": [evidence_id],
                        "confidence": "medium",
                    },
                    {
                        "product": "Cross-product",
                        "claim_type": "llm_synthesis",
                        "claim": "Seed attempted an unsupported claim",
                        "supporting_evidence": ["missing_evidence"],
                        "confidence": "high",
                    },
                ]
            }

    monkeypatch.setattr(
        nodes,
        "build_provider_bundle",
        lambda: ProviderBundle(
            search=MockSearchProvider(),
            llm=FakeSeedProvider(),
            fixture_mode=False,
            search_mode="mock",
            llm_mode="seed",
            allow_provider_fallback=True,
            allow_empty_search_fallback=True,
        ),
    )
    state = _strictness_state("high", source_type="official_homepage", confidence="high")
    state.sources.append(
        Source(
            source_id="src_test_2",
            task_id=state.task.task_id,
            title="Second official source",
            url="https://example.com/second",
            source_type="official_homepage",
            product="GitHub Copilot",
            query="GitHub Copilot positioning",
            confidence="high",
            risk="Synthetic test source.",
            content="GitHub Copilot positioning discussion.",
        )
    )
    state.evidence.append(
        Evidence(
            evidence_id="ev_test_2",
            task_id=state.task.task_id,
            source_id="src_test_2",
            product="GitHub Copilot",
            evidence_type="positioning",
            summary="GitHub Copilot is discussed as an AI pair programmer",
            quote_or_locator="Official page",
            confidence="high",
            risk="Synthetic test source.",
        )
    )

    enriched = analyst_node(state)

    assert any(claim.claim_type == "llm_synthesis" and "bound synthesis" in claim.claim for claim in enriched.claims)
    assert not any("unsupported claim" in claim.claim for claim in enriched.claims)
    assert any(call.tool == "SeedLLMProvider" and call.query == "claim_enrichment" for call in enriched.tool_calls)
    assert any(event.event_type == "llm_claim_enrichment_applied" for event in enriched.trace)


def test_critic_uses_structured_llm_review_ticket_suggestions(monkeypatch):
    from app.core import nodes
    from app.core.nodes import critic_node
    from app.providers.factory import ProviderBundle
    from app.providers.llm import LLMProvider
    from app.providers.mock_search import MockSearchProvider

    class FakeSeedProvider(LLMProvider):
        provider_name = "SeedLLMProvider"

        def complete_structured(self, purpose: str, payload: dict) -> dict:
            assert purpose == "review_ticket_suggestions"
            assert payload["task"]["target_product"] == "Cursor"
            return {
                "review_tickets": [
                    {
                        "product": "Cursor",
                        "missing_evidence_type": "security",
                        "target_node": "ResearchAgent",
                        "reason": "Cursor security evidence should be verified.",
                        "required_action": "Find official security documentation for Cursor.",
                        "severity": "medium",
                        "preferred_source_type": "official_docs",
                    },
                    {
                        "product": "NotInScope",
                        "missing_evidence_type": "pricing",
                        "target_node": "ResearchAgent",
                        "reason": "Out-of-scope product should be ignored.",
                        "required_action": "Ignore this.",
                        "severity": "high",
                    },
                ]
            }

    monkeypatch.setattr(
        nodes,
        "build_provider_bundle",
        lambda: ProviderBundle(
            search=MockSearchProvider(),
            llm=FakeSeedProvider(),
            fixture_mode=False,
            search_mode="mock",
            llm_mode="seed",
            allow_provider_fallback=True,
            allow_empty_search_fallback=True,
        ),
    )
    state = _strictness_state("high", source_type="official_homepage", confidence="high")
    state.evidence.append(
        Evidence(
            evidence_id="ev_price",
            task_id=state.task.task_id,
            source_id=state.sources[0].source_id,
            product="Cursor",
            evidence_type="pricing",
            summary="Cursor publishes pricing.",
            quote_or_locator="Pricing page",
            confidence="high",
            risk="Synthetic test source.",
        )
    )
    state.max_loops = 0

    reviewed = critic_node(state)

    assert any(ticket.product == "Cursor" and ticket.missing_evidence_type == "security" for ticket in reviewed.review_tickets)
    assert not any(ticket.product == "NotInScope" for ticket in reviewed.review_tickets)
    assert any(call.tool == "SeedLLMProvider" and call.query == "review_ticket_suggestions" for call in reviewed.tool_calls)
    assert any(event.event_type == "llm_review_ticket_suggestions_applied" for event in reviewed.trace)


def _strictness_state(strictness: str, source_type: str, confidence: str) -> GraphState:
    task = Task(
        config=TaskConfig(
            domain="ai_tools",
            target_product="Cursor",
            competitors=["GitHub Copilot"],
            analysis_goals=["positioning"],
            evidence_strictness=strictness,
        )
    )
    source = Source(
        source_id="src_test",
        task_id=task.task_id,
        title="Community post",
        url="https://example.com/community",
        source_type=source_type,
        product="Cursor",
        query="Cursor positioning",
        confidence=confidence,
        risk="Synthetic test source.",
        content="Cursor positioning discussion.",
    )
    evidence = Evidence(
        evidence_id="ev_test",
        task_id=task.task_id,
        source_id=source.source_id,
        product="Cursor",
        evidence_type="positioning",
        summary="Cursor is discussed as an AI-native editor",
        quote_or_locator="Community thread",
        confidence=confidence,
        risk=source.risk,
    )
    claim = Claim(
        task_id=task.task_id,
        product="Cursor",
        claim="Cursor is discussed as an AI-native editor.",
        claim_type="positioning",
        supporting_evidence=[evidence.evidence_id],
        confidence=confidence,
        verified_status="passed",
        included_in_report=True,
    )
    return GraphState(task=task, sources=[source], evidence=[evidence], claims=[claim])


def test_sqlite_store_round_trip(tmp_path):
    from app.storage.sqlite import SQLiteStore

    store = SQLiteStore(str(tmp_path / "app.db"))
    task = store.create_task(
        Task(
            config=TaskConfig(
                domain="general_product",
                target_product="Notion",
                competitors=["Coda", "Airtable"],
                analysis_goals=["positioning", "pricing"],
            )
        )
    )
    result = run_workflow(task)
    store.save_result(result)
    loaded = store.get_result(task.task_id)

    assert loaded is not None
    assert loaded.task.task_id == task.task_id
    assert loaded.report is not None
    assert loaded.sources


def test_workflow_report_contains_pm_scoring_sections_and_trace_metadata():
    task = Task(
        config=TaskConfig(
            domain="ai_tools",
            target_product="Cursor",
            competitors=["GitHub Copilot", "Windsurf", "TRAE"],
            analysis_goals=["positioning", "pricing", "feature", "target_users", "security"],
        )
    )

    result = run_workflow(task)

    assert result.report is not None
    assert result.report.feature_tree is not None
    assert result.report.pricing_model is not None
    assert result.report.user_personas
    assert result.report.swot is not None
    section_keys = {section.section_key for section in result.report.sections}
    assert {"feature_tree", "pricing_model", "user_persona", "swot"}.issubset(section_keys)
    assert result.trust_summary is not None
    assert result.trust_summary.provider_mode_label == "Demo fixture run"
    provider_trace = [event for event in result.trace if event.provider or event.prompt_name]
    assert provider_trace
    assert any(event.input_summary and event.output_summary for event in provider_trace)
    assert any(event.token_count is not None and event.latency_ms is not None for event in provider_trace)
