from __future__ import annotations

import inspect
import json
import asyncio
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


def _patch_fastmcp_tool_decorator() -> None:
    """Allow xiaohongshu-mcp-server 0.1.1 to import with FastMCP 2.x."""
    from fastmcp.server.server import FastMCP

    original_tool = FastMCP.tool

    def compatible_tool(self: FastMCP, *args: Any, **kwargs: Any) -> Any:
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return original_tool(self)(args[0])
        return original_tool(self, *args, **kwargs)

    FastMCP.tool = compatible_tool


_patch_fastmcp_tool_decorator()
from xhs_mcp import server as xhs_server  # noqa: E402


def _patch_xhs_server_direct_browser() -> None:
    if getattr(xhs_server, "_codex_direct_browser_patched", False):
        return

    def ensure_browser_direct() -> None:
        if xhs_server._page is not None:
            return

        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth

        xhs_server._pw = sync_playwright().start()
        headless = _xhs_browser_headless()
        try:
            xhs_server._browser = xhs_server._pw.chromium.launch(**_xhs_browser_launch_options(headless))
            xhs_server._codex_browser_headless = headless
        except Exception:
            if headless:
                raise
            xhs_server._browser = xhs_server._pw.chromium.launch(**_xhs_browser_launch_options(True))
            xhs_server._codex_browser_headless = True
        xhs_server._ctx = xhs_server._browser.new_context()

        cookies = xhs_server._load_cookies()
        if cookies:
            xhs_server._ctx.add_cookies(cookies)

        xhs_server._page = xhs_server._ctx.new_page()
        Stealth().apply_stealth_sync(xhs_server._page)
        xhs_server._page.goto("https://www.xiaohongshu.com", timeout=60000, wait_until="domcontentloaded")
        xhs_server._page.wait_for_timeout(3000)

    xhs_server._ensure_browser = ensure_browser_direct
    xhs_server._codex_direct_browser_patched = True


def _xhs_browser_headless() -> bool:
    configured = os.getenv("XHS_MCP_BROWSER_HEADLESS", "").strip().lower()
    if configured:
        return configured in {"1", "true", "yes", "on"}
    if os.getenv("CI", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if sys.platform.startswith("linux") and not (os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY")):
        return True
    return False


def _xhs_browser_launch_options(headless: bool) -> dict[str, Any]:
    options: dict[str, Any] = {
        "headless": headless,
        "args": ["--no-proxy-server", "--proxy-server=direct://", "--proxy-bypass-list=*"],
    }
    channel = os.getenv("XHS_MCP_BROWSER_CHANNEL", "").strip()
    if channel:
        options["channel"] = channel
    return options


_patch_xhs_server_direct_browser()


app = FastAPI(title="XHS MCP Dev Bridge")
xhs_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="xhs-mcp")


def _jsonrpc_result(request_id: str | int | None, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def _jsonrpc_error(request_id: str | int | None, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def _content_result(value: Any, *, is_error: bool = False) -> dict[str, Any]:
    text = value if isinstance(value, str) else str(value)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _tool_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "get_feed_detail":
        mapped = dict(arguments)
        if "feed_id" in mapped and "note_id" not in mapped:
            mapped["note_id"] = mapped.pop("feed_id")
        mapped.pop("load_all_comments", None)
        return mapped
    if name == "get_feed_comments":
        mapped = dict(arguments)
        if "feed_id" in mapped and "note_id" not in mapped:
            mapped["note_id"] = mapped.pop("feed_id")
        return mapped
    if name == "search_feeds":
        mapped = {"keyword": arguments.get("keyword", "")}
        filters = arguments.get("filters") if isinstance(arguments.get("filters"), dict) else {}
        if arguments.get("limit") or filters.get("limit") or filters.get("max_results"):
            mapped["limit"] = arguments.get("limit") or filters.get("limit") or filters.get("max_results")
        sort_by = str(filters.get("sort_by") or filters.get("sort") or "")
        note_type = str(filters.get("note_type") or filters.get("noteType") or "")
        if sort_by in {"最新", "time_descending"}:
            mapped["sort"] = "time_descending"
        elif sort_by in {"最热", "popularity_descending"}:
            mapped["sort"] = "popularity_descending"
        else:
            mapped["sort"] = "general"
        if note_type in {"视频", "video", "1"}:
            mapped["note_type"] = 1
        elif note_type in {"图文", "图片", "image", "2"}:
            mapped["note_type"] = 2
        else:
            mapped["note_type"] = 0
        return mapped
    return arguments


def _call_tool(name: str, arguments: dict[str, Any]) -> Any:
    if name == "get_login_qrcode":
        return _get_login_qrcode()
    if name == "search_feeds":
        return _search_feeds(arguments)
    if name == "get_feed_detail":
        return _get_feed_detail(arguments)
    if name == "get_feed_comments":
        return _get_feed_comments(arguments)
    tool = getattr(xhs_server, name, None)
    if not callable(tool):
        raise ValueError(f"Unknown tool: {name}")
    call_args = _tool_arguments(name, arguments)
    signature = inspect.signature(tool)
    supported_args = {key: value for key, value in call_args.items() if key in signature.parameters}
    return tool(**supported_args)


def _search_feeds(arguments: dict[str, Any]) -> str:
    call_args = _tool_arguments("search_feeds", arguments)
    keyword = str(call_args.get("keyword") or arguments.get("keyword") or "").strip()
    limit = _positive_int(call_args.get("limit") or arguments.get("limit") or 15, 15)
    limit = max(1, min(limit, 50))
    raw = _raw_search_feeds(call_args)
    error = _xhs_api_error_message(raw)
    if error:
        feeds = _scrape_search_page(keyword, limit=limit)
        if feeds:
            return json.dumps(
                {"keyword": keyword, "feeds": feeds, "count": len(feeds), "fallback": "web_dom", "api_error": error, "raw_status": _raw_status(raw)},
                ensure_ascii=False,
            )
        return json.dumps({"message": f"小红书搜索失败：{error}", "raw": raw}, ensure_ascii=False)

    feeds = _feeds_from_raw_search(raw)
    if feeds:
        return json.dumps({"keyword": keyword, "feeds": feeds, "count": len(feeds), "raw_status": _raw_status(raw)}, ensure_ascii=False)

    message = _search_failure_message(keyword, json.dumps({"feeds": [], "count": 0}, ensure_ascii=False))
    return json.dumps({"keyword": keyword, "feeds": [], "count": 0, "message": message, "raw_status": _raw_status(raw)}, ensure_ascii=False)


def _get_feed_detail(arguments: dict[str, Any]) -> str:
    tool = getattr(xhs_server, "get_feed_detail", None)
    if not callable(tool):
        raise ValueError("Unknown tool: get_feed_detail")

    call_args = _tool_arguments("get_feed_detail", arguments)
    signature = inspect.signature(tool)
    supported_args = {key: value for key, value in call_args.items() if key in signature.parameters}
    raw_value = tool(**supported_args)
    raw_text = raw_value if isinstance(raw_value, str) else json.dumps(raw_value, ensure_ascii=False)
    parsed = _try_json(raw_text)
    if isinstance(parsed, dict) and not _xhs_response_is_empty_detail(parsed):
        return raw_text

    note_id = str(call_args.get("note_id") or "").strip()
    xsec_token = str(call_args.get("xsec_token") or "").strip()
    scraped = _scrape_note_detail(note_id, xsec_token=xsec_token, comment_limit=0)
    if scraped.get("title") or scraped.get("desc"):
        return json.dumps({"data": scraped, "fallback": "web_dom", "api_response": raw_text[:500]}, ensure_ascii=False)
    return raw_text


def _raw_search_feeds(call_args: dict[str, Any]) -> dict[str, Any]:
    from xhs.help import get_search_id

    return _signed_api_post(
        "/api/sns/web/v1/search/notes",
        {
            "keyword": str(call_args.get("keyword") or ""),
            "page": 1,
            "page_size": 20,
            "search_id": get_search_id(),
            "sort": call_args.get("sort") or "general",
            "note_type": call_args.get("note_type", 0),
        },
        host="https://edith.xiaohongshu.com",
    )


def _feeds_from_raw_search(raw: dict[str, Any]) -> list[dict[str, Any]]:
    items = raw.get("data", {}).get("items", []) if isinstance(raw.get("data"), dict) else []
    feeds: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return feeds
    for item in items:
        if not isinstance(item, dict):
            continue
        note_card = item.get("note_card") if isinstance(item.get("note_card"), dict) else {}
        user = note_card.get("user") if isinstance(note_card.get("user"), dict) else {}
        interact = note_card.get("interact_info") if isinstance(note_card.get("interact_info"), dict) else {}
        note_id = str(item.get("id") or note_card.get("note_id") or "").strip()
        feeds.append(
            {
                "id": note_id,
                "note_id": note_id,
                "title": note_card.get("display_title") or note_card.get("title") or "",
                "desc": note_card.get("desc") or "",
                "user": user.get("nickname") or "",
                "user_id": user.get("user_id") or "",
                "likes": interact.get("liked_count", "0"),
                "comment_count": interact.get("comment_count", "0"),
                "collect_count": interact.get("collected_count", "0"),
                "share_count": interact.get("share_count", "0"),
                "xsec_token": item.get("xsec_token", ""),
                "url": f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={quote(str(item.get('xsec_token') or ''))}&xsec_source=pc_search" if note_id else "",
            }
        )
    return feeds


def _get_feed_comments(arguments: dict[str, Any]) -> str:
    call_args = _tool_arguments("get_feed_comments", arguments)
    note_id = str(call_args.get("note_id") or "").strip()
    if not note_id:
        return json.dumps({"comments": [], "count": 0, "message": "缺少 note_id，无法抓取评论。"}, ensure_ascii=False)

    limit = _positive_int(call_args.get("limit") or call_args.get("max_comments"), 30)
    limit = max(1, min(limit, 100))
    cursor = str(call_args.get("cursor") or "")
    comments: list[dict[str, Any]] = []
    has_more = True
    page_count = 0
    last_raw: dict[str, Any] = {}

    while has_more and len(comments) < limit and page_count < 10:
        raw = _signed_api_get("/api/sns/web/v2/comment/page", {"note_id": note_id, "cursor": cursor}, host="https://edith.xiaohongshu.com")
        last_raw = raw
        error = _xhs_api_error_message(raw)
        if error:
            scraped = _scrape_note_detail(note_id, xsec_token=str(call_args.get("xsec_token") or ""), comment_limit=limit)
            scraped_comments = scraped.get("comments") if isinstance(scraped.get("comments"), list) else []
            if scraped_comments:
                return json.dumps(
                    {
                        "note_id": note_id,
                        "comments": scraped_comments[:limit],
                        "count": len(scraped_comments[:limit]),
                        "fallback": "web_dom",
                        "api_error": error,
                        "raw_status": _raw_status(raw),
                    },
                    ensure_ascii=False,
                )
            return json.dumps({"note_id": note_id, "comments": comments, "count": len(comments), "message": f"小红书评论抓取失败：{error}", "raw": raw}, ensure_ascii=False)

        data = raw.get("data", raw) if isinstance(raw, dict) else {}
        page_comments = data.get("comments") if isinstance(data, dict) else []
        if not isinstance(page_comments, list) or not page_comments:
            break

        comments.extend(_flatten_xhs_comments(page_comments, remaining=limit - len(comments)))
        next_cursor = str(data.get("cursor") or "")
        has_more = bool(data.get("has_more")) and bool(next_cursor) and next_cursor != cursor
        cursor = next_cursor
        page_count += 1

    return json.dumps(
        {
            "note_id": note_id,
            "comments": comments[:limit],
            "count": len(comments[:limit]),
            "cursor": cursor,
            "has_more": has_more,
            "page_count": page_count,
            "raw_status": {key: last_raw.get(key) for key in ["code", "success", "msg", "status", "host"] if isinstance(last_raw, dict) and key in last_raw},
        },
        ensure_ascii=False,
    )


def _scrape_search_page(keyword: str, *, limit: int) -> list[dict[str, Any]]:
    if not keyword:
        return []
    search_url = f"https://www.xiaohongshu.com/search_result?keyword={quote(keyword)}&source=web_explore_feed"
    with xhs_server._lock:
        xhs_server._ensure_browser()
        xhs_server._page.goto(search_url, timeout=60000, wait_until="networkidle")
        xhs_server._page.wait_for_timeout(3000)
        return xhs_server._page.evaluate(
            """(limit) => Array.from(document.querySelectorAll('section.note-item')).slice(0, limit).map((section) => {
                const explore = section.querySelector('a[href^="/explore/"]');
                const tokenLink = section.querySelector('a[href*="xsec_token="]');
                const href = explore?.getAttribute('href') || '';
                const noteId = href.split('/').filter(Boolean).pop() || '';
                let xsecToken = '';
                try {
                    const rawHref = tokenLink?.getAttribute('href') || '';
                    xsecToken = new URL(rawHref, location.origin).searchParams.get('xsec_token') || '';
                } catch (_) {}
                const lines = (section.innerText || '').split('\\n').map((line) => line.trim()).filter(Boolean);
                const title = lines[0] || '';
                const author = lines[1] || '';
                const likes = lines[lines.length - 1] || '0';
                return {
                    id: noteId,
                    note_id: noteId,
                    title,
                    desc: '',
                    user: author,
                    likes,
                    comment_count: 0,
                    xsec_token: xsecToken,
                    url: noteId ? `https://www.xiaohongshu.com/explore/${noteId}${xsecToken ? `?xsec_token=${encodeURIComponent(xsecToken)}&xsec_source=pc_search` : ''}` : ''
                };
            }).filter((item) => item.id && item.title && item.title !== '相关搜索')""",
            limit,
        )


def _scrape_note_detail(note_id: str, *, xsec_token: str = "", comment_limit: int = 30) -> dict[str, Any]:
    if not note_id:
        return {}
    detail_url = f"https://www.xiaohongshu.com/explore/{note_id}"
    if xsec_token:
        detail_url = f"{detail_url}?xsec_token={quote(xsec_token)}&xsec_source=pc_search"

    with xhs_server._lock:
        xhs_server._ensure_browser()
        xhs_server._page.goto(detail_url, timeout=60000, wait_until="networkidle")
        xhs_server._page.wait_for_timeout(3000)
        for _ in range(16):
            current_count = xhs_server._page.locator(".comment-item").count()
            if current_count >= comment_limit:
                break
            clicked_expand = False
            for button in xhs_server._page.get_by_text(re.compile(r"展开.*条回复")).all()[:8]:
                try:
                    button.click(timeout=1000)
                    clicked_expand = True
                    break
                except Exception:
                    continue
            if not clicked_expand:
                xhs_server._page.mouse.wheel(0, 1600)
            xhs_server._page.wait_for_timeout(1200)
        return xhs_server._page.evaluate(
            """(commentLimit) => {
                const title = (document.querySelector('.title')?.innerText || document.title.replace(/ - 小红书$/, '') || '').trim();
                const desc = (document.querySelector('.desc')?.innerText || document.querySelector('.note-content')?.innerText || '').trim();
                const comments = Array.from(document.querySelectorAll('.comment-item')).slice(0, commentLimit).map((item, index) => {
                    const lines = (item.innerText || '').split('\\n').map((line) => line.trim()).filter(Boolean);
                    const content = (item.querySelector('.content')?.innerText || lines[1] || '').trim();
                    const author = lines[0] || '';
                    const likeText = [...lines].reverse().find((line) => /^\\d+$/.test(line)) || '0';
                    return {
                        id: `dom_${index + 1}`,
                        comment_id: `dom_${index + 1}`,
                        nickname: author,
                        author,
                        content,
                        like_count: Number(likeText) || 0,
                    };
                }).filter((item) => item.content && item.content !== '回复' && item.content !== '赞');
                const commentCountText = document.body.innerText.match(/共\\s*([\\d,，]+)\\s*条评论/)?.[1] || '';
                return {
                    title,
                    desc,
                    comments,
                    comment_count: commentCountText ? Number(commentCountText.replace(/[,，]/g, '')) || comments.length : comments.length,
                    url: location.href,
                };
            }""",
            max(0, min(comment_limit, 100)),
        )


def _xhs_response_is_empty_detail(value: dict[str, Any]) -> bool:
    return not any(value.get(key) for key in ["title", "desc", "description", "content", "data"])


async def _call_tool_on_xhs_thread(name: str, arguments: dict[str, Any]) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(xhs_executor, _call_tool, name, arguments)


def _get_login_qrcode() -> str:
    result: dict[str, Any] = {}
    data: dict[str, Any] = {}
    attempts: list[dict[str, Any]] = []
    for host in ("https://edith.xiaohongshu.com", "https://www.xiaohongshu.com"):
        result = _signed_api_post("/api/sns/web/v1/login/qrcode/create", {}, host=host)
        attempts.append(result)
        data = result.get("data", result) if isinstance(result, dict) else {}
        if data.get("url") or data.get("qr_id") or data.get("code"):
            break

    qr_url = str(data.get("url") or "")
    qr_id = str(data.get("qr_id") or "")
    code = str(data.get("code") or "")
    qr_image_path = ""
    if qr_url:
        try:
            import qrcode

            img = qrcode.make(qr_url)
            qr_image_path = str(xhs_server.COOKIE_DIR / "login_qr.png")
            xhs_server.COOKIE_DIR.mkdir(parents=True, exist_ok=True)
            img.save(qr_image_path)
        except ImportError:
            pass

    return json.dumps(
        {
            "qr_url": qr_url,
            "qr_id": qr_id,
            "code": code,
            "qr_image": qr_image_path,
            "browser_headless": bool(getattr(xhs_server, "_codex_browser_headless", _xhs_browser_headless())),
            "raw": result,
            "attempts": attempts,
            "message": "已生成登录二维码。" if qr_url else _qrcode_failure_message(attempts),
        },
        ensure_ascii=False,
    )


def _qrcode_failure_message(attempts: list[dict[str, Any]]) -> str:
    for attempt in attempts:
        msg = attempt.get("msg") or attempt.get("message")
        if msg:
            return f"小红书创建二维码失败：{msg}"
    for attempt in attempts:
        preview = attempt.get("body_preview")
        if preview:
            return f"小红书创建二维码失败：{preview}"
    return "小红书创建二维码接口未返回有效 qr_url。"


def _search_failure_message(keyword: str, raw_text: str) -> str:
    parsed = _try_json(raw_text)
    if isinstance(parsed, dict):
        code = parsed.get("code")
        msg = str(parsed.get("msg") or parsed.get("message") or "").strip()
        if code not in (None, 0) or msg:
            detail = msg or f"code={code}"
            return f"小红书搜索失败：{detail}"
        feeds = parsed.get("feeds")
        if isinstance(feeds, list) and feeds:
            return ""
    elif raw_text.strip():
        if "search failed" in raw_text.casefold() or "失败" in raw_text:
            return f"小红书搜索失败：{raw_text.strip()}"

    probe = _probe_search_page(keyword)
    error_code = str(probe.get("error_code") or "").strip()
    message = str(probe.get("message") or "").strip()
    if error_code == "300012" or "IP存在风险" in message:
        return "小红书搜索失败：IP存在风险，请切换可靠网络环境后重试（error_code=300012）"
    if message:
        return f"小红书搜索失败：{message}"
    if keyword:
        return f"小红书搜索失败：关键词 {keyword} 暂未返回可用结果。"
    return "小红书搜索失败：暂未返回可用结果。"


def _probe_search_page(keyword: str) -> dict[str, str]:
    if not keyword:
        return {}

    search_url = f"https://www.xiaohongshu.com/search_result?keyword={quote(keyword)}&source=web_explore_feed"
    with xhs_server._lock:
        xhs_server._ensure_browser()
        xhs_server._page.goto(search_url, timeout=60000, wait_until="domcontentloaded")
        xhs_server._page.wait_for_timeout(2500)
        page_url = xhs_server._page.url
        title = xhs_server._page.title()
        body_preview = xhs_server._page.content()[:800]

    query = parse_qs(urlparse(page_url).query)
    error_code = str(query.get("error_code", [""])[0] or "").strip()
    error_msg = str(query.get("error_msg", [""])[0] or "").strip()
    if error_code or error_msg:
        return {
            "error_code": error_code,
            "message": error_msg,
            "title": title,
            "url": page_url,
        }
    if "安全限制" in title or "IP存在风险" in body_preview:
        return {
            "error_code": "300012",
            "message": "IP存在风险，请切换可靠网络环境后重试",
            "title": title,
            "url": page_url,
        }
    if "website-login/error" in page_url:
        return {
            "message": "搜索页跳转到了登录或安全校验页。",
            "title": title,
            "url": page_url,
        }
    return {"title": title, "url": page_url}


def _signed_api_post(uri: str, data: dict[str, Any], host: str) -> dict[str, Any]:
    with xhs_server._lock:
        xhs_server._ensure_browser()
        signs = xhs_server._sign(uri, data)
        response = xhs_server._ctx.request.post(
            f"{host}{uri}",
            headers={
                **signs,
                "Content-Type": "application/json",
                "Origin": "https://www.xiaohongshu.com",
                "Referer": "https://www.xiaohongshu.com/",
            },
            data=json.dumps(data, separators=(",", ":"), ensure_ascii=False),
        )
        text = response.text()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"host": host, "status": response.status, "non_json": True, "body_preview": text[:500]}
    if isinstance(parsed, dict):
        parsed.setdefault("host", host)
        parsed.setdefault("status", response.status)
        return parsed
    return {"host": host, "status": response.status, "value": parsed}


def _signed_api_get(uri: str, params: dict[str, Any], host: str) -> dict[str, Any]:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    final_uri = f"{uri}?{query}" if query else uri
    with xhs_server._lock:
        xhs_server._ensure_browser()
        signs = xhs_server._sign(final_uri)
        response = xhs_server._ctx.request.get(
            f"{host}{final_uri}",
            headers={
                **signs,
                "Content-Type": "application/json",
                "Origin": "https://www.xiaohongshu.com",
                "Referer": "https://www.xiaohongshu.com/",
            },
        )
        text = response.text()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"host": host, "status": response.status, "non_json": True, "body_preview": text[:500]}
    if isinstance(parsed, dict):
        parsed.setdefault("host", host)
        parsed.setdefault("status", response.status)
        return parsed
    return {"host": host, "status": response.status, "value": parsed}


def _xhs_api_error_message(raw: dict[str, Any]) -> str:
    code = raw.get("code")
    success = raw.get("success")
    msg = str(raw.get("msg") or raw.get("message") or "").strip()
    if code not in (None, 0) or success is False:
        return msg or f"code={code}"
    return ""


def _raw_status(raw: dict[str, Any]) -> dict[str, Any]:
    return {key: raw.get(key) for key in ["code", "success", "msg", "status", "host"] if key in raw}


def _flatten_xhs_comments(items: list[Any], *, remaining: int) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or len(flattened) >= remaining:
            continue
        flattened.append(item)
        sub_comments = item.get("sub_comments")
        if isinstance(sub_comments, list):
            for sub_comment in sub_comments:
                if not isinstance(sub_comment, dict) or len(flattened) >= remaining:
                    continue
                flattened.append(sub_comment)
    return flattened


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _try_json(text: str) -> Any:
    value = text.strip()
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "xhs-mcp-dev-bridge"}


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> Response:
    payload = await request.json()
    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}

    if method == "initialize":
        return _jsonrpc_result(
            request_id,
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "xhs-mcp-dev-bridge", "version": "0.1.0"},
            },
        )
    if method == "notifications/initialized":
        return Response(status_code=204)
    if method == "tools/list":
        tools = [
            "check_login_status",
            "get_login_qrcode",
            "check_qrcode_status",
            "reload_cookies",
            "publish_content",
            "search_feeds",
            "get_feed_detail",
            "get_feed_comments",
            "user_profile",
        ]
        return _jsonrpc_result(request_id, {"tools": [{"name": name} for name in tools]})
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            value = await _call_tool_on_xhs_thread(name, arguments)
            return _jsonrpc_result(request_id, _content_result(value))
        except Exception as exc:
            return _jsonrpc_result(request_id, _content_result(str(exc), is_error=True))
    return _jsonrpc_error(request_id, -32601, f"Unknown method: {method}")


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=18060, log_level="info")


if __name__ == "__main__":
    main()
