from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.models.schemas import Task, WorkflowResult, now_iso


class SQLiteStore:
    def __init__(self, db_path: str = "data/app.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def create_task(self, task: Task) -> Task:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO tasks (task_id, status, config_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (task.task_id, task.status, task.config.model_dump_json(), task.created_at, task.updated_at),
            )
        return task

    def save_result(self, result: WorkflowResult) -> None:
        result.task.updated_at = now_iso()
        with self.connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, result_json = ?, updated_at = ? WHERE task_id = ?",
                (result.task.status, result.model_dump_json(), result.task.updated_at, result.task.task_id),
            )

    def get_result(self, task_id: str) -> WorkflowResult | None:
        with self.connect() as conn:
            row = conn.execute("SELECT result_json FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row or not row["result_json"]:
            return None
        return WorkflowResult.model_validate_json(row["result_json"])

    def get_task(self, task_id: str) -> Task | None:
        result = self.get_result(task_id)
        if result:
            return result.task
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            return None
        return Task(
            task_id=row["task_id"],
            config=json.loads(row["config_json"]),
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_tasks(self) -> list[Task]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        tasks: list[Task] = []
        for row in rows:
            result = self.get_result(row["task_id"])
            if result:
                tasks.append(result.task)
            else:
                tasks.append(
                    Task(
                        task_id=row["task_id"],
                        config=json.loads(row["config_json"]),
                        status=row["status"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                )
        return tasks

    def find_result_by_evidence_id(self, evidence_id: str) -> WorkflowResult | None:
        with self.connect() as conn:
            rows = conn.execute("SELECT result_json FROM tasks WHERE result_json IS NOT NULL ORDER BY updated_at DESC").fetchall()
        for row in rows:
            result = WorkflowResult.model_validate_json(row["result_json"])
            if any(item.evidence_id == evidence_id for item in result.evidence):
                return result
        return None

    def find_result_by_ticket_id(self, ticket_id: str) -> WorkflowResult | None:
        with self.connect() as conn:
            rows = conn.execute("SELECT result_json FROM tasks WHERE result_json IS NOT NULL ORDER BY updated_at DESC").fetchall()
        for row in rows:
            result = WorkflowResult.model_validate_json(row["result_json"])
            if any(item.ticket_id == ticket_id for item in result.review_tickets):
                return result
        return None
