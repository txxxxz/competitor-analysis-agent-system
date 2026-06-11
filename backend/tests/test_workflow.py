import pytest

from app.core.graph import run_workflow
from app.core.nodes import _xhs_posts_from_search, source_normalizer_node, trust_summary_node, writer_node
from app.models.schemas import Claim, Evidence, GraphState, ReviewTicket, SearchPlan, SearchQuery, Source, Task, TaskConfig


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
            search_provider="anysearch",
            anysearch_api_key="test-key",
            anysearch_base_url="https://api.anysearch.com/v1/search",
            anysearch_max_results=3,
            anysearch_content_types=(),
            llm_provider="seed",
            deepseek_api_key="",
            deepseek_base_url="https://api.deepseek.com/chat/completions",
            deepseek_model="deepseek-chat",
            seed_api_key="",
            seed_base_url="",
            seed_model="",
            lightweight_llm_provider="seed",
            lightweight_seed_api_key="test-key",
            lightweight_seed_base_url="https://ark.cn-beijing.volces.com/api/v3/chat/completions",
            lightweight_seed_model="ep-test",
            allow_provider_fallback=True,
            allow_empty_search_fallback=True,
        )
    )

    assert isinstance(bundle.search, AnySearchProvider)
    assert isinstance(bundle.llm, MockLLMProvider)
    assert bundle.search_mode == "anysearch"
    assert bundle.llm_mode == "mock"
    assert bundle.fixture_mode is True


def test_anysearch_provider_reads_nested_data_results(monkeypatch):
    import json

    from app.models.schemas import SearchQuery
    from app.providers.anysearch import AnySearchProvider

    class FakeResponse:
        headers = {"x-request-id": "req-test"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "results": [
                            {
                                "title": "Cursor · Pricing",
                                "url": "https://cursor.com/pricing",
                                "snippet": "Pricing plans for Cursor.",
                                "content": "Pricing plans for Cursor.",
                            }
                        ]
                    },
                }
            ).encode("utf-8")

    monkeypatch.setattr("app.providers.anysearch.urlopen", lambda *_args, **_kwargs: FakeResponse())

    provider = AnySearchProvider(api_key="test-key", base_url="https://api.anysearch.com/v1/search")
    results = provider.search(
        "task_test",
        SearchQuery(
            query="Cursor pricing official",
            product="Cursor",
            expected_evidence="pricing",
            source_preference="official_pricing_page",
        ),
    )

    assert len(results) == 1
    assert results[0]["title"] == "Cursor · Pricing"
    assert results[0]["url"] == "https://cursor.com/pricing"
    assert results[0]["summary"] == "Pricing plans for Cursor."


def test_generate_user_research_survey_returns_skill_source(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("LIGHTWEIGHT_LLM_PROVIDER", "mock")
    client = TestClient(app)

    response = client.post(
        "/api/v1/surveys/generate",
        json={
            "product_name": "Cursor 企业版",
            "research_goal": "验证研发团队采用 AI 编程工具的核心动机、阻力和付费意愿。",
            "target_users": "10-100 人研发团队的工程负责人",
            "scenario": "面向中国市场，避免引导性问题。",
            "question_count": 10,
            "language": "zh-CN",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["skill_source"]["repository"] == "https://github.com/rendis/surveygo"
    assert data["skill_source"]["license"] == "MIT"
    assert data["questions"]
    assert data["survey_json"]["title"]


def test_xhs_status_and_qrcode_endpoints_support_login_flow(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr("app.providers.xhs_mcp.XhsMcpClient.check_login_status", lambda self: {"logged_in": False, "message": "未登录"})
    monkeypatch.setattr(
        "app.providers.xhs_mcp.XhsMcpClient.get_login_qrcode",
        lambda self: {
            "qrcode_base64": "abc123",
            "qr_url": "xhsdiscover://scan-demo",
            "qr_id": "qr_123",
            "code": "code_123",
            "expires_in_seconds": 120,
        },
    )
    client = TestClient(app)

    status_response = client.get("/api/v1/social/xhs/status")
    qr_response = client.post("/api/v1/social/xhs/login-qrcode", json={})

    assert status_response.status_code == 200
    status_data = status_response.json()["data"]
    assert status_data["connected"] is True
    assert status_data["login_required"] is True
    assert status_data["logged_in"] is False

    assert qr_response.status_code == 200
    qr_data = qr_response.json()["data"]
    assert qr_data["connected"] is True
    assert qr_data["qrcode_base64"] == "abc123"
    assert qr_data["qr_url"] == "xhsdiscover://scan-demo"
    assert qr_data["qr_id"] == "qr_123"
    assert qr_data["code"] == "code_123"
    assert qr_data["expires_in_seconds"] == 120


def test_xhs_status_and_qrcode_endpoints_recognize_mcp_success_text(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr("app.providers.xhs_mcp.XhsMcpClient.check_login_status", lambda self: {"message": "Logged in as: demo-user"})
    monkeypatch.setattr("app.providers.xhs_mcp.XhsMcpClient.check_qrcode_status", lambda self, qr_id, code: {"message": "Login successful! Cookies saved."})
    client = TestClient(app)

    status_response = client.get("/api/v1/social/xhs/status")
    qr_status_response = client.post("/api/v1/social/xhs/qrcode-status", json={"qr_id": "qr_123", "code": "code_123"})

    assert status_response.status_code == 200
    status_data = status_response.json()["data"]
    assert status_data["logged_in"] is True
    assert status_data["login_required"] is False

    assert qr_status_response.status_code == 200
    qr_status_data = qr_status_response.json()["data"]
    assert qr_status_data["logged_in"] is True
    assert qr_status_data["login_required"] is False


def test_xhs_status_treats_current_logged_account_permission_message_as_login_required(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(
        "app.providers.xhs_mcp.XhsMcpClient.check_login_status",
        lambda self: {"message": "Not logged in: 您当前登录的账号没有权限访问\nUse get_login_qrcode to log in."},
    )
    client = TestClient(app)

    response = client.get("/api/v1/social/xhs/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["logged_in"] is False
    assert data["login_required"] is True


def test_xhs_browser_mode_defaults_to_headed_on_desktop(monkeypatch):
    from app.providers import xhs_mcp

    monkeypatch.delenv("XHS_MCP_BROWSER_HEADLESS", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(xhs_mcp.sys, "platform", "darwin")
    assert xhs_mcp._xhs_browser_headless() is False

    monkeypatch.setenv("XHS_MCP_BROWSER_HEADLESS", "true")
    assert xhs_mcp._xhs_browser_headless() is True


def test_workflow_collects_xhs_social_listening_with_mcp(monkeypatch):
    requested_comment_limits = []
    monkeypatch.setattr("app.providers.xhs_mcp.XhsMcpClient.check_login_status", lambda self: {"logged_in": True})
    def fake_search(self, keyword, filters=None):
        assert filters["limit"] == "50"
        return {
            "items": [
                {
                    "feed_id": f"feed_{index}",
                    "xsec_token": f"token_{index}",
                    "title": f"{keyword} 真实体验 {index}",
                    "desc": "好用但是价格有点贵，适合重度用户。",
                    "liked_count": 88 + index,
                    "comment_count": 35,
                }
                for index in range(1, 19)
            ]
        }

    monkeypatch.setattr("app.providers.xhs_mcp.XhsMcpClient.search_feeds", fake_search)
    monkeypatch.setattr(
        "app.providers.xhs_mcp.XhsMcpClient.get_feed_detail",
        lambda self, feed_id, xsec_token, load_all_comments=False: {
            "data": {
                "title": "Cursor 真实体验",
                "desc": "整体推荐，但团队版价格需要比较。",
            }
        },
    )

    def fake_comments(self, feed_id, limit=30, cursor="", xsec_token=""):
        requested_comment_limits.append(limit)
        return {
            "note_id": feed_id,
            "comments": [
                {"id": f"c{index}", "content": f"第 {index} 条评论，好用但价格贵。", "like_count": index}
                for index in range(1, 36)
            ],
            "count": 35,
        }

    monkeypatch.setattr("app.providers.xhs_mcp.XhsMcpClient.get_feed_comments", fake_comments)

    result = run_workflow(
        Task(
            config=TaskConfig(
                domain="ai_tools",
                target_product="Cursor",
                competitors=["GitHub Copilot"],
                analysis_goals=["社媒舆情"],
                social_listening={
                    "enabled": True,
                    "platforms": [
                        {
                            "platform": "xiaohongshu",
                            "enabled": True,
                            "keywords": ["Cursor"],
                            "max_posts_per_keyword": 15,
                            "fetch_comments": True,
                            "max_comments_per_post": 30,
                        }
                    ],
                },
            )
        )
    )

    assert result.social_posts
    assert result.social_insights
    assert any(evidence.evidence_type == "social_sentiment" for evidence in result.evidence)
    assert "社媒舆情洞察" in result.report.markdown
    assert result.report.social_insights
    assert requested_comment_limits
    assert set(requested_comment_limits) == {30}
    assert len(result.social_posts) == 15
    assert all(len(post.comments) == 30 for post in result.social_posts)
    collected_xhs_insights = [
        insight for insight in result.social_insights if insight.platform == "xiaohongshu" and insight.status == "collected"
    ]
    assert len(collected_xhs_insights) == 1
    assert "15 条笔记、450 条评论" in result.social_insights[0].summary
    assert result.social_insights[0].findings
    assert any(finding.category == "positive" for finding in result.social_insights[0].findings)


def test_workflow_does_not_search_xhs_when_permission_message_requires_login(monkeypatch):
    searched = False

    monkeypatch.setattr(
        "app.providers.xhs_mcp.XhsMcpClient.check_login_status",
        lambda self: {"message": "Not logged in: 您当前登录的账号没有权限访问\nUse get_login_qrcode to log in."},
    )

    def fake_search(self, keyword, filters=None):
        nonlocal searched
        searched = True
        return {"feeds": [{"id": "feed_1", "title": "不应被搜索到"}]}

    monkeypatch.setattr("app.providers.xhs_mcp.XhsMcpClient.search_feeds", fake_search)

    result = run_workflow(
        Task(
            config=TaskConfig(
                domain="ai_tools",
                target_product="Cursor",
                competitors=["GitHub Copilot"],
                analysis_goals=["社媒舆情"],
                social_listening={
                    "enabled": True,
                    "platforms": [{"platform": "xiaohongshu", "enabled": True, "keywords": ["Cursor"]}],
                },
            )
        )
    )

    assert searched is False
    assert not result.social_posts
    assert any(ticket.source_query_hint == "XHS_LOGIN_REQUIRED" for ticket in result.review_tickets)


def test_workflow_surfaces_xhs_search_failure_message(monkeypatch):
    monkeypatch.setattr("app.providers.xhs_mcp.XhsMcpClient.check_login_status", lambda self: {"message": "Logged in as: demo-user"})
    monkeypatch.setattr(
        "app.providers.xhs_mcp.XhsMcpClient.search_feeds",
        lambda self, keyword, filters=None: {"message": "Search failed: not logged in"},
    )

    result = run_workflow(
        Task(
            config=TaskConfig(
                domain="ai_tools",
                target_product="Cursor",
                competitors=["GitHub Copilot"],
                analysis_goals=["社媒舆情"],
                social_listening={
                    "enabled": True,
                    "platforms": [{"platform": "xiaohongshu", "enabled": True, "keywords": ["Cursor"]}],
                },
            )
        )
    )

    assert not result.social_posts
    assert any(ticket.source_query_hint == "XHS_LOGIN_REQUIRED" for ticket in result.review_tickets)
    assert any(event.event_type == "xhs_search_failed" for event in result.trace)
    assert "当前阻断" in result.report.markdown


def test_xhs_search_parser_supports_installed_mcp_feed_shape():
    posts = _xhs_posts_from_search(
        {
            "feeds": [
                {
                    "id": "feed_1",
                    "title": "Cursor 真实体验",
                    "user": "产品同学",
                    "likes": "88",
                    "xsec_token": "token_1",
                }
            ],
            "count": 1,
        },
        "Cursor",
        10,
    )

    assert len(posts) == 1
    assert posts[0].post_id == "feed_1"
    assert posts[0].author == "产品同学"
    assert posts[0].like_count == 88
    assert posts[0].xsec_token == "token_1"


def test_xhs_search_parser_supports_results_payload_and_xsec_token_in_link():
    posts = _xhs_posts_from_search(
        {
            "keyword": "飞书",
            "results": [
                {
                    "feed_id": "feed_42",
                    "title": "飞书使用体验",
                    "author": "demo-user",
                    "link": "https://www.xiaohongshu.com/explore/feed_42?xsec_token=token_42&xsec_source=pc_search",
                    "desc": "搜索结果里已经带了访问链接。",
                }
            ],
        },
        "飞书",
        10,
    )

    assert len(posts) == 1
    assert posts[0].post_id == "feed_42"
    assert posts[0].xsec_token == "token_42"
    assert posts[0].url.endswith("xsec_source=pc_search")


def test_workflow_supports_manual_xhs_summary_when_mcp_not_available():
    result = run_workflow(
        Task(
            config=TaskConfig(
                domain="ai_tools",
                target_product="Cursor",
                competitors=["GitHub Copilot"],
                analysis_goals=["社媒舆情"],
                social_listening={
                    "enabled": True,
                    "manual_xhs_summary": "小红书点点 AI 总结：用户觉得 Cursor 好用但价格贵，也有人推荐 GitHub Copilot。",
                    "manual_source_urls": ["https://www.xiaohongshu.com/explore/demo"],
                    "platforms": [{"platform": "xiaohongshu", "enabled": False}],
                },
            )
        )
    )

    assert result.social_insights
    assert result.social_insights[0].status == "manual"
    assert any(source.source_type == "social_xiaohongshu_manual" for source in result.sources)
    assert "小红书点点 AI 总结" in result.report.markdown


def test_workflow_requires_xhs_login_even_when_manual_summary_is_present(monkeypatch):
    monkeypatch.setattr(
        "app.providers.xhs_mcp.XhsMcpClient.check_login_status",
        lambda self: {"message": "Not logged in: 请登录\nUse get_login_qrcode to log in."},
    )

    result = run_workflow(
        Task(
            config=TaskConfig(
                domain="ai_tools",
                target_product="飞书",
                competitors=["钉钉"],
                analysis_goals=["社媒舆情"],
                social_listening={
                    "enabled": True,
                    "manual_xhs_summary": "小红书点点 AI 总结：用户觉得飞书协作体验顺滑，但也提到开放接口权限和配置门槛偏高。",
                    "platforms": [{"platform": "xiaohongshu", "enabled": True, "keywords": ["飞书"]}],
                },
            )
        )
    )

    assert result.social_insights
    assert result.social_insights[0].status == "manual"
    assert any(ticket.source_query_hint == "XHS_LOGIN_REQUIRED" for ticket in result.review_tickets)
    assert "小红书点点 AI 总结" in result.report.markdown


def test_provider_factory_falls_back_to_mock_when_anysearch_key_missing():
    from app.providers.factory import ProviderSettings, build_provider_bundle
    from app.providers.mock_search import MockSearchProvider

    bundle = build_provider_bundle(
        ProviderSettings(
            use_mock_search=False,
            use_mock_llm=True,
            search_provider="anysearch",
            anysearch_api_key="",
            anysearch_base_url="https://api.anysearch.com/v1/search",
            anysearch_max_results=5,
            anysearch_content_types=(),
            llm_provider="seed",
            deepseek_api_key="",
            deepseek_base_url="https://api.deepseek.com/chat/completions",
            deepseek_model="deepseek-chat",
            seed_api_key="",
            seed_base_url="",
            seed_model="",
            lightweight_llm_provider="seed",
            lightweight_seed_api_key="test-key",
            lightweight_seed_base_url="https://ark.cn-beijing.volces.com/api/v3/chat/completions",
            lightweight_seed_model="ep-test",
            allow_provider_fallback=True,
            allow_empty_search_fallback=True,
        )
    )

    assert isinstance(bundle.search, MockSearchProvider)
    assert bundle.search_mode == "mock_fallback"
    assert bundle.warnings


def test_provider_factory_selects_duckduckgo_and_deepseek_when_configured():
    from app.providers.deepseek import DeepSeekLLMProvider
    from app.providers.duckduckgo import DuckDuckGoSearchProvider
    from app.providers.factory import ProviderSettings, build_provider_bundle

    bundle = build_provider_bundle(
        ProviderSettings(
            use_mock_search=False,
            use_mock_llm=False,
            search_provider="duckduckgo",
            anysearch_api_key="",
            anysearch_base_url="https://api.anysearch.com/v1/search",
            anysearch_max_results=5,
            anysearch_content_types=(),
            llm_provider="deepseek",
            deepseek_api_key="test-key",
            deepseek_base_url="https://api.deepseek.com/chat/completions",
            deepseek_model="deepseek-4-flash",
            seed_api_key="",
            seed_base_url="",
            seed_model="",
            lightweight_llm_provider="seed",
            lightweight_seed_api_key="test-key",
            lightweight_seed_base_url="https://ark.cn-beijing.volces.com/api/v3/chat/completions",
            lightweight_seed_model="ep-test",
            allow_provider_fallback=False,
            allow_empty_search_fallback=False,
        )
    )

    assert isinstance(bundle.search, DuckDuckGoSearchProvider)
    assert isinstance(bundle.llm, DeepSeekLLMProvider)
    assert bundle.search_mode == "duckduckgo"
    assert bundle.llm_mode == "deepseek"
    assert bundle.fixture_mode is False


def test_load_provider_settings_defaults_anysearch_max_results_to_15(monkeypatch):
    from app.providers import factory

    monkeypatch.delenv("ANYSEARCH_MAX_RESULTS", raising=False)
    monkeypatch.setattr(factory, "_load_env_files", lambda: None)
    monkeypatch.setattr(factory, "_stored_provider_settings", lambda: {})

    settings = factory.load_provider_settings()

    assert settings.anysearch_max_results == 15


def test_load_provider_settings_normalizes_llm_to_deepseek(monkeypatch):
    from app.providers import factory

    monkeypatch.setenv("LLM_PROVIDER", "seed")
    monkeypatch.setenv("LIGHTWEIGHT_LLM_PROVIDER", "seed")
    monkeypatch.setattr(factory, "_load_env_files", lambda: None)
    monkeypatch.setattr(factory, "_stored_provider_settings", lambda: {})

    settings = factory.load_provider_settings()

    assert settings.llm_provider == "deepseek"
    assert settings.lightweight_llm_provider == "deepseek"


def test_load_provider_settings_normalizes_legacy_deepseek_model(monkeypatch):
    from app.providers import factory

    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-4-flash")
    monkeypatch.setattr(factory, "_load_env_files", lambda: None)
    monkeypatch.setattr(factory, "_stored_provider_settings", lambda: {})

    settings = factory.load_provider_settings()

    assert settings.deepseek_model == "deepseek-chat"


def test_duckduckgo_provider_defaults_to_15_results():
    from app.providers.duckduckgo import DuckDuckGoSearchProvider

    provider = DuckDuckGoSearchProvider()

    assert provider.max_results == 15


def test_source_normalizer_rejects_ambiguous_cursor_css_noise():
    task = Task(
        config=TaskConfig(
            domain="ai_tools",
            target_product="Cursor",
            competitors=["GitHub Copilot"],
            analysis_goals=["positioning"],
        )
    )
    query = SearchQuery(
        query="Anysphere Cursor AI code editor official positioning product page",
        product="Cursor",
        expected_evidence="positioning",
        source_preference="official_homepage",
    )
    state = GraphState(
        task=task,
        search_plan=SearchPlan(task_id=task.task_id, queries=[query]),
        raw_sources=[
            {
                "title": "cursor - CSS: Cascading Style Sheets | MDN",
                "url": "https://developer.mozilla.org/en-US/docs/Web/CSS/cursor",
                "source_type": "web_search",
                "product": "Cursor",
                "evidence_type": "positioning",
                "summary": "The cursor CSS property sets the mouse cursor.",
                "locator": "MDN",
                "content": "The cursor CSS property sets the mouse cursor.",
                "query": query.query,
            },
            {
                "title": "Cursor — Build Software with AI Agents",
                "url": "https://cursor.com/product",
                "source_type": "web_search",
                "product": "Cursor",
                "evidence_type": "positioning",
                "summary": "Cursor plans, writes, and reviews code using AI agents.",
                "locator": "https://cursor.com/product",
                "content": "Cursor plans, writes, and reviews code using AI agents.",
                "query": query.query,
            },
        ],
    )

    normalized = source_normalizer_node(state)

    assert len(normalized.sources) == 1
    assert normalized.sources[0].url == "https://cursor.com/product"
    assert normalized.sources[0].source_type == "official_homepage"
    assert "rejected 1 irrelevant" in normalized.trace[-1].summary


def test_source_normalizer_does_not_treat_copilot_as_pilot_noise():
    task = Task(
        config=TaskConfig(
            domain="ai_tools",
            target_product="GitHub Copilot",
            competitors=["Cursor"],
            analysis_goals=["third-party support"],
        )
    )
    query = SearchQuery(
        query="GitHub Copilot third party review benchmark user feedback",
        product="GitHub Copilot",
        expected_evidence="third_party_context",
        source_preference="independent_web",
    )
    state = GraphState(
        task=task,
        search_plan=SearchPlan(task_id=task.task_id, queries=[query]),
        raw_sources=[
            {
                "title": "Developer community discussion of GitHub Copilot",
                "url": "https://example.com/community/github-copilot-feedback",
                "source_type": "third_party_relevant",
                "product": "GitHub Copilot",
                "evidence_type": "third_party_context",
                "summary": "GitHub Copilot is discussed as a familiar AI coding assistant for developers.",
                "locator": "Community thread",
                "content": "GitHub Copilot feedback from developers compares workflow fit and team controls.",
                "query": query.query,
            }
        ],
    )

    normalized = source_normalizer_node(state)

    assert len(normalized.sources) == 1
    assert normalized.sources[0].source_type == "third_party_relevant"


def test_source_normalizer_recognizes_chinese_product_official_domains():
    task = Task(
        config=TaskConfig(
            domain="ai_tools",
            target_product="飞书",
            competitors=["钉钉", "企业微信"],
            analysis_goals=["OpenCloud 接入开放性"],
        )
    )
    queries = [
        SearchQuery(query="飞书开放平台官方文档", product="飞书", expected_evidence="feature", source_preference="official_docs"),
        SearchQuery(query="钉钉开放平台官方文档", product="钉钉", expected_evidence="feature", source_preference="official_docs"),
        SearchQuery(query="企业微信开发者中心官方文档", product="企业微信", expected_evidence="feature", source_preference="official_docs"),
    ]
    state = GraphState(
        task=task,
        search_plan=SearchPlan(task_id=task.task_id, queries=queries),
        raw_sources=[
            {
                "title": "应用类型与能力 - 飞书开放平台",
                "url": "https://open.feishu.cn/document/platform-overveiw/overview",
                "source_type": "web_search",
                "product": "飞书",
                "evidence_type": "feature",
                "summary": "飞书开放平台提供应用、机器人、网页应用等能力。",
                "locator": "https://open.feishu.cn/document/platform-overveiw/overview",
                "content": "飞书开放平台提供应用、机器人、网页应用等能力。",
                "query": queries[0].query,
            },
            {
                "title": "开放文档 - 钉钉开放平台",
                "url": "https://developers.dingtalk.com/document/app/overview",
                "source_type": "web_search",
                "product": "钉钉",
                "evidence_type": "feature",
                "summary": "钉钉开放平台提供 API、机器人和应用能力。",
                "locator": "https://developers.dingtalk.com/document/app/overview",
                "content": "钉钉开放平台提供 API、机器人和应用能力。",
                "query": queries[1].query,
            },
            {
                "title": "首页 - 企业微信开发者中心",
                "url": "https://developer.work.weixin.qq.com/document/path/90487",
                "source_type": "web_search",
                "product": "企业微信",
                "evidence_type": "feature",
                "summary": "企业微信开发者中心提供开放接口和工具资源。",
                "locator": "https://developer.work.weixin.qq.com/document/path/90487",
                "content": "企业微信开发者中心提供开放接口和工具资源。",
                "query": queries[2].query,
            },
        ],
    )

    normalized = source_normalizer_node(state)

    assert len(normalized.sources) == 3
    assert {source.product: source.source_type for source in normalized.sources} == {
        "飞书": "official_docs",
        "钉钉": "official_docs",
        "企业微信": "official_docs",
    }
    assert {source.product: source.confidence for source in normalized.sources} == {
        "飞书": "high",
        "钉钉": "high",
        "企业微信": "high",
    }


def test_writer_marks_report_reviewing_when_tickets_remain_open(monkeypatch):
    monkeypatch.setenv("USE_MOCK_LLM", "true")
    task = Task(
        config=TaskConfig(
            domain="ai_tools",
            target_product="Cursor",
            competitors=["GitHub Copilot"],
            analysis_goals=["positioning"],
        )
    )
    source = Source(
        task_id=task.task_id,
        title="Cursor — Build Software with AI Agents",
        url="https://cursor.com/product",
        source_type="official_homepage",
        product="Cursor",
        query="Cursor official positioning",
        confidence="high",
        content="Cursor plans, writes, and reviews code using AI agents.",
    )
    evidence = Evidence(
        task_id=task.task_id,
        source_id=source.source_id,
        product="Cursor",
        evidence_type="positioning",
        summary="Cursor plans, writes, and reviews code using AI agents.",
        quote_or_locator=source.url,
        confidence="high",
    )
    claim = Claim(
        task_id=task.task_id,
        claim="Cursor plans, writes, and reviews code using AI agents.",
        product="Cursor",
        claim_type="positioning",
        supporting_evidence=[evidence.evidence_id],
        confidence="high",
        verified_status="passed",
        included_in_report=True,
    )
    ticket = ReviewTicket(
        task_id=task.task_id,
        reviewer="CriticAgent",
        target_node="ResearchAgent",
        reason="GitHub Copilot lacks pricing evidence.",
        required_action="Collect pricing evidence.",
        product="GitHub Copilot",
        missing_evidence_type="pricing",
    )
    state = GraphState(task=task, sources=[source], evidence=[evidence], claims=[claim], review_tickets=[ticket])
    state = trust_summary_node(state)
    state = writer_node(state)

    assert state.trust_summary.unresolved_ticket_count == 1
    assert state.report.status == "reviewing"
    assert "需人工复核" in state.report.markdown


def test_reviewing_report_requires_draft_export(tmp_path, monkeypatch):
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
                analysis_goals=["positioning"],
            )
        )
    )
    result = run_workflow(task)
    result.report.status = "reviewing"
    store.save_result(result)

    blocked_response = client.get(f"/api/v1/tasks/{task.task_id}/report/export?format=markdown")
    draft_response = client.get(f"/api/v1/tasks/{task.task_id}/report/export?format=markdown&allow_draft=true")

    assert blocked_response.status_code == 409
    assert "reviewing" in blocked_response.json()["detail"]
    assert draft_response.status_code == 200
    assert "Draft export: report status is reviewing." in draft_response.json()["data"]["content"]


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


@pytest.mark.parametrize("closed_status", ["resolved", "dismissed", "blocked"])
def test_v1_review_ticket_rerun_rejects_closed_ticket(tmp_path, monkeypatch, closed_status):
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
    ticket = result.review_tickets[0]
    ticket.status = closed_status
    store.save_result(result)

    response = client.post(f"/api/v1/review-tickets/{ticket.ticket_id}/rerun", json={"preserve_existing_artifacts": True})

    assert response.status_code == 409
    assert f"already {closed_status}" in response.json()["detail"]


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
    monkeypatch.setattr(routes, "_provider_status", lambda: {"workflow_ready": True, "issues": []})
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


def test_research_plan_includes_independent_web_queries():
    task = Task(
        config=TaskConfig(
            domain="ai_tools",
            target_product="Cursor",
            competitors=["GitHub Copilot"],
            analysis_goals=["positioning", "pricing", "agent_capability"],
        )
    )

    result = run_workflow(task)

    independent_queries = [query for query in result.search_plan.queries if query.source_preference == "independent_web"]
    assert independent_queries
    assert any("用户反馈" in query.query or "customer review" in query.query for query in independent_queries)
    assert any("社区讨论" in query.query for query in independent_queries)
    assert any("security privacy incident controversy" in query.query for query in independent_queries)
    assert any(query.expected_evidence == "third_party_context" for query in independent_queries)
    assert "independent_web" in result.search_plan.preferred_source_types
    assert any(source.source_type == "third_party_relevant" for source in result.sources)
    assert any(item.evidence_type == "third_party_context" for item in result.evidence)
    assert "第三方来源占比" in result.report.markdown


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


def test_high_strictness_accepts_non_official_reviewed_evidence_and_caps_confidence():
    from app.core.nodes import evidence_reviewer_node

    state = _strictness_state("high", source_type="community_forum", confidence="high")

    reviewed = evidence_reviewer_node(state)
    claim = reviewed.claims[0]

    assert claim.verified_status == "passed"
    assert claim.included_in_report is True
    assert claim.confidence == "medium"


def test_high_strictness_accepts_medium_official_evidence():
    from app.core.nodes import evidence_reviewer_node

    state = _strictness_state("high", source_type="official_homepage", confidence="medium")

    reviewed = evidence_reviewer_node(state)
    claim = reviewed.claims[0]

    assert claim.verified_status == "passed"
    assert claim.included_in_report is True


def test_high_strictness_accepts_cross_validated_relevant_sources():
    from app.core.nodes import evidence_reviewer_node

    state = _strictness_state("high", source_type="third_party_relevant", confidence="medium")
    second_source = Source(
        source_id="src_test_2",
        task_id=state.task.task_id,
        title="Second relevant source",
        url="https://example.com/second",
        source_type="third_party_relevant",
        product="Cursor",
        query="Cursor positioning",
        confidence="medium",
        risk="Synthetic corroborating source.",
        content="Cursor is an AI-native editor discussed in another relevant source.",
    )
    second_evidence = Evidence(
        evidence_id="ev_test_2",
        task_id=state.task.task_id,
        source_id=second_source.source_id,
        product="Cursor",
        evidence_type="positioning",
        summary="Cursor is described as an AI-native editor in a second relevant source.",
        quote_or_locator="Second relevant source",
        confidence="medium",
        risk=second_source.risk,
    )
    state.sources.append(second_source)
    state.evidence.append(second_evidence)
    state.claims[0].supporting_evidence.append(second_evidence.evidence_id)

    reviewed = evidence_reviewer_node(state)
    claim = reviewed.claims[0]

    assert claim.verified_status == "passed"
    assert claim.included_in_report is True


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


def test_pricing_model_caps_third_party_only_confidence_to_medium():
    from app.core.nodes import _build_pricing_model

    task = Task(
        config=TaskConfig(
            domain="ai_tools",
            target_product="Cursor",
            competitors=[],
            analysis_goals=["pricing"],
        )
    )
    source = Source(
        source_id="src_pricing",
        task_id=task.task_id,
        title="Third-party pricing roundup",
        url="https://example.com/cursor-pricing",
        source_type="third_party_relevant",
        product="Cursor",
        query="Cursor pricing review",
        confidence="high",
        risk="Synthetic third-party pricing source.",
        content="Cursor pricing comparison.",
    )
    evidence = Evidence(
        evidence_id="ev_pricing",
        task_id=task.task_id,
        source_id=source.source_id,
        product="Cursor",
        evidence_type="pricing",
        summary="Free, Pro, and Business plans are described in a pricing roundup.",
        quote_or_locator=source.url,
        confidence="high",
        risk=source.risk,
    )

    pricing = _build_pricing_model(GraphState(task=task, sources=[source], evidence=[evidence]))

    assert pricing.plans[0].confidence == "medium"


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
            claim_id = payload["included_claims"][0]["claim_id"]
            evidence_id = payload["included_claims"][0]["supporting_evidence"][0]
            return {
                "executive_summary": [{"text": "Seed synthesized an executive summary.", "claim_ids": [claim_id], "evidence_ids": [evidence_id]}],
                "strategic_recommendations": [
                    {"text": "Seed recommended validating pricing evidence.", "claim_ids": [claim_id], "evidence_ids": [evidence_id]},
                    {"text": "Seed attempted an unbound recommendation."},
                ],
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
    assert written.report.markdown.index("## 结构化综合摘要") < written.report.markdown.index("## 决策摘要")
    assert written.report.markdown.index("## 结构化综合摘要") < written.report.markdown.index("## 数据来源（Resources）")
    assert "依据：cl_" in written.report.markdown
    assert "未绑定证据，需复核：Seed attempted an unbound recommendation." in written.report.markdown
    assert any(call.tool == "SeedLLMProvider" and call.operation == "complete_structured" for call in written.tool_calls)
    assert any(event.event_type == "llm_enhancement_applied" for event in written.trace)


def test_writer_keeps_raw_excerpt_only_in_resources():
    from app.core.nodes import writer_node

    state = _strictness_state("high", source_type="third_party_relevant", confidence="medium")
    state.sources[0].content = "# Vendor Copy\n\n**Cursor** is discussed as an AI-native editor with <b>raw HTML</b>."

    written = writer_node(state)

    assert written.report is not None
    assert "第三方来源占比" in written.report.markdown
    assert "## 决策摘要" in written.report.markdown
    assert "## 关键差异洞察" in written.report.markdown
    assert "## 数据来源（Resources）" in written.report.markdown
    assert "原文摘录：" in written.report.markdown
    assert "<b>" not in written.report.markdown
    assert "# Vendor Copy" not in written.report.markdown


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


def test_review_ticket_improvement_requires_new_bound_passed_claim():
    from app.core.nodes import resolve_review_ticket_improvements

    state = _strictness_state("high", source_type="official_homepage", confidence="high")
    ticket = ReviewTicket(
        task_id=state.task.task_id,
        reviewer="CriticAgent",
        target_node="ResearchAgent",
        status="rerun_started",
        product="Cursor",
        missing_evidence_type="positioning",
        reason="Cursor positioning evidence should be improved.",
        required_action="Collect positioning evidence.",
        before_evidence_ids=[state.evidence[0].evidence_id],
        before_claim_statuses=[
            {
                "claim_id": state.claims[0].claim_id,
                "verified_status": "passed",
                "included_in_report": True,
                "supporting_evidence": state.claims[0].supporting_evidence,
            }
        ],
    )
    state.review_tickets = [ticket]

    resolved = resolve_review_ticket_improvements(state)

    assert resolved == 0
    assert ticket.status == "rerun_started"
    assert ticket.added_evidence_ids == []
    assert ticket.improved_claim_ids == []
    assert ticket.after_claim_statuses


def test_review_ticket_improvement_rejects_wrong_product_or_type():
    from app.core.nodes import resolve_review_ticket_improvements

    state = _strictness_state("high", source_type="official_homepage", confidence="high")
    state.evidence[0].product = "GitHub Copilot"
    state.claims[0].product = "GitHub Copilot"
    ticket = ReviewTicket(
        task_id=state.task.task_id,
        reviewer="CriticAgent",
        target_node="ResearchAgent",
        status="rerun_started",
        product="Cursor",
        missing_evidence_type="positioning",
        reason="Cursor positioning evidence should be improved.",
        required_action="Collect positioning evidence.",
        before_evidence_ids=[],
        before_claim_statuses=[],
    )
    state.review_tickets = [ticket]

    resolved = resolve_review_ticket_improvements(state)

    assert resolved == 0
    assert ticket.status == "rerun_started"
    assert ticket.added_evidence_ids == []
    assert ticket.improved_claim_ids == []


def test_review_ticket_improvement_rejects_added_evidence_when_claim_is_downgraded():
    from app.core.nodes import resolve_review_ticket_improvements

    state = _strictness_state("high", source_type="official_homepage", confidence="high")
    state.claims[0].verified_status = "downgraded"
    state.claims[0].included_in_report = False
    ticket = ReviewTicket(
        task_id=state.task.task_id,
        reviewer="CriticAgent",
        target_node="ResearchAgent",
        status="rerun_started",
        product="Cursor",
        missing_evidence_type="positioning",
        reason="Cursor positioning evidence should be improved.",
        required_action="Collect positioning evidence.",
        before_evidence_ids=[],
        before_claim_statuses=[],
    )
    state.review_tickets = [ticket]

    resolved = resolve_review_ticket_improvements(state)

    assert resolved == 0
    assert ticket.status == "rerun_started"
    assert ticket.added_evidence_ids == [state.evidence[0].evidence_id]
    assert ticket.improved_claim_ids == []


def test_interaction_ticket_rerun_records_before_after_snapshot():
    from app.core.graph import rerun_review_ticket

    task = Task(
        config=TaskConfig(
            domain="ai_tools",
            target_product="Cursor",
            competitors=[],
            analysis_goals=["browser walkthrough"],
        )
    )
    source = Source(
        task_id=task.task_id,
        title="Cursor Features",
        url="https://cursor.com/features",
        source_type="official_docs",
        product="Cursor",
        query="Cursor official product features docs",
        confidence="high",
        content="Cursor feature workflow.",
    )
    ticket = ReviewTicket(
        task_id=task.task_id,
        reviewer="CriticAgent",
        target_node="InteractionAgent",
        status="open",
        product="Cursor",
        missing_evidence_type="browser_interaction",
        reason="Cursor lacks browser interaction evidence.",
        required_action="Record browser interaction path.",
        preferred_source_type="browser_walkthrough",
    )
    result = GraphState(
        task=task,
        sources=[source],
        evidence=[
            Evidence(
                task_id=task.task_id,
                source_id=source.source_id,
                product="Cursor",
                evidence_type="feature",
                summary="Cursor feature coverage centers on editor-native AI workflows.",
                quote_or_locator="Features fixture",
                interaction_path=["Editor", "Agent panel", "Apply changes"],
                confidence="high",
            )
        ],
        raw_sources=[
            {
                "title": source.title,
                "url": source.url,
                "source_type": source.source_type,
                "product": source.product,
                "content": source.content,
                "evidence_type": "feature",
                "summary": "Cursor feature coverage centers on editor-native AI workflows.",
                "locator": "Features fixture",
                "query": source.query,
                "interaction_steps": ["Editor", "Agent panel", "Apply changes"],
                "interaction_summary": "Cursor workflow was observed from agent panel to applying changes.",
            }
        ],
        review_tickets=[ticket],
    ).result()

    rerun = rerun_review_ticket(result, ticket.ticket_id)
    updated_ticket = next(item for item in rerun.review_tickets if item.ticket_id == ticket.ticket_id)

    assert updated_ticket.status == "resolved"
    assert updated_ticket.before_evidence_ids == []
    assert updated_ticket.before_claim_statuses == []
    assert updated_ticket.added_evidence_ids
    assert updated_ticket.improved_claim_ids
    assert updated_ticket.after_claim_statuses
    assert any(evidence.evidence_type == "browser_interaction" for evidence in rerun.evidence)


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


def test_competitor_recommendation_endpoint_returns_filtered_candidates(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import routes
    from app.main import app
    from app.providers.mock_llm import MockLLMProvider

    monkeypatch.setattr(routes, "build_lightweight_llm_provider", lambda: (MockLLMProvider(), "mock"))

    client = TestClient(app)

    response = client.post(
        "/api/v1/competitors/recommend",
        json={
            "target_product": "Cursor",
            "domain": "ai_tools",
            "existing_competitors": ["Windsurf"],
            "audience": "产品团队",
            "max_results": 5,
        },
    )

    assert response.status_code == 200
    competitors = response.json()["data"]["competitors"]
    normalized = {item.casefold() for item in competitors}
    assert competitors
    assert "cursor" not in normalized
    assert "windsurf" not in normalized
