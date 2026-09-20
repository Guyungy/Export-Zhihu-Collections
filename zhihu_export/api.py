# -*- coding: utf-8 -*-
"""正文 API 兜底：页面被 403 / 改版挡住时，改从知乎 OpenAPI 取正文。

知乎的网页端（``/question/xxx/answer/yyy``、``zhuanlan.zhihu.com/p/zzz``）经常对
自动化请求返回 403 或一个空壳页面，但同一篇内容的 OpenAPI 接口通常仍可访问。
这个模块负责：

* 从各种形态的 URL 里解析出「内容类型 + 内容 ID」
* 调用对应接口取出正文 HTML
* 把想法（pin）那种分块结构拼回 HTML

拿到的是**正文片段**而非整页，所以不需要再做「找容器」那一步选择器匹配，
也不受页面改版影响 —— 这是它比解析网页更稳的原因。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from . import http as http_mod

#: 内容类型 -> API 地址模板
CONTENT_API_TEMPLATES: Dict[str, str] = {
    "answer": "https://www.zhihu.com/api/v4/answers/{content_id}?include=content",
    "article": "https://www.zhihu.com/api/v4/articles/{content_id}?include=content",
    "pin": "https://www.zhihu.com/api/v4/pins/{content_id}",
}

#: 判定顺序有意义：``/answer/`` 必须排在通用数字规则之前。
_URL_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("answer", re.compile(r"/answer/(\d+)")),
    ("article", re.compile(r"zhuanlan\.zhihu\.com/p/(\d+)")),
    ("pin", re.compile(r"/pin/(\d+)")),
)


def parse_content_target(url: str) -> Optional[Tuple[str, str]]:
    """从 URL 解析 ``(内容类型, 内容 ID)``；无法识别时返回 ``None``。"""
    if not url:
        return None

    for kind, pattern in _URL_PATTERNS:
        match = pattern.search(url)
        if match:
            return kind, match.group(1)
    return None


def build_api_url(kind: str, content_id: str) -> Optional[str]:
    """拼出 API 地址。"""
    template = CONTENT_API_TEMPLATES.get(kind)
    if not template:
        return None
    return template.format(content_id=content_id)


def _render_pin_blocks(blocks: Any) -> str:
    """把想法的分块结构拼成 HTML。"""
    if not isinstance(blocks, list):
        return ""

    parts: List[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue

        block_type = block.get("type")
        if block_type == "text":
            parts.append(str(block.get("content") or ""))
        elif block_type == "image":
            src = block.get("url") or block.get("original_url") or ""
            if src:
                parts.append('<img src="%s" alt="">' % src)
        elif block_type == "link":
            href = block.get("url") or ""
            title = block.get("title") or block.get("data_text") or href
            parts.append('<a href="%s">%s</a>' % (href, title))
        elif block_type == "video":
            title = block.get("title") or block.get("url") or "视频"
            parts.append("<p>[视频] %s</p>" % title)
        elif block_type == "title":
            parts.append("<h2>%s</h2>" % (block.get("content") or ""))
        else:  # 未知块类型：能拿到 content 就保留，避免静默丢内容
            fallback = block.get("content") or block.get("text")
            if fallback:
                parts.append(str(fallback))

    return "".join(parts)


def extract_content_html(payload: Any, kind: str) -> Optional[str]:
    """从 API 返回的 JSON 里取出正文 HTML。"""
    if not isinstance(payload, dict):
        return None

    if payload.get("error"):
        # 原因由调用方用 describe_api_error 翻译后输出，这里只留调试信息
        logging.debug("正文 API 返回错误体: %s", describe_api_error(payload))
        return None

    if kind == "pin":
        html = _render_pin_blocks(payload.get("content"))
    else:
        html = payload.get("content")

    if not isinstance(html, str) or not html.strip():
        return None
    return html


def describe_api_error(payload: Any) -> str:
    """把 API 的错误体翻译成能照着做的提示。

    知乎的错误体里其实写清楚了原因，但都是机器码：

    * ``{"error": {"need_login": true, ...}}`` —— 回答接口，登录态不可用
    * ``{"error": {"name": "AuthenticationInvalidRequest", "message": "ERR_LOGIN_TICKET_EXPIRED"}}``
    * ``{"error": {"code": 10003, "message": "请求参数异常，请升级客户端后重试。"}}`` —— 风控拦截

    照抄机器码对用户没用，所以这里做一次翻译。
    """
    if not isinstance(payload, dict):
        return ""

    error = payload.get("error")
    if not isinstance(error, dict):
        return ""

    name = str(error.get("name") or "")
    message = str(error.get("message") or "")
    code = error.get("code")

    if error.get("need_login") or "need_login" in name:
        return "接口要求登录（need_login），请重新导出知乎 cookies"
    if "LOGIN" in name.upper() or "LOGIN" in message.upper():
        return "登录票据已失效（%s），请重新导出知乎 cookies" % (message or name)
    if code == 10003:
        return "被知乎风控拦截（code 10003），需要有效登录态，且该接口可能对非浏览器客户端受限"
    if message:
        return "%s (code=%s)" % (message, code)
    return str(error)


def _is_json(response) -> bool:
    return "json" in (response.headers.get("Content-Type") or "").lower()


def fetch_content_via_api(session, url: str, limiter=None) -> Optional[str]:
    """页面抓取失败时，改从 API 取正文 HTML；不可用则返回 ``None``。

    :param session: 已带 cookies 的 :class:`requests.Session`
    :param url: 原始页面 URL（回答 / 专栏 / 想法均可）
    :param limiter: 可选的 :class:`~zhihu_export.http.RateLimiter`
    """
    target = parse_content_target(url)
    if not target:
        logging.debug("URL 无法解析出内容 ID，跳过 API 兜底: %s", url)
        return None

    kind, content_id = target
    api_url = build_api_url(kind, content_id)
    if not api_url:
        return None

    logging.info("尝试 API 兜底（%s %s）: %s", kind, content_id, api_url)

    try:
        if limiter:
            limiter.wait()
        response = session.get(
            api_url,
            headers=http_mod.build_api_headers(),
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001 - 兜底失败不应中断导出
        logging.warning("API 兜底请求失败（%s %s）: %s", kind, content_id, exc)
        return None

    # 知乎把「登录票据失效」「被风控」都塞在响应体里一起返回 4xx，
    # 先解析 JSON 才能拿到真正的原因，直接 raise_for_status 会把线索丢掉。
    if _is_json(response):
        try:
            payload = response.json()
        except ValueError as exc:
            logging.warning("API 兜底返回的 JSON 无法解析（%s %s）: %s", kind, content_id, exc)
            return None

        html = extract_content_html(payload, kind)
        if html is None:
            reason = describe_api_error(payload)
            logging.warning(
                "API 兜底未取到正文（%s %s）: %s", kind, content_id, reason or "响应体为空"
            )
            return None

        logging.info("API 兜底成功（%s %s），正文长度 %d", kind, content_id, len(html))
        return html

    # 非 JSON：多半是风控页 / 登录页，把状态码报出来
    logging.warning(
        "API 兜底返回了非 JSON 内容（%s %s，HTTP %s，Content-Type=%s），可能被风控拦截",
        kind,
        content_id,
        response.status_code,
        response.headers.get("Content-Type") or "未知",
    )
    return None
