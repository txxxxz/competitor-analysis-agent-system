from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.core.nodes import (
    analyst_node,
    critic_node,
    evidence_extractor_node,
    evidence_reviewer_node,
    finalize_node,
    planner_node,
    research_node,
    source_normalizer_node,
    template_node,
    trust_summary_node,
    writer_node,
)
from app.models.schemas import GraphState, Task, WorkflowResult


def route_after_critic(state: GraphState) -> str:
    if any(ticket.status == "open" and ticket.target_node == "ResearchAgent" for ticket in state.review_tickets):
        return "research_node"
    return "writer_node"


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("planner_node", planner_node)
    graph.add_node("template_node", template_node)
    graph.add_node("research_node", research_node)
    graph.add_node("source_normalizer_node", source_normalizer_node)
    graph.add_node("evidence_extractor_node", evidence_extractor_node)
    graph.add_node("analyst_node", analyst_node)
    graph.add_node("critic_node", critic_node)
    graph.add_node("trust_summary_node", trust_summary_node)
    graph.add_node("writer_node", writer_node)
    graph.add_node("evidence_reviewer_node", evidence_reviewer_node)
    graph.add_node("finalize_node", finalize_node)

    graph.add_edge(START, "planner_node")
    graph.add_edge("planner_node", "template_node")
    graph.add_edge("template_node", "research_node")
    graph.add_edge("research_node", "source_normalizer_node")
    graph.add_edge("source_normalizer_node", "evidence_extractor_node")
    graph.add_edge("evidence_extractor_node", "analyst_node")
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
    final_state = compiled_graph.invoke(initial)
    if isinstance(final_state, dict):
        final_state = GraphState.model_validate(final_state)
    return final_state.result()
