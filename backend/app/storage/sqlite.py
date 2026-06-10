from __future__ import annotations

import json
import base64
import hashlib
import hmac
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from secrets import token_bytes

from app.models.schemas import PMSkill, PMSkillAssignment, Task, WorkflowResult, now_iso


class SettingsEncryptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredSetting:
    key: str
    value: str
    encrypted: bool
    updated_at: str


class SQLiteStore:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = _resolve_db_path(db_path)
        self.secret_path = self.db_path.with_name(".app_settings.key")
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    encrypted INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pm_skills (
                    skill_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    repo_url TEXT NOT NULL,
                    path TEXT NOT NULL,
                    ref TEXT NOT NULL,
                    license TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    markdown TEXT NOT NULL,
                    source TEXT NOT NULL,
                    requires_license_ack INTEGER NOT NULL DEFAULT 0,
                    imported_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pm_skill_assignments (
                    slot TEXT PRIMARY KEY,
                    skill_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    license_acknowledged INTEGER NOT NULL DEFAULT 0,
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
                """
                INSERT INTO tasks (task_id, status, config_json, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status = excluded.status,
                    config_json = excluded.config_json,
                    result_json = excluded.result_json,
                    updated_at = excluded.updated_at
                """,
                (
                    result.task.task_id,
                    result.task.status,
                    result.task.config.model_dump_json(),
                    result.model_dump_json(),
                    result.task.created_at,
                    result.task.updated_at,
                ),
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

    def get_app_settings(self) -> dict[str, StoredSetting]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM app_settings ORDER BY key").fetchall()
        settings: dict[str, StoredSetting] = {}
        for row in rows:
            raw_value = row["value"]
            encrypted = bool(row["encrypted"])
            value = decrypt_setting(raw_value, self.secret_path) if encrypted else raw_value
            settings[row["key"]] = StoredSetting(
                key=row["key"],
                value=value,
                encrypted=encrypted,
                updated_at=row["updated_at"],
            )
        return settings

    def save_app_settings(self, values: dict[str, str]) -> dict[str, StoredSetting]:
        timestamp = now_iso()
        with self.connect() as conn:
            for key, value in values.items():
                conn.execute(
                    """
                    INSERT INTO app_settings (key, value, encrypted, created_at, updated_at)
                    VALUES (?, ?, 1, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, encrypted = 1, updated_at = excluded.updated_at
                    """,
                    (key, encrypt_setting(value, self.secret_path), timestamp, timestamp),
                )
        return self.get_app_settings()

    def upsert_pm_skill(self, skill: PMSkill) -> PMSkill:
        timestamp = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO pm_skills (
                    skill_id, name, description, intent, repo_url, path, ref, license,
                    content_hash, markdown, source, requires_license_ack, imported_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(skill_id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    intent = excluded.intent,
                    repo_url = excluded.repo_url,
                    path = excluded.path,
                    ref = excluded.ref,
                    license = excluded.license,
                    content_hash = excluded.content_hash,
                    markdown = excluded.markdown,
                    source = excluded.source,
                    requires_license_ack = excluded.requires_license_ack,
                    updated_at = excluded.updated_at
                """,
                (
                    skill.skill_id,
                    skill.name,
                    skill.description,
                    skill.intent,
                    skill.repo_url,
                    skill.path,
                    skill.ref,
                    skill.license,
                    skill.content_hash,
                    skill.markdown,
                    skill.source,
                    int(skill.requires_license_ack),
                    skill.imported_at,
                    timestamp,
                ),
            )
        return skill

    def list_pm_skills(self) -> list[PMSkill]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM pm_skills ORDER BY source, intent, name").fetchall()
        return [_pm_skill_from_row(row) for row in rows]

    def get_pm_skill(self, skill_id: str) -> PMSkill | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM pm_skills WHERE skill_id = ?", (skill_id,)).fetchone()
        return _pm_skill_from_row(row) if row else None

    def save_pm_skill_assignment(
        self,
        slot: str,
        skill_id: str,
        enabled: bool = True,
        license_acknowledged: bool = False,
    ) -> PMSkillAssignment:
        assignment = PMSkillAssignment(
            slot=slot,
            skill_id=skill_id,
            enabled=enabled,
            license_acknowledged=license_acknowledged,
            updated_at=now_iso(),
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO pm_skill_assignments (slot, skill_id, enabled, license_acknowledged, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(slot) DO UPDATE SET
                    skill_id = excluded.skill_id,
                    enabled = excluded.enabled,
                    license_acknowledged = excluded.license_acknowledged,
                    updated_at = excluded.updated_at
                """,
                (
                    assignment.slot,
                    assignment.skill_id,
                    int(assignment.enabled),
                    int(assignment.license_acknowledged),
                    assignment.updated_at,
                ),
            )
        return assignment

    def get_pm_skill_assignments(self) -> list[PMSkillAssignment]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM pm_skill_assignments ORDER BY slot").fetchall()
        return [
            PMSkillAssignment(
                slot=row["slot"],
                skill_id=row["skill_id"],
                enabled=bool(row["enabled"]),
                license_acknowledged=bool(row["license_acknowledged"]),
                updated_at=row["updated_at"],
            )
            for row in rows
        ]


def encrypt_setting(value: str, secret_path: Path | None = None) -> str:
    plaintext = str(value or "").encode("utf-8")
    salt = token_bytes(16)
    nonce = token_bytes(16)
    key = _derive_settings_key(salt, secret_path)
    ciphertext = _xor_stream(plaintext, key, nonce)
    body = b"v1" + salt + nonce + ciphertext
    signature = hmac.new(key, body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(body + signature).decode("ascii")


def _pm_skill_from_row(row: sqlite3.Row) -> PMSkill:
    return PMSkill(
        skill_id=row["skill_id"],
        name=row["name"],
        description=row["description"],
        intent=row["intent"],
        repo_url=row["repo_url"],
        path=row["path"],
        ref=row["ref"],
        license=row["license"],
        content_hash=row["content_hash"],
        markdown=row["markdown"],
        source=row["source"],
        requires_license_ack=bool(row["requires_license_ack"]),
        imported_at=row["imported_at"],
    )


def decrypt_setting(value: str, secret_path: Path | None = None) -> str:
    try:
        payload = base64.urlsafe_b64decode(value.encode("ascii"))
        if len(payload) < 2 + 16 + 16 + 32 or payload[:2] != b"v1":
            raise ValueError("Unsupported encrypted setting payload.")
        salt = payload[2:18]
        nonce = payload[18:34]
        ciphertext = payload[34:-32]
        signature = payload[-32:]
        key = _derive_settings_key(salt, secret_path)
        expected = hmac.new(key, payload[:-32], hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("Encrypted setting signature mismatch.")
        return _xor_stream(ciphertext, key, nonce).decode("utf-8")
    except Exception as exc:
        raise SettingsEncryptionError("Unable to decrypt stored application setting.") from exc


def _derive_settings_key(salt: bytes, secret_path: Path | None = None) -> bytes:
    secret = os.getenv("APP_SETTINGS_SECRET") or _read_or_create_local_secret(secret_path)
    return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 200_000, dklen=32)


def _resolve_db_path(db_path: str | None) -> Path:
    if db_path:
        if db_path.startswith("sqlite:///"):
            return Path(db_path.removeprefix("sqlite:///"))
        return Path(db_path)

    env_database_url = os.getenv("DATABASE_URL", "").strip()
    if env_database_url.startswith("sqlite:///"):
        return Path(env_database_url.removeprefix("sqlite:///"))

    if os.getenv("VERCEL") or os.getenv("VERCEL_ENV") or os.getenv("NOW_REGION"):
        return Path("/tmp/app.db")

    return Path("data/app.db")


def _read_or_create_local_secret(secret_path: Path | None = None) -> str:
    key_path = secret_path or _resolve_db_path(None).with_name(".app_settings.key")
    if key_path.exists():
        return key_path.read_text(encoding="utf-8").strip()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    secret = base64.urlsafe_b64encode(token_bytes(32)).decode("ascii")
    key_path.write_text(secret, encoding="utf-8")
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    return secret


def _xor_stream(payload: bytes, key: bytes, nonce: bytes) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < len(payload):
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        output.extend(block)
        counter += 1
    return bytes(item ^ mask for item, mask in zip(payload, output))
