# -*- coding: utf-8 -*-
"""HTTP 层：统一请求头、cookies 加载、自动重试与限流。

相比原来的逐次 ``requests.get``，这里做了三件事：

1. 复用同一个 :class:`requests.Session`（keep-alive，减少握手与风控命中）
2. 挂载 ``urllib3.Retry``，对 429 / 5xx / 连接重置自动退避重试
3. 提供线程安全的 :class:`RateLimiter`，避免并发请求把账号请求成风控对象
"""

from __future__ import annotations

import json
import logging
import pathlib
import threading
import time
from typing import Any, Dict, Mapping, Optional

import requests
from requests.adapters import HTTPAdapter

try:  # urllib3 v2
    from urllib3.util.retry import Retry
except ImportError:  # pragma: no cover - 兼容极老版本 urllib3
    from requests.packages.urllib3.util.retry import Retry  # type: ignore

from . import config as config_mod

# 旧版本用的是 Chrome/61（2017 年的 UA），知乎会据此判定为陈旧客户端并更容易返回 403。
# 这里换成近期版本，并补齐 sec-ch-ua / Sec-Fetch-* 等现代浏览器必带字段。
BROWSER_MAJOR_VERSION = "147"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/%s.0.0.0 Safari/537.36" % BROWSER_MAJOR_VERSION
)
SEC_CH_UA = '"Microsoft Edge";v="%s", "Not.A/Brand";v="8", "Chromium";v="%s"' % (
    BROWSER_MAJOR_VERSION,
    BROWSER_MAJOR_VERSION,
)

#: 浏览器会声明 ``br`` / ``zstd``，但 requests 只在装了对应解码库时才能解压。
#: 盲目声明会拿到一堆乱码（比拿到 403 更难排查），所以按实际能力协商。
def supported_content_encodings() -> str:
    """返回当前环境真正能解压的 Accept-Encoding 列表。"""
    encodings = ["gzip", "deflate"]
    for module in ("brotli", "brotlicffi"):
        try:
            __import__(module)
            encodings.append("br")
            break
        except ImportError:
            continue
    try:
        __import__("zstandard")
        encodings.append("zstd")
    except ImportError:
        pass
    return ", ".join(encodings)


DEFAULT_HEADERS: Dict[str, str] = {
    "User-Agent": USER_AGENT,
    "Connection": "keep-alive",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,zh-TW;q=0.5",
    "Accept-Encoding": supported_content_encodings(),
    "Referer": "https://www.zhihu.com/",
    "sec-ch-ua": SEC_CH_UA,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

#: 走 OpenAPI 拿正文时用得上的头。知乎对 XHR 与文档请求的风控策略不同。
API_HEADERS: Dict[str, str] = {
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": DEFAULT_HEADERS["Accept-Encoding"],
    "Referer": "https://www.zhihu.com/",
    "x-requested-with": "fetch",
    "sec-ch-ua": SEC_CH_UA,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

RETRY_STATUS_CODES = (429, 500, 502, 503, 504)

# 需要保留的关键 cookie，缺失时给出更明确的提示
IMPORTANT_COOKIES = ("z_c0", "d_c0", "SESSIONID")


def build_headers(extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """返回一份请求头副本，可追加自定义字段。"""
    headers = dict(DEFAULT_HEADERS)
    if extra:
        headers.update(extra)
    return headers


def build_api_headers(extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """返回一份 OpenAPI 请求头副本（用于正文 API 兜底）。"""
    headers = dict(API_HEADERS)
    if extra:
        headers.update(extra)
    return headers


def load_cookies(path: Optional[str] = None, warn_expired: bool = True) -> Dict[str, str]:
    """读取 cookies 文件并转成 ``{name: value}``。

    同时兼容两种写法：

    * 浏览器插件导出的列表：``[{"name": ..., "value": ...}, ...]``
    * 手写的对象：``{"z_c0": "...", "d_c0": "..."}``
    """
    cookie_path = pathlib.Path(path) if path else config_mod.PROJECT_ROOT / config_mod.COOKIES_FILENAME

    try:
        with open(cookie_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        message = "未找到 %s，将以未登录状态访问（私密收藏夹无法获取）" % cookie_path.name
        print(message)
        logging.warning(message)
        return {}
    except json.JSONDecodeError as exc:
        message = "cookies 文件格式错误: %s（%s）" % (cookie_path, exc)
        print(message)
        logging.error(message)
        return {}

    cookies: Dict[str, str] = {}
    expired_names = []
    now = time.time()

    if isinstance(raw, dict) and not isinstance(raw.get("name"), str):
        cookies = {str(k): str(v) for k, v in raw.items()}
    elif isinstance(raw, list):
        for cookie in raw:
            if not isinstance(cookie, dict) or "name" not in cookie:
                continue
            name = cookie.get("name")
            cookies[str(name)] = str(cookie.get("value", ""))
            expiration = cookie.get("expirationDate")
            if expiration and float(expiration) < now:
                expired_names.append(str(name))
    else:
        logging.error("cookies 文件结构无法识别: %s", cookie_path)
        print("cookies 文件结构无法识别，将忽略该文件")
        return {}

    if warn_expired and expired_names:
        unique_names = sorted(set(expired_names))
        warning = "检测到已过期的 cookies: %s，请重新导出知乎 cookies" % ", ".join(unique_names)
        logging.warning(warning)
        print("警告：" + warning)

    # 登录票据缺失时提前提示，避免用户对着一堆「需要登录」的失败发呆
    missing_login = [name for name in IMPORTANT_COOKIES if name not in cookies]
    if warn_expired and cookies and "z_c0" in missing_login:
        note = "cookies 中缺少 z_c0，私密收藏夹大概率无法获取，请确认导出的是登录态 cookies"
        logging.warning(note)
        print("警告：" + note)

    return cookies


def create_session(
    cookies: Optional[Mapping[str, str]] = None,
    headers: Optional[Mapping[str, str]] = None,
    retries: int = 3,
    backoff: float = 0.6,
) -> requests.Session:
    """创建一个带重试策略的会话对象。"""
    session = requests.Session()
    session.headers.update(build_headers(headers))

    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        backoff_factor=backoff,
        status_forcelist=RETRY_STATUS_CODES,
        allowed_methods=frozenset(["GET", "HEAD", "OPTIONS"]),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    if cookies:
        session.cookies.update(dict(cookies))

    return session


class RateLimiter:
    """线程安全的请求间隔限制器。

    允许多线程并发，但保证全局任意两次请求之间至少间隔 ``min_interval`` 秒。
    """

    def __init__(self, min_interval: float = 0.0):
        self.min_interval = max(0.0, float(min_interval or 0.0))
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        """必要时阻塞，直到下一次请求可以被发起。"""
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next_allowed - now
            if sleep_for > 0:
                time.sleep(sleep_for)
                now = time.monotonic()
            self._next_allowed = now + self.min_interval

    def __enter__(self) -> "RateLimiter":
        self.wait()
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None
