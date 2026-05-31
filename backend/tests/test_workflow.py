from app.core.graph import run_workflow
from app.models.schemas import Task, TaskConfig


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
