from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.graph import run_workflow
from app.models.schemas import Task, TaskConfig, WorkflowResult
from app.storage.sqlite import SQLiteStore


router = APIRouter()
store = SQLiteStore()


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
    task = Task(config=config)
    store.create_task(task)
    return task


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


@router.get("/tasks/{task_id}/claims")
def get_claims(task_id: str):
    result = store.get_result(task_id)
    if not result:
        raise HTTPException(status_code=404, detail="Task has no run result")
    return result.claims


@router.get("/tasks/{task_id}/report")
def get_report(task_id: str):
    result = store.get_result(task_id)
    if not result or not result.report:
        raise HTTPException(status_code=404, detail="Task has no report")
    return result.report
