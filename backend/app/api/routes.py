from __future__ import annotations

import base64
import json
from queue import Empty, Queue
from threading import Thread
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from app.core.graph import apply_review_ticket_claim_decision, rerun_review_ticket, run_workflow, stream_workflow
from app.models.schemas import (
    AgentTraceEvent,
    AnalysisGoalCondenseRequest,
    AnalysisGoalCondenseResponse,
    AnalysisGoalPolishItem,
    AnalysisGoalPolishRequest,
    AnalysisGoalPolishResponse,
    CompetitorRecommendationRequest,
    CompetitorRecommendationResponse,
    PMSkillAssignment,
    PMSkillAssignmentsRequest,
    PMSkillCatalogResponse,
    PMSkillImportRequest,
    PMSkillRecommendRequest,
    PMSkillRecommendResponse,
    PMSkillSlot,
    PMSkillSyncResponse,
    SurveyGenerationRequest,
    SurveyGenerationResponse,
    SurveyQuestion,
    Task,
    TaskConfig,
    WorkflowResult,
    XhsLoginQrCodeResponse,
    XhsMcpStatusResponse,
    XhsQrCodeStatusRequest,
    XhsQrCodeStatusResponse,
    count_goal_words,
    now_iso,
    validate_task_config_fields,
)
from app.providers.errors import ProviderConfigurationError, ProviderRequestError
from app.providers.factory import build_lightweight_llm_provider, build_provider_bundle, load_provider_settings
from app.providers.xhs_mcp import XhsMcpClient
from app.skills import (
    DEFAULT_SKILL_CANDIDATES,
    SKILL_SLOTS,
    SkillImportError,
    SkillPromptComposer,
    import_github_skill,
    recommend_skill_slots,
    skill_trace_fields,
    sync_default_skills,
)
from app.storage.sqlite import SQLiteStore


router = APIRouter()
store = SQLiteStore()

SETTING_KEYS = {
    "USE_MOCK_SEARCH",
    "USE_MOCK_LLM",
    "SEARCH_PROVIDER",
    "ANYSEARCH_API_KEY",
    "ANYSEARCH_BASE_URL",
    "ANYSEARCH_MAX_RESULTS",
    "ANYSEARCH_CONTENT_TYPES",
    "LLM_PROVIDER",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "LIGHTWEIGHT_LLM_PROVIDER",
    "ALLOW_PROVIDER_FALLBACK",
    "ALLOW_EMPTY_SEARCH_FALLBACK",
}
SECRET_SETTING_KEYS = {
    "ANYSEARCH_API_KEY",
    "DEEPSEEK_API_KEY",
}
LEGACY_SEED_SETTING_KEYS = {
    "SEED_API_KEY",
    "SEED_BASE_URL",
    "SEED_MODEL",
    "LIGHTWEIGHT_SEED_API_KEY",
    "LIGHTWEIGHT_SEED_BASE_URL",
    "LIGHTWEIGHT_SEED_MODEL",
}


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


def _task_from_v1_payload(payload: dict) -> Task | JSONResponse:
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
    return Task(config=config)


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
        "before_evidence_ids": ticket.before_evidence_ids,
        "added_evidence_ids": ticket.added_evidence_ids,
        "improved_claim_ids": ticket.improved_claim_ids,
        "before_claim_statuses": ticket.before_claim_statuses,
        "after_claim_statuses": ticket.after_claim_statuses,
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
        "social_insights": [insight.model_dump(mode="json") for insight in report.social_insights],
        "skill_assignments": report.skill_assignments,
        "created_at": report.created_at,
    }


def _xhs_logged_in(response: dict) -> bool:
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
    "没有权限访问",
    "use get_login_qrcode",
    "login check failed",
]


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


def _xhs_message(response: dict) -> str:
    for key in ["message", "msg", "status", "state"]:
        if response.get(key):
            return str(response[key])
    return json.dumps(response, ensure_ascii=False)[:500]


def _extract_qrcode_payload(response: dict) -> tuple[str, str, str, str, str, int, str]:
    qrcode = ""
    qr_url = ""
    qr_image_path = ""
    qr_id = ""
    code = ""
    expires = 0
    message = _xhs_message(response)
    for key in ["qrcode_base64", "qr_code_base64", "qrcode", "qrCode", "image", "base64"]:
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            qrcode = value.strip()
            break
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    for key in ["qr_url", "qrUrl", "url"]:
        value = response.get(key, data.get(key))
        if isinstance(value, str) and value.strip():
            qr_url = value.strip()
            break
    for key in ["qr_image", "qrImage", "qr_image_path"]:
        value = response.get(key, data.get(key))
        if isinstance(value, str) and value.strip():
            qr_image_path = value.strip()
            break
    for key in ["qr_id", "qrId", "id"]:
        value = response.get(key, data.get(key))
        if isinstance(value, str) and value.strip():
            qr_id = value.strip()
            break
    for key in ["code", "qr_code", "qrCode"]:
        value = response.get(key, data.get(key))
        if isinstance(value, str) and value.strip():
            code = value.strip()
            break
    if not qrcode:
        for key in ["qrcode_base64", "qr_code_base64", "qrcode", "qrCode", "image", "base64"]:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                qrcode = value.strip()
                break
    if not qrcode and qr_image_path:
        qrcode = _read_qrcode_image(qr_image_path)
    for key in ["expires_in_seconds", "expires_in", "expire_seconds", "timeout"]:
        value = response.get(key, data.get(key))
        if isinstance(value, int):
            expires = value
            break
        if isinstance(value, str) and value.isdigit():
            expires = int(value)
            break
    return qrcode, qr_url, qr_image_path, qr_id, code, expires, message


def _read_qrcode_image(path: str) -> str:
    try:
        candidate = Path(path).expanduser()
        if not candidate.exists() or not candidate.is_file():
            return ""
        if candidate.stat().st_size > 1024 * 1024:
            return ""
        return base64.b64encode(candidate.read_bytes()).decode("ascii")
    except OSError:
        return ""


def _clean_goal(value: object) -> str:
    text = " ".join(str(value or "").split())
    return text.strip(" -\t")


def _format_polished_goals(goals: list[str]) -> str:
    return "\n".join(f"{index + 1}. {goal}" for index, goal in enumerate(goals))


def _normalized_name(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _polish_response_from_provider(response: dict, provider: str) -> AnalysisGoalPolishResponse:
    raw_goals = response.get("goals") if isinstance(response.get("goals"), list) else []
    raw_items = response.get("items") if isinstance(response.get("items"), list) else []
    items: list[AnalysisGoalPolishItem] = []
    goals: list[str] = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        title = _clean_goal(item.get("title"))
        details = [_clean_goal(detail) for detail in item.get("details", []) if _clean_goal(detail)]
        if title:
            items.append(AnalysisGoalPolishItem(title=title, details=details[:4]))

    if items:
        goals = [
            f"{item.title}：{'；'.join(item.details)}" if item.details else item.title
            for item in items
        ]
    else:
        for goal in raw_goals:
            cleaned = _clean_goal(goal)
            if cleaned:
                goals.append(cleaned)

    if not goals:
        goals = ["明确竞品定位差异", "梳理核心功能与工作流差异", "对比定价、目标用户和可落地机会"]
        items = [
            AnalysisGoalPolishItem(title="定位差异", details=["明确目标产品与竞品的核心卖点和适用场景"]),
            AnalysisGoalPolishItem(title="功能工作流", details=["对比关键功能、AI 能力和用户完成任务的路径"]),
            AnalysisGoalPolishItem(title="商业机会", details=["结合定价、目标用户和证据风险输出机会点"]),
        ]

    meta = response.get("__provider_meta") if isinstance(response.get("__provider_meta"), dict) else {}
    return AnalysisGoalPolishResponse(
        goals=goals[:8],
        items=items[:8],
        formatted_text=_format_polished_goals(goals[:8]),
        provider=provider,
        provider_request_id=str(meta.get("request_id") or ""),
    )


def _condense_response_from_provider(response: dict, payload: AnalysisGoalCondenseRequest, provider: str) -> AnalysisGoalCondenseResponse:
    condensed = " ".join(str(response.get("condensed_text") or "").split())
    if not condensed and isinstance(response.get("goals"), list):
        condensed = "\n".join(str(goal).strip() for goal in response["goals"] if str(goal).strip())
    if not condensed:
        condensed = payload.draft.strip()
    meta = response.get("__provider_meta") if isinstance(response.get("__provider_meta"), dict) else {}
    return AnalysisGoalCondenseResponse(
        condensed_text=condensed,
        word_count=count_goal_words(condensed),
        provider=provider,
        provider_request_id=str(meta.get("request_id") or ""),
    )


def _competitor_recommendation_from_provider(response: dict, payload: CompetitorRecommendationRequest, provider: str) -> CompetitorRecommendationResponse:
    raw_competitors = response.get("competitors") if isinstance(response.get("competitors"), list) else []
    blocked = {_normalized_name(payload.target_product), *(_normalized_name(item) for item in payload.existing_competitors)}
    competitors: list[str] = []
    seen: set[str] = set()
    max_results = min(max(payload.max_results, 1), 5)

    for item in raw_competitors:
        name = " ".join(str(item or "").split()).strip(" -\t")
        key = _normalized_name(name)
        if not name or key in blocked or key in seen:
            continue
        seen.add(key)
        competitors.append(name)
        if len(competitors) >= max_results:
            break

    meta = response.get("__provider_meta") if isinstance(response.get("__provider_meta"), dict) else {}
    return CompetitorRecommendationResponse(
        competitors=competitors,
        rationale=str(response.get("rationale") or ""),
        provider=provider,
        provider_request_id=str(meta.get("request_id") or ""),
    )


def _skill_catalog_payload() -> PMSkillCatalogResponse:
    return PMSkillCatalogResponse(
        skills=store.list_pm_skills(),
        slots=[PMSkillSlot(**slot) for slot in SKILL_SLOTS],
        defaults=DEFAULT_SKILL_CANDIDATES,
        assignments=store.get_pm_skill_assignments(),
    )


def _skill_context(slot: str):
    return SkillPromptComposer(store).context_for_slot(slot)


def _complete_with_skill(llm, purpose: str, payload: dict, skill_context=None) -> dict:
    skill_prompt = skill_context.prompt if skill_context else ""
    try:
        return llm.complete_structured(purpose, payload, skill_prompt=skill_prompt)
    except TypeError as exc:
        if "skill_prompt" not in str(exc):
            raise
        return llm.complete_structured(purpose, payload)


def _apply_skill_assignment(update) -> PMSkillAssignment:
    skill_id = update.skill_id.strip()
    if not update.enabled or not skill_id:
        return store.save_pm_skill_assignment(update.slot, "", False, False)
    skill = store.get_pm_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} was not found.")
    if skill.requires_license_ack and not update.license_acknowledged:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{skill.name} uses {skill.license}. Please confirm non-commercial/compliant use "
                "before enabling this skill."
            ),
        )
    return store.save_pm_skill_assignment(update.slot, skill_id, True, update.license_acknowledged or not skill.requires_license_ack)


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _survey_questions_from_provider(raw_questions: object, fallback_product: str) -> list[SurveyQuestion]:
    questions: list[SurveyQuestion] = []
    valid_types = {"screening", "single_choice", "multiple_choice", "likert", "ranking", "open_text"}
    if isinstance(raw_questions, list):
        for index, raw_question in enumerate(raw_questions, start=1):
            if not isinstance(raw_question, dict):
                continue
            question_type = str(raw_question.get("type") or "open_text")
            if question_type not in valid_types:
                question_type = "open_text"
            text = _clean_text(raw_question.get("text"))
            if not text:
                continue
            raw_options = raw_question.get("options") if isinstance(raw_question.get("options"), list) else []
            questions.append(
                SurveyQuestion(
                    question_id=_clean_text(raw_question.get("question_id")) or f"Q{index}",
                    type=question_type,
                    text=text,
                    options=[_clean_text(option) for option in raw_options if _clean_text(option)],
                    required=bool(raw_question.get("required", True)),
                    purpose=_clean_text(raw_question.get("purpose")),
                )
            )
    if questions:
        return questions[:30]
    return [
        SurveyQuestion(
            question_id="Q1",
            type="screening",
            text=f"你是否在过去 3 个月内使用或评估过 {fallback_product} 或同类产品？",
            options=["是", "否"],
            purpose="确认样本资格",
        ),
        SurveyQuestion(
            question_id="Q2",
            type="likert",
            text=f"请评价 {fallback_product} 对核心任务效率的帮助程度。",
            options=["1 非常不同意", "2", "3", "4", "5 非常同意"],
            purpose="量化价值感知",
        ),
        SurveyQuestion(
            question_id="Q3",
            type="open_text",
            text=f"请描述一次你使用 {fallback_product} 或同类产品完成任务的经历。",
            required=False,
            purpose="收集真实使用场景",
        ),
    ]


def _survey_json(title: str, questions: list[SurveyQuestion]) -> dict:
    return {
        "title": title,
        "version": "1.0",
        "groups": [
            {
                "nameID": "user_research",
                "title": "用户调研",
                "questions": [question.question_id for question in questions],
            }
        ],
        "questions": [
            {
                "nameID": question.question_id,
                "type": question.type,
                "title": question.text,
                "required": question.required,
                "choices": question.options,
                "metadata": {"purpose": question.purpose},
            }
            for question in questions
        ],
    }


def _survey_response_from_provider(response: dict, payload: SurveyGenerationRequest, provider: str, skill_context=None) -> SurveyGenerationResponse:
    product_name = _clean_text(payload.product_name)
    title = _clean_text(response.get("title")) or f"{product_name} 用户调研问卷"
    questions = _survey_questions_from_provider(response.get("questions"), product_name)
    raw_screening = response.get("screening_criteria") if isinstance(response.get("screening_criteria"), list) else []
    raw_analysis = response.get("analysis_plan") if isinstance(response.get("analysis_plan"), list) else []
    raw_survey_json = response.get("survey_json") if isinstance(response.get("survey_json"), dict) else {}
    meta = response.get("__provider_meta") if isinstance(response.get("__provider_meta"), dict) else {}
    return SurveyGenerationResponse(
        title=title,
        research_objective=_clean_text(response.get("research_objective")) or _clean_text(payload.research_goal),
        target_users=_clean_text(response.get("target_users")) or _clean_text(payload.target_users),
        screening_criteria=[_clean_text(item) for item in raw_screening if _clean_text(item)] or [
            "受访者需要符合目标用户画像。",
            "受访者需要有相关产品或替代方案的真实使用/评估经验。",
        ],
        questions=questions,
        analysis_plan=[_clean_text(item) for item in raw_analysis if _clean_text(item)] or [
            "按筛选题剔除无效样本。",
            "量表题做分布和分组比较，开放题做主题编码。",
            "将高频痛点、任务场景和购买阻力沉淀为产品机会点。",
        ],
        survey_json=raw_survey_json or _survey_json(title, questions),
        skill_source=(
            {
                "name": skill_context.skill_name,
                "repository": skill_context.skill_repo,
                "license": skill_context.license,
                "agent_skill": skill_context.skill_path,
                "content_hash": skill_context.skill_hash,
            }
            if skill_context
            else {
                "name": "surveygo",
                "repository": "https://github.com/rendis/surveygo",
                "license": "MIT",
                "agent_skill": "skills/surveygo",
                "install_hint": "npx skills add https://github.com/rendis/surveygo --skill surveygo",
            }
        ),
        provider=provider,
        provider_request_id=str(meta.get("request_id") or ""),
    )


def _sse_message(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _streaming_workflow_response(task: Task) -> StreamingResponse:
    def event_generator():
        task.status = "running"
        final_result = None
        events: Queue[dict | None] = Queue()

        def worker():
            try:
                for event in stream_workflow(task):
                    events.put(event)
                events.put(None)
            except Exception as exc:
                task.status = "failed"
                events.put({"event": "workflow_error", "data": {"task_id": task.task_id, "message": str(exc)}})
                events.put(None)

        Thread(target=worker, daemon=True).start()
        while True:
            try:
                event = events.get(timeout=10)
            except Empty:
                yield _sse_message("heartbeat", {"task_id": task.task_id, "status": "running"})
                continue
            if event is None:
                break
            if event["event"] == "result":
                final_result = WorkflowResult.model_validate(event["data"])
            yield _sse_message(event["event"], event["data"])
        if final_result:
            try:
                store.save_result(final_result)
            except Exception:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/tasks")
def list_tasks(include_fixture: bool = False):
    tasks = store.list_tasks()
    if include_fixture:
        return tasks
    return [
        task
        for task in tasks
        if (result := store.get_result(task.task_id))
        and result.trust_summary
        and not result.trust_summary.fixture_mode
    ]


def _provider_status():
    settings = load_provider_settings()
    issues: list[str] = []
    search_ready = not settings.use_mock_search
    llm_ready = not settings.use_mock_llm
    lightweight_llm_ready = True

    if settings.use_mock_search:
        issues.append("真实搜索未启用：USE_MOCK_SEARCH 必须为 false。")
    elif settings.search_provider == "anysearch" and not settings.anysearch_api_key:
        search_ready = False
        issues.append("AnySearch 未配置：请设置 ANYSEARCH_API_KEY。")
    elif settings.search_provider not in {"anysearch", "duckduckgo"}:
        search_ready = False
        issues.append(f"不支持的搜索 Provider：{settings.search_provider}。")

    if settings.use_mock_llm:
        issues.append("真实 LLM 未启用：USE_MOCK_LLM 必须为 false。")
    elif settings.llm_provider == "deepseek" and not settings.deepseek_api_key:
        llm_ready = False
        issues.append("DeepSeek 未配置：请设置 DEEPSEEK_API_KEY。")
    elif settings.llm_provider != "deepseek":
        llm_ready = False
        issues.append(f"不支持的 LLM Provider：{settings.llm_provider}。")

    if settings.lightweight_llm_provider == "deepseek" and not settings.deepseek_api_key:
        lightweight_llm_ready = False
        issues.append("轻量 LLM 使用 DeepSeek，但 DEEPSEEK_API_KEY 未配置。")
    elif settings.lightweight_llm_provider not in {"deepseek", "mock"}:
        lightweight_llm_ready = False
        issues.append(f"不支持的轻量 LLM Provider：{settings.lightweight_llm_provider}。")

    fallback_enabled = settings.allow_provider_fallback or settings.allow_empty_search_fallback
    if fallback_enabled:
        issues.append("真实业务流程禁止演示数据降级：请将 ALLOW_PROVIDER_FALLBACK 和 ALLOW_EMPTY_SEARCH_FALLBACK 都设为 false。")

    return {
        "workflow_ready": search_ready and llm_ready and not fallback_enabled,
        "search": {"ready": search_ready, "provider": settings.search_provider},
        "llm": {"ready": llm_ready, "provider": settings.llm_provider},
        "lightweight_llm": {"ready": lightweight_llm_ready, "provider": settings.lightweight_llm_provider},
        "fallback_enabled": fallback_enabled,
        "issues": issues,
    }


def _settings_payload() -> dict:
    settings = load_provider_settings()
    stored = store.get_app_settings()
    api_keys = {
        "ANYSEARCH_API_KEY": bool(settings.anysearch_api_key),
        "DEEPSEEK_API_KEY": bool(settings.deepseek_api_key),
    }
    visible_stored_keys = sorted(key for key in stored.keys() if key not in LEGACY_SEED_SETTING_KEYS)
    return {
        "values": {
            "USE_MOCK_SEARCH": settings.use_mock_search,
            "USE_MOCK_LLM": settings.use_mock_llm,
            "SEARCH_PROVIDER": settings.search_provider,
            "ANYSEARCH_BASE_URL": settings.anysearch_base_url,
            "ANYSEARCH_MAX_RESULTS": settings.anysearch_max_results,
            "ANYSEARCH_CONTENT_TYPES": ",".join(settings.anysearch_content_types),
            "LLM_PROVIDER": settings.llm_provider,
            "DEEPSEEK_BASE_URL": settings.deepseek_base_url,
            "DEEPSEEK_MODEL": settings.deepseek_model,
            "LIGHTWEIGHT_LLM_PROVIDER": settings.lightweight_llm_provider,
            "ALLOW_PROVIDER_FALLBACK": settings.allow_provider_fallback,
            "ALLOW_EMPTY_SEARCH_FALLBACK": settings.allow_empty_search_fallback,
        },
        "api_keys": api_keys,
        "stored_keys": visible_stored_keys,
        "encrypted_keys": sorted(key for key, value in stored.items() if value.encrypted and key not in LEGACY_SEED_SETTING_KEYS),
        "provider_status": _provider_status(),
    }


def _normalized_setting_updates(payload: dict) -> dict[str, str]:
    values = payload.get("values", payload)
    if not isinstance(values, dict):
        raise ValueError("Settings payload must contain a values object.")
    updates: dict[str, str] = {}
    for key, value in values.items():
        if key not in SETTING_KEYS:
            continue
        if key in SECRET_SETTING_KEYS and str(value or "") == "":
            continue
        if isinstance(value, bool):
            updates[key] = "true" if value else "false"
        else:
            updates[key] = str(value or "").strip()
    return updates


@router.get("/v1/provider-status")
def provider_status_v1():
    return api_response(_provider_status())


@router.get("/v1/settings")
def get_settings_v1():
    return api_response(_settings_payload())


@router.get("/v1/social/xhs/status")
def get_xhs_status_v1():
    client = XhsMcpClient()
    try:
        response = client.check_login_status()
    except ProviderRequestError as exc:
        return api_response(
            XhsMcpStatusResponse(
                connected=False,
                logged_in=False,
                login_required=True,
                message=str(exc),
                mcp_url=client.base_url,
            ).model_dump(mode="json")
        )
    logged_in = _xhs_logged_in(response)
    return api_response(
        XhsMcpStatusResponse(
            connected=True,
            logged_in=logged_in,
            login_required=not logged_in,
            message=_xhs_message(response) or ("已登录" if logged_in else "小红书需要登录。"),
            mcp_url=client.base_url,
        ).model_dump(mode="json")
    )


@router.post("/v1/social/xhs/login-qrcode")
def get_xhs_login_qrcode_v1():
    client = XhsMcpClient()
    try:
        response = client.get_login_qrcode()
    except ProviderRequestError as exc:
        return api_response(
            XhsLoginQrCodeResponse(
                connected=False,
                login_required=True,
                message=str(exc),
                mcp_url=client.base_url,
            ).model_dump(mode="json")
        )
    qrcode, qr_url, qr_image_path, qr_id, code, expires, message = _extract_qrcode_payload(response)
    return api_response(
        XhsLoginQrCodeResponse(
            connected=True,
            login_required=True,
            qrcode_base64=qrcode,
            qr_url=qr_url,
            qr_image_path=qr_image_path,
            qr_id=qr_id,
            code=code,
            expires_in_seconds=expires,
            message=message or ("已生成登录二维码。" if qrcode else "MCP 未返回二维码，请检查服务日志。"),
            mcp_url=client.base_url,
        ).model_dump(mode="json")
    )


@router.post("/v1/social/xhs/qrcode-status")
def check_xhs_qrcode_status_v1(payload: XhsQrCodeStatusRequest):
    client = XhsMcpClient()
    if not payload.qr_id or not payload.code:
        return api_response(
            XhsQrCodeStatusResponse(
                connected=False,
                logged_in=False,
                login_required=True,
                status="missing_qrcode_tokens",
                message="MCP 未返回 qr_id/code，无法轮询扫码状态；请刷新二维码或检查 xiaohongshu-mcp 版本。",
                mcp_url=client.base_url,
            ).model_dump(mode="json")
        )
    try:
        response = client.check_qrcode_status(payload.qr_id, payload.code)
    except ProviderRequestError as exc:
        return api_response(
            XhsQrCodeStatusResponse(
                connected=False,
                logged_in=False,
                login_required=True,
                status="unavailable",
                message=str(exc),
                mcp_url=client.base_url,
            ).model_dump(mode="json")
        )
    logged_in = _xhs_logged_in(response)
    status_text = str(response.get("status") or response.get("state") or response.get("code") or "")
    return api_response(
        XhsQrCodeStatusResponse(
            connected=True,
            logged_in=logged_in,
            login_required=not logged_in,
            status=status_text,
            message=_xhs_message(response) or ("扫码登录已完成。" if logged_in else "等待扫码确认。"),
            mcp_url=client.base_url,
        ).model_dump(mode="json")
    )


@router.put("/v1/settings")
async def update_settings_v1(request: Request):
    payload = await request.json()
    try:
        updates = _normalized_setting_updates(payload)
    except ValueError as exc:
        return problem_response(422, "Validation Error", str(exc))
    if not updates:
        return problem_response(422, "Validation Error", "No supported settings were provided.")
    store.save_app_settings(updates)
    return api_response(_settings_payload())


@router.get("/v1/skills/catalog")
def get_pm_skills_catalog():
    return api_response(_skill_catalog_payload())


@router.post("/v1/skills/import-github")
def import_pm_skill_from_github(payload: PMSkillImportRequest):
    try:
        skill = import_github_skill(payload.github_url, intent=payload.intent, license_name=str(payload.license), source="user")
    except SkillImportError as exc:
        return problem_response(422, "Skill Import Error", str(exc))
    store.upsert_pm_skill(skill)
    return api_response({"skill": skill, "catalog": _skill_catalog_payload()})


@router.post("/v1/skills/sync-defaults")
def sync_default_pm_skills():
    imported, warnings, assignments = sync_default_skills(store)
    return api_response(PMSkillSyncResponse(imported=imported, warnings=warnings, assignments=assignments))


@router.put("/v1/skills/assignments")
def update_pm_skill_assignments(payload: PMSkillAssignmentsRequest):
    assignments: list[PMSkillAssignment] = []
    for update in payload.assignments:
        assignments.append(_apply_skill_assignment(update))
    return api_response({"assignments": assignments, "catalog": _skill_catalog_payload()})


@router.post("/v1/skills/recommend")
def recommend_pm_skills(payload: PMSkillRecommendRequest):
    recommendations = recommend_skill_slots(payload.top_level_goal, payload.task_domain, payload.data_sources, store.list_pm_skills())
    return api_response(PMSkillRecommendResponse(recommendations=recommendations))


@router.post("/tasks")
def create_task(config: TaskConfig):
    errors = validate_task_config_fields(config)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    task = Task(config=config)
    store.create_task(task)
    return task


@router.post("/v1/analysis-goals/polish")
def polish_analysis_goals(payload: AnalysisGoalPolishRequest):
    draft = payload.draft.strip()
    if not draft:
        return problem_response(422, "Validation Error", "Draft analysis goal text is required.")
    try:
        llm, llm_mode = build_lightweight_llm_provider()
        skill_context = _skill_context("competitor_analysis")
        response = _complete_with_skill(
            llm,
            "analysis_goal_polish",
            {
                "draft": draft,
                "domain": payload.domain,
                "target_product": payload.target_product,
                "competitors": payload.competitors,
                "audience": payload.audience,
                "requirements": [
                    "Turn the draft into categorized competitor-analysis goals.",
                    "Use concise polished wording.",
                    "Return numbered-list-ready items.",
                ],
            },
            skill_context,
        )
    except (ProviderConfigurationError, ProviderRequestError) as exc:
        return problem_response(502, "Provider Error", str(exc))
    return api_response(_polish_response_from_provider(response, llm.provider_name))


@router.post("/v1/analysis-goals/condense")
def condense_analysis_goals(payload: AnalysisGoalCondenseRequest):
    draft = payload.draft.strip()
    if not draft:
        return problem_response(422, "Validation Error", "Draft analysis goal text is required.")
    max_words = min(max(payload.max_words, 100), 1000)
    try:
        llm, llm_mode = build_lightweight_llm_provider()
        skill_context = _skill_context("competitor_analysis")
        response = _complete_with_skill(
            llm,
            "analysis_goal_condense",
            {
                "draft": draft,
                "domain": payload.domain,
                "target_product": payload.target_product,
                "competitors": payload.competitors,
                "audience": payload.audience,
                "max_words": max_words,
                "requirements": [
                    "Preserve the user's intended comparison dimensions.",
                    "Remove repetition and overly detailed implementation notes.",
                    "Return a concise text that can be used directly as analysis_goals.",
                ],
            },
            skill_context,
        )
    except (ProviderConfigurationError, ProviderRequestError) as exc:
        return problem_response(502, "Provider Error", str(exc))
    return api_response(_condense_response_from_provider(response, payload, llm.provider_name))


@router.post("/v1/competitors/recommend")
def recommend_competitors(payload: CompetitorRecommendationRequest):
    target_product = payload.target_product.strip()
    if not target_product:
        return problem_response(422, "Validation Error", "Target product is required for competitor recommendation.")
    try:
        llm, llm_mode = build_lightweight_llm_provider()
        skill_context = _skill_context("competitor_analysis")
        response = _complete_with_skill(
            llm,
            "competitor_recommendation",
            {
                "target_product": target_product,
                "domain": payload.domain,
                "existing_competitors": payload.existing_competitors,
                "audience": payload.audience,
                "max_results": min(max(payload.max_results, 1), 5),
                "requirements": [
                    "Recommend products that a product analyst would reasonably compare with the target.",
                    "Return only product names in competitors.",
                    "Exclude duplicates, the target product, and existing competitors.",
                ],
            },
            skill_context,
        )
    except (ProviderConfigurationError, ProviderRequestError) as exc:
        return problem_response(502, "Provider Error", str(exc))
    return api_response(_competitor_recommendation_from_provider(response, payload, llm.provider_name))


@router.post("/v1/surveys/generate")
def generate_user_research_survey(payload: SurveyGenerationRequest):
    product_name = payload.product_name.strip()
    research_goal = payload.research_goal.strip()
    target_users = payload.target_users.strip()
    if not product_name:
        return problem_response(422, "Validation Error", "Product name is required.")
    if not research_goal:
        return problem_response(422, "Validation Error", "Research goal is required.")
    if not target_users:
        return problem_response(422, "Validation Error", "Target users are required.")
    question_count = min(max(payload.question_count, 6), 30)
    try:
        llm, llm_mode = build_lightweight_llm_provider()
        skill_context = _skill_context("interview_script")
        response = _complete_with_skill(
            llm,
            "survey_generation",
            {
                "product_name": product_name,
                "research_goal": research_goal,
                "target_users": target_users,
                "scenario": payload.scenario,
                "question_count": question_count,
                "language": payload.language,
                "skill_source": {
                    "name": "surveygo",
                    "repository": "https://github.com/rendis/surveygo",
                    "license": "MIT",
                    "agent_skill": "skills/surveygo",
                },
                "requirements": [
                    "Use the MIT-licensed SurveyGo agent skill as the questionnaire structure reference.",
                    "Include screening, behavior, need, decision-factor, Likert-scale, and open-text questions.",
                    "Avoid leading questions, double-barreled questions, and invented product facts.",
                    "Return a SurveyGo/SurveyJS-friendly survey_json draft.",
                ],
            },
            skill_context,
        )
    except (ProviderConfigurationError, ProviderRequestError) as exc:
        return problem_response(502, "Provider Error", str(exc))
    return api_response(_survey_response_from_provider(response, payload, llm.provider_name, skill_context))


@router.post("/v1/tasks", status_code=status.HTTP_201_CREATED)
async def create_task_v1(request: Request):
    payload = await request.json()
    task = _task_from_v1_payload(payload)
    if isinstance(task, JSONResponse):
        return task
    store.create_task(task)
    return api_response(
        {
            "task_id": task.task_id,
            "status": "draft",
            "task_config": {
                "product_domain": "generic" if task.config.domain == "general_product" else task.config.domain,
                "target_product": task.config.target_product,
                "competitors": task.config.competitors,
                "analysis_goals": task.config.analysis_goals,
                "report_depth": "brief" if task.config.depth == "quick" else task.config.depth,
                "evidence_strictness": task.config.evidence_strictness,
                "output_audience": task.config.audience,
                "natural_language_notes": task.config.notes,
            },
        }
    )


@router.post("/v1/tasks/run/stream")
async def stream_task_run_from_config_v1(request: Request):
    payload = await request.json()
    task = _task_from_v1_payload(payload)
    if isinstance(task, JSONResponse):
        return task
    provider_status = _provider_status()
    if not provider_status["workflow_ready"]:
        return problem_response(503, "Provider Not Ready", " ".join(provider_status["issues"]))
    return _streaming_workflow_response(task)


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
    provider_status = _provider_status()
    if not provider_status["workflow_ready"]:
        raise HTTPException(status_code=503, detail=provider_status["issues"])
    task.status = "running"
    result = run_workflow(task)
    store.save_result(result)
    return result


@router.get("/v1/tasks/{task_id}/run/stream")
def stream_task_run_v1(task_id: str):
    task = store.get_task(task_id)
    if not task:
        return problem_response(404, "Not Found", "Task not found.")
    provider_status = _provider_status()
    if not provider_status["workflow_ready"]:
        return problem_response(503, "Provider Not Ready", " ".join(provider_status["issues"]))
    return _streaming_workflow_response(task)


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
    if ticket.status in {"resolved", "dismissed", "blocked"}:
        return problem_response(409, "Conflict", f"Ticket is already {ticket.status}; reopen it before running a new evidence-improvement rerun.")
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
    if result.report.status in {"stale", "blocked", "reviewing"} and not allow_draft:
        return problem_response(
            409,
            "Conflict",
            f"Report status is {result.report.status}; pass allow_draft=true to export a warning-marked draft.",
        )
    warning = None
    content = result.report.markdown
    if result.report.status in {"stale", "blocked", "reviewing"}:
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
