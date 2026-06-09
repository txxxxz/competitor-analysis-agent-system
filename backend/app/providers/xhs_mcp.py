from __future__ import annotations

import atexit
import json
import inspect
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse, urlunparse
from urllib.request import ProxyHandler, Request, build_opener, urlopen
from uuid import uuid4

from app.providers.errors import ProviderRequestError


DEFAULT_XHS_MCP_URL = "http://localhost:18060/mcp"
_LOCAL_TOOL_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="xhs-local-mcp")
_LOCAL_TOOL_LOCK = threading.Lock()
_LOCAL_XHS_SERVER = None
_AUTO_START_LOCK = threading.Lock()
_AUTO_STARTED_PROCESS: subprocess.Popen[str] | None = None


class XhsMcpClient:
    provider_name = "XhsMcpProvider"

    def __init__(self, base_url: str | None = None, timeout: float = 15.0):
        self.base_url = (base_url or os.getenv("XHS_MCP_URL") or DEFAULT_XHS_MCP_URL).strip()
        self.timeout = timeout
        self.session_id = ""

    def check_login_status(self) -> dict[str, Any]:
        return self.call_tool("check_login_status", {})

    def get_login_qrcode(self) -> dict[str, Any]:
        return self.call_tool("get_login_qrcode", {})

    def check_qrcode_status(self, qr_id: str, code: str) -> dict[str, Any]:
        arguments = {"qr_id": qr_id, "code": code}
        return self.call_tool("check_qrcode_status", arguments)

    def search_feeds(self, keyword: str, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"keyword": keyword}
        if filters:
            payload["filters"] = filters
            if filters.get("limit") or filters.get("max_results"):
                payload["limit"] = filters.get("limit") or filters.get("max_results")
        return self.call_tool("search_feeds", payload)

    def get_feed_detail(self, feed_id: str, xsec_token: str, load_all_comments: bool = False) -> dict[str, Any]:
        return self.call_tool(
            "get_feed_detail",
            {
                "feed_id": feed_id,
                "xsec_token": xsec_token,
                "load_all_comments": load_all_comments,
            },
        )

    def get_feed_comments(self, feed_id: str, limit: int = 30, cursor: str = "", xsec_token: str = "") -> dict[str, Any]:
        return self.call_tool(
            "get_feed_comments",
            {
                "feed_id": feed_id,
                "limit": limit,
                "cursor": cursor,
                "xsec_token": xsec_token,
            },
        )

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._jsonrpc("tools/call", {"name": name, "arguments": arguments})
        except ProviderRequestError as direct_error:
            if _should_auto_start_mcp(self.base_url, direct_error):
                _ensure_local_mcp_server(self.base_url)
                try:
                    response = self._jsonrpc("tools/call", {"name": name, "arguments": arguments})
                    result = response.get("result")
                    if isinstance(result, dict) and result.get("isError"):
                        raise ProviderRequestError(_stringify_mcp_content(result) or f"{name} returned an MCP error.")
                    return _normalize_mcp_result(result)
                except ProviderRequestError as auto_started_error:
                    direct_error = ProviderRequestError(
                        f"{direct_error} Auto-started local xiaohongshu-mcp bridge, but the request still failed: {auto_started_error}"
                    )
            if _should_use_local_tool_fallback(self.base_url, direct_error):
                return _call_local_tool(name, arguments)
            self._initialize()
            try:
                response = self._jsonrpc("tools/call", {"name": name, "arguments": arguments})
            except ProviderRequestError as initialized_error:
                if _should_use_local_tool_fallback(self.base_url, initialized_error):
                    return _call_local_tool(name, arguments)
                raise ProviderRequestError(f"{direct_error} Retried after MCP initialize: {initialized_error}") from initialized_error
        result = response.get("result")
        if isinstance(result, dict) and result.get("isError"):
            raise ProviderRequestError(_stringify_mcp_content(result) or f"{name} returned an MCP error.")
        return _normalize_mcp_result(result)

    def _initialize(self) -> None:
        try:
            self._jsonrpc(
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "competitor-analysis-agent-system", "version": "0.1.0"},
                },
                allow_empty_params=True,
            )
        except ProviderRequestError:
            self._jsonrpc("initialize", {}, allow_empty_params=True)
        try:
            self._jsonrpc("notifications/initialized", {}, notification=True, allow_empty_params=True)
        except ProviderRequestError:
            pass

    def _jsonrpc(self, method: str, params: dict[str, Any], *, notification: bool = False, allow_empty_params: bool = False) -> dict[str, Any]:
        payload = {
                "jsonrpc": "2.0",
                "method": method,
        }
        if not notification:
            payload["id"] = f"xhs_{uuid4().hex[:10]}"
        if params or allow_empty_params:
            payload["params"] = params
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = Request(
            self.base_url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with _open_url(request, self.base_url, self.timeout) as response:
                self.session_id = response.headers.get("mcp-session-id") or response.headers.get("Mcp-Session-Id") or self.session_id
                raw = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise ProviderRequestError(f"Cannot connect to xiaohongshu-mcp at {self.base_url}: HTTP Error {exc.code}: {detail or exc.reason}") from exc
        except URLError as exc:
            raise ProviderRequestError(f"Cannot connect to xiaohongshu-mcp at {self.base_url}: {exc}") from exc
        except TimeoutError as exc:
            raise ProviderRequestError(f"Timed out connecting to xiaohongshu-mcp at {self.base_url}.") from exc

        if notification and not raw.strip():
            return {}
        payload = _parse_json_or_sse(raw)
        if not isinstance(payload, dict):
            raise ProviderRequestError("xiaohongshu-mcp returned a non-object response.")
        if payload.get("error"):
            raise ProviderRequestError(str(payload["error"]))
        return payload


def _should_use_local_tool_fallback(base_url: str, exc: ProviderRequestError) -> bool:
    if os.getenv("XHS_MCP_LOCAL_FALLBACK", "true").strip().lower() in {"0", "false", "no"}:
        return False
    if "Connection refused" not in str(exc) and "HTTP Error 502" not in str(exc):
        return False
    return _is_default_local_mcp_url(base_url)


def _is_default_local_mcp_url(base_url: str) -> bool:
    parsed = urlparse(base_url)
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1"}
        and (parsed.port or 80) == 18060
        and parsed.path.rstrip("/") == "/mcp"
    )


def _open_url(request: Request, base_url: str, timeout: float):
    if _is_default_local_mcp_url(base_url):
        return build_opener(ProxyHandler({})).open(request, timeout=timeout)
    return urlopen(request, timeout=timeout)


def _should_auto_start_mcp(base_url: str, exc: ProviderRequestError) -> bool:
    if os.getenv("XHS_MCP_AUTOSTART", "true").strip().lower() in {"0", "false", "no"}:
        return False
    if not _is_default_local_mcp_url(base_url):
        return False
    message = str(exc)
    if "Connection refused" not in message and "Errno 61" not in message and "Errno 111" not in message:
        return False
    return _is_local_port_closed(base_url)


def _call_local_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    future = _LOCAL_TOOL_EXECUTOR.submit(_call_local_tool_sync, name, dict(arguments))
    return future.result()


def _ensure_local_mcp_server(base_url: str) -> None:
    global _AUTO_STARTED_PROCESS

    with _AUTO_START_LOCK:
        if not _is_local_port_closed(base_url):
            return
        if _AUTO_STARTED_PROCESS is not None and _AUTO_STARTED_PROCESS.poll() is None:
            _wait_for_local_mcp_ready(base_url, _AUTO_STARTED_PROCESS)
            return

        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")
        _AUTO_STARTED_PROCESS = subprocess.Popen(
            [_xhs_autostart_python(), str(_xhs_bridge_script_path())],
            cwd=str(_xhs_repo_root()),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        _wait_for_local_mcp_ready(base_url, _AUTO_STARTED_PROCESS)


def _xhs_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _xhs_bridge_script_path() -> Path:
    return _xhs_repo_root() / "scripts" / "run_xhs_mcp_bridge.py"


def _xhs_autostart_python() -> str:
    configured = os.getenv("XHS_MCP_AUTOSTART_PYTHON", "").strip()
    if configured:
        return configured
    return sys.executable


def _is_local_port_closed(base_url: str) -> bool:
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    try:
        with socket.create_connection((host, port), timeout=0.35):
            return False
    except ConnectionRefusedError:
        return True
    except socket.timeout:
        return False
    except OSError as exc:
        if exc.errno in {61, 111}:
            return True
        return False


def _wait_for_local_mcp_ready(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.time() + float(os.getenv("XHS_MCP_AUTOSTART_TIMEOUT", "15"))
    health_url = _health_url_for_mcp(base_url)
    while time.time() < deadline:
        if process.poll() is not None:
            raise ProviderRequestError("Auto-started local xiaohongshu-mcp bridge exited before becoming ready.")
        if _healthcheck_ok(health_url):
            return
        time.sleep(0.25)
    raise ProviderRequestError(f"Auto-started local xiaohongshu-mcp bridge did not become ready before timeout: {health_url}")


def _health_url_for_mcp(base_url: str) -> str:
    parsed = urlparse(base_url)
    return urlunparse((parsed.scheme, parsed.netloc, "/health", "", "", ""))


def _healthcheck_ok(url: str) -> bool:
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=0.5) as response:
            return 200 <= getattr(response, "status", 200) < 300
    except Exception:
        return False


def _call_local_tool_sync(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    server = _local_xhs_server()
    if name == "get_login_qrcode":
        value = _get_local_login_qrcode(server)
    elif name == "search_feeds":
        value = _get_local_search_feeds(server, arguments)
    elif name == "get_feed_comments":
        value = _get_local_feed_comments(server, arguments)
    else:
        tool = getattr(server, name, None)
        if not callable(tool):
            raise ProviderRequestError(f"Unknown xiaohongshu-mcp tool: {name}")
        call_args = _tool_arguments(name, arguments)
        signature = inspect.signature(tool)
        supported_args = {key: value for key, value in call_args.items() if key in signature.parameters}
        value = tool(**supported_args)
    return _normalize_local_tool_result(value)


def _local_xhs_server():
    global _LOCAL_XHS_SERVER
    with _LOCAL_TOOL_LOCK:
        if _LOCAL_XHS_SERVER is None:
            _patch_fastmcp_tool_decorator()
            from xhs_mcp import server as xhs_server

            _patch_xhs_server_direct_browser(xhs_server)
            _LOCAL_XHS_SERVER = xhs_server
        return _LOCAL_XHS_SERVER


def _patch_fastmcp_tool_decorator() -> None:
    from fastmcp.server.server import FastMCP

    if getattr(FastMCP.tool, "_xhs_compat_patched", False):
        return
    original_tool = FastMCP.tool

    def compatible_tool(self, *args: Any, **kwargs: Any) -> Any:
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return original_tool(self)(args[0])
        return original_tool(self, *args, **kwargs)

    compatible_tool._xhs_compat_patched = True
    FastMCP.tool = compatible_tool


def _patch_xhs_server_direct_browser(server) -> None:
    if getattr(server, "_codex_direct_browser_patched", False):
        return

    def ensure_browser_direct() -> None:
        if server._page is not None:
            return

        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth

        server._pw = sync_playwright().start()
        headless = _xhs_browser_headless()
        try:
            server._browser = server._pw.chromium.launch(**_xhs_browser_launch_options(headless))
            server._codex_browser_headless = headless
        except Exception:
            if headless:
                raise
            server._browser = server._pw.chromium.launch(**_xhs_browser_launch_options(True))
            server._codex_browser_headless = True
        server._ctx = server._browser.new_context()

        cookies = server._load_cookies()
        if cookies:
            server._ctx.add_cookies(cookies)

        server._page = server._ctx.new_page()
        Stealth().apply_stealth_sync(server._page)
        server._page.goto("https://www.xiaohongshu.com", timeout=60000, wait_until="domcontentloaded")
        server._page.wait_for_timeout(3000)

    server._ensure_browser = ensure_browser_direct
    server._codex_direct_browser_patched = True


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


def _get_local_login_qrcode(server) -> str:
    attempts: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    data: dict[str, Any] = {}
    for host in ("https://edith.xiaohongshu.com", "https://www.xiaohongshu.com"):
        result = _signed_api_post(server, "/api/sns/web/v1/login/qrcode/create", {}, host=host)
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
            qr_image_path = str(server.COOKIE_DIR / "login_qr.png")
            server.COOKIE_DIR.mkdir(parents=True, exist_ok=True)
            img.save(qr_image_path)
        except ImportError:
            pass

    return json.dumps(
        {
            "qr_url": qr_url,
            "qr_id": qr_id,
            "code": code,
            "qr_image": qr_image_path,
            "browser_headless": bool(getattr(server, "_codex_browser_headless", _xhs_browser_headless())),
            "raw": result,
            "attempts": attempts,
            "message": "已生成登录二维码。" if qr_url else _qrcode_failure_message(attempts),
        },
        ensure_ascii=False,
    )


def _get_local_search_feeds(server, arguments: dict[str, Any]) -> str:
    call_args = _tool_arguments("search_feeds", arguments)
    keyword = str(call_args.get("keyword") or arguments.get("keyword") or "").strip()
    raw = _raw_local_search_feeds(server, call_args)
    error = _xhs_api_error_message(raw)
    if error:
        return json.dumps({"message": f"小红书搜索失败：{error}", "raw": raw}, ensure_ascii=False)

    feeds = _feeds_from_raw_search(raw)
    return json.dumps({"keyword": keyword, "feeds": feeds, "count": len(feeds), "raw_status": _raw_status(raw)}, ensure_ascii=False)


def _raw_local_search_feeds(server, call_args: dict[str, Any]) -> dict[str, Any]:
    from xhs.help import get_search_id

    return _signed_api_post(
        server,
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
        xsec_token = str(item.get("xsec_token") or "")
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
                "xsec_token": xsec_token,
                "url": f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={quote(xsec_token)}&xsec_source=pc_search" if note_id else "",
            }
        )
    return feeds


def _get_local_feed_comments(server, arguments: dict[str, Any]) -> str:
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
        raw = _signed_api_get(server, "/api/sns/web/v2/comment/page", {"note_id": note_id, "cursor": cursor}, host="https://edith.xiaohongshu.com")
        last_raw = raw
        error = _xhs_api_error_message(raw)
        if error:
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


def _signed_api_post(server, uri: str, data: dict[str, Any], host: str) -> dict[str, Any]:
    with server._lock:
        server._ensure_browser()
        signs = server._sign(uri, data)
        response = server._ctx.request.post(
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


def _signed_api_get(server, uri: str, params: dict[str, Any], host: str) -> dict[str, Any]:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    final_uri = f"{uri}?{query}" if query else uri
    with server._lock:
        server._ensure_browser()
        signs = server._sign(final_uri)
        response = server._ctx.request.get(
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


def _normalize_local_tool_result(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        parsed = _try_json(value)
        if isinstance(parsed, dict):
            return parsed
        return {"message": value}
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"items": value}
    return {"value": value}


def _parse_json_or_sse(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return {}
    if text.startswith("{"):
        return json.loads(text)
    data_lines = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
    for line in reversed(data_lines):
        if line and line != "[DONE]":
            return json.loads(line)
    return json.loads(text)


def _normalize_mcp_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        parsed = _parse_embedded_content(result.get("content"))
        if parsed is not None:
            return parsed if isinstance(parsed, dict) else {"items": parsed}
        return result
    if isinstance(result, list):
        return {"items": result}
    return {"value": result}


def _parse_embedded_content(content: Any) -> Any:
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                texts.append(item)
        for text in texts:
            parsed = _try_json(text)
            if parsed is not None:
                return parsed
        if texts:
            return {"message": "\n".join(texts)}
    if isinstance(content, str):
        return _try_json(content) or {"message": content}
    return None


def _try_json(text: str) -> Any:
    value = text.strip()
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _stringify_mcp_content(result: dict[str, Any]) -> str:
    parsed = _parse_embedded_content(result.get("content"))
    if isinstance(parsed, dict):
        return str(parsed.get("message") or parsed.get("error") or "")
    if parsed:
        return str(parsed)
    return ""


def _cleanup_auto_started_process() -> None:
    process = _AUTO_STARTED_PROCESS
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=3)
    except Exception:
        pass


atexit.register(_cleanup_auto_started_process)
