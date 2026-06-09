from __future__ import annotations

from collections.abc import Iterator

from langgraph.graph import END, START, StateGraph

from app.core.nodes import (
    analyst_node,
    critic_node,
    evidence_extractor_node,
    evidence_reviewer_node,
    finalize_node,
    planner_node,
    interaction_node,
    research_node,
    social_listening_node,
    source_normalizer_node,
    template_node,
    trust_summary_node,
    writer_node,
)
from app.models.schemas import AgentTraceEvent, GraphState, Task, WorkflowResult, now_iso


def route_after_critic(state: GraphState) -> str:
    if state.loop_count >= state.max_loops:
        return "writer_node"
    if any(ticket.status == "open" and ticket.target_node == "ResearchAgent" for ticket in state.review_tickets):
        return "research_node"
    return "writer_node"


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("planner_node", planner_node)
    graph.add_node("template_node", template_node)
    graph.add_node("research_node", research_node)
    graph.add_node("social_listening_node", social_listening_node)
    graph.add_node("source_normalizer_node", source_normalizer_node)
    graph.add_node("evidence_extractor_node", evidence_extractor_node)
    graph.add_node("interaction_node", interaction_node)
    graph.add_node("analyst_node", analyst_node)
    graph.add_node("critic_node", critic_node)
    graph.add_node("trust_summary_node", trust_summary_node)
    graph.add_node("writer_node", writer_node)
    graph.add_node("evidence_reviewer_node", evidence_reviewer_node)
    graph.add_node("finalize_node", finalize_node)

    graph.add_edge(START, "planner_node")
    graph.add_edge("planner_node", "template_node")
    graph.add_edge("template_node", "research_node")
    graph.add_edge("research_node", "social_listening_node")
    graph.add_edge("social_listening_node", "source_normalizer_node")
    graph.add_edge("source_normalizer_node", "evidence_extractor_node")
    graph.add_edge("evidence_extractor_node", "interaction_node")
    graph.add_edge("interaction_node", "analyst_node")
    graph.add_edge("analyst_node", "critic_node")
    graph.add_conditional_edges("critic_node", route_after_critic, {"research_node": "research_node", "writer_node": "evidence_reviewer_node"})
    graph.add_edge("evidence_reviewer_node", "trust_summary_node")
    graph.add_edge("trust_summary_node", "writer_node")
    graph.add_edge("writer_node", "finalize_node")
    graph.add_edge("finalize_node", END)
    return graph.compile()


compiled_graph = build_graph()


def run_workflow(task: Task) -> WorkflowResult:
    initial = GraphState(task=task)
    final_state = compiled_graph.invoke(initial, config={"recursion_limit": 80})
    if isinstance(final_state, dict):
        final_state = GraphState.model_validate(final_state)
    return final_state.result()


def stream_workflow(task: Task) -> Iterator[dict]:
    initial = GraphState(task=task)
    seen_trace_events = 0
    final_state = initial
    yield {"event": "workflow_started", "data": {"task_id": task.task_id, "status": "running"}}
    for state_update in compiled_graph.stream(initial, stream_mode="values", config={"recursion_limit": 80}):
        final_state = GraphState.model_validate(state_update)
        new_events = final_state.trace[seen_trace_events:]
        for trace_event in new_events:
            yield {"event": "trace", "data": trace_event.model_dump(mode="json")}
        seen_trace_events = len(final_state.trace)
        yield {
            "event": "state",
            "data": {
                "task_id": task.task_id,
                "trace_count": len(final_state.trace),
                "source_count": len(final_state.sources),
                "evidence_count": len(final_state.evidence),
                "claim_count": len(final_state.claims),
                "ticket_count": len(final_state.review_tickets),
            },
        }
    yield {"event": "result", "data": final_state.result().model_dump(mode="json")}
    yield {"event": "workflow_completed", "data": {"task_id": task.task_id, "status": final_state.task.status}}


def rerun_review_ticket(result: WorkflowResult, ticket_id: str) -> WorkflowResult:
    state = GraphState(
        task=result.task,
        brief=result.brief,
        template=result.template,
        search_plan=result.search_plan,
        tool_calls=result.tool_calls,
        sources=result.sources,
        evidence=result.evidence,
        claims=result.claims,
        review_tickets=result.review_tickets,
        trace=result.trace,
        trust_summary=result.trust_summary,
        report=result.report,
    )
    ticket = next(ticket for ticket in state.review_tickets if ticket.ticket_id == ticket_id)
    ticket.status = "open"
    if ticket.target_node == "InteractionAgent":
        state = interaction_node(state)
    else:
        state = research_node(state)
        state = source_normalizer_node(state)
        state = evidence_extractor_node(state)
        state = interaction_node(state)
    state = analyst_node(state)
    state = evidence_reviewer_node(state)
    state = trust_summary_node(state)
    state = writer_node(state)
    return state.result()


def apply_review_ticket_claim_decision(result: WorkflowResult, ticket_id: str, claim_status: str, summary: str) -> WorkflowResult:
    state = GraphState(
        task=result.task,
        brief=result.brief,
        template=result.template,
        search_plan=result.search_plan,
        tool_calls=result.tool_calls,
        sources=result.sources,
        evidence=result.evidence,
        claims=result.claims,
        review_tickets=result.review_tickets,
        trace=result.trace,
        trust_summary=result.trust_summary,
        report=result.report,
    )
    ticket = next(ticket for ticket in state.review_tickets if ticket.ticket_id == ticket_id)
    affected_claims = [
        claim
        for claim in state.claims
        if (not ticket.product or claim.product == ticket.product)
        and (not ticket.missing_evidence_type or claim.claim_type == ticket.missing_evidence_type)
    ]
    for claim in affected_claims:
        claim.verified_status = claim_status
        claim.included_in_report = False
        claim.note = summary
    ticket.status = "resolved"
    ticket.resolution_summary = summary
    ticket.resolved_at = now_iso()
    state.trace.append(
        AgentTraceEvent(
            task_id=state.task.task_id,
            agent="ReviewTicketService",
            node="review_ticket",
            event_type=f"ticket_{claim_status}",
            summary=summary,
            related_ids=[ticket_id, *[claim.claim_id for claim in affected_claims]],
        )
    )
    state = trust_summary_node(state)
    state = writer_node(state)
    return state.result()
