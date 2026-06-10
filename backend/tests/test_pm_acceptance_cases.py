import pytest

from app.core.graph import run_workflow
from app.models.schemas import Task, TaskConfig


@pytest.fixture(autouse=True)
def use_demo_providers(monkeypatch):
    monkeypatch.setenv("USE_MOCK_SEARCH", "true")
    monkeypatch.setenv("USE_MOCK_LLM", "true")


def _section_keys(result):
    return {section.section_key for section in result.report.sections}


def test_pm_case_publishable_ai_tools_report_supports_decision_review():
    task = Task(
        config=TaskConfig(
            domain="ai_tools",
            target_product="Cursor",
            competitors=["GitHub Copilot", "Windsurf"],
            analysis_goals=[
                "比较 AI 编程工具的定位、定价、用户旅程、目标用户、安全风险和团队采购建议。",
                "报告必须能让产品经理判断下一步差异化机会和仍需补证的风险。",
            ],
            evidence_strictness="high",
            audience="企业产品经理",
        )
    )

    result = run_workflow(task)

    assert result.report.status == "passed"
    assert {"feature_tree", "pricing_model", "user_persona", "swot", "pm_acceptance", "pm_decision_page"}.issubset(_section_keys(result))
    assert {"decision_summary", "differentiated_insights", "next_actions"}.issubset(_section_keys(result))
    assert result.trust_summary.unresolved_ticket_count == 0
    assert result.trust_summary.browser_verified_product_count == 0
    assert result.trust_summary.claim_evidence_binding_rate >= 0.75
    assert all(claim.supporting_evidence for claim in result.claims if claim.included_in_report)
    assert len(result.report.pricing_model.plans) == 3
    assert all(plan.evidence_ids for plan in result.report.pricing_model.plans)
    assert all(plan.data_gaps for plan in result.report.pricing_model.plans)
    assert any(source.source_type == "fixture_walkthrough" for source in result.sources)
    assert result.report.user_personas
    assert result.report.swot.evidence_ids
    assert "## PM 决策页" in result.report.markdown
    assert "## PM 验收检查" in result.report.markdown
    assert "## 决策摘要" in result.report.markdown
    assert "## 关键差异洞察" in result.report.markdown
    assert "## 下一步行动清单" in result.report.markdown
    assert "决策状态：可进入内部评审" in result.report.markdown
    assert "正式发布前需补外部样本" in result.report.markdown
    assert "不能下价格高低结论" in result.report.markdown
    assert "补价格深度" in result.report.markdown
    assert "质量 rubric" in result.report.markdown
    assert "优先级 / 风险 / 证据等级" in result.report.markdown
    assert "金额 未抽取到公开金额" in result.report.markdown
    assert "结构化交互路径证据：3 条" in result.report.markdown
    assert "用于旅程假设，不等同真实浏览器实测" in result.report.markdown
    assert "从定位看，Cursor 的关键信号是" not in result.report.markdown
    assert "The report binds claims to evidence IDs" not in result.report.markdown
    assert "Cursor is positioned as an AI-native code editor" not in result.report.markdown
    assert "workflow is represented as" not in result.report.markdown
    assert "Capability tree for" not in result.report.markdown
    assert "User Journey separates" not in result.report.markdown
    assert "实测链路" not in result.report.markdown
    assert "已有实测路径" not in result.report.markdown
    assert "三者定位差异足够明显" in result.report.markdown
    assert result.report.markdown.index("## 结构化综合摘要") < result.report.markdown.index("## PM 决策页")
    assert result.report.markdown.index("## PM 决策页") < result.report.markdown.index("## 决策摘要")
    assert result.report.markdown.index("## 结构化综合摘要") < result.report.markdown.index("## 数据来源（Resources）")
    assert "效率证据：一次任务覆盖 3 个产品" in result.report.markdown
    assert "覆盖度证据" in result.report.markdown
    assert "一致性证据" in result.report.markdown


def test_pm_case_review_ticket_rerun_records_before_after_improvement():
    task = Task(
        config=TaskConfig(
            domain="ai_tools",
            target_product="Cursor",
            competitors=["TRAE"],
            analysis_goals=[
                "刻意覆盖一个定价缺口场景，验证 Critic 打回 Research 后是否真的新增证据。",
                "最终报告不能把未补证的结论写成事实，必须展示工单改善或人工复核状态。",
            ],
            evidence_strictness="high",
            audience="产品负责人",
        )
    )

    result = run_workflow(task)
    pricing_ticket = next(
        ticket for ticket in result.review_tickets if ticket.product == "TRAE" and ticket.missing_evidence_type == "pricing"
    )

    assert pricing_ticket.status == "resolved"
    assert pricing_ticket.before_evidence_ids == []
    assert pricing_ticket.added_evidence_ids
    assert pricing_ticket.improved_claim_ids
    assert any(event.event_type == "supplemental_search" for event in result.trace)
    assert any(event.event_type == "review_ticket_improvement_verified" for event in result.trace)
    assert any(query.is_supplemental and query.related_ticket_id == pricing_ticket.ticket_id for query in result.search_plan.queries)
    assert "Review Tickets:" in result.report.markdown
    assert "已处理 Review Ticket" in result.report.markdown
    assert all(
        claim.supporting_evidence
        for claim in result.claims
        if claim.product == "TRAE" and claim.claim_type == "pricing" and claim.included_in_report
    )


def test_pm_case_social_listening_blocker_is_visible_and_not_fabricated(monkeypatch):
    monkeypatch.setattr(
        "app.providers.xhs_mcp.XhsMcpClient.check_login_status",
        lambda self: {"message": "Not logged in: 请登录\nUse get_login_qrcode to log in."},
    )
    task = Task(
        config=TaskConfig(
            domain="ai_tools",
            target_product="飞书",
            competitors=["钉钉"],
            analysis_goals=[
                "验证协同产品在小红书用户反馈中的痛点、购买信号和竞品提及。",
                "当社媒登录受阻时，报告必须明确阻断项和下一步人工动作。",
            ],
            audience="增长与产品团队",
            social_listening={
                "enabled": True,
                "manual_xhs_summary": "小红书点点 AI 总结：用户觉得飞书协作体验顺滑，但也提到开放接口权限和配置门槛偏高。",
                "platforms": [{"platform": "xiaohongshu", "enabled": True, "keywords": ["飞书"]}],
            },
        )
    )

    result = run_workflow(task)

    assert result.report.status == "reviewing"
    assert "## 社媒舆情洞察" in result.report.markdown
    assert "小红书点点 AI 总结" in result.report.markdown
    assert "当前阻断" in result.report.markdown
    assert "## PM 验收检查" in result.report.markdown
    assert "决策状态：需人工复核" in result.report.markdown
    assert "跨团队协同负责人" in result.report.markdown
    assert "开放接口和配置成本" in result.report.markdown
    assert "Individual AI-assisted developer" not in result.report.markdown
    assert any(ticket.source_query_hint == "XHS_LOGIN_REQUIRED" for ticket in result.review_tickets)
    assert result.trust_summary.unresolved_ticket_count >= 1
    assert not any(
        claim.claim_type == "social_sentiment" and claim.included_in_report and not claim.supporting_evidence
        for claim in result.claims
    )


def test_pm_case_live_provider_contract_keeps_acceptance_gates(monkeypatch):
    from app.core import nodes
    from app.fixtures.demo_data import AI_TOOLS_FIXTURES
    from app.providers.factory import ProviderBundle
    from app.providers.llm import LLMProvider
    from app.providers.search import SearchProvider

    class StaticLiveSearchProvider(SearchProvider):
        provider_name = "DuckDuckGoSearchProvider"

        def search(self, task_id, query, supplement=False):
            candidates = AI_TOOLS_FIXTURES.get(query.product, [])
            if query.expected_evidence == "pricing":
                return [item for item in candidates if item.get("evidence_type") == "pricing"]
            if query.expected_evidence in {"feature", "target_user", "security", "contradiction", "third_party_context"}:
                return [item for item in candidates if item.get("evidence_type") == query.expected_evidence]
            if query.expected_evidence in {"positioning", "agent_capability", "workflow"}:
                matched = [item for item in candidates if item.get("evidence_type") == query.expected_evidence]
                return matched or candidates[:1]
            return candidates[:1]

    class StaticLiveLLMProvider(LLMProvider):
        provider_name = "DeepSeekLLMProvider"

        def complete_structured(self, purpose, payload, skill_prompt=""):
            return {"__provider_meta": {"request_id": "req-live-contract", "usage": {"total_tokens": 42}}}

    monkeypatch.setattr(
        nodes,
        "build_provider_bundle",
        lambda: ProviderBundle(
            search=StaticLiveSearchProvider(),
            llm=StaticLiveLLMProvider(),
            fixture_mode=False,
            search_mode="duckduckgo",
            llm_mode="deepseek",
            allow_provider_fallback=False,
            allow_empty_search_fallback=False,
        ),
    )

    result = run_workflow(
        Task(
            config=TaskConfig(
                domain="ai_tools",
                target_product="Cursor",
                competitors=["GitHub Copilot"],
                analysis_goals=["验证 live provider contract 下 PM 验收门禁仍然生效。"],
                audience="企业产品经理",
            )
        )
    )

    assert result.trust_summary.fixture_mode is False
    assert result.trust_summary.provider_mode_label == "Live provider run"
    assert result.trust_summary.search_mode == "duckduckgo"
    assert result.trust_summary.llm_mode == "deepseek"
    assert result.report.status == "passed"
    assert "## PM 验收检查" in result.report.markdown
    assert all(claim.supporting_evidence for claim in result.claims if claim.included_in_report)


def test_pm_case_unknown_product_uses_generic_decision_quality_without_template_leakage():
    task = Task(
        config=TaskConfig(
            domain="general_product",
            target_product="Notion Calendar",
            competitors=["Google Calendar"],
            analysis_goals=["比较日历产品的定位、用户旅程、商业化机会和仍需补证的风险。"],
            audience="产品团队",
        )
    )

    result = run_workflow(task)

    assert result.report.status == "reviewing"
    assert "## PM 决策页" in result.report.markdown
    assert "质量 rubric" in result.report.markdown
    assert "不可外发，先处理阻断项" in result.report.markdown
    assert "核心场景使用者" in result.report.markdown
    assert "采购与增长决策者" in result.report.markdown
    assert "个人 AI 辅助开发者" not in result.report.markdown
    assert "编辑器内闭环工作流" not in result.report.markdown
    assert "未绑定证据，需复核" in result.report.markdown
    assert all(not claim.included_in_report for claim in result.claims)
