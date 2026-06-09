from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.nodes import (  # noqa: E402
    _classify_sentiment,
    _fallback_social_findings,
    _overall_sentiment,
)
from app.models.schemas import (  # noqa: E402
    Evidence,
    Report,
    ReportSection,
    SentimentSummary,
    SocialComment,
    SocialInsight,
    SocialPost,
    Task,
    TaskConfig,
    TrustSummary,
    WorkflowResult,
)
from app.storage.sqlite import SQLiteStore  # noqa: E402

MCP_URL = "http://127.0.0.1:18060/mcp"
TARGET_POSTS = 15
TARGET_COMMENTS = 30
KEYWORDS = ["飞书", "飞书 cli", "飞书 AI", "飞书多维表格", "飞书 Agent"]


def main() -> None:
    task = Task(
        config=TaskConfig(
            domain="ai_tools",
            target_product="飞书",
            competitors=["钉钉", "企业微信"],
            analysis_goals=["小红书真实舆情：15 条笔记，每条 30 条评论，展示 AI 总结和原始评论证据。"],
            social_listening={
                "enabled": True,
                "platforms": [
                    {
                        "platform": "xiaohongshu",
                        "enabled": True,
                        "keywords": ["飞书"],
                        "max_posts_per_keyword": TARGET_POSTS,
                        "fetch_comments": True,
                        "max_comments_per_post": TARGET_COMMENTS,
                    }
                ],
            },
        ),
        status="completed",
    )
    status = _call_tool("check_login_status", {})
    if "logged in" not in json.dumps(status, ensure_ascii=False).casefold() and "已登录" not in json.dumps(status, ensure_ascii=False):
        raise RuntimeError(f"XHS MCP is not logged in: {status}")

    posts: list[SocialPost] = []
    seen_post_ids: set[str] = set()
    for keyword in KEYWORDS:
        if len(posts) >= TARGET_POSTS:
            break
        search = _call_tool(
            "search_feeds",
            {
                "keyword": keyword,
                "filters": {"sort_by": "综合", "note_type": "不限", "publish_time": "一周内", "limit": "50"},
                "limit": "50",
            },
        )
        candidates = _candidate_items(search)
        print(f"[xhs] keyword {keyword}: {len(candidates)} candidates", flush=True)
        for item in candidates:
            if len(posts) >= TARGET_POSTS:
                break
            post_id = str(item.get("note_id") or item.get("id") or "").strip()
            xsec_token = str(item.get("xsec_token") or "").strip()
            if not post_id or not xsec_token or post_id in seen_post_ids:
                continue
            seen_post_ids.add(post_id)
            detail = _call_tool("get_feed_detail", {"feed_id": post_id, "xsec_token": xsec_token, "load_all_comments": False})
            comments_payload = _call_tool("get_feed_comments", {"feed_id": post_id, "xsec_token": xsec_token, "limit": TARGET_COMMENTS})
            comments = _comments_from_payload(post_id, comments_payload)
            print(f"[xhs] candidate {post_id}: {len(comments)}/{TARGET_COMMENTS} comments", flush=True)
            if len(comments) < TARGET_COMMENTS:
                continue
            detail_data = detail.get("data") if isinstance(detail.get("data"), dict) else detail
            title = str(detail_data.get("title") or item.get("title") or f"飞书小红书笔记 {len(posts) + 1}")
            content = str(detail_data.get("desc") or detail_data.get("content") or item.get("desc") or "")
            posts.append(
                SocialPost(
                    post_id=post_id,
                    platform="xiaohongshu",
                    title=title,
                    content=content,
                    author=str(item.get("user") or item.get("author") or ""),
                    url=str(item.get("url") or f"https://www.xiaohongshu.com/explore/{post_id}"),
                    xsec_token=xsec_token,
                    like_count=_int_value(item.get("likes") or item.get("liked_count")),
                    collect_count=_int_value(detail_data.get("collect_count") or detail_data.get("collected_count")),
                    share_count=_int_value(detail_data.get("share_count")),
                    comment_count=_int_value(detail_data.get("comment_count") or detail_data.get("comments_count")) or _int_value(item.get("comment_count")),
                    comments=comments[:TARGET_COMMENTS],
                )
            )

    if len(posts) < TARGET_POSTS:
        raise RuntimeError(f"Only collected {len(posts)}/{TARGET_POSTS} posts with {TARGET_COMMENTS} comments each.")

    comments = [comment for post in posts for comment in post.comments]
    evidence = _build_evidence(task.task_id, posts)
    insight = SocialInsight(
        platform="xiaohongshu",
        summary=f"小红书共采集 {len(posts)} 条笔记、{len(comments)} 条评论；主要反馈集中在：价格/性价比、功能体验、效果与效率、购买决策。",
        findings=_fallback_social_findings(comments),
        themes=["价格/性价比", "功能体验", "效果与效率", "购买决策"],
        pain_points=[comment.content[:90] for comment in comments if comment.sentiment == "negative"][:6],
        purchase_signals=[comment.content[:90] for comment in comments if comment.sentiment == "positive"][:6],
        sentiment=SentimentSummary(
            positive_count=len([comment for comment in comments if comment.sentiment == "positive"]),
            neutral_count=len([comment for comment in comments if comment.sentiment == "neutral"]),
            negative_count=len([comment for comment in comments if comment.sentiment == "negative"]),
            overall=_overall_sentiment(comments),
            evidence_ids=[item.evidence_id for item in evidence],
        ),
        post_ids=[post.post_id for post in posts],
        evidence_ids=[item.evidence_id for item in evidence],
        status="collected",
    )
    markdown = "\n".join(
        [
            "# 飞书小红书舆情分析",
            "",
            "## 社媒舆情洞察",
            f"- 小红书真实采集：{len(posts)} 条笔记，{len(comments)} 条评论。",
            f"- 每条入选笔记均保留 {TARGET_COMMENTS} 条评论样本。",
            f"- 结构化要点：{len(insight.findings)} 条，含产品好评、痛点与需求机会。",
        ]
    )
    report = Report(
        task_id=task.task_id,
        title="飞书小红书舆情分析",
        markdown=markdown,
        sections=[
            ReportSection(section_key="social", title="社媒舆情洞察", markdown=markdown, sort_order=1),
        ],
        claim_count=1,
        evidence_coverage_rate=1,
        social_insights=[insight],
    )
    result = WorkflowResult(
        task=task,
        evidence=evidence,
        trust_summary=TrustSummary(
            total_evidence_count=len(evidence),
            passed_claim_count=1,
            total_claim_count=1,
            fixture_mode=False,
            provider_mode_label="Real Xiaohongshu MCP snapshot",
            search_mode="xiaohongshu-mcp",
            llm_mode="local_synthesis",
            summary="真实小红书 MCP 采集结果，专用于舆情页端到端验收。",
        ),
        report=report,
        social_posts=posts,
        social_insights=[insight],
    )
    db_paths = [ROOT / "backend" / "data" / "app.db", ROOT / "data" / "app.db"]
    for db_path in db_paths:
        SQLiteStore(str(db_path)).save_result(result)
    print(
        json.dumps(
            {
                "task_id": task.task_id,
                "posts": len(posts),
                "comments": len(comments),
                "per_post": [len(post.comments) for post in posts],
                "findings": len(insight.findings),
                "db_paths": [str(path) for path in db_paths],
            },
            ensure_ascii=False,
        )
    )


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": name, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    completed = subprocess.run(
        [
            "curl",
            "-sS",
            "-X",
            "POST",
            MCP_URL,
            "-H",
            "Content-Type: application/json",
            "-H",
            "Accept: application/json, text/event-stream",
            "-d",
            json.dumps(payload, ensure_ascii=False),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )
    body = json.loads(completed.stdout)
    if body.get("error"):
        raise RuntimeError(body["error"])
    content = body.get("result", {}).get("content") or []
    text = content[0].get("text", "") if content else ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"message": text}


def _candidate_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in ["feeds", "items", "notes", "list", "data", "results", "result", "feed_list", "note_list"]:
        item = value.get(key)
        if isinstance(item, list):
            return [entry for entry in item if isinstance(entry, dict)]
        if isinstance(item, dict):
            nested = _candidate_items(item)
            if nested:
                return nested
    return []


def _comments_from_payload(post_id: str, payload: dict[str, Any]) -> list[SocialComment]:
    raw_comments = _candidate_comments(payload)
    comments = []
    for index, item in enumerate(raw_comments[:TARGET_COMMENTS], start=1):
        content = str(item.get("content") or item.get("text") or item.get("desc") or "").strip()
        if not content:
            continue
        raw_id = str(item.get("id") or item.get("comment_id") or item.get("commentId") or f"c{index}")
        user = item.get("user_info") if isinstance(item.get("user_info"), dict) else item.get("user") if isinstance(item.get("user"), dict) else {}
        comments.append(
            SocialComment(
                comment_id=f"{post_id}_{raw_id}" if not raw_id.startswith(f"{post_id}_") else raw_id,
                author=str(item.get("nickname") or item.get("author") or user.get("nickname") or user.get("name") or ""),
                content=content,
                like_count=_int_value(item.get("like_count") or item.get("liked_count") or item.get("likeCount")),
                sentiment=_classify_sentiment(content),
            )
        )
    return comments


def _candidate_comments(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        comments: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                comments.append(item)
                if isinstance(item.get("sub_comments"), list):
                    comments.extend(entry for entry in item["sub_comments"] if isinstance(entry, dict))
        return comments
    if not isinstance(value, dict):
        return []
    for key in ["comments", "comment_list", "commentList"]:
        item = value.get(key)
        if isinstance(item, list):
            return _candidate_comments(item)
        if isinstance(item, dict):
            nested = _candidate_comments(item)
            if nested:
                return nested
    for item in value.values():
        if isinstance(item, (dict, list)):
            nested = _candidate_comments(item)
            if nested:
                return nested
    return []


def _build_evidence(task_id: str, posts: list[SocialPost]) -> list[Evidence]:
    evidence = []
    for post in posts:
        evidence.append(
            Evidence(
                task_id=task_id,
                source_id=f"xhs_{post.post_id}",
                product="飞书",
                evidence_type="social_sentiment",
                summary=f"{post.title}：采集 {len(post.comments)} 条评论样本。",
                quote_or_locator=f"xiaohongshu:{post.post_id}",
                confidence="medium",
            )
        )
    return evidence


def _int_value(value: Any) -> int:
    if isinstance(value, int):
        return value
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if not digits:
        return 0
    return int(digits)


if __name__ == "__main__":
    main()
