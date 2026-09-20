# -*- coding: utf-8 -*-
"""测试辅助工具：离线假会话与样例数据读取。

放在独立模块（而不是 conftest）里，是为了支持：

* 测试模块之间共享假对象
* ``test/legacy/`` 下的历史脚本不会与顶层 ``conftest`` 名字冲突
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any, Callable, Dict, List, Optional

import requests

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


class FakeResponse:
    """最小可用的 requests.Response 替身。"""

    def __init__(
        self,
        text: str = "",
        json_data: Any = None,
        content: bytes = b"",
        status_code: int = 200,
        headers: Optional[Dict[str, str]] = None,
        url: str = "https://www.zhihu.com/fake",
        encoding: str = "utf-8",
    ):
        self.text = text
        self.content = content if content else text.encode("utf-8")
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url
        self.encoding = encoding
        self._json = json_data

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("响应没有 JSON 内容")
        return self._json

    def iter_content(self, chunk_size: int = 8192, **_kwargs: Any):
        """模拟流式读取（专栏文章走的就是这条路）。"""
        payload = self.content or b""
        for start in range(0, len(payload), chunk_size):
            yield payload[start:start + chunk_size]

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(
                "HTTP %s" % self.status_code, response=self  # type: ignore[arg-type]
            )


class FakeSession:
    """按 URL 分发的假会话，记录所有请求 URL 与调用参数。"""

    def __init__(self, handler: Callable[[str], Optional[FakeResponse]]):
        self.handler = handler
        self.calls: List[str] = []
        self.calls_detail: List[Dict[str, Any]] = []
        self.headers: Dict[str, str] = {}
        self.cookies: Dict[str, str] = {}
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(url)
        self.calls_detail.append({"url": url, "kwargs": kwargs})
        response = self.handler(url)
        if response is None:
            raise requests.ConnectionError("FakeSession 未配置该 URL: %s" % url)
        return response

    def close(self) -> None:
        self.closed = True


def read_fixture(name: str) -> str:
    """读取 ``test/fixtures`` 下的样例文件。"""
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def collection_items_payload(items: List[Dict[str, Any]], totals: int, is_end: bool) -> Dict[str, Any]:
    """构造收藏夹条目接口的返回体。"""
    return {
        "data": [{"content": item} for item in items],
        "paging": {"totals": totals, "is_end": is_end},
    }


def dump(data: Any) -> str:
    """调试辅助：把对象转成可读 JSON。"""
    return json.dumps(data, ensure_ascii=False, indent=2)
