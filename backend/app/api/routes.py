from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from app.core.graph import apply_review_ticket_claim_decision, rerun_review_ticket, run_workflow, stream_workflow
from app.models.schemas import AgentTraceEvent, Task, TaskConfig, WorkflowResult, now_iso, validate_task_config_fields
from app.storage.sqlite import SQLiteStore


router = APIRouter()
store = SQLiteStore()


def request_id() -> str:
    return f"req_{uuid4().hex[:10]}"


def api_response(data):
    return {"data": data, "meta": {"request_id": request_id()}}


def problem_response(status_code: int, title: str, detail: str, errors: list[dict[str, str]] | None = None) -> JSONResponse:
    body = {
        "type": "https://api.local/errors/validation-error" if status_code == 422 else "https://api.local/errors/request-error",
        "title": title,
        "status": status_code,
        "detail": detail,
        "request_id": request_id(),
    }
    if errors:
        body["errors"] = errors
    return JSONResponse(status_code=status_code, content=body)


def _adapt_v1_task_config(payload: dict) -> dict:
    adapted = dict(payload)
    if "product_domain" in adapted and "domain" not in adapted:
        product_domain = adapted.pop("product_domain")
        adapted["domain"] = "general_product" if product_domain == "generic" else product_domain
    if "report_depth" in adapted and "depth" not in adapted:
        report_depth = adapted.pop("report_depth")
        adapted["depth"] = "quick" if report_depth == "brief" else report_depth
    if "output_audience" in adapted and "audience" not in adapted:
        adapted["audience"] = adapted.pop("output_audience")
    if "natural_language_notes" in adapted and "notes" not in adapted:
        adapted["notes"] = adapted.pop("natural_language_notes")
    return adapted


def _validation_errors_from_exception(error: ValidationError) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for item in error.errors():
        field = ".".join(str(part) for part in item.get("loc", [])) or "body"
        errors.append(
            {
                "field": field,
                "message": item.get("msg", "Invalid value."),
                "code": item.get("type", "VALIDATION_ERROR").upper(),
            }
        )
    return errors


def _mark_evidence_dependents_stale(result: WorkflowResult, evidence_id: str, reason: str) -> tuple[list[str], str]:
    stale_claims: list[str] = []
    for claim in result.claims:
        if evidence_id in claim.supporting_evidence:
            claim.verified_status = "stale"
            claim.included_in_report = False
            claim.note = reason
            stale_claims.append(claim.claim_id)
    if result.report:
        result.report.status = "stale"
        stale_claim_set = set(stale_claims)
        for section in result.report.sections:
            if stale_claim_set.intersection(section.claim_ids):
                section.status = "stale"
    result.trace.append(
        AgentTraceEvent(
            task_id=result.task.task_id,
            agent="User",
            node="evidence",
            event_type="artifact_stale",
            summary=reason,
            related_ids=[evidence_id, *stale_claims],
        )
    )
    return stale_claims, result.report.status if result.report else "stale"


def _ticket_response(ticket):
    return {
        "ticket_id": ticket.ticket_id,
        "task_id": ticket.task_id,
        "source_node": ticket.source_node or ticket.reviewer,
        "target_node": ticket.target_node,
        "product": ticket.product,
        "missing_evidence_type": ticket.missing_evidence_type,
        "preferred_source_type": ticket.preferred_source_type,
        "severity": ticket.severity,
        "status": ticket.status,
        "reason": ticket.reason,
        "required_action": ticket.required_action,
        "affected_artifacts": ticket.affected_artifacts,
        "rerun_count": ticket.rerun_count,
        "max_reruns": ticket.max_reruns,
        "resolution_summary": ticket.resolution_summary or ticket.resolution_note,
        "resolved_at": ticket.resolved_at,
    }


def _report_summary(report):
    return {
        "report_id": report.report_id,
        "task_id": report.task_id,
        "title": report.title,
        "status": report.status,
        "markdown": report.markdown,
        "claim_count": report.claim_count,
        "unsupported_claim_count": report.unsupported_claim_count,
        "stale_claim_count": report.stale_claim_count,
        "evidence_coverage_rate": report.evidence_coverage_rate,
        "feature_tree": report.feature_tree.model_dump(mode="json") if report.feature_tree else None,
        "pricing_model": report.pricing_model.model_dump(mode="json") if report.pricing_model else None,
        "user_personas": [persona.model_dump(mode="json") for persona in report.user_personas],
        "swot": report.swot.model_dump(mode="json") if report.swot else None,
        "created_at": report.created_at,
    }


def _sse_message(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


DEMO_TASKS = [
    {
        "id": "ai_tools_cursor",
        "name": "AI 工具增强 Demo",
        "description": "Cursor vs GitHub Copilot vs Windsurf vs TRAE，包含 pricing evidence 补采回环。",
        "config": TaskConfig(
            domain="ai_tools",
            target_product="Cursor",
            competitors=["GitHub Copilot", "Windsurf", "TRAE"],
            analysis_goals=["positioning", "ai_capability", "agent_capability", "developer_workflow", "pricing"],
            depth="standard",
            evidence_strictness="high",
            audience="AI tool product team",
        ),
    },
    {
        "id": "generic_notion",
        "name": "通用产品 Demo",
        "description": "Notion vs Coda vs Airtable，展示通用模板与非 AI 工具 fallback。",
        "config": TaskConfig(
            domain="general_product",
            target_product="Notion",
            competitors=["Coda", "Airtable"],
            analysis_goals=["positioning", "collaboration", "pricing", "target_users"],
            depth="standard",
            evidence_strictness="standard",
            audience="product manager",
        ),
    },
]


@router.get("/demo-tasks")
def demo_tasks():
    return DEMO_TASKS


@router.get("/tasks")
def list_tasks():
    return store.list_tasks()


@router.post("/tasks")
def create_task(config: TaskConfig):
    errors = validate_task_config_fields(config)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    task = Task(config=config)
    store.create_task(task)
    return task


@router.post("/v1/tasks", status_code=status.HTTP_201_CREATED)
async def create_task_v1(request: Request):
    payload = await request.json()
    adapted = _adapt_v1_task_config(payload)
    contract_errors = validate_task_config_fields(SimpleNamespace(**adapted))
    if contract_errors:
        return problem_response(
            422,
            "Validation Error",
            "Task config validation failed.",
            contract_errors,
        )

    try:
        config = TaskConfig(**adapted)
    except ValidationError as exc:
        return problem_response(
            422,
            "Validation Error",
            "Task config validation failed.",
            _validation_errors_from_exception(exc),
        )

    task = Task(config=config)
    store.create_task(task)
    return api_response(
        {
            "task_id": task.task_id,
            "status": "draft",
            "task_config": {
                "product_domain": "generic" if config.domain == "general_product" else config.domain,
                "target_product": config.target_product,
                "competitors": config.competitors,
                "analysis_goals": config.analysis_goals,
                "report_depth": "brief" if config.depth == "quick" else config.depth,
                "evidence_strictness": config.evidence_strictness,
                "output_audience": config.audience,
                "natural_language_notes": config.notes,
            },
        }
    )


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    result = store.get_result(task_id)
    if result:
        return result
    task = store.get_task(task_id)
    if task:
        return task
    raise HTTPException(status_code=404, detail="Task not found")


@router.post("/tasks/{task_id}/run", response_model=WorkflowResult)
def run_task(task_id: str):
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = "running"
    result = run_workflow(task)
    store.save_result(result)
    return result


@router.get("/v1/tasks/{task_id}/run/stream")
def stream_task_run_v1(task_id: str):
    task = store.get_task(task_id)
    if not task:
        return problem_response(404, "Not Found", "Task not found.")

    def event_generator():
        task.status = "running"
        final_result = None
        try:
            for event in stream_workflow(task):
                if event["event"] == "result":
                    final_result = WorkflowResult.model_validate(event["data"])
                yield _sse_message(event["event"], event["data"])
            if final_result:
                store.save_result(final_result)
        except Exception as exc:
            task.status = "failed"
            yield _sse_message("workflow_error", {"task_id": task_id, "message": str(exc)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/tasks/{task_id}/trace")
def get_trace(task_id: str):
    result = store.get_result(task_id)
    if not result:
        raise HTTPException(status_code=404, detail="Task has no run result")
    return result.trace


@router.get("/tasks/{task_id}/evidence")
def get_evidence(task_id: str):
    result = store.get_result(task_id)
    if not result:
        raise HTTPException(status_code=404, detail="Task has no run result")
    return result.evidence


@router.get("/v1/tasks/{task_id}/evidence")
def get_evidence_v1(task_id: str):
    result = store.get_result(task_id)
    if not result:
        return problem_response(404, "Not Found", "Task has no run result.")
    return api_response(result.evidence)


@router.post("/v1/evidence/{evidence_id}/exclude")
async def exclude_evidence_v1(evidence_id: str, request: Request):
    payload = await request.json()
    result = store.find_result_by_evidence_id(evidence_id)
    if not result:
        return problem_response(404, "Not Found", "Evidence not found.")

    evidence = next(item for item in result.evidence if item.evidence_id == evidence_id)
    evidence.status = "excluded"
    evidence.excluded_reason = str(payload.get("reason") or "Excluded by user.")
    stale_claims, report_status = _mark_evidence_dependents_stale(
        result,
        evidence_id,
        f"Marked stale because evidence {evidence_id} was excluded.",
    )
    store.save_result(result)
    return api_response(
        {
            "evidence_id": evidence_id,
            "status": evidence.status,
            "stale_claims": stale_claims,
            "report_status": report_status,
        }
    )


@router.post("/v1/evidence/{evidence_id}/restore")
async def restore_evidence_v1(evidence_id: str, request: Request):
    await request.json()
    result = store.find_result_by_evidence_id(evidence_id)
    if not result:
        return problem_response(404, "Not Found", "Evidence not found.")

    evidence = next(item for item in result.evidence if item.evidence_id == evidence_id)
    evidence.status = "active"
    evidence.excluded_reason = ""
    stale_claims, report_status = _mark_evidence_dependents_stale(
        result,
        evidence_id,
        f"Marked stale because evidence {evidence_id} was restored and needs re-review.",
    )
    store.save_result(result)
    return api_response(
        {
            "evidence_id": evidence_id,
            "status": evidence.status,
            "stale_claims": stale_claims,
            "report_status": report_status,
        }
    )


@router.get("/tasks/{task_id}/claims")
def get_claims(task_id: str):
    result = store.get_result(task_id)
    if not result:
        raise HTTPException(status_code=404, detail="Task has no run result")
    return result.claims


@router.get("/v1/tasks/{task_id}/review-tickets")
def get_review_tickets_v1(task_id: str):
    result = store.get_result(task_id)
    if not result:
        return problem_response(404, "Not Found", "Task has no run result.")
    return api_response([_ticket_response(ticket) for ticket in result.review_tickets])


@router.post("/v1/review-tickets/{ticket_id}/accept")
async def accept_review_ticket_v1(ticket_id: str, request: Request):
    payload = await request.json()
    result = store.find_result_by_ticket_id(ticket_id)
    if not result:
        return problem_response(404, "Not Found", "Review Ticket not found.")
    ticket = next(item for item in result.review_tickets if item.ticket_id == ticket_id)
    if ticket.status not in {"open", "accepted"}:
        return problem_response(409, "Conflict", f"Ticket cannot be accepted from status {ticket.status}.")
    ticket.status = "accepted"
    ticket.resolution_note = str(payload.get("note") or ticket.resolution_note)
    result.trace.append(
        AgentTraceEvent(
            task_id=result.task.task_id,
            agent="ReviewTicketService",
            node="review_ticket",
            event_type="ticket_accepted",
            summary=f"Accepted Review Ticket {ticket_id}.",
            related_ids=[ticket_id],
        )
    )
    store.save_result(result)
    return api_response(_ticket_response(ticket))


@router.post("/v1/review-tickets/{ticket_id}/rerun")
async def rerun_review_ticket_v1(ticket_id: str, request: Request):
    await request.json()
    result = store.find_result_by_ticket_id(ticket_id)
    if not result:
        return problem_response(404, "Not Found", "Review Ticket not found.")
    ticket = next(item for item in result.review_tickets if item.ticket_id == ticket_id)
    if ticket.rerun_count >= ticket.max_reruns:
        ticket.status = "blocked"
        ticket.resolution_summary = "Review Ticket reached the maximum rerun count and requires manual intervention."
        result.task.status = "blocked"
        result.trace.append(
            AgentTraceEvent(
                task_id=result.task.task_id,
                agent="ReviewTicketService",
                node="review_ticket",
                event_type="ticket_blocked",
                summary=ticket.resolution_summary,
                related_ids=[ticket_id],
            )
        )
        store.save_result(result)
        return api_response(_ticket_response(ticket))

    ticket.status = "rerun_started"
    ticket.rerun_count += 1
    result.trace.append(
        AgentTraceEvent(
            task_id=result.task.task_id,
            agent="ReviewTicketService",
            node="review_ticket",
            event_type="ticket_rerun_started",
            summary=f"Started local rerun for Review Ticket {ticket_id}.",
            related_ids=[ticket_id],
        )
    )
    store.save_result(result)
    rerun_result = rerun_review_ticket(result, ticket_id)
    ticket = next(item for item in rerun_result.review_tickets if item.ticket_id == ticket_id)
    if ticket.status == "resolved" and not ticket.resolved_at:
        ticket.resolved_at = now_iso()
    rerun_result.trace.append(
        AgentTraceEvent(
            task_id=rerun_result.task.task_id,
            agent="ReviewTicketService",
            node="review_ticket",
            event_type="ticket_local_rerun_completed",
            summary=f"Completed local rerun for Review Ticket {ticket_id}.",
            related_ids=[ticket_id],
        )
    )
    store.save_result(rerun_result)
    response = _ticket_response(ticket)
    response["workflow_result"] = rerun_result.model_dump(mode="json")
    return JSONResponse(status_code=202, content=api_response(response))


@router.post("/v1/review-tickets/{ticket_id}/mark-unavailable")
async def mark_review_ticket_unavailable_v1(ticket_id: str, request: Request):
    payload = await request.json()
    result = store.find_result_by_ticket_id(ticket_id)
    if not result:
        return problem_response(404, "Not Found", "Review Ticket not found.")
    summary = str(payload.get("reason") or "Required evidence was marked unavailable by reviewer.")
    updated = apply_review_ticket_claim_decision(result, ticket_id, "unsupported", summary)
    store.save_result(updated)
    ticket = next(item for item in updated.review_tickets if item.ticket_id == ticket_id)
    response = _ticket_response(ticket)
    response["workflow_result"] = updated.model_dump(mode="json")
    return api_response(response)


@router.post("/v1/review-tickets/{ticket_id}/downgrade")
async def downgrade_review_ticket_claim_v1(ticket_id: str, request: Request):
    payload = await request.json()
    result = store.find_result_by_ticket_id(ticket_id)
    if not result:
        return problem_response(404, "Not Found", "Review Ticket not found.")
    summary = str(payload.get("reason") or "Related conclusion was downgraded by reviewer.")
    updated = apply_review_ticket_claim_decision(result, ticket_id, "downgraded", summary)
    store.save_result(updated)
    ticket = next(item for item in updated.review_tickets if item.ticket_id == ticket_id)
    response = _ticket_response(ticket)
    response["workflow_result"] = updated.model_dump(mode="json")
    return api_response(response)


@router.post("/v1/review-tickets/{ticket_id}/dismiss")
async def dismiss_review_ticket_v1(ticket_id: str, request: Request):
    payload = await request.json()
    result = store.find_result_by_ticket_id(ticket_id)
    if not result:
        return problem_response(404, "Not Found", "Review Ticket not found.")
    ticket = next(item for item in result.review_tickets if item.ticket_id == ticket_id)
    ticket.status = "dismissed"
    ticket.resolution_summary = str(payload.get("reason") or "Dismissed by user.")
    ticket.resolved_at = now_iso()
    store.save_result(result)
    return api_response(_ticket_response(ticket))


@router.post("/v1/review-tickets/{ticket_id}/resolve")
async def resolve_review_ticket_v1(ticket_id: str, request: Request):
    payload = await request.json()
    result = store.find_result_by_ticket_id(ticket_id)
    if not result:
        return problem_response(404, "Not Found", "Review Ticket not found.")
    ticket = next(item for item in result.review_tickets if item.ticket_id == ticket_id)
    ticket.status = "resolved"
    ticket.resolution_summary = str(payload.get("resolution_summary") or "Resolved.")
    ticket.resolved_at = now_iso()
    store.save_result(result)
    return api_response(_ticket_response(ticket))


@router.get("/tasks/{task_id}/report")
def get_report(task_id: str):
    result = store.get_result(task_id)
    if not result or not result.report:
        raise HTTPException(status_code=404, detail="Task has no report")
    return result.report


@router.get("/v1/tasks/{task_id}/report")
def get_report_v1(task_id: str):
    result = store.get_result(task_id)
    if not result or not result.report:
        return problem_response(404, "Not Found", "Task has no report.")
    return api_response(_report_summary(result.report))


@router.get("/v1/tasks/{task_id}/report/sections")
def get_report_sections_v1(task_id: str):
    result = store.get_result(task_id)
    if not result or not result.report:
        return problem_response(404, "Not Found", "Task has no report.")
    return api_response(result.report.sections)


@router.get("/v1/tasks/{task_id}/report/export")
def export_report_v1(task_id: str, format: str = "markdown", allow_draft: bool = False):
    result = store.get_result(task_id)
    if not result or not result.report:
        return problem_response(404, "Not Found", "Task has no report.")
    if format != "markdown":
        return problem_response(422, "Validation Error", "MVP export only supports markdown.")
    if result.report.status in {"stale", "blocked"} and not allow_draft:
        return problem_response(
            409,
            "Conflict",
            f"Report status is {result.report.status}; pass allow_draft=true to export a warning-marked draft.",
        )
    warning = None
    content = result.report.markdown
    if result.report.status in {"stale", "blocked"}:
        warning = f"Draft export: report status is {result.report.status}."
        content = f"> {warning}\n\n{content}"
    return api_response(
        {
            "filename": f"{task_id}_report.md",
            "content_type": "text/markdown",
            "content": content,
            "status": result.report.status,
            "warning": warning,
        }
    )
